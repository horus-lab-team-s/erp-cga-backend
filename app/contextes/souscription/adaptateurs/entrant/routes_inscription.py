"""Inscrire au portefeuille l'entreprise d'un dossier commercial payé (16 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
DÉCISION DU CABINET : une entreprise qui existe déjà (adhésion, paie, tenue) entre au
portefeuille par la main du cabinet, depuis son dossier commercial payé. Ce que le dossier
sait déjà (le nom, le téléphone, les réponses du questionnaire) préremplit l'inscription,
sans seconde saisie ; un humain vérifie l'identité fiscale avant l'entrée.

QUI : `INSCRIRE_STATUT`, la permission qui admet un dossier au Centre et inscrit ses
régimes (le réviseur, la direction). L'inscription pose un régime et un centre : elle
engage le dossier comme eux.

Le dossier construit, ses règles et ce qu'il ne pose pas (l'adhésion) : voir
`portefeuille/application/inscription.py`.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.portefeuille.api import (
    CentreRattachement,
    EntrepriseIntrouvable,
    FormeJuridique,
    InscriptionImpossible,
    RegimeFiscal,
    depot_entreprises,
    inscrire_une_entreprise_existante,
)
from app.contextes.souscription.adaptateurs.entrant.sources import (
    depot_dossiers,
    depot_qualifications,
)
from app.contextes.souscription.adaptateurs.sortant.depots_memoire import (
    DossierIntrouvable,
    SouscriptionIntrouvable,
)
from app.contextes.souscription.domaine.dossier_commercial import EtatDossier
from app.contextes.souscription.domaine.souscriptions import EtatSouscription
from app.contextes.transverse.api import AccesRequis, Permission, atelier, exiger
from app.partage.horloge import aujourd_hui, maintenant

__all__ = ["routeur"]

routeur = APIRouter(prefix="/acquisition", tags=["Acquisition · inscription au portefeuille"])

#: La réponse « régime fiscal actuel » du questionnaire d'adhésion, vers le régime du
#: CGI 2026. Le régime simplifié est supprimé : il relève désormais du réel. Une
#: entreprise non immatriculée n'a pas de régime à proposer.
_REGIMES_DECLARES = {
    "REEL_NORMAL": RegimeFiscal.REEL,
    "SIMPLIFIE": RegimeFiscal.REEL,
    "LIBERATOIRE": RegimeFiscal.IGS,
}


class Proposition(BaseModel):
    """Ce que le dossier commercial sait déjà : à vérifier, jamais à ressaisir."""

    denomination: str | None = None
    #: 10 novembre 2026 : connu d'une souscription en ligne (l'accès est ouvert sur lui).
    niu: str | None = None
    telephone: str | None = None
    regime: RegimeFiscal | None = None
    groupe_risque_cnps: str | None = None
    numero_cnps_employeur: str | None = None


class EtatDeLInscription(BaseModel):
    reference: str
    service: str
    #: 18 octobre 2026 : celui qui suit le dossier commercial, et qui l'inscrit.
    responsable: str | None = None
    #: Seul un dossier payé s'inscrit.
    inscriptible: bool
    motif: str | None = None
    inscrit_sous: str | None = None
    proposition: Proposition


class InscriptionDemandee(BaseModel):
    model_config = ConfigDict(extra="forbid")

    niu: str = Field(min_length=10, max_length=20)
    denomination: str = Field(min_length=2, max_length=200)
    forme_juridique: FormeJuridique
    date_creation: date
    regime: RegimeFiscal
    centre: CentreRattachement
    cloture_exercice_en_cours: date
    rccm: str | None = Field(default=None, max_length=60)
    activite: str | None = Field(default=None, max_length=200)
    siege: str | None = Field(default=None, max_length=300)
    telephone: str | None = Field(default=None, max_length=40)
    numero_cnps_employeur: str | None = Field(default=None, max_length=40)
    groupe_risque_cnps: str | None = Field(default=None, pattern="^[ABC]$")
    groupe_risque_notifie_le: date | None = None
    #: Les pièces vues (carte de contribuable, RCCM, avis de régime) : elles partent au
    #: journal avec l'accès, comme pour toute inscription de statut.
    justification: str = Field(min_length=30, max_length=500)


def _direction(acces) -> bool:
    """La direction : elle affecte les dossiers et inscrit les statuts. Lue aux droits, pas
    au nom du rôle : la matrice se règle (18 octobre 2026)."""
    return acces.detient(Permission.INSCRIRE_STATUT) and acces.detient(Permission.AFFECTER_DOSSIER)


def _droit_d_inscrire(acces) -> Permission:
    """⚠️ DÉCISION DU GÉRANT, 18 OCTOBRE 2026 : « c'est au responsable à qui l'inscription
    a été confiée de renseigner lui-même le RCCM et le NIU ». Le responsable affecté au
    dossier commercial l'inscrit (il qualifie les demandes), ou la direction. Un réviseur
    qui ne suit pas ce client ne l'inscrit plus : il n'a pas vu ses pièces.

    La route exige le droit rendu ici, puis `_exiger_le_responsable`."""
    return Permission.INSCRIRE_STATUT if _direction(acces) else Permission.QUALIFIER_PROSPECT


def _exiger_le_responsable(acces, dossier) -> None:
    if _direction(acces):
        return
    if dossier.responsable != acces.compte:
        raise HTTPException(
            status_code=403,
            detail="Action non permise : seul le responsable affecté au dossier, ou la "
            "direction, inscrit cette entreprise.",
        )


def _dossier(reference: str):
    try:
        return depot_dossiers().lire(reference)
    except DossierIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence


def _etat(dossier) -> EtatDeLInscription:
    qualification = depot_qualifications().trouver(dossier.reference)
    reponses = dict(qualification.faits()) if qualification else {}
    groupe = reponses.get("groupe_risque_cnps")
    numero = reponses.get("numero_employeur_cnps")
    motif = None
    if dossier.inscrit_sous:
        motif = f"déjà inscrit au portefeuille sous le NIU {dossier.inscrit_sous}."
    elif dossier.service == "CREATION":
        motif = "une société créée entre au portefeuille par sa conversion, à la livraison."
    elif dossier.etat is not EtatDossier.PAYEE:
        motif = "le dossier n'est pas encore payé : l'inscription suit le règlement."
    return EtatDeLInscription(
        reference=dossier.reference,
        service=dossier.service,
        responsable=dossier.responsable,
        inscriptible=motif is None,
        motif=motif,
        inscrit_sous=dossier.inscrit_sous,
        proposition=Proposition(
            denomination=dossier.demande.nom,
            telephone=dossier.demande.telephone,
            regime=_REGIMES_DECLARES.get(str(reponses.get("regime_fiscal"))),
            groupe_risque_cnps=groupe if groupe in ("A", "B", "C") else None,
            numero_cnps_employeur=str(numero) if numero else None,
        ),
    )


@routeur.get(
    "/inscriptions/en-attente",
    summary="Les entreprises payées qui attendent leur inscription au portefeuille",
)
def lister_les_inscriptions_en_attente(acces: AccesRequis) -> list[EtatDeLInscription]:
    """Hors création de société : celle-là entre au portefeuille par sa conversion, à la
    livraison des formalités. Elle n'a ni NIU ni RCCM au moment du paiement."""
    exiger(acces, _droit_d_inscrire(acces), motif="lister les inscriptions en attente")
    # La direction voit toute la file ; un responsable, les dossiers qu'il suit.
    file = [
        _etat(d)
        for d in depot_dossiers().payes()
        if _etat_inscriptible(d) and (_direction(acces) or d.responsable == acces.compte)
    ]
    # 10 novembre 2026 : les souscriptions en ligne n'ont pas de responsable : la direction.
    if _direction(acces):
        file += [_etat_de_la_souscription(so) for so in _souscriptions_a_inscrire()]
    return file


