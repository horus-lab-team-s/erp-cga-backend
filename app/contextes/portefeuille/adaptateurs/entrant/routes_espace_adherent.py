"""« Mon entreprise » : la fiche de l'adhérent, et le signalement d'un changement (pas 114).

─────────────────────────────────────────────────────────────────────────────────
DEUX ROUTES, SOUS `/portefeuille`

* `GET  /portefeuille/entreprises/{niu}/mon-entreprise` (`LIRE_DOSSIER`) : l'identité du dossier
  dans les mots de l'adhérent (forme, centre, régime expliqué et depuis quand), l'adhésion, les
  dirigeants en fonction, l'interlocuteur, les natures de changement et les derniers signalements.
* `POST /portefeuille/entreprises/{niu}/signalements` (`SIGNALER_UN_CHANGEMENT`, l'adhérent seul) :
  une parole datée au journal, annoncée au chargé de clientèle. **Rien ne change au dossier** : le
  cabinet instruit (note 2 de la vue E).

⚠️ Le même signalement, le même jour, par le même compte, est refusé (409) : un double appui.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.portefeuille.adaptateurs.entrant.routes_http import _lire
from app.contextes.portefeuille.adaptateurs.sortant.espace_adherent_yaml import (
    charger_la_fiche_adherent,
)
from app.contextes.portefeuille.domaine.espace_adherent import (
    CandidatInterlocuteur,
    Interlocuteur,
    NatureDeChangement,
    NatureReglee,
    SignalementDeChangement,
    choisir_l_interlocuteur,
    signalement_en_double,
)
from app.contextes.portefeuille.domaine.fiche_signaletique import (
    ActiviteDeclaree,
    ControleDeLEntite,
    FicheRefusee,
    FicheSignaletique,
    MembreDuConseil,
)
from app.contextes.portefeuille.domaine.temporel import resoudre
from app.contextes.transverse.api import (
    AccesRequis,
    EtatCompte,
    Permission,
    atelier,
    exiger_dossier,
    habilitations_actives,
)
from app.infrastructure.config import configuration
from app.partage.horloge import maintenant

routeur = APIRouter(prefix="/portefeuille", tags=["Portefeuille · espace de l'adhérent"])

#: Le type d'objet des signalements au journal d'audit, par dossier.
OBJET_SIGNALEMENT = "signalement_de_changement"


def _reglages():
    return charger_la_fiche_adherent(configuration().dossier_referentiel)


def _signalements(niu: str) -> list[SignalementDeChangement]:
    return [
        SignalementDeChangement(
            nature=NatureDeChangement(e.apres["nature"]),
            message=e.apres["message"],
            le=e.horodatage,
            par=e.acteur,
        )
        for e in atelier().journal.lister(objet_type=OBJET_SIGNALEMENT, objet_id=niu)
        # Seule action écrite sur ce type d'objet (mutant équivalent au pas 114) : le filtre
        # protège la
        # lecture le jour où s'y ajoute une autre action (un signalement instruit, par exemple).
        if e.action == "portefeuille.changement_signale"
    ]


def _interlocuteur(niu: str, jour: date) -> Interlocuteur | None:
    boutique = atelier()
    candidats = []
    for h in habilitations_actives(boutique.habilitations.pour_dossier(niu), jour):
        compte = boutique.comptes.lire(h.compte)
        if compte.etat is not EtatCompte.ACTIF:
            continue
        candidats.append(
            CandidatInterlocuteur(
                compte=compte.identifiant,
                role=h.role.value,
                nommee_sur_le_dossier=h.portee is not None,
                debut=h.debut,
                nom=compte.nom_affiche,
                courriel=compte.courriel,
                telephone=compte.telephone,
            )
        )
    return choisir_l_interlocuteur(candidats)


class RegimeLu(BaseModel):
    code: str
    titre: str
    explication: str | None
    #: Le début de la période de régime en cours : « depuis 2019 ».
    depuis: date


class Dirigeant(BaseModel):
    nom: str
    qualite: str


class SignalementLu(BaseModel):
    nature: NatureDeChangement
    libelle: str
    message: str
    le: str


class MonEntreprise(BaseModel):
    niu: str
    denomination: str
    forme: str
    activite: str | None
    rccm: str | None
    siege: str | None
    centre: str
    regime: RegimeLu
    assujettie_tva: bool
    adhesion_numero: str | None
    adherente_depuis: date | None
    dirigeants: list[Dirigeant]
    interlocuteur: Interlocuteur | None
    natures: list[NatureReglee]
    signalements: list[SignalementLu]
    explications_validees: bool


@routeur.get(
    "/entreprises/{niu}/mon-entreprise",
    summary="La fiche d'un dossier, telle que l'adhérent la lit",
    responses={404: {"description": "Dossier inconnu ou hors périmètre"}},
)
def lire_mon_entreprise(acces: AccesRequis, niu: str) -> MonEntreprise:
    exiger_dossier(acces, Permission.LIRE_DOSSIER, niu)
    reglages = _reglages()
    dossier = _lire(niu)
    jour = maintenant().date()
    regime = resoudre(dossier.regimes, jour)
    if regime is None:
        raise HTTPException(status_code=409, detail=f"{niu} : aucun régime connu à ce jour.")
    explication = reglages.regime(regime.regime.value)
    centre = dossier.rattachement_au(jour).value
    adhesion = dossier.adhesion_au(jour)
    libelles = {n.nature: n.libelle for n in reglages.natures}
    return MonEntreprise(
        niu=dossier.niu,
        denomination=dossier.denomination,
        forme=reglages.formes.get(dossier.forme_juridique.value, dossier.forme_juridique.value),
        activite=dossier.activite,
        rccm=dossier.rccm,
        siege=dossier.siege,
        centre=reglages.centres.get(centre, centre),
        regime=RegimeLu(
            code=regime.regime.value,
            titre=explication.titre if explication else regime.regime.value,
            explication=explication.explication if explication else None,
            depuis=regime.debut,
        ),
        assujettie_tva=dossier.assujettie_tva_au(jour),
        adhesion_numero=adhesion.numero if adhesion else None,
        adherente_depuis=adhesion.debut if adhesion else None,
        dirigeants=[
            Dirigeant(nom=d.nom, qualite=d.qualite)
            for d in dossier.dirigeants
            if d.depuis <= jour and (d.jusqu_a is None or d.jusqu_a >= jour)
        ],
        interlocuteur=_interlocuteur(niu, jour),
        natures=list(reglages.natures),
        signalements=[
            SignalementLu(
                nature=s.nature,
                libelle=libelles[s.nature],
                message=s.message,
                le=s.le.isoformat(),
            )
            # ⚠️ Le rang au journal départage deux signalements de la même seconde : trié sur
            # l'horodatage seul, l'ordre d'égalité gardait le plus ancien en tête (vu en test).
            for _, s in sorted(
                enumerate(_signalements(niu)), key=lambda rs: (rs[1].le, rs[0]), reverse=True
            )[: reglages.signalements_montres]
        ],
        explications_validees=reglages.statut == "VALIDE",
    )


class Signalement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nature: NatureDeChangement
    message: str = Field(min_length=5, max_length=1000)


@routeur.post(
    "/entreprises/{niu}/signalements",
    summary="L'adhérent signale un changement de son entreprise",
    status_code=201,
    responses={
        404: {"description": "Dossier inconnu ou hors périmètre"},
        409: {"description": "Le même signalement, déjà reçu aujourd'hui"},
    },
)
def signaler_un_changement(acces: AccesRequis, niu: str, corps: Signalement) -> SignalementLu:
    exiger_dossier(acces, Permission.SIGNALER_UN_CHANGEMENT, niu)
    reglages = _reglages()
    dossier = _lire(niu)
    message = corps.message.strip()
    if len(message) < 5:
        raise HTTPException(status_code=422, detail="décrivez le changement en quelques mots.")
    horodatage = maintenant()
    nouveau = SignalementDeChangement(
        nature=corps.nature, message=message, le=horodatage, par=acces.compte
    )
    if signalement_en_double(nouveau, _signalements(niu)):
        raise HTTPException(
            status_code=409,
            detail="ce changement a déjà été signalé aujourd'hui : le cabinet l'a reçu.",
        )
    libelle = reglages.nature(corps.nature).libelle
    atelier().journal.ajouter(
        horodatage=horodatage,
        acteur=acces.compte,
        action="portefeuille.changement_signale",
        objet_type=OBJET_SIGNALEMENT,
        objet_id=niu,
        apres={
            "dossier": niu,
            "denomination": dossier.denomination,
            "nature": corps.nature.value,
            "changement": libelle,
            "message": message,
            "par": acces.nom_complet,
        },
    )
    return SignalementLu(
        nature=corps.nature, libelle=libelle, message=message, le=horodatage.isoformat()
    )


__all__ = ["routeur"]


# ── La fiche signalétique de la DSF (15 octobre 2026) ────────────────────────────
#
# Fiches R1 à R3 du modèle officiel : ce que le client connaît et que le dossier ne
# portait pas. Il la renseigne ici, le cabinet la valide. Voir `fiche_signaletique.py`.

OBJET_FICHE = "fiche_dsf"


class VueDeLaFiche(BaseModel):
    fiche: FicheSignaletique
    #: Ce qui manque encore pour établir la DSF, dans les mots du client.
    manquants: list[str]
    #: Ce que le dossier porte déjà et que la DSF reprendra (rappel, non modifiable ici).
    denomination: str
    forme_juridique: str
    rccm: str | None
    siege: str | None


class SaisieDeLaFiche(BaseModel):
    """Les champs que le client renseigne. Ceux du cycle (état, dates) ne s'envoient pas."""

    model_config = ConfigDict(extra="forbid")

    telephone: str | None = Field(default=None, max_length=40)
    courriel: str | None = Field(default=None, max_length=200)
    boite_postale: str | None = Field(default=None, max_length=40)
    ville: str | None = Field(default=None, max_length=80)
    adresse_geographique: str | None = Field(default=None, max_length=300)
    numero_cnps_employeur: str | None = Field(default=None, max_length=40)
    code_importateur: str | None = Field(default=None, max_length=40)
    personne_a_contacter: str | None = Field(default=None, max_length=200)
    professionnel_des_etats: str | None = Field(default=None, max_length=300)
    signataire_nom: str | None = Field(default=None, max_length=120)
    signataire_qualite: str | None = Field(default=None, max_length=120)
    etats_approuves_en_ag: bool | None = None
    banque: str | None = Field(default=None, max_length=120)
    numero_de_compte: str | None = Field(default=None, max_length=60)
    etablissements_au_cameroun: int | None = Field(default=None, ge=0)
    etablissements_hors_cameroun: int | None = Field(default=None, ge=0)
    premiere_annee_au_cameroun: int | None = Field(default=None, ge=1900, le=2100)
    controle: ControleDeLEntite | None = None
    activites: list[ActiviteDeclaree] = Field(default_factory=list, max_length=20)
    conseil_d_administration: list[MembreDuConseil] = Field(default_factory=list, max_length=30)


