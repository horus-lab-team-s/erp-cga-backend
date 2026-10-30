"""La proforma : émission, transmission, acceptation, et son document (16 octobre 2026).

Extrait de `routes_acquisition.py`, qui dépassait 4 000 lignes : c'était un tiers du
fichier. Les routes gardent leurs chemins (`/acquisition/...`) ; le routeur est monté à
côté de celui de l'acquisition. Ce que ce module emprunte au parcours (le chiffrage, la
sollicitation du téléphone) est public là-bas.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
)
from sqlalchemy.exc import IntegrityError

from app.contextes.souscription.adaptateurs.entrant.routes_acquisition import (
    DeboursEntrant,
    proposition_du_dossier,
    solliciter_le_telephone,
)
from app.contextes.souscription.adaptateurs.entrant.sources import (
    depot_dossiers,
    depot_proformas,
)
from app.contextes.souscription.adaptateurs.sortant.compositions_proforma import (
    RepertoireDesCompositions,
)
from app.contextes.souscription.adaptateurs.sortant.depots_memoire import (
    DossierIntrouvable,
    ProformaIntrouvable,
)
from app.contextes.souscription.adaptateurs.sortant.document_proforma import (
    rendre_le_document,
)
from app.contextes.souscription.adaptateurs.sortant.magasins_memoire import (
    proformas_memoire,
)
from app.contextes.souscription.api import (
    DemandeDeContact,
    EtatDossier,
    TransitionDossierRefusee,
)
from app.contextes.souscription.application.encaissement_du_parcours import (
    ouvre_un_locataire,
)
from app.contextes.souscription.domaine.composition_proforma import (
    CompositionIntrouvable,
    CompositionProforma,
    LigneDeRubrique,
    Rubrique,
)
from app.contextes.souscription.domaine.proforma import (
    LienDAcceptation,
    LienInvalide,
    Proforma,
    TarifArrete,
    accepter,
    emettre,
    empreinte,
    lien_pour,
    numero_suivant,
)
from app.contextes.transverse.api import (
    AccesRequis,
    Permission,
    exiger,
    service_de_notification,
)
from app.infrastructure.config import configuration
from app.partage.erreurs import message_lisible
from app.partage.horloge import aujourd_hui, jour_a_douala, maintenant
from app.partage.locataire import courant
from app.partage.magasin_de_fichiers import (
    CleInvalide,
    FichierAbsent,
    MagasinFichiersLocal,
)

routeur = APIRouter(prefix="/acquisition", tags=["Acquisition"])

#: ⚠️ Un journal nommé, pour l'accusé d'acceptation : il ne lève jamais, et son seul
#: canal de signalement est celui-ci. Voir `_accuser_l_acceptation`.
_journal_acceptation = logging.getLogger(__name__)


# ── La proforma : émission, transmission, acceptation ────────────────────────


class LigneAjustee(BaseModel):
    libelle: str = Field(min_length=1, max_length=200)
    montant: Decimal = Field(gt=0)


class RubriqueAjustee(BaseModel):
    numero: str = Field(min_length=1, max_length=8)
    intitule: str = Field(min_length=1, max_length=200)
    lignes: list[LigneAjustee] = Field(min_length=1, max_length=30)


class TarifAArreter(BaseModel):
    """Ce que le responsable arrête, à partir de la proposition du chiffrage.

    ⚠️ **Le montant est arrêté par un humain, jamais recopié du chiffrage.** Le
    moteur propose un intervalle ; c'est un collaborateur habilité qui décide, et
    le motif devient obligatoire hors de l'intervalle. Recopier la référence par
    défaut ferait disparaître la décision derrière un automatisme, et le jour d'un
    litige personne ne saurait qui a fixé le prix.
    """

    montant: Decimal = Field(gt=0)
    #: ⚠️ Les mêmes entrées que le chiffrage : l'intervalle est **recalculé** à
    #: l'émission, jamais reçu (pas 65). Plancher, référence, plafond et version du
    #: barème ne se déclarent plus.
    score_charge: int = Field(default=0, ge=0)
    debours: list[DeboursEntrant] = Field(default_factory=list)
    #: Obligatoire hors de l'intervalle. Le domaine le refuse sinon.
    motif: str | None = None
    #: ⚠️ Envoyer la proposition au client par courriel, **au moment de l'émission**.
    #:
    #: C'est le seul instant où le lien d'acceptation existe en clair (voir la route).
    #: Le serveur l'écrit directement dans le courriel du client : le lien ne passe
    #: ni par l'écran du responsable, ni par un copier-coller. Faux par défaut, pour
    #: que les appels existants gardent leur comportement exact.
    envoyer_par_courriel: bool = False
    #: ⚠️ **LA COMPOSITION DU DOCUMENT**, choisie par le responsable au moment où il
    #: arrête le prix. C'est le « sélectionner les services » du parcours : le même
    #: montant n'a pas le même détail selon que le suivi sur douze mois est inclus.
    #:
    #: Absente, le document PDF n'est pas produit et l'empreinte reste calculée sur
    #: le résumé textuel, comme pour toutes les proformas émises avant le pas 148.
    #: Les codes disponibles sont ceux de `Docs/referentiel/tarification/`.
    composition: str | None = None
    # ─────────────────────────────────────────────────────────────────────────
    # ⚠️ LE DÉTAIL QUE LE RESPONSABLE A AJUSTÉ DANS L'APERÇU (14 octobre 2026).
    #
    # Le cabinet l'a demandé : le juriste voit la proforma avant de l'émettre et
    # la corrige. Sept pages de statuts deviennent neuf, une ligne de timbres
    # change, une prestation s'ajoute. Le document du référentiel est le point
    # de départ, pas un carcan.
    #
    # Absent : le détail est celui du référentiel, comme avant.
    # Présent : il le remplace ENTIER, et le montant arrêté doit en être le
    # total — voir `_document_a_emettre`.
    # ─────────────────────────────────────────────────────────────────────────
    rubriques: list[RubriqueAjustee] | None = Field(default=None, max_length=20)

    # ⚠️ **`valide_par` n'est plus reçu** (pas 63), et tout champ inconnu est refusé.
    # Le corps disait qui engageait le cabinet sur ce prix ; le contrôle interne lit
    # `chiffre_par != valide_par` pour dire si la séparation des tâches est respectée,
    # et écrire « direction » ici simulait un contrôle que personne n'avait fait.
    model_config = ConfigDict(extra="forbid")


class ProformaEmise(BaseModel):
    numero: str
    version: int
    etat: str
    montant: Decimal
    empreinte_document: str
    #: ⚠️ LE SCEAU SEUL, malgré son nom. Conservé tel quel : deux routes le
    #: rendent et la vitrine le lit. Voir `lien_client`, qui porte l'adresse.
    lien_acceptation: str | None = None
    #: L'adresse complète que la cliente ouvre, prête à coller.
    #:
    #: ⚠️ AJOUTÉE LE 28 SEPTEMBRE POUR LA VOIE WHATSAPP. La console devait sinon
    #: recomposer l'adresse à partir du sceau, de la version et de l'expiration :
    #: deux recettes pour une même adresse, qui auraient divergé au premier
    #: paramètre renommé — et la cliente aurait reçu un lien vers une page
    #: d'erreur. Elle est composée là où le courriel la compose.
    lien_client: str | None = None
    #: Le téléphone du prospect, au format international, pour ouvrir WhatsApp.
    #: ⚠️ Rendu seulement à l'émission, à celui qui détient déjà la fiche.
    telephone: str | None = None
    expire_le: datetime | None = None
    #: L'intervalle recalculé à l'émission (pas 65), pour que la console dise où le
    #: montant arrêté se situe.
    plancher: Decimal | None = None
    reference: Decimal | None = None
    plafond: Decimal | None = None
    #: Les faits sur lesquels le prix repose, score de charge déclaré compris (pas 65).
    faits: dict[str, str] = Field(default_factory=dict)
    #: Qui a chiffré, et qui a engagé le cabinet (pas 63). Rendus pour que la console
    #: dise si la séparation des tâches est respectée, au lieu de le laisser croire.
    chiffre_par: str
    valide_par: str
    separation_respectee: bool
    #: Ce qu'est devenu l'envoi par courriel demandé à l'émission, s'il l'a été :
    #:   ENVOYE        le courriel est parti, et la proforma est marquée transmise
    #:   SANS_ADRESSE  la demande de contact ne porte aucun courriel
    #:   REFUSE        le service de courriel a refusé l'envoi (à retenter autrement)
    #: ⚠️ Un échec d'envoi n'annule PAS l'émission : la proforma est numérotée et
    #: figée, le responsable la transmet alors par un autre canal.
    courriel: str | None = None
    #: L'adresse, masquée, à laquelle le courriel est parti : de quoi confirmer au
    #: responsable sans réafficher l'adresse entière.
    courriel_masque: str | None = None
    transmise_le: datetime | None = None


class Acceptee(BaseModel):
    numero: str
    version: int
    montant: Decimal
    acceptee_le: datetime
    identite_declaree: str


class LigneConsultee(BaseModel):
    libelle: str
    montant: Decimal


class RubriqueConsultee(BaseModel):
    numero: str
    intitule: str
    lignes: list[LigneConsultee]
    sous_total: Decimal


class ProformaConsultee(BaseModel):
    """Ce que le client lit avant d'accepter. **Rien de ce qui est interne au cabinet.**

    Ni l'intervalle du barème, ni qui a chiffré, ni les faits de la qualification : le
    client accepte un prix et ce qui le compose, pas la marge de négociation du cabinet.
    """

    numero: str
    version: int
    service: str
    etat: str
    montant: Decimal
    #: Le calcul du barème, ligne à ligne. **Vide quand le document porte des
    #: rubriques** : voir `rubriques`.
    lignes: list[dict[str, str]]
    debours: list[dict[str, str]]
    expire_le: datetime
    acceptee: bool
    #: Les rubriques du document qu'il a reçu, avec leurs lignes et leur
    #: sous-total.
    #:
    #: ⚠️ QUAND ELLES EXISTENT, ELLES SEULES EXPLIQUENT LE PRIX (14 octobre 2026).
    #: La page montrait les lignes du barème (« Siège hors du Littoral +
    #: 37 500 »), qui justifient l'intervalle devant le cabinet et ne totalisent
    #: pas le montant du document. Le client lisait deux explications du même
    #: prix, et elles se contredisaient.
    rubriques: list[RubriqueConsultee] = Field(default_factory=list)
    #: Le règlement est constaté, quel qu'en soit le chemin. La page du client le
    #: relit pour passer d'elle-même de « à régler » à « réglé ».
    payee: bool = False
    #: Le client peut régler en ligne depuis cette page. Faux pour une vente qui
    #: ouvre un espace : son adresse se choisit avec le cabinet.
    reglable_en_ligne: bool = False


class DemandeDAcceptation(BaseModel):
    """Ce que le client envoie en acceptant. **Publique, donc minimale.**"""

    sceau: str = Field(min_length=16)
    expire_le: datetime
    version: int = Field(ge=1)
    #: ⚠️ Déclarée, jamais vérifiée : on n'a pas de pièce d'identité, et prétendre
    #: le contraire serait une fausse garantie. Elle sert au litige.
    identite_declaree: str = Field(min_length=2, max_length=120)


# ── Le document de la proforma ───────────────────────────────────────────────
#
# ⚠️ **L'EMPREINTE PORTE LE PDF, ET NON PLUS UN RÉSUMÉ.** Jusqu'au pas 148, le
# document n'existait pas : `empreinte_document` hachait la chaîne
# « numéro|dossier|montant|instant ». Elle prouvait que ces quatre valeurs
# n'avaient pas bougé, ce qui n'est pas rien, mais elle ne prouvait rien du
# papier que la cliente lit et sur lequel elle s'engage.
#
# Le PDF produit ici est **déterministe** : deux rendus des mêmes données donnent
# le même octet, et donc la même empreinte. C'est ce qui rend `document_intact`
# vérifiable, et c'est pourquoi le rendu passe `invariant=1` à reportlab.
#
# ⚠️ **LES PROFORMAS DÉJÀ ÉMISES NE BOUGENT PAS.** Leur empreinte reste celle du
# résumé, et c'est la seule réponse honnête : recalculer donnerait une empreinte
# qui ne correspond à aucun document jamais envoyé. Sans composition choisie, le
# comportement d'avant est donc conservé tel quel.


def magasin_des_proformas() -> MagasinFichiersLocal:
    """Le magasin où dort le PDF d'une proforma.

    Le même magasin adressé par contenu que la collecte, promu au partage pour
    que la Souscription y accède sans qu'une arête « souscription lit collecte »
    soit ajoutée au graphe pour un besoin de stockage.

    ⚠️ **La clé du fichier EST son empreinte**, donc `empreinte_document`. Ce
    n'est pas une coïncidence qu'on exploite, c'est la propriété du magasin :
    retrouver le document d'une proforma ne demande aucune table de
    correspondance, et un document rangé sous une autre clé que son contenu est
    impossible à produire.

    ⚠️ **Le locataire est celui de la requête**, et non la constante par défaut
    que la collecte emploie encore. `app/partage/locataire.py` dit pourquoi : un
    défaut sert les données d'un cabinet à un autre sans que rien n'échoue. Le
    chemin étant neuf, il n'a aucune raison de naître avec la dette.
    """
    return MagasinFichiersLocal(Path(configuration().dossier_fichiers), courant())


@lru_cache
def _compositions() -> RepertoireDesCompositions:
    """Le référentiel des compositions, lu une fois par processus.

    ⚠️ **LE CHEMIN VIENT DE LA CONFIGURATION, JAMAIS DU DÉPÔT.** Écrit d'abord
    en dur sur `RACINE_DEPOT / "Docs" / "referentiel"`, il marchait en local et
    rendait une liste **vide** en conteneur, sans erreur : l'image range les
    référentiels sous `/application/donnees/referentiel`. Le responsable n'aurait
    eu aucun document à proposer, et rien ne lui aurait dit pourquoi.

    Vu en interrogeant la pile réelle, pas la suite de tests : celle-ci tourne
    depuis le dépôt, où les deux chemins coïncident.
    """
    return RepertoireDesCompositions.depuis(
        configuration().dossier_referentiel / "tarification"
    )


def _document_a_emettre(
    code_composition: str | None,
    *,
    rubriques_ajustees: list[RubriqueAjustee] | None = None,
    dossier,
    numero: str,
    reference: str,
    tarif: TarifArrete,
    version_bareme: str,
    instant: datetime,
) -> tuple[bytes, str, str, CompositionProforma | None]:
    """Le contenu à hacher, et ce qui identifie le modèle qui l'a produit.

    Rend `(contenu, modèle, version du modèle, composition imprimée)`. La
    composition est `None` sans document ; sinon c'est celle qui a servi au
    rendu, **ajustements compris**, pour que le courriel détaille les mêmes
    lignes que le PDF qu'il joint.

    Avant le 14 octobre 2026 : le triplet `(contenu, modèle, version du modèle)`.
    Avec une composition,
    le contenu est le PDF lui-même, rangé au passage ; sans, le résumé d'avant.

    ⚠️ **Le rangement se fait ici, avant l'émission**, et non après
    l'enregistrement : une proforma enregistrée dont le document manque serait
    une proforma qu'on ne peut plus montrer, et l'incohérence ne se découvrirait
    qu'au moment où la cliente clique. Le magasin étant adressé par contenu,
    ranger un document dont l'émission échoue ensuite ne coûte que trois kilo
    octets orphelins, ce qui est infiniment préférable.
    """
    if not code_composition:
        if rubriques_ajustees:
            raise HTTPException(
                status_code=422,
                detail=(
                    "un détail de rubriques a été envoyé sans document : choisissez "
                    "la proforma qu'il ajuste."
                ),
            )
        return (
            f"{numero}|{reference}|{tarif.montant}|{instant.isoformat()}".encode(),
            dossier.service,
            version_bareme,
            None,
        )

    try:
        composition = _compositions().prendre(code_composition, jour_a_douala(instant))
    except CompositionIntrouvable as absente:
        raise HTTPException(status_code=422, detail=str(absente)) from absente

    # ─────────────────────────────────────────────────────────────────────────
    # ⚠️ **LE DÉTAIL AJUSTÉ REMPLACE CELUI DU RÉFÉRENTIEL, ET LE TOTAL SUIT.**
    #
    # Le montant arrêté doit être la somme des lignes ajustées. C'est tout le
    # sens de la demande du cabinet : « les éléments de la proforma viennent
    # justifier le prix fixé ». Un prix que ses propres lignes ne totalisent pas
    # n'est justifié par rien, et le responsable qui veut un autre montant a
    # désormais le geste honnête à sa portée : modifier la ligne qui change.
    #
    # ⚠️ Le modèle enregistré porte `+AJUSTE`. Six mois plus tard, on doit
    # pouvoir distinguer d'un coup d'œil une proforma émise telle que le
    # référentiel la décrit d'une proforma que quelqu'un a retouchée.
    # ─────────────────────────────────────────────────────────────────────────
    ajustee = False
    if rubriques_ajustees:
        nouvelles = tuple(
            Rubrique(
                numero=rubrique.numero,
                intitule=rubrique.intitule,
                lignes=tuple(
                    LigneDeRubrique(libelle=ligne.libelle.strip(), montant=ligne.montant)
                    for ligne in rubrique.lignes
                ),
            )
            for rubrique in rubriques_ajustees
        )
        ajustee = nouvelles != composition.rubriques
        composition = composition.model_copy(update={"rubriques": nouvelles})
        if tarif.montant != composition.total:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"le montant arrêté ({tarif.montant:.0f} FCFA) n'est pas le total "
                    f"des lignes de la proforma ({composition.total:.0f} FCFA). C'est "
                    "le détail qui justifie le prix : ajustez une ligne, ou le montant."
                ),
            )

    pdf = rendre_le_document(
        composition,
        numero=numero,
        version=1,
        emise_le=instant,
        client=dossier.demande.nom,
        # ⚠️ Le total arrêté par le responsable, et non celui de la composition.
        # Il a le droit de s'en écarter, motif à l'appui ; le document doit alors
        # porter les deux, sans quoi la cliente signerait un total que le détail
        # sous ses yeux ne justifie pas.
        total_arrete=tarif.montant,
    )
    magasin_des_proformas().deposer(
        empreinte(pdf), pdf, type_mime="application/pdf"
    )
    return (
        pdf,
        composition.code,
        composition.statut + ("+AJUSTE" if ajustee else ""),
        composition,
    )


class RubriqueOfferte(BaseModel):
    """Une rubrique telle que l'écran la montre au responsable."""

    numero: str
    intitule: str
    lignes: list[dict[str, str]]
    sous_total: Decimal


