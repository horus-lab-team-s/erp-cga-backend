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
from app.contextes.souscription.adaptateurs.sortant.depots_memoire import DossierIntrouvable
from app.contextes.souscription.domaine.dossier_commercial import EtatDossier
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
    telephone: str | None = None
    regime: RegimeFiscal | None = None
    groupe_risque_cnps: str | None = None
    numero_cnps_employeur: str | None = None


class EtatDeLInscription(BaseModel):
    reference: str
    service: str
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
    exiger(acces, Permission.INSCRIRE_STATUT, motif="lister les inscriptions en attente")
    return [
        _etat(d)
        for d in depot_dossiers().payes()
        if _etat_inscriptible(d)
    ]


def _etat_inscriptible(dossier) -> bool:
    return dossier.inscrit_sous is None and dossier.service != "CREATION"


@routeur.get(
    "/dossiers/{reference}/inscription",
    summary="Ce que l'inscription au portefeuille reprend du dossier commercial",
)
def lire_l_inscription(acces: AccesRequis, reference: str) -> EtatDeLInscription:
    exiger(acces, Permission.INSCRIRE_STATUT, motif=f"préparer l'inscription de {reference}")
    return _etat(_dossier(reference))


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
    exiger(acces, Permission.INSCRIRE_STATUT, motif=demande.justification)
    dossier = _dossier(reference)
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
