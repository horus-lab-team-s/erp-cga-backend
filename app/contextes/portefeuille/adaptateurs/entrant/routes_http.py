"""API du contexte B · Portefeuille adhérents.

Ce que ces routes exposent tient en une idée : **rien ne se lit sans date.**

Le régime, le rattachement, l'adhésion sont des statuts historisés. Une route qui
rendrait « le régime de l'entreprise » sans dire à quelle date obligerait l'écran
à supposer « aujourd'hui », et l'écran qui affiche une facture de 2022 afficherait
alors le régime de 2026. C'est exactement l'erreur que le contexte B a été bâti
pour rendre impossible : elle ne se voit pas, et elle fausse tout ce qui suit.

Le paramètre `a_la_date` est donc **obligatoire** partout où un statut est rendu —
comme sur le référentiel normatif, et pour la même raison.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.portefeuille.adaptateurs.sortant.depot_entreprises_memoire import (
    EntrepriseIntrouvable,
    HistoireAmputee,
)
from app.contextes.portefeuille.adaptateurs.sortant.depot_entreprises_sql import (
    DepotEntreprisesSql,
)
from app.contextes.portefeuille.adaptateurs.sortant.magasins_memoire import entreprises_en_memoire
from app.contextes.portefeuille.api import (
    AdhesionRefusee,
    AnomalieIdentifiant,
    CentreRattachement,
    Entreprise,
    Exercice,
    InscriptionRefusee,
    MotifChangement,
    RegimeFiscal,
    StatutIntrouvable,
    inscrire_un_regime,
    inscrire_une_adhesion,
    prochain_numero_d_adhesion,
    resilier_l_adhesion,
    verifier_portefeuille,
)
from app.contextes.portefeuille.domaine.ports import DepotEntreprises
from app.contextes.referentiel.api import (
    AucuneVersionApplicable,
    ParametreResolu,
    ServiceParametres,
    service_parametres,
)
from app.contextes.transverse.api import (
    AccesRequis,
    Permission,
    exiger,
    exiger_dossier,
    restreindre,
    session_de_travail,
)
from app.infrastructure.config import configuration
from app.partage.horloge import maintenant
from app.partage.locataire import courant
from app.partage.recherche import (
    ReponseDeRecherche,
    RequeteTropCourte,
    ResultatDeRecherche,
    charger_les_reglages_de_recherche,
    pertinence,
    verifier_la_requete,
)
from app.partage.recherche import (
    # ⚠️ Renommée à l'import : la collecte a déjà une route `classer` (classer une
    # demande sans suite), et l'homonyme importée l'avait masquée. Même nom partout.
    classer as classer_les_resultats,
)

routeur = APIRouter(prefix="/portefeuille", tags=["Portefeuille"])


def depot() -> DepotEntreprises:
    """Le dépôt en vigueur — SQL dans une requête, mémoire sinon.

    La bascule est ici et non dans chaque route : une route qui choisirait son
    dépôt le choisirait un jour mal, et l'incohérence — une lecture en base, une
    écriture en mémoire — ne se verrait qu'au redémarrage. C'est exactement le
    défaut trouvé sur le registre des accusés de réception.
    """
    session = session_de_travail()
    if session is None:
        return _depot_memoire()
    return DepotEntreprisesSql(session, courant())


# ⚠️ Le magasin mémoire du contexte propriétaire, et non une construction locale :
# voir `magasins_memoire.py` de ce contexte, et le pas 52.
_depot_memoire = entreprises_en_memoire


def parametres() -> ServiceParametres:
    # ⚠️ Pas 95 : le référentiel **du cabinet**, par le point de montage unique, et non plus
    # le fichier commun mémoïsé ici. Voir `service_parametres` dans `referentiel/api.py`.
    return service_parametres()


class LigneDossier(BaseModel):
    """Une ligne de la liste des dossiers, résolue à une date.

    Elle porte les statuts déjà résolus plutôt que les listes de périodes : c'est
    l'écran de portefeuille, et il affiche un régime, pas une histoire. Le détail
    complet se lit sur la fiche du dossier.
    """

    niu: str
    denomination: str
    forme_juridique: str
    activite: str | None
    siege: str | None

    regime: RegimeFiscal
    centre: CentreRattachement
    assujettie_tva: bool
    adherente: bool
    numero_adhesion: str | None
    #: ⚠️ La fin **déjà inscrite** de l'adhésion en cours, borne exclue : le dossier
    #: n'est plus adhérent ce jour-là. `None` si rien n'est résilié. (pas 57)
    #:
    #: Sans elle, l'écran ne pouvait pas savoir qu'une adhésion « en cours » est déjà
    #: résiliée pour le mois prochain, et proposait de la résilier une seconde fois.
    adhesion_jusqu_au: date | None = None
    #: La prise d'effet d'une adhésion **à venir**, déjà inscrite. `None` sinon.
    #:
    #: Sans elle, un dossier admis à compter du mois prochain paraissait « non
    #: adhérent », et l'écran proposait de l'admettre, ce que le domaine refuse.
    adhesion_a_venir: date | None = None
    exercice_courant: str | None


def _resoudre(entreprise: Entreprise, a_la_date: date) -> LigneDossier:
    adhesion = entreprise.adhesion_au(a_la_date)
    exercice = next(
        (e for e in entreprise.exercices if e.ouverture <= a_la_date <= e.cloture), None
    )
    return LigneDossier(
        niu=entreprise.niu,
        denomination=entreprise.denomination,
        forme_juridique=entreprise.forme_juridique,
        activite=entreprise.activite,
        siege=entreprise.siege,
        regime=entreprise.regime_au(a_la_date),
        centre=entreprise.rattachement_au(a_la_date),
        assujettie_tva=entreprise.assujettie_tva_au(a_la_date),
        adherente=adhesion is not None,
        numero_adhesion=adhesion.numero if adhesion else None,
        adhesion_jusqu_au=adhesion.fin if adhesion else None,
        adhesion_a_venir=min(
            (a.debut for a in entreprise.adhesions if a.debut > a_la_date), default=None
        ),
        exercice_courant=exercice.libelle if exercice else None,
    )


def _lire(niu: str) -> Entreprise:
    try:
        return depot().lire(niu)
    except EntrepriseIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence


@routeur.get("/entreprises", summary="Les dossiers du portefeuille, résolus à une date")
def lister_entreprises(
    acces: AccesRequis,
    a_la_date: date = Query(
        ...,
        description=(
            "Date à laquelle les statuts sont résolus. Obligatoire : un régime sans "
            "date n'a pas de sens, et supposer « aujourd'hui » fausserait tout "
            "affichage portant sur un exercice antérieur."
        ),
    ),
    regime: RegimeFiscal | None = Query(None, description="Ne rend que ce régime"),
    centre: CentreRattachement | None = Query(None, description="Ne rend que ce centre"),
) -> list[LigneDossier]:
    exiger(acces, Permission.LIRE_DOSSIER)
    # Restreint, et non refusé : un comptable ne voit que son portefeuille, et
    # une liste vide vaut mieux qu'un écran d'erreur. Voir `restreindre`.
    dossiers = restreindre(acces, depot().lister(), lambda e: e.niu)
    # ⚠️ Pas 86 : un dossier sans statut à cette date n'est pas dans la liste de ce
    # jour-là. Avant, un seul dossier créé après la date demandée faisait tomber tout le
    # portefeuille en erreur 500 (essai : `a_la_date=2019-01-01`, SARL BATIMENT PLUS
    # n'ayant de régime qu'à partir du 15/03/2021). Le cabinet ne connaissait alors ni
    # son régime ni son centre : il ne le suivait pas.
    lignes = []
    for entreprise in dossiers:
        try:
            lignes.append(_resoudre(entreprise, a_la_date))
        except StatutIntrouvable:
            continue
    if regime is not None:
        lignes = [ligne for ligne in lignes if ligne.regime is regime]
    if centre is not None:
        lignes = [ligne for ligne in lignes if ligne.centre is centre]
    return lignes


@routeur.get("/entreprises/{niu}", summary="La fiche complète d'un dossier")
def lire_entreprise(acces: AccesRequis, niu: str) -> Entreprise:
    """Rend le dossier avec **toutes ses périodes**, sans date.

    C'est délibérément l'exception : la fiche montre l'histoire, elle ne la résout
    pas. C'est l'écran où l'on répond à « depuis quand ? » et « pourquoi ? », et
    ces deux questions n'ont de réponse que dans les motifs des périodes.
    """
    exiger_dossier(acces, Permission.LIRE_DOSSIER, niu)
    return _lire(niu)


class StatutsResolus(BaseModel):
    niu: str
    denomination: str
    a_la_date: date
    regime: RegimeFiscal
    centre: CentreRattachement
    assujettie_tva: bool
    adherente: bool
    exercice: Exercice | None
    #: Codes des obligations que le Centre est mandaté pour déposer à cette date.
    #: Un mandat peut être partiel, et déposer hors mandat n'est pas une
    #: négligence de procédure : c'est agir sans qualité.
    obligations_mandatees: list[str]


@routeur.get(
    "/entreprises/{niu}/statuts",
    summary="Les statuts d'un dossier à une date donnée",
    description=(
        "Le seul appel dont un autre écran a besoin pour savoir comment traiter une "
        "pièce : régime, centre, assujettissement, exercice de rattachement et "
        "étendue du mandat, tous résolus à la même date."
    ),
)
def lire_statuts(
    acces: AccesRequis, niu: str, a_la_date: date = Query(...)
) -> StatutsResolus:
    exiger_dossier(acces, Permission.LIRE_DOSSIER, niu)
    entreprise = _lire(niu)
    try:
        regime = entreprise.regime_au(a_la_date)
        centre = entreprise.rattachement_au(a_la_date)
    except StatutIntrouvable as absence:
        # ⚠️ Pas 86 : c'était une erreur 500. 409 et non 404 : le dossier existe,
        # c'est la date qui sort de son histoire, et le message nomme les périodes.
        raise HTTPException(status_code=409, detail=str(absence)) from absence
    mandates = sorted(
        {
            code
            for mandat in entreprise.mandats
            if mandat.couvre(a_la_date)
            for code in mandat.obligations
        }
    )
    return StatutsResolus(
        niu=entreprise.niu,
        denomination=entreprise.denomination,
        a_la_date=a_la_date,
        regime=regime,
        centre=centre,
        assujettie_tva=entreprise.assujettie_tva_au(a_la_date),
        adherente=entreprise.est_adherente_au(a_la_date),
        exercice=next(
            (e for e in entreprise.exercices if e.ouverture <= a_la_date <= e.cloture), None
        ),
        obligations_mandatees=mandates,
    )


@routeur.get(
    "/anomalies",
    summary="Les identifiants douteux du portefeuille",
    description=(
        "Contrôle le NIU et le RCCM de chaque dossier contre les formats du "
        "référentiel en vigueur à la date demandée. Destiné à la reprise d'un "
        "portefeuille : mieux vaut découvrir un NIU malformé lors d'une revue que "
        "le jour où l'administration refuse une déclaration."
    ),
)
def lister_anomalies(
    acces: AccesRequis, a_la_date: date = Query(...)
) -> list[AnomalieIdentifiant]:
    exiger(acces, Permission.LIRE_DOSSIER)
    dossiers = restreindre(acces, depot().lister(), lambda e: e.niu)
    return verifier_portefeuille(dossiers, parametres(), a_la_date)


class DemandeDeRegime(BaseModel):
    """Ce qu'un réviseur soumet pour inscrire un changement de régime.

    ⚠️ `extra="forbid"`. Tout ce qui n'est pas ici se déduit : la date de fin de
    l'ancien statut est la date d'effet du nouveau, et rien d'autre dans
    l'histoire du dossier ne bouge. Accepter un champ de plus ouvrirait la porte à
    une histoire réécrite par le corps de la requête.
    """

    model_config = ConfigDict(extra="forbid")

    regime: RegimeFiscal
    a_compter_du: date
    cause: MotifChangement
    #: ⚠️ **Un texte, deux usages.** Il est transmis au contrôle d'autorisation,
    #: qui l'exige et le porte au journal d'audit, **et** il devient la précision
    #: du nouveau statut. Deux champs distincts auraient invité à écrire « voir
    #: audit » dans l'un et la vraie raison dans l'autre.
    #:
    #: Trente caractères : au-dessous, on écrit « RAS », et un statut justifié par
    #: « RAS » ne dit rien au vérificateur qui demande pourquoi ce dossier est
    #: passé au réel ce jour-là.
    justification: str = Field(min_length=30, max_length=500)


@routeur.post(
    "/entreprises/{niu}/regimes",
    summary="Inscrire un changement de régime fiscal",
    status_code=201,
    responses={
        403: {"description": "Habilitation insuffisante, ou justification manquante"},
        404: {"description": "Dossier inconnu ou hors périmètre"},
        409: {"description": "Le changement contredit l'histoire du dossier"},
    },
)
def inscrire_regime(
    acces: AccesRequis, niu: str, demande: DemandeDeRegime
) -> StatutsResolus:
    """Ferme le régime en cours et ouvre le nouveau, à la date d'effet.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LA PREMIÈRE ROUTE QUI ÉCRIT DANS LE PORTEFEUILLE.**

    Le contexte était entièrement en lecture seule. Le pas 44 a montré ce que cela
    coûtait : la surveillance voyait un dossier franchir le seuil, et le
    reclassement devait se poser à la main en base, hors de tout contrôle.

    ⚠️ **`409` ET NON `422` POUR UN REFUS DE DOMAINE.** La requête est bien formée ;
    ce sont les faits du dossier qui s'y opposent — un exercice clos traversé, une
    période probatoire en cours. Un `422` ferait chercher une faute de saisie dans
    un champ qui n'en contient pas.

    ⚠️ **LA RÉPONSE EST LE DOSSIER RÉSOLU À LA DATE D'EFFET**, pas l'entité brute :
    c'est ce que le réviseur veut vérifier d'un coup d'œil, le régime qui s'appliquera
    ce jour-là et les obligations qui en découlent.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger_dossier(acces, Permission.INSCRIRE_STATUT, niu, motif=demande.justification)
    entreprise = _lire(niu)
    try:
        nouvelle = inscrire_un_regime(
            entreprise,
            demande.regime,
            demande.a_compter_du,
            demande.cause,
            justification=demande.justification,
            # ⚠️ Une question, pas une réponse : lue seulement si la règle en a
            # besoin. Voir la docstring de `inscrire_un_regime`.
            exercices_probatoires=lambda jour: int(
                parametres().valeur_numerique("IGS_PERIODE_PROBATOIRE_EXERCICES", jour)
            ),
        )
        depot().enregistrer(nouvelle)
    except (InscriptionRefusee, HistoireAmputee) as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    except AucuneVersionApplicable as silence:
        # ⚠️ Le référentiel ne dit rien de la règle à cette date. Ce n'est pas une
        # panne du produit, c'est un fait à nommer : refuser d'inscrire plutôt que
        # de supposer une durée de période probatoire.
        raise HTTPException(
            status_code=409,
            detail=f"le référentiel ne permet pas de trancher : {silence}",
        ) from silence
    return lire_statuts(acces, niu, a_la_date=demande.a_compter_du)


