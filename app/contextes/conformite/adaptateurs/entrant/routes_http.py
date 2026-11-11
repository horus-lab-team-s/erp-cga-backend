"""API du contexte Conformité.

⚠️ 16 octobre 2026 : la construction des règles du cabinet et le journal des dérogations
sont dans `routes_regles_du_cabinet.py`, sous les mêmes chemins.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.conformite.adaptateurs.entrant.presentateur_verdict import (
    Verdict,
    composer_verdict,
)
from app.contextes.conformite.adaptateurs.sortant.factures_a_controler import (
    facture_par_reference,
    factures_a_controler,
)
from app.contextes.conformite.api import (
    EcartDeConstat,
    EcartIntrouvable,
    EcartRefuse,
    EtatDUnEcart,
    MotifInsuffisant,
    PolitiqueDEcart,
    StatutEcart,
    appliquer_les_ecarts,
    depot_des_ecarts,
    etat_des_ecarts,
    joindre_une_piece_d_appui,
    lever_un_ecart,
    moteur_par_defaut,
    politique_d_ecart,
    proposer_un_ecart,
    trancher_un_ecart,
)
from app.contextes.conformite.application.ecarts_en_masse import (
    ConsequencesDesEcarts,
    consequences_des_ecarts,
    ecarter_en_masse,
)
from app.contextes.conformite.application.moteur_conformite import MoteurConformite
from app.contextes.conformite.domaine.ecarts import MotifType, empreinte_du_constat
from app.contextes.conformite.domaine.entites import FactureAControler, RapportConformite, Regle
from app.contextes.conformite.domaine.qualite_des_regles import (
    mesurer_les_regles,
)
from app.contextes.transverse.api import (
    Acces,
    AccesRequis,
    Permission,
    atelier,
    exiger,
    exiger_dossier,
    restreindre,
)
from app.partage.horloge import aujourd_hui, maintenant

routeur = APIRouter(prefix="/conformite", tags=["Conformité"])


def moteur() -> MoteurConformite:
    """Le moteur monté sur le référentiel en vigueur.

    ⚠️ **Le montage a déménagé vers `api.py`**, et ces routes y passent comme tout
    le monde. Il vivait ici, et la comptabilité l'a recopié le jour où elle a eu
    besoin de contrôler une facture : deux montages auraient divergé au premier
    changement de dépôt, et deux écrans auraient rendu deux verdicts sur la même
    facture.
    """
    return moteur_par_defaut()


class ReponseControle(BaseModel):
    """Ce dont l'écran E02 a besoin, en un appel.

    La facture est renvoyée avec le rapport : la fiche § 8.2 impose d'afficher les
    données extraites — fournisseur, NIU, date, montants, mode de règlement — à côté
    des constats, et de pouvoir les corriger. Les séparer en deux appels obligerait
    l'écran à recoller deux états qui doivent rester cohérents.
    """

    facture: FactureAControler
    verdict: Verdict
    #: ⚠️ Pas 92 : le rapport **arbitré**. Les constats écartés par un écart effectif
    #: sont passés dans `rapport.constats_ecartes`, et le verdict est composé sur ce
    #: rapport-là. Afficher le verdict brut à côté d'un écart confirmé ferait lire
    #: « TVA rejetée » sur une TVA que le cabinet a jugée récupérable.
    rapport: RapportConformite
    #: Les écarts de la pièce et ce qu'ils produisent aujourd'hui. **Vide pour qui
    #: n'est pas du cabinet** : le motif d'un réviseur est une note interne.
    ecarts: list[EtatDUnEcart] = []


def _voit(acces: Acces | None):
    """Le dossier est-il dans le périmètre ? Sans accès connu, tous se valent."""
    if acces is None:
        return lambda _niu: True
    return lambda niu: niu is not None and acces.voit(niu)


def _destinataire(facture: FactureAControler) -> str | None:
    """Le NIU du dossier auquel une facture se rattache, s'il est connu.

    Sert de clé de périmètre : c'est le destinataire, jamais l'émetteur, qui dit
    de quel dossier relève une facture d'achat. Se tromper de côté ferait voir à
    un adhérent toutes les factures qu'il a **émises** chez les autres.
    """
    destinataire = facture.destinataire
    return destinataire.niu if destinataire is not None else None


def _reponse(acces: Acces, facture: FactureAControler, brut: RapportConformite) -> ReponseControle:
    """Compose la réponse d'un contrôle : rapport arbitré, verdict, écarts (pas 92).

    Un seul chemin pour les trois routes qui contrôlent : si l'une appliquait les
    écarts et l'autre non, la boîte de réception et le détail d'une pièce donneraient
    deux verdicts sur la même facture.

    Les écarts sont lus une fois, et servent aux deux usages : l'arbitrage du rapport
    et l'état affiché. Les relire pour chacun ouvrirait une fenêtre où un second regard
    donné entre-temps rendrait un état qui ne correspond pas au rapport.
    """
    dossier = _destinataire(facture)
    if dossier is None:
        return ReponseControle(facture=facture, verdict=composer_verdict(brut), rapport=brut)
    ecarts = depot_des_ecarts().pour_la_piece(dossier, brut.reference_document)
    if not ecarts:
        return ReponseControle(facture=facture, verdict=composer_verdict(brut), rapport=brut)
    politique = politique_d_ecart()
    arbitre = appliquer_les_ecarts(brut, ecarts, politique)
    return ReponseControle(
        facture=facture,
        verdict=composer_verdict(arbitre),
        rapport=arbitre,
        ecarts=etat_des_ecarts(brut, ecarts, politique) if acces.interne else [],
    )


@routeur.get("/regles", summary="Catalogue des règles de conformité")
def lister_regles(
    acces: AccesRequis,
    a_la_date: date | None = Query(
        None, description="Ne rend que les règles en vigueur à cette date"
    ),
) -> list[Regle]:
    """Le catalogue des règles, réservé à qui travaille sur un dossier.

    ─────────────────────────────────────────────────────────────────────────
    POURQUOI CE CATALOGUE N'EST PAS PUBLIC

    Les taux et seuils sont dans la loi : les publier ne révèle rien. Le
    **rulebook**, lui, est le produit. C'est la traduction du texte en contrôles
    exécutables, avec pour chaque anomalie sa conséquence chiffrée — c'est-à-dire
    exactement ce que le cabinet vend et ce qu'un concurrent recopierait en une
    après-midi.

    La règle générale que ce contexte a longtemps enfreinte : **une route sans
    dépendance d'accès est une route publique**, et le seul moyen de s'en
    apercevoir est de l'appeler sans témoin.
    ─────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.LIRE_DOSSIER)
    # ⚠️ PAS 90 : LA PROMESSE CI-DESSUS N'ÉTAIT PAS TENUE.
    #
    # `LIRE_DOSSIER` est détenue par l'adhérent, qui l'a sur son propre dossier. Essai :
    # l'adhérent de SARL BATIMENT PLUS lisait le catalogue entier, **prédicats exécutables
    # compris**, c'est-à-dire le rulebook que cette docstring refuse à un concurrent. Et
    # depuis la souscription en ligne (pas 83), n'importe qui peut devenir adhérent.
    #
    # La permission dit « lire un dossier » ; elle ne disait rien de « travailler au
    # cabinet ». C'est `interne` qui le dit, et le refus est un 403 : aucune donnée de
    # dossier n'est en jeu, et la table des rôles est publique.
    if not acces.interne:
        raise HTTPException(
            status_code=403,
            detail="le catalogue des règles de conformité est réservé au cabinet.",
        )
    regles = moteur().regles
    if a_la_date is not None:
        regles = [r for r in regles if r.en_vigueur(a_la_date)]
    return regles


