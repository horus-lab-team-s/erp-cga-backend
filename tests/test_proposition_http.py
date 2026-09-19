"""De la facture contrôlée à l'écriture proposée, par l'API.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

`proposition_ecriture.py` annonce depuis sa première ligne ce qu'il referme :
*« la boîte de réception mène au rapport, le rapport mène à l'écriture »*.

⚠️ **Aucune route ne l'empruntait.** `proposer_ecriture_achat` n'était appelée que
par le jeu de démonstration. Le comptable disposait de deux gestes sans
passerelle — contrôler une facture d'un côté, saisir une écriture à la main de
l'autre — alors que le moteur sait déduire la seconde de la première, TVA rejetée
comprise.

Le lien entre le verdict et l'imputation reposait donc entièrement sur sa
vigilance, sur l'acte que la route de conformité appelle elle-même *« l'acte
central du produit »*.

CES CAS EMPLOIENT LES CINQ FACTURES CANONIQUES

Celles du § 13.5 du dossier de design, dont les verdicts sont déjà verrouillés
ailleurs. Les reprendre ici relie deux garanties : *ce verdict-là* produit *cette
écriture-là*. Inventer des factures aurait éprouvé le mécanisme sans rien dire de
ce que la plateforme fera sur les cas que le centre a choisis.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.contextes.conformite.adaptateurs.sortant.donnees_demo import FACTURES_DEMO
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO
from app.main import app

#: `a.bouba` porte le rôle comptable du jeu de démonstration : il saisit. Un
#: compte réel, et non une habilitation forgée : ces cas doivent prouver que
#: quelqu'un, dans le cabinet, a réellement ce droit.
COMPTABLE = "a.bouba@cga-brcg.cm"

#: Une facture par verdict. ⚠️ Les références sont celles du dossier de design,
#: et les verdicts sont verrouillés par `test_conformite.py` : si l'un d'eux
#: change, c'est ce fichier-ci qui doit être relu, pas corrigé.
CONFORME = "F-2026-0413"
TVA_REJETEE = "F-2026-0412"
BLOQUANTE = "F-2026-0414"


@pytest.fixture
def comptable():
    with TestClient(app) as client:
        reponse = client.post(
            "/transverse/session",
            json={"courriel": COMPTABLE, "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        assert reponse.status_code == 200, reponse.text
        yield client


@pytest.fixture
def dossier():
    """Le dossier destinataire de la facture, lu **sur la facture elle-même**.

    ⚠️ Il venait du portefeuille, par `GET /entreprises`, et ces cas tombaient en
    suite complète sans tomber isolément : un autre fichier avait vidé le magasin
    mémoire du portefeuille entre-temps.

    Le lire sur la facture supprime la dépendance à un état ambiant, et dit au
    passage la bonne chose : **c'est le destinataire qui décide du dossier**, pas
    le premier de la liste.
    """
    return FACTURES_DEMO[CONFORME].destinataire.niu


def _proposer(client, dossier, reference, **surcharges):
    corps = {"facture": FACTURES_DEMO[reference].model_dump(mode="json"), "journal": "AC"}
    return client.post(
        f"/comptabilite/dossiers/{dossier}/propositions", json={**corps, **surcharges}
    )


class TestLaFactureConforme:
    def test_elle_produit_une_saisie_prete_a_poster(self, comptable, dossier):
        """⚠️ **`saisie` est le corps de la requête suivante, au champ près.**

        Ce n'est pas une commodité : c'est ce qui empêche l'écran de recomposer
        l'écriture à sa façon. Un client qui reconstruirait les lignes à partir du
        rapport referait le travail du moteur, et le referait autrement le jour où
        une règle change.
        """
        reponse = _proposer(comptable, dossier, CONFORME)
        assert reponse.status_code == 200, reponse.text
        corps = reponse.json()

        assert corps["comptabilisable"] is True
        assert corps["empechements"] == []
        saisie = corps["saisie"]
        assert saisie is not None
        assert set(saisie) >= {"journal", "exercice", "date_operation", "libelle", "lignes"}

    def test_l_ecriture_est_equilibree_et_a_trois_lignes(self, comptable, dossier):
        """Une entreprise au réel récupère la TVA : achat, TVA déductible,
        fournisseur."""
        from decimal import Decimal

        saisie = _proposer(comptable, dossier, CONFORME).json()["saisie"]
        assert len(saisie["lignes"]) == 3

        lignes = saisie["lignes"]
        debit = sum(Decimal(x["montant"]) for x in lignes if x["sens"] == "DEBIT")
        credit = sum(Decimal(x["montant"]) for x in lignes if x["sens"] == "CREDIT")
        assert debit == credit, saisie["lignes"]

    def test_la_saisie_proposee_est_acceptee_telle_quelle(self, comptable, dossier):
        """⚠️ **Le cas qui referme la boucle.**

        Une proposition que la route de saisie refuserait ne vaudrait rien : le
        comptable la corrigerait à la main, donc referait le travail. Ce cas
        poste le corps rendu **sans y toucher**.
        """
        saisie = _proposer(comptable, dossier, CONFORME).json()["saisie"]
        enregistree = comptable.post(
            f"/comptabilite/dossiers/{dossier}/ecritures", json=saisie
        )
        assert enregistree.status_code == 201, enregistree.text
        assert enregistree.json()["etat"] == "BROUILLON"


class TestLaFactureATvaRejetee:
    def test_la_tva_non_deductible_ajoute_une_ligne(self, comptable, dossier):
        """⚠️ **C'est ici que le contrôle sert à quelque chose.**

        Une TVA rejetée par une règle ne se récupère pas : elle s'incorpore au
        coût d'achat. Le comptable qui saisirait à la main récupérerait une TVA
        que le moteur venait de refuser, et personne ne s'en apercevrait avant le
        contrôle fiscal.
        """
        corps = _proposer(comptable, dossier, TVA_REJETEE).json()
        assert corps["comptabilisable"] is True
        assert len(corps["saisie"]["lignes"]) == 4, corps["saisie"]["lignes"]

    def test_elle_reste_comptabilisable(self, comptable, dossier):
        """⚠️ La contre-épreuve : une anomalie **majeure** n'est pas bloquante.

        Les confondre interdirait de comptabiliser des factures que le droit
        permet de comptabiliser, et le cabinet contournerait la plateforme.
        """
        assert _proposer(comptable, dossier, TVA_REJETEE).json()["comptabilisable"] is True


class TestLaFactureBloquante:
    """⚠️ **La facture bloquante se propose dans le dossier de son destinataire**,
    BOULANGERIE LA COLOMBE (`CHEZ_ELLE`).

    Jusqu'au pas 73, ces cas la proposaient dans le dossier de SARL BATIMENT PLUS,
    à qui elle n'est pas adressée, et la route l'acceptait : les cas reposaient sur
    le défaut que le pas 73 a fermé. Ils le montraient sans le voir.
    """

    CHEZ_ELLE = FACTURES_DEMO[BLOQUANTE].destinataire.niu

    def test_rien_n_est_propose_et_l_empechement_est_nomme(self, comptable):
        """⚠️ Le refus **porte l'anomalie**. Sans elle, le comptable saurait que
        c'est interdit et pas pourquoi, donc irait saisir à la main."""
        corps = _proposer(comptable, self.CHEZ_ELLE, BLOQUANTE).json()

        assert corps["comptabilisable"] is False
        assert corps["saisie"] is None
        assert corps["empechements"], corps
        assert "FAC-ID-003" in corps["empechements"][0]

    def test_le_refus_repond_200_et_non_4xx(self, comptable):
        """Une anomalie bloquante n'est pas une erreur de requête.

        ⚠️ La facture est bien formée ; c'est **elle** qui ne permet pas d'écrire.
        Un 4xx ferait chercher au comptable une faute dans son formulaire.
        """
        assert _proposer(comptable, self.CHEZ_ELLE, BLOQUANTE).status_code == 200

    def test_aucun_numero_n_est_consomme(self, comptable, dossier):
        """⚠️ **Un trou dans un journal est le premier signal que cherche un
        contrôleur.**

        Le numéro est *pressenti*, jamais réservé : un refus, ou un comptable qui
        renonce, ne doit pas laisser de trou. Ce cas propose deux fois et vérifie
        que le numéro n'a pas avancé.
        """
        premier = _proposer(comptable, dossier, CONFORME).json()["numero_pressenti"]
        # Un refus dans ce dossier : la facture bloquante n'y est pas adressée, et la
        # route la refuse (409) depuis le pas 73. Ni ce refus ni un autre ne consomme.
        assert _proposer(comptable, dossier, BLOQUANTE).status_code == 409
        second = _proposer(comptable, dossier, CONFORME).json()["numero_pressenti"]
        assert premier == second

    def test_le_numero_pressenti_est_celui_qu_on_obtient(self, comptable, dossier):
        """⚠️ **La contre-épreuve, et c'est elle qui donne sa valeur au champ.**

        Un numéro « pressenti » qui ne serait pas celui attribué serait pire que
        pas de numéro du tout : l'écran l'afficherait, le comptable le noterait,
        et la pièce porterait une référence fausse.

        Ce cas poste la saisie proposée et compare. Le mot *pressenti* dit que le
        numéro peut être pris par une autre saisie entre-temps — pas qu'il est
        inventé.
        """
        proposition = _proposer(comptable, dossier, CONFORME).json()
        enregistree = comptable.post(
            f"/comptabilite/dossiers/{dossier}/ecritures", json=proposition["saisie"]
        )
        assert enregistree.status_code == 201, enregistree.text
        assert enregistree.json()["numero"] == proposition["numero_pressenti"]


