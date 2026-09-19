"""API du rapprochement bancaire (pas 101), sous `/comptabilite`.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI UN MODULE À PART

`routes_http.py` porte déjà la tenue du journal, la balance, l'échange et la reprise. Le
rapprochement est un geste complet, avec son objet, ses réglages et son écran : il a son
module, monté sous le même préfixe, comme les décisions sur le référentiel ont le leur.

LES PERMISSIONS, ET POURQUOI CES TROIS-LÀ

* lire : `LIRE_COMPTABILITE`, comme le grand livre dont le rapprochement est le contrôle ;
* importer, rapprocher, dissocier, justifier, abandonner : `SAISIR_ECRITURE`. Ce sont des
  gestes de préparation, réversibles tant que le rapprochement est en cours ;
* arrêter le rapprochement : `VALIDER_ECRITURE`. L'état arrêté ne bougera plus, et il se
  relit à la révision : il engage quelqu'un, comme la validation d'une écriture.

Le chargé de clientèle lit la comptabilité sans saisir : il voit les rapprochements, et
reçoit la notification quand une pièce est à demander à l'adhérent.

QUI A FAIT QUOI : LE NOM DANS LE RAPPROCHEMENT, LE COMPTE AU JOURNAL

Le rapprochement retient le **nom** de la personne (« arrêté par Léonard FOTSO ») : c'est
ce que lit un réviseur, qui ne connaît pas les identifiants de comptes. Le journal d'audit
garde l'identifiant du compte comme acteur : c'est lui qui fait foi.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import base64
import binascii
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.contextes.comptabilite.adaptateurs.entrant.routes_http import _journaux, depot
from app.contextes.comptabilite.adaptateurs.sortant.depots_rapprochements import (
    depot_des_rapprochements,
)
from app.contextes.comptabilite.adaptateurs.sortant.rapprochement_yaml import (
    charger_les_profils_de_releve,
    charger_les_reglages_du_rapprochement,
)
from app.contextes.comptabilite.application.rapprochement import (
    abandonner_un_rapprochement,
    dissocier_une_ligne,
    importer_un_releve,
    justifier_une_ligne,
    rapprocher_une_ligne,
    rapproches_par_les_autres,
    valider_le_rapprochement,
)
from app.contextes.comptabilite.domaine.entites import Sens
from app.contextes.comptabilite.domaine.rapprochement import (
    Appariement,
    EtatDeRapprochement,
    Justification,
    LigneDeReleve,
    MouvementComptable,
    NatureJustification,
    ProfilDeReleve,
    Proposition,
    RapprochementBancaire,
    RapprochementRefuse,
    ReglagesDuRapprochement,
    ReleveIllisible,
    StatutRapprochement,
    etat_du_rapprochement,
    lire_le_releve,
    mouvements_du_compte,
    proposer_des_correspondances,
)
from app.contextes.transverse.api import (
    Acces,
    AccesRequis,
    Permission,
    atelier,
    exiger,
    exiger_dossier,
)
from app.infrastructure.config import configuration
from app.partage.horloge import maintenant

routeur = APIRouter(prefix="/comptabilite", tags=["Comptabilité · rapprochement"])


def _reglages() -> ReglagesDuRapprochement:
    # Relus à chaque appel : un petit fichier, qu'on doit voir changer sans redémarrer.
    return charger_les_reglages_du_rapprochement(configuration().dossier_referentiel)


def _profils() -> dict[str, ProfilDeReleve]:
    return charger_les_profils_de_releve(configuration().dossier_referentiel)


def _mouvements(dossier: str, compte: str, exercice: str) -> list[MouvementComptable]:
    """Tous les mouvements du compte sur l'exercice, à-nouveaux compris (solde comptable)."""
    return mouvements_du_compte(depot(dossier).lister(exercice), compte)


def _refus(erreur: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(erreur))


def _journaliser(
    acces: Acces, action: str, r: RapprochementBancaire, motif: str | None = None, **donnees
) -> None:
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action=action,
        objet_type="rapprochement_bancaire",
        objet_id=r.identifiant,
        apres={
            "dossier": r.dossier,
            "journal": r.journal,
            "identifiant": r.identifiant,
            "du": r.du.isoformat(),
            "au": r.au.isoformat(),
            "par": acces.nom_complet,
            **donnees,
        },
        motif=motif,
    )


# ── Ce que l'écran lit ────────────────────────────────────────────────────────


class ResumeDeRapprochement(BaseModel):
    identifiant: str
    journal: str
    compte: str
    du: date
    au: date
    statut: StatutRapprochement
    source: str
    lignes: int
    a_traiter: int
    ecart_inexplique: Decimal


