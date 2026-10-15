"""Les déclarations annuelles par tiers de la DGI : achats par fournisseur et sommes versées
aux tiers, aux modèles officiels (15 octobre 2026).

⚠️ CE QUI EST GARDÉ
* la facturation est EXACTE : HT des charges, TVA des lignes 445, de la même écriture ;
  un avoir vient en diminution ;
* un règlement (avec une ligne de trésorerie) est un paiement ; l'ouverture vient des AN ;
  règlements et soldes TTC sont répartis à la proportion des factures du tiers ;
* la clôture = ouverture + facturation − paiement, en HT comme en TVA ;
* « tiers » ne retient que les locations (622) et les honoraires (632) ;
* le fichier garde le logo et le nom du modèle ; au-delà de seize tiers, TOTAL descend et
  ses formules couvrent toutes les lignes.
"""

from __future__ import annotations

import io
import re
import zipfile
from decimal import Decimal

from app.contextes.comptabilite.adaptateurs.sortant.declaration_par_tiers_dgi import (
    MODELES,
    FicheDeclaree,
    remplir_la_declaration_par_tiers,
)
from app.contextes.comptabilite.contrats import EtatEcriture
from app.contextes.comptabilite.domaine.declaration_par_tiers import (
    LigneParTiers,
    MontantHtTva,
    declaration_par_tiers,
)
from tests.conftest import exige_postgresql, ouvrir_une_session
from tests.test_balance_auxiliaire import _ecriture

D = Decimal
QUINCAILLERIE = "M053311224455R"
BAILLEUR = "M045566778899G"

ECRITURES = [
    # À-nouveau : 1 193 000 dus à la quincaillerie.
    _ecriture(("401", -1_193_000, QUINCAILLERIE), ("471", 1_193_000, None), numero=1, journal="AN"),
    # Facture : 2 000 000 HT + 385 000 de TVA.
    _ecriture(
        ("601", 2_000_000, None),
        ("4452", 385_000, None),
        ("401", -2_385_000, QUINCAILLERIE),
        numero=2,
    ),
    # Avoir : 100 000 HT + 19 250 de TVA.
    _ecriture(
        ("401", 119_250, QUINCAILLERIE),
        ("601", -100_000, None),
        ("4452", -19_250, None),
        numero=3,
    ),
    # Règlement par banque : 1 193 000 TTC.
    _ecriture(("401", 1_193_000, QUINCAILLERIE), ("521", -1_193_000, None), numero=4),
    # Loyer d'un engin : un « tiers » au sens du modèle TIERS.
    _ecriture(
        ("622", 1_000_000, None), ("4452", 192_500, None), ("401", -1_192_500, BAILLEUR), numero=5
    ),
    # Un brouillon ne compte pas.
    _ecriture(
        ("601", 9_000_000, None),
        ("401", -9_000_000, QUINCAILLERIE),
        numero=6,
        etat=EtatEcriture.BROUILLON,
    ),
]


class TestLeCalcul:
    def _ligne(self, tiers: str) -> LigneParTiers:
        return next(x for x in declaration_par_tiers(ECRITURES) if x.tiers == tiers)

    def test_la_facturation_est_exacte_avoir_deduit(self):
        q = self._ligne(QUINCAILLERIE)
        assert q.facturation == MontantHtTva(ht=D(1_900_000), tva=D(365_750))

    def test_paiement_et_soldes_sont_repartis_a_la_proportion_des_factures(self):
        q = self._ligne(QUINCAILLERIE)
        # 1 193 000 × 365 750 / 2 265 750 = 192 580,8 : la part de TVA des factures.
        assert q.paiement.ttc == D(1_193_000) and q.paiement.tva == D(192_581)
        assert q.ouverture.ttc == D(1_193_000)

    def test_la_cloture_boucle_en_ht_comme_en_tva(self):
        q = self._ligne(QUINCAILLERIE)
        assert q.cloture.ht == q.ouverture.ht + q.facturation.ht - q.paiement.ht
        assert q.cloture.tva == q.ouverture.tva + q.facturation.tva - q.paiement.tva
        # Et le TTC retombe sur le solde du compte : 1 193 000 + 2 265 750 − 1 193 000.
        assert q.cloture.ttc == D(2_265_750)

    def test_seuls_locations_et_honoraires_font_un_tiers(self):
        assert self._ligne(BAILLEUR).sommes_versees is True
        assert self._ligne(QUINCAILLERIE).sommes_versees is False