class RenvoiDeLaFiche(BaseModel):
    motif: str = Field(min_length=10, max_length=500)


def _vue(entreprise) -> VueDeLaFiche:
    fiche = entreprise.fiche_dsf or FicheSignaletique()
    return VueDeLaFiche(
        fiche=fiche,
        manquants=fiche.manquants(),
        denomination=entreprise.denomination,
        forme_juridique=entreprise.forme_juridique,
        rccm=entreprise.rccm,
        siege=entreprise.siege,
    )


def _enregistrer(entreprise, fiche: FicheSignaletique, *, acteur: str, action: str, **apres):
    from app.contextes.portefeuille.adaptateurs.entrant.routes_http import depot

    depot().enregistrer(entreprise.model_copy(update={"fiche_dsf": fiche}))
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acteur,
        action=action,
        objet_type=OBJET_FICHE,
        objet_id=entreprise.niu,
        apres={"etat": fiche.etat.value, **apres},
    )


@routeur.get(
    "/entreprises/{niu}/fiche-signaletique",
    summary="La fiche signalétique de la DSF (fiches R1 à R3) et ce qui y manque",
)
def lire_la_fiche(acces: AccesRequis, niu: str) -> VueDeLaFiche:
    exiger_dossier(acces, Permission.LIRE_DOSSIER, niu)
    return _vue(_lire(niu))


