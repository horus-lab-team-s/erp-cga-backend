"""API du contexte I · Création d'entreprise.

Le pipeline, les gestes qui font avancer un dossier, et la conversion.

⚠️ **Une seule route écrit hors du contexte** : `POST /creations/{ref}/conversion`
enregistre l'entreprise née au dépôt de B · Portefeuille. Elle le fait ici, dans
l'adaptateur entrant, et non dans le cas d'usage — c'est la couche qui connaît la
transaction, et c'est aussi celle où la frontière franchie reste visible.

Permissions. Tout ce contexte relève de `SUIVRE_FORMALITE`, détenue par le chargé
de formalités seul. C'était jusqu'ici la seule permission du produit qui n'ouvrait
aucun écran : le rôle existait, son droit existait, et il n'y avait rien derrière.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.contextes.creation_entreprise.adaptateurs.sortant.donnees_demo import PIPELINE_DEMO
from app.contextes.creation_entreprise.api import (
    ConversionImpossible,
    DepotDossiersCreation,
    DepotDossiersCreationMemoire,
    DepotDossiersCreationSql,
    DossierCreation,
    DossierCreationIntrouvable,
    EtapeCreation,
    Fondateur,
    Immatriculation,
    Jalon,
    PieceConstitution,
    TransitionInterdite,
    abandonner,
    avancer,
    checklist_de,
    convertir,
    depasse_le_delai_annonce,
    diagnostiquer,
    enregistrer_identifiant,
    etapes_ouvertes_depuis,
    fournir_piece,
    ouvrir_dossier,
)
from app.contextes.portefeuille.api import (
    CentreRattachement,
    DepotEntreprisesSql,
    FormeJuridique,
    RegimeFiscal,
    entreprises_en_memoire,
)
from app.contextes.referentiel.api import ServiceParametres, service_parametres
from app.contextes.transverse.api import (
    AccesRequis,
    Permission,
    exiger,
    session_de_travail,
)
from app.partage.horloge import maintenant
from app.partage.locataire import courant


def _aujourd_hui() -> date:
    """La date du jour, prise à l'horloge du projet et jamais à `date.today()`.

    ⚠️ `horloge_figee` doit pouvoir la figer. Un cas d'usage qui lit l'horloge
    murale casse la suite de tests à minuit — c'est arrivé sur ce projet, sur
    une route de souscription, et le test échouait alors sans qu'aucune ligne de
    code n'ait changé.
    """
    return maintenant().date()


routeur = APIRouter(prefix="/creations", tags=["Création d'entreprise"])

# ─────────────────────────────────────────────────────────────────────────────
# ⚠️ PAS 82 : LES ÉCRITURES NE REÇOIVENT PLUS DE DATE DE LA REQUÊTE
#
# Ouvrir, franchir une étape, recevoir une pièce, abandonner et convertir
# acceptaient un paramètre `a_la_date`, qui datait le jalon. Essai, avant
# correction : un dossier au dépôt CFCE du 30/07/2026 franchissait le suivi
# d'immatriculation **le 01/01/2019**. La chronologie du dossier se cassait, et
# avec elle le délai du guichet, que ce contexte appelle « le seul chiffre que le
# cabinet puisse lui opposer ». C'est la faute corrigée au pas 71 pour la
# contre-passation : une date d'acte fournie par l'appelant n'est pas une date d'acte.
#
# Les écritures prennent la date de l'horloge du projet (`_aujourd_hui`), que les
# tests figent. Les **lectures** gardent `a_la_date` : lire un pipeline à une date
# passée ne réécrit rien.
# ─────────────────────────────────────────────────────────────────────────────


def depot() -> DepotDossiersCreation:
    """Le dépôt en vigueur — SQL dans une requête, mémoire sinon.

    La bascule est ici et non dans chaque route : une route qui choisirait son
    dépôt le choisirait un jour mal, et l'incohérence — lecture en base, écriture
    en mémoire — ne se verrait qu'au redémarrage.
    """
    session = session_de_travail()
    if session is None:
        return _depot_memoire()
    return DepotDossiersCreationSql(session, courant())


@lru_cache
def _depot_memoire() -> DepotDossiersCreationMemoire:
    return DepotDossiersCreationMemoire(list(PIPELINE_DEMO))


def parametres() -> ServiceParametres:
    # ⚠️ Pas 95 : le référentiel **du cabinet**, par le point de montage unique, et non plus
    # le fichier commun mémoïsé ici. Voir `service_parametres` dans `referentiel/api.py`.
    return service_parametres()


# ── Ce que les écrans lisent ─────────────────────────────────────────────────
class LigneDossier(BaseModel):
    """Une ligne du pipeline, avec ce qui décide d'agir.

    Elle porte `pieces_manquantes` et `en_retard` plutôt que la checklist entière
    : l'écran groupe par étape et signale ce qui bloque. Le détail se lit sur la
    fiche.
    """

    reference: str
    denomination_souhaitee: str
    forme_juridique: FormeJuridique
    fondateur: str
    etape: EtapeCreation
    ouvert_le: date
    immobile_depuis: date
    jours_d_immobilite: int
    pieces_manquantes: int
    #: Le guichet a dépassé son délai annoncé. Déclenche une relance, pas un blocage.
    en_retard: bool
    immatriculee: bool
    converti_en: str | None

    @classmethod
    def depuis(cls, dossier: DossierCreation, a_la_date: date) -> LigneDossier:
        return cls(
            reference=dossier.reference,
            denomination_souhaitee=dossier.denomination_souhaitee,
            forme_juridique=dossier.forme_juridique,
            fondateur=f"{dossier.fondateur.prenom} {dossier.fondateur.nom}".strip(),
            etape=dossier.etape,
            ouvert_le=dossier.ouvert_le,
            immobile_depuis=dossier.immobile_depuis,
            jours_d_immobilite=(a_la_date - dossier.immobile_depuis).days,
            pieces_manquantes=len(dossier.pieces_manquantes),
            en_retard=depasse_le_delai_annonce(dossier, parametres(), a_la_date),
            immatriculee=dossier.immatriculation.immatriculee,
            converti_en=dossier.converti_en,
        )


class FicheDossier(BaseModel):
    """Le dossier entier, plus ce qui se calcule à une date."""

    dossier: DossierCreation
    constats: list[dict]
    deposable: bool
    etapes_ouvertes: list[EtapeCreation]
    en_retard: bool


class DemandeOuverture(BaseModel):
    reference: str = Field(min_length=1)
    fondateur: Fondateur
    denomination_souhaitee: str = Field(min_length=1)
    forme_juridique: FormeJuridique
    activite: str = Field(min_length=1)
    siege: str = Field(min_length=1)
    capital: Decimal | None = None


class DemandeAvancement(BaseModel):
    vers: EtapeCreation
    commentaire: str | None = None


class DemandeAbandon(BaseModel):
    motif: str = Field(min_length=1)


class DemandePiece(BaseModel):
    code: str = Field(min_length=1)
    empreinte: str | None = None


class DemandeIdentifiant(BaseModel):
    """Un identifiant délivré, avec sa date. Les quatre arrivent séparément."""

    rccm: str | None = None
    rccm_obtenu_le: date | None = None
    niu: str | None = None
    niu_obtenu_le: date | None = None
    patente: str | None = None
    patente_obtenue_le: date | None = None
    cnps: str | None = None
    cnps_obtenue_le: date | None = None


class DemandeConversion(BaseModel):
    """Le régime d'entrée et le centre sont **choisis**, jamais déduits.

    Une entreprise qui vient d'être immatriculée n'a aucun chiffre d'affaires,
    donc aucun élément pour déterminer son régime au seuil. Le fondateur peut
    opter pour le réel dès l'origine, et c'est fréquent quand il sait qu'il
    facturera de la TVA.
    """

    regime: RegimeFiscal
    centre: CentreRattachement
    adherent: bool = True


# ── Lectures ─────────────────────────────────────────────────────────────────
@routeur.get("/pipeline", summary="Le pipeline des créations en cours")
def lister(
    acces: AccesRequis,
    a_la_date: date | None = Query(None, description="Défaut : aujourd'hui"),
    etape: EtapeCreation | None = Query(None),
    immobiles_depuis_jours: int | None = Query(
        None, ge=0, description="Ne rend que les dossiers sans mouvement depuis N jours"
    ),
) -> list[LigneDossier]:
    """Le pipeline, du plus ancien mouvement au plus récent.

    L'ordre n'est pas anodin : le pipeline se lit par urgence, pas par ordre
    d'arrivée. Un dossier qui dort depuis trois semaines se présente avant celui
    d'hier — c'est ce qui fait de cet écran un outil de relance et non une liste.
    """
    exiger(acces, Permission.SUIVRE_FORMALITE)
    jour = a_la_date or _aujourd_hui()
    seuil = None
    if immobiles_depuis_jours is not None:
        seuil = jour - timedelta(days=immobiles_depuis_jours)
    dossiers = depot().lister(etape=etape, immobiles_avant=seuil)
    return [LigneDossier.depuis(d, jour) for d in dossiers]


@routeur.get("/checklist/{forme_juridique}", summary="Les pièces à réunir pour une forme")
def checklist(acces: AccesRequis, forme_juridique: FormeJuridique) -> list[PieceConstitution]:
    """Ce qu'il faut préparer avant de se déplacer au guichet.

    Consultable dès la qualification, avant même qu'un dossier existe : c'est la
    réponse à « qu'est-ce qu'il me faut ? », première question de tout fondateur.
    """
    exiger(acces, Permission.SUIVRE_FORMALITE)
    return list(checklist_de(forme_juridique))


@routeur.get("/{reference}", summary="Un dossier de création")
def lire(
    acces: AccesRequis,
    reference: str,
    a_la_date: date | None = Query(None),
) -> FicheDossier:
    exiger(acces, Permission.SUIVRE_FORMALITE)
    jour = a_la_date or _aujourd_hui()
    dossier = _lire(reference)
    diagnostic = diagnostiquer(dossier, parametres(), jour)
    return FicheDossier(
        dossier=dossier,
        constats=[
            {"code": c.code, "message": c.message, "bloquant": c.bloquant}
            for c in diagnostic.constats
        ],
        deposable=diagnostic.deposable,
        etapes_ouvertes=sorted(etapes_ouvertes_depuis(dossier.etape)),
        en_retard=depasse_le_delai_annonce(dossier, parametres(), jour),
    )


# ── Écritures ────────────────────────────────────────────────────────────────
@routeur.post("", summary="Ouvrir un dossier de création", status_code=201)
def ouvrir(acces: AccesRequis, demande: DemandeOuverture) -> DossierCreation:
    exiger(acces, Permission.SUIVRE_FORMALITE)
    magasin = depot()
    try:
        magasin.lire(demande.reference)
    except DossierCreationIntrouvable:
        pass
    else:
        raise HTTPException(
            status_code=409,
            detail=f"un dossier porte déjà la référence {demande.reference}.",
        )
    dossier = ouvrir_dossier(
        reference=demande.reference,
        fondateur=demande.fondateur,
        denomination_souhaitee=demande.denomination_souhaitee,
        forme_juridique=demande.forme_juridique,
        activite=demande.activite,
        siege=demande.siege,
        capital=demande.capital,
        a_la_date=_aujourd_hui(),
        par=acces.compte,
    )
    magasin.enregistrer(dossier)
    return dossier


@routeur.post("/{reference}/etape", summary="Faire avancer un dossier")
def franchir(
    acces: AccesRequis,
    reference: str,
    demande: DemandeAvancement,
) -> DossierCreation:
    """Franchit une étape, ou refuse **en disant pourquoi**.

    Le refus rend 409 et non 400 : la requête est bien formée, c'est l'état du
    dossier qui s'y oppose. La distinction compte pour l'écran, qui affiche un
    conflit d'état autrement qu'une saisie invalide.
    """
    exiger(acces, Permission.SUIVRE_FORMALITE)
    magasin = depot()
    dossier = _lire(reference, magasin)
    try:
        avance = avancer(
            dossier,
            demande.vers,
            _aujourd_hui(),
            par=acces.compte,
            commentaire=demande.commentaire,
        )
    except TransitionInterdite as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    magasin.enregistrer(avance)
    return avance


@routeur.post("/{reference}/pieces", summary="Marquer une pièce comme reçue")
def recevoir_piece(
    acces: AccesRequis,
    reference: str,
    demande: DemandePiece,
) -> DossierCreation:
    exiger(acces, Permission.SUIVRE_FORMALITE)
    magasin = depot()
    dossier = fournir_piece(
        _lire(reference, magasin),
        demande.code,
        _aujourd_hui(),
        empreinte=demande.empreinte,
    )
    magasin.enregistrer(dossier)
    return dossier


@routeur.post("/{reference}/identifiants", summary="Porter un identifiant délivré")
def porter_identifiant(
    acces: AccesRequis, reference: str, demande: DemandeIdentifiant
) -> DossierCreation:
    exiger(acces, Permission.SUIVRE_FORMALITE)
    magasin = depot()
    couples = {
        "rccm": _couple(demande.rccm, demande.rccm_obtenu_le, "rccm"),
        "niu": _couple(demande.niu, demande.niu_obtenu_le, "niu"),
        "patente": _couple(demande.patente, demande.patente_obtenue_le, "patente"),
        "cnps": _couple(demande.cnps, demande.cnps_obtenue_le, "cnps"),
    }
    dossier = enregistrer_identifiant(_lire(reference, magasin), **couples)
    magasin.enregistrer(dossier)
    return dossier


def _couple(valeur: str | None, obtenu_le: date | None, nom: str) -> tuple[str, date] | None:
    """Assemble un identifiant et sa date, ou refuse l'un sans l'autre.

    La date sert à mesurer le délai tenu par le guichet, et c'est le seul chiffre
    que le cabinet puisse lui opposer. Un RCCM saisi sans elle laisserait un
    dossier qui paraît complet et un délai qu'on ne peut plus reconstituer.
    """
    if valeur is None and obtenu_le is None:
        return None
    if valeur is None or obtenu_le is None:
        raise HTTPException(
            status_code=422,
            detail=f"{nom} et sa date d'obtention vont ensemble : sans la date, le "
            "délai du guichet ne serait plus reconstituable.",
        )
    # ⚠️ Pas 82 : une date d'obtention future n'existe pas, et elle fausserait le délai
    # du guichet dans l'autre sens, en le raccourcissant.
    if obtenu_le > _aujourd_hui():
        raise HTTPException(
            status_code=422,
            detail=f"{nom} obtenu le {obtenu_le:%d/%m/%Y} : cette date n'est pas encore passée.",
        )
    return (valeur, obtenu_le)


@routeur.post("/{reference}/abandon", summary="Abandonner un dossier, avec son motif")
def abandonner_dossier(
    acces: AccesRequis,
    reference: str,
    demande: DemandeAbandon,
) -> DossierCreation:
    exiger(acces, Permission.SUIVRE_FORMALITE)
    magasin = depot()
    try:
        clos = abandonner(
            _lire(reference, magasin),
            demande.motif,
            _aujourd_hui(),
            par=acces.compte,
        )
    except TransitionInterdite as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    magasin.enregistrer(clos)
    return clos


class ResultatConversion(BaseModel):
    """Ce que la conversion produit : un dossier clos et une entreprise née."""

    dossier: DossierCreation
    niu: str
    denomination: str
    date_creation: date
    enregistree_au_portefeuille: bool


@routeur.post("/{reference}/conversion", summary="Convertir en entreprise du portefeuille")
def convertir_dossier(
    acces: AccesRequis,
    reference: str,
    demande: DemandeConversion,
) -> ResultatConversion:
    """Fait naître l'entreprise au portefeuille et clôt le dossier.

    ─────────────────────────────────────────────────────────────────────────
    LA SEULE ÉCRITURE HORS DU CONTEXTE, ET POURQUOI ELLE EST ICI

    `convertir` fabrique l'entreprise sans la persister : le cas d'usage ne
    connaît pas la transaction. C'est cette route qui l'enregistre, et qui
    enregistre ensuite le dossier clos — dans cet ordre.

    L'ordre compte. Si l'écriture du portefeuille échoue, le dossier n'est pas
    marqué converti et le geste se rejoue. Dans l'ordre inverse, un dossier
    marqué converti sans entreprise en base laisserait un client immatriculé,
    payant, et absent du portefeuille — invisible de tous les écrans qui
    comptent.

    Sur PostgreSQL, les deux écritures partagent la transaction de la requête et
    tombent ensemble. En mémoire, il n'y a pas de transaction : l'ordre est alors
    la seule garantie, et c'est pour ce cas qu'il est choisi.

    ⚠️ Aucun calendrier d'obligations n'est généré ici. F · Obligations le calcule
    déjà depuis le portefeuille, à la demande. Le dupliquer produirait deux
    calendriers qui divergeraient au premier changement de régime.
    ─────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.SUIVRE_FORMALITE)
    magasin = depot()
    dossier = _lire(reference, magasin)
    try:
        clos, entreprise = convertir(
            dossier,
            a_la_date=_aujourd_hui(),
            regime=demande.regime,
            centre=demande.centre,
            adherent=demande.adherent,
            par=acces.compte,
        )
    except ConversionImpossible as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus

    # ⚠️ Pas 82 : en mémoire, l'entreprise n'était écrite nulle part. La conversion
    # réussissait, rendait `enregistree_au_portefeuille: false`, et l'entreprise restait
    # introuvable au portefeuille (404) : exactement le client « invisible de tous les
    # écrans qui comptent » que l'en-tête de cette route dit empêcher. Le magasin mémoire
    # du portefeuille est celui que ses routes lisent (pas 52).
    session = session_de_travail()
    if session is not None:
        DepotEntreprisesSql(session, courant()).enregistrer(entreprise)
    else:
        entreprises_en_memoire().enregistrer(entreprise)
    enregistree = True
    magasin.enregistrer(clos)

    return ResultatConversion(
        dossier=clos,
        niu=entreprise.niu,
        denomination=entreprise.denomination,
        date_creation=entreprise.date_creation,
        enregistree_au_portefeuille=enregistree,
    )


def _lire(reference: str, magasin: DepotDossiersCreation | None = None) -> DossierCreation:
    try:
        return (magasin or depot()).lire(reference)
    except DossierCreationIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence


__all__ = ["Immatriculation", "Jalon", "routeur"]
