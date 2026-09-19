"""La fiche d'une écriture, sa correction et la palette des comptes (pas 110), sous `/comptabilite`.

─────────────────────────────────────────────────────────────────────────────────
CE QUE LA MAQUETTE DEMANDE

« Parcours comptable », vue E (UC05, UC11) : une écriture ouverte seule, ses lignes et leurs
attributs fiscaux, **son historique**, l'état de sa période, et quatre actions : contre-passer,
dupliquer, ouvrir la pièce, signaler au réviseur. Puis la contre-passation elle-même : les
lignes inversées montrées **avant** d'être créées, et « l'impact sur une déclaration en
préparation annoncé avant validation ». Enfin la palette des comptes, où « les comptes déjà
utilisés dans le dossier passent avant le plan SYSCOHADA général ».

QUATRE ROUTES

* `GET  …/fiche`                      la fiche : écriture, historique, contre-passations, verrou.
* `GET  …/contre-passation/apercu`    l'inverse et le refus éventuel, sans rien écrire.
* `POST …/signalement`                signaler au réviseur, inscrit au journal d'audit et annoncé
                                      par les abonnements aux notifications (aucun code de plus).
* `GET  /dossiers/{e}/comptes-utilises` les comptes mouvementés, du plus au moins employé.

L'impact sur la TVA est lu chez les obligations (`/obligations/…/impact-contrepassation`) :
c'est leur calcul de la déclaration, et l'arête `comptabilite → obligations` n'existe pas.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.comptabilite.adaptateurs.entrant.routes_http import (
    _exercice_du_dossier,
    depot,
    periodes_verrouillees_du_dossier,
)
from app.contextes.comptabilite.adaptateurs.sortant.depot_ecritures_memoire import (
    EcritureIntrouvable,
)
from app.contextes.comptabilite.adaptateurs.sortant.depots_lettrages import depot_des_lettrages
from app.contextes.comptabilite.adaptateurs.sortant.depots_revues import depot_des_revues
from app.contextes.comptabilite.application.tenue_du_journal import (
    ApercuDeContrepassation,
    apercevoir_la_contrepassation,
)
from app.contextes.comptabilite.domaine.cloture_mensuelle import (
    PeriodeVerrouillee,
    periode_verrouillee_au,
)
from app.contextes.comptabilite.domaine.entites import EcritureComptable, EtatEcriture
from app.contextes.transverse.api import (
    Acces,
    AccesRequis,
    Permission,
    atelier,
    exiger_dossier,
)
from app.partage.horloge import maintenant

routeur = APIRouter(prefix="/comptabilite", tags=["Comptabilité · fiche d'écriture"])


def _lire(entreprise: str, exercice: str, journal: str, numero: int) -> EcritureComptable:
    try:
        return depot(entreprise).lire(f"{exercice}/{journal}/{numero:06d}")
    except EcritureIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence


def _noms() -> dict[str, str]:
    return {c.identifiant: f"{c.prenom} {c.nom}".strip() for c in atelier().comptes.lister()}


_PASSAGES = {
    "TRANSMISE": "Mois transmis au réviseur",
    "RENVOYEE": "Mois renvoyé au comptable",
    "VALIDEE": "Mois validé par le réviseur",
}


def _objet_id(entreprise: str, cle: str) -> str:
    # La clé d'une écriture n'est unique que dans son dossier.
    return f"{entreprise}:{cle}"


class EvenementDEcriture(BaseModel):
    """Une ligne de l'historique. `quand` est nul quand le fait n'a pas d'heure connue."""

    model_config = ConfigDict(frozen=True)

    quand: datetime | date | None
    quoi: str
    qui: str | None = None


class FicheDEcriture(BaseModel):
    ecriture: EcritureComptable
    #: « Validée, non modifiable » ou « Brouillon ».
    modifiable: bool
    #: Du plus ancien au plus récent.
    historique: list[EvenementDEcriture]
    #: Les écritures qui contre-passent celle-ci. Plus d'une ne devrait jamais exister.
    contrepassee_par: list[str]
    verrou: PeriodeVerrouillee | None
    exercice_clos: bool
    peut_contrepasser: bool
    raison_de_ne_pas_contrepasser: str | None


