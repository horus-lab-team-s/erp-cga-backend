"""Tenir le dossier de démonstration à jour de ses déclarations, par rapport au jour.

─────────────────────────────────────────────────────────────────────────────────
LE DÉFAUT QUE CE MODULE CORRIGE, CONSTATÉ SUR L'ESPACE ADHÉRENT

Le jeu d'essai ne portait que six accusés, tous de 2024 (`accuses_demo.py`). Le
dossier de démonstration, SARL BATIMENT PLUS, affichait donc en septembre 2026
« 81 périodes en retard », dont des retards de 584 jours, et des documents déposés
par le cabinet datés de mars 2024. Montré à un prospect, le client modèle du
cabinet avait l'air en infraction depuis deux ans, et son dossier abandonné.

Ce n'était pas un scénario à conserver : c'était un jeu d'essai écrit à une date,
et que le calendrier avait dépassé. Écrire d'autres dates en dur n'aurait fait que
repousser le problème de deux ans.

⚠️ LES ACCUSÉS SONT CALCULÉS SUR L'ÉCHÉANCIER, PAR RAPPORT AU JOUR

Pour chaque obligation que l'échéancier du dossier connaît — la même fonction que
l'écran de l'adhérent —, un accusé est consigné si l'échéance est passée depuis
plus de `DELAI_DE_GRACE`. Ce qui tombe dans le dernier mois reste ouvert : le
dossier montre un ou deux retards récents, crédibles, et les relances ont encore
quelque chose à montrer. Aucune période n'est inventée : on ne dépose que ce que
l'échéancier réclame.

⚠️ PAR LE DOMAINE, JAMAIS PAR LA TABLE

Comme `accuses_demo.py` : un document préparé, puis l'accusé qui lui correspond, à
l'octet près, consignés par `enregistrer_accuse`, qui vérifie la cohérence et
refuse un second dépôt de la même période.

⚠️ LE MOIS QUE LA COMPTABILITÉ DE DÉMONSTRATION TRAITE RESTE OUVERT

Le jeu d'essai porte des écritures d'achat de juillet 2026 : c'est le mois que la
console montre en train d'être déclaré (préparer la TVA, puis consigner l'accusé).
Le déclarer d'office ici cassait ce geste : le dépôt répondait « déjà déclarée ».
La suite l'a vu. Une période MENSUELLE sur laquelle le dossier a des écritures n'est
donc jamais consignée ; les périodes annuelles, elles, le sont, sans quoi la patente
de l'année resterait ouverte pour quelques écritures de juillet.

⚠️ REJOUABLE SANS DOMMAGE

Une période déjà déposée est sautée, et les numéros et montants dérivent de la
période elle-même. Rejouer l'outil un mois plus tard complète le mois écoulé, et
ne touche à rien d'autre.

⚠️ SEULEMENT LES DOSSIERS DU JEU DE DÉMONSTRATION

La liste vient de `PORTEFEUILLE_DEMO`, le jeu versé par l'amorçage, et de nulle
part ailleurs : ce module ne consigne jamais de dépôt pour un vrai dossier, parce
qu'un accusé est une preuve devant l'administration.

⚠️ **TOUT LE PORTEFEUILLE, ET NON LE SEUL DOSSIER DE L'ADHÉRENT.** La première
version ne recalait que SARL BATIMENT PLUS, celui que l'application mobile montre.
Vu sur le tableau de bord de la console : les cinq autres dossiers portaient encore
soixante-treize périodes échues, dont certaines de février — c'est-à-dire que le
CABINET, lui, avait l'air de ne rien déposer pour personne. Or c'est cet écran-là
qu'un prospect regarde en démonstration.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import zlib
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from app.contextes.obligations.adaptateurs.entrant.routes_http import depot_entreprises
from app.contextes.obligations.adaptateurs.sortant.accuses import accuses_du_portail
from app.contextes.obligations.adaptateurs.sortant.catalogue_obligations import (
    DepotTypesObligationMemoire,
)
from app.contextes.obligations.adaptateurs.sortant.effectif import effectif_du_dossier
from app.contextes.obligations.api import ObligationInstance, generer_echeancier
from app.contextes.transverse.adaptateurs.sortant.depots_sql import PortailSql
from app.contextes.transverse.api import LOCATAIRE_PAR_DEFAUT, unite_de_travail
from app.contextes.transverse.domaine.teledeclaration import (
    AccuseReception,
    DocumentATransmettre,
    FormatTransmission,
    ModeDepot,
    Portail,
)
from app.partage.horloge import aujourd_hui
from app.partage.locataire import courant, etabli

__all__ = [
    "DELAI_DE_GRACE",
    "DOSSIER_DE_DEMONSTRATION",
    "dossiers_de_demonstration",
    "accuses_a_consigner",
    "consigner",
    "mettre_la_demonstration_a_jour",
]

#: Le dossier que l'application mobile montre. Il reste nommé : plusieurs cas
#: d'essai s'y réfèrent, et c'est celui dont l'histoire est la plus fournie.
DOSSIER_DE_DEMONSTRATION = "M081234567890P"


#: Le dossier qu'on laisse VOLONTAIREMENT en retard.
#:
#: ⚠️ UN CABINET DONT AUCUN CLIENT NE TRAÎNE N'A RIEN À PILOTER. Recaler tout le
#: portefeuille a d'abord éteint les écrans qui existent pour cela : la vue
#: risque, la file d'anomalies, les mesures de direction, et jusqu'aux relances.
#: Mesuré : un cas d'essai des décisions de direction s'est mis à échouer, parce
#: que la mesure « exiger une régularisation » n'est prévue que pour un dossier à
#: surveiller, et que plus aucun ne l'était.
#:
#: Ce dossier-là reste donc en retard, comme un vrai client qui tarde. Les cinq
#: autres sont à jour : c'est la proportion d'un cabinet qui travaille.
DOSSIER_EN_RETARD_VOULU = "P027788990011M"


def dossiers_de_demonstration() -> tuple[str, ...]:
    """Les NIU du jeu d'essai, et eux seuls.

    ⚠️ Lus à l'appel et non figés dans une constante : le jeu d'essai peut
    grandir, et un dossier ajouté à l'amorçage doit être recalé comme les autres
    sans que personne ne pense à venir modifier ce fichier.
    """
    from app.contextes.portefeuille.api import PORTEFEUILLE_DEMO

    return tuple(niu for niu in PORTEFEUILLE_DEMO if niu != DOSSIER_EN_RETARD_VOULU)

#: Ce qui échoit dans ce délai reste ouvert : c'est le retard récent que le dossier
#: montre. Trente jours laissent ouvert le dernier mois déclaratif, pas plus.
DELAI_DE_GRACE = timedelta(days=30)

#: L'histoire commence où `accuses_demo.py` s'arrête : janvier et février 2024 y
#: sont déjà déposés. Rien avant : le dossier n'a pas à porter quatre ans d'accusés
#: pour être crédible.
DEBUT_DE_L_HISTOIRE = date(2024, 1, 1)

#: Le réviseur qui consigne, le même que dans `accuses_demo.py`.
_REVISEUR = "C-003"

#: Des montants de l'ordre de ceux de 2024, pour une SARL du bâtiment de trois
#: salariés. ⚠️ Pas de montant pour la DSF : c'est une déclaration, pas un
#: paiement, et l'écran afficherait « 0 F » (constaté sur l'appareil).
_MONTANTS = {
    "TVA": (1_180_000, 260_000),
    "IRPP_SALAIRES": (318_750, 0),
    "CNPS": (412_500, 0),
    "IRPP_ACOMPTE": (152_000, 48_000),
    "PATENTE": (485_000, 0),
}


def _graine(obligation: ObligationInstance) -> int:
    """Un nombre stable pour une période D'UN DOSSIER : même dossier, même période,
    même numéro et même montant.

    ⚠️ LE DOSSIER ENTRE DANS LA GRAINE, et c'est une correction. Sans lui, deux
    dossiers du portefeuille qui doivent la même obligation sur la même période
    recevaient le MÊME numéro d'accusé — la base l'a refusé, et elle a eu raison :
    un numéro d'accusé identifie un dépôt devant l'administration.
    """
    cle = (
        f"{obligation.entreprise}|{obligation.code_obligation}"
        f"|{obligation.periode_debut}|{obligation.periode_fin}"
    )
    return zlib.crc32(cle.encode())


def _montant(obligation: ObligationInstance) -> Decimal | None:
    base = _MONTANTS.get(obligation.code_obligation)
    if base is None:
        return None
    socle, amplitude = base
    variation = (_graine(obligation) % (amplitude + 1)) if amplitude else 0
    # Arrondi à la centaine de francs, comme un avis d'imposition.
    return Decimal((socle + variation) // 100 * 100)


def _numero(obligation: ObligationInstance, portail: Portail) -> str:
    guichet = "CNPS" if portail is Portail.CNPS_DIPE else "DGI"
    fin = obligation.periode_fin
    suffixe = _graine(obligation) % 900_000 + 100_000
    if obligation.periode_debut.month == 1 and fin.month == 12:
        return f"{guichet}-{fin.year}-{obligation.code_obligation[:3]}-{suffixe}"
    return f"{guichet}-{fin.year}-{fin.month:02d}-{suffixe}"


def _depose_le(obligation: ObligationInstance) -> datetime:
    """Deux jours avant l'échéance, un matin : un cabinet qui dépose à temps."""
    heure = time(8 + _graine(obligation) % 3, _graine(obligation) % 60)
    return datetime.combine(obligation.echeance - timedelta(days=2), heure)


