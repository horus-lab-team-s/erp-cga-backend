"""Le dossier de démonstration est à jour de ses déclarations, au jour où l'on regarde.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Le jeu d'essai ne portait que des accusés de 2024 : en septembre 2026, l'espace de
l'adhérent de démonstration affichait « 81 périodes en retard », dont des retards de
584 jours. Le client modèle du cabinet avait l'air en infraction depuis deux ans.

  1. après l'amorçage, il ne reste que des retards RÉCENTS : le dernier mois échu, et
     le mois que la comptabilité de démonstration montre en cours de déclaration ;
  2. rejouer le recalage le même jour n'ajoute rien ;
  3. aucun autre dossier n'est touché : un accusé est une preuve.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, timedelta

from app.contextes.transverse.api import unite_de_travail
from app.demonstration_a_jour import (
    DELAI_DE_GRACE,
    DOSSIER_DE_DEMONSTRATION,
    DOSSIER_EN_RETARD_VOULU,
    accuses_a_consigner,
    dossiers_de_demonstration,
    mettre_la_demonstration_a_jour,
)
from app.partage.horloge import maintenant
from app.partage.locataire import etabli
from tests.conftest import exige_postgresql, ouvrir_une_session

pytestmark = exige_postgresql

ADHERENT = "jp.nkoa@batimentplus.cm"
#: Le locataire de la plateforme amorcée par `tests/conftest.py`.
LOCATAIRE = "CGA-BRCG"


def _echeances(client):
    ouvrir_une_session(client, ADHERENT)
    r = client.get(f"/obligations/dossiers/{DOSSIER_DE_DEMONSTRATION}/mes-echeances")
    assert r.status_code == 200, r.text
    return r.json()["echeances"]


def test_il_ne_reste_que_des_retards_recents(plateforme):
    echeances = _echeances(plateforme)
    retards = [e for e in echeances if e["etat"] == "EN_RETARD"]
    assert retards, "le dossier doit encore montrer un retard récent, pour les relances"
    for e in retards:
        # Jamais plus de trois mois : ni les 584 jours d'avant, ni une année oubliée.
        assert e["jours"] <= 3 * DELAI_DE_GRACE.days, e
        assert e["autres_periodes_en_retard"] <= 1, e


def test_rejouer_n_ajoute_rien(plateforme):
    avant = _echeances(plateforme)
    assert mettre_la_demonstration_a_jour(LOCATAIRE) == 0
    assert _echeances(plateforme) == avant


def test_seuls_les_dossiers_du_jeu_d_essai_sont_vises(plateforme):
    """Deux mois plus tard, ce qu'il faudrait consigner : les dossiers du jeu d'essai
    et eux seuls, avec des dépôts tous antérieurs au jour dit, et des numéros distincts.

    ⚠️ LES NUMÉROS : deux dossiers qui doivent la même obligation sur la même période
    recevaient le même numéro d'accusé, et la base le refusait — à raison, un numéro
    identifie un dépôt devant l'administration.
    """
    dans_deux_mois = maintenant().date() + timedelta(days=60)
    with etabli(LOCATAIRE), unite_de_travail(LOCATAIRE):
        a_consigner = accuses_a_consigner(dans_deux_mois)
        connus = set(dossiers_de_demonstration())
    assert a_consigner, "deux mois plus tard, de nouvelles périodes sont échues"
    assert DOSSIER_DE_DEMONSTRATION in connus
    numeros = set()
    for document, accuse in a_consigner:
        assert document.entreprise in connus, "un dossier hors du jeu d'essai"
        assert accuse.depose_le.date() <= dans_deux_mois
        assert accuse.reference_document == document.reference
        assert accuse.numero not in numeros, f"numéro en double : {accuse.numero}"
        numeros.add(accuse.numero)


def test_le_mois_en_cours_de_declaration_reste_a_deposer(plateforme):
    """La TVA de juillet 2026 porte les écritures de démonstration : elle doit rester
    à déclarer, pour que la console puisse montrer le parcours de dépôt."""
    juillet = [
        e for e in _echeances(plateforme)
        if e["code_obligation"] == "TVA" and e["periode_debut"] == "2026-07-01"
    ]
    if maintenant().date() > date(2026, 8, 15):
        assert juillet and juillet[0]["etat"] != "DECLAREE", juillet


def test_un_dossier_reste_en_retard_pour_que_le_pilotage_ait_de_quoi_montrer(plateforme):
    """⚠️ UN CABINET DONT AUCUN CLIENT NE TRAÎNE N'A RIEN À PILOTER.

    Recaler tout le portefeuille a d'abord éteint les écrans qui existent pour cela :
    la vue risque, la file d'anomalies, les mesures de direction. Un cas d'essai des
    décisions de direction est tombé le jour où plus aucun dossier n'était « à
    surveiller ». Ce dossier-ci reste donc en retard, comme un vrai client qui tarde.
    """
    client = plateforme
    ouvrir_une_session(client, ADHERENT)  # rattaché au dossier à jour
    assert DOSSIER_EN_RETARD_VOULU not in dossiers_de_demonstration()

    ouvrir_une_session(client, "b.mballa@cga-brcg.cm")
    lignes = client.get(
        f"/obligations/dossiers/{DOSSIER_EN_RETARD_VOULU}/echeancier"
        "?exercice=2026&a_la_date=2026-09-24"
    ).json()
    echues = [
        ligne["obligation"]
        for ligne in lignes
        if ligne["obligation"]["statut"] == "A_FAIRE"
        and ligne["obligation"]["echeance"] < "2026-09-24"
    ]
    assert echues, "le portefeuille n'a plus aucun dossier en retard à piloter"