@routeur.post("/controler", summary="Contrôler une facture")
def controler(
    acces: AccesRequis,
    facture: FactureAControler,
    a_la_date: date | None = Query(
        None,
        description=(
            "Date de contrôle. Par défaut la date d'émission de la facture : "
            "une facture de 2024 se contrôle avec les règles de 2024."
        ),
    ),
) -> ReponseControle:
    """Contrôle une facture — l'acte central du produit.

    Deux gardes, et elles ne disent pas la même chose. `CONTROLER_CONFORMITE`
    dit qui a le droit de faire tourner le moteur ; `exiger_dossier` dit sur
    quel dossier. Sans la seconde, un comptable habilité sur trois dossiers
    pourrait faire contrôler une facture du quatrième et lire, dans le rapport,
    des montants qui ne le regardent pas.

    Une facture sans destinataire connu échappe au second contrôle : elle
    n'appartient encore à aucun dossier. C'est le cas d'une pièce que l'on
    contrôle avant de savoir à qui la rattacher.
    """
    exiger(acces, Permission.CONTROLER_CONFORMITE)
    dossier = _destinataire(facture)
    if dossier is not None:
        exiger_dossier(acces, Permission.CONTROLER_CONFORMITE, dossier)
    return _reponse(acces, facture, moteur().controler(facture, a_la_date))


@routeur.get("/demonstration", summary="Références du jeu de démonstration")
def lister_demonstration(acces: AccesRequis) -> list[str]:
    """Les références visibles depuis le périmètre de l'appelant.

    Le jeu est fictif, mais la liste ne l'est pas : elle dit quelles factures
    existent pour quel dossier. Rendue en entier, elle apprend à un adhérent
    combien de pièces portent les dossiers de ses voisins.
    """
    exiger(acces, Permission.LIRE_PIECE)
    visibles = restreindre(acces, factures_a_controler(), _destinataire)
    return sorted(facture.document.reference for facture in visibles)


