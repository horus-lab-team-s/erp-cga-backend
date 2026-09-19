"""Inscrire un changement de régime, et refuser ce qui n'en est pas un.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Le pas 44 a donné au centre le moyen de voir qu'un dossier franchit le seuil. Le
portefeuille était en lecture seule : le reclassement devait se poser à la main en
base, hors de tout contrôle. Ces cas gardent la première route qui écrit dans le
portefeuille, et les quatre refus qui l'empêchent de réécrire l'histoire d'un
dossier.

⚠️ Et ils gardent un garde-fou qui ne regardait que le **nombre** de périodes.
Tant que rien n'écrivait, c'était sans conséquence ; dès qu'une route écrit, une
mise à jour à nombre égal pouvait faire passer 2021 de l'IGS au réel.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date

import pytest

from app.contextes.portefeuille.api import (
    PORTEFEUILLE_DEMO,
    DepotEntreprisesMemoire,
    HistoireAmputee,
    InscriptionRefusee,
    MotifChangement,
    RegimeFiscal,
    inscrire_un_regime,
)
from app.contextes.portefeuille.domaine.temporel import ecart_d_histoire
from app.partage.copie import transiter
from tests.conftest import exige_postgresql, ouvrir_une_session

#: Encore à l'IGS depuis sa création en 2019, exercices clos jusqu'en 2025.
A_L_IGS = "P019876543210K"
#: Au réel depuis le 1er janvier 2023, par dépassement de seuil.
AU_REEL = "M081234567890P"

JUSTIFICATION = "Liasse 2025 : chiffre d'affaires de 61 M FCFA, au-delà du seuil légal."


def _inscrire(niu, regime, a_compter_du, cause, *, probatoire=2, justification=JUSTIFICATION):
    return inscrire_un_regime(
        PORTEFEUILLE_DEMO[niu],
        regime,
        a_compter_du,
        cause,
        justification=justification,
        exercices_probatoires=lambda _jour: probatoire,
    )


class TestCeQuUneInscriptionFait:
    def test_fermer_l_ancien_et_ouvrir_le_nouveau_le_meme_jour(self):
        avant = PORTEFEUILLE_DEMO[A_L_IGS]
        apres = _inscrire(
            A_L_IGS, RegimeFiscal.REEL, date(2027, 1, 1), MotifChangement.DEPASSEMENT_SEUIL
        )
        assert len(apres.regimes) == len(avant.regimes) + 1
        ancien, nouveau = sorted(apres.regimes, key=lambda s: s.debut)[-2:]

        # ⚠️ `[debut, fin[` : le 31 décembre relève encore de l'IGS.
        assert ancien.fin == date(2027, 1, 1)
        assert apres.regime_au(date(2026, 12, 31)) is RegimeFiscal.IGS
        assert apres.regime_au(date(2027, 1, 1)) is RegimeFiscal.REEL

        assert nouveau.motif is MotifChangement.DEPASSEMENT_SEUIL
        assert nouveau.precision == JUSTIFICATION
        assert nouveau.fin is None

    def test_l_ancien_statut_ne_recoit_que_sa_date_de_fin(self):
        """Motif et précision d'origine demeurent : c'est l'histoire du dossier."""
        avant = PORTEFEUILLE_DEMO[A_L_IGS].regime_courant
        apres = _inscrire(
            A_L_IGS, RegimeFiscal.REEL, date(2027, 1, 1), MotifChangement.OPTION
        )
        ferme = sorted(apres.regimes, key=lambda s: s.debut)[-2]
        assert transiter(ferme, fin=None) == avant

    def test_une_date_d_effet_future_est_le_cas_normal(self):
        """Un franchissement vu en novembre prend effet au 1er janvier suivant, et
        le cabinet qui fait son travail l'inscrit dès qu'il le voit."""
        apres = _inscrire(
            A_L_IGS, RegimeFiscal.REEL, date(2030, 1, 1), MotifChangement.DEPASSEMENT_SEUIL
        )
        assert apres.regime_au(date(2029, 12, 31)) is RegimeFiscal.IGS


