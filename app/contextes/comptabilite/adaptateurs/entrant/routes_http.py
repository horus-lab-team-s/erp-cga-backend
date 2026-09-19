"""API du contexte E · Comptabilité SYSCOHADA.

**Un dépôt par dossier.** Les routes sont donc toutes préfixées par le NIU : la
clé `2026/AC/000042` n'est unique qu'à l'intérieur d'un dossier, et une route
`/comptabilite/ecritures/2026-AC-000042` sans dossier désignerait six écritures
différentes.

**La balance et le grand livre sont calculés, jamais stockés.** Il n'existe qu'une
source, le journal. Une balance persistée serait un second exemplaire de la vérité,
et le jour où les deux divergeraient personne ne saurait lequel croire — alors
qu'il n'y a rien à croire : la balance *est* la somme des écritures.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, computed_field

from app.contextes.comptabilite.adaptateurs.sortant.cloture_mensuelle_yaml import (
    charger_les_reglages_de_la_cloture_mensuelle,
)
from app.contextes.comptabilite.adaptateurs.sortant.depot_ecritures_memoire import (
    EcritureIntrouvable,
)
from app.contextes.comptabilite.adaptateurs.sortant.depots_lettrages import depot_des_lettrages
from app.contextes.comptabilite.adaptateurs.sortant.depots_revues import depot_des_revues
from app.contextes.comptabilite.adaptateurs.sortant.depots_sql import (
    DepotEcrituresSql,
    DepotPlanImputationSql,
)
from app.contextes.comptabilite.adaptateurs.sortant.donnees_demo import (
    PLAN_IMPUTATION_DEMO,
    DepotPlanImputationMemoire,
)
from app.contextes.comptabilite.adaptateurs.sortant.magasins_memoire import ecritures_en_memoire
from app.contextes.comptabilite.adaptateurs.sortant.plan_syscohada import (
    DepotJournauxMemoire,
    DepotPlanMemoire,
)
from app.contextes.comptabilite.adaptateurs.sortant.profils_echange import (
    charger_les_profils,
)
from app.contextes.comptabilite.api import (
    BrouillonEcriture,
    ComptabilisationInterdite,
    Compte,
    CorrectionDeBrouillon,
    EcritureComptable,
    FactureDUnAutreDossier,
    ImputationImpossible,
    Journal,
    LigneEcriture,
    LigneGrandLivre,
    PlanImputation,
    SaisieRefusee,
    SoldeCompte,
    TrouSequence,
    balance,
    contrepasser_une_ecriture,
    controle_balance_equilibree,
    corriger_un_brouillon,
    enregistrer_une_ecriture,
    grand_livre,
    proposer_ecriture_achat,
    rattacher_au_dossier,
    resultat,
    sequences_incompletes,
    valider_une_ecriture,
)
from app.contextes.comptabilite.application.lettrage import lettrages_par_ligne
from app.contextes.comptabilite.application.reprise import (
    RapportDeReprise,
    reprendre_un_lot,
)
from app.contextes.comptabilite.domaine.cloture_mensuelle import (
    PeriodeVerrouillee,
    periodes_verrouillees,
)
from app.contextes.comptabilite.domaine.echange import EchangeRefuse, lire, rendre
from app.contextes.comptabilite.domaine.ports import DepotEcritures

# ⚠️ L'arête `comptabilite → conformite` est déclarée au registre des services :
# c'est elle qui autorise cet import, et `test_architecture.py` la garde.
from app.contextes.conformite.api import (
    FactureAControler,
    RapportConformite,
    RegimeEmetteur,
    Verdict,
    composer_verdict,
    rapport_arbitre,
)
from app.contextes.portefeuille.api import (
    DepotEntreprisesSql,
    Entreprise,
    EntrepriseIntrouvable,
    Exercice,
    StatutIntrouvable,
    entreprises_en_memoire,
)
from app.contextes.transverse.api import (
    AccesRequis,
    Permission,
    atelier,
    exiger,
    exiger_dossier,
    session_de_travail,
)
from app.infrastructure.config import configuration
from app.partage.erreurs import message_lisible
from app.partage.horloge import maintenant
from app.partage.locataire import courant


class FiltreDeLettrage(StrEnum):
    """Pas 108 : le filtre « Lettrage : tous ▾ » de la maquette."""

    TOUTES = "TOUTES"
    LETTREES = "LETTREES"
    NON_LETTREES = "NON_LETTREES"


routeur = APIRouter(prefix="/comptabilite", tags=["Comptabilité"])

_plan = DepotPlanMemoire()
_journaux = DepotJournauxMemoire()
_imputation_memoire = DepotPlanImputationMemoire()


def imputation():
    """Le plan d'imputation en vigueur — SQL dans une requête, mémoire sinon."""
    session = session_de_travail()
    if session is None:
        return _imputation_memoire
    return DepotPlanImputationSql(session, courant(), PLAN_IMPUTATION_DEMO)


# ⚠️ Le magasin mémoire du contexte propriétaire, et non une construction locale :
# voir `magasins_memoire.py` de ce contexte, et le pas 52.
_ecritures_memoire = ecritures_en_memoire


def depot(entreprise: str) -> DepotEcritures:
    """Le dépôt en vigueur — SQL dans une requête, mémoire sinon.

    La bascule est ici et non dans chaque route : une route qui choisirait son
    dépôt le choisirait un jour mal, et l'incohérence — une lecture en base, une
    écriture en mémoire — ne se verrait qu'au redémarrage.
    """
    session = session_de_travail()
    if session is None:
        return _ecritures_memoire(entreprise)
    return DepotEcrituresSql(session, courant(), entreprise)