@routeur.put(
    "/entreprises/{niu}/fiche-signaletique",
    summary="Renseigner la fiche signalétique : le client dans son espace, ou le cabinet",
    responses={409: {"description": "Fiche soumise : en attente de la réponse du cabinet"}},
)
def renseigner_la_fiche(acces: AccesRequis, niu: str, saisie: SaisieDeLaFiche) -> VueDeLaFiche:
    exiger_dossier(acces, Permission.RENSEIGNER_FICHE_DSF, niu)
    entreprise = _lire(niu)
    try:
        fiche = (entreprise.fiche_dsf or FicheSignaletique()).modifier(
            saisie.model_dump(), par=acces.compte, a_l_instant=maintenant()
        )
    except FicheRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    _enregistrer(entreprise, fiche, acteur=acces.compte, action="portefeuille.fiche_dsf_renseignee")
    return _vue(_lire(niu))


@routeur.post(
    "/entreprises/{niu}/fiche-signaletique/soumission",
    summary="Soumettre la fiche au cabinet",
    responses={409: {"description": "Fiche incomplète, ou déjà soumise"}},
)
def soumettre_la_fiche(acces: AccesRequis, niu: str) -> VueDeLaFiche:
    exiger_dossier(acces, Permission.RENSEIGNER_FICHE_DSF, niu)
    entreprise = _lire(niu)
    try:
        fiche = (entreprise.fiche_dsf or FicheSignaletique()).soumettre(a_l_instant=maintenant())
    except FicheRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    _enregistrer(entreprise, fiche, acteur=acces.compte, action="portefeuille.fiche_dsf_soumise")
    return _vue(_lire(niu))


