"""Les obligations sociales existent, et l'échéancier les voit.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE (pas 56)

Le catalogue des obligations désigne la CNPS comme le piège le plus coûteux du
métier : l'impôt libératoire n'efface pas les cotisations sociales, et un adhérent
au synthétique se découvre débiteur de plusieurs années. Et pourtant, **aucun calcul
d'échéance ne la produisait**. L'échéancier recevait `a_des_salaries: bool = False`,
trois appelants sur quatre ne l'acceptaient pas, et le quatrième le recevait d'un
écran qui ne l'envoyait jamais.

Ces cas gardent la réponse lue au fichier du personnel, période par période, par
toutes les portes : l'échéancier, les relances, le pilotage.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date

import pytest

from app.contextes.obligations.adaptateurs.sortant import effectif as module_effectif
from app.contextes.obligations.api import effectif_du_dossier
from app.contextes.social.api import CONTRATS_DEMO, Periode, a_employe_sur
from app.partage.copie import transiter
from tests.conftest import exige_postgresql, ouvrir_une_session

REVISEUR = "a.bouba@cga-brcg.cm"
#: Trois salariés, dont deux en contrat à durée indéterminée depuis des années.
EMPLOYEUR = "M065544332211L"
#: Aucun salarié au fichier du personnel.
SANS_PERSONNEL = "P019876543210K"
CODES_SOCIAUX = {"CNPS", "IRPP_SALAIRES"}


def _contrat(debut, fin):
    return transiter(CONTRATS_DEMO[0], debut=debut, fin=fin)


class TestUnContratCourtCompteDansSonMois:
    """⚠️ **Le second défaut du pas 56, dans le social lui-même.**

    `couvre_la_periode` ne regardait que le premier et le dernier jour du mois : un
    contrat du 10 au 20 mars n'en couvre aucun, et le saisonnier disparaissait du
    DIPE et de la paie de mars.
    """

    def test_un_contrat_du_10_au_20_couvre_son_mois(self):
        court = _contrat(date(2026, 3, 10), date(2026, 3, 21))
        assert court.couvre_la_periode(Periode(annee=2026, mois=3)) is True

    @pytest.mark.parametrize(
        "debut, fin, attendu",
        [
            (date(2026, 3, 31), None, True),               # commence le dernier jour
            (date(2026, 4, 1), None, False),               # commence le lendemain
            (date(2025, 1, 1), date(2026, 3, 2), True),    # finit le 2 : le 1er est couvert
            (date(2025, 1, 1), date(2026, 3, 1), False),   # fin exclue : le 1er ne l'est pas
        ],
    )
    def test_les_bornes_du_mois(self, debut, fin, attendu):
        mars = Periode(annee=2026, mois=3)
        assert _contrat(debut, fin).couvre_la_periode(mars) is attendu

    def test_a_employe_sur_suffit_d_un_contrat(self):
        contrats = [_contrat(date(2020, 1, 1), date(2021, 1, 1)), _contrat(date(2026, 6, 20), None)]
        assert a_employe_sur(contrats, date(2026, 6, 1), date(2026, 6, 30)) is True
        assert a_employe_sur(contrats, date(2026, 5, 1), date(2026, 5, 31)) is False
        assert a_employe_sur([], date(2026, 6, 1), date(2026, 6, 30)) is False


class TestLeSocialQuiNeRepondPas:
    def test_une_panne_du_personnel_rend_une_question_sans_reponse(self, monkeypatch):
        """⚠️ Le social en panne n'emporte pas l'échéancier : chaque question reçoit
        `None`, et l'obligation figure « à confirmer » plutôt que de disparaître."""

        def en_panne():
            raise RuntimeError("base du personnel indisponible")

        monkeypatch.setattr(module_effectif, "depots_du_social", en_panne)
        question = effectif_du_dossier(EMPLOYEUR)
        assert question(date(2026, 1, 1), date(2026, 1, 31)) is None


class TestParLesRoutes:
    pytestmark = exige_postgresql

    def _echeancier(self, client, niu, requete=""):
        reponse = client.get(
            f"/obligations/dossiers/{niu}/echeancier?exercice=2026&a_la_date=2026-09-14{requete}"
        )
        assert reponse.status_code == 200, reponse.text
        return [ligne["obligation"] for ligne in reponse.json()]

    def test_l_employeur_voit_ses_obligations_sociales_chaque_mois(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, REVISEUR)
        cnps = [o for o in self._echeancier(client, EMPLOYEUR) if o["code_obligation"] == "CNPS"]
        assert len(cnps) == 12, [o["periode_debut"] for o in cnps]
        assert not any(o["effectif_a_confirmer"] for o in cnps)
        assert CODES_SOCIAUX <= {o["code_obligation"] for o in self._echeancier(client, EMPLOYEUR)}

    def test_un_dossier_sans_personnel_n_en_a_aucune(self, plateforme):
        """⚠️ La contre-épreuve : sans elle, un échéancier qui produirait la CNPS
        partout ferait passer le cas précédent."""
        client = plateforme
        ouvrir_une_session(client, REVISEUR)
        codes = {o["code_obligation"] for o in self._echeancier(client, SANS_PERSONNEL)}
        assert not codes & CODES_SOCIAUX, codes

    def test_l_ancien_parametre_de_requete_n_a_plus_d_effet(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, REVISEUR)
        codes = {
            o["code_obligation"]
            for o in self._echeancier(client, SANS_PERSONNEL, "&a_des_salaries=true")
        }
        assert not codes & CODES_SOCIAUX, codes

    def test_une_embauche_par_la_route_fait_naitre_la_cnps_de_son_mois(self, plateforme):
        """La boucle entière : le social écrit, l'échéancier lit le même personnel."""
        client = plateforme
        ouvrir_une_session(client, REVISEUR)
        salarie = client.post(
            f"/social/dossiers/{SANS_PERSONNEL}/salaries",
            json={"matricule": "SAL-P56-001", "nom": "Mbarga", "prenom": "Aline"},
        )
        assert salarie.status_code == 201, salarie.text
        contrat = client.post(
            f"/social/dossiers/{SANS_PERSONNEL}/salaries/SAL-P56-001/contrats",
            json={"type_contrat": "CDD", "debut": "2026-10-10", "fin": "2026-10-21",
                  "salaire_base": "150000"},
        )
        assert contrat.status_code == 201, contrat.text

        cnps = [
            o for o in self._echeancier(client, SANS_PERSONNEL)
            if o["code_obligation"] == "CNPS"
        ]
        # ⚠️ Un seul mois, et c'est octobre : le contrat du 10 au 20 est le cas que
        # le test du premier et du dernier jour laissait tomber.
        assert [o["periode_debut"] for o in cnps] == ["2026-10-01"], cnps

    def test_les_relances_portent_la_cnps(self, plateforme):
        """Les relances calculaient leur propre échéancier, sans salariés."""
        client = plateforme
        ouvrir_une_session(client, REVISEUR)
        # J-2 de l'échéance du 15 octobre, pour la période de septembre.
        reponse = client.get("/obligations/relances?a_la_date=2026-10-13&exercice=2026")
        assert reponse.status_code == 200, reponse.text
        sociales = {
            (r["obligation"]["entreprise"], r["obligation"]["code_obligation"])
            for r in reponse.json()
        }
        assert (EMPLOYEUR, "CNPS") in sociales, sociales
        assert (SANS_PERSONNEL, "CNPS") not in sociales