@routeur.get("/plan-comptable", summary="Les comptes ouverts")
def lire_plan(
    acces: AccesRequis,
    classe: int | None = Query(None, ge=1, le=9, description="Filtrer sur une classe"),
) -> list[Compte]:
    exiger(acces, Permission.LIRE_COMPTABILITE)
    comptes = _plan.charger()
    if classe is not None:
        comptes = [c for c in comptes if c.classe == classe]
    return sorted(comptes, key=lambda c: c.numero)


@routeur.get("/journaux", summary="Les journaux du dossier")
def lire_journaux(acces: AccesRequis) -> list[Journal]:
    exiger(acces, Permission.LIRE_COMPTABILITE)
    return _journaux.charger()


@routeur.get(
    "/dossiers/{entreprise}/ecritures",
    summary="Le journal d'un dossier",
)
def lister_ecritures(
    acces: AccesRequis,
    entreprise: str,
    exercice: str = Query(..., description="Libellé de l'exercice, par exemple « 2026 »"),
    journal: str | None = Query(None, description="Code du journal, par exemple « AC »"),
) -> list[EcritureComptable]:
    """Rend les écritures dans l'ordre du numéro par journal.

    C'est l'ordre du journal papier, et c'est celui qu'un vérificateur attend :
    trier par date ferait apparaître des sauts de numéro qui n'existent pas.
    """
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    return depot(entreprise).lister(exercice, journal)


@routeur.get(
    "/dossiers/{entreprise}/ecritures/{exercice}/{journal}/{numero}",
    summary="Une écriture",
)
def lire_ecriture(
    acces: AccesRequis, entreprise: str, exercice: str, journal: str, numero: int
) -> EcritureComptable:
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    try:
        return depot(entreprise).lire(f"{exercice}/{journal}/{numero:06d}")
    except EcritureIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence


@routeur.get(
    "/dossiers/{entreprise}/balance",
    summary="La balance, calculée sur les écritures",
)
def lire_balance(
    acces: AccesRequis,
    entreprise: str,
    exercice: str = Query(...),
    jusqu_au: date | None = Query(
        None, description="Arrête la balance à cette date incluse"
    ),
    du: date | None = Query(
        None,
        description=(
            "Pas 108 : ne retient que les mouvements depuis cette date incluse (la période de "
            "la maquette, « janvier à juillet »)"
        ),
    ),
    journal: str | None = Query(None, description="Pas 108 : un seul journal"),
    brouillons: bool = Query(
        False,
        description=(
            "Inclure les écritures en brouillon. Par défaut non : un brouillon "
            "n'est pas de la comptabilité, c'est une intention."
        ),
    ),
) -> list[SoldeCompte]:
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    ecritures = depot(entreprise).lister(exercice)
    if jusqu_au is not None:
        ecritures = [e for e in ecritures if e.date_operation <= jusqu_au]
    if du is not None:
        ecritures = [e for e in ecritures if e.date_operation >= du]
    if journal is not None:
        ecritures = [e for e in ecritures if e.journal == journal]
    return balance(ecritures, brouillons_inclus=brouillons)


class SanteComptable(BaseModel):
    """Les contrôles à rejouer avant tout dépôt.

    Trois questions, et il vaut mieux se les poser avant que l'administration ne
    les pose : la balance est-elle équilibrée, la numérotation est-elle continue,
    et que dit le résultat.
    """

    exercice: str
    equilibree: bool
    total_debit: Decimal
    total_credit: Decimal
    resultat: Decimal
    trous_de_sequence: list[TrouSequence]

    @computed_field
    @property
    def deposable(self) -> bool:
        """Sérialisé, et pas seulement calculé : c'est le champ que l'écran lit."""
        return self.equilibree and not self.trous_de_sequence


@routeur.get(
    "/dossiers/{entreprise}/sante",
    summary="Les contrôles de cohérence d'un exercice",
)
def lire_sante(
    acces: AccesRequis,
    entreprise: str,
    exercice: str = Query(...),
    du: date | None = Query(None, description="Pas 108 : totaux depuis cette date incluse"),
    jusqu_au: date | None = Query(None, description="Pas 108 : totaux jusqu'à cette date"),
    journal: str | None = Query(None, description="Pas 108 : totaux d'un seul journal"),
) -> SanteComptable:
    """⚠️ Pas 108 : les filtres portent sur les **totaux**, l'équilibre et le résultat, pour que
    le pied de la balance filtrée soit calculé ici et non additionné par l'écran. La
    numérotation, elle, se contrôle toujours sur l'exercice entier : un trou ne disparaît pas
    parce qu'on regarde un autre mois."""
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    ecritures = depot(entreprise).lister(exercice)
    retenues = [
        e
        for e in ecritures
        if (du is None or e.date_operation >= du)
        and (jusqu_au is None or e.date_operation <= jusqu_au)
        and (journal is None or e.journal == journal)
    ]
    soldes = balance(retenues)
    equilibree = controle_balance_equilibree(soldes)
    return SanteComptable(
        exercice=exercice,
        equilibree=equilibree,
        total_debit=sum((s.total_debit for s in soldes), Decimal(0)),
        total_credit=sum((s.total_credit for s in soldes), Decimal(0)),
        resultat=resultat(soldes),
        trous_de_sequence=sequences_incompletes(ecritures),
    )


