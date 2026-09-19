"""API du contexte Conformité."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.conformite.adaptateurs.entrant.presentateur_verdict import (
    Verdict,
    composer_verdict,
)
from app.contextes.conformite.adaptateurs.sortant.depots_regles_du_cabinet import (
    depot_des_regles_du_cabinet,
)
from app.contextes.conformite.adaptateurs.sortant.donnees_demo import FACTURES_DEMO
from app.contextes.conformite.api import (
    EcartDeConstat,
    EcartIntrouvable,
    EcartRefuse,
    EtatDUnEcart,
    JournalDesDerogations,
    MotifInsuffisant,
    PolitiqueDEcart,
    QualiteDesRegles,
    StatutEcart,
    appliquer_les_ecarts,
    depot_des_ecarts,
    derogations_du_perimetre,
    etat_des_ecarts,
    joindre_une_piece_d_appui,
    lever_un_ecart,
    lire_le_journal_des_derogations,
    mesurer_la_qualite_des_regles,
    moteur_par_defaut,
    noms_des_comptes,
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
from app.contextes.conformite.application.regles_du_cabinet import (
    ConstructionDeRegle,
    HorsDuCircuitDesRegles,
    construire,
    eprouver,
    proposer_une_regle,
    retirer_une_regle,
    trancher_une_regle,
)
from app.contextes.conformite.domaine.constructeur import (
    ConstructionRefusee,
    decrire,
    faits_constructibles,
    operateurs_pour,
)
from app.contextes.conformite.domaine.ecarts import MotifType, empreinte_du_constat
from app.contextes.conformite.domaine.entites import FactureAControler, RapportConformite, Regle
from app.contextes.conformite.domaine.qualite_des_regles import (
    mesurer_les_regles,
)
from app.contextes.conformite.domaine.regles_du_cabinet import (
    PropositionDeRegle,
    PropositionRefusee,
    StatutProposition,
)
from app.contextes.conformite.domaine.schema_faits import SCHEMA_FACTURE
from app.contextes.transverse.api import (
    Acces,
    AccesRequis,
    Permission,
    atelier,
    exiger,
    exiger_dossier,
    restreindre,
)
from app.partage.erreurs import message_lisible
from app.partage.horloge import maintenant

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
    visibles = restreindre(acces, FACTURES_DEMO.values(), _destinataire)
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
    for facture in restreindre(acces, FACTURES_DEMO.values(), _destinataire):
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
    facture = FACTURES_DEMO.get(reference)
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
# Les pièces sont aujourd'hui celles du jeu de démonstration, comme pour
# `/demonstration/{reference}`. Le jour où les pièces réelles porteront leur facture,
# seule `_facture_de_la_piece` changera.

_INTROUVABLE = "pièce « {reference} » inconnue."


def _facture_de_la_piece(reference: str) -> FactureAControler:
    facture = FACTURES_DEMO.get(reference)
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
    facture = _facture_de_la_piece(reference)
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
    facture = _facture_de_la_piece(reference)
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
    facture = _facture_de_la_piece(reference)
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
    facture = _facture_de_la_piece(reference)
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


# ── Construire une règle sans syntaxe (pas 97) ───────────────────────────────
#
# ⚠️ LE GESTE LE PLUS LOURD DU PRODUIT, ET POURQUOI IL EST ENCADRÉ AINSI
#
# Une règle validée ici contrôle **toutes** les factures du cabinet au contrôle suivant.
# Trois garde-fous se cumulent : le constructeur ne laisse écrire aucune syntaxe (il pose
# lui-même la négation et vérifie chaque condition au schéma et au référentiel), la règle
# est **éprouvée** sur les factures avant d'être proposée, et une autre personne la valide
# selon le circuit du cabinet (`validation/circuit.yaml`, entrée `regles`).


class FaitDuCatalogue(BaseModel):
    code: str
    libelle: str
    type: str
    unite: str | None
    valeurs: list[str] | None
    operateurs: list[str]


class ParametreDuCatalogue(BaseModel):
    code: str
    libelle: str
    unite: str


class CatalogueDuConstructeur(BaseModel):
    faits: list[FaitDuCatalogue]
    parametres: list[ParametreDuCatalogue]
    #: Ce que le constructeur ne sait pas exprimer, dit en clair.
    limites: str
    peut_proposer: bool
    peut_valider: bool
    quatre_yeux: bool
    motif_minimum: int


def _reserver_au_cabinet(acces: Acces) -> None:
    if not acces.interne:
        raise HTTPException(
            status_code=403, detail="le constructeur de règles est réservé au cabinet."
        )


def _unites_des_parametres() -> dict[str, str]:
    from app.contextes.referentiel.api import parametres_du_cabinet

    return {p.code: p.unite.value for p in parametres_du_cabinet()}


def _controler_avec(regle: Regle, facture: FactureAControler) -> RapportConformite:
    """Le moteur du cabinet réduit à **cette seule règle**, pour l'essai."""
    from app.contextes.referentiel.api import service_parametres

    return MoteurConformite(regles=[regle], parametres=service_parametres()).controler(facture)