class LigneLue(BaseModel):
    ligne: LigneDeReleve
    appariement: Appariement | None = None
    #: Le mouvement rapproché, pour l'afficher sans relire l'écriture.
    mouvement: MouvementComptable | None = None
    justification: Justification | None = None
    #: Pour une ligne ni rapprochée ni justifiée : les écritures possibles, les plus
    #: probables d'abord, chacune avec ses motifs.
    propositions: list[Proposition] = Field(default_factory=list)


class VueRapprochement(BaseModel):
    rapprochement: RapprochementBancaire
    etat: EtatDeRapprochement
    #: Les lignes à traiter d'abord : ce qui résiste en haut, ce qui est fait en bas.
    lignes: list[LigneLue]
    #: Les mouvements comptables de la période absents du relevé (chèques non encaissés…).
    mouvements_non_rapproches: list[MouvementComptable]
    reglages: ReglagesDuRapprochement


def _vue(r: RapprochementBancaire) -> VueRapprochement:
    mouvements = _mouvements(r.dossier, r.compte, r.exercice)
    ailleurs = rapproches_par_les_autres(depot_des_rapprochements(), r)
    reglages = _reglages()
    par_designation = {m.designation: m for m in mouvements}
    deja = r.designations | ailleurs
    lignes = []
    for ligne in r.lignes:
        appariement = r.appariement(ligne.rang)
        justification = r.justification(ligne.rang)
        propositions = (
            proposer_des_correspondances(ligne, mouvements, deja, reglages)
            if appariement is None and r.statut is StatutRapprochement.EN_COURS
            else []
        )
        lignes.append(
            LigneLue(
                ligne=ligne,
                appariement=appariement,
                mouvement=par_designation.get((appariement.ecriture, appariement.ligne))
                if appariement
                else None,
                justification=justification,
                propositions=propositions,
            )
        )
    lignes.sort(
        key=lambda l_: (l_.appariement is not None, l_.justification is not None, l_.ligne.rang)
    )
    return VueRapprochement(
        rapprochement=r,
        etat=etat_du_rapprochement(r, mouvements, ailleurs),
        lignes=lignes,
        mouvements_non_rapproches=[
            m for m in mouvements if r.du <= m.date <= r.au and m.designation not in deja
        ],
        reglages=reglages,
    )


def _du_dossier(niu: str, identifiant: str) -> RapprochementBancaire:
    for r in depot_des_rapprochements().du_dossier(niu):
        if r.identifiant == identifiant:
            return r
    raise HTTPException(status_code=404, detail=f"rapprochement « {identifiant} » inconnu.")


@routeur.get("/releves/profils", summary="Les formats de relevé bancaire acceptés à l'import")
def lire_les_profils(acces: AccesRequis) -> list[ProfilDeReleve]:
    exiger(acces, Permission.LIRE_COMPTABILITE)
    return list(_profils().values())


@routeur.get(
    "/dossiers/{entreprise}/rapprochements",
    summary="Les relevés importés d'un dossier, avec ce qui reste à traiter",
)
def lister_les_rapprochements(acces: AccesRequis, entreprise: str) -> list[ResumeDeRapprochement]:
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    resumes = []
    tous = depot_des_rapprochements().du_dossier(entreprise)
    for r in sorted(tous, key=lambda r: (r.au, r.identifiant), reverse=True):
        etat = etat_du_rapprochement(
            r,
            _mouvements(entreprise, r.compte, r.exercice),
            rapproches_par_les_autres(depot_des_rapprochements(), r),
        )
        resumes.append(
            ResumeDeRapprochement(
                identifiant=r.identifiant,
                journal=r.journal,
                compte=r.compte,
                du=r.du,
                au=r.au,
                statut=r.statut,
                source=r.source,
                lignes=etat.lignes,
                a_traiter=etat.a_traiter,
                ecart_inexplique=etat.ecart_inexplique,
            )
        )
    return resumes


class LigneSaisie(BaseModel):
    """Une ligne de relevé saisie à la main. Montant **signé** : positif = reçu."""

    date: date
    libelle: str = Field(min_length=1, max_length=300)
    montant: Decimal


class DemandeDImport(BaseModel):
    journal: str = Field(min_length=1, max_length=8)
    du: date
    au: date
    solde_initial: Decimal
    solde_final: Decimal
    exercice: str | None = Field(None, description="Défaut : l'année de fin du relevé")
    #: Un fichier, lu selon un profil…
    profil: str | None = None
    fichier_base64: str | None = None
    #: … ou les lignes saisies (banques locales sans export).
    lignes: list[LigneSaisie] | None = None