@routeur.get(
    "/dossiers/{entreprise}/grand-livre/{compte}",
    summary="Le détail d'un compte",
    description=(
        "Le grand livre est la vue par compte de ce que le journal enregistre par "
        "ordre chronologique. C'est là que se fait le lettrage, et c'est le seul "
        "endroit où l'on voit ce qui reste réellement ouvert chez un fournisseur."
    ),
)
def lire_grand_livre(
    acces: AccesRequis,
    entreprise: str,
    compte: str,
    exercice: str = Query(...),
    du: date | None = Query(None, description="Pas 108 : mouvements depuis cette date incluse"),
    au: date | None = Query(None, description="Pas 108 : mouvements jusqu'à cette date incluse"),
    journal: str | None = Query(None, description="Pas 108 : un seul journal"),
    lettrage: FiltreDeLettrage = Query(
        FiltreDeLettrage.TOUTES, description="Pas 108 : lettrées, non lettrées, ou toutes"
    ),
) -> list[LigneGrandLivre]:
    """⚠️ Pas 108 : les filtres s'appliquent **après** le calcul du solde progressif. Filtrer
    avant ferait partir le solde de zéro au premier jour de la période, et le solde affiché
    d'un fournisseur en juillet ne serait plus ce qu'on lui doit."""
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    lettrages = lettrages_par_ligne(depot_des_lettrages().du_compte(entreprise, exercice, compte))
    lignes = grand_livre(depot(entreprise).lister(exercice), compte, lettrages=lettrages)
    if lignes:
        lignes = [
            ligne
            for ligne in lignes
            if (du is None or ligne.date_operation >= du)
            and (au is None or ligne.date_operation <= au)
            and (journal is None or ligne.journal == journal)
            and (
                lettrage is FiltreDeLettrage.TOUTES
                or (lettrage is FiltreDeLettrage.LETTREES) == (ligne.lettrage is not None)
            )
        ]
        # Des mouvements existent, aucun ne passe le filtre : ce n'est pas « aucun mouvement ».
        return lignes
    if not lignes:
        raise HTTPException(
            status_code=404,
            detail=(
                f"aucun mouvement sur le compte {compte} pour l'exercice {exercice}. "
                "Un compte sans mouvement n'est pas une erreur — vérifier le numéro "
                "avant de conclure."
            ),
        )
    return lignes


@routeur.get(
    "/dossiers/{entreprise}/plan-imputation",
    summary="Les règles d'imputation d'un dossier",
    description=(
        "Elles sont propres à chaque adhérent : une entreprise de BTP et une "
        "clinique n'imputent pas les mêmes achats sur les mêmes comptes."
    ),
)
def lire_plan_imputation(acces: AccesRequis, entreprise: str) -> PlanImputation:
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    return imputation().charger(entreprise)


# ══ Écrire ════════════════════════════════════════════════════════════════════
#
# ─────────────────────────────────────────────────────────────────────────────
# TROIS ROUTES, TROIS PERMISSIONS DISTINCTES, ET CE N'EST PAS DE LA CÉRÉMONIE
#
# `SAISIR_ECRITURE` propose, `VALIDER_ECRITURE` engage, `CONTRE_PASSER` corrige.
# Les trois existaient dans les rôles depuis le premier jour sans qu'aucune route
# ne les emploie : c'étaient des permissions décoratives. Elles deviennent ici ce
# qu'elles annonçaient.
#
# Un adhérent ne détient aucune des trois, et c'est le point le plus important de
# la section : un centre de gestion agréé engage son agrément sur les comptes
# qu'il produit. Laisser l'adhérent écrire dans son propre journal ferait du
# cabinet le témoin de ses écritures plutôt que leur auteur.
# ─────────────────────────────────────────────────────────────────────────────


def _dossier_du_portefeuille(entreprise: str) -> Entreprise | None:
    """Le dossier tel que le portefeuille le connaît, ou `None`.

    Extrait au pas 73 : l'exercice et le régime se lisent au même endroit, et deux
    lectures recopiées finiraient par ne plus choisir le même dépôt.
    """
    session = session_de_travail()
    depot_dossiers = (
        entreprises_en_memoire()
        if session is None
        else DepotEntreprisesSql(session, courant())
    )
    try:
        return depot_dossiers.lire(entreprise)
    except EntrepriseIntrouvable:
        return None


def periodes_verrouillees_du_dossier(entreprise: str) -> list[PeriodeVerrouillee]:
    """Les mois verrouillés du dossier, déduits de ses revues (pas 107).

    Relu à chaque geste, sans mémoïsation : un mois renvoyé par le réviseur doit redevenir
    corrigeable à la requête suivante, pas au redémarrage.
    """
    return periodes_verrouillees(
        depot_des_revues().du_dossier(entreprise),
        charger_les_reglages_de_la_cloture_mensuelle(configuration().dossier_referentiel),
    )


def _exercice_du_dossier(entreprise: str, libelle: str) -> Exercice | None:
    """L'exercice tel que le portefeuille le connaît, ou `None`.

    ⚠️ La comptabilité ne stocke **pas** ses propres exercices, et c'est
    volontaire : deux registres d'exercices divergeraient, et l'on saisirait dans
    un exercice ouvert d'un côté, clos de l'autre. L'arête
    `comptabilite → portefeuille` est déclarée pour cette raison.
    """
    dossier = _dossier_du_portefeuille(entreprise)
    if dossier is None:
        return None
    return next((e for e in dossier.exercices if e.libelle == libelle), None)


class DemandeSaisie(BaseModel):
    """Le corps d'une saisie.

    Il ne porte ni numéro, ni état, ni valideur : voir `BrouillonEcriture`. Ce
    qu'un client ne peut pas envoyer n'a pas besoin d'être contrôlé.
    """

    journal: str
    exercice: str
    date_operation: date
    libelle: str
    piece_justificative: str | None = None
    reference_externe: str | None = None
    lignes: list[LigneEcriture]