@routeur.get("/constructeur/catalogue", summary="Ce qu'une règle construite peut lire et comparer")
def catalogue_du_constructeur(acces: AccesRequis) -> CatalogueDuConstructeur:
    from app.contextes.referentiel.api import circuit_de_validation, parametres_du_cabinet

    exiger(acces, Permission.LIRE_DOSSIER)
    _reserver_au_cabinet(acces)
    circuit = circuit_de_validation()
    detenues = {p.value for p in acces.permissions}
    return CatalogueDuConstructeur(
        faits=[
            FaitDuCatalogue(
                code=f.code,
                libelle=f.libelle,
                type=f.type.value,
                unite=f.unite,
                valeurs=list(f.valeurs) if f.valeurs else None,
                operateurs=[o.value for o in operateurs_pour(f)],
            )
            for f in faits_constructibles(SCHEMA_FACTURE)
        ],
        parametres=[
            ParametreDuCatalogue(code=p.code, libelle=p.libelle, unite=p.unite.value)
            for p in parametres_du_cabinet()
        ],
        limites=(
            "Les lignes de détail d'une facture ne sont pas proposées : une condition sur « une "
            "ligne » est ambiguë. Ces règles restent écrites en fichier et relues en revue."
        ),
        peut_proposer=bool(detenues.intersection(circuit.regles.proposer)),
        peut_valider=bool(detenues.intersection(circuit.regles.valider)),
        quatre_yeux=circuit.regles.quatre_yeux,
        motif_minimum=circuit.motif_minimum,
    )


class EssaiDeConstruction(BaseModel):
    phrase: str
    predicat: dict
    eprouvees: int
    reagit_sur: list[str]
    reagit_partout: bool


@routeur.post(
    "/constructeur/essai",
    summary="Éprouver une règle construite, sans rien enregistrer",
    responses={422: {"description": "Condition impossible à traduire"}},
)
def eprouver_une_construction(
    acces: AccesRequis, construction: ConstructionDeRegle
) -> EssaiDeConstruction:
    """La phrase, le prédicat produit et les factures sur lesquelles la règle réagirait.

    Rien n'est enregistré, et la règle n'entre dans aucun contrôle : c'est un essai.
    """
    exiger(acces, Permission.CONTROLER_CONFORMITE)
    _reserver_au_cabinet(acces)
    try:
        regle = construire(
            construction, code="CAB-ESSAI", unite_du_parametre=_unites_des_parametres()
        )
    except (ConstructionRefusee, ValueError) as refus:
        # `construire` valide une `Regle` : une ValidationError pydantic rendrait sa trace brute.
        raise HTTPException(status_code=422, detail=message_lisible(refus)) from refus
    essai = eprouver(regle, FACTURES_DEMO.values(), _controler_avec)
    return EssaiDeConstruction(
        phrase=decrire(construction.conditions, construction.combinaison, SCHEMA_FACTURE),
        predicat=regle.predicat,
        eprouvees=essai.eprouvees,
        reagit_sur=essai.reagit_sur,
        reagit_partout=essai.reagit_partout,
    )