class CompositionOfferte(BaseModel):
    """Une composition proposable à l'émission."""

    code: str
    service: str
    forme_juridique: str
    titre: str
    libelle: str
    total: Decimal
    #: ⚠️ `TRANSCRIT` tant que la direction n'a pas confirmé les montants.
    #: L'écran doit le montrer : un responsable qui envoie une proforma issue
    #: d'une transcription non validée doit le savoir avant, pas après.
    statut: str
    rubriques: list[RubriqueOfferte]


@routeur.get(
    "/compositions-proforma",
    summary="Les compositions de document proposables à l'émission",
    responses={403: {"description": "Habilitation insuffisante"}},
)
def lister_les_compositions(
    acces: AccesRequis, service: str | None = Query(default=None)
) -> list[CompositionOfferte]:
    """Le catalogue des documents, filtré sur le service quand on le précise.

    ⚠️ Cette route existe pour la même raison que celle des motifs de
    classement : une liste recopiée dans l'écran diverge au premier document
    ajouté, et le formulaire proposerait alors des codes que le serveur refuse.

    ⚠️ **LE DÉTAIL EST RENDU, PAS SEULEMENT LE CODE.** Le responsable choisit un
    document dont il doit voir les rubriques avant de l'envoyer à une cliente.
    Un menu déroulant de codes nus l'obligerait à ouvrir le référentiel à côté.
    """
    exiger(acces, Permission.LIRE_PROSPECT)
    aujourdhui = aujourd_hui()
    return [
        CompositionOfferte(
            code=composition.code,
            service=composition.service,
            forme_juridique=composition.forme_juridique,
            titre=composition.titre,
            libelle=composition.libelle,
            total=composition.total,
            statut=composition.statut,
            rubriques=[
                RubriqueOfferte(
                    numero=rubrique.numero,
                    intitule=rubrique.intitule,
                    lignes=[
                        {"libelle": ligne.libelle, "montant": str(ligne.montant)}
                        for ligne in rubrique.lignes
                    ],
                    sous_total=rubrique.sous_total,
                )
                for rubrique in composition.rubriques
            ],
        )
        for composition in _compositions().toutes()
        # ⚠️ Seulement celles en vigueur aujourd'hui : proposer une composition
        # périmée ferait émettre un document aux montants de l'an dernier.
        if composition.couvre(aujourdhui)
        and (service is None or composition.service == service)
    ]