class DemandeContrepassation(BaseModel):
    motif: str
    #: Le jour où l'on s'aperçoit de l'erreur. Défaut : aujourd'hui. Jamais avant
    #: l'écriture d'origine, jamais hors de son exercice, jamais dans un exercice
    #: clos : le cas d'usage le refuse (pas 71).
    date_operation: date | None = None


@routeur.post(
    "/dossiers/{entreprise}/ecritures",
    summary="Enregistrer une écriture en brouillon",
    status_code=201,
)
def saisir_ecriture(
    acces: AccesRequis, entreprise: str, demande: DemandeSaisie
) -> EcritureComptable:
    """Enregistre l'écriture, numérotée par le registre, à l'état brouillon.

    Un refus de saisie répond **409 et non 422** : la requête est bien formée, ce
    sont les faits qui s'y opposent — journal inconnu, compte absent du plan,
    exercice clos, date hors bornes. Un 422 laisserait croire à un défaut de
    format, et le comptable chercherait sa faute dans le mauvais champ.
    """
    exiger_dossier(acces, Permission.SAISIR_ECRITURE, entreprise)
    brouillon = BrouillonEcriture(**demande.model_dump())
    try:
        return enregistrer_une_ecriture(
            brouillon,
            journaux=_journaux.charger(),
            plan=_plan.charger(),
            exercice=_exercice_du_dossier(entreprise, demande.exercice),
            periodes_verrouillees=periodes_verrouillees_du_dossier(entreprise),
            depot=depot(entreprise),
            par=acces.compte,
        )
    except SaisieRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    except ValueError as refus:
        # Les invariants de l'entité : déséquilibre, ligne unique, montant nul.
        # Ils portent le même statut que les contrôles d'environnement, parce
        # qu'ils disent la même chose au comptable — l'écriture ne tient pas.
        raise HTTPException(status_code=409, detail=message_lisible(refus)) from refus


@routeur.post(
    "/dossiers/{entreprise}/ecritures/{exercice}/{journal}/{numero}/correction",
    summary="Corriger un brouillon, à son numéro",
    description=(
        "Réécrit le contenu d'un brouillon : date, libellé, pièce, référence, lignes. "
        "Ni le journal, ni l'exercice, ni le numéro ne changent. Refusé sur une écriture "
        "validée (elle se contre-passe) et sur une contre-passation en brouillon. Tous "
        "les contrôles de la saisie sont rejoués (pas 72)."
    ),
)
def corriger_ecriture(
    acces: AccesRequis,
    entreprise: str,
    exercice: str,
    journal: str,
    numero: int,
    demande: CorrectionDeBrouillon,
) -> EcritureComptable:
    """⚠️ La permission est celle de la saisie : corriger un brouillon, c'est le saisir
    à nouveau. Un rôle qui ne saisit pas ne corrige pas."""
    exiger_dossier(acces, Permission.SAISIR_ECRITURE, entreprise)
    try:
        return corriger_un_brouillon(
            f"{exercice}/{journal}/{numero:06d}",
            demande,
            journaux=_journaux.charger(),
            plan=_plan.charger(),
            exercice=_exercice_du_dossier(entreprise, exercice),
            periodes_verrouillees=periodes_verrouillees_du_dossier(entreprise),
            depot=depot(entreprise),
            par=acces.compte,
        )
    except EcritureIntrouvable as absente:
        raise HTTPException(status_code=404, detail=str(absente)) from absente
    except ValueError as refus:
        # SaisieRefusee et CorrectionRefusee en descendent : 409, comme la saisie.
        raise HTTPException(status_code=409, detail=message_lisible(refus)) from refus


@routeur.post(
    "/dossiers/{entreprise}/ecritures/{exercice}/{journal}/{numero}/validation",
    summary="Valider une écriture, qui devient immuable",
)
def valider_ecriture(
    acces: AccesRequis, entreprise: str, exercice: str, journal: str, numero: int
) -> EcritureComptable:
    """Engage l'écriture au nom de celui qui valide.

    Après quoi la seule correction possible est la contre-passation : c'est le
    principe d'intangibilité, et c'est ce qui distingue une comptabilité d'un
    tableur.
    """
    exiger_dossier(acces, Permission.VALIDER_ECRITURE, entreprise)
    cle = f"{exercice}/{journal}/{numero:06d}"
    try:
        validee = valider_une_ecriture(
            cle,
            exercice=_exercice_du_dossier(entreprise, exercice),
            periodes_verrouillees=periodes_verrouillees_du_dossier(entreprise),
            depot=depot(entreprise),
            par=acces.compte,
            le=maintenant(),
        )
    except EcritureIntrouvable as absente:
        raise HTTPException(status_code=404, detail=str(absente)) from absente
    except ValueError as refus:
        raise HTTPException(status_code=409, detail=message_lisible(refus)) from refus
    _comptabiliser_la_piece_citee(acces, entreprise, validee)
    return validee


def _comptabiliser_la_piece_citee(acces, entreprise: str, ecriture: EcritureComptable) -> None:
    """La pièce que l'écriture validée cite termine son traitement (pas 107).

    ⚠️ Avant le pas 107, rien ne le faisait : voir l'en-tête de
    `collecte/application/traitement.py`. Par la surface publique de la collecte, l'arête
    `comptabilite → collecte` étant déclarée.

    Une pièce non reconnue sans doute n'est pas rattachée, et la validation n'échoue pas pour
    autant : l'écriture est juste, c'est le rattachement qui reste à faire (classer le doublon,
    ou la pièce se retrouve dans la clôture du mois).
    """
    from app.contextes.collecte.api import (
        DepotPiecesSql,
        comptabiliser_la_piece,
        pieces_en_memoire,
    )

    session = session_de_travail()
    pieces = pieces_en_memoire() if session is None else DepotPiecesSql(session, courant())
    piece = comptabiliser_la_piece(
        pieces,
        entreprise=entreprise,
        piece_justificative=ecriture.piece_justificative,
        cle_ecriture=ecriture.cle,
    )
    if piece is not None:
        atelier().journal.ajouter(
            horodatage=maintenant(),
            acteur=acces.compte,
            action="collecte.piece_comptabilisee",
            objet_type="piece_justificative",
            objet_id=piece.identifiant,
            apres={"dossier": entreprise, "ecriture": ecriture.cle, "etat": piece.etat.value},
        )


