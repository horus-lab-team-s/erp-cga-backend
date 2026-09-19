"""Surface publique du contexte D · Conformité documentaire.

**Les autres contextes n'importent QUE ce module.**

Ce que la conformité promet aux autres contextes :

* `MoteurConformite.controler(facture)` rend un `RapportConformite` **immuable** :
  réévaluer produit un nouveau rapport, jamais une mise à jour de l'ancien ;
* chaque `Constat` porte sa **conséquence fiscale déclarative** et son enjeu chiffré.
  La conformité ne modifie rien : elle décrit. C'est la comptabilité qui pose
  l'attribut fiscal sur la ligne d'écriture, et les obligations qui rejettent la TVA
  du mois ;
* le rapport conserve les **paramètres du référentiel employés**, avec leur valeur et
  leur date d'effet, ce qui le rend reproductible des années plus tard.

Consommateurs prévus : `collecte` (contrôle à la réception d'une pièce),
`comptabilite` (héritage de l'attribut fiscal), `obligations` (TVA rejetée du mois),
`cloture` (réintégrations du tableau de passage), `creation_entreprise` (même moteur,
autre jeu de règles pour les checklists de pièces), `pilotage` (score de risque).
"""

from __future__ import annotations

from datetime import date as _date
from decimal import Decimal as _Decimal
from functools import lru_cache as _lru_cache

from pydantic import BaseModel as _BaseModel

from app.contextes.conformite.adaptateurs.entrant.presentateur_verdict import (
    Verdict,
    composer_verdict,
)
from app.contextes.conformite.adaptateurs.sortant.depot_regles_yaml import (
    DepotReglesYaml,
    charger_regles,
)
from app.contextes.conformite.adaptateurs.sortant.depots_ecarts import (
    depot_des_ecarts,
    vider_les_ecarts,
)
from app.contextes.conformite.adaptateurs.sortant.depots_regles_du_cabinet import (
    depot_des_regles_du_cabinet,
    vider_les_regles_du_cabinet,
)
from app.contextes.conformite.adaptateurs.sortant.donnees_demo import (
    FACTURES_DEMO,
    REFERENCES_CANONIQUES,
)
from app.contextes.conformite.adaptateurs.sortant.politique_ecarts_yaml import (
    charger_la_politique_d_ecart,
)
from app.contextes.conformite.application.ecarts import (
    EtatDUnEcart,
    appliquer_les_ecarts,
    etat_des_ecarts,
    joindre_une_piece_d_appui,
    lever_un_ecart,
    proposer_un_ecart,
    trancher_un_ecart,
)
from app.contextes.conformite.application.moteur_conformite import MoteurConformite
from app.contextes.conformite.domaine.ecarts import (
    EcartDeConstat,
    EcartIntrouvable,
    EcartRefuse,
    MotifInsuffisant,
    PolitiqueDEcart,
    RevueDesRegles,
    StatutEcart,
    a_regulariser,
    piece_appui_exigee_pour,
)
from app.contextes.conformite.domaine.entites import (
    ConsequenceFiscale,
    Constat,
    ConstatEcarte,
    ContexteControle,
    Document,
    FactureAControler,
    LigneFacture,
    ModeReglement,
    Montants,
    Partie,
    RapportConformite,
    RegimeEmetteur,
    Regle,
    RegleEnEchec,
    Reglement,
    Severite,
    TypeDocument,
)
from app.contextes.conformite.domaine.qualite_des_regles import (
    StatistiqueDeRegle,
    mesurer_les_regles,
)
from app.partage.horloge import maintenant as _maintenant