@routeur.post(
    "/dossiers/{reference}/proforma",
    summary="Émettre la proforma d'un dossier chiffré",
    status_code=201,
    responses={
        403: {"description": "Habilitation insuffisante"},
        404: {"description": "Dossier inconnu"},
        409: {"description": "Le dossier n'est pas au stade du chiffrage"},
        422: {"description": "Tarif hors intervalle sans motif"},
    },
)
def emettre_la_proforma(
    acces: AccesRequis, reference: str, corps: TarifAArreter
) -> ProformaEmise:
    """Fige le document, le numérote dans la série, et rend son lien d'acceptation.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **CE GESTE ÉTAIT LE SEUL DU PARCOURS SANS ROUTE.**

    Le domaine existe depuis le pas 10, éprouvé ; la surface HTTP manquait. La
    recette de bout en bout devait passer par le cas d'usage, ce qui veut dire
    qu'aucune console n'aurait pu émettre une proforma.

    LE NUMÉRO EST TIRÉ DE LA SÉRIE, ET LE CONFLIT EST POSSIBLE

    `dernier_numero` peut mentir : entre la lecture et l'écriture, une autre requête
    a pu émettre. C'est la contrainte d'unicité qui arbitre, et le refus rend `409`
    plutôt que `500` : le responsable renvoie sa demande, et la seconde tentative
    prend le numéro suivant.

    Verrouiller la série entière sérialiserait toutes les émissions du cabinet pour
    une garantie identique.

    ⚠️ **LE LIEN N'EST RENDU QU'ICI.** Il porte un sceau à usage unique ; le
    remettre à chaque lecture de la proforma multiplierait les chemins par lesquels
    un engagement contractuel peut fuiter.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.QUALIFIER_PROSPECT)
    depot = depot_dossiers()
    try:
        dossier = depot.lire(reference)
    except DossierIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    instant = maintenant()
    config = configuration()
    proposition, faits_conserves = proposition_du_dossier(
        dossier, corps.score_charge, corps.debours
    )
    try:
        tarif = TarifArrete(
            montant=corps.montant,
            plancher=proposition.plancher,
            reference=proposition.reference,
            plafond=proposition.plafond,
            version_bareme=proposition.version_bareme,
            # ⚠️ Le compte qui chiffre est celui de la session, jamais un champ du
            # corps. Le laisser déclarer permettrait d'attribuer un tarif à un
            # collègue, et le jour d'un litige la trace désignerait la mauvaise
            # personne.
            chiffre_par=acces.compte,
            # ⚠️ Le même compte, et c'est la vérité : aucun geste distinct de
            # validation n'existe encore. `separation_respectee` vaut donc `False`, et
            # le contrôle interne le verra, au lieu de lire une validation déclarée.
            # Le jour où une validation par un second collaborateur existera, elle
            # sera sa propre route, sous sa propre session.
            valide_par=acces.compte,
            arrete_le=instant,
            motif=corps.motif,
        )
    except ValueError as refus:
        raise HTTPException(status_code=422, detail=message_lisible(refus)) from refus

    proformas = depot_proformas()
    numero = numero_suivant(
        proformas.dernier_numero(config.serie_proforma, instant.year),
        serie=config.serie_proforma,
        annee=instant.year,
    )
    contenu, modele, version_modele, composition_imprimee = _document_a_emettre(
        corps.composition,
        rubriques_ajustees=corps.rubriques,
        dossier=dossier,
        numero=numero,
        reference=reference,
        tarif=tarif,
        version_bareme=proposition.version_bareme,
        instant=instant,
    )

    try:
        proforma = emettre(
            numero=numero,
            dossier=reference,
            service=dossier.service,
            tarif=tarif,
            contenu=contenu,
            modele=modele,
            version_modele=version_modele,
            a_l_instant=instant,
            # ⚠️ Ces trois champs existaient au domaine et n'étaient jamais remplis :
            # la proforma émise ne gardait ni les lignes du calcul, ni les débours, ni
            # les faits sur lesquels le prix reposait (pas 65).
            lignes=proposition.lignes,
            debours=proposition.debours,
            faits=faits_conserves,
            rubriques=composition_imprimee.rubriques if composition_imprimee else (),
        )
        avance = dossier.emettre_la_proforma(instant)
    except TransitionDossierRefusee as echec:
        raise HTTPException(status_code=409, detail=str(echec)) from echec
    except ValueError as refus:
        raise HTTPException(status_code=422, detail=message_lisible(refus)) from refus

    lien = _lien_de(proforma)
    try:
        proformas.enregistrer(proforma)
        depot.enregistrer(avance)
    except IntegrityError as conflit:
        raise HTTPException(
            status_code=409,
            detail=(
                f"le numéro {numero} vient d'être pris par une autre émission. "
                "Renvoyer la demande : la suivante prendra le numéro d'après."
            ),
        ) from conflit

    courriel, courriel_masque = None, None
    if corps.envoyer_par_courriel:
        courriel, courriel_masque = _envoyer_la_proforma_par_courriel(
            proforma,
            dossier.demande,
            lien,
            composition=composition_imprimee,
            document=contenu if composition_imprimee is not None else None,
        )
        if courriel == "ENVOYE":
            # ⚠️ Le courriel est parti : c'est une transmission, et c'est elle qui
            # arme la relance. Sans cette ligne, le client aurait sa proposition et
            # personne ne le relancerait jamais.
            proforma = proforma.transmise(instant)
            proformas.enregistrer(proforma)

    return ProformaEmise(
        numero=proforma.numero,
        version=proforma.version,
        etat=proforma.etat.value,
        montant=proforma.tarif.montant,
        empreinte_document=proforma.empreinte_document,
        courriel=courriel,
        courriel_masque=courriel_masque,
        transmise_le=proforma.transmise_le,
        lien_acceptation=lien.sceau,
        lien_client=adresse_de_la_proforma(
            lien.numero, lien.version, lien.expire_le, lien.sceau
        ),
        telephone=dossier.demande.telephone,
        expire_le=lien.expire_le,
        plancher=proforma.tarif.plancher,
        reference=proforma.tarif.reference,
        plafond=proforma.tarif.plafond,
        faits=proforma.faits,
        chiffre_par=proforma.tarif.chiffre_par,
        valide_par=proforma.tarif.valide_par,
        separation_respectee=proforma.tarif.separation_respectee,
    )


def adresse_de_la_proforma(numero: str, version: int, expire_le: datetime, sceau: str) -> str:
    """L'adresse complète que le client ouvre, sur la vitrine.

    ⚠️ Les mêmes paramètres courts que la page de la vitrine lit (`v`, `e`, `s`) :
    changer l'un sans l'autre donnerait au client un lien qui ouvre une page d'erreur.
    La date est encodée, parce que le `+` d'un fuseau horaire deviendrait une espace.
    """
    base = configuration().adresse_publique_vitrine.rstrip("/")
    requete = urlencode({"v": version, "e": expire_le.isoformat(), "s": sceau})
    return f"{base}/fr/proforma/{quote(numero)}?{requete}"


def _francs_du_courriel(valeur: Decimal) -> str:
    """Un montant tel qu'un courriel l'écrit : espace fine entre les milliers."""
    return f"{valeur:,.0f}".replace(",", "\u202f") + " FCFA"


def _envoyer_la_proforma_par_courriel(
    proforma: Proforma,
    demande: DemandeDeContact,
    lien: LienDAcceptation,
    *,
    composition: CompositionProforma | None = None,
    document: bytes | None = None,
) -> tuple[str, str | None]:
    """Écrit au client, et dit ce qui s'est passé. **Ne lève jamais.**

    Le service de notification rend un booléen et ne lève pas (voir son en-tête) :
    l'émission, elle, est déjà écrite en base. Faire échouer la requête ici ferait
    croire au responsable que la proforma n'existe pas, alors qu'elle a pris son
    numéro dans la série.
    """
    if not demande.courriel:
        return "SANS_ADRESSE", None
    contexte: dict[str, Any] = {
        "nom": demande.nom,
        "proforma": proforma.numero,
        "montant": f"{proforma.tarif.montant:,.0f}".replace(",", "\u202f"),
        "lien": adresse_de_la_proforma(
            lien.numero, lien.version, lien.expire_le, lien.sceau
        ),
        "expire_le": lien.expire_le.strftime("%d/%m/%Y"),
    }
    code = "proforma.envoi"
    # ⚠️ AVEC UN DOCUMENT, LE COURRIEL EN PORTE LE DÉTAIL ET LE JOINT.
    #
    # Une ligne par rubrique, puis le total : ce que la cliente lit dans le
    # message est ce que le PDF totalise, parce que les deux viennent de la même
    # composition, ajustements du responsable compris. Le détail ligne à ligne
    # reste dans le document : un courriel de vingt lignes ne se lit pas.
    if composition is not None and document is not None:
        code = "proforma.envoi.detaillee"
        contexte["detail"] = [
            (
                # ⚠️ L'intitulé tel que le référentiel le porte : le passer en
                # minuscules écrasait les sigles, « cfce », « cnps », « dsf ».
                f"{rubrique.numero}. {rubrique.intitule}",
                _francs_du_courriel(rubrique.sous_total),
            )
            for rubrique in composition.rubriques
        ] + [("Total général", _francs_du_courriel(proforma.tarif.montant))]
        contexte["pieces_jointes"] = [
            (f"Proforma-{proforma.numero}.pdf", document, "application/pdf")
        ]
    parti = service_de_notification().envoyer(
        code, destinataire=demande.courriel, contexte=contexte
    )
    nom, _, domaine = demande.courriel.partition("@")
    masque = f"{nom[:1]}***@{domaine}"
    return ("ENVOYE" if parti else "REFUSE"), masque


class LienDeLaProforma(BaseModel):
    """De quoi renvoyer une proposition au client, sans rien changer au dossier."""

    numero: str
    #: ⚠️ Un entier, comme sur `ProformaEmise` : une proforma corrigée change de
    #: version, et l'adresse porte laquelle. Deux types pour un même champ
    #: feraient diverger les deux écrans qui le lisent.
    version: int
    etat: str
    montant: Decimal
    #: L'adresse complète que le client ouvrira. Composée par le serveur, comme
    #: le courriel la compose. Voir `adresse_de_la_proforma`.
    lien_client: str
    expire_le: datetime
    #: Pour composer le message WhatsApp du cabinet. Jamais masqué ici : cette
    #: route exige `QUALIFIER_PROSPECT`, donc son appelant traite déjà ce dossier.
    telephone: str
    nom: str
    #: `False` quand le client n'a pas autorisé la messagerie à sa demande.
    whatsapp_autorise: bool


@routeur.get(
    "/proformas/{numero}/lien",
    summary="Retrouver le lien d'une proforma, pour le renvoyer au client",
    responses={
        403: {"description": "Habilitation insuffisante"},
        404: {"description": "Proforma inconnue"},
    },
)
def lien_de_la_proforma(acces: AccesRequis, numero: str) -> LienDeLaProforma:
    """Le lien du client, relu. **Aucun effet de bord, et c'est tout l'objet.**

    ─────────────────────────────────────────────────────────────────────────────
    POURQUOI CETTE ROUTE EXISTE

    Le lien d'acceptation n'était rendu qu'à **l'émission**. La console le gardait
    dans l'état d'un composant, et le perdait au premier rechargement de page :
    le bouton « Envoyer sur WhatsApp » disparaissait alors pour toujours.

    Constaté le 29 septembre, sur sept dossiers réels en `PROFORMA_EMISE` : aucun
    n'affichait le bouton. Le responsable qui revenait le lendemain sur un dossier
    n'avait plus aucun moyen de renvoyer la proposition autrement que par
    courriel.

    ⚠️ **POURQUOI PAS SUR LA FICHE DU DOSSIER.** Elle n'exige que `LIRE_PROSPECT`,
    et son en-tête le dit : « jamais le lien d'acceptation ». Ce lien est un
    porteur — qui l'a, peut accepter. L'élargir à tous les lecteurs de prospects
    serait une extension silencieuse de ce que le sceau protège.

    ⚠️ **POURQUOI PAS `POST /transmission`.** Elle rend déjà le lien, et la console
    aurait pu s'en servir. Mais elle **date l'envoi**, et c'est cette date qui arme
    la relance : la rappeler pour relire un lien remettrait le compteur à zéro à
    chaque consultation, et le client ne serait jamais relancé. Pire, elle
    obligerait à déclarer « j'ai envoyé le lien » **avant** de l'avoir.

    Une lecture est une lecture. Elle ne note rien.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.QUALIFIER_PROSPECT)
    try:
        proforma = depot_proformas().lire(numero)
    except ProformaIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    demande = depot_dossiers().lire(proforma.dossier).demande
    lien = _lien_de(proforma)
    return LienDeLaProforma(
        numero=proforma.numero,
        version=proforma.version,
        etat=proforma.etat.value,
        montant=proforma.tarif.montant,
        lien_client=adresse_de_la_proforma(
            lien.numero, lien.version, lien.expire_le, lien.sceau
        ),
        expire_le=lien.expire_le,
        telephone=demande.telephone,
        nom=demande.nom,
        whatsapp_autorise=demande.consentement.vaut_maintenant,
    )


