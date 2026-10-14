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

import logging
import re
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.contextes.creation_entreprise.adaptateurs.sortant.donnees_demo import PIPELINE_DEMO
from app.contextes.creation_entreprise.api import (
    CanalDeLaPiece,
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
    exiger_dossier,
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


_journal_de_creation = logging.getLogger(__name__)

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
    #: ⚠️ **LA CLIENTE A BOUGÉ LA DERNIÈRE, ET PERSONNE NE LUI A RÉPONDU.**
    #:
    #: Sans cette ligne, un dossier où elle vient de déposer cinq documents
    #: affiche « sans mouvement depuis 1 j » et tombe au bas d'une liste triée
    #: du plus ancien au plus récent : elle a répondu, et plus personne ne
    #: regarde. Voir `DossierCreation.attend_le_cabinet`.
    attend_le_cabinet: bool
    #: Depuis combien de jours elle attend. `0` quand ce n'est pas son tour.
    jours_d_attente: int
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
            attend_le_cabinet=dossier.attend_le_cabinet,
            jours_d_attente=(
                (a_la_date - dossier.derniere_piece_recue).days
                if dossier.attend_le_cabinet and dossier.derniere_piece_recue
                else 0
            ),
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
    #: Par où le cabinet l'a reçue : messagerie, guichet, courriel (14 octobre 2026).
    canal: CanalDeLaPiece | None = None


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
    """Le pipeline par urgence : d'abord celles qui attendent une réponse.

    ─────────────────────────────────────────────────────────────────────────────
    L'ordre n'est pas anodin : le pipeline se lit par urgence, pas par ordre
    d'arrivée. Un dossier qui dort depuis trois semaines se présente avant celui
    d'hier, et c'est ce qui fait de cet écran un outil de relance.

    ⚠️ **DEUX FILES, ET NON UNE.**

    Compter le dépôt d'une cliente comme un mouvement (pas 149) a rendu
    `immobile_depuis` honnête et l'ordre faux : un dossier où elle vient
    d'envoyer cinq documents affiche « sans mouvement depuis 1 j » et tombait
    **au bas** de la liste, sous ceux où il ne se passe rien. Elle avait répondu,
    et plus personne ne regardait.

    On sépare donc ce qui demande deux gestes différents :

      1. **elle attend une réponse** : la plus ancienne attente d'abord, parce
         qu'un client qui a fait sa part et n'obtient rien est celui qui part ;
      2. **rien ne bouge** : le plus ancien mouvement d'abord, comme avant.

    ⚠️ Le tri SQL reste celui du dépôt, et il garde son index ; on ne fait ici
    que remonter la première file. Le pipeline se lit entier, sans pagination :
    réordonner en Python ne coûte rien et évite une colonne de plus.

    ⚠️ **`immobiles_depuis_jours` garde son sens**, qui est littéral : « sans
    mouvement depuis N jours ». Un dépôt étant un mouvement, il en exclut les
    dossiers qui attendent une réponse. C'est cohérent avec ce que le paramètre
    dit, et c'est un piège pour qui le prendrait pour « qui a besoin de moi ? » :
    cette question-là se lit dans l'ordre rendu ici, sans filtre.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.SUIVRE_FORMALITE)
    jour = a_la_date or _aujourd_hui()
    seuil = None
    if immobiles_depuis_jours is not None:
        seuil = jour - timedelta(days=immobiles_depuis_jours)
    dossiers = depot().lister(etape=etape, immobiles_avant=seuil)
    lignes = [LigneDossier.depuis(d, jour) for d in dossiers]
    # ⚠️ Tri **stable** : à égalité de clé, l'ordre du dépôt est conservé, donc
    # le plus ancien mouvement d'abord à l'intérieur de chaque file.
    return sorted(
        lignes, key=lambda ligne: (not ligne.attend_le_cabinet, -ligne.jours_d_attente)
    )


@routeur.get("/checklist/{forme_juridique}", summary="Les pièces à réunir pour une forme")
def checklist(acces: AccesRequis, forme_juridique: FormeJuridique) -> list[PieceConstitution]:
    """Ce qu'il faut préparer avant de se déplacer au guichet.

    Consultable dès la qualification, avant même qu'un dossier existe : c'est la
    réponse à « qu'est-ce qu'il me faut ? », première question de tout fondateur.
    """
    exiger(acces, Permission.SUIVRE_FORMALITE)
    return list(checklist_de(forme_juridique))


class PieceAttendue(BaseModel):
    """Une pièce, telle qu'elle la lit. Sans empreinte ni détail interne."""

    code: str
    libelle: str
    obligatoire: bool
    fournie: bool
    fournie_le: date | None = None
    #: Un document est joint, et elle peut le relire depuis son compte. Un booléen,
    #: et non l'empreinte : elle n'a pas à manier un identifiant du magasin.
    document: bool = False


class IdentifiantsDeMaSociete(BaseModel):
    """Les numéros délivrés, tels que la fondatrice les lira.

    ⚠️ Chaque numéro vient avec sa date : « obtenu le » est ce qu'une banque
    demande, et une date sans son numéro n'aurait aucun sens.

    ⚠️ `immatriculee` est calculée ici plutôt que laissée à l'écran : la règle
    est « RCCM **et** NIU », et un écran qui la referait pourrait la refaire
    autrement. La patente et le numéro CNPS suivent souvent de plusieurs
    semaines et ne conditionnent rien.
    """

    rccm: str | None = None
    rccm_obtenu_le: date | None = None
    niu: str | None = None
    niu_obtenu_le: date | None = None
    patente: str | None = None
    patente_obtenue_le: date | None = None
    cnps: str | None = None
    cnps_obtenu_le: date | None = None
    #: RCCM et NIU obtenus : la société existe.
    immatriculee: bool = False


class SuiviDeMaCreation(BaseModel):
    """Le dossier dans les mots de la cliente.

    ⚠️ **UN SOUS-ENSEMBLE DÉLIBÉRÉ**, et non le dossier tel que le cabinet le
    voit. `DossierCreation` porte le fondateur, les jalons, les identifiants
    délivrés et les commentaires internes. Rien de tout cela n'a à sortir sur un
    lien qui circule dans une boîte aux lettres : elle a besoin de savoir où en
    est son dossier et ce qu'on attend d'elle, pas de relire le travail du
    cabinet.
    """

    reference: str
    denomination_souhaitee: str
    forme_juridique: str
    etape: str
    ouvert_le: date
    clos: bool
    pieces: list[PieceAttendue]
    #: Ce qui manque encore, et qui bloque. Compté ici pour que l'écran n'ait pas
    #: à refaire la règle : une pièce d'usage absente ne bloque pas.
    manquantes_bloquantes: int
    #: ⚠️ **LES IDENTIFIANTS DE SA PROPRE SOCIÉTÉ.**
    #:
    #: Ils manquaient, et c'était le plus gros manque de cette page : la cliente
    #: suivait son dossier jusqu'à « SUIVI_IMMATRICULATION », une étiquette
    #: technique, sans jamais lire **la nouvelle qu'elle attend depuis le
    #: premier jour**. Son RCCM et son NIU existaient au dossier, visibles du
    #: cabinet seul.
    #:
    #: Ce ne sont pas des informations internes : ce sont les numéros de son
    #: entreprise, que sa banque, ses fournisseurs et l'administration lui
    #: demanderont. Les lui cacher derrière un appel téléphonique n'ajoutait
    #: aucune sécurité, seulement un délai.
    #:
    #: ⚠️ Le reste du dossier n'est toujours pas rendu : ni jalons, ni
    #: commentaires internes, ni identité du collaborateur. Le sous-ensemble
    #: reste délibéré, il s'élargit de ce qui lui appartient.
    identifiants: IdentifiantsDeMaSociete


def _suivi(dossier) -> SuiviDeMaCreation:
    return SuiviDeMaCreation(
        reference=dossier.reference,
        denomination_souhaitee=dossier.denomination_souhaitee,
        forme_juridique=dossier.forme_juridique.value,
        etape=dossier.etape.value,
        ouvert_le=dossier.ouvert_le,
        clos=dossier.clos,
        pieces=[
            PieceAttendue(
                code=p.code,
                libelle=p.libelle,
                obligatoire=p.obligatoire,
                fournie=p.fournie_le is not None,
                fournie_le=p.fournie_le,
                document=p.empreinte is not None,
            )
            for p in dossier.pieces
        ],
        manquantes_bloquantes=sum(
            1 for p in dossier.pieces if p.obligatoire and p.fournie_le is None
        ),
        identifiants=IdentifiantsDeMaSociete(
            **dossier.immatriculation.model_dump(),
            immatriculee=dossier.immatriculation.immatriculee,
        ),
    )


class PieceQuElleDepose(BaseModel):
    """Ce que la fondatrice déclare après avoir téléversé son document."""

    code: str = Field(min_length=1, description="Le code de la pièce, tel que le suivi le rend")
    empreinte: str = Field(
        min_length=64,
        max_length=64,
        description="L'empreinte rendue par le dépôt de fichier scellé",
    )


@routeur.get(
    "/mes-creations",
    summary="Les créations que suit la cliente connectée",
    responses={403: {"description": "Le compte ne suit aucune création"}},
)
def mes_creations(acces: AccesRequis) -> list[SuiviDeMaCreation]:
    """Ses dossiers de formalité, dans les mots du suivi.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ LE MÊME SOUS-ENSEMBLE QUE LE LIEN SIGNÉ (14 octobre 2026)

    Le compte client s'ouvre désormais au paiement. Ce qu'elle lit connectée est
    exactement ce qu'elle lit par son lien : `SuiviDeMaCreation`, ni jalons
    internes, ni commentaires du cabinet. Un compte ne lui donne pas plus de
    droits sur son dossier, il lui évite de chercher un courriel.

    ⚠️ **SA PORTÉE, ET RIEN D'AUTRE.** Les références viennent de son
    habilitation ; une portée qui porterait aussi un NIU (la même cliente,
    devenue adhérente) ne désigne aucun dossier de formalité, et s'ignore.

    ⚠️ Déclarée AVANT `GET /{reference}` : sinon « mes-creations » serait lu
    comme une référence, et la cliente recevrait un refus de collaborateur.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.SUIVRE_MA_CREATION)
    magasin = depot()
    suivis: list[SuiviDeMaCreation] = []
    for reference in acces.dossiers or []:
        try:
            suivis.append(_suivi(magasin.lire(reference)))
        except DossierCreationIntrouvable:
            continue
    return suivis


@routeur.post(
    "/mes-creations/{reference}/pieces",
    summary="Déposer une pièce depuis son compte",
    responses={
        404: {"description": "Dossier hors de sa portée, ou pièce non attendue"},
        409: {"description": "Le dossier est clos"},
    },
)
def deposer_depuis_mon_compte(
    acces: AccesRequis, reference: str, corps: PieceQuElleDepose
) -> SuiviDeMaCreation:
    """Rattache le document qu'elle vient de téléverser (`/collecte/fichiers-de-creation`).

    ⚠️ Hors de sa portée rend 404, comme un dossier inconnu : voir `exiger_dossier`.
    """
    exiger_dossier(acces, Permission.SUIVRE_MA_CREATION, reference)
    return _rattacher_sa_piece(_lire(reference), corps, CanalDeLaPiece.ESPACE_CLIENT)


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


# ══ Ce que la fondatrice voit, sans compte ═══════════════════════════════════


def _dossier_scelle(reference: str, sceau: str):
    """Le dossier derrière un lien signé, ou 404.

    ⚠️ **404 DANS LES TROIS CAS** : sceau absent, sceau faux, dossier inconnu.
    Distinguer « dossier inconnu » de « sceau invalide » permettrait d'énumérer
    les références en lisant les refus. C'est le raisonnement déjà tenu par la
    consultation de proforma et par la résolution des locataires.
    """
    from app.contextes.creation_entreprise.domaine.lien_de_suivi import (
        LienDeSuiviInvalide,
        exiger_le_sceau,
    )

    try:
        exiger_le_sceau(reference, sceau, secret=_secret_du_suivi())
        return _lire(reference, depot())
    except (LienDeSuiviInvalide, DossierCreationIntrouvable) as refus:
        raise HTTPException(
            status_code=404,
            detail=(
                "Ce lien ne s'ouvre pas. Vérifiez que vous l'avez copié en "
                "entier, ou demandez-en un nouveau au cabinet."
            ),
        ) from refus


def _secret_du_suivi() -> str:
    """La clé du cabinet, comme pour les liens d'acceptation.

    ⚠️ Un second secret serait un second réglage à poser, à conserver et à faire
    tourner, pour une garantie identique.
    """
    from app.infrastructure.config import configuration

    return configuration().cle_chiffrement or "secret-de-developpement"


class LienDeSuivi(BaseModel):
    """Le lien de suivi d'un dossier, pour le cabinet qui doit le redonner."""

    reference: str
    denomination_souhaitee: str
    #: L'adresse complète, prête à coller dans un message ou à dicter.
    #:
    #: ⚠️ **PAS DE CHAMP `sceau` À CÔTÉ**, et ce n'est pas un oubli. Il y en avait
    #: un, « pour un canal qui composerait lui-même son adresse » : personne ne
    #: le demandait, et `tests/test_surface_des_reponses.py` l'a refusé, à raison.
    #: Un secret rendu deux fois se protège deux fois moins bien qu'une fois, et
    #: le besoin invoqué était imaginaire.
    lien: str
    #: `True` quand le dossier est clos : le lien ouvre encore, mais il n'y a
    #: plus rien à déposer. L'écran doit le dire avant qu'on le renvoie.
    clos: bool


@routeur.get(
    "/{reference}/lien-de-suivi",
    summary="Retrouver le lien de suivi d'un dossier, pour le redonner",
    responses={
        403: {"description": "Habilitation insuffisante"},
        404: {"description": "Dossier inconnu"},
    },
)
def lien_de_suivi_du_dossier(acces: AccesRequis, reference: str) -> LienDeSuivi:
    """Le lien que la cliente a reçu à l'ouverture, retrouvé.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **POURQUOI CETTE ROUTE EXISTE : LE COURRIEL SE PERD.**

    Le lien ne partait qu'une fois, à l'ouverture du dossier, dans un courriel.
    Perdu, classé en indésirable, adresse saisie de travers, cliente qui change
    de téléphone : elle n'avait plus aucun moyen d'entrer, et **le cabinet non
    plus n'avait aucun moyen de le lui rendre**. Le sceau n'apparaissait nulle
    part : ni à la fiche, ni au pipeline, ni dans un journal.

    C'est un cas de tous les jours, pas un cas limite, et il n'avait pas d'issue.
    Vu en jouant le parcours complet, pas en relisant le code.

    ⚠️ LE SCEAU EST RECALCULÉ, IL N'EST PAS STOCKÉ

    `sceau_de_suivi` est une fonction de la référence et de la clé du cabinet :
    le même dossier rend toujours le même sceau. Rien à conserver, rien à
    resynchroniser, et le lien envoyé il y a trois semaines reste exactement
    celui-ci. C'est la propriété qui distingue ce sceau de celui de la proforma,
    à usage unique et daté.

    ⚠️ LECTURE PURE, COMME `GET /proformas/{numero}/lien`

    Aucun envoi, aucune trace de transmission : le collaborateur relit, puis
    décide du canal. Envoyer d'ici mêlerait deux gestes, et un clic malheureux
    écrirait à la cliente.

    ⚠️ **`SUIVRE_FORMALITE` et non `LIRE_PROSPECT`.** Ce lien ouvre le dossier
    sans mot de passe : le rendre lisible plus largement que le dossier lui-même
    créerait un chemin d'accès plus court que la porte qu'il contourne.

    ⚠️ **CE QUI N'EST PAS FAIT, ET QU'IL FAUT SAVOIR : AUCUNE TRACE.**

    Cette route remet une valeur qui vaut accès, et rien ne consigne qui l'a
    demandée ni quand. `GET /proformas/{numero}/lien` est dans le même cas : ces
    deux routes sont les seules à rendre un secret en clair à un collaborateur.

    Le journal d'audit chaîné existe (`transverse/domaine/audit.py`) et pourrait
    les recevoir. Ce n'est pas fait ici, et la raison n'est pas technique : ce
    journal se conserve dix ans et se lit par tous les rôles portant
    `LIRE_AUDIT`. Y verser une nouvelle catégorie d'événement est une décision
    du cabinet, pas une commodité d'implémentation. Noté plutôt que sous-entendu.
    ─────────────────────────────────────────────────────────────────────────────
    """
    from app.contextes.creation_entreprise.domaine.lien_de_suivi import sceau_de_suivi
    from app.infrastructure.config import configuration

    exiger(acces, Permission.SUIVRE_FORMALITE)
    try:
        dossier = depot().lire(reference)
    except DossierCreationIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    sceau = sceau_de_suivi(dossier.reference, secret=_secret_du_suivi())
    base = configuration().adresse_publique_vitrine.rstrip("/")
    return LienDeSuivi(
        reference=dossier.reference,
        denomination_souhaitee=dossier.denomination_souhaitee,
        # ⚠️ La MÊME composition que celle du courriel d'ouverture
        # (`abonne_de_souscription._annoncer_l_ouverture`). Deux recettes pour
        # une seule adresse ont déjà divergé une fois sur la proforma, et la
        # cliente recevait un lien vers un site où son dossier n'existait pas.
        lien=f"{base}/fr/mon-dossier/{dossier.reference}?s={sceau}",
        clos=dossier.clos,
    )


@routeur.get(
    "/{reference}/suivi",
    summary="Suivre sa création depuis le lien reçu, sans compte",
    responses={404: {"description": "Lien invalide, ou dossier inconnu"}},
)
def suivre_ma_creation(
    reference: str,
    s: str = Query("", description="Le sceau du lien reçu par courriel"),
) -> SuiviDeMaCreation:
    """Où en est son dossier, et ce qu'on attend d'elle.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ AUCUNE HABILITATION, ET C'EST TOUT L'OBJET.

    Les dix autres routes de ce contexte exigent `SUIVRE_FORMALITE`, une
    habilitation de collaborateur. Entre son paiement et son immatriculation —
    les semaines où elle attend le plus — la cliente n'avait donc **rien** : ni
    dépôt de pièce, ni avancement.

    Le canal prévu était WhatsApp. Il ne tient pas : on ne peut écrire librement
    qu'à l'intérieur de la fenêtre de vingt-quatre heures ouverte par son dernier
    message, et hors de cette fenêtre le seul recours est un modèle approuvé —
    dont aucun ne l'est. Une formalité dure des semaines.

    ⚠️ **ET TOUJOURS PAS DE MOT DE PASSE.** « Un formulaire à identifiants pour
    envoyer une photo de sa carte d'identité ferait abandonner la moitié des
    dossiers. » Le compte arrive avec la société, au NIU.
    ─────────────────────────────────────────────────────────────────────────────
    """
    return _suivi(_dossier_scelle(reference, s))


@routeur.post(
    "/{reference}/suivi/pieces",
    summary="Déposer une pièce depuis le lien reçu, sans compte",
    responses={
        404: {"description": "Lien invalide, dossier inconnu, ou pièce hors checklist"},
        409: {"description": "Le dossier est clos"},
    },
)
def deposer_ma_piece(
    reference: str,
    corps: PieceQuElleDepose,
    s: str = Query("", description="Le sceau du lien reçu par courriel"),
) -> SuiviDeMaCreation:
    """Rattache le document qu'elle vient de téléverser à la pièce attendue.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ DEUX APPELS, ET C'EST LE MÊME MOTIF QU'AILLEURS.

    Le fichier part d'abord à `POST /collecte/fichiers-scelles`, qui rend son
    empreinte ; cette route-ci la rattache. C'est exactement ce que fait la
    console pour les pièces du cabinet, et pour la même raison : on ne peut pas
    désigner une pièce qui n'existe pas encore.

    ⚠️ **LA CRÉATION NE VOIT PAS LA COLLECTE**, et ne la verra pas. Le graphe ne
    l'autorise pas, et ce n'est pas nécessaire : l'empreinte suffit. Les deux
    contextes ne partagent que le sceau, qui vit à `partage/` et ne dépend de
    rien.

    ⚠️ **UN DOSSIER CLOS N'ACCEPTE PLUS RIEN.** Converti, la société existe et ses
    pièces vivent au portefeuille ; abandonné, le cabinet n'attend plus. Accepter
    un dépôt sur l'un ou l'autre rangerait un document que personne ne relira.
    ─────────────────────────────────────────────────────────────────────────────
    """
    dossier = _dossier_scelle(reference, s)
    # ─────────────────────────────────────────────────────────────────────────
    # ⚠️ ELLE NE REMPLIT QUE CE QUI EST ATTENDU D'ELLE.
    #
    # `fournir_piece` **ajoute** une pièce inconnue plutôt que de la refuser, et
    # c'est juste pour un collaborateur : « le guichet réclame parfois une pièce
    # que la liste n'a pas prévue, et le dossier doit pouvoir en garder la
    # trace ».
    #
    # Ce raisonnement est celui du cabinet face au greffe. Il n'est pas celui de
    # la cliente : lui laisser inventer un code ferait entrer dans son dossier des
    # « Pièce complémentaire XYZ » que personne n'a demandées, et qu'un
    # collaborateur devrait nettoyer. Constaté en écrivant le cas : un code
    # fantaisiste passait, et sortait dans sa propre liste.
    #
    # On restreint donc ici, et ici seulement. Le domaine garde sa largeur.
    # ─────────────────────────────────────────────────────────────────────────
    return _rattacher_sa_piece(dossier, corps, CanalDeLaPiece.LIEN_DE_SUIVI)


def _rattacher_sa_piece(
    dossier, corps: PieceQuElleDepose, canal: CanalDeLaPiece
) -> SuiviDeMaCreation:
    """Range la pièce qu'elle dépose. Partagé par le lien signé et par son compte.

    ⚠️ Les deux portes doivent refuser les mêmes choses : un dossier clos, une
    pièce que la liste n'attend pas. Recopiées, l'une des deux se serait relâchée.
    """
    if dossier.clos:
        raise HTTPException(
            status_code=409,
            detail=(
                "Ce dossier est clos : le cabinet n'attend plus de pièce. "
                "Contactez-le si vous pensez qu'il s'agit d'une erreur."
            ),
        )
    if not any(p.code == corps.code for p in dossier.pieces):
        raise HTTPException(
            status_code=404,
            detail=(
                f"Aucune pièce « {corps.code} » n'est attendue sur ce dossier. "
                "Si le cabinet vous a demandé autre chose, répondez-lui "
                "directement."
            ),
        )
    magasin = depot()
    avec = fournir_piece(
        dossier,
        corps.code,
        _aujourd_hui(),
        empreinte=corps.empreinte,
        canal=canal,
        versee_par="cliente",
    )
    magasin.enregistrer(avec)
    return _suivi(avec)


@routeur.get(
    "/{reference}/pieces/{code}/document",
    summary="Le document d'une pièce de constitution, côté cabinet",
    responses={404: {"description": "Dossier, pièce ou document introuvable"}},
)
def document_de_la_piece(acces: AccesRequis, reference: str, code: str) -> Response:
    """Le document joint à une pièce de CE dossier, pour le relire ou le télécharger.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ POURQUOI ICI, ET NON PAR `/collecte/fichiers/{empreinte}` (14 octobre 2026)

    Le cabinet a demandé « l'aperçu et le téléchargement des documents déposés,
    côté administration ». La route par empreinte exige `LIRE_PIECE`, que le
    chargé de formalités n'a pas : il ne pouvait ouvrir aucun des documents de ses
    propres dossiers. Lui donner cette permission, ou ouvrir cette route à
    `SUIVRE_FORMALITE`, lui aurait donné tout fichier du magasin dont il connaît
    l'empreinte, pièces comptables des adhérents comprises.

    Ici, le document est désigné par **le dossier et la pièce** : on ne sert que
    l'empreinte que ce dossier porte. Le magasin vit au socle partagé
    (`app.partage`), la création le lit sans passer par la collecte.

    ⚠️ Les mêmes en-têtes que les autres portes : `attachment`, `nosniff`,
    `no-store`. L'aperçu se fait dans la console, qui sait ce qu'elle affiche.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.SUIVRE_FORMALITE)
    return _le_document(_lire(reference), code)


@routeur.get(
    "/mes-creations/{reference}/pieces/{code}/document",
    summary="Relire un document qu'elle a déposé, depuis son compte",
    responses={404: {"description": "Hors de sa portée, ou aucun document"}},
)
def mon_document(acces: AccesRequis, reference: str, code: str) -> Response:
    """Ce qu'elle a envoyé, ou ce que le cabinet a versé pour elle : c'est son dossier."""
    exiger_dossier(acces, Permission.SUIVRE_MA_CREATION, reference)
    return _le_document(_lire(reference), code)


#: L'extension du fichier rendu, d'après le type lu dans ses octets.
_EXTENSIONS = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/tiff": "tif",
}


def _le_document(dossier: DossierCreation, code: str) -> Response:
    """Le fichier de la pièce, ou 404, toujours avec le même message.

    ⚠️ Pièce inconnue, pièce sans document, fichier absent du magasin : une seule
    réponse. Les distinguer ne servirait à personne de légitime.
    """
    from app.contextes.transverse.api import LOCATAIRE_PAR_DEFAUT
    from app.infrastructure.config import configuration
    from app.partage.magasin_de_fichiers import CleInvalide, MagasinFichiersLocal

    introuvable = HTTPException(status_code=404, detail="Aucun document pour cette pièce.")
    piece = next((p for p in dossier.pieces if p.code == code), None)
    if piece is None or piece.empreinte is None:
        raise introuvable
    magasin = MagasinFichiersLocal(Path(configuration().dossier_fichiers), LOCATAIRE_PAR_DEFAUT)
    try:
        contenu = magasin.lire(piece.empreinte)
        type_mime = magasin.type_mime(piece.empreinte)
    except (CleInvalide, KeyError, FileNotFoundError) as absent:
        raise introuvable from absent
    nom = re.sub(r"[^A-Za-z0-9._-]", "", f"{dossier.reference}-{code}")[:100] or "document"
    nom = f"{nom}.{_EXTENSIONS.get(type_mime, 'bin')}"
    return Response(
        content=contenu,
        media_type=type_mime,
        headers={
            "Content-Disposition": f'attachment; filename="{nom}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@routeur.post("/{reference}/pieces", summary="Marquer une pièce comme reçue")
def recevoir_piece(
    acces: AccesRequis,
    reference: str,
    demande: DemandePiece,
) -> DossierCreation:
    exiger(acces, Permission.SUIVRE_FORMALITE)
    # ⚠️ Les deux canaux de la cliente ne se déclarent pas pour elle : un
    # collaborateur qui écrirait « déposée par la cliente » attribuerait à la
    # cliente un geste qu'elle n'a pas fait.
    if demande.canal in (CanalDeLaPiece.LIEN_DE_SUIVI, CanalDeLaPiece.ESPACE_CLIENT):
        raise HTTPException(
            status_code=422,
            detail="Ce canal est celui de la cliente : choisissez messagerie, guichet ou courriel.",
        )
    magasin = depot()
    dossier = fournir_piece(
        _lire(reference, magasin),
        demande.code,
        _aujourd_hui(),
        empreinte=demande.empreinte,
        canal=demande.canal,
        versee_par=acces.compte,
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
    _annoncer_l_entreprise(clos, entreprise, adherente=demande.adherent)

    return ResultatConversion(
        dossier=clos,
        niu=entreprise.niu,
        denomination=entreprise.denomination,
        date_creation=entreprise.date_creation,
        enregistree_au_portefeuille=enregistree,
    )


#: Le fait publié quand une société créée entre au portefeuille.
#:
#: ⚠️ Le nom métier, tel que le document de conception l'écrit — c'est lui qui
#: figurera dans les journaux qu'un exploitant lit à trois heures du matin.
NOM_ENTREPRISE_CREEE = "EntrepriseCréée"


def _annoncer_l_entreprise(
    dossier: DossierCreation, entreprise, *, adherente: bool
) -> None:
    """Dépose `EntrepriseCréée`, sur lequel l'ouverture de l'accès se branche.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ POURQUOI ICI, ET PAS À L'ENCAISSEMENT.

    Le cabinet a tranché le 27 septembre : une cliente qui achète une prestation
    est une **adhérente**, et la portée d'une adhérente est le dossier de SON
    entreprise — « une souscription ne peut pas fabriquer un réviseur ».
    L'ouverture d'un accès adhérent exige donc un NIU.

    Or au moment où elle paie sa création, son entreprise **n'existe pas**. Son
    NIU n'arrive qu'ici, à la conversion. Son accès ne peut pas s'ouvrir avant —
    et ce n'est pas une contrainte technique subie : avant l'immatriculation, il
    n'y a rien à lui montrer.

    ⚠️ **Par un événement, et non par un appel.** Le graphe autorise
    `creation_entreprise → {conformite, portefeuille}` : ce contexte n'a pas le
    droit de toucher à l'identité, et c'est voulu. L'événement porte ce qu'il
    faut savoir, et l'abonné du Transverse ouvre l'accès.

    ⚠️ **La clé est la référence du dossier**, comme partout ailleurs : le relais
    garantit « au moins une fois », et deux conversions rejouées doivent tomber
    sur le même fait.

    ⚠️ **On ne lève pas.** L'entreprise est née, le dossier est clos : faire
    échouer la requête pour une boîte d'envoi indisponible reviendrait à défaire
    une immatriculation. L'accès s'ouvrira au rattrapage.
    """
    from app.contextes.transverse.api import boite_d_envoi
    from app.orchestration.boite_d_envoi import deposer

    fondateur = dossier.fondateur
    try:
        deposer(
            boite_d_envoi(),
            f"ent-{dossier.reference}",
            NOM_ENTREPRISE_CREEE,
            dossier.reference,
            {
                "niu": entreprise.niu,
                "denomination": entreprise.denomination,
                # ⚠️ Le strict nécessaire à créer son accès, et rien de plus :
                # ni le capital, ni le siège, ni la forme juridique. Ils vivent
                # au portefeuille, et un événement est recopié dans les
                # journaux, les files et les sauvegardes.
                "courriel_fondateur": fondateur.courriel,
                "nom_fondateur": fondateur.nom,
                "prenom_fondateur": fondateur.prenom,
                "telephone_fondateur": fondateur.telephone,
                # ⚠️ A-T-ELLE PRIS LE SUIVI ? C'est la seconde condition de son
                # accès, et elle est aussi importante que la première.
                #
                # Le cabinet peut immatriculer une société sans en tenir la
                # comptabilité : c'est une prestation ponctuelle, et la cliente
                # repart avec son RCCM et son NIU. Lui ouvrir un espace de suivi
                # qu'elle n'a pas acheté lui ferait croire à un service qu'elle
                # ne recevra pas, et le cabinet le découvrirait à la première
                # échéance non traitée.
                "adherente": adherente,
            },
            maintenant(),
        )
    except Exception:  # noqa: BLE001 — voir la docstring : on ne défait pas une naissance
        _journal_de_creation.exception(
            "Entreprise %s créée, mais l'annonce n'a pas pu être déposée : "
            "son accès devra être ouvert à la main.",
            entreprise.niu,
        )


def _lire(reference: str, magasin: DepotDossiersCreation | None = None) -> DossierCreation:
    try:
        return (magasin or depot()).lire(reference)
    except DossierCreationIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence


__all__ = ["Immatriculation", "Jalon", "routeur"]