__all__ = [
    "DerogationARegulariser",
    "JournalDesDerogations",
    "QualiteDesRegles",
    "derogations_du_perimetre",
    "lire_le_journal_des_derogations",
    "mesurer_la_qualite_des_regles",
    "noms_des_comptes",
    # Contrôle
    "MoteurConformite",
    "DepotReglesYaml",
    "moteur_par_defaut",
    "charger_regles",
    # Jeu de démonstration — les vingt-neuf factures du flux entrant de
    # juillet 2026. Exposé parce que la collecte et le pilotage en dérivent
    # leurs propres jeux, et qu'ils ne doivent pas fouiller les adaptateurs.
    "FACTURES_DEMO",
    "REFERENCES_CANONIQUES",
    # Entrée
    "ContexteControle",
    "Document",
    "FactureAControler",
    "LigneFacture",
    "ModeReglement",
    "Montants",
    "Partie",
    "RegimeEmetteur",
    "Reglement",
    "TypeDocument",
    # Sortie
    "ConsequenceFiscale",
    "Constat",
    "RapportConformite",
    "RegleEnEchec",
    "Severite",
    # Écarts de constats (pas 92)
    "ConstatEcarte",
    "EcartDeConstat",
    "EcartIntrouvable",
    "EcartRefuse",
    "EtatDUnEcart",
    "MotifInsuffisant",
    "PolitiqueDEcart",
    "StatutEcart",
    "appliquer_les_ecarts",
    "depot_des_ecarts",
    "etat_des_ecarts",
    "a_regulariser",
    "joindre_une_piece_d_appui",
    "lever_un_ecart",
    "piece_appui_exigee_pour",
    "politique_d_ecart",
    "proposer_un_ecart",
    "rapport_arbitre",
    "trancher_un_ecart",
    "vider_les_ecarts",
    # Règles du cabinet (pas 97)
    "depot_des_regles_du_cabinet",
    "regles_du_cabinet_en_vigueur",
    "vider_les_regles_du_cabinet",
    # Règles
    "Regle",
    # Restitution
    "Verdict",
    "composer_verdict",
]


def moteur_par_defaut() -> MoteurConformite:
    """Le moteur monté sur le référentiel en vigueur.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **POURQUOI CE MONTAGE VIT DANS LA SURFACE PUBLIQUE**

    Il vivait dans l'adaptateur entrant du contexte, et n'y servait qu'à ses
    propres routes. Le jour où la comptabilité a eu besoin de contrôler une
    facture pour en proposer l'écriture, elle a recopié les trois lignes — et le
    test d'architecture l'a refusée : *on n'entre chez l'autre que par sa surface
    publique.*

    Il avait raison, et pas pour une raison de forme. Un second montage aurait
    divergé du premier au premier changement de dépôt : la conformité aurait
    contrôlé avec un jeu de règles, la comptabilité avec un autre, et les deux
    écrans auraient donné deux verdicts sur la même facture.

    ⚠️ **Mémoïsé.** Les règles sont sur disque et ne changent qu'au déploiement ou
    par un geste d'exploitation. Les relire à chaque contrôle ferait deux lectures
    de fichiers sur le chemin d'un écran de saisie.
    ─────────────────────────────────────────────────────────────────────────────
    """
    from app.infrastructure.config import configuration

    return _monter(configuration().dossier_referentiel)


def _monter(referentiel) -> MoteurConformite:
    # ⚠️ Importés ici et non en tête : le référentiel est un **autre contexte**,
    # et l'importer au chargement ferait dépendre la surface publique de la
    # conformité de l'ordre dans lequel les modules se chargent.
    from app.contextes.referentiel.api import service_parametres

    # ⚠️ Pas 95 : les **règles** restent mémoïsées (des fichiers, qui ne changent qu'au
    # déploiement) ; les **paramètres** sont ceux du cabinet, lus à chaque montage. Le moteur
    # entier était mémoïsé : un cabinet qui valide un seuil aurait contrôlé avec l'ancien
    # jusqu'au redémarrage, pendant que l'échéancier employait le nouveau.
    return MoteurConformite(
        regles=[*_regles(referentiel), *regles_du_cabinet_en_vigueur()],
        parametres=service_parametres(),
    )


def regles_du_cabinet_en_vigueur() -> list[Regle]:
    """Les règles que le cabinet courant a construites **et validées** (pas 97).

    Lues à chaque montage, comme ses paramètres : une règle validée entre au contrôle
    suivant. Hors de tout cabinet (un script, un amorçage), aucune.
    """
    from app.partage.locataire import courant_ou_none

    if courant_ou_none() is None:
        return []
    return [
        r
        for r in (p.regle_en_vigueur() for p in depot_des_regles_du_cabinet().toutes())
        if r is not None
    ]