@routeur.post(
    "/proformas/{numero}/transmission",
    summary="Marquer la proforma transmise au client",
    responses={
        403: {"description": "Habilitation insuffisante"},
        404: {"description": "Proforma inconnue"},
        409: {"description": "État incompatible"},
    },
)
def transmettre(acces: AccesRequis, numero: str) -> ProformaEmise:
    """Note que le document est parti. **C'est cette date qui arme la relance.**

    ⚠️ Sans elle, le balayage ne voit rien : `a_relancer` cherche les transmises
    dont la date est renseignée. Une proforma émise et jamais marquée transmise
    n'est relancée par personne, et le dossier dort.
    """
    exiger(acces, Permission.QUALIFIER_PROSPECT)
    proformas = depot_proformas()
    try:
        proforma = proformas.lire(numero)
    except ProformaIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec
    try:
        transmise = proforma.transmise(maintenant())
    except ValueError as refus:
        raise HTTPException(status_code=409, detail=message_lisible(refus)) from refus

    proformas.enregistrer(transmise)
    # ⚠️ LE LIEN EST RENDU ICI AUSSI, et c'est une correction du 27 septembre.
    #
    # Il n'était rendu qu'à l'émission. Un client qui perd son courriel n'avait
    # plus de chemin vers sa proforma, et le cabinet n'avait d'autre recours que
    # d'émettre une v2 d'un document que personne n'avait contesté — ce qui
    # change le numéro, invalide l'ancien lien, et brouille le suivi.
    #
    # Le rendre ici est sans risque : cette route demande déjà
    # `QUALIFIER_PROSPECT`, et le lien ne dit rien que son porteur ne doive voir.
    lien = _lien_de(transmise)
    return ProformaEmise(
        numero=transmise.numero,
        version=transmise.version,
        etat=transmise.etat.value,
        montant=transmise.tarif.montant,
        empreinte_document=transmise.empreinte_document,
        lien_acceptation=lien.sceau,
        lien_client=adresse_de_la_proforma(
            lien.numero, lien.version, lien.expire_le, lien.sceau
        ),
        expire_le=lien.expire_le,
        transmise_le=transmise.transmise_le,
        chiffre_par=transmise.tarif.chiffre_par,
        valide_par=transmise.tarif.valide_par,
        separation_respectee=transmise.tarif.separation_respectee,
    )