# ── Les souscriptions en ligne (10 novembre 2026) ─────────────────────────────
#
# ⚠️ LE TROU QUE CES FONCTIONS BOUCHENT. Une entreprise existante qui souscrit en ligne paie,
# puis l'administration vérifie l'identité et ouvre l'accès : son compte porte le NIU
# déclaré. Mais rien ne l'inscrivait au portefeuille — la file ne lisait que les dossiers
# commerciaux — et l'adhérent restait devant « le cabinet finalise l'ouverture de votre
# dossier », indéfiniment (constaté à la recette du 10 novembre).
#
# La souscription rejoint donc la même file, sous sa référence `SO-…`, et s'inscrit par la
# même route, avec le même formulaire et le même journal. Le NIU inscrit est celui de la
# souscription : c'est sur lui que l'accès a été ouvert.

_FORMULES = {
    "LIBERATOIRE": RegimeFiscal.IGS,
    "REEL_SIMPLIFIE": RegimeFiscal.REEL,
    "REEL": RegimeFiscal.REEL,
}


def _est_une_souscription(reference: str) -> bool:
    return reference.startswith("SO-")


def _souscriptions():
    # Importé à l'appel : le comptoir vit avec les routes de la souscription.
    from app.contextes.souscription.adaptateurs.entrant.routes_http import comptoir

    return comptoir().souscriptions


def _au_portefeuille(niu: str):
    try:
        return depot_entreprises().lire(niu)
    except EntrepriseIntrouvable:
        return None


def _souscriptions_a_inscrire():
    return [
        so
        for so in _souscriptions().lister()
        if so.etat is EtatSouscription.ACTIVEE
        and so.niu
        and _au_portefeuille(so.niu.upper()) is None
    ]