class TestCeQuiEstImposeEtCeQuiEstAccorde:
    def test_la_proposition_demande_une_session(self, dossier):
        with TestClient(app) as anonyme:
            reponse = anonyme.post(
                f"/comptabilite/dossiers/{dossier}/propositions", json={}
            )
        assert reponse.status_code in (401, 403, 422)

    def test_le_controle_n_est_pas_une_permission_de_plus(self, comptable, dossier):
        """⚠️ **`SAISIR_ECRITURE` seule, et c'est un choix.**

        Le contrôle n'est pas ici un droit qu'on accorde : c'est une contrainte
        qu'on impose. Exiger aussi `CONTROLER_CONFORMITE` rendrait le chemin sûr
        plus difficile que le chemin libre — un comptable habilité à saisir mais
        pas à contrôler passerait par la saisie manuelle et perdrait la
        vérification.

        *Un garde-fou qui coûte plus cher que son contournement ne garde rien.*

        Ce cas vérifie que celui qui peut saisir peut proposer, ce qui est la
        propriété dont dépend l'argument.
        """
        assert _proposer(comptable, dossier, CONFORME).status_code == 200
        assert (
            comptable.post(
                f"/comptabilite/dossiers/{dossier}/ecritures",
                json=_proposer(comptable, dossier, CONFORME).json()["saisie"],
            ).status_code
            == 201
        )


class TestLaDateEtLExercice:
    def test_la_date_par_defaut_est_celle_de_la_facture(self, comptable, dossier):
        """⚠️ **Jamais aujourd'hui.**

        Imputer une facture de juillet au jour de la saisie déplacerait la charge
        d'exercice, et le bilan s'en ressentirait sans qu'aucune erreur ne se
        produise.
        """
        facture = FACTURES_DEMO[CONFORME]
        saisie = _proposer(comptable, dossier, CONFORME).json()["saisie"]
        assert saisie["date_operation"] == facture.document.date_emission.isoformat()

    def test_l_exercice_suit_la_date(self, comptable, dossier):
        facture = FACTURES_DEMO[CONFORME]
        saisie = _proposer(comptable, dossier, CONFORME).json()["saisie"]
        assert saisie["exercice"] == str(facture.document.date_emission.year)