class TestLesQuatreRefus:
    def test_un_exercice_clos_ne_change_pas_de_regime(self):
        """⚠️ Ses comptes ont été arrêtés, et souvent déclarés, sous le régime en
        vigueur. Les faire relever d'un autre après coup contredirait ce que le
        cabinet a signé."""
        with pytest.raises(InscriptionRefusee, match="exercice clos 2025"):
            _inscrire(
                A_L_IGS, RegimeFiscal.REEL, date(2025, 7, 1),
                MotifChangement.DEPASSEMENT_SEUIL,
            )

    def test_le_dernier_jour_de_l_exercice_clos_en_fait_partie(self):
        """⚠️ **La borne, là où la règle change quelque chose.**

        `[debut, fin[` : un statut qui commence le 31 décembre couvre le 31
        décembre, donc un jour de l'exercice clos. Une mutation a montré qu'aucun
        cas ne distinguait « pendant » de « strictement pendant ».
        """
        with pytest.raises(InscriptionRefusee, match="exercice clos 2025"):
            _inscrire(
                A_L_IGS, RegimeFiscal.REEL, date(2025, 12, 31),
                MotifChangement.DEPASSEMENT_SEUIL,
            )

    def test_la_contre_epreuve_au_lendemain_de_la_cloture(self):
        """Sans elle, un contrôle qui refuserait toute date passée passerait aussi.

        Le 1er janvier 2026 suit la clôture de 2025 : c'est la date d'effet d'un
        reclassement constaté sur la liasse 2025, souvent inscrit en mars.
        """
        apres = _inscrire(
            A_L_IGS, RegimeFiscal.REEL, date(2026, 1, 1), MotifChangement.DEPASSEMENT_SEUIL
        )
        assert apres.regime_au(date(2026, 1, 1)) is RegimeFiscal.REEL

    def test_le_meme_regime_n_est_pas_un_changement(self):
        with pytest.raises(InscriptionRefusee, match="relève déjà du régime IGS"):
            _inscrire(
                A_L_IGS, RegimeFiscal.IGS, date(2027, 1, 1),
                MotifChangement.DECISION_ADMINISTRATION,
            )

    @pytest.mark.parametrize(
        "regime, cause",
        [
            (RegimeFiscal.IGS, MotifChangement.DEPASSEMENT_SEUIL),
            (RegimeFiscal.REEL, MotifChangement.RETOUR_APRES_PROBATOIRE),
            # ⚠️ **La porte de sortie de la période probatoire.** Si l'option
            # menait à l'IGS, on inscrirait « option » là où il faudrait « retour »,
            # et le contrôle probatoire ne jouerait jamais.
            (RegimeFiscal.IGS, MotifChangement.OPTION),
            # Une correction ne se distingue pas d'un retour déguisé.
            (RegimeFiscal.IGS, MotifChangement.CORRECTION),
            (RegimeFiscal.REEL, MotifChangement.CORRECTION),
        ],
    )
    def test_la_cause_doit_pouvoir_produire_ce_regime(self, regime, cause):
        niu = AU_REEL if regime is RegimeFiscal.IGS else A_L_IGS
        with pytest.raises(InscriptionRefusee, match="Causes admises"):
            _inscrire(niu, regime, date(2027, 1, 1), cause, probatoire=0)

    def test_la_creation_ne_sert_qu_au_premier_statut(self):
        with pytest.raises(InscriptionRefusee, match="premier statut"):
            _inscrire(A_L_IGS, RegimeFiscal.REEL, date(2027, 1, 1), MotifChangement.CREATION)

    def test_une_date_qui_effacerait_une_periode_est_refusee(self):
        with pytest.raises(InscriptionRefusee, match="effacerait une période"):
            _inscrire(
                AU_REEL, RegimeFiscal.IGS, date(2023, 1, 1),
                MotifChangement.DECISION_ADMINISTRATION,
            )

    def test_une_justification_vide_est_refusee(self):
        with pytest.raises(InscriptionRefusee, match="se justifie"):
            _inscrire(
                A_L_IGS, RegimeFiscal.REEL, date(2027, 1, 1),
                MotifChangement.OPTION, justification="   ",
            )