@routeur.post(
    "/dossiers/{entreprise}/rapprochements",
    summary="Importer un relevé et rapprocher ce qui ne fait aucun doute",
    status_code=201,
)
def importer(acces: AccesRequis, entreprise: str, demande: DemandeDImport) -> VueRapprochement:
    exiger_dossier(acces, Permission.SAISIR_ECRITURE, entreprise)
    journal = next((j for j in _journaux.charger() if j.code == demande.journal), None)
    if journal is None:
        raise HTTPException(status_code=422, detail=f"journal {demande.journal} inconnu.")
    par_fichier = demande.profil is not None or demande.fichier_base64 is not None
    if par_fichier == (demande.lignes is not None):
        raise HTTPException(
            status_code=422,
            detail="fournir soit un fichier et son profil, soit les lignes saisies, pas les deux.",
        )
    try:
        if par_fichier:
            profil = _profils().get(demande.profil or "")
            if profil is None:
                connus = ", ".join(_profils()) or "aucun"
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"profil « {demande.profil} » inconnu. Profils du référentiel : {connus}."
                    ),
                )
            try:
                contenu = base64.b64decode(demande.fichier_base64 or "", validate=True)
            except binascii.Error as erreur:
                raise HTTPException(status_code=422, detail="fichier mal transmis.") from erreur
            lignes = lire_le_releve(contenu, profil)
            source = profil.code
        else:
            lignes = []
            for rang, saisie in enumerate(demande.lignes or [], start=1):
                if saisie.montant == 0:
                    raise HTTPException(status_code=422, detail=f"ligne {rang} : montant nul.")
                lignes.append(
                    LigneDeReleve(
                        rang=rang,
                        date=saisie.date,
                        libelle=saisie.libelle,
                        montant=abs(saisie.montant),
                        sens=Sens.DEBIT if saisie.montant > 0 else Sens.CREDIT,
                    )
                )
            if not lignes:
                raise HTTPException(status_code=422, detail="aucune ligne saisie.")
            source = "saisie"
        exercice = demande.exercice or str(demande.au.year)
        rapprochement = importer_un_releve(
            dossier=entreprise,
            journal=journal,
            exercice=exercice,
            du=demande.du,
            au=demande.au,
            solde_initial=demande.solde_initial,
            solde_final=demande.solde_final,
            lignes=lignes,
            source=source,
            mouvements=(
                _mouvements(entreprise, journal.compte_contrepartie, exercice)
                if journal.compte_contrepartie
                else []
            ),
            reglages=_reglages(),
            par=acces.nom_complet,
            le=maintenant(),
            depot=depot_des_rapprochements(),
        )
    except (ReleveIllisible, RapprochementRefuse) as refus:
        raise _refus(refus) from refus
    _journaliser(acces, "comptabilite.releve_importe", rapprochement, lignes=len(lignes))
    return _vue(rapprochement)


@routeur.get(
    "/dossiers/{entreprise}/rapprochements/{identifiant}",
    summary="Un rapprochement : l'état, les lignes qui résistent et leurs propositions",
)
def lire_le_rapprochement(
    acces: AccesRequis, entreprise: str, identifiant: str
) -> VueRapprochement:
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    return _vue(_du_dossier(entreprise, identifiant))


class DemandeDAppariement(BaseModel):
    rang: int = Field(ge=1)
    #: La clé de l'écriture (`2026/BQ/000012`) et l'index de sa ligne, à partir de 0.
    ecriture: str
    ligne: int = Field(ge=0)


@routeur.post(
    "/dossiers/{entreprise}/rapprochements/{identifiant}/appariements",
    summary="Rapprocher une ligne du relevé d'une ligne d'écriture",
)
def apparier(
    acces: AccesRequis, entreprise: str, identifiant: str, demande: DemandeDAppariement
) -> VueRapprochement:
    exiger_dossier(acces, Permission.SAISIR_ECRITURE, entreprise)
    r = _du_dossier(entreprise, identifiant)
    try:
        rapproche = rapprocher_une_ligne(
            dossier=entreprise,
            identifiant=identifiant,
            rang=demande.rang,
            ecriture=demande.ecriture,
            ligne=demande.ligne,
            mouvements=_mouvements(entreprise, r.compte, r.exercice),
            reglages=_reglages(),
            par=acces.nom_complet,
            le=maintenant(),
            depot=depot_des_rapprochements(),
        )
    except RapprochementRefuse as refus:
        raise _refus(refus) from refus
    return _vue(rapproche)


