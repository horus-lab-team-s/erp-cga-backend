"""API du contexte J · Pilotage CGA.

─────────────────────────────────────────────────────────────────────────────────
C'EST ICI QUE LA COLLECTE SE FAIT, ET NULLE PART AILLEURS

Le cas d'usage `evaluer_le_risque` reçoit des observations déjà relevées ; il
n'interroge personne. Cette route, elle, interroge cinq contextes — portefeuille,
collecte, conformité, comptabilité, obligations — et compose.

La séparation n'est pas cosmétique. Une collecte fausse **compte mal**, une
pondération fausse **classe mal**, et les deux échouent différemment. Les mêler
dans un même module rendrait le diagnostic impossible : devant un score aberrant,
personne ne saurait s'il faut corriger un relevé ou un poids.

Permission : `LIRE_PILOTAGE`, détenue par la direction seule. C'était, avec
`SUIVRE_FORMALITE` avant le contexte I, l'une des permissions qui n'ouvraient
aucun écran.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.contextes.collecte.api import (
    DepotDemandesSql,
    DepotPiecesSql,
    demandes_en_memoire,
    pieces_en_memoire,
)
from app.contextes.comptabilite.api import (
    DepotEcrituresSql,
    ecritures_en_memoire,
    montant_tva_rejetee,
)
from app.contextes.obligations.api import (
    DepotTypesObligationMemoire,
    accuses_du_portail,
    effectif_du_dossier,
    generer_echeancier,
)
from app.contextes.pilotage.api import (
    CatalogueDesMesures,
    ChargeCollaborateur,
    DecisionDeDirection,
    DecisionIntrouvable,
    DecisionRefusee,
    MesureComposante,
    MesureDeDirection,
    NiveauRisque,
    ObservationsDossier,
    ScoreRisque,
    catalogue_des_mesures,
    clore_une_decision,
    depot_des_decisions_de_direction,
    evaluer_le_risque,
    prendre_une_mesure,
    repartir_la_charge,
)
from app.contextes.portefeuille.api import (
    DepotEntreprisesSql,
    EntrepriseIntrouvable,
    entreprises_en_memoire,
)
from app.contextes.referentiel.api import ServiceParametres, service_parametres
from app.contextes.transverse.api import (
    ROLES_INTERNES,
    Acces,
    AccesRequis,
    Permission,
    atelier,
    exiger,
    exiger_dossier,
    habilitations_actives,
    session_de_travail,
)
from app.partage.horloge import maintenant
from app.partage.locataire import courant

routeur = APIRouter(prefix="/pilotage", tags=["Pilotage"])


def _aujourd_hui() -> date:
    """La date du jour, prise à l'horloge du projet et jamais à `date.today()`."""
    return maintenant().date()


def parametres() -> ServiceParametres:
    # ⚠️ Pas 95 : le référentiel **du cabinet**, par le point de montage unique, et non plus
    # le fichier commun mémoïsé ici. Voir `service_parametres` dans `referentiel/api.py`.
    return service_parametres()


def _depot_dossiers():
    session = session_de_travail()
    if session is None:
        return entreprises_en_memoire()
    return DepotEntreprisesSql(session, courant())


def _depot_pieces():
    session = session_de_travail()
    if session is None:
        return pieces_en_memoire()
    return DepotPiecesSql(session, courant())


def _depot_demandes():
    session = session_de_travail()
    if session is None:
        return demandes_en_memoire()
    return DepotDemandesSql(session, courant())


def _ecritures(entreprise: str):
    session = session_de_travail()
    if session is None:
        return ecritures_en_memoire(entreprise)
    return DepotEcrituresSql(session, courant(), entreprise)


# ── Ce que l'écran lit ───────────────────────────────────────────────────────
class LigneRisque(BaseModel):
    """Un dossier du tableau de bord, avec son score décomposé."""

    entreprise: str
    denomination: str
    total: Decimal
    niveau: NiveauRisque
    mesures: list[MesureComposante]
    poids_non_arretes: bool