def _lien_de(proforma: Proforma) -> LienDAcceptation:
    """Le lien d'acceptation d'une proforma, **recalculé** et non relu.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ LE LIEN N'EST PAS PERSISTÉ, ET C'EST UN CHOIX QUI SE TIENT.

    Il ne contient rien que la proforma ne porte déjà : son numéro, sa version,
    et une date d'expiration qui vaut `emise_le + N jours`. Le sceau en est la
    signature. Le ranger en base ajouterait une donnée dérivée à tenir
    cohérente, pour rien.

    ⚠️ MAIS IL ÉTAIT RENDU UNE SEULE FOIS, ET C'ÉTAIT LE DÉFAUT.

    Constaté le 27 septembre en jouant le parcours d'une création souscrite :
    l'émission rendait le sceau, la transmission ne le rendait pas, et rien ne
    permettait de le retrouver. Un collaborateur dont le client a perdu son
    courriel ne pouvait pas lui renvoyer son lien — il fallait émettre une
    nouvelle version d'un document que personne n'avait contesté.

    Le calcul vit donc ici, en un seul endroit, et les deux routes s'en servent.
    ─────────────────────────────────────────────────────────────────────────────
    """
    return lien_pour(
        proforma,
        expire_le=proforma.emise_le
        + timedelta(days=configuration().validite_lien_acceptation_jours),
        secret=_secret_des_liens(),
    )