class TestLaPeriodeProbatoire:
    """⚠️ `retour_au_synthetique_admis` existait depuis le premier jour, et
    personne ne l'appelait. Elle a enfin un appelant, et c'est celui-ci."""

    def test_un_retour_avant_la_fin_de_la_periode_est_refuse(self):
        # Au réel depuis 2023 : trois exercices clos (2023, 2024, 2025). Une période
        # de quatre n'est pas écoulée.
        with pytest.raises(InscriptionRefusee, match="pas encore admis"):
            _inscrire(
                AU_REEL, RegimeFiscal.IGS, date(2027, 1, 1),
                MotifChangement.RETOUR_APRES_PROBATOIRE, probatoire=4,
            )

    def test_la_contre_epreuve_quand_la_periode_est_ecoulee(self):
        apres = _inscrire(
            AU_REEL, RegimeFiscal.IGS, date(2027, 1, 1),
            MotifChangement.RETOUR_APRES_PROBATOIRE, probatoire=3,
        )
        assert apres.regime_au(date(2027, 1, 1)) is RegimeFiscal.IGS

    def test_la_decision_de_l_administration_n_attend_pas(self):
        """Quand l'administration décide, le cabinet enregistre."""
        apres = _inscrire(
            AU_REEL, RegimeFiscal.IGS, date(2027, 1, 1),
            MotifChangement.DECISION_ADMINISTRATION, probatoire=99,
        )
        assert apres.regime_au(date(2027, 1, 1)) is RegimeFiscal.IGS


class TestLeGardeFouRegardeLeContenu:
    """⚠️ **Le défaut que l'ouverture en écriture rendait dangereux.**

    Les dépôts ne comparaient que le nombre de périodes, et chacun portait sa
    propre copie de la règle — qui ne disaient même pas la même phrase.
    """

    def test_reecrire_une_periode_close_a_nombre_egal_est_refuse(self):
        dossier = PORTEFEUILLE_DEMO[AU_REEL]
        premiere, seconde = sorted(dossier.regimes, key=lambda s: s.debut)
        # Même nombre de périodes, mêmes dates : seule la nature du régime de
        # 2021-2022 change. L'ancien garde-fou laissait passer.
        falsifiee = transiter(premiere, regime=RegimeFiscal.REEL, motif=MotifChangement.OPTION)

        depot = DepotEntreprisesMemoire.avec_demonstration()
        with pytest.raises(HistoireAmputee, match="période close"):
            depot.enregistrer(transiter(dossier, regimes=[falsifiee, seconde]))

    def test_modifier_la_periode_ouverte_au_dela_de_sa_fin_est_refuse(self):
        dossier = PORTEFEUILLE_DEMO[A_L_IGS]
        ouverte = dossier.regime_courant
        retouchee = transiter(ouverte, precision="Réécrit après coup")
        ecart = ecart_d_histoire(dossier.regimes, [retouchee], quoi="essai")
        assert ecart is not None and "Seule sa fermeture" in ecart

    def test_une_inscription_est_un_prolongement(self):
        """⚠️ La contre-épreuve : sans elle, un garde-fou qui refuserait tout
        passerait les deux cas précédents."""
        dossier = PORTEFEUILLE_DEMO[A_L_IGS]
        apres = _inscrire(
            A_L_IGS, RegimeFiscal.REEL, date(2027, 1, 1), MotifChangement.DEPASSEMENT_SEUIL
        )
        assert ecart_d_histoire(dossier.regimes, apres.regimes, quoi="essai") is None

        depot = DepotEntreprisesMemoire.avec_demonstration()
        depot.enregistrer(apres)
        assert len(depot.lire(A_L_IGS).regimes) == 2