class TableauDeBord(BaseModel):
    """Ce que la direction ouvre le matin.

    Les trois compteurs d'en-tête plutôt qu'un seul : « douze dossiers » ne dit
    rien, « douze dossiers dont trois à risque élevé » dit quoi faire aujourd'hui.
    """

    a_la_date: date
    dossiers: int
    a_risque_eleve: int
    a_risque_modere: int
    #: Vrai si un poids ou un seuil n'a pas encore été arrêté par la direction.
    poids_non_arretes: bool
    risques: list[LigneRisque]
    charges: list[ChargeCollaborateur]


@routeur.get("/tableau-de-bord", summary="Les dossiers à risque et la charge du cabinet")
def lire_tableau_de_bord(
    acces: AccesRequis,
    a_la_date: date | None = Query(None, description="Défaut : aujourd'hui"),
    exercice: str = Query("2026", description="Exercice sur lequel juger les retards"),
) -> TableauDeBord:
    """Le tableau de bord, calculé à la volée.

    ⚠️ **Rien n'est stocké.** Le score découle de l'état du dossier au jour de la
    lecture ; le figer produirait un tableau de bord qui vieillit sans le dire, et
    la direction agirait sur un risque déjà levé.

    Les dossiers sont rendus **du plus risqué au moins risqué** : la direction
    traite ce qui est en haut. Un tri alphabétique ferait un annuaire.
    """
    exiger(acces, Permission.LIRE_PILOTAGE)
    jour = a_la_date or _aujourd_hui()

    dossiers = _depot_dossiers().lister()
    pieces = _depot_pieces()
    demandes = _depot_demandes()

    scores: list[ScoreRisque] = []
    for dossier in dossiers:
        observations = _observer(dossier, pieces, demandes, jour, exercice)
        scores.append(evaluer_le_risque(observations, parametres(), jour))

    scores.sort(key=lambda s: s.total, reverse=True)

    return TableauDeBord(
        a_la_date=jour,
        dossiers=len(scores),
        a_risque_eleve=sum(1 for s in scores if s.niveau is NiveauRisque.ELEVE),
        a_risque_modere=sum(1 for s in scores if s.niveau is NiveauRisque.MODERE),
        poids_non_arretes=any(s.repose_sur_des_poids_non_arretes for s in scores),
        risques=[
            LigneRisque(
                entreprise=s.entreprise,
                denomination=s.denomination,
                total=s.total,
                niveau=s.niveau,
                mesures=list(s.mesures_actives),
                poids_non_arretes=s.repose_sur_des_poids_non_arretes,
            )
            for s in scores
        ],
        charges=repartir_la_charge(scores, *_qui_suit_quoi(scores, jour)),
    )


# ── La vue risque d'un dossier, et les décisions de la direction (pas 100) ───
#
# ─────────────────────────────────────────────────────────────────────────────
# DU TABLEAU DE BORD AU DOSSIER, PUIS DU DOSSIER À LA DÉCISION
#
# Le tableau de bord classe ; la vue risque **explique et fait décider**. Elle
# rend le même score que le tableau de bord (même `_observer`, même
# `evaluer_le_risque` : deux calculs différents finiraient par diverger), mais
# avec toutes les composantes, y compris celles à zéro, les seuils employés, les
# mesures que le catalogue du cabinet propose à ce niveau, et l'histoire des
# décisions déjà prises.
#
# QUI LIT QUOI
#
# * la vue risque : `LIRE_PILOTAGE`, la direction. Le score porte un jugement
#   d'affectation que seule la direction a mandat de lire (voir la navigation) ;
# * décider et clore : `DECIDER_SUR_DOSSIER`, qui exige un motif ;
# * la liste des décisions, sans le score : `LIRE_DOSSIER` et **interne**. Le
#   collaborateur qui porte le dossier doit savoir qu'une régularisation a été
#   exigée, puisque c'est lui qui la suivra ; l'adhérent, lui, ne lit pas qu'on
#   envisage de mettre fin à son adhésion avant que le comité en ait décidé.
# ─────────────────────────────────────────────────────────────────────────────