def _lien_presente(numero: str, version: int, expire_le: datetime, sceau: str) -> LienDAcceptation:
    """Le lien tel que le client le présente, ou un refus **identique à un sceau faux**.

    ⚠️ Un sceau malformé levait une erreur de validation non rattrapée, donc une erreur
    500 : une réponse distincte de celle d'un sceau faux, et une page d'erreur pour un
    visiteur. Il reçoit désormais le même refus qu'un sceau bien formé mais faux.
    """
    try:
        return LienDAcceptation(numero=numero, version=version, expire_le=expire_le, sceau=sceau)
    except ValidationError as refus:
        raise HTTPException(
            status_code=422,
            detail=(
                f"lien de {numero} : sceau invalide. Le lien a été modifié, ou il vient "
                "d'un autre environnement."
            ),
        ) from refus


@routeur.get(
    "/proformas/{numero}/consultation",
    summary="Le client lit sa proforma par son lien signé",
    responses={
        404: {"description": "Proforma inconnue"},
        422: {"description": "Lien invalide, expiré, ou d'une autre version"},
    },
)
def consulter_la_proforma(
    numero: str,
    version: int = Query(ge=1),
    expire_le: datetime = Query(),
    sceau: str = Query(min_length=16),
) -> ProformaConsultee:
    """⚠️ **Publique, comme l'acceptation, et pour la même raison.** (pas 67)

    ─────────────────────────────────────────────────────────────────────────────
    POURQUOI CETTE ROUTE EXISTE

    L'acceptation était publique et scellée, mais **aucune route ne montrait au client
    ce qu'il acceptait**. Un engagement signé sur un prix qu'on n'a pas vu ne se défend
    pas, ni devant le client, ni devant un juge.

    ⚠️ LE SCEAU D'ABORD, ICI AUSSI

    Le même contrôle que l'acceptation, dans le même ordre : sans lien valable, un
    numéro existant et un numéro inventé reçoivent la même réponse. Lire ne consomme
    rien : le client peut rouvrir sa proforma autant de fois qu'il veut avant de
    l'accepter.

    ⚠️ UNE PROFORMA REMPLACÉE SE LIT, ET LE DIT

    Une nouvelle version prend un **nouveau numéro** et marque l'ancien document
    `REMPLACEE`. Le lien de l'ancienne lit donc l'ancienne, dont l'état le dit : l'écran
    du client ne propose pas de l'accepter, et le domaine le refuserait. Un contrôle de
    version écrit ici au premier jet ne pouvait jamais se déclencher, et une mutation
    l'a montré en survivant ; il a été retiré plutôt que gardé pour la forme.
    ─────────────────────────────────────────────────────────────────────────────
    """
    lien = _lien_presente(numero, version, expire_le, sceau)
    try:
        lien.exiger_valide(maintenant(), secret=_secret_des_liens())
    except LienInvalide as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus
    try:
        proforma = depot_proformas().lire(numero)
    except ProformaIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec
    return ProformaConsultee(
        numero=proforma.numero,
        version=proforma.version,
        service=proforma.service,
        etat=proforma.etat.value,
        montant=proforma.tarif.montant,
        lignes=[]
        if proforma.rubriques
        else [
            {"libelle": ligne.libelle, "montant": str(ligne.montant)} for ligne in proforma.lignes
        ],
        rubriques=[
            RubriqueConsultee(
                numero=r.numero,
                intitule=r.intitule,
                lignes=[
                    LigneConsultee(libelle=ligne.libelle, montant=ligne.montant)
                    for ligne in r.lignes
                ],
                sous_total=r.sous_total,
            )
            for r in proforma.rubriques
        ],
        debours=[
            {"libelle": d.libelle, "montant": str(d.montant)} for d in proforma.debours
        ],
        expire_le=expire_le,
        acceptee=proforma.etat.value == "ACCEPTEE",
        payee=_dossier_regle(proforma),
        reglable_en_ligne=not ouvre_un_locataire(proforma.service),
    )


def _dossier_regle(proforma: Proforma) -> bool:
    """Le dossier de cette proposition est-il encaissé ? Faux si on ne sait pas."""
    try:
        return depot_dossiers().lire(proforma.dossier).etat is EtatDossier.PAYEE
    except DossierIntrouvable:
        return False


class DemandeDePaiementEnLigne(BaseModel):
    """Ce que le client envoie en cliquant « Payer par mobile money ».

    Publique, donc minimale, et scellée comme l'acceptation : sans le lien
    personnel de la proposition, personne ne fait sonner le téléphone de personne.
    """

    sceau: str = Field(min_length=16)
    expire_le: datetime
    version: int = Field(ge=1)
    #: Le numéro à débiter. Vide : celui que le client a donné à sa demande.
    telephone: str = Field(default="", max_length=30)


class PaiementEnLigneDemande(BaseModel):
    """Ce que la page du client affiche après le clic. Rien d'interne."""

    #: Le prestataire a accepté d'envoyer la demande. ⚠️ Ce n'est PAS un paiement.
    accepte: bool
    message: str
    #: Les quatre derniers chiffres seulement : la page est ouverte par un lien.
    telephone: str
    #: Aucun débit réel n'a été demandé : la plateforme n'a pas d'identifiants
    #: marchands. La page le dit au client plutôt que de le laisser attendre.
    simule: bool


