"""API du contexte H · Clôture et DSF.

Une seule route de lecture, et c'est délibéré : la liasse ne se consulte pas par
morceaux. Un comptable qui ouvrirait le bilan sans le tableau de passage n'aurait
qu'une moitié de vérité, et c'est l'autre moitié qui décide de l'impôt.

Permissions. `LIRE_COMPTABILITE` pour consulter. `CLOTURER_EXERCICE` pour clore,
détenue par le réviseur et la direction seuls, et exigeant un motif écrit.

⚠️ DEUX ACTES QUE CE MODULE A LONGTEMPS CONFONDUS

Cette documentation disait que la clôture « suppose l'archivage de l'accusé de
dépôt ». C'était tenir pour un seul acte ce qui en fait deux, et dans le mauvais
ordre :

    la clôture comptable    arrêter les comptes, reporter les soldes
    le dépôt de la DSF      postérieur, puisque la liasse se fabrique **à partir**
                            des comptes arrêtés

Attendre l'accusé de dépôt pour reporter les soldes laisserait l'entreprise
travailler de janvier à mai sans caisse d'ouverture, sans fournisseurs à payer et
sans capital. Ce n'est pas tenable, et aucun cabinet ne procède ainsi.

L'archivage de l'accusé reste à construire, dans F · Obligations, et il ne
conditionne pas la clôture : il l'accompagne.

⚠️ ET APRÈS, SI UN AJUSTEMENT ARRIVE

La clôture ne se défait pas : c'est le principe d'intangibilité, celui-là même qui
distingue une comptabilité d'un tableur. Une correction venue après coup — un
redressement, une facture oubliée — se passe dans l'exercice **suivant**, sur les
comptes de charges et produits sur exercices antérieurs. C'est la pratique, et
c'est ce que l'administration attend de voir.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.cloture.api import (
    ControleCoherence,
    LigneLiasse,
    LignePassage,
    SystemeDsf,
    assembler_les_etats,
    determiner_le_systeme,
    etablir_le_passage,
    moissonner,
    postes_du_systeme,
)
from app.contextes.cloture.application.exercice_clos import (
    ClotureRefusee,
    RapportDeCloture,
    clore_un_exercice,
)
from app.contextes.cloture.domaine.droit_cga import (
    DroitAuxAvantagesCGA,
    apprecier_le_droit,
)
from app.contextes.comptabilite.api import (
    COMPTES_SYSCOHADA,
    JOURNAUX_CABINET,
    DepotEcrituresSql,
    balance,
    chiffre_affaires,
    ecritures_en_memoire,
)
from app.contextes.portefeuille.api import (
    DepotEntreprisesSql,
    EntrepriseIntrouvable,
    entreprises_en_memoire,
)
from app.contextes.referentiel.api import (
    AucuneVersionApplicable,
    ParametreInconnu,
    ParametreResolu,
    ServiceParametres,
    service_parametres,
)
from app.contextes.transverse.api import (
    AccesRequis,
    Permission,
    exiger,
    exiger_dossier,
    session_de_travail,
)
from app.partage.horloge import maintenant
from app.partage.locataire import courant

routeur = APIRouter(prefix="/cloture", tags=["Clôture et DSF"])


def _depot_ecritures(entreprise: str):
    session = session_de_travail()
    if session is None:
        return ecritures_en_memoire(entreprise)
    return DepotEcrituresSql(session, courant(), entreprise)


def _depot_dossiers():
    session = session_de_travail()
    if session is None:
        return entreprises_en_memoire()
    return DepotEntreprisesSql(session, courant())


def parametres() -> ServiceParametres:
    # ⚠️ Pas 95 : le référentiel **du cabinet**, par le point de montage unique, et non plus
    # le fichier commun mémoïsé ici. Voir `service_parametres` dans `referentiel/api.py`.
    return service_parametres()


class Liasse(BaseModel):
    """La liasse d'un exercice : états, contrôles et passage fiscal.

    Un seul objet plutôt que trois routes. Le tableau de passage n'a de sens
    qu'au regard du résultat comptable dont il part, et les contrôles ne valent
    que rapportés aux états qu'ils contrôlent : les séparer obligerait l'écran à
    recoller trois lectures qui doivent être cohérentes entre elles.
    """

    entreprise: str
    denomination: str
    exercice: str
    cloture: date
    systeme: SystemeDsf
    #: Vrai si le classement repose sur un seuil non encore validé.
    systeme_non_valide: bool

    lignes: list[LigneLiasse]
    comptes_non_couverts: list[str]
    controles: list[ControleCoherence]
    coherent: bool

    total_actif: Decimal
    total_passif: Decimal
    total_charges: Decimal
    total_produits: Decimal
    resultat_comptable: Decimal
    resultat_par_le_bilan: Decimal

    passage: list[LignePassage]
    total_reintegrations: Decimal
    total_deductions: Decimal
    resultat_fiscal: Decimal
    passage_non_valide: bool

    #: La TVA rejetée par le contrôle de conformité — **signalée, non réintégrée**.
    tva_rejetee_a_verifier: Decimal
    pieces_a_verifier: list[str]

    #: Pourquoi l'abattement CGA figure au passage, ou pourquoi il n'y figure pas.
    droit_cga: DroitAuxAvantagesCGA
    #: Le droit est ouvert, et l'abattement ne figure pourtant pas : pourquoi.
    #: `None` si l'abattement figure, ou si le droit est fermé (voir `droit_cga`).
    abattement_cga_ecarte: str | None = None


@routeur.get(
    "/dossiers/{entreprise}/liasse/{exercice}",
    summary="La liasse fiscale d'un exercice, assemblée et contrôlée",
)
def lire_liasse(
    acces: AccesRequis,
    entreprise: str,
    exercice: str,
) -> Liasse:
    """Assemble la liasse, du solde des comptes au résultat fiscal.

    ⚠️ **La date de clôture vient de l'exercice du dossier, jamais du jour.** Les
    paramètres du référentiel — seuil du Système Normal, taux d'abattement — se
    lisent à cette date : une liasse 2024 rouverte en 2027 doit employer les
    valeurs de 2024, sans quoi elle cesserait d'être reproductible et un contrôle
    fiscal trouverait deux chiffres pour un même exercice.

    ⚠️ **LE DROIT À L'ABATTEMENT CGA NE SE DEMANDE PLUS, IL SE CONSTATE.** Cette
    route recevait un paramètre `adherent_sur_l_exercice`, `True` par défaut : toute
    liasse déduisait l'abattement du bénéfice imposable, adhérent ou non. Le droit
    est maintenant apprécié sur le portefeuille et sur les livres, et rendu dans la
    liasse avec son motif. Un écran qui enverrait encore le paramètre ne changerait
    rien : il n'est plus lu.
    """
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)

    try:
        dossier = _depot_dossiers().lire(entreprise)
    except EntrepriseIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence

    periode = next((e for e in dossier.exercices if e.libelle == exercice), None)
    if periode is None:
        connus = ", ".join(e.libelle for e in dossier.exercices) or "aucun"
        raise HTTPException(
            status_code=404,
            detail=f"exercice « {exercice} » inconnu pour {dossier.denomination}. "
            f"Connus : {connus}.",
        )

    ecritures = _depot_ecritures(entreprise).toutes(exercice)
    soldes = balance(ecritures)

    # ⚠️ La projection partagée, et non une somme refaite ici : cette route portait
    # sa propre version du chiffre d'affaires, écrite avant qu'il en existe une.
    ca = chiffre_affaires(soldes)
    systeme, systeme_non_valide = determiner_le_systeme(
        ca, parametres(), periode.cloture
    )
    seuil = _seuil_d_adhesion(periode.cloture)
    droit = apprecier_le_droit(
        dossier,
        periode,
        chiffre_affaires=ca,
        seuil_adhesion=seuil.valeur_decimale if seuil else None,
        borne_adhesion=seuil.borne if seuil else None,
    )
    etats = assembler_les_etats(soldes, systeme, systeme_non_valide=systeme_non_valide)

    moisson = moissonner(ecritures)
    passage = etablir_le_passage(
        etats.resultat_comptable,
        list(moisson.a_reintegrer),
        parametres(),
        periode.cloture,
        droit_a_l_abattement_cga=droit.ouvert,
    )

    return Liasse(
        entreprise=entreprise,
        denomination=dossier.denomination,
        exercice=exercice,
        cloture=periode.cloture,
        systeme=etats.systeme,
        systeme_non_valide=etats.systeme_non_valide,
        lignes=list(etats.lignes),
        comptes_non_couverts=list(etats.comptes_non_couverts),
        controles=list(etats.controles),
        coherent=etats.coherent,
        total_actif=etats.total_actif,
        total_passif=etats.total_passif,
        total_charges=etats.total_charges,
        total_produits=etats.total_produits,
        resultat_comptable=etats.resultat_comptable,
        resultat_par_le_bilan=etats.resultat_par_le_bilan,
        passage=list(passage.lignes),
        total_reintegrations=passage.total_reintegrations,
        total_deductions=passage.total_deductions,
        resultat_fiscal=passage.resultat_fiscal,
        passage_non_valide=passage.repose_sur_des_valeurs_non_validees,
        tva_rejetee_a_verifier=moisson.tva_rejetee,
        pieces_a_verifier=list(moisson.pieces_a_verifier),
        droit_cga=droit,
        abattement_cga_ecarte=passage.abattement_cga_ecarte,
    )


def _seuil_d_adhesion(cloture: date) -> ParametreResolu | None:
    """Le seuil d'adhésion en vigueur à la clôture, ou `None` si le référentiel se tait.

    ⚠️ `None` ferme le droit : attester une éligibilité qu'on ne peut pas vérifier
    est la faute même que l'appréciation du droit existe pour empêcher.
    """
    try:
        return parametres().resoudre("SEUIL_ADHESION_CGA", cloture)
    except (ParametreInconnu, AucuneVersionApplicable):
        return None


class LignePlan(BaseModel):
    """Une entrée du plan de correspondance, pour l'écran de référence."""

    code: str
    libelle: str
    sens: str
    prefixes: list[str]