@_lru_cache
def _regles(referentiel):
    return DepotReglesYaml(referentiel / "regles").charger()


def politique_d_ecart() -> PolitiqueDEcart:
    """La politique d'écart du cabinet, lue au référentiel en vigueur (pas 92).

    Relue à chaque appel, **sans mémoïsation** : c'est un petit fichier, et un cabinet
    qui durcit sa politique doit la voir s'appliquer au prochain écran, pas au prochain
    redémarrage. Voir l'en-tête de `application/ecarts.py` sur les écarts suspendus.
    """
    from app.infrastructure.config import configuration

    return charger_la_politique_d_ecart(configuration().dossier_referentiel)


def rapport_arbitre(rapport: RapportConformite, dossier: str | None) -> RapportConformite:
    """Le rapport tel que le cabinet l'a arbitré : les écarts effectifs appliqués.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **TOUT CONSOMMATEUR QUI DÉCIDE SUR UN RAPPORT PASSE PAR ICI** (pas 92)

    Un écart que la conformité afficherait mais que la comptabilité ignorerait serait
    le pire des états : l'écran dirait « écarté », la proposition d'écriture rejetterait
    quand même la TVA, et le comptable irait saisir à la main pour « corriger ».

    Sans dossier connu (une facture contrôlée avant d'être rattachée), aucun écart ne
    peut s'appliquer : un écart se pose toujours sur la pièce **d'un dossier**.
    ─────────────────────────────────────────────────────────────────────────────
    """
    if dossier is None:
        return rapport
    ecarts = depot_des_ecarts().pour_la_piece(dossier, rapport.reference_document)
    if not ecarts:
        return rapport
    return appliquer_les_ecarts(rapport, ecarts, politique_d_ecart())


# ── La revue de conformité, lisible par un voisin (pas 106) ───────────────────
#
# ─────────────────────────────────────────────────────────────────────────────
# POURQUOI CES LECTURES ONT QUITTÉ LA ROUTE
#
# Le journal des dérogations et la qualité des règles (pas 99) étaient calculés dans le
# corps des routes. Le rapport mensuel de la direction (pas 106) doit reprendre **exactement**
# les mêmes chiffres : la maquette l'exige (« pas de second calcul, pas d'écart possible
# entre l'écran et le document de comité »). Recopier le calcul dans le pilotage l'aurait
# violé au premier changement ; importer la route depuis le pilotage aurait enfreint la règle
# qui veut qu'on n'entre chez un voisin que par sa surface publique.
#
# Les deux lectures vivent donc ici, et la route comme le rapport les appellent. Le contrôle
# des permissions reste à l'appelant ; le **périmètre**, lui, est appliqué ici, par l'accès
# passé en argument.
# ─────────────────────────────────────────────────────────────────────────────


class DerogationARegulariser(_BaseModel):
    """Un écart qui exige une pièce d'appui, n'en a pas, et dont le délai est écoulé (pas 118)."""

    identifiant: str
    dossier: str
    reference_document: str
    code_regle: str
    severite: str
    propose_le: _date
    propose_par: str
    jours: int


class JournalDesDerogations(_BaseModel):
    derogations: list[EcartDeConstat]
    #: Le nom en clair de chaque auteur : le journal est lu par un vérificateur qui ne
    #: connaît pas les identifiants de comptes.
    auteurs: dict[str, str]
    total: int
    effectives: int
    #: Pas 118 : celles qui exigent une pièce d'appui et n'en ont pas, délai écoulé. La revue
    #: trimestrielle commence par là.
    a_regulariser: list[DerogationARegulariser] = []
    #: Le délai que le cabinet s'est donné, pour que l'écran puisse le dire.
    delai_de_regularisation_jours: int = 30
    #: La somme des enjeux des dérogations **effectives** : ce que le cabinet a accepté de ne
    #: pas faire payer aux dossiers. Montants relevés au moment de chaque écart.
    enjeu_leve: _Decimal


class QualiteDesRegles(_BaseModel):
    du: _date
    au: _date
    pieces_controlees: int
    constats: int
    ecartes: int
    taux_global: float | None
    regles: list[StatistiqueDeRegle]
    seuils: RevueDesRegles