class TestParLaRoute:
    """La première route qui écrit dans le portefeuille, et la boucle qu'elle ferme."""

    pytestmark = exige_postgresql

    REVISEUR = "a.bouba@cga-brcg.cm"
    #: Tient ce dossier, peut y saisir, et ne peut pas en changer le régime.
    COMPTABLE_DU_DOSSIER = "c.ndongo@cga-brcg.cm"
    ADHERENT = "jp.nkoa@batimentplus.cm"

    def _demande(self, **surcharges):
        corps = {
            "regime": "REEL",
            "a_compter_du": "2027-01-01",
            "cause": "DEPASSEMENT_SEUIL",
            "justification": JUSTIFICATION,
        }
        return {**corps, **surcharges}

    def _regimes(self, client):
        fiche = client.get(f"/portefeuille/entreprises/{A_L_IGS}")
        assert fiche.status_code == 200, fiche.text
        return fiche.json()["regimes"]

    def test_le_reviseur_inscrit_et_la_surveillance_se_tait(self, plateforme):
        """⚠️ **La boucle fermée**, de la vente à l'inscription.

        Une vente fait franchir le seuil sur l'exercice en cours ; la surveillance
        signale le dossier ; le réviseur inscrit le passage au réel au 1er janvier ;
        la surveillance cesse de le signaler, et dit pourquoi.
        """
        client = plateforme
        ouvrir_une_session(client, self.REVISEUR)

        saisie = client.post(
            f"/comptabilite/dossiers/{A_L_IGS}/ecritures",
            json={
                "journal": "VE", "exercice": "2026", "date_operation": "2026-10-15",
                "libelle": "Marché de fournitures", "piece_justificative": "PJ-VE-9001",
                "lignes": [
                    {"compte": "411", "libelle": "Client", "sens": "DEBIT", "montant": "61000000"},
                    {"compte": "701", "libelle": "Vente", "sens": "CREDIT", "montant": "61000000"},
                ],
            },
        )
        assert saisie.status_code == 201, saisie.text
        numero = saisie.json()["numero"]
        assert client.post(
            f"/comptabilite/dossiers/{A_L_IGS}/ecritures/2026/VE/{numero}/validation", json={}
        ).status_code == 200

        url_seuil = f"/obligations/dossiers/{A_L_IGS}/seuil-de-regime?a_la_date=2026-11-02"
        avant = client.get(url_seuil).json()
        assert avant["a_surveiller"] is True, avant

        inscrit = client.post(f"/portefeuille/entreprises/{A_L_IGS}/regimes", json=self._demande())
        assert inscrit.status_code == 201, inscrit.text
        assert inscrit.json()["regime"] == "REEL"
        assert inscrit.json()["assujettie_tva"] is True
        assert inscrit.json()["a_la_date"] == "2027-01-01"

        apres = client.get(url_seuil).json()
        assert apres["reclassement_inscrit_au"] == "2027-01-01"
        assert apres["a_surveiller"] is False, apres

        regimes = self._regimes(client)
        assert len(regimes) == 2
        assert regimes[-1]["precision"] == JUSTIFICATION

    def test_le_comptable_du_dossier_ne_change_pas_son_regime(self, plateforme):
        """Il tient le dossier et y saisit ; changer ce que l'entreprise doit à
        l'administration engage le cabinet au-delà de la saisie."""
        client = plateforme
        ouvrir_une_session(client, self.COMPTABLE_DU_DOSSIER)
        reponse = client.post(f"/portefeuille/entreprises/{A_L_IGS}/regimes", json=self._demande())
        assert reponse.status_code == 403, reponse.text
        ouvrir_une_session(client, self.REVISEUR)
        assert len(self._regimes(client)) == 1

    def test_l_adherent_ne_change_pas_son_propre_regime(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, self.ADHERENT)
        reponse = client.post(f"/portefeuille/entreprises/{AU_REEL}/regimes", json=self._demande())
        assert reponse.status_code in (403, 404), reponse.text

    @pytest.mark.parametrize(
        "surcharge",
        [
            {"justification": "RAS"},
            # ⚠️ Un champ de plus ouvrirait la porte à une histoire réécrite par
            # le corps de la requête.
            {"fin": "2030-01-01"},
            {"debut": "2019-09-02"},
        ],
    )
    def test_la_demande_ne_porte_que_ce_qui_se_decide(self, plateforme, surcharge):
        client = plateforme
        ouvrir_une_session(client, self.REVISEUR)
        reponse = client.post(
            f"/portefeuille/entreprises/{A_L_IGS}/regimes", json=self._demande(**surcharge)
        )
        assert reponse.status_code == 422, reponse.text

    def test_un_refus_du_domaine_repond_409_et_ne_touche_a_rien(self, plateforme):
        """⚠️ `409` et non `422` : la requête est bien formée, ce sont les faits du
        dossier qui s'y opposent. Et l'histoire reste intacte."""
        client = plateforme
        ouvrir_une_session(client, self.REVISEUR)
        reponse = client.post(
            f"/portefeuille/entreprises/{A_L_IGS}/regimes",
            json=self._demande(a_compter_du="2025-07-01"),
        )
        assert reponse.status_code == 409, reponse.text
        assert "exercice clos" in reponse.text
        assert len(self._regimes(client)) == 1