@routeur.get("/plan-liasse", summary="Le plan de correspondance balance → postes")
def lire_plan(
    acces: AccesRequis, systeme: SystemeDsf = Query(SystemeDsf.NORMAL)
) -> list[LignePlan]:
    """Ce qui alimente quoi.

    Consultable parce que la question « pourquoi ce montant est-il là ? » se pose
    à chaque revue de liasse, et qu'y répondre en ouvrant le code n'est pas une
    réponse qu'un cabinet peut donner à son adhérent.

    `exiger` sans dossier : le plan ne porte aucune donnée d'adhérent, seulement
    la mécanique de ventilation.
    """
    exiger(acces, Permission.LIRE_COMPTABILITE)
    return [
        LignePlan(
            code=p.code, libelle=p.libelle, sens=p.sens.value, prefixes=list(p.prefixes)
        )
        for p in postes_du_systeme(systeme)
    ]



__all__ = ["routeur"]


class DemandeDeCloture(BaseModel):
    """Ce qu'un réviseur soumet pour clore.

    ⚠️ `extra="forbid"` : la route ne reçoit **que** le motif. Tout le reste —
    l'exercice, le dossier, la date, le résultat — est calculé ou vient du chemin.
    Accepter un champ de plus ouvrirait la porte à un résultat déclaré par
    l'appelant, ce qui est exactement ce que la section sur les trois surfaces a
    corrigé ailleurs.
    """

    model_config = ConfigDict(extra="forbid")

    #: ⚠️ Un motif court est un motif vide. Trente caractères n'est pas une
    #: contrainte de forme : c'est le seuil au-dessous duquel on écrit « RAS » ou
    #: « ok », et un journal d'audit rempli de « ok » ne sert plus à rien le jour
    #: où un vérificateur demande pourquoi cet exercice a été arrêté ce jour-là.
    motif: str = Field(min_length=30, max_length=500)

    #: ⚠️ La **fin** de l'exercice suivant, quand elle n'est pas douze mois. Son
    #: ouverture, elle, n'est jamais un choix : l'entité garantit que les exercices
    #: se suivent sans trou, donc c'est le lendemain de cette clôture-ci.
    #:
    #: Absente, douze mois moins un jour, qui est le cas de la quasi-totalité des
    #: dossiers. Présente alors que l'exercice suivant existe déjà avec d'autres
    #: bornes, la clôture refuse plutôt que d'ignorer la demande en silence.
    cloture_du_suivant: date | None = None