def _etat_de_la_souscription(so) -> EtatDeLInscription:
    present = _au_portefeuille(so.niu.upper()) if so.niu else None
    motif = None
    if not so.niu:
        motif = "cette souscription ne porte aucun NIU : elle n'ouvre aucun dossier."
    elif present is not None:
        motif = f"déjà inscrit au portefeuille sous le NIU {present.niu}."
    elif so.etat is not EtatSouscription.ACTIVEE:
        motif = "l'accès n'est pas encore ouvert : vérifier l'identité, puis ouvrir l'accès."
    return EtatDeLInscription(
        reference=so.reference,
        service=so.service,
        responsable=None,
        inscriptible=motif is None,
        motif=motif,
        inscrit_sous=present.niu if present is not None else None,
        proposition=Proposition(
            denomination=so.prospect.denomination,
            niu=so.niu,
            telephone=so.prospect.telephone,
            regime=_FORMULES.get(str(so.formule)) if so.formule else None,
        ),
    )


def _lire_la_souscription(acces, reference: str):
    if not _direction(acces):
        raise HTTPException(
            status_code=403,
            detail="Action non permise : une souscription en ligne s'inscrit par la direction.",
        )
    try:
        return _souscriptions().lire(reference)
    except SouscriptionIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence


def _etat_inscriptible(dossier) -> bool:
    return dossier.inscrit_sous is None and dossier.service != "CREATION"


@routeur.get(
    "/dossiers/{reference}/inscription",
    summary="Ce que l'inscription au portefeuille reprend du dossier commercial",
)
def lire_l_inscription(acces: AccesRequis, reference: str) -> EtatDeLInscription:
    exiger(acces, _droit_d_inscrire(acces), motif=f"préparer l'inscription de {reference}")
    if _est_une_souscription(reference):
        return _etat_de_la_souscription(_lire_la_souscription(acces, reference))
    dossier = _dossier(reference)
    _exiger_le_responsable(acces, dossier)
    return _etat(dossier)


@routeur.post(
    "/dossiers/{reference}/inscription",
    summary="Inscrire l'entreprise du dossier payé au portefeuille",
    status_code=201,
    responses={
        409: {"description": "Dossier non payé, déjà inscrit, ou NIU déjà au portefeuille"},
        422: {"description": "Données incohérentes (date de création, clôture, groupe)"},
    },
)
def inscrire(
    acces: AccesRequis, reference: str, demande: InscriptionDemandee
) -> EtatDeLInscription:
    exiger(acces, _droit_d_inscrire(acces), motif=demande.justification)
    if _est_une_souscription(reference):
        return _inscrire_la_souscription(acces, reference, demande)
    dossier = _dossier(reference)
    _exiger_le_responsable(acces, dossier)
    etat = _etat(dossier)
    if not etat.inscriptible:
        raise HTTPException(status_code=409, detail=etat.motif)
    portefeuille = depot_entreprises()
    niu = demande.niu.strip().upper()
    try:
        existant = portefeuille.lire(niu)
    except EntrepriseIntrouvable:
        existant = None
    if existant is not None:
        raise HTTPException(
            status_code=409,
            detail=f"le NIU {niu} est déjà au portefeuille ({existant.denomination}).",
        )
    try:
        entreprise = inscrire_une_entreprise_existante(
            **demande.model_dump(exclude={"niu", "justification"}),
            niu=niu,
            origine=reference,
            a_la_date=aujourd_hui(),
        )
    except InscriptionImpossible as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus
    portefeuille.enregistrer(entreprise)
    depot_dossiers().enregistrer(dossier.model_copy(update={"inscrit_sous": niu}))
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action="acquisition.inscription_au_portefeuille",
        objet_type="dossier_commercial",
        objet_id=reference,
        apres={"niu": niu, "denomination": entreprise.denomination, "regime": demande.regime},
        motif=demande.justification,
    )
    return _etat(_dossier(reference))


def _inscrire_la_souscription(
    acces, reference: str, demande: InscriptionDemandee
) -> EtatDeLInscription:
    so = _lire_la_souscription(acces, reference)
    etat = _etat_de_la_souscription(so)
    if not etat.inscriptible:
        raise HTTPException(status_code=409, detail=etat.motif)
    niu = demande.niu.strip().upper()
    if niu != (so.niu or "").strip().upper():
        raise HTTPException(
            status_code=422,
            detail=(
                f"le NIU inscrit ({niu}) doit être celui de la souscription ({so.niu}) : c'est "
                "sur lui que l'accès de l'adhérent a été ouvert."
            ),
        )
    try:
        entreprise = inscrire_une_entreprise_existante(
            **demande.model_dump(exclude={"niu", "justification"}),
            niu=niu,
            origine=reference,
            a_la_date=aujourd_hui(),
        )
    except InscriptionImpossible as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus
    depot_entreprises().enregistrer(entreprise)
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action="acquisition.inscription_au_portefeuille",
        objet_type="souscription",
        objet_id=reference,
        apres={"niu": niu, "denomination": entreprise.denomination, "regime": demande.regime},
        motif=demande.justification,
    )
    return _etat_de_la_souscription(_lire_la_souscription(acces, reference))