@routeur.post(
    "/dossiers/{entreprise}/ecritures/{exercice}/{journal}/{numero}/contre-passation",
    summary="Contre-passer une écriture validée",
    status_code=201,
)
def contrepasser_ecriture(
    acces: AccesRequis,
    entreprise: str,
    exercice: str,
    journal: str,
    numero: int,
    demande: DemandeContrepassation,
) -> EcritureComptable:
    """Fabrique l'écriture inverse, en brouillon, et la rend.

    ⚠️ Le motif est **obligatoire**, et le domaine le refuse vide. Une
    contre-passation sans motif est une trace illisible : six mois plus tard,
    personne ne sait si l'écriture d'origine était fausse ou si elle a été
    annulée par erreur.
    """
    # ⚠️ Le motif part **au contrôle d'accès**, et pas seulement au domaine.
    # `CONTRE_PASSER` figure dans `EXIGE_MOTIF` : le socle refuse l'acte tant que
    # le motif n'est pas recueilli, et il le journalise. Le domaine le refuse une
    # seconde fois, pour la trace écrite dans l'écriture elle-même. Les deux
    # contrôles ont des raisons d'être différentes, et aucun ne remplace l'autre.
    exiger_dossier(acces, Permission.CONTRE_PASSER, entreprise, motif=demande.motif)
    cle = f"{exercice}/{journal}/{numero:06d}"
    try:
        return contrepasser_une_ecriture(
            cle,
            motif=demande.motif,
            jour=demande.date_operation or maintenant().date(),
            exercice=_exercice_du_dossier(entreprise, exercice),
            periodes_verrouillees=periodes_verrouillees_du_dossier(entreprise),
            depot=depot(entreprise),
            par=acces.compte,
        )
    except EcritureIntrouvable as absente:
        raise HTTPException(status_code=404, detail=str(absente)) from absente
    except ValueError as refus:
        raise HTTPException(status_code=409, detail=message_lisible(refus)) from refus


# ── La pièce contrôlée devient une écriture proposée ──────────────────────────
#
# ⚠️ **CE CHAÎNON MANQUAIT, ET IL EST LE CŒUR DU PRODUIT.**
#
# `proposition_ecriture.py` annonce depuis sa première ligne ce qu'il referme :
# *« la boîte de réception mène au rapport, le rapport mène à l'écriture »*.
# Aucune route ne l'empruntait : `proposer_ecriture_achat` n'était appelée que par
# le jeu de démonstration.
#
# Le comptable disposait donc de deux gestes sans passerelle — contrôler une
# facture d'un côté, saisir une écriture à la main de l'autre — alors que le
# moteur sait déduire la seconde de la première, TVA rejetée comprise. Le lien
# entre le verdict et l'imputation reposait entièrement sur sa vigilance.


class DemandeDeProposition(BaseModel):
    """La facture à contrôler, et le journal où l'écriture irait.

    ⚠️ **Le régime n'est pas demandé** : il est lu au portefeuille. Le laisser
    fournir par l'appelant permettrait de récupérer une TVA qu'une entreprise au
    synthétique ne récupère jamais, en cochant une case.

    ⚠️ Cette phrase était fausse jusqu'au pas 73 : la facture de la requête portait
    un régime du destinataire, et c'est lui qui décidait. Il est désormais remplacé
    par celui du portefeuille, voir `rattacher_au_dossier`.
    """

    facture: FactureAControler
    #: Le journal d'achats par défaut. Nommé plutôt que déduit : un cabinet peut
    #: tenir plusieurs journaux d'achats, et deviner le sien serait deviner.
    journal: str = "AC"
    #: L'exercice de destination. Vide, celui de la date d'opération.
    exercice: str | None = None
    #: ⚠️ Par défaut la **date de la facture**, jamais aujourd'hui : imputer une
    #: facture de juillet au jour de la saisie déplacerait la charge d'exercice.
    date_operation: date | None = None


class PropositionDEcriture(BaseModel):
    """Ce que le contrôle propose, et ce qu'il refuse.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **`saisie` EST LE CORPS DE LA REQUÊTE SUIVANTE**, au champ près.

    Ce n'est pas une commodité : c'est ce qui empêche l'écran de recomposer
    l'écriture à sa façon. Un client qui reconstruirait les lignes à partir du
    rapport referait le travail du moteur, et le referait autrement le jour où
    une règle change.

    ⚠️ **RIEN N'EST ENREGISTRÉ.** Le comptable garde la main : il relit, corrige,
    puis poste lui-même vers `/ecritures`. Un logiciel qui comptabiliserait tout
    seul déplacerait la responsabilité vers l'éditeur, alors que c'est le Centre
    qui engage son agrément.
    ─────────────────────────────────────────────────────────────────────────────
    """

    #: Faux quand une anomalie bloquante interdit la comptabilisation.
    comptabilisable: bool
    #: Le verdict composé — glyphe, titre, phrase. ⚠️ Le **même objet** que rend
    #: `/conformite/controler` : deux écrans qui afficheraient deux formes du même
    #: verdict finiraient par en afficher deux sens.
    verdict: Verdict
    rapport: RapportConformite
    #: Le corps prêt à être posté vers `/ecritures`. `None` si non comptabilisable.
    saisie: DemandeSaisie | None = None
    #: Le numéro que le registre attribuerait **à cet instant**. Indicatif : c'est
    #: la saisie qui numérote, et une autre saisie peut passer entre-temps.
    numero_pressenti: int | None = None
    #: Les anomalies qui s'opposent à la comptabilisation, en clair.
    empechements: list[str] = []
    #: Ce que le portefeuille a rectifié sur le destinataire déclaré (pas 73) : un
    #: NIU manquant, un régime différent. Dit, parce qu'une TVA qui disparaît sans
    #: explication se « corrige » à la main.
    rectifications: list[str] = []


