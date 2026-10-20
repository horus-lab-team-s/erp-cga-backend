"""Le droit à l'abattement CGA se constate, il ne se demande pas.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

La liasse accordait l'abattement CGA sur la parole de la requête : un paramètre
`adherent_sur_l_exercice`, **`True` par défaut**. Toute liasse déduisait donc
l'abattement du bénéfice imposable, adhérente ou non, sauf si l'écran pensait à
dire le contraire. Et le seuil d'adhésion de l'article 118 du CGI, validé au
référentiel, n'était lu par aucune ligne de code.

C'est le Centre qui atteste l'adhésion. Une liasse qui accorde l'abattement à tort
est une attestation fausse du Centre.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.contextes.cloture.domaine.droit_cga import apprecier_le_droit
from app.contextes.portefeuille.api import (
    PORTEFEUILLE_DEMO,
    Adhesion,
    Exercice,
    MotifChangement,
)
from app.contextes.referentiel.contrats import Borne
from app.partage.copie import transiter
from tests.conftest import exige_postgresql, ouvrir_une_session

#: Adhérent de 2018 à fin 2020, puis de nouveau depuis le 1er juillet 2022.
INTERMITTENT = "M093344556677N"
SEUIL = Decimal(100000000)


def _exercice(annee: int) -> Exercice:
    return Exercice(libelle=str(annee), ouverture=date(annee, 1, 1), cloture=date(annee, 12, 31))


def _avec_adhesions(*periodes):
    return transiter(
        PORTEFEUILLE_DEMO[INTERMITTENT],
        adhesions=[
            Adhesion(debut=debut, fin=fin, motif=MotifChangement.ADHESION, numero=f"ADH-{i}")
            for i, (debut, fin) in enumerate(periodes)
        ],
    )


class TestLAdhesionCouvreTElleLExercice:
    """⚠️ La question que le passage fiscal renvoyait au portefeuille depuis le
    premier jour, et que personne ne posait."""

    @pytest.mark.parametrize(
        "annee, attendu",
        [
            (2020, True),   # dernière année de la première adhésion
            (2021, False),  # entre deux adhésions
            (2022, False),  # réadhésion au 1er juillet : en cours d'exercice
            (2023, True),
        ],
    )
    def test_le_dossier_intermittent_de_demonstration(self, annee, attendu):
        dossier = PORTEFEUILLE_DEMO[INTERMITTENT]
        assert (
            dossier.adherente_sur_toute_la_periode(date(annee, 1, 1), date(annee, 12, 31))
            is attendu
        )

    def test_deux_adhesions_jointives_ne_font_pas_une_adhesion(self):
        """⚠️ **Chaque jour est couvert, et c'est pourtant une rupture.**

        Une résiliation au 30 juin suivie d'une réadhésion au 1er juillet : deux
        contrats, deux dates d'effet. Compter jour par jour conclurait à une
        adhésion continue, et accorderait l'abattement ouvert par le second contrat
        sur toute l'année.
        """
        dossier = _avec_adhesions(
            (date(2020, 1, 1), date(2025, 7, 1)),
            (date(2025, 7, 1), None),
        )
        assert all(
            dossier.est_adherente_au(jour)
            for jour in (
                date(2025, 1, 1), date(2025, 6, 30), date(2025, 7, 1), date(2025, 12, 31)
            )
        ), "la contre-épreuve : chaque jour de 2025 est bien couvert"
        assert (
            dossier.adherente_sur_toute_la_periode(date(2025, 1, 1), date(2025, 12, 31))
            is False
        )

    @pytest.mark.parametrize(
        "debut, fin, attendu",
        [
            (date(2025, 1, 1), None, True),               # prend effet le premier jour
            (date(2025, 1, 2), None, False),              # un jour trop tard
            (date(2020, 1, 1), date(2026, 1, 1), True),   # fin exclue : le 31/12 est couvert
            (date(2020, 1, 1), date(2025, 12, 31), False),  # fin exclue : le 31/12 ne l'est pas
        ],
    )
    def test_les_bornes(self, debut, fin, attendu):
        dossier = _avec_adhesions((debut, fin))
        assert (
            dossier.adherente_sur_toute_la_periode(date(2025, 1, 1), date(2025, 12, 31))
            is attendu
        )


class TestLeSeuilDAdhesion:
    """⚠️ CGI art. 118 : le centre assiste les entreprises dont le chiffre d'affaires
    annuel **n'excède pas** 100 millions. Le paramètre existait, et rien ne le lisait."""

    def _droit(self, chiffre, seuil=SEUIL, annee=2023):
        return apprecier_le_droit(
            PORTEFEUILLE_DEMO[INTERMITTENT],
            _exercice(annee),
            chiffre_affaires=Decimal(chiffre),
            seuil_adhesion=seuil,
            borne_adhesion=None if seuil is None else Borne.EXCLUSE,
        )

    def test_le_seuil_lui_meme_reste_eligible(self):
        droit = self._droit(100000000)
        assert droit.ouvert is True
        assert "sous le seuil" in droit.motif, droit.motif

    @pytest.mark.parametrize(
        "chiffre", [0, 99999999, 100000000, 100000001, 250000000]
    )
    def test_le_motif_ne_contredit_jamais_le_verdict(self, chiffre):
        """⚠️ **Le verdict et son motif sont calculés en deux endroits.**

        Une mutation a montré qu'ils pouvaient se contredire sans qu'aucun cas le
        voie : à 100 000 000 exactement, droit ouvert et motif « au-delà du seuil ».
        Un réviseur qui lit le motif corrigerait la liasse dans le mauvais sens.
        """
        droit = self._droit(chiffre)
        assert droit.ouvert is ("sous le seuil" in droit.motif), (chiffre, droit.motif)

    def test_un_franc_de_plus_ferme_le_droit(self):
        droit = self._droit(100000001)
        assert droit.ouvert is False
        assert droit.adherent_sur_tout_l_exercice is True
        assert "CGI art. 118" in droit.motif

    def test_un_seuil_inconnu_ferme_le_droit(self):
        """Attester une éligibilité qu'on ne peut pas vérifier est la faute même."""
        droit = self._droit(1000, seuil=None)
        assert droit.ouvert is False
        assert "ne peut pas être attestée" in droit.motif

    def test_une_adhesion_incomplete_ferme_le_droit_meme_sous_le_seuil(self):
        droit = self._droit(1000, annee=2022)
        assert droit.ouvert is False
        assert "Q17" in droit.motif