class DemandeDeRegle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    construction: ConstructionDeRegle
    motif: str = Field(min_length=10, max_length=2000)


def _journaliser_la_regle(acces: Acces, action: str, proposition: PropositionDeRegle) -> None:
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action=action,
        objet_type="regle",
        objet_id=proposition.regle.code,
        apres={
            "proposition": proposition.identifiant,
            "regle": proposition.regle.code,
            "libelle": proposition.regle.libelle,
            "severite": proposition.regle.severite.value,
            "reagit": len(proposition.essai.reagit_sur),
            "eprouvees": proposition.essai.eprouvees,
            "statut": proposition.statut.value,
            "fin_d_effet": None
            if proposition.fin_d_effet is None
            else proposition.fin_d_effet.isoformat(),
        },
        motif=proposition.motif_du_retrait or proposition.motif_de_la_decision or proposition.motif,
    )


@routeur.post(
    "/regles/propositions",
    summary="Proposer une règle construite (éprouvée à la proposition)",
    status_code=201,
    responses={
        403: {"description": "Hors du circuit du cabinet"},
        409: {"description": "Motif, date, ou règle qui réagit sur toutes les factures"},
        422: {"description": "Condition impossible à traduire"},
    },
)
def proposer_une_regle_construite(
    acces: AccesRequis, demande: DemandeDeRegle
) -> PropositionDeRegle:
    """**Sans effet** tant qu'une autre personne désignée par le circuit ne l'a pas validée."""
    from app.contextes.referentiel.api import circuit_de_validation

    exiger(acces, Permission.CONTROLER_CONFORMITE)
    _reserver_au_cabinet(acces)
    circuit = circuit_de_validation()
    try:
        proposition = proposer_une_regle(
            demande.construction,
            motif=demande.motif,
            par=acces.compte,
            nom=acces.nom_complet,
            le=maintenant(),
            permissions={p.value for p in acces.permissions},
            designees=circuit.regles.proposer,
            motif_minimum=circuit.motif_minimum,
            unite_du_parametre=_unites_des_parametres(),
            factures=FACTURES_DEMO.values(),
            controler=_controler_avec,
            depot=depot_des_regles_du_cabinet(),
        )
    except HorsDuCircuitDesRegles as refus:
        raise HTTPException(status_code=403, detail=str(refus)) from refus
    except PropositionRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    except (ConstructionRefusee, ValueError) as refus:
        # `construire` valide une `Regle` : une ValidationError pydantic rendrait sa trace brute.
        raise HTTPException(status_code=422, detail=message_lisible(refus)) from refus
    _journaliser_la_regle(acces, "conformite.regle_proposee", proposition)
    return proposition


@routeur.get("/regles/propositions", summary="Les règles construites par le cabinet")
def lister_les_regles_du_cabinet(acces: AccesRequis) -> list[PropositionDeRegle]:
    """De la plus récente à la plus ancienne ; les refusées restent lisibles."""
    exiger(acces, Permission.LIRE_DOSSIER)
    _reserver_au_cabinet(acces)
    return list(reversed(depot_des_regles_du_cabinet().toutes()))


class DemandeDeTranchageDeRegle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: DecisionDuSecondRegard
    motif: str = Field(min_length=10, max_length=2000)