class DemandeDAdhesion(BaseModel):
    """Ce qu'un réviseur soumet pour admettre un dossier au Centre.

    ⚠️ **Aucun numéro dans la demande.** Il est attribué par le registre, comme le
    numéro d'une écriture : un numéro saisi à la main finit en double.
    """

    model_config = ConfigDict(extra="forbid")

    a_compter_du: date
    #: Le chiffre d'affaires de référence, déclaré à l'admission avec sa source.
    #: Le portefeuille ne lit pas les livres, et l'entreprise qui adhère n'en a
    #: souvent encore aucun chez le Centre ; la liasse le vérifiera à chaque clôture.
    chiffre_affaires_declare: Decimal = Field(ge=0)
    source_du_chiffre: str = Field(min_length=5, max_length=200)
    justification: str = Field(min_length=30, max_length=500)


class DemandeDeResiliation(BaseModel):
    """Ce qu'un réviseur soumet pour mettre fin à une adhésion."""

    model_config = ConfigDict(extra="forbid")

    au: date
    #: Portée au journal d'audit : la période ne la conserve pas, voir
    #: `resilier_l_adhesion`.
    justification: str = Field(min_length=30, max_length=500)


def _seuil_d_adhesion(a_la_date: date) -> ParametreResolu | None:
    """Le seuil de l'article 118 à cette date, ou `None` si le référentiel se tait."""
    try:
        return parametres().resoudre("SEUIL_ADHESION_CGA", a_la_date)
    except AucuneVersionApplicable:
        return None