@routeur.post(
    "/dossiers/{entreprise}/propositions",
    summary="Proposer l'écriture d'une facture contrôlée",
    responses={
        403: {"description": "Habilitation insuffisante sur ce dossier"},
        404: {"description": "Dossier inconnu"},
        409: {"description": "La facture ne permet aucune écriture équilibrée"},
    },
)
def proposer_depuis_une_facture(
    acces: AccesRequis, entreprise: str, demande: DemandeDeProposition
) -> PropositionDEcriture:
    """Contrôle la facture, puis rend l'écriture qui en découle. **Sans rien écrire.**

    ─────────────────────────────────────────────────────────────────────────────
    POURQUOI `SAISIR_ECRITURE` SEULE, ET NON AUSSI `CONTROLER_CONFORMITE`

    Le contrôle n'est pas ici un droit qu'on accorde : c'est une **contrainte
    qu'on impose**. Exiger les deux permissions rendrait le chemin sûr plus
    difficile que le chemin libre — un comptable habilité à saisir mais pas à
    contrôler passerait par la saisie manuelle et perdrait la vérification.

    ⚠️ C'est l'inversion à éviter : *un garde-fou qui coûte plus cher que son
    contournement ne garde rien.*

    UNE ANOMALIE BLOQUANTE N'EST PAS UNE ERREUR DE REQUÊTE

    La réponse reste **200**, avec `comptabilisable: false` et les empêchements en
    clair. Un 4xx ferait croire à une requête fautive, alors que c'est un fait
    métier : la facture est bien formée, et c'est elle qui ne permet pas d'écrire.

    Le 409 est réservé au cas où les montants ne se recoupent pas — une facture
    dont le total ne vaut pas la somme de ses lignes n'a **aucune** écriture
    équilibrée, et ce n'est pas la même chose que « elle en a une, interdite ».

    LE NUMÉRO EST PRESSENTI, JAMAIS RÉSERVÉ

    `proposer_ecriture_achat` exige un numéro pour construire son entité. Celui-ci
    vient du registre et sert à l'affichage ; il n'est **pas** consommé. Le
    réserver créerait un trou dans le journal si le comptable renonce — et *un
    trou dans un journal est le premier signal que cherche un contrôleur*.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger_dossier(acces, Permission.SAISIR_ECRITURE, entreprise)

    quand = demande.date_operation or demande.facture.document.date_emission

    # ⚠️ Pas 73 : le régime vient du portefeuille, à la date de l'opération, et plus
    # de la requête. Voir `rattacher_au_dossier`. Le contrôle de conformité se fait
    # ensuite sur la facture rattachée : ses règles lisent aussi ce régime.
    dossier = _dossier_du_portefeuille(entreprise)
    if dossier is None:
        raise HTTPException(
            status_code=404, detail=f"dossier {entreprise} inconnu du portefeuille."
        )
    try:
        regime_fiscal = dossier.regime_au(quand)
    except StatutIntrouvable as absence:
        raise HTTPException(status_code=409, detail=str(absence)) from absence
    regime = (
        RegimeEmetteur(regime_fiscal.value)
        if regime_fiscal.value in RegimeEmetteur.__members__
        else RegimeEmetteur.INCONNU
    )
    try:
        facture, rectifications = rattacher_au_dossier(
            demande.facture, niu=entreprise, regime=regime
        )
    except FactureDUnAutreDossier as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    exercice = demande.exercice or str(quand.year)

    # ⚠️ Pas 92 : le rapport **arbitré**. Un constat que le cabinet a écarté (TVA
    # rejetée sur un règlement que le relevé bancaire atteste par virement) ne doit
    # plus rejeter la TVA de l'écriture proposée. Sans cet arbitrage, l'écran de la
    # pièce dirait « écarté » et la proposition rejetterait quand même : le comptable
    # irait saisir à la main pour « corriger », c'est-à-dire sans aucun contrôle.
    rapport = rapport_arbitre(_moteur_de_conformite().controler(facture, quand), entreprise)
    verdict = composer_verdict(rapport)

    numero = depot(entreprise).prochain_numero(exercice, demande.journal)
    try:
        proposee = proposer_ecriture_achat(
            facture,
            rapport,
            imputation().charger(entreprise),
            journal=demande.journal,
            exercice=exercice,
            numero=numero,
            saisie_par=acces.compte,
            date_operation=quand,
        )
    except ComptabilisationInterdite as refus:
        # ⚠️ Le refus porte les anomalies : sans elles, le comptable saurait que
        # c'est interdit et pas pourquoi, donc irait saisir à la main.
        return PropositionDEcriture(
            comptabilisable=False,
            verdict=verdict,
            rapport=rapport,
            empechements=[str(refus)],
            rectifications=rectifications,
        )
    except ImputationImpossible as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus

    return PropositionDEcriture(
        comptabilisable=True,
        verdict=verdict,
        rapport=rapport,
        rectifications=rectifications,
        numero_pressenti=numero,
        saisie=DemandeSaisie(
            journal=proposee.journal,
            exercice=proposee.exercice,
            date_operation=proposee.date_operation,
            libelle=proposee.libelle,
            piece_justificative=proposee.piece_justificative,
            reference_externe=proposee.reference_externe,
            lignes=list(proposee.lignes),
        ),
    )


def _moteur_de_conformite():
    """Le moteur de contrôle, obtenu par la **surface publique** de la conformité.

    ⚠️ Ce montage a été recopié ici, depuis l'adaptateur entrant de la conformité,
    et le test d'architecture l'a refusé : *on n'entre chez l'autre que par sa
    surface publique.* Il avait raison, et pas pour une raison de forme — deux
    montages auraient divergé au premier changement de dépôt, et deux écrans
    auraient rendu deux verdicts sur la même facture.

    L'arête `comptabilite → conformite` est déclarée au registre des services :
    ce n'est pas une dépendance qu'on s'accorde ici, c'est une dépendance que
    l'architecture autorise et que le test garde.
    """
    from app.contextes.conformite.api import moteur_par_defaut

    return moteur_par_defaut()


# ── L'échange avec le logiciel du client ─────────────────────────────────────
#
# ⚠️ **UN CENTRE DE GESTION NE REMPLACE PAS LE LOGICIEL DE SES ADHÉRENTS.**
#
# Il s'y branche. Chaque adhérent arrive avec le sien, et la plateforme doit
# s'adapter au système final, jamais l'inverse. Le format est donc une **donnée**
# du référentiel : brancher un progiciel de plus, c'est déposer un fichier, pas
# modifier ce module.


class ProfilPublie(BaseModel):
    """Un profil d'échange, tel qu'un écran le propose."""

    code: str
    libelle: str
    #: Ce que le centre sait de ce profil, et ce qu'il ne garantit pas. Repris du
    #: référentiel : un exploitant qui choisit doit le lire sans ouvrir le dépôt.
    remarque: str
    colonnes: list[str]
    encodage: str


@routeur.get("/echange/profils", summary="Les formats d'export disponibles")
def lister_les_profils(acces: AccesRequis) -> list[ProfilPublie]:
    """Ce que le référentiel propose, à l'instant.

    ⚠️ Lue au référentiel et non écrite ici : un écran qui recopierait la liste
    proposerait un profil retiré, ou tairait un profil ajouté le matin même.
    """
    exiger(acces, Permission.LIRE_COMPTABILITE)
    return [
        ProfilPublie(
            code=profil.code,
            libelle=profil.libelle,
            remarque=profil.remarque,
            colonnes=[c.intitule or c.champ for c in profil.colonnes],
            encodage=profil.encodage,
        )
        for profil in _profils().values()
    ]


@routeur.get(
    "/dossiers/{entreprise}/export",
    summary="Exporter les écritures vers le logiciel du client",
    responses={
        403: {"description": "Habilitation insuffisante sur ce dossier"},
        404: {"description": "Profil d'échange inconnu"},
        409: {"description": "Le lot contient une écriture non validée"},
    },
)
def exporter(
    acces: AccesRequis,
    entreprise: str,
    exercice: str = Query(description="L'exercice à exporter."),
    profil: str = Query(
        "pivot-csv", description="Le code d'un profil du référentiel."
    ),
    journal: str | None = Query(None, description="Restreint à un journal."),
) -> Response:
    """Le fichier, encodé comme le logiciel destinataire l'attend.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **SEULES LES ÉCRITURES VALIDÉES SORTENT, ET LE REFUS EST FRANC.**

    Un brouillon est une écriture que le centre n'a pas engagée. L'exporter
    pousserait dans le système du client des mouvements que personne n'assume, et
    il les tiendrait pour arrêtés — il déclare avec, puis découvre au contrôle que
    le centre les a corrigés depuis.

    Le lot est donc refusé **en entier**, en nommant les écritures en cause. Un
    export qui filtrerait en silence livrerait un fichier incomplet, et l'écart ne
    se verrait qu'à la balance.

    ⚠️ **LE FICHIER EST RENDU EN OCTETS, PAS EN TEXTE.**

    L'encodage fait partie du format : le rendre en JSON ferait passer le contenu
    par de l'UTF-8, et le client recevrait des libellés abîmés après avoir cru
    télécharger le bon fichier. C'est le piège le plus coûteux de cet échange,
    parce qu'il ne se voit qu'après l'import.

    Les caractères que l'encodage cible ne connaît pas sont **remplacés**, jamais
    fatals : un libellé qui porterait un caractère exotique ne doit pas faire
    perdre le fichier entier d'un exercice.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)

    try:
        choisi = _profils()[profil]
    except KeyError as absent:
        raise HTTPException(
            status_code=404,
            detail=(
                f"aucun profil d'échange « {profil} ». Les profils du référentiel "
                f"sont : {', '.join(sorted(_profils())) or 'aucun'}."
            ),
        ) from absent

    ecritures = depot(entreprise).toutes(exercice)
    if journal is not None:
        ecritures = [e for e in ecritures if e.journal == journal]

    try:
        contenu = rendre(ecritures, choisi)
    except EchangeRefuse as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus

    octets = contenu.encode(choisi.encodage, errors="replace")
    nom = f"{entreprise}-{exercice}{'-' + journal if journal else ''}-{choisi.code}.csv"
    return Response(
        content=octets,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{nom}"',
            # ⚠️ L'encodage est annoncé dans l'en-tête **et** appliqué au contenu.
            # Un navigateur qui devine se trompe une fois sur deux, et le client
            # ouvre un fichier abîmé dans son tableur avant même de l'importer.
            "Content-Type": f"text/csv; charset={choisi.encodage}",
        },
    )


