"""La DSF au modèle officiel du Système minimal de trésorerie (11 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Les adhérents à l'IGS déposent leur DSF au modèle SMT. La plateforme le refusait : la
règle de passage d'une comptabilité d'engagement à un compte de résultat de trésorerie
n'était pas tranchée. Elle l'est, à partir de la formule du modèle lui-même
(G = C − D + E − F), et ce fichier la fixe :

  1. recettes = ventes moins la hausse des créances ; dépenses = achats moins la hausse
     des dettes : ce qui a été encaissé et payé ;
  2. les variations se portent en N-1 moins N, et G retombe sur le résultat comptable ;
  3. le bilan s'équilibre, compte de tiers rangé selon le sens de son solde ;
  4. un écart entre G et le résultat comptable bloque le fichier ;
  5. le fichier est le modèle officiel, sous son nom, totaux en cache à jour.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date
from decimal import Decimal as D

from app.contextes.cloture.adaptateurs.sortant.dsf_format_dgi import (
    NOM_DU_MODELE_SMT,
    remplir_la_dsf_smt,
)
from app.contextes.cloture.application.format_dgi import etat_du_format_smt
from app.contextes.cloture.domaine.etats_smt import etablir_les_etats_smt

OUVERTURE = {
    "101": D(-1220000),
    "521": D(1000000),
    "311": D(200000),
    "411": D(50000),
    "401": D(-30000),
}


def _cloture() -> dict[str, D]:
    """Ventes 900 000 (120 000 non encaissés en fin), achats 500 000 (80 000 dus en fin),
    stock final 260 000, loyer 60 000, salaires 120 000, IGS 25 000, dotation 40 000."""
    soldes = dict(OUVERTURE)
    soldes.update(
        {
            "411": D(120000),
            "401": D(-80000),
            "311": D(260000),
            "701": D(-900000),
            "601": D(500000),
            "6031": D(-60000),
            "622": D(60000),
            "661": D(120000),
            "641": D(25000),
            "681": D(40000),
            "241": D(400000),
            "2841": D(-40000),
        }
    )
    soldes["521"] = -sum(v for k, v in soldes.items() if k != "521")
    return soldes


class TestLaRegleDePassage:
    def test_recettes_et_depenses_sont_celles_de_tresorerie(self):
        e = etablir_les_etats_smt(_cloture(), ouverture=OUVERTURE)
        assert e.resultat["recettes_ventes"] == D(830000)  # 900 000 − (120 000 − 50 000)
        assert e.resultat["depenses_achats"] == D(450000)  # 500 000 − (80 000 − 30 000)

    def test_g_retombe_sur_le_resultat_comptable(self):
        e = etablir_les_etats_smt(_cloture(), ouverture=OUVERTURE)
        # 900 000 − (500 000 − 60 000) − 60 000 − 120 000 − 25 000 − 40 000
        assert e.resultat_comptable == D(215000)
        assert e.resultat_g == D(215000)
        assert e.concorde

    def test_les_variations_se_portent_en_n_moins_1_moins_n(self):
        e = etablir_les_etats_smt(_cloture(), ouverture=OUVERTURE)
        assert e.resultat["variation_stocks"] == D(-60000)
        assert e.resultat["variation_creances"] == D(-70000)
        assert e.resultat["variation_dettes"] == D(-50000)

    def test_le_bilan_s_equilibre(self):
        e = etablir_les_etats_smt(_cloture(), ouverture=OUVERTURE)
        assert e.equilibre
        assert e.actif["immobilisations"] == D(360000)
        assert e.passif["fournisseurs_et_crediteurs"] == D(80000)


class TestCeQuiBloque:
    def test_un_stock_qui_ne_passe_pas_par_603_bloque(self):
        soldes = _cloture()
        soldes["6031"] = D(0)  # le stock monte au bilan sans que 603 le constate
        soldes["601"] += D(-60000)
        etat = etat_du_format_smt(soldes, ouverture=OUVERTURE, precedent=None, exercice_clos=True)
        assert not etat.pret
        assert any("ne retombe pas" in o for o in etat.obstacles)

    def test_un_compte_de_resultat_sans_ligne_bloque(self):
        soldes = _cloture()
        soldes["901"] = D(0)
        soldes["691"] = D(0)
        soldes["701"] -= D(1000)
        soldes["599"] = D(0)
        soldes["801"] = D(1000)  # 80 n'a pas de ligne au modèle SMT
        etat = etat_du_format_smt(soldes, ouverture=OUVERTURE, precedent=None, exercice_clos=True)
        assert any("801" in o for o in etat.obstacles), etat.obstacles


class TestLeFichier:
    def test_le_modele_officiel_rempli_totaux_en_cache(self):
        e = etablir_les_etats_smt(_cloture(), ouverture=OUVERTURE)
        octets = remplir_la_dsf_smt(
            e,
            precedents=None,
            denomination="ESSAI SMT",
            niu="P000000000000X",
            cloture=date(2025, 12, 31),
            duree_en_mois=12,
        )
        assert NOM_DU_MODELE_SMT == "DSF_SMT_VEROUILLE-09-03-2021.xlsx"
        archive = zipfile.ZipFile(io.BytesIO(octets))
        classeur = archive.read("xl/workbook.xml").decode()
        assert 'fullCalcOnLoad="1"' in classeur
        feuilles = [n for n in archive.namelist() if n.startswith("xl/worksheets/sheet")]
        mod = next(
            archive.read(n).decode()
            for n in feuilles
            if "BILAN SMT AU 31/12/2025" in archive.read(n).decode()
        )
        assert re.search(r'<c r="E42"[^>]*><v>215000</v></c>', mod)
        assert re.search(r'<c r="E16"[^>]*><f>[^<]+</f><v>\d+</v></c>', mod), "formule gardée"
        assert re.search(r'<c r="E34"[^>]*><f>[^<]+</f><v>', mod)


class TestUnExerciceVide:
    def test_aucun_fichier_vide_ne_sort(self):
        """11 novembre 2026 : un exercice sans écriture donnait un fichier « prêt », rempli de
        zéros, que le portail aurait accepté."""
        etat = etat_du_format_smt({}, ouverture={}, precedent=None, exercice_clos=True)
        assert not etat.pret
        assert any("aucune écriture validée" in o for o in etat.obstacles)