@routeur.get(
    "/dossiers/{entreprise}/ecritures/{exercice}/{journal}/{numero}/fiche",
    summary="La fiche d'une écriture : lignes, historique, période",
)
def lire_la_fiche(
    acces: AccesRequis, entreprise: str, exercice: str, journal: str, numero: int
) -> FicheDEcriture:
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    ecriture = _lire(entreprise, exercice, journal, numero)
    noms = _noms()

    def nom(compte: str | None) -> str | None:
        return None if compte is None else noms.get(compte, compte)

    historique: list[EvenementDEcriture] = [
        # ⚠️ L'heure de la saisie n'est pas conservée par l'écriture : l'historique le montre
        # sans date plutôt que d'en inventer une.
        EvenementDEcriture(quand=None, quoi="Saisie en brouillon", qui=nom(ecriture.saisie_par)),
    ]
    if ecriture.ecriture_contrepassee:
        historique.append(
            EvenementDEcriture(
                quand=ecriture.date_operation,
                quoi=(
                    f"Contre-passe {ecriture.ecriture_contrepassee} : "
                    f"{ecriture.motif_contrepassation}"
                ),
                qui=nom(ecriture.saisie_par),
            )
        )
    if ecriture.validee_le:
        historique.append(
            EvenementDEcriture(
                quand=ecriture.validee_le, quoi="Validée", qui=nom(ecriture.validee_par)
            )
        )
    inverses = [
        e for e in depot(entreprise).lister(exercice) if e.ecriture_contrepassee == ecriture.cle
    ]
    for inverse in inverses:
        historique.append(
            EvenementDEcriture(
                quand=inverse.date_operation,
                quoi=(
                    f"Contre-passée par {inverse.cle}"
                    f"{' (brouillon)' if inverse.etat is EtatEcriture.BROUILLON else ''} : "
                    f"{inverse.motif_contrepassation}"
                ),
                qui=nom(inverse.saisie_par),
            )
        )
    for lettrage in depot_des_lettrages().du_dossier(entreprise, exercice):
        if not any(r.cle_ecriture == ecriture.cle for r in lettrage.lignes):
            continue
        historique.append(
            EvenementDEcriture(
                quand=lettrage.le,
                quoi=f"Lettrée {lettrage.lettre} au compte {lettrage.compte}",
                qui=nom(lettrage.par),
            )
        )
        if lettrage.defait_le:
            historique.append(
                EvenementDEcriture(
                    quand=lettrage.defait_le,
                    quoi=f"Lettrage {lettrage.lettre} défait",
                    qui=nom(lettrage.defait_par),
                )
            )
    for revue in depot_des_revues().du_dossier(entreprise):
        if revue.du <= ecriture.date_operation <= revue.au:
            for passage in revue.historique:
                historique.append(
                    EvenementDEcriture(
                        quand=passage.le,
                        quoi=f"{_PASSAGES[passage.statut.value]} ({revue.identifiant})",
                        qui=passage.par,
                    )
                )
    for entree in atelier().journal.lister(
        objet_type="ecriture", objet_id=_objet_id(entreprise, ecriture.cle)
    ):
        historique.append(
            EvenementDEcriture(
                quand=entree.horodatage,
                quoi=f"Signalée au réviseur : {entree.apres.get('message', '')}",
                qui=nom(entree.acteur),
            )
        )

    def ordre(evenement: EvenementDEcriture):
        quand = evenement.quand
        if quand is None:
            return (0, "")
        return (1, quand.isoformat() if isinstance(quand, datetime) else f"{quand.isoformat()}T")

    verrou = periode_verrouillee_au(
        ecriture.date_operation, periodes_verrouillees_du_dossier(entreprise)
    )
    exercice_du_dossier = _exercice_du_dossier(entreprise, exercice)
    clos = bool(exercice_du_dossier and exercice_du_dossier.clos)
    raison = (
        "Un brouillon se corrige ; seule une écriture validée se contre-passe."
        if ecriture.etat is not EtatEcriture.VALIDEE
        else f"Déjà contre-passée par {inverses[0].cle}."
        if inverses
        else f"Exercice {exercice} clos : corriger dans l'exercice ouvert."
        if clos
        else None
    )
    return FicheDEcriture(
        ecriture=ecriture,
        modifiable=ecriture.modifiable,
        historique=sorted(historique, key=ordre),
        contrepassee_par=[e.cle for e in inverses],
        verrou=verrou,
        exercice_clos=clos,
        peut_contrepasser=raison is None,
        raison_de_ne_pas_contrepasser=raison,
    )