class TestLeDepotDansLesDelais:
    """⚠️ CGI 2026, art. 119 (2) : l'adhérent perd les avantages quand sa déclaration
    n'est pas souscrite dans les délais. La date de l'accusé est la date opposable."""

    ECHEANCE = date(2024, 4, 15)

    def _droit(self, *, deposee_le=None, au=date(2024, 3, 1)):
        return apprecier_le_droit(
            PORTEFEUILLE_DEMO[INTERMITTENT],
            _exercice(2023),
            chiffre_affaires=Decimal(40000000),
            seuil_adhesion=SEUIL,
            borne_adhesion=Borne.EXCLUSE,
            echeance_declaration=self.ECHEANCE,
            deposee_le=deposee_le,
            au=au,
        )

    def test_deposee_a_l_echeance_le_droit_reste_ouvert(self):
        assert self._droit(deposee_le=self.ECHEANCE).ouvert is True

    def test_deposee_apres_l_echeance_le_droit_se_ferme_et_le_dit(self):
        droit = self._droit(deposee_le=date(2024, 4, 16))
        assert droit.ouvert is False and droit.declaration_hors_delai is True
        assert "art. 119 (2)" in droit.motif and "16/04/2024" in droit.motif

    def test_avant_l_echeance_une_dsf_non_deposee_ne_ferme_rien(self):
        """La liasse se prépare AVANT le dépôt."""
        assert self._droit(au=date(2024, 4, 15)).ouvert is True

    def test_l_echeance_passee_sans_depot_ferme_le_droit(self):
        droit = self._droit(au=date(2024, 4, 16))
        assert droit.ouvert is False and "non déposée" in droit.motif