class DecisionLue(BaseModel):
    """Une décision telle que l'écran l'affiche : la décision, et si elle est échue."""

    decision: DecisionDeDirection
    #: Vrai si la mesure est encore en cours alors que son échéance est dépassée.
    echue: bool


class VueRisque(BaseModel):
    """Ce que la direction lit en ouvrant un dossier depuis le tableau de bord."""

    a_la_date: date
    exercice: str
    entreprise: str
    denomination: str
    total: Decimal
    niveau: NiveauRisque
    seuil_modere: Decimal
    seuil_eleve: Decimal
    poids_non_arretes: bool
    #: Les quatre composantes, **y compris celles à zéro**, de la plus lourde à la
    #: moins lourde. Une composante absente laisserait croire qu'elle n'a pas été
    #: regardée, alors qu'elle l'a été et qu'elle est saine.
    composantes: list[MesureComposante]
    #: Les mesures que le catalogue propose **à ce niveau**, dans l'ordre du fichier.
    mesures_proposees: list[MesureDeDirection]
    #: D'où vient le catalogue (« pilotage/mesures.yaml », ou pourquoi il est vide).
    source_du_catalogue: str
    motif_minimum: int
    #: Les décisions du dossier, **de la plus récente à la plus ancienne**.
    decisions: list[DecisionLue]


class DemandeDeMesure(BaseModel):
    mesure: str = Field(min_length=3, max_length=40)
    motif: str = Field(min_length=1, max_length=1000)
    echeance: date | None = None
    exercice: str | None = Field(
        None, description="Exercice sur lequel juger les retards. Défaut : l'année du jour."
    )


class DemandeDeCloture(BaseModel):
    motif: str = Field(min_length=1, max_length=1000)


def _dossier_ou_404(niu: str):
    try:
        return _depot_dossiers().lire(niu)
    except EntrepriseIntrouvable as absent:
        raise HTTPException(status_code=404, detail=f"dossier « {niu} » inconnu.") from absent


def _score_du_dossier(niu: str, jour: date, exercice: str) -> ScoreRisque:
    """Le score du dossier, calculé **exactement** comme au tableau de bord."""
    observations = _observer(
        _dossier_ou_404(niu), _depot_pieces(), _depot_demandes(), jour, exercice
    )
    return evaluer_le_risque(observations, parametres(), jour)


def _decisions_lues(niu: str, jour: date) -> list[DecisionLue]:
    decisions = depot_des_decisions_de_direction().du_dossier(niu)
    return [
        DecisionLue(decision=d, echue=d.echue(jour))
        for d in sorted(decisions, key=lambda d: (d.prise_le, d.identifiant), reverse=True)
    ]


def _journaliser_la_decision(acces: Acces, action: str, decision: DecisionDeDirection, motif: str):
    """La décision au journal d'audit, chaîné : c'est lui qui ferait foi en comité.

    ⚠️ Le motif est dans l'entrée, **pas dans `apres`** : les notifications lisent `apres`,
    et un motif de direction (« dirigeant injoignable depuis trois mois ») n'a pas à être
    affiché dans la cloche du collaborateur.
    """
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action=action,
        objet_type="decision_de_direction",
        objet_id=decision.identifiant,
        apres={
            "dossier": decision.dossier,
            "mesure": decision.mesure,
            "libelle": decision.libelle,
            "echeance": decision.echeance.isoformat() if decision.echeance else None,
            "statut": decision.statut.value,
            "score": str(decision.score.total),
            "niveau": decision.score.niveau.value,
            "par": acces.nom_complet,
        },
        motif=motif.strip(),
    )


