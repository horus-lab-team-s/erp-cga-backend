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

    def test_sigle_telephone_et_ville_viennent_de_la_fiche(self):
        """16 octobre 2026 : les colonnes D, E et G partaient vides."""
        ligne = LigneParTiers(
            tiers="M053311224455R",
            ouverture=MontantHtTva(),
            facturation=MontantHtTva(ht=D(1000), tva=D(193)),
            paiement=MontantHtTva(),
            cloture=MontantHtTva(ht=D(1000), tva=D(193)),
            sommes_versees=False,
        )
        fiche = FicheDeclaree(
            denomination="QUINCAILLERIE DU WOURI",
            niu="M053311224455R",
            sigle="QDW",
            telephone="+237 699 00 11 22",
            ville="Douala",
        )
        rempli = remplir_la_declaration_par_tiers("fournisseurs", [ligne], {ligne.tiers: fiche})
        xml = zipfile.ZipFile(io.BytesIO(rempli)).read("xl/worksheets/sheet1.xml").decode()

        def cellule(ref: str) -> str | None:
            m = re.search(rf'<c r="{ref}"[^>]*><is><t[^>]*>([^<]*)</t></is></c>', xml)
            return m.group(1) if m else None

        lues = (cellule("D14"), cellule("E14"), cellule("F14"), cellule("G14"))
        assert lues == ("QDW", "+237 699 00 11 22", "CAMEROUN", "Douala")


@exige_postgresql
class TestLeRegistreDesTiers:
    """16 octobre 2026 : le comptable complète la fiche d'un tiers une fois ; la déclaration
    et le modèle de la DGI la reprennent."""

    URL = "/comptabilite/dossiers/M081234567890P/declarations-par-tiers"
    REGISTRE = "/portefeuille/entreprises/M081234567890P/tiers"

    def _quincaillerie(self, client) -> dict:
        lignes = client.get(f"{self.URL}/fournisseurs", params={"exercice": "2026"}).json()
        return next(x for x in lignes["lignes"] if x["denomination"] == "QUINCAILLERIE DU WOURI")

    def test_la_fiche_renseignee_remplit_la_declaration_et_le_modele(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "l.fotso@cga-brcg.cm")
        avant = self._quincaillerie(client)
        assert (avant["au_registre"], avant["telephone"], avant["ville"]) == (False, None, None)

        reponse = client.put(
            f"{self.REGISTRE}/{avant['tiers']}",
            json={
                "denomination": "QUINCAILLERIE DU WOURI",
                "niu": avant["niu"],
                "sigle": "QDW",
                "telephone": "+237 699 00 11 22",
                "ville": " Douala ",
            },
        )
        assert reponse.status_code == 200, reponse.text
        assert reponse.json()["ville"] == "Douala"

        apres = self._quincaillerie(client)
        lus = (apres["au_registre"], apres["sigle"], apres["telephone"], apres["ville"])
        assert lus == (True, "QDW", "+237 699 00 11 22", "Douala")
        registre = client.get(self.REGISTRE).json()
        assert [t["code"] for t in registre] == [avant["tiers"]]

        excel = client.get(f"{self.URL}/fournisseurs/excel", params={"exercice": "2026"})
        xml = zipfile.ZipFile(io.BytesIO(excel.content)).read("xl/worksheets/sheet1.xml").decode()
        repris = "+237 699 00 11 22" in xml and ">QDW<" in xml  # booléen : feuille longue
        assert repris

    def test_un_tiers_etranger_porte_son_pays(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "l.fotso@cga-brcg.cm")
        code = self._quincaillerie(client)["tiers"]
        corps = {"denomination": "QUINCAILLERIE DU WOURI", "etranger": True, "pays": "GABON"}
        assert client.put(f"{self.REGISTRE}/{code}", json=corps).status_code == 200
        ligne = self._quincaillerie(client)
        assert (ligne["etranger"], ligne["pays"]) == (True, "GABON")
        # Le pays d'un tiers résident ne se lit pas : il réside au Cameroun.
        corps |= {"etranger": False}
        assert client.put(f"{self.REGISTRE}/{code}", json=corps).json()["pays"] is None
        assert self._quincaillerie(client)["pays"] == "CAMEROUN"

    def test_sans_la_saisie_des_ecritures_rien_ne_s_ecrit(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "p.moukouri@cga-brcg.cm")  # chargée de clientèle
        refus = client.put(f"{self.REGISTRE}/X1", json={"denomination": "TIERS"})
        assert refus.status_code == 403
        ouvrir_une_session(client, "l.fotso@cga-brcg.cm")
        assert client.get(self.REGISTRE).json() == []


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