def _consignation(
    obligation: ObligationInstance, portail: Portail
) -> tuple[DocumentATransmettre, AccuseReception]:
    montant = _montant(obligation)
    document = DocumentATransmettre(
        portail=portail,
        code_document=obligation.code_obligation,
        entreprise=obligation.entreprise,
        periode_debut=obligation.periode_debut,
        periode_fin=obligation.periode_fin,
        format=FormatTransmission.SAISIE_MANUELLE,
        contenu=(
            f"{obligation.code_obligation} {obligation.periode_debut:%Y-%m-%d}/"
            f"{obligation.periode_fin:%Y-%m-%d} déposé au guichet"
        ),
    )
    accuse = AccuseReception(
        numero=_numero(obligation, portail),
        portail=portail,
        reference_document=document.reference,
        depose_le=_depose_le(obligation),
        mode=ModeDepot.MANUEL,
        empreinte_deposee=document.empreinte,
        depose_par=_REVISEUR,
        montant_constate=montant,
        precision=(
            "Déposé au guichet CNPS Bonabéri"
            if portail is Portail.CNPS_DIPE
            else "Télédéclaration DGI, accusé imprimé"
        ),
    )
    return document, accuse


def _mois_comptabilises(dossier: str) -> set[tuple[int, int]]:
    """Les mois sur lesquels CE dossier a des écritures : ceux que la démonstration
    montre en cours de déclaration. Voir l'en-tête."""
    from app.contextes.comptabilite.api import DepotEcrituresSql
    from app.contextes.transverse.api import session_de_travail

    session = session_de_travail()
    if session is None:
        return set()
    ecritures = DepotEcrituresSql(session, courant(), dossier).toutes()
    return {(e.date_operation.year, e.date_operation.month) for e in ecritures}