@routeur.get(
    "/dossiers/{niu}/risque", summary="La vue risque d'un dossier : le score expliqué"
)
def lire_la_vue_risque(
    acces: AccesRequis,
    niu: str,
    a_la_date: date | None = Query(None, description="Défaut : aujourd'hui"),
    exercice: str | None = Query(None, description="Défaut : l'année de la date"),
) -> VueRisque:
    exiger_dossier(acces, Permission.LIRE_PILOTAGE, niu)
    jour = a_la_date or _aujourd_hui()
    exercice = exercice or str(jour.year)
    score = _score_du_dossier(niu, jour, exercice)
    catalogue: CatalogueDesMesures = catalogue_des_mesures()
    return VueRisque(
        a_la_date=jour,
        exercice=exercice,
        entreprise=score.entreprise,
        denomination=score.denomination,
        total=score.total,
        niveau=score.niveau,
        seuil_modere=score.seuil_modere,
        seuil_eleve=score.seuil_eleve,
        poids_non_arretes=score.repose_sur_des_poids_non_arretes,
        composantes=sorted(
            score.mesures, key=lambda m: (m.contribution, m.occurrences), reverse=True
        ),
        mesures_proposees=list(catalogue.proposees_pour(score.niveau)),
        source_du_catalogue=catalogue.source,
        motif_minimum=catalogue.motif_minimum,
        decisions=_decisions_lues(niu, jour),
    )


@routeur.get(
    "/dossiers/{niu}/decisions",
    summary="Les décisions de la direction sur un dossier, sans le score",
)
def lire_les_decisions(acces: AccesRequis, niu: str) -> list[DecisionLue]:
    """Pour la fiche adhérent : ce qui a été décidé, par qui, jusqu'à quand.

    Réservée au cabinet (voir l'en-tête du bloc). Le score n'y figure pas en tant que tel ;
    l'instantané reste dans la décision, parce qu'il en est le fondement.
    """
    exiger_dossier(acces, Permission.LIRE_DOSSIER, niu)
    if not acces.interne:
        # 404 et non 403 : l'existence même d'une décision de direction est une information.
        raise HTTPException(status_code=404, detail="aucune décision lisible sur ce dossier.")
    _dossier_ou_404(niu)
    return _decisions_lues(niu, _aujourd_hui())


@routeur.post(
    "/dossiers/{niu}/decisions", summary="Décider une mesure sur un dossier à risque"
)
def decider_une_mesure(
    acces: AccesRequis, niu: str, demande: DemandeDeMesure
) -> DecisionLue:
    """Décide une mesure du catalogue devant le score **recalculé à l'instant**.

    ⚠️ Le score n'est pas pris de l'écran : une vue risque restée ouverte depuis la veille
    montrerait un niveau qui n'est peut-être plus le bon, et la décision serait fondée sur
    un état qui n'existe plus.
    """
    exiger_dossier(acces, Permission.DECIDER_SUR_DOSSIER, niu, motif=demande.motif)
    instant = maintenant()
    jour = instant.date()
    score = _score_du_dossier(niu, jour, demande.exercice or str(jour.year))
    try:
        decision = prendre_une_mesure(
            score=score,
            a_la_date=jour,
            catalogue=catalogue_des_mesures(),
            code=demande.mesure,
            motif=demande.motif,
            echeance=demande.echeance,
            par=acces.compte,
            par_nom=acces.nom_complet,
            le=instant,
            depot=depot_des_decisions_de_direction(),
        )
    except DecisionRefusee as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus
    _journaliser_la_decision(acces, "pilotage.decision_prise", decision, demande.motif)
    return DecisionLue(decision=decision, echue=False)