@routeur.post(
    "/dossiers/{entreprise}/rapprochements/{identifiant}/lignes/{rang}/dissociation",
    summary="Défaire un rapprochement de ligne",
)
def dissocier(acces: AccesRequis, entreprise: str, identifiant: str, rang: int) -> VueRapprochement:
    exiger_dossier(acces, Permission.SAISIR_ECRITURE, entreprise)
    _du_dossier(entreprise, identifiant)
    try:
        dissocie = dissocier_une_ligne(
            dossier=entreprise, identifiant=identifiant, rang=rang, depot=depot_des_rapprochements()
        )
    except RapprochementRefuse as refus:
        raise _refus(refus) from refus
    return _vue(dissocie)


class DemandeDeJustification(BaseModel):
    nature: NatureJustification
    motif: str = Field(min_length=1, max_length=500)


@routeur.post(
    "/dossiers/{entreprise}/rapprochements/{identifiant}/lignes/{rang}/justification",
    summary="Expliquer une ligne du relevé sans écriture",
)
def justifier(
    acces: AccesRequis,
    entreprise: str,
    identifiant: str,
    rang: int,
    demande: DemandeDeJustification,
) -> VueRapprochement:
    exiger_dossier(acces, Permission.SAISIR_ECRITURE, entreprise)
    _du_dossier(entreprise, identifiant)
    try:
        justifie = justifier_une_ligne(
            dossier=entreprise,
            identifiant=identifiant,
            rang=rang,
            nature=demande.nature,
            motif=demande.motif,
            par=acces.nom_complet,
            le=maintenant(),
            depot=depot_des_rapprochements(),
        )
    except RapprochementRefuse as refus:
        raise _refus(refus) from refus
    if demande.nature is NatureJustification.PIECE_DEMANDEE:
        # ⚠️ Le contrôle en amont : un mouvement sans pièce se demande à l'adhérent, plutôt
        # que d'être passé en écriture d'attente. Le journal d'audit porte la demande, et les
        # notifications (pas 94) la font arriver à qui relance l'adhérent.
        ligne = justifie.ligne(rang)
        _journaliser(
            acces,
            "comptabilite.piece_bancaire_demandee",
            justifie,
            motif=demande.motif.strip(),
            rang=rang,
            date=ligne.date.isoformat(),
            libelle=ligne.libelle,
            montant=str(ligne.signe),
        )
    return _vue(justifie)


@routeur.post(
    "/dossiers/{entreprise}/rapprochements/{identifiant}/validation",
    summary="Arrêter le rapprochement : l'état ne bougera plus",
)
def valider(acces: AccesRequis, entreprise: str, identifiant: str) -> VueRapprochement:
    exiger_dossier(acces, Permission.VALIDER_ECRITURE, entreprise)
    r = _du_dossier(entreprise, identifiant)
    try:
        valide = valider_le_rapprochement(
            dossier=entreprise,
            identifiant=identifiant,
            mouvements=_mouvements(entreprise, r.compte, r.exercice),
            par=acces.nom_complet,
            le=maintenant(),
            depot=depot_des_rapprochements(),
        )
    except RapprochementRefuse as refus:
        raise _refus(refus) from refus
    etat = valide.etat_valide
    _journaliser(
        acces,
        "comptabilite.rapprochement_valide",
        valide,
        solde_releve=str(etat.solde_releve) if etat else None,
        solde_comptable=str(etat.solde_comptable) if etat else None,
    )
    return _vue(valide)


class DemandeDAbandon(BaseModel):
    motif: str = Field(min_length=1, max_length=500)


@routeur.post(
    "/dossiers/{entreprise}/rapprochements/{identifiant}/abandon",
    summary="Abandonner un relevé importé par erreur",
)
def abandonner(
    acces: AccesRequis, entreprise: str, identifiant: str, demande: DemandeDAbandon
) -> VueRapprochement:
    exiger_dossier(acces, Permission.SAISIR_ECRITURE, entreprise)
    _du_dossier(entreprise, identifiant)
    try:
        abandonne = abandonner_un_rapprochement(
            dossier=entreprise,
            identifiant=identifiant,
            motif=demande.motif,
            depot=depot_des_rapprochements(),
        )
    except RapprochementRefuse as refus:
        raise _refus(refus) from refus
    _journaliser(acces, "comptabilite.releve_abandonne", abandonne, motif=demande.motif.strip())
    return _vue(abandonne)


__all__ = ["routeur"]