@routeur.post(
    "/dossiers/{entreprise}/exercices/{exercice}/cloture",
    summary="Clore un exercice et reporter ses soldes sur le suivant",
    responses={
        403: {"description": "Habilitation insuffisante, ou motif manquant"},
        404: {"description": "Dossier ou exercice inconnu"},
        409: {"description": "La clôture est refusée par les contrôles de saisie"},
    },
)
def clore(
    acces: AccesRequis,
    entreprise: str,
    exercice: str,
    demande: DemandeDeCloture,
    appliquer: bool = Query(
        False,
        description=(
            "Faux, le défaut : le rapport dit ce qui se passerait, rien n'est écrit."
        ),
    ),
) -> RapportDeCloture:
    """Arrête les comptes de l'exercice, et rouvre le suivant sur ses soldes.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LE MODE CONTRÔLE EST LE DÉFAUT, ET L'ENJEU EST PLUS GRAND QU'AILLEURS.**

    Une reprise mal faite laisse des brouillons, qui ne se suppriment pas (Q23). Une clôture mal
    faite **ferme un exercice**, et le produit ne sait pas le rouvrir. Se tromper
    de dossier ou d'exercice doit donc se voir avant.

    ⚠️ **LE MOTIF PASSE PAR `exiger_dossier`, IL NE SE CONTRÔLE PAS À LA MAIN.**

    `CLOTURER_EXERCICE` figure dans `EXIGE_MOTIF`. Appeler `exiger` puis vérifier
    le périmètre séparément est le chemin par lequel l'un des deux contrôles se
    perd un jour ; le motif se transmet donc au contrôle, qui en fait ce qu'il
    doit.

    ⚠️ **UN REFUS D'OBSTACLE N'EST PAS UNE ERREUR HTTP.**

    Des brouillons subsistants, un exercice suivant manquant : ce sont des
    **résultats**, et un écran doit pouvoir afficher la liste entière avec le geste
    à faire pour chacun. Un `409` obligerait l'interface à relire un message pour
    savoir quoi proposer, et elle le relirait mal. Le `409` est réservé au cas où
    l'écriture d'à-nouveau elle-même est refusée à l'enregistrement.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger_dossier(
        acces, Permission.CLOTURER_EXERCICE, entreprise, motif=demande.motif
    )
    try:
        return clore_un_exercice(
            entreprise,
            exercice,
            motif=demande.motif,
            entreprises=_depot_dossiers(),
            depot=_depot_ecritures(entreprise),
            journaux=list(JOURNAUX_CABINET),
            plan=list(COMPTES_SYSCOHADA),
            par=acces.compte,
            a_l_instant=maintenant(),
            appliquer=appliquer,
            cloture_du_suivant=demande.cloture_du_suivant,
        )
    except EntrepriseIntrouvable as absent:
        raise HTTPException(status_code=404, detail=str(absent)) from absent
    except ClotureRefusee as refus:
        # ⚠️ Deux causes possibles, et le statut les sépare : un exercice qui
        # n'existe pas est un `404`, une écriture d'à-nouveau refusée est un `409`.
        # Les confondre ferait chercher une faute de saisie sur un libellé mal
        # tapé.
        statut = 404 if "n'existe pas" in str(refus) else 409
        raise HTTPException(status_code=statut, detail=str(refus)) from refus