@routeur.post(
    "/dossiers/{niu}/decisions/{identifiant}/cloture",
    summary="Clore une décision de direction",
)
def clore_la_decision(
    acces: AccesRequis, niu: str, identifiant: str, demande: DemandeDeCloture
) -> DecisionLue:
    exiger_dossier(acces, Permission.DECIDER_SUR_DOSSIER, niu, motif=demande.motif)
    instant = maintenant()
    try:
        decision = clore_une_decision(
            dossier=niu,
            identifiant=identifiant,
            motif=demande.motif,
            par=acces.compte,
            par_nom=acces.nom_complet,
            le=instant,
            catalogue=catalogue_des_mesures(),
            depot=depot_des_decisions_de_direction(),
        )
    except DecisionIntrouvable as absente:
        raise HTTPException(status_code=404, detail=str(absente)) from absente
    except DecisionRefusee as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus
    _journaliser_la_decision(acces, "pilotage.decision_close", decision, demande.motif)
    return DecisionLue(decision=decision, echue=False)


def _qui_suit_quoi(
    scores: list[ScoreRisque], jour: date
) -> tuple[dict[str, tuple[str, ...]], dict[str, str]]:
    """Qui suit quel dossier, et comment chacun s'appelle.

    ─────────────────────────────────────────────────────────────────────────
    LES HABILITATIONS À PORTÉE OUVERTE NE COMPTENT PAS DANS LA CHARGE

    Un réviseur ou une direction habilités sur **tout** le portefeuille ne
    « portent » pas les cent dossiers du cabinet : ils y ont accès. Les compter
    ferait apparaître la direction en tête de la charge, chaque matin, et
    l'indicateur cesserait de dire ce qu'il est censé dire — comment le travail
    se répartit entre ceux qui le font.

    Seules les habilitations **à portée explicite** entrent donc dans le calcul.
    C'est aussi ce qui rend l'indicateur actionnable : rééquilibrer, c'est
    déplacer un dossier d'une portée à une autre.

    ⚠️ Les habilitations sont relues **à la date du tableau de bord**, pas à
    l'instant courant : une habilitation fermée hier ne doit pas apparaître dans
    la charge d'aujourd'hui, et une charge relue sur un mois passé doit refléter
    qui suivait quoi ce mois-là.
    """
    suivi: dict[str, list[str]] = {}
    noms: dict[str, str] = {}
    try:
        boutique = atelier()
        for score in scores:
            for habilitation in boutique.habilitations.pour_dossier(score.entreprise):
                if not habilitations_actives([habilitation], jour):
                    continue
                if habilitation.portee is None:
                    continue
                # ⚠️ Pas 105 : un adhérent et un inspecteur ont une portée explicite (leur
                # dossier, leur mission), et figuraient donc parmi les « collaborateurs » de la
                # charge. Ils ne portent aucun travail du cabinet.
                if habilitation.role not in ROLES_INTERNES:
                    continue
                suivi.setdefault(score.entreprise, []).append(habilitation.compte)
        for comptes in suivi.values():
            for compte in comptes:
                if compte not in noms:
                    fiche = boutique.comptes.lire(compte)
                    noms[compte] = f"{fiche.prenom} {fiche.nom}".strip() or compte
    except Exception:  # noqa: BLE001 — agrégateur : voir `_sans_echouer`.
        return {}, {}
    return {niu: tuple(comptes) for niu, comptes in suivi.items()}, noms