@routeur.post(
    "/entreprises/{niu}/fiche-signaletique/validation",
    summary="Le cabinet valide la fiche soumise",
    responses={409: {"description": "Aucune fiche soumise"}},
)
def valider_la_fiche(acces: AccesRequis, niu: str) -> VueDeLaFiche:
    exiger_dossier(acces, Permission.VALIDER_FICHE_DSF, niu)
    entreprise = _lire(niu)
    try:
        fiche = (entreprise.fiche_dsf or FicheSignaletique()).valider(
            par=acces.compte, a_l_instant=maintenant()
        )
    except FicheRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    _enregistrer(entreprise, fiche, acteur=acces.compte, action="portefeuille.fiche_dsf_validee")
    return _vue(_lire(niu))


@routeur.post(
    "/entreprises/{niu}/fiche-signaletique/renvoi",
    summary="Le cabinet renvoie la fiche au client, avec ce qu'il doit corriger",
    responses={409: {"description": "Aucune fiche soumise"}},
)
def renvoyer_la_fiche(acces: AccesRequis, niu: str, renvoi: RenvoiDeLaFiche) -> VueDeLaFiche:
    exiger_dossier(acces, Permission.VALIDER_FICHE_DSF, niu)
    entreprise = _lire(niu)
    try:
        fiche = (entreprise.fiche_dsf or FicheSignaletique()).renvoyer(motif=renvoi.motif)
    except FicheRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    _enregistrer(
        entreprise,
        fiche,
        acteur=acces.compte,
        action="portefeuille.fiche_dsf_renvoyee",
        motif=fiche.motif_du_renvoi,
    )
    return _vue(_lire(niu))