class TestParLaRoute:
    """La liasse, sur PostgreSQL, avec des ventes produites par les routes réelles."""

    pytestmark = exige_postgresql

    REVISEUR = "a.bouba@cga-brcg.cm"
    DOSSIER = "M081234567890P"  # adhérent depuis 2022, sans interruption

    def _vendre(self, client, montant, *, charge=None):
        """Une vente validée ; avec `charge`, un achat du même jour qui la dépasse."""
        lignes = [
            {"compte": "411", "libelle": "Client", "sens": "DEBIT", "montant": str(montant)},
            {"compte": "701", "libelle": "Vente", "sens": "CREDIT", "montant": str(montant)},
        ]
        if charge is not None:
            lignes += [
                {"compte": "604", "libelle": "Achats", "sens": "DEBIT", "montant": str(charge)},
                {"compte": "401", "libelle": "Fournisseur", "sens": "CREDIT",
                 "montant": str(charge)},
            ]
        saisie = client.post(
            f"/comptabilite/dossiers/{self.DOSSIER}/ecritures",
            json={
                "journal": "VE", "exercice": "2026", "date_operation": "2026-06-30",
                "libelle": "Ventes de l'exercice", "piece_justificative": "PJ-VE-CGA",
                "lignes": lignes,
            },
        )
        assert saisie.status_code == 201, saisie.text
        numero = saisie.json()["numero"]
        assert client.post(
            f"/comptabilite/dossiers/{self.DOSSIER}/ecritures/2026/VE/{numero}/validation",
            json={},
        ).status_code == 200

    def _liasse(self, client, requete=""):
        reponse = client.get(f"/cloture/dossiers/{self.DOSSIER}/liasse/2026{requete}")
        assert reponse.status_code == 200, reponse.text
        return reponse.json()

    @staticmethod
    def _abattement(liasse):
        return [ligne for ligne in liasse["passage"] if ligne["code"] == "ABATT_CGA"]

    def test_sous_le_seuil_l_abattement_figure_avec_son_motif(self, plateforme):
        """⚠️ La contre-épreuve des cas suivants : sans elle, une liasse qui
        n'accorderait jamais l'abattement les ferait tous passer."""
        client = plateforme
        ouvrir_une_session(client, self.REVISEUR)
        self._vendre(client, 60000000)
        liasse = self._liasse(client)
        assert liasse["droit_cga"]["ouvert"] is True, liasse["droit_cga"]
        assert self._abattement(liasse), liasse["passage"]
        assert liasse["abattement_cga_ecarte"] is None

    def test_un_droit_ouvert_sur_un_deficit_dit_pourquoi_l_abattement_manque(self, plateforme):
        """⚠️ **Le troisième cas, qui était muet.**

        Droit ouvert, motif « chiffre d'affaires sous le seuil », et aucun abattement :
        sans explication, la liasse se lit comme un oubli.
        """
        client = plateforme
        ouvrir_une_session(client, self.REVISEUR)
        self._vendre(client, 10000000, charge=25000000)
        liasse = self._liasse(client)
        assert Decimal(liasse["resultat_comptable"]) < 0, liasse["resultat_comptable"]
        assert liasse["droit_cga"]["ouvert"] is True
        assert self._abattement(liasse) == []
        assert "déficitaire" in (liasse["abattement_cga_ecarte"] or ""), liasse

    def test_au_dela_du_seuil_l_abattement_disparait_et_dit_pourquoi(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, self.REVISEUR)
        self._vendre(client, 150000000)
        liasse = self._liasse(client)
        assert Decimal(liasse["droit_cga"]["chiffre_affaires"]) == Decimal(150000000)
        assert liasse["droit_cga"]["ouvert"] is False
        assert "CGI art. 118" in liasse["droit_cga"]["motif"]
        # Le droit fermé s'explique par son propre motif, pas par l'écart.
        assert liasse["abattement_cga_ecarte"] is None
        assert self._abattement(liasse) == [], (
            "une entreprise à 150 millions reçoit l'abattement : le Centre atteste "
            "une éligibilité que la loi lui refuse."
        )

    def test_l_ancien_parametre_de_requete_n_a_plus_aucun_effet(self, plateforme):
        """⚠️ **Le défaut d'origine, gardé dans le temps.**

        Un écran écrit avant ce pas envoie encore `adherent_sur_l_exercice=true`.
        Il ne doit rien pouvoir rouvrir.
        """
        client = plateforme
        ouvrir_une_session(client, self.REVISEUR)
        self._vendre(client, 150000000)
        liasse = self._liasse(client, "?adherent_sur_l_exercice=true")
        assert liasse["droit_cga"]["ouvert"] is False
        assert self._abattement(liasse) == []


@exige_postgresql
def test_la_liasse_ferme_le_droit_d_une_dsf_en_retard(plateforme):
    """NGUEMA : DSF 2025 due au 15/05/2026 (centre divisionnaire), sans accusé au
    15 octobre 2026. Art. 119 (2) : le droit aux avantages est fermé, et la liasse le dit."""
    client = plateforme
    ouvrir_une_session(client, "a.bouba@cga-brcg.cm")
    droit = client.get("/cloture/dossiers/P027788990011M/liasse/2025").json()["droit_cga"]
    assert droit["ouvert"] is False and droit["declaration_hors_delai"] is True
    assert "15/05/2026" in droit["motif"]
