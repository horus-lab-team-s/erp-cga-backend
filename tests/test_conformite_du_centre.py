"""Ce que la loi impose au Centre lui-même (CGI 2026, art. 118 et 119).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE (10 novembre 2026)

La plateforme encadrait les obligations des adhérents, pas celles du Centre :

  1. art. 119 (4) : la liste à jour des adhérents, annexée aux déclarations mensuelles ;
  2. art. 119 (3) : le seuil des cent adhérents actifs, qui ouvre les avantages du promoteur ;
  3. art. 118 (5) : un conseil fiscal et un expert-comptable par tranche de 2 000 adhérents ;
  4. art. 118 (3) et (4) : les fourchettes des droits et cotisations, rendues sans être
     comparées à un prix du catalogue qui n'est pas une cotisation.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO

from openpyxl import load_workbook

from app.contextes.pilotage.adaptateurs.sortant.conformite_du_centre_yaml import (
    charger_les_regles_du_centre,
)
from app.contextes.pilotage.domaine.conformite_du_centre import (
    Professionnel,
    ReglesDuCentre,
    evaluer_la_conformite,
    professionnels_requis,
)
from app.infrastructure.config import RACINE_DEPOT
from tests.conftest import exige_postgresql, ouvrir_une_session

JOUR = date(2026, 11, 10)


class TestLeCalcul:
    def test_au_moins_un_professionnel_puis_un_par_tranche_entamee(self):
        assert professionnels_requis(0, 2000) == 1
        assert professionnels_requis(2000, 2000) == 1
        assert professionnels_requis(2001, 2000) == 2

    def test_sans_professionnel_declare_le_cabinet_est_averti(self):
        conformite = evaluer_la_conformite([], ReglesDuCentre(), JOUR)
        assert conformite.adherents_actifs == 0
        assert conformite.seuil_atteint is False and conformite.manque_pour_le_seuil == 100
        assert conformite.professionnels_conformes is False
        assert any("118 (5)" in action for action in conformite.a_faire)

    def test_des_professionnels_declares_rendent_le_centre_conforme(self):
        un = [Professionnel(nom="A. B.", inscription="ONECCA n° 1")]
        regles = ReglesDuCentre(conseils_fiscaux=un, experts_comptables=un)
        conformite = evaluer_la_conformite([], regles, JOUR)
        assert conformite.professionnels_conformes is True
        assert not any("118 (5)" in action for action in conformite.a_faire)


class TestLeReferentiel:
    def test_les_valeurs_sont_celles_du_cgi_2026(self):
        regles = charger_les_regles_du_centre(RACINE_DEPOT / "Docs" / "referentiel")
        assert regles.seuil_adherents_actifs == 100
        assert regles.adherents_par_professionnel == 2000
        assert (regles.droits_d_adhesion.minimum, regles.droits_d_adhesion.maximum) == (
            Decimal(25000),
            Decimal(50000),
        )
        assert regles.cotisation_igs.maximum == Decimal(150000)
        assert regles.cotisation_reel.maximum == Decimal(250000)
        # Personne n'est inventé : le cabinet déclare ses professionnels.
        assert regles.conseils_fiscaux == [] and regles.experts_comptables == []


@exige_postgresql
class TestParLesRoutes:
    def test_la_direction_lit_la_conformite(self, plateforme):
        ouvrir_une_session(plateforme, "b.mballa@cga-brcg.cm")
        r = plateforme.get("/pilotage/conformite-du-centre")
        assert r.status_code == 200, r.text
        corps = r.json()
        assert corps["seuil_adherents_actifs"] == 100
        assert corps["adherents_actifs"] >= 1
        assert corps["professionnels_requis"] == 1

    def test_la_comptable_n_y_a_pas_acces(self, plateforme):
        ouvrir_une_session(plateforme, "l.fotso@cga-brcg.cm")
        assert plateforme.get("/pilotage/conformite-du-centre").status_code == 403
        assert plateforme.get("/pilotage/liste-des-adherents.xlsx").status_code == 403

    def test_la_liste_des_adherents_reprend_chaque_adherent_actif(self, plateforme):
        ouvrir_une_session(plateforme, "b.mballa@cga-brcg.cm")
        actifs = plateforme.get("/pilotage/conformite-du-centre").json()["adherents_actifs"]
        r = plateforme.get("/pilotage/liste-des-adherents.xlsx")
        assert r.status_code == 200, r.text
        assert "liste-des-adherents-" in r.headers["content-disposition"]
        feuille = load_workbook(BytesIO(r.content)).active
        valeurs = [[c.value for c in ligne] for ligne in feuille.iter_rows()]
        entete = next(i for i, ligne in enumerate(valeurs) if ligne[0] == "N°")
        assert valeurs[entete][2] == "NIU"
        lignes = [ligne for ligne in valeurs[entete + 1 :] if isinstance(ligne[0], int)]
        assert len(lignes) == actifs
        assert all(ligne[2] for ligne in lignes), "chaque adhérent porte son NIU"
        assert any("119 (4)" in str(ligne[0]) for ligne in valeurs[:entete])