@routeur.post(
    "/proformas/{numero}/paiement-en-ligne",
    status_code=202,
    summary="Le client règle sa proposition acceptée par mobile money",
    responses={
        404: {"description": "Proforma inconnue"},
        409: {"description": "La proposition n'est pas à régler"},
        422: {"description": "Lien invalide ou numéro inexploitable"},
    },
)
def payer_en_ligne(numero: str, corps: DemandeDePaiementEnLigne) -> PaiementEnLigneDemande:
    """Publique. Le client paie lui-même, sans attendre que le cabinet le rappelle.

    ─────────────────────────────────────────────────────────────────────────
    CE QUE LE CABINET A DEMANDÉ, LE 14 OCTOBRE 2026

    « Un lien de paiement Tara Money pour le montant mentionné par le responsable,
    avec auto-validation si le paiement est effectif. » La cliente acceptait sa
    proposition, lisait « le cabinet revient vers vous pour le règlement », et
    attendait un appel.

    ⚠️ **TARA N'A PAS DE PAGE DE PAIEMENT AU CAMEROUN.** Il pousse un menu sur le
    téléphone de l'abonné, qui valide avec son code — voir `fournisseur_tara`. Le
    « lien de paiement » est donc la page de la proposition elle-même, et ce
    geste-ci est ce qui se passe quand le client y clique.

    ⚠️ **L'AUTO-VALIDATION N'EST PAS ICI.** Cette route demande le débit ; elle
    ne constate rien. Le règlement est constaté par la notification de
    l'opérateur, rapprochée d'un paiement que nous avons créé, pour un montant
    que nous avons fixé — et, si la notification se perd, par la réconciliation
    qui interroge l'opérateur. La page du client relit simplement son état.

    ⚠️ **LE SCEAU D'ABORD, COMME À L'ACCEPTATION.** Le numéro d'une proforma est
    imprimé sur un document. Sans le lien personnel, quiconque le connaît
    ferait sonner le téléphone d'un client à volonté.

    ⚠️ **SEULEMENT APRÈS L'ACCORD, ET JAMAIS DEUX FOIS.** Un dossier qui n'a pas
    accepté ne doit rien ; un dossier réglé ne se redemande pas.

    ⚠️ **PAS POUR UNE VENTE QUI OUVRE UN ESPACE.** Son adresse se choisit avec le
    cabinet, et un client ne la choisit pas seul depuis un lien.
    ─────────────────────────────────────────────────────────────────────────
    """
    lien = _lien_presente(numero, corps.version, corps.expire_le, corps.sceau)
    try:
        lien.exiger_valide(maintenant(), secret=_secret_des_liens())
    except LienInvalide as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus
    try:
        proforma = depot_proformas().lire(numero)
    except ProformaIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec
    depot = depot_dossiers()
    try:
        dossier = depot.lire(proforma.dossier)
    except DossierIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    if dossier.etat is EtatDossier.PAYEE:
        raise HTTPException(
            status_code=409, detail="Cette proposition est déjà réglée : il n'y a rien à payer."
        )
    if ouvre_un_locataire(proforma.service):
        raise HTTPException(
            status_code=409,
            detail=(
                "Cette proposition se règle avec le cabinet, qui choisit avec vous "
                "l'adresse de votre espace."
            ),
        )
    from app.contextes.souscription.adaptateurs.entrant.routes_http import comptoir

    demande = solliciter_le_telephone(proforma, dossier, depot, None, corps.telephone)
    return PaiementEnLigneDemande(
        accepte=demande.accepte,
        message=demande.message,
        telephone=f"…{demande.telephone[-4:]}",
        simule=bool(comptoir().fournisseur.simule),
    )


def servir_le_pdf_de_la_proforma(proforma: Proforma) -> Response:
    """Sert le PDF d'une proforma, après avoir vérifié qu'il est intact.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **L'INTÉGRITÉ EST VÉRIFIÉE À CHAQUE LECTURE**, comme le domaine l'exige
    (`Proforma.document_intact`). Ce n'est pas une précaution de principe : le
    document est ce sur quoi la cliente s'engage, et servir un fichier altéré
    sans le dire vaudrait mieux ne rien servir du tout.

    Une altération rend `409` et non `500` : le serveur fonctionne, c'est le
    document qui ne correspond plus. Un `500` enverrait chercher une panne
    d'infrastructure là où il y a un fichier à remplacer.

    ⚠️ **UNE PROFORMA D'AVANT LE PAS 148 N'A PAS DE DOCUMENT.** Son empreinte
    porte un résumé textuel, jamais rangé au magasin. Elle rend donc `404`, et
    c'est la vérité : il n'y a pas de PDF à montrer. Le détail chiffré reste
    lisible par `GET /proformas/{numero}/consultation`.
    ─────────────────────────────────────────────────────────────────────────────
    """
    magasin = magasin_des_proformas()
    try:
        contenu = magasin.lire(proforma.empreinte_document)
    except (CleInvalide, FichierAbsent) as absent:
        raise HTTPException(
            status_code=404,
            detail=(
                f"aucun document pour {proforma.numero}. Les proformas émises avant "
                "la mise en service du document PDF n'en ont pas ; leur détail se lit "
                "par la consultation."
            ),
        ) from absent

    if not proforma.document_intact(contenu):
        raise HTTPException(
            status_code=409,
            detail=(
                f"le document de {proforma.numero} ne correspond plus à son empreinte. "
                "Il a été altéré depuis l'émission et ne peut pas être servi."
            ),
        )

    return Response(
        content=contenu,
        media_type="application/pdf",
        headers={
            # `inline` et non `attachment` : la cliente doit **lire** sa proposition
            # avant d'accepter, et un téléchargement forcé mettrait un fichier entre
            # elle et sa décision. Le risque d'exécution qui justifie `attachment`
            # sur un justificatif déposé n'existe pas ici : ce PDF, c'est nous qui
            # l'avons produit.
            "Content-Disposition": (
                f'inline; filename="proforma-{proforma.numero}.pdf"'
            ),
            "X-Content-Type-Options": "nosniff",
            # Un engagement contractuel nominatif n'a rien à faire dans un cache
            # partagé.
            "Cache-Control": "private, no-store",
        },
    )


@routeur.get(
    "/proformas/{numero}/document",
    summary="Le client lit le PDF de sa proforma par son lien signé",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "Le document"},
        404: {"description": "Proforma inconnue, ou sans document"},
        409: {"description": "Le document ne correspond plus à son empreinte"},
        422: {"description": "Lien invalide, expiré, ou d'une autre version"},
    },
)
def lire_le_document_de_la_proforma(
    numero: str,
    version: int = Query(ge=1),
    expire_le: datetime = Query(),
    sceau: str = Query(min_length=16),
) -> Response:
    """⚠️ **Publique et scellée**, exactement comme la consultation.

    Le même contrôle, dans le même ordre, et pour la même raison : sans lien
    valable, un numéro existant et un numéro inventé reçoivent la même réponse.
    Lire ne consomme rien ; la cliente rouvre sa proforma autant qu'elle veut.
    """
    lien = _lien_presente(numero, version, expire_le, sceau)
    try:
        lien.exiger_valide(maintenant(), secret=_secret_des_liens())
    except LienInvalide as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus
    try:
        return servir_le_pdf_de_la_proforma(depot_proformas().lire(numero))
    except ProformaIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec


@routeur.get(
    "/proformas/{numero}/document-interne",
    summary="Le cabinet relit le PDF d'une proforma qu'il a émise",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "Le document"},
        403: {"description": "Habilitation insuffisante"},
        404: {"description": "Proforma inconnue, ou sans document"},
        409: {"description": "Le document ne correspond plus à son empreinte"},
    },
)
def relire_le_document_de_la_proforma(acces: AccesRequis, numero: str) -> Response:
    """Le même document, sous session, sans sceau.

    ⚠️ **Une route distincte, et non la précédente sans sceau.** Le lien du
    client expire, et c'est voulu ; le cabinet, lui, doit pouvoir ressortir la
    proforma de l'an dernier le jour d'un litige. Assouplir l'expiration de la
    route publique pour couvrir ce besoin aurait allongé la vie du lien qui
    circule par courriel, ce qui est exactement l'inverse de ce qu'on veut.
    """
    exiger(acces, Permission.QUALIFIER_PROSPECT)
    try:
        return servir_le_pdf_de_la_proforma(depot_proformas().lire(numero))
    except ProformaIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec


@routeur.post(
    "/proformas/{numero}/acceptation",
    summary="Le client accepte sa proforma par son lien signé",
    responses={
        404: {"description": "Proforma inconnue"},
        409: {"description": "Cette proforma ne peut plus être acceptée"},
        422: {"description": "Lien invalide, expiré, ou d'une autre version"},
    },
)
def accepter_la_proforma(
    numero: str, corps: DemandeDAcceptation, requete: Request
) -> Acceptee:
    """⚠️ **Publique, et elle doit l'être.**

    ─────────────────────────────────────────────────────────────────────────────
    Un client n'a pas de compte sur la plateforme, et lui en imposer un pour
    accepter un devis ferait perdre la moitié des acceptations. Le lien signé
    **est** l'authentification : scellé sur le numéro, la version et l'échéance,
    à usage unique.

    ⚠️ Le sceau est vérifié **avant** la proforma. Une vérification qui lirait
    d'abord le document permettrait de sonder l'existence d'un numéro sans posséder
    de lien, ce qui est précisément ce qu'on refuse ailleurs par des `404`.

    L'ORIGINE EST CONSERVÉE, ET ELLE NE SERT PAS AU CONTRÔLE D'ACCÈS

    Elle est notée pour le litige : « d'où est venue l'acceptation ». S'en servir
    pour autoriser reviendrait à faire d'une adresse réseau une identité, ce qu'elle
    n'est pas.
    ─────────────────────────────────────────────────────────────────────────────
    """
    lien = _lien_presente(numero, corps.version, corps.expire_le, corps.sceau)
    # ⚠️ **Le sceau d'abord, réellement** (pas 67). Ce docstring l'affirmait, et le code
    # lisait la proforma avant : un numéro inexistant répondait 404, un numéro existant
    # au sceau faux répondait 422. Les numéros étant séquentiels, n'importe qui
    # énumérait les proformas du cabinet sans posséder un seul lien. Le sceau porte sur
    # le numéro, la version et l'échéance : il se vérifie sans rien lire.
    try:
        lien.exiger_valide(maintenant(), secret=_secret_des_liens())
    except LienInvalide as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus

    proformas = depot_proformas()
    try:
        proforma = proformas.lire(numero)
    except ProformaIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    try:
        # ⚠️ Trois valeurs, et le lien en fait partie : il est rendu **employé**,
        # à usage unique. Le conserver permettrait de le rejouer, et une acceptation
        # rejouée est une seconde signature sur le même engagement.
        acceptee, _lien_employe, acceptation = accepter(
            proforma,
            lien,
            identite_declaree=corps.identite_declaree,
            a_l_instant=maintenant(),
            secret=_secret_des_liens(),
            origine=requete.client.host if requete.client else "",
        )
    except LienInvalide as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus
    except ValueError as refus:
        raise HTTPException(status_code=409, detail=message_lisible(refus)) from refus

    proformas.enregistrer(acceptee)
    # ⚠️ **Le dossier suit.** L'acceptation d'un client est le geste qui prouve
    # l'engagement : le laisser à `PROFORMA_ÉMISE` rendrait l'encaissement
    # impossible, et le dossier resterait éternellement en attente d'une réponse
    # qui est déjà arrivée.
    #
    # Une transition impossible est ignorée plutôt que levée : le client a bien
    # accepté, et lui rendre une erreur pour une raison d'état interne ferait
    # perdre une signature contractuelle.
    dossiers = depot_dossiers()
    try:
        dossier = dossiers.lire(acceptee.dossier)
        if dossier.etat is EtatDossier.PROFORMA_EMISE:
            dossiers.enregistrer(dossier.accepter(maintenant()))
    except (DossierIntrouvable, TransitionDossierRefusee):
        pass

    _accuser_l_acceptation(acceptee, acceptation)
    return Acceptee(
        numero=acceptation.proforma,
        version=acceptation.version,
        montant=acceptation.montant,
        acceptee_le=acceptation.acceptee_le,
        identite_declaree=acceptation.identite_declaree,
    )


def _accuser_l_acceptation(proforma, acceptation) -> None:
    """La trace écrite de ce que la cliente vient d'engager.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ ELLE ENGAGEAIT SON ENTREPRISE ET NE RECEVAIT RIEN.

    La page le dit en toutes lettres — « mon accord engage l'entreprise que je
    représente » — et le domaine le confirme : une proforma acceptée vaut
    contrat. Vérifié sur la pile le 29 septembre, en acceptant réellement : la
    boîte de la cliente est **inchangée** après le clic. Ni le montant accepté,
    ni la date, ni le nom déclaré. Le cabinet, lui, a tout.

    ⚠️ **IL NE LÈVE JAMAIS.** Le client a accepté ; l'accord est enregistré, le
    dossier a suivi, et rendre une erreur parce qu'un serveur de messagerie
    n'a pas répondu ferait **perdre une signature contractuelle** — c'est le
    raisonnement déjà tenu quinze lignes plus haut pour la transition du dossier.

    ⚠️ **SANS ADRESSE, RIEN**, et sans bruit : l'adresse est facultative au
    formulaire public, et le rappel se fait alors au téléphone.
    ─────────────────────────────────────────────────────────────────────────────
    """
    try:
        demande = depot_dossiers().lire(proforma.dossier).demande
        courriel = (demande.courriel or "").strip()
        if not courriel:
            return
        service_de_notification().envoyer(
            "proforma.acceptation",
            destinataire=courriel,
            contexte={
                "prenom": demande.nom,
                "numero": acceptation.proforma,
                "montant": f"{acceptation.montant:,.0f}".replace(",", "\u202f"),
                "acceptee_le": acceptation.acceptee_le.strftime("%d/%m/%Y à %Hh%M"),
                "identite": acceptation.identite_declaree,
            },
        )
    except Exception:  # noqa: BLE001 — voir l'en-tête : jamais au prix de l'accord
        _journal_acceptation.exception(
            "Accusé d'acceptation NON envoyé pour %s. L'accord EST enregistré ; "
            "seule la trace écrite manque au client.",
            acceptation.proforma,
        )




#: ⚠️ **L'unique magasin, importé et non redéclaré.** Voir `magasins_memoire` :
#: la route écrivait ici, le balayage de relance lisait ailleurs, et il ne
#: trouvait jamais rien à relancer.
_proformas_memoire = proformas_memoire


def _secret_des_liens() -> str:
    """Le secret qui scelle les liens d'acceptation.

    ⚠️ **La clé de chiffrement du cabinet, et non un secret dédié.** Un second
    secret serait un second réglage à poser, à conserver et à faire tourner, pour
    une garantie identique : les deux protègent des engagements du même cabinet.

    En production, `Configuration` refuse de démarrer sans clé. Hors production, le
    repli est explicite et **le sceau n'y protège rien** : c'est assumé, et le
    registre des services signale déjà l'absence de clé.
    """
    return configuration().cle_chiffrement or "secret-de-developpement"