def _observer(
    dossier,
    pieces,
    demandes,
    jour: date,
    exercice: str,
) -> ObservationsDossier:
    """Relève les quatre composantes sur un dossier.

    ─────────────────────────────────────────────────────────────────────────
    CHAQUE RELEVÉ RAMÈNE DES RÉFÉRENCES, JAMAIS UN COMPTE

    C'est ce qui rend le score traçable jusqu'à la pièce. Compter suffirait au
    calcul et rendrait le résultat inactionnable : le directeur verrait « trois
    pièces en souffrance » sans savoir lesquelles, et rouvrirait le dossier pour
    les chercher.

    ⚠️ Un contexte indisponible ne fait pas échouer le tableau de bord : la
    composante concernée reste vide et le score est sous-estimé. C'est le seul
    défaut acceptable ici — un tableau de bord absent ne sert personne, un
    tableau de bord sous-estimé se corrige au premier incident.
    ─────────────────────────────────────────────────────────────────────────
    """
    niu = dossier.niu

    en_souffrance = tuple(
        p.identifiant
        for p in _sans_echouer(lambda: pieces.du_dossier(niu), [])
        if getattr(p, "en_attente_de_traitement", False)
    )

    # ⚠️ Pas 104 : cette composante valait **toujours zéro**, sans que rien ne le dise. La
    # lecture appelait `demandes.du_dossier`, que le dépôt des demandes n'a jamais eu ; l'erreur
    # était avalée par `_sans_echouer`, si bien que le filtre sur `StatutDemande.EN_ATTENTE`,
    # un statut qui n'existe pas, n'était jamais évalué. Deux défauts qui se masquaient.
    #
    # « Sans réponse » : une demande **ouverte** qui est échue, ou assez ancienne pour être
    # escaladée (une demande sans date butoir n'échoit jamais, et restait sinon invisible).
    # Une demande ouverte hier n'est pas encore un risque.
    sans_reponse = tuple(
        d.identifiant
        for d in _sans_echouer(lambda: demandes.ouvertes(niu), [])
        if d.en_retard(jour) or d.a_escalader(jour)
    )

    bloquantes = tuple(
        ecriture.piece_justificative or f"{ecriture.journal}-{ecriture.numero:06d}"
        for ecriture in _sans_echouer(lambda: _ecritures(niu).toutes(exercice), [])
        if montant_tva_rejetee(ecriture) > 0
    )

    retards = tuple(
        f"{instance.code_obligation} {instance.periode_debut:%m/%Y}"
        for instance in _sans_echouer(
            lambda: _echeancier(dossier, exercice, jour), []
        )
        if instance.en_retard(jour)
    )

    return ObservationsDossier(
        entreprise=niu,
        denomination=dossier.denomination,
        anomalies_bloquantes=bloquantes,
        obligations_en_retard=retards,
        pieces_en_souffrance=en_souffrance,
        demandes_sans_reponse=sans_reponse,
    )


#: Le catalogue des types d'obligation. En mémoire, comme dans le contexte F :
#: ce sont des définitions, pas des données de dossier.
_catalogue = DepotTypesObligationMemoire()


def _echeancier(dossier, exercice: str, jour: date):
    periode = next((e for e in dossier.exercices if e.libelle == exercice), None)
    if periode is None:
        return []
    # ⚠️ La même question que l'échéancier des obligations (pas 56) : sans elle, le
    # pilotage ne comptait jamais une CNPS en retard.
    return generer_echeancier(
        dossier,
        _catalogue.charger(jour),
        periode,
        emploie_sur=effectif_du_dossier(dossier.niu),
        # ⚠️ Sans elle, une déclaration déposée pesait sur le score comme un retard
        # (pas 58).
        accuse_de=accuses_du_portail(),
    )


def _sans_echouer(lecture, repli):
    """Exécute une lecture, ou rend le repli.

    Le tableau de bord agrège cinq contextes : si l'un d'eux refuse, la direction
    doit quand même voir les quatre autres. L'exception est avalée **ici et
    seulement ici** — dans un agrégateur, jamais dans un calcul.

    ⚠️ **Sauf une erreur de programmation** (pas 104). Une méthode qui n'existe pas, un
    mauvais nombre d'arguments ne sont pas un contexte indisponible : ce sont des défauts du
    code, et les avaler les rend invisibles pour toujours. C'est exactement ce qui est arrivé
    aux demandes sans réponse, dont la composante a valu zéro sans que personne ne le voie.
    """
    try:
        return lecture()
    except (AttributeError, TypeError, NameError):
        raise
    except Exception:  # noqa: BLE001 — voir la docstring : c'est un agrégateur.
        return repli


__all__ = ["routeur"]