def _profils():
    """Les profils du référentiel, relus à chaque appel.

    ⚠️ Non mémoïsés, contrairement au moteur de conformité : un export se demande
    quelques fois par mois, et un profil corrigé le matin doit servir l'après-midi
    **sans redéploiement**. C'est le sens même de l'avoir mis au référentiel.
    """
    return charger_les_profils(configuration().dossier_referentiel / "echange")


#: ⚠️ Un plafond en octets, et pas en écritures : le contrôle doit porter sur ce
#: qui est reçu, avant d'être compris. Un exercice de PME fait quelques milliers
#: de lignes, soit quelques centaines de kilo-octets ; huit méga-octets laissent
#: dix fois la marge et n'épuisent pas la mémoire d'un conteneur.
TAILLE_MAXIMALE_DE_REPRISE = 8 * 1024 * 1024


@routeur.post(
    "/dossiers/{entreprise}/reprise",
    summary="Reprendre un fichier d'écritures venu du logiciel du client",
    responses={
        403: {"description": "Habilitation insuffisante sur ce dossier"},
        404: {"description": "Profil d'échange inconnu"},
        409: {"description": "Le fichier ne se lit pas avec ce profil"},
        413: {"description": "Fichier trop volumineux"},
    },
)
async def reprendre(
    acces: AccesRequis,
    entreprise: str,
    fichier: UploadFile = File(description="Le fichier exporté par l'autre logiciel."),
    exercice: str = Query(description="L'exercice dans lequel reprendre."),
    profil: str = Query("pivot-csv", description="Le code d'un profil du référentiel."),
    journal: str | None = Query(
        None, description="Le journal, quand le fichier n'en porte pas."
    ),
    appliquer: bool = Query(
        False,
        description=(
            "Faux, le défaut : le rapport dit ce qui entrerait, rien n'est écrit."
        ),
    ),
) -> RapportDeReprise:
    """Relit le fichier avec le profil déclaré, et rend ce qui entrerait.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LE MODE CONTRÔLE EST LE DÉFAUT.**

    Reprendre un exercice entier est irréversible, et pas seulement en pratique :
    les écritures entrent en brouillon, et **aucune route ne supprime un brouillon**.
    Ce texte disait « donc elles se suppriment » ; c'était faux (pas 72). Un brouillon
    se corrige à son numéro, se valide, se contre-passe : quatre mille brouillons
    d'un mauvais fichier n'ont pas d'autre issue, et la question est ouverte (Q23).
    Se tromper de fichier, de dossier ou d'exercice doit se voir **avant**.

    ⚠️ **LA PERMISSION EST CELLE DE LA SAISIE, PAS CELLE DE LA LECTURE.**

    Une reprise produit des écritures. Qu'elles viennent d'un fichier plutôt que
    d'un formulaire ne change rien à ce qu'elles sont, et le mode contrôle exige
    le même droit que le mode application : le rapport de contrôle **montre le
    contenu comptable du fichier**, ce qui n'est pas une lecture publique.

    TROIS STATUTS, TROIS SENS DIFFÉRENTS

    `404` le profil n'existe pas au référentiel, donc la demande désigne un format
    inconnu du centre.

    `409` le fichier ne se lit pas **du tout** avec ce profil : encodage, charabia,
    profil incapable de relire. Ce n'est pas un défaut de la requête, c'est un
    désaccord entre le fichier et le format annoncé.

    `200` avec des anomalies : le fichier se lit, et son contenu est refusé ligne
    par ligne. C'est un résultat, pas une erreur, et il faut qu'un écran puisse
    l'afficher en entier plutôt qu'un message d'échec.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger_dossier(acces, Permission.SAISIR_ECRITURE, entreprise)

    try:
        choisi = _profils()[profil]
    except KeyError as absent:
        raise HTTPException(
            status_code=404,
            detail=(
                f"aucun profil d'échange « {profil} ». Les profils du référentiel "
                f"sont : {', '.join(sorted(_profils())) or 'aucun'}."
            ),
        ) from absent

    octets = await fichier.read()
    if len(octets) > TAILLE_MAXIMALE_DE_REPRISE:
        raise HTTPException(
            status_code=413,
            detail=(
                f"fichier de {len(octets)} octets, au-delà des "
                f"{TAILLE_MAXIMALE_DE_REPRISE} admis. Un exercice de PME tient "
                "largement dedans : vérifier qu'il s'agit bien d'un export "
                "d'écritures et non d'une archive."
            ),
        )

    plan = _plan.charger()
    try:
        lot = lire(
            octets,
            choisi,
            exercice=exercice,
            journal_par_defaut=journal,
            # ⚠️ Le plan du dossier est passé au lecteur pour que le compte
            # inconnu sorte **avec son rang de ligne**. Le contrôle d'environnement
            # le refuserait aussi, mais en nommant l'écriture, pas la ligne du
            # fichier : l'exploitant chercherait dans le mauvais outil.
            comptes_connus=frozenset(c.numero for c in plan),
        )
    except EchangeRefuse as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus

    return reprendre_un_lot(
        lot,
        journaux=_journaux.charger(),
        plan=plan,
        exercice=_exercice_du_dossier(entreprise, exercice),
        periodes_verrouillees=periodes_verrouillees_du_dossier(entreprise),
        depot=depot(entreprise),
        par=acces.compte,
        appliquer=appliquer,
    )