@routeur.post(
    "/regles/propositions/{identifiant}/tranchage",
    summary="Valider ou refuser une règle construite",
    responses={
        403: {"description": "Hors du circuit du cabinet"},
        404: {"description": "Proposition inconnue"},
        409: {"description": "Déjà tranchée, ou construite par la même personne"},
    },
)
def trancher_une_regle_construite(
    acces: AccesRequis, identifiant: str, demande: DemandeDeTranchageDeRegle
) -> PropositionDeRegle:
    """Validée, la règle entre au contrôle suivant **du cabinet**, signée par le valideur."""
    from app.contextes.referentiel.api import circuit_de_validation

    exiger(acces, Permission.LIRE_DOSSIER)
    _reserver_au_cabinet(acces)
    circuit = circuit_de_validation()
    try:
        proposition = trancher_une_regle(
            identifiant,
            valider=demande.decision is DecisionDuSecondRegard.CONFIRMER,
            motif=demande.motif,
            par=acces.compte,
            nom=acces.nom_complet,
            le=maintenant(),
            permissions={p.value for p in acces.permissions},
            designees=circuit.regles.valider,
            quatre_yeux=circuit.regles.quatre_yeux,
            motif_minimum=circuit.motif_minimum,
            depot=depot_des_regles_du_cabinet(),
        )
    except HorsDuCircuitDesRegles as refus:
        raise HTTPException(status_code=403, detail=str(refus)) from refus
    except LookupError as refus:
        raise HTTPException(status_code=404, detail=str(refus)) from refus
    except PropositionRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    action = (
        "conformite.regle_validee"
        if proposition.statut is StatutProposition.APPLIQUEE
        else "conformite.regle_refusee"
    )
    _journaliser_la_regle(acces, action, proposition)
    return proposition


class DemandeDeRetraitDeRegle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    a_compter_du: date
    motif: str = Field(min_length=10, max_length=2000)


@routeur.post(
    "/regles/propositions/{identifiant}/retrait",
    summary="Mettre fin à une règle du cabinet, à compter d'une date",
    responses={
        403: {"description": "Hors du circuit du cabinet"},
        404: {"description": "Proposition inconnue"},
        409: {"description": "Règle pas en vigueur, date passée ou motif trop court"},
    },
)
def retirer_une_regle_du_cabinet(
    acces: AccesRequis, identifiant: str, demande: DemandeDeRetraitDeRegle
) -> PropositionDeRegle:
    """Pas 98. À compter de la date, la règle ne contrôle plus ; les contrôles rendus restent."""
    from app.contextes.referentiel.api import circuit_de_validation

    exiger(acces, Permission.LIRE_DOSSIER)
    _reserver_au_cabinet(acces)
    circuit = circuit_de_validation()
    try:
        proposition = retirer_une_regle(
            identifiant,
            a_compter_du=demande.a_compter_du,
            motif=demande.motif,
            par=acces.compte,
            nom=acces.nom_complet,
            le=maintenant(),
            permissions={p.value for p in acces.permissions},
            designees=circuit.regles.valider,
            motif_minimum=circuit.motif_minimum,
            depot=depot_des_regles_du_cabinet(),
        )
    except HorsDuCircuitDesRegles as refus:
        raise HTTPException(status_code=403, detail=str(refus)) from refus
    except LookupError as refus:
        raise HTTPException(status_code=404, detail=str(refus)) from refus
    except PropositionRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    _journaliser_la_regle(acces, "conformite.regle_retiree", proposition)
    return proposition


# ── Le journal des dérogations et la qualité des règles (pas 99) ─────────────
#
# L'entrée « Conformité » du menu était marquée « à venir » depuis la première coquille.
# Elle porte les deux écrans du parcours réviseur : ce que le cabinet a écarté (et qu'un
# vérificateur demandera à voir), et ce que ces écarts disent de la justesse des règles.