def accuses_a_consigner(jour: date) -> list[tuple[DocumentATransmettre, AccuseReception]]:
    """Ce qui manque au portefeuille de démonstration pour être à jour au `jour` dit.

    À appeler dans une unité de travail : les dossiers, l'effectif et les accusés
    déjà consignés se lisent sur sa session.
    """
    types = DepotTypesObligationMemoire().charger(jour)
    portails = {t.code: t.portail for t in types}
    deja = accuses_du_portail()
    limite = jour - DELAI_DE_GRACE
    a_consigner: list[tuple[DocumentATransmettre, AccuseReception]] = []
    for niu in dossiers_de_demonstration():
        try:
            dossier = depot_entreprises().lire(niu)
        except Exception:
            # ⚠️ Un dossier du jeu d'essai absent de la base n'arrête pas les
            # autres : la base peut avoir été amorcée avant qu'il n'existe.
            continue
        en_traitement = _mois_comptabilises(niu)
        for exercice in dossier.exercices:
            if exercice.cloture < DEBUT_DE_L_HISTOIRE or exercice.ouverture > jour:
                continue
            for obligation in generer_echeancier(
                dossier,
                types,
                exercice,
                emploie_sur=effectif_du_dossier(dossier.niu),
                accuse_de=deja,
            ):
                mensuelle = (obligation.periode_fin - obligation.periode_debut).days <= 31
                debut = obligation.periode_debut
                if (
                    obligation.deposee
                    or debut < DEBUT_DE_L_HISTOIRE
                    or obligation.echeance > limite
                    or (mensuelle and (debut.year, debut.month) in en_traitement)
                ):
                    continue
                a_consigner.append(
                    _consignation(obligation, portails[obligation.code_obligation])
                )
    return a_consigner


def consigner(session, locataire: str, jour: date) -> int:
    """Consigne ce qui manque, dans la transaction de l'appelant. Rend le nombre.

    ⚠️ Le locataire est établi ICI, et non laissé à l'appelant : l'amorçage reçoit
    son locataire en paramètre sans l'établir, et l'échéancier lit sous le
    cloisonnement, qui refuse toute lecture sans locataire. Constaté dans la suite :
    un amorçage appelé hors requête échouait, et laissait la base à moitié versée.
    """
    with etabli(locataire):
        accuses = accuses_a_consigner(jour)
    for document, accuse in accuses:
        PortailSql(session, locataire, accuse.portail).enregistrer_accuse(
            accuse, document=document
        )
    return len(accuses)


def mettre_la_demonstration_a_jour(locataire: str = LOCATAIRE_PAR_DEFAUT) -> int:
    """Le geste complet, pour une base déjà amorcée. Rend le nombre d'accusés ajoutés.

    ⚠️ Le locataire est ÉTABLI d'abord, comme l'intergiciel le fait pour une requête :
    sans lui, le cloisonnement refuse toute lecture, et c'est voulu.
    """
    with etabli(locataire), unite_de_travail(locataire) as boutique:
        if boutique.session is None:
            raise RuntimeError(
                "la démonstration ne se met à jour qu'en persistance PostgreSQL : en "
                "mémoire, elle repart de zéro à chaque démarrage."
            )
        return consigner(boutique.session, locataire, aujourd_hui())