@routeur.post(
    "/entreprises/{niu}/adhesions",
    summary="Admettre un dossier au Centre",
    status_code=201,
    responses={
        403: {"description": "Habilitation insuffisante, ou justification manquante"},
        404: {"description": "Dossier inconnu ou hors périmètre"},
        409: {"description": "L'adhésion contredit le dossier ou le seuil d'adhésion"},
    },
)
def admettre(acces: AccesRequis, niu: str, demande: DemandeDAdhesion) -> StatutsResolus:
    """Inscrit l'adhésion, numérotée par le registre, à la date d'effet.

    ⚠️ **L'HORLOGE EST LUE ICI, ET SEULEMENT ICI.** Le domaine reçoit le jour
    d'inscription : c'est la route qui sait quel jour on est, et c'est ce qui rend
    la règle d'antidate éprouvable.
    """
    exiger_dossier(acces, Permission.INSCRIRE_STATUT, niu, motif=demande.justification)
    entreprise = _lire(niu)
    portefeuille = depot().lister()
    seuil = _seuil_d_adhesion(demande.a_compter_du)
    try:
        adherente = inscrire_une_adhesion(
            entreprise,
            demande.a_compter_du,
            numero=prochain_numero_d_adhesion(portefeuille, demande.a_compter_du.year),
            inscrite_le=maintenant().date(),
            chiffre_affaires_declare=demande.chiffre_affaires_declare,
            source_du_chiffre=demande.source_du_chiffre,
            seuil_adhesion=seuil.valeur_decimale if seuil else None,
            borne_adhesion=seuil.borne if seuil else None,
            numeros_existants=[
                a.numero for d in portefeuille for a in d.adhesions if a.numero
            ],
        )
        depot().enregistrer(adherente)
    except (AdhesionRefusee, HistoireAmputee) as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    return lire_statuts(acces, niu, a_la_date=demande.a_compter_du)