@routeur.get("/derogations", summary="Le journal des dérogations du cabinet")
def journal_des_derogations(
    acces: AccesRequis,
    regle: str | None = Query(None),
    auteur: str | None = Query(None, description="Compte qui a proposé l'écart"),
    dossier: str | None = Query(None, description="NIU"),
    statut: str | None = Query(None),
    du: date | None = Query(None, description="Proposées à partir de ce jour (pas 106)"),
    au: date | None = Query(None, description="Proposées jusqu'à ce jour inclus (pas 106)"),
) -> JournalDesDerogations:
    """Tous les écarts de constats du périmètre, du plus récent au plus ancien, avec leur motif.

    ⚠️ **`LIRE_AUDIT`, et non `CONTROLER_CONFORMITE`.** Le journal est ce qu'un vérificateur
    demande à voir : il est ouvert au réviseur, à la direction, et à l'inspecteur **sur son
    seul dossier** (`restreindre`). Un comptable, qui contrôle sans auditer, ne le lit pas.
    Rien ne s'y modifie : une dérogation se lève, elle ne s'efface pas.
    """
    exiger(acces, Permission.LIRE_AUDIT)
    # Pas 106 : le calcul vit dans `api.py`, pour que le rapport mensuel lise les mêmes chiffres.
    return lire_le_journal_des_derogations(
        acces, regle=regle, auteur=auteur, dossier=dossier, statut=statut, du=du, au=au
    )


@routeur.get(
    "/derogations/export",
    summary="Exporter le journal des dérogations pour un contrôle",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
def exporter_les_derogations(
    acces: AccesRequis,
    regle: str | None = Query(None),
    auteur: str | None = Query(None),
    dossier: str | None = Query(None),
    statut: str | None = Query(None),
) -> Response:
    """Le même journal, en CSV séparé par des points-virgules, avec les mêmes filtres.

    ⚠️ UTF-8 avec marque d'ordre des octets : sans elle, le tableur d'un vérificateur lit
    « Ã© » à la place des accents du motif, et le motif est la raison même de l'export.
    """
    import csv
    import io

    exiger(acces, Permission.LIRE_AUDIT)
    noms = noms_des_comptes()
    tampon = io.StringIO()
    ecrivain = csv.writer(tampon, delimiter=";")
    ecrivain.writerow(
        ["Date", "Dossier", "Pièce", "Règle", "Sévérité", "Enjeu levé (FCFA)", "Statut", "Motif",
         "Proposé par", "Second regard", "Motif du second regard", "Levé par", "Motif de levée"]
    )
    for e in derogations_du_perimetre(
        acces, regle=regle, auteur=auteur, dossier=dossier, statut=statut
    ):
        ecrivain.writerow(
            [
                e.propose_le.date().isoformat(), e.dossier, e.reference_document, e.code_regle,
                e.severite.value, "" if e.enjeu is None else str(e.enjeu), e.statut.value, e.motif,
                noms.get(e.propose_par, e.propose_par),
                noms.get(e.tranche_par or "", e.tranche_par or ""),
                e.motif_du_second_regard or "", noms.get(e.leve_par or "", e.leve_par or ""),
                e.motif_de_levee or "",
            ]
        )
    return Response(
        content="﻿" + tampon.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="journal-des-derogations.csv"'},
    )


@routeur.get("/regles/qualite", summary="La qualité des règles, mesurée par les écarts")
def qualite_des_regles(
    acces: AccesRequis,
    du: date | None = Query(None, description="Par défaut : 90 jours avant « au »"),
    au: date | None = Query(None, description="Par défaut : aujourd'hui"),
) -> QualiteDesRegles:
    """Pour chaque règle en vigueur, sur les pièces du périmètre émises dans la période :
    constats émis, constats écartés, enjeu retenu, et la lecture selon les seuils du référentiel.

    Les pièces sont contrôlées ici, par le moteur du cabinet, et arbitrées par ses écarts :
    un rapport ne se conserve pas, il se recalcule (voir `RapportConformite`).
    """
    exiger(acces, Permission.CONTROLER_CONFORMITE)
    fin = au or maintenant().date()
    debut = du or fin - timedelta(days=90)
    if debut > fin:
        raise HTTPException(status_code=422, detail="la période commence après sa fin.")
    # Pas 106 : le calcul vit dans `api.py`, partagé avec le rapport mensuel.
    return mesurer_la_qualite_des_regles(acces, debut, fin)


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
    fin = au or maintenant().date()
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
    for facture in restreindre(acces, FACTURES_DEMO.values(), _destinataire):
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
        facture = _facture_de_la_piece(reference)
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
    _reserver_au_cabinet(acces)
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