@routeur.get(
    "/dossiers/{entreprise}/ecritures/{exercice}/{journal}/{numero}/contre-passation/apercu",
    summary="L'écriture inverse qu'une contre-passation créerait, sans rien écrire",
)
def apercevoir_la_contre_passation(
    acces: AccesRequis,
    entreprise: str,
    exercice: str,
    journal: str,
    numero: int,
    date_operation: date | None = Query(None, description="Défaut : aujourd'hui"),
) -> ApercuDeContrepassation:
    """⚠️ `SAISIR_ECRITURE`, et non `CONTRE_PASSER` : cette permission-là exige un motif au
    contrôle d'accès, qui l'inscrit au journal. Un aperçu n'engage rien et ne se motive pas ; le
    motif est demandé au geste. Les rôles qui contre-passent sont ceux qui saisissent."""
    exiger_dossier(acces, Permission.SAISIR_ECRITURE, entreprise)
    _lire(entreprise, exercice, journal, numero)
    return apercevoir_la_contrepassation(
        f"{exercice}/{journal}/{numero:06d}",
        jour=date_operation or maintenant().date(),
        exercice=_exercice_du_dossier(entreprise, exercice),
        periodes_verrouillees=periodes_verrouillees_du_dossier(entreprise),
        depot=depot(entreprise),
        par=acces.compte,
    )


class DemandeDeSignalement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=10, max_length=500)


class Signalement(BaseModel):
    ecriture: str
    message: str
    le: datetime


@routeur.post(
    "/dossiers/{entreprise}/ecritures/{exercice}/{journal}/{numero}/signalement",
    summary="Signaler une écriture au réviseur",
    status_code=201,
)
def signaler_au_reviseur(
    acces: AccesRequis,
    entreprise: str,
    exercice: str,
    journal: str,
    numero: int,
    demande: DemandeDeSignalement,
) -> Signalement:
    """Un signalement n'est pas une remarque de revue : la remarque est l'acte du réviseur sur un
    mois transmis (pas 102). Le signalement est l'inverse, le comptable qui dit « regarde
    celle-ci », à tout moment. Il n'a pas d'objet à part : c'est une entrée du journal d'audit,
    que les abonnements annoncent aux réviseurs du dossier, et que la fiche relit dans son
    historique.
    """
    exiger_dossier(acces, Permission.SAISIR_ECRITURE, entreprise)
    ecriture = _lire(entreprise, exercice, journal, numero)
    le = maintenant()
    message = demande.message.strip()
    _journaliser_le_signalement(acces, entreprise, ecriture, message, le)
    return Signalement(ecriture=ecriture.cle, message=message, le=le)


def _journaliser_le_signalement(
    acces: Acces, entreprise: str, ecriture: EcritureComptable, message: str, le: datetime
) -> None:
    atelier().journal.ajouter(
        horodatage=le,
        acteur=acces.compte,
        action="comptabilite.ecriture_signalee",
        objet_type="ecriture",
        objet_id=_objet_id(entreprise, ecriture.cle),
        apres={
            "dossier": entreprise,
            "ecriture": ecriture.cle,
            "exercice": ecriture.exercice,
            "journal": ecriture.journal,
            "numero": ecriture.numero,
            "message": message,
            "par": acces.nom_complet,
        },
    )


class CompteUtilise(BaseModel):
    compte: str
    lignes: int


@routeur.get(
    "/dossiers/{entreprise}/comptes-utilises",
    summary="Les comptes mouvementés du dossier, du plus au moins employé",
)
def lire_les_comptes_utilises(
    acces: AccesRequis, entreprise: str, exercice: str = Query(...)
) -> list[CompteUtilise]:
    """« Les comptes déjà utilisés dans le dossier passent avant le plan SYSCOHADA général »
    (maquette, vue E, palette F2). Brouillons compris : un compte qu'on vient de saisir est
    celui qu'on cherche à nouveau."""
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    compteur = Counter(
        ligne.compte for e in depot(entreprise).lister(exercice) for ligne in e.lignes
    )
    return [
        CompteUtilise(compte=compte, lignes=nombre)
        for compte, nombre in sorted(compteur.items(), key=lambda c: (-c[1], c[0]))
    ]


__all__ = ["routeur"]