def noms_des_comptes() -> dict[str, str]:
    from app.contextes.transverse.api import atelier

    return {c.identifiant: f"{c.prenom} {c.nom}" for c in atelier().comptes.lister()}


def derogations_du_perimetre(
    acces,
    *,
    regle: str | None = None,
    auteur: str | None = None,
    dossier: str | None = None,
    statut: str | None = None,
    du: _date | None = None,
    au: _date | None = None,
) -> list[EcartDeConstat]:
    """Les écarts du périmètre, du plus récent au plus ancien. `du` et `au` bornent la date de
    **proposition** (pas 106 : « les dérogations du mois » du rapport mensuel)."""
    from app.contextes.transverse.api import restreindre

    ecarts = restreindre(acces, depot_des_ecarts().toutes(), lambda e: e.dossier)
    return [
        e
        for e in reversed(ecarts)
        if (regle is None or e.code_regle == regle)
        and (auteur is None or e.propose_par == auteur)
        and (dossier is None or e.dossier == dossier)
        and (statut is None or e.statut.value == statut)
        and (du is None or e.propose_le.date() >= du)
        and (au is None or e.propose_le.date() <= au)
    ]


def lire_le_journal_des_derogations(acces, **filtres) -> JournalDesDerogations:
    derogations = derogations_du_perimetre(acces, **filtres)
    effectives = [e for e in derogations if e.statut is StatutEcart.EFFECTIF]
    noms = noms_des_comptes()
    politique = politique_d_ecart()
    jour = _maintenant().date()
    retards = [
        DerogationARegulariser(
            identifiant=e.identifiant,
            dossier=e.dossier,
            reference_document=e.reference_document,
            code_regle=e.code_regle,
            severite=e.severite.value,
            propose_le=e.propose_le.date(),
            propose_par=noms.get(e.propose_par, e.propose_par),
            jours=(jour - e.propose_le.date()).days,
        )
        for e in derogations
        if a_regulariser(e, politique, jour)
    ]
    return JournalDesDerogations(
        derogations=derogations,
        a_regulariser=sorted(retards, key=lambda r: -r.jours),
        delai_de_regularisation_jours=politique.delai_de_regularisation_jours,
        auteurs={
            c: noms.get(c, c)
            for e in derogations
            for c in (e.propose_par, e.tranche_par, e.leve_par)
            if c
        },
        total=len(derogations),
        effectives=len(effectives),
        enjeu_leve=sum((e.enjeu or _Decimal(0) for e in effectives), _Decimal(0)),
    )


def mesurer_la_qualite_des_regles(acces, du: _date, au: _date) -> QualiteDesRegles:
    """Pour chaque règle en vigueur, sur les pièces du périmètre émises entre `du` et `au` :
    constats émis (rapport brut), écartés (rapport arbitré), et la lecture selon les seuils."""
    from app.contextes.transverse.api import restreindre

    politique = politique_d_ecart()
    moteur_ = moteur_par_defaut()
    depot = depot_des_ecarts()
    controles = []

    def destinataire(facture):
        return facture.destinataire.niu if facture.destinataire is not None else None

    for facture in restreindre(acces, FACTURES_DEMO.values(), destinataire):
        if not (du <= facture.document.date_emission <= au):
            continue
        brut = moteur_.controler(facture)
        dossier = destinataire(facture)
        ecarts = depot.pour_la_piece(dossier, brut.reference_document) if dossier else []
        controles.append((brut, appliquer_les_ecarts(brut, ecarts, politique) if ecarts else brut))
    statistiques = mesurer_les_regles(moteur_.regles, controles, politique.revue_des_regles)
    constats = sum(s.constats for s in statistiques)
    ecartes = sum(s.ecartes for s in statistiques)
    return QualiteDesRegles(
        du=du,
        au=au,
        pieces_controlees=len(controles),
        constats=constats,
        ecartes=ecartes,
        taux_global=None if constats == 0 else round(ecartes / constats, 4),
        regles=statistiques,
        seuils=politique.revue_des_regles,
    )