@routeur.post(
    "/entreprises/{niu}/adhesions/resiliation",
    summary="Résilier l'adhésion en cours",
    responses={
        403: {"description": "Habilitation insuffisante, ou justification manquante"},
        404: {"description": "Dossier inconnu ou hors périmètre"},
        409: {"description": "La résiliation contredit le dossier"},
    },
)
def resilier(acces: AccesRequis, niu: str, demande: DemandeDeResiliation) -> StatutsResolus:
    """Ferme l'adhésion en cours ; le dossier n'est plus adhérent le jour `au`."""
    exiger_dossier(acces, Permission.INSCRIRE_STATUT, niu, motif=demande.justification)
    try:
        resiliee = resilier_l_adhesion(_lire(niu), demande.au)
        depot().enregistrer(resiliee)
    except (AdhesionRefusee, HistoireAmputee) as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    return lire_statuts(acces, niu, a_la_date=demande.au)


# ── La recherche globale (pas 93) ────────────────────────────────────────────
#
# Une source de la recherche fédérée, déclarée au registre des services. Voir
# `app/partage/recherche.py` : chaque service cherche chez lui, avec son périmètre.


@routeur.get(
    "/recherche",
    summary="Chercher un dossier du portefeuille",
    responses={422: {"description": "Requête trop courte pour les réglages de recherche"}},
)
def chercher_un_dossier(
    acces: AccesRequis,
    q: str = Query(min_length=1, max_length=100, description="NIU, RCCM, dénomination…"),
) -> ReponseDeRecherche:
    """Par NIU ou RCCM (identifiants), par dénomination, activité ou siège (libellés).

    ⚠️ `restreindre` **avant** de comparer : un comptable qui cherche « AGRO » ne doit
    pas apprendre, par un résultat, qu'un dossier de ce nom existe hors de son
    portefeuille. Le périmètre est celui de la liste des dossiers, ni plus ni moins.
    """
    exiger(acces, Permission.LIRE_DOSSIER)
    reglages = charger_les_reglages_de_recherche(configuration().dossier_referentiel)
    try:
        verifier_la_requete(q, reglages)
    except RequeteTropCourte as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus
    resultats = []
    for entreprise in restreindre(acces, depot().lister(), lambda e: e.niu):
        score = pertinence(
            q,
            identifiants=(entreprise.niu, entreprise.rccm),
            libelles=(entreprise.denomination, entreprise.activite, entreprise.siege),
            longueur_minimale=reglages.longueur_minimale,
        )
        if score is None:
            continue
        resultats.append(
            ResultatDeRecherche(
                nature="Dossier",
                identifiant=entreprise.niu,
                titre=entreprise.denomination,
                detail=" · ".join(
                    x for x in (entreprise.forme_juridique.value, entreprise.siege) if x
                ),
                dossier=entreprise.niu,
                lien=f"/portefeuille/{entreprise.niu}",
                pertinence=score,
            )
        )
    return classer_les_resultats("portefeuille", resultats, reglages)