@routeur.get(
    "/demonstration/rapports",
    summary="Contrôler tout le flux entrant de démonstration",
    description=(
        "Rend le contrôle de chaque facture du jeu de démonstration. La boîte de réception "
        "affiche la pastille de conformité sur chaque ligne : la peupler par appels unitaires "
        "coûterait un aller-retour par ligne, sur des connexions où chacun se paie."
    ),
)
def controler_tout(acces: AccesRequis) -> list[ReponseControle]:
    """La boîte de réception, **restreinte au périmètre de l'appelant**.

    ─────────────────────────────────────────────────────────────────────────
    CE QUE CETTE ROUTE ALIMENTE, ET POURQUOI ELLE ÉTAIT DANGEREUSE

    C'est elle qui peuple l'écran E03. Tant qu'elle n'exigeait aucun accès, la
    boîte de réception montrait les six sociétés du portefeuille à **n'importe
    quel** porteur de session — un adhérent habilité à un seul dossier
    compris. Le jeu était fictif ; la liste de raisons sociales, non.

    `restreindre` et non `exiger` : une liste se restreint, une lecture unitaire
    se refuse. Refuser toute la boîte parce qu'une ligne sort du périmètre la
    rendrait vide pour tout le monde.
    ─────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.LIRE_PIECE)
    moteur_ = moteur()
    rapports = []
    for facture in restreindre(acces, factures_a_controler(), _destinataire):
        rapports.append(_reponse(acces, facture, moteur_.controler(facture)))
    return rapports


# Déclaré APRÈS /demonstration/rapports : sinon « rapports » serait capturé comme
# une référence de facture.
@routeur.get("/demonstration/{reference}", summary="Contrôler une facture de démonstration")
def controler_demonstration(acces: AccesRequis, reference: str) -> ReponseControle:
    """Une facture de démonstration, si elle relève d'un dossier de l'appelant.

    Le 404 ne liste plus les références disponibles : cette liste servait au
    diagnostic et renseignait, en passant, sur les pièces des autres dossiers.
    Un hors-périmètre et une référence inexistante rendent désormais la même
    réponse — c'est ce qui empêche de sonder l'existence d'un dossier.
    """
    exiger(acces, Permission.LIRE_PIECE)
    facture = facture_par_reference(reference, voit=_voit(acces))
    if facture is None:
        raise HTTPException(status_code=404, detail=f"facture « {reference} » inconnue.")
    dossier = _destinataire(facture)
    if dossier is not None:
        # ⚠️ Pas 88 : la docstring le promettait, et c'était faux. Le refus hors
        # périmètre nommait le NIU du destinataire. Voir `exiger_dossier`.
        exiger_dossier(
            acces, Permission.LIRE_PIECE, dossier, introuvable=f"facture « {reference} » inconnue."
        )
    return _reponse(acces, facture, moteur().controler(facture))


def reserver_au_cabinet(acces: Acces) -> None:
    if not acces.interne:
        raise HTTPException(
            status_code=403, detail="le constructeur de règles est réservé au cabinet."
        )


# ── Écarter un constat (pas 92) ──────────────────────────────────────────────
#
# ⚠️ LE GESTE LE PLUS DÉLICAT DE LA CONFORMITÉ
#
# Tout le produit repose sur ce qu'un contrôle ne se contourne pas. Écarter un constat
# est le seul contournement légitime, et c'est pourquoi il est encadré à trois niveaux :
#
# * la **permission** `ECARTER_CONSTAT`, détenue par le seul réviseur, avec motif écrit
#   (`EXIGE_MOTIF`) ;
# * la **politique du cabinet**, lue au référentiel (`ecarts/politique.yaml`) : ce qui
#   est écartable, ce qui exige un second regard, et qui peut le donner ;
# * le **journal d'audit**, qui nomme l'auteur de chaque décision.
#
# Les pièces sont celles de `factures_a_controler` (20 octobre 2026) : les factures
# réelles lues et confirmées, et le jeu de démonstration hors production.

_INTROUVABLE = "pièce « {reference} » inconnue."


def _facture_de_la_piece(reference: str, acces: Acces | None = None) -> FactureAControler:
    facture = facture_par_reference(reference, voit=_voit(acces))
    if facture is None:
        raise HTTPException(status_code=404, detail=_INTROUVABLE.format(reference=reference))
    return facture


def _dossier_de_la_facture(facture: FactureAControler, reference: str) -> str:
    """Le dossier de la pièce. **Le périmètre reste à contrôler par la route.**

    Une facture sans destinataire n'appartient à aucun dossier : elle ne peut porter
    aucun écart (voir `rapport_arbitre`). 404 et non 409, avec le même texte qu'une
    pièce inconnue : un écart n'y a pas de sens, et la distinction n'apprendrait rien.

    ⚠️ Le contrôle du périmètre (`exiger_dossier`) est écrit dans chaque route, et non
    caché ici : c'est dans le corps de la route qu'un relecteur le cherche, et que le
    test de surface des permissions le vérifie.
    """
    dossier = _destinataire(facture)
    if dossier is None:
        raise HTTPException(status_code=404, detail=_INTROUVABLE.format(reference=reference))
    return dossier


class DemandeDEcart(BaseModel):
    """Le constat à écarter, désigné par sa règle, et la raison."""

    model_config = ConfigDict(extra="forbid")

    code_regle: str = Field(min_length=1, max_length=64)
    #: La longueur minimale vraie est celle de la politique, contrôlée au domaine. Le
    #: plancher ici (10) n'est qu'un garde-fou de forme, commun à tous les motifs.
    motif: str = Field(min_length=10, max_length=2000)
    #: Pas 118 : le document qui prouve le motif, s'il est déjà au dossier. Facultatif ici, exigible
    #: ensuite selon la politique : la pièce arrive souvent après la décision (le fournisseur
    #: envoie son attestation le lendemain).
    piece_appui: str | None = Field(default=None, max_length=120)


class DecisionDuSecondRegard(StrEnum):
    CONFIRMER = "CONFIRMER"
    REFUSER = "REFUSER"


class DemandeDeSecondRegard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: DecisionDuSecondRegard
    motif: str = Field(min_length=10, max_length=2000)


class DemandeDeLevee(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motif: str = Field(min_length=10, max_length=2000)


def _traduire(refus: Exception) -> HTTPException:
    """Les refus du domaine, en statuts.

    * motif trop court pour la politique : 422, la demande est incomplète ;
    * refus de la politique ou de l'état : 409, la demande est bien formée et c'est
      la situation qui s'y oppose ;
    * écart inconnu sur cette pièce : 404.
    """
    if isinstance(refus, MotifInsuffisant):
        return HTTPException(status_code=422, detail=str(refus))
    if isinstance(refus, EcartIntrouvable):
        return HTTPException(status_code=404, detail=str(refus))
    return HTTPException(status_code=409, detail=str(refus))


def _journaliser(acces: Acces, action: str, ecart: EcartDeConstat, motif: str,
                 avant: str | None) -> None:
    """Une entrée d'audit par décision, qui nomme l'auteur.

    Le motif figure dans l'entrée **et** dans l'écart. Le journal est chaîné et ne se
    réécrit pas ; l'écart est un document qui évolue. Si les deux divergeaient un jour,
    c'est le journal qui ferait foi.
    """
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action=action,
        objet_type="ecart_de_constat",
        objet_id=ecart.identifiant,
        avant=None if avant is None else {"statut": avant},
        apres={
            "statut": ecart.statut.value,
            "dossier": ecart.dossier,
            "piece": ecart.reference_document,
            "regle": ecart.code_regle,
            "severite": ecart.severite.value,
            # Pas 103 : le motif type, s'il y en a un. Le motif détaillé reste dans `motif`.
            "motif_type": ecart.motif_type,
        },
        motif=motif,
    )


@routeur.get(
    "/ecarts/politique",
    summary="La politique d'écart des constats en vigueur",
    description=(
        "Pas 92. Ce que le cabinet permet d'écarter, ce qui exige un second regard et qui "
        "peut le donner, lu au référentiel. Sans fichier, la politique prudente : rien "
        "n'est écartable."
    ),
)
def lire_la_politique_d_ecart(acces: AccesRequis) -> PolitiqueDEcart:
    """Réservée au cabinet, comme le catalogue des règles : elle dit quels constats un
    adhérent pourrait espérer voir écartés, et ce n'est pas à lui d'en juger."""
    exiger(acces, Permission.LIRE_PIECE)
    if not acces.interne:
        raise HTTPException(
            status_code=403, detail="la politique d'écart est réservée au cabinet."
        )
    return politique_d_ecart()


def _permission_du_second_regard(acces: Acces, politique: PolitiqueDEcart) -> Permission:
    """La première permission de la politique que la session détient, ou 403.

    Un nom inconnu de la table des rôles est ignoré ici, et refusé par le test
    d'intégrité du référentiel : ignoré en exécution, parce qu'une coquille ne doit
    pas ouvrir le second regard à tout le monde ; refusé en test, parce qu'elle
    le fermerait en silence à ceux que le cabinet voulait désigner.
    """
    for nom in politique.permissions_du_second_regard:
        if nom in Permission.__members__ and acces.detient(Permission(nom)):
            return Permission(nom)
    raise HTTPException(
        status_code=403,
        detail="le second regard sur un écart est réservé aux personnes que la "
        "politique du cabinet désigne.",
    )


@routeur.get(
    "/ecarts/en-attente",
    summary="Les écarts qui attendent un second regard",
    description="Pas 92. Restreinte au périmètre de l'appelant, du plus ancien au plus récent.",
)
def lister_les_ecarts_en_attente(acces: AccesRequis) -> list[EcartDeConstat]:
    _permission_du_second_regard(acces, politique_d_ecart())
    return list(restreindre(acces, depot_des_ecarts().en_attente(), lambda e: e.dossier))


@routeur.post(
    "/pieces/{reference}/ecarts",
    summary="Écarter un constat du rapport d'une pièce",
    responses={
        404: {"description": "Pièce inconnue ou hors périmètre"},
        409: {"description": "Constat absent, non écartable, ou déjà écarté"},
        422: {"description": "Motif trop court pour la politique du cabinet"},
    },
)
def ecarter_un_constat(
    acces: AccesRequis, reference: str, demande: DemandeDEcart
) -> EtatDUnEcart:
    """Propose l'écart. Effectif tout de suite, ou en attente d'un second regard.

    Le rapport est **recalculé** ici, et non repris de l'écran : c'est le constat que
    le moteur produit maintenant qui est écarté, avec son enjeu de maintenant.
    """
    exiger(acces, Permission.ECARTER_CONSTAT, motif=demande.motif)
    facture = _facture_de_la_piece(reference, acces)
    dossier = _dossier_de_la_facture(facture, reference)
    exiger_dossier(
        acces, Permission.ECARTER_CONSTAT, dossier, motif=demande.motif,
        introuvable=_INTROUVABLE.format(reference=reference),
    )
    brut = moteur().controler(facture)
    politique = politique_d_ecart()
    depot = depot_des_ecarts()
    try:
        ecart = proposer_un_ecart(
            brut,
            dossier=dossier,
            code_regle=demande.code_regle,
            motif=demande.motif,
            par=acces.compte,
            le=maintenant(),
            politique=politique,
            depot=depot,
        )
    except EcartRefuse as refus:
        raise _traduire(refus) from refus
    _journaliser(acces, "conformite.ecart_propose", ecart, demande.motif, avant=None)
    if demande.piece_appui:
        try:
            ecart = joindre_une_piece_d_appui(
                dossier=dossier,
                reference_document=reference,
                identifiant=ecart.identifiant,
                piece=demande.piece_appui,
                par=acces.compte,
                le=maintenant(),
                depot=depot,
            )
        except (EcartRefuse, EcartIntrouvable) as refus:
            raise _traduire(refus) from refus
        _journaliser(acces, "conformite.piece_d_appui_jointe", ecart, demande.motif, avant=None)
    return etat_des_ecarts(brut, [ecart], politique)[0]


class DemandeDePieceDAppui(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: L'identifiant d'une pièce du dossier (« PJ-2026-0042 ») ou la référence du document conservé.
    piece: str = Field(min_length=3, max_length=120)


@routeur.post(
    "/pieces/{reference}/ecarts/{identifiant}/piece-appui",
    summary="Joindre la pièce d'appui d'une dérogation",
    description=(
        "Le motif dit ce que le cabinet a vérifié ; la pièce d'appui est ce qu'il montrera au "
        "vérificateur. Elle se joint avant ou après le second regard, jamais sur un écart levé ou "
        "refusé, et se remplace (le journal garde les deux gestes)."
    ),
    responses={
        404: {"description": "Pièce ou écart inconnu, ou hors périmètre"},
        409: {"description": "Écart levé ou refusé : il n'a plus d'effet"},
    },
)
def joindre_la_piece_d_appui(
    acces: AccesRequis, reference: str, identifiant: str, demande: DemandeDePieceDAppui
) -> EtatDUnEcart:
    # ⚠️ Le contrôle de surface, en première ligne, **en plus** du contrôle par dossier : l'outil de
    # relecture des permissions lit le corps de la route. Les deux disent la même chose ici (mutant
    # équivalent au pas 118), et c'est la convention du projet : un contrôle caché est un contrôle
    # qu'on oublie de porter sur la route suivante.
    exiger(acces, Permission.ECARTER_CONSTAT, motif=f"pièce d'appui {demande.piece}")
    facture = _facture_de_la_piece(reference, acces)
    dossier = _dossier_de_la_facture(facture, reference)
    exiger_dossier(
        acces, Permission.ECARTER_CONSTAT, dossier, motif=f"pièce d'appui {demande.piece}",
        introuvable=_INTROUVABLE.format(reference=reference),
    )
    try:
        ecart = joindre_une_piece_d_appui(
            dossier=dossier,
            reference_document=reference,
            identifiant=identifiant,
            piece=demande.piece,
            par=acces.compte,
            le=maintenant(),
            depot=depot_des_ecarts(),
        )
    except (EcartRefuse, EcartIntrouvable) as refus:
        raise _traduire(refus) from refus
    _journaliser(acces, "conformite.piece_d_appui_jointe", ecart, demande.piece, avant=None)
    return etat_des_ecarts(moteur().controler(facture), [ecart], politique_d_ecart())[0]


@routeur.post(
    "/pieces/{reference}/ecarts/{identifiant}/second-regard",
    summary="Confirmer ou refuser un écart en attente",
    responses={
        403: {"description": "La politique ne désigne pas l'appelant"},
        404: {"description": "Pièce ou écart inconnu, ou hors périmètre"},
        409: {"description": "Écart qui n'attend pas, ou proposé par l'appelant"},
        422: {"description": "Motif trop court pour la politique du cabinet"},
    },
)
def donner_le_second_regard(
    acces: AccesRequis, reference: str, identifiant: str, demande: DemandeDeSecondRegard
) -> EtatDUnEcart:
    politique = politique_d_ecart()
    permission = _permission_du_second_regard(acces, politique)
    facture = _facture_de_la_piece(reference, acces)
    # ⚠️ Le motif est transmis : `ECARTER_CONSTAT` figure dans `EXIGE_MOTIF`, et sans
    # lui le réviseur désigné par la politique recevait un 403 au lieu de trancher.
    dossier = _dossier_de_la_facture(facture, reference)
    exiger_dossier(
        acces, permission, dossier, motif=demande.motif,
        introuvable=_INTROUVABLE.format(reference=reference),
    )
    depot = depot_des_ecarts()
    try:
        ecart = trancher_un_ecart(
            dossier=dossier,
            reference_document=reference,
            identifiant=identifiant,
            confirme=demande.decision is DecisionDuSecondRegard.CONFIRMER,
            motif=demande.motif,
            par=acces.compte,
            le=maintenant(),
            politique=politique,
            depot=depot,
        )
    except (EcartRefuse, EcartIntrouvable) as refus:
        raise _traduire(refus) from refus
    action = (
        "conformite.ecart_confirme" if ecart.statut is StatutEcart.EFFECTIF
        else "conformite.ecart_refuse"
    )
    _journaliser(acces, action, ecart, demande.motif, avant="EN_ATTENTE")
    return etat_des_ecarts(moteur().controler(facture), [ecart], politique)[0]


@routeur.post(
    "/pieces/{reference}/ecarts/{identifiant}/levee",
    summary="Lever un écart : le constat compte de nouveau",
    responses={
        404: {"description": "Pièce ou écart inconnu, ou hors périmètre"},
        409: {"description": "Écart déjà refusé ou levé"},
        422: {"description": "Motif trop court pour la politique du cabinet"},
    },
)
def lever_l_ecart(
    acces: AccesRequis, reference: str, identifiant: str, demande: DemandeDeLevee
) -> EtatDUnEcart:
    exiger(acces, Permission.ECARTER_CONSTAT, motif=demande.motif)
    facture = _facture_de_la_piece(reference, acces)
    dossier = _dossier_de_la_facture(facture, reference)
    exiger_dossier(
        acces, Permission.ECARTER_CONSTAT, dossier, motif=demande.motif,
        introuvable=_INTROUVABLE.format(reference=reference),
    )
    politique = politique_d_ecart()
    depot = depot_des_ecarts()
    avant = next(
        (e.statut.value for e in depot.pour_la_piece(dossier, reference)
         if e.identifiant == identifiant),
        None,
    )
    try:
        ecart = lever_un_ecart(
            dossier=dossier,
            reference_document=reference,
            identifiant=identifiant,
            motif=demande.motif,
            par=acces.compte,
            le=maintenant(),
            politique=politique,
            depot=depot,
        )
    except (EcartRefuse, EcartIntrouvable) as refus:
        raise _traduire(refus) from refus
    _journaliser(acces, "conformite.ecart_leve", ecart, demande.motif, avant=avant)
    return etat_des_ecarts(moteur().controler(facture), [ecart], politique)[0]


# ── Écarter en masse (pas 103) ───────────────────────────────────────────────
#
# ─────────────────────────────────────────────────────────────────────────────
# LA VUE D'UNE RÈGLE, PUIS LA DÉCISION
#
# `GET /regles/{code}/constats` rend, pour une règle et une période, tous ses constats
# sur les pièces du périmètre : fournisseur, vérification DGI, enjeu, et si le constat
# peut être choisi (la politique le permet-elle, un écart est-il déjà ouvert ?). Avec les
# chiffres de la maquette : constats, enjeu cumulé, adhérents concernés, taux
# d'écartement de la règle.
#
# `POST /regles/{code}/ecarts` écarte les pièces choisies, **tout ou rien**, avec un motif
# type facultatif et un motif détaillé obligatoire. Chaque écart est un écart ordinaire :
# même politique, même journal, mêmes notifications (les comptables des dossiers dont un
# constat est effectivement écarté sont prévenus, voir `abonnements.yaml`).
# ─────────────────────────────────────────────────────────────────────────────


class LigneDeConstat(BaseModel):
    piece: str
    dossier: str
    date: date
    fournisseur: str | None
    fournisseur_niu: str | None
    #: La vérification au fichier DGI : vrai (actif), faux (radié), absent (indisponible).
    verification_dgi: bool | None
    enjeu: Decimal | None
    message: str
    #: L'écart ouvert sur ce constat, s'il y en a un.
    ecart: str | None = None
    statut_ecart: StatutEcart | None = None
    selectionnable: bool
    raison: str | None = None


class VueDEcartEnMasse(BaseModel):
    code: str
    libelle: str
    severite: str
    du: date
    au: date
    ecartable: bool
    second_regard: bool
    motif_minimum: int
    source_de_la_politique: str
    motifs_types: list[MotifType]
    constats: int
    enjeu_cumule: Decimal
    adherents: int
    #: Écartés sur constats, pour la période (comme la qualité des règles). `None` sans constat.
    taux_d_ecartement: float | None
    lignes: list[LigneDeConstat]


def _periode(du: date | None, au: date | None) -> tuple[date, date]:
    fin = au or aujourd_hui()
    debut = du or fin - timedelta(days=90)
    if debut > fin:
        raise HTTPException(status_code=422, detail="la période commence après sa fin.")
    return debut, fin


@routeur.get(
    "/regles/{code}/constats",
    summary="Les constats d'une règle sur le périmètre, pour les écarter en masse",
    responses={404: {"description": "Règle inconnue"}},
)
def constats_de_la_regle(
    acces: AccesRequis,
    code: str,
    du: date | None = Query(None, description="Par défaut : 90 jours avant « au »"),
    au: date | None = Query(None, description="Par défaut : aujourd'hui"),
) -> VueDEcartEnMasse:
    exiger(acces, Permission.CONTROLER_CONFORMITE)
    debut, fin = _periode(du, au)
    moteur_ = moteur()
    regle = next((r for r in moteur_.regles if r.code == code), None)
    if regle is None:
        raise HTTPException(status_code=404, detail=f"règle « {code} » inconnue.")
    politique = politique_d_ecart()
    depot = depot_des_ecarts()
    lignes: list[LigneDeConstat] = []
    controles = []
    for facture in restreindre(acces, factures_a_controler(), _destinataire):
        dossier = _destinataire(facture)
        if dossier is None or not (debut <= facture.document.date_emission <= fin):
            continue
        brut = moteur_.controler(facture)
        ecarts = depot.pour_la_piece(dossier, brut.reference_document)
        controles.append((brut, appliquer_les_ecarts(brut, ecarts, politique)))
        constat = next((c for c in brut.constats if c.code_regle == code), None)
        if constat is None:
            continue
        empreinte = empreinte_du_constat(constat)
        ouvert = next(
            (
                e
                for e in reversed(ecarts)
                if e.code_regle == code and e.ouvert and e.empreinte == empreinte
            ),
            None,
        )
        regle_d_ecart = politique.regle_pour(constat)
        raison = (
            f"non écartable selon la politique du cabinet ({constat.severite.value})"
            if not regle_d_ecart.ecartable
            else f"écart déjà {ouvert.statut.value.lower().replace('_', ' ')}"
            if ouvert
            else None
        )
        lignes.append(
            LigneDeConstat(
                piece=brut.reference_document,
                dossier=dossier,
                date=facture.document.date_emission,
                fournisseur=facture.emetteur.denomination,
                fournisseur_niu=facture.emetteur.niu,
                verification_dgi=facture.emetteur.niu_actif,
                enjeu=constat.enjeu,
                message=constat.message,
                ecart=ouvert.identifiant if ouvert else None,
                statut_ecart=ouvert.statut if ouvert else None,
                selectionnable=raison is None,
                raison=raison,
            )
        )
    statistique = next(
        (s for s in mesurer_les_regles([regle], controles, politique.revue_des_regles)), None
    )
    lignes.sort(key=lambda l_: (not l_.selectionnable, -(l_.enjeu or 0), l_.piece))
    regle_ecart = politique.par_severite.get(regle.severite)
    ecartable = (
        code not in politique.regles_non_ecartables
        and regle_ecart is not None
        and regle_ecart.ecartable
    )
    return VueDEcartEnMasse(
        code=regle.code,
        libelle=regle.libelle,
        severite=regle.severite.value,
        du=debut,
        au=fin,
        ecartable=ecartable,
        second_regard=bool(regle_ecart and regle_ecart.second_regard),
        motif_minimum=politique.motif_minimum,
        source_de_la_politique=politique.source,
        motifs_types=list(politique.motifs_pour(code)),
        constats=len(lignes),
        enjeu_cumule=sum((l_.enjeu or Decimal(0) for l_ in lignes), Decimal(0)),
        adherents=len({l_.dossier for l_ in lignes}),
        taux_d_ecartement=None
        if statistique is None or statistique.constats == 0
        else round(statistique.ecartes / statistique.constats, 4),
        lignes=lignes,
    )


class DemandeDEcartEnMasse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pieces: list[str] = Field(min_length=1, max_length=200)
    motif_type: str | None = Field(None, max_length=40)
    motif: str = Field(min_length=10, max_length=2000)


class ResultatDEcartEnMasse(BaseModel):
    ecarts: list[EcartDeConstat]
    consequences: ConsequencesDesEcarts


@routeur.post(
    "/regles/{code}/ecarts",
    summary="Écarter en une décision les constats d'une règle sur plusieurs pièces",
    responses={
        404: {"description": "Règle ou pièce inconnue, ou hors périmètre"},
        409: {"description": "Au moins un constat ne peut pas être écarté : aucun ne l'est"},
        422: {"description": "Motif trop court pour la politique du cabinet"},
    },
)
def ecarter_des_constats_en_masse(
    acces: AccesRequis, code: str, demande: DemandeDEcartEnMasse
) -> ResultatDEcartEnMasse:
    exiger(acces, Permission.ECARTER_CONSTAT, motif=demande.motif)
    if not any(r.code == code for r in moteur().regles):
        raise HTTPException(status_code=404, detail=f"règle « {code} » inconnue.")
    pieces = []
    for reference in demande.pieces:
        facture = _facture_de_la_piece(reference, acces)
        dossier = _dossier_de_la_facture(facture, reference)
        # ⚠️ Toute pièce hors périmètre fait échouer la demande entière, en 404 : on ne
        # dit pas laquelle existe ailleurs.
        exiger_dossier(
            acces, Permission.ECARTER_CONSTAT, dossier, motif=demande.motif,
            introuvable=_INTROUVABLE.format(reference=reference),
        )
        pieces.append((dossier, moteur().controler(facture)))
    politique = politique_d_ecart()
    depot = depot_des_ecarts()
    try:
        ecarts = ecarter_en_masse(
            pieces,
            code_regle=code,
            motif=demande.motif,
            motif_type=demande.motif_type,
            par=acces.compte,
            le=maintenant(),
            politique=politique,
            depot=depot,
        )
    except EcartRefuse as refus:
        raise _traduire(refus) from refus
    for ecart in ecarts:
        _journaliser(acces, "conformite.ecart_propose", ecart, demande.motif, avant=None)
    return ResultatDEcartEnMasse(
        ecarts=ecarts,
        consequences=consequences_des_ecarts(pieces, ecarts, politique, depot),
    )


class DemandeDeSignalement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motif: str = Field(min_length=10, max_length=2000)


class SignalementDeRegle(BaseModel):
    regle: str
    signale_par: str
    motif: str


@routeur.post(
    "/regles/{code}/signalement",
    summary="Signaler une règle au fiscaliste",
    responses={404: {"description": "Règle inconnue"}},
)
def signaler_une_regle(
    acces: AccesRequis, code: str, demande: DemandeDeSignalement
) -> SignalementDeRegle:
    """Le réviseur signale une règle bruyante ; le fiscaliste est notifié et la corrige au
    constructeur. Rien n'est stocké hors du journal d'audit : le signalement est un fait, et
    la notification le lit là (pas 94)."""
    exiger(acces, Permission.CONTROLER_CONFORMITE)
    reserver_au_cabinet(acces)
    regle = next((r for r in moteur().regles if r.code == code), None)
    if regle is None:
        raise HTTPException(status_code=404, detail=f"règle « {code} » inconnue.")
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action="conformite.regle_signalee",
        objet_type="regle",
        objet_id=code,
        apres={"regle": code, "libelle": regle.libelle, "par": acces.nom_complet},
        motif=demande.motif.strip(),
    )
    return SignalementDeRegle(
        regle=code, signale_par=acces.nom_complet, motif=demande.motif.strip()
    )