def _fichier(nombre: int) -> zipfile.ZipFile:
    lignes = [
        LigneParTiers(
            tiers=f"M{i:013d}X",
            ouverture=MontantHtTva(),
            facturation=MontantHtTva(ht=D(1000), tva=D(193)),
            paiement=MontantHtTva(),
            cloture=MontantHtTva(ht=D(1000), tva=D(193)),
            sommes_versees=False,
        )
        for i in range(nombre)
    ]
    fiches = {
        x.tiers: FicheDeclaree(denomination=f"FOURNISSEUR & FILS {i}", niu=x.tiers)
        for i, x in enumerate(lignes)
    }
    return zipfile.ZipFile(
        io.BytesIO(remplir_la_declaration_par_tiers("fournisseurs", lignes, fiches))
    )


class TestLeFichier:
    def test_le_modele_est_garde_hors_la_feuille(self):
        rempli = _fichier(2)
        modele = zipfile.ZipFile(
            io.BytesIO(
                (
                    __import__("pathlib").Path(
                        "app/contextes/comptabilite/adaptateurs/sortant/modeles_dgi"
                    )
                    / MODELES["fournisseurs"]
                ).read_bytes()
            )
        )
        assert rempli.namelist() == modele.namelist()
        changes = {n for n in modele.namelist() if modele.read(n) != rempli.read(n)}
        assert changes == {"xl/worksheets/sheet1.xml", "xl/calcChain.xml"}
        assert rempli.read("xl/media/image1.jpeg") == modele.read("xl/media/image1.jpeg")

    def test_seize_tiers_tiennent_dans_le_modele(self):
        xml = _fichier(16).read("xl/worksheets/sheet1.xml").decode()
        total = re.search(r'<c r="H30"[^>]*><f>([^<]*)</f><v>(\d+)</v>', xml)
        assert total is not None and total.groups() == ("SUM(H14:H29)", "0")
        assert re.search(r'<c r="J30"[^>]*><f>SUM\(J14:J29\)</f><v>16000</v>', xml)
        echappee = "FOURNISSEUR &amp; FILS 3" in xml  # booléen : la feuille est longue
        assert echappee

    def test_au_dela_total_descend_et_couvre_tout(self):
        xml = _fichier(20).read("xl/worksheets/sheet1.xml").decode()
        assert re.search(r'<c r="J34"[^>]*><f>SUM\(J14:J33\)</f><v>20000</v>', xml)
        assert '<mergeCell ref="A34:G34"/>' in xml
        assert '<c r="A33"' in xml and '<row r="35"' in xml  # la ligne qui suivait, décalée
        assert re.search(r'<dimension ref="A1:O35"/>', xml)


@exige_postgresql
class TestParLesRoutes:
    URL = "/comptabilite/dossiers/M081234567890P/declarations-par-tiers"

    def test_batiment_plus_ses_fournisseurs_et_son_loueur(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "a.bouba@cga-brcg.cm")

        fournisseurs = client.get(f"{self.URL}/fournisseurs", params={"exercice": "2026"}).json()
        tiers = client.get(f"{self.URL}/tiers", params={"exercice": "2026"}).json()

        noms = {x["denomination"] for x in fournisseurs["lignes"]}
        assert {"QUINCAILLERIE DU WOURI", "LOCATION ENGINS BONABÉRI"} <= noms
        assert [x["denomination"] for x in tiers["lignes"]] == ["LOCATION ENGINS BONABÉRI"]
        assert tiers["nom_du_fichier"] == "MODELE TIERS.xlsx"

        excel = client.get(f"{self.URL}/tiers/excel", params={"exercice": "2026"})
        assert excel.status_code == 200
        assert 'filename="MODELE TIERS.xlsx"' in excel.headers["content-disposition"]

    def test_un_autre_dossier_n_y_accede_pas(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "mc.essomba@lacolombe.cm")
        assert (
            client.get(f"{self.URL}/fournisseurs", params={"exercice": "2026"}).status_code == 403
        )
