"""La DSF au format DGI : le modèle officiel, rempli aux codes REF officiels (15 octobre 2026).

⚠️ CE QUI EST GARDÉ
* chaque code REF produit existe dans le modèle officiel, et le modèle n'en a pas que
  nous ignorions (hors en-têtes de rubrique) ;
* les comptes de tiers et de banque changent de côté selon le sens de leur solde ;
* un amortissement se retranche de l'actif qu'il corrige (règle miroir) ; un compte
  racine ambigu bloque au lieu d'être rangé d'office ;
* totaux et soldes intermédiaires conformes aux définitions du modèle, bilan équilibré ;
* le fichier ne touche que les deux feuilles remplies, et garde le nom du modèle ;
* un exercice ouvert ne sort pas, un autre dossier n'y accède pas.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.contextes.cloture.adaptateurs.sortant.dsf_fiches_r import (
    ActiviteR2,
    IdentiteDsf,
    PersonneR3,
    valeurs_des_fiches_r,
)
from app.contextes.cloture.adaptateurs.sortant.dsf_format_dgi import (
    MODELE,
    NOM_DU_MODELE,
    _chaines,
    _chemins_des_feuilles,
    _codes_ref,
    remplir_la_dsf_normal,
)
from app.contextes.cloture.domaine.etats_dgi import (
    TOTAUX_ACTIF,
    TOTAUX_PASSIF,
    etablir_les_etats_dgi,
)
from app.contextes.comptabilite.adaptateurs.sortant.exercice_repris_demo import _exercice_2025
from app.contextes.comptabilite.contrats import Sens
from app.partage.horloge import horloge_figee
from tests.conftest import exige_postgresql, ouvrir_une_session

D = Decimal
NGUEMA = "P027788990011M"
BATIMENT = "M081234567890P"
FICHE_COMPLETE = {
    "telephone": "+237 233 42 18 90",
    "ville": "Douala",
    "adresse_geographique": "Immeuble Ekwalla, rue Joss, Bonanjo",
    "controle": "PRIVE_NATIONAL",
    "signataire_nom": "NKOA Jean-Pierre",
    "signataire_qualite": "Gérant",
    "banque": "Afriland First Bank",
    "numero_de_compte": "10005-00001-01234567890-12",
    "activites": [{"designation": "Construction de bâtiments", "code_nacam": "4100"}],
}


def _soldes_de_la_demo() -> dict[str, Decimal]:
    soldes: dict[str, Decimal] = {}
    for e in _exercice_2025():
        for ligne in e.lignes:
            signe = ligne.montant if ligne.sens is Sens.DEBIT else -ligne.montant
            soldes[ligne.compte] = soldes.get(ligne.compte, D(0)) + signe
    return soldes


def _codes_du_modele() -> tuple[set[str], set[str], set[str]]:
    z = zipfile.ZipFile(MODELE)
    chemins, chaines = _chemins_des_feuilles(z), _chaines(z)
    bilan = z.read(chemins["BILAN PAYSAGE"]).decode()
    resultat = z.read(chemins["COMPTE DE RESULTAT"]).decode()
    return (
        set(_codes_ref(bilan, chaines, "A")),
        set(_codes_ref(bilan, chaines, "H")),
        set(_codes_ref(resultat, chaines, "A")),
    )


class TestLaTableOfficielle:
    def test_nos_codes_et_ceux_du_modele_se_recouvrent(self):
        actif, passif, resultat = _codes_du_modele()
        etats = etablir_les_etats_dgi({})

        assert set(etats.actif) <= actif, set(etats.actif) - actif
        assert set(etats.passif) <= passif, set(etats.passif) - passif
        assert set(etats.resultat) <= resultat, set(etats.resultat) - resultat
        # Le modèle n'a pas de ligne que nous ignorions, hors l'en-tête « Créances » (BC).
        assert actif - set(etats.actif) <= {"BC"}, actif - set(etats.actif)
        assert passif == set(etats.passif)
        assert resultat == set(etats.resultat)

    def test_l_exercice_repris_de_la_demo(self):
        etats = etablir_les_etats_dgi(_soldes_de_la_demo())

        assert etats.equilibre and etats.passif["DZ"] == D(32_520_000)
        assert etats.resultat["XI"] == etats.passif["CJ"] == D(27_520_000)
        assert (etats.actif["AM"].brut, etats.actif["AM"].amortissements) == (
            D(2_400_000),
            D(800_000),
        )
        assert etats.resultat["TC"] == D(38_500_000)  # 706 : services vendus, pas TB
        (avertissement,) = etats.avertissements
        assert "compte 28" in avertissement and "AM" in avertissement

    def test_le_sens_du_solde_decide_du_cote(self):
        etats = etablir_les_etats_dgi(
            {
                "411": D(-300_000),  # client créditeur : avance reçue
                "409": D(150_000),  # fournisseur débiteur : avance versée
                "521": D(-2_000_000),  # banque à découvert
                "401": D(-900_000),
                "4441": D(-120_000),  # TVA due
            }
        )

        assert etats.passif["DI"] == D(300_000)
        assert etats.actif["BH"].brut == D(150_000)
        assert etats.passif["DR"] == D(2_000_000)
        assert etats.passif["DJ"] == D(900_000)
        assert etats.passif["DK"] == D(120_000)

    def test_l_amortissement_suit_son_actif(self):
        etats = etablir_les_etats_dgi(
            {"245": D(9_000_000), "2845": D(-3_000_000), "241": D(1_000_000), "2841": D(-250_000)}
        )

        assert etats.actif["AN"].net == D(6_000_000)
        assert etats.actif["AM"].net == D(750_000)
        assert etats.actif["AI"].net == D(6_750_000) == etats.actif["AZ"].net

    def test_un_compte_racine_ambigu_bloque(self):
        etats = etablir_les_etats_dgi(
            {"245": D(9_000_000), "241": D(1_000_000), "28": D(-500_000), "101": D(-9_500_000)}
        )

        assert "28" in etats.comptes_non_couverts

    def test_les_soldes_intermediaires(self):
        r = etablir_les_etats_dgi(
            {
                "701": D(-10_000_000),
                "601": D(6_000_000),
                "702": D(-1_000_000),
                "706": D(-2_000_000),
                "66": D(1_500_000),
                "681": D(500_000),
                "77": D(-100_000),
                "67": D(300_000),
                "89": D(1_000_000),
            }
        ).resultat

        assert r["XA"] == D(4_000_000)
        assert r["XB"] == D(13_000_000)
        assert r["XC"] == D(7_000_000)
        assert r["XD"] == D(5_500_000)
        assert r["XE"] == D(5_000_000)
        assert r["XF"] == D(-200_000)
        assert r["XI"] == D(3_800_000)

    def test_les_totaux_sont_ceux_du_modele(self):
        assert TOTAUX_ACTIF["BZ"] == ("AZ", "BK", "BT", "BU")
        assert TOTAUX_PASSIF["DZ"] == ("DF", "DP", "DT", "DY")


class TestLeFichier:
    def _fichier(self) -> bytes:
        return remplir_la_dsf_normal(
            etablir_les_etats_dgi(_soldes_de_la_demo()),
            precedents=None,
            denomination="CABINET NGUEMA & ASSOCIÉS",
            niu=NGUEMA,
            cloture=date(2025, 12, 31),
        )

    def test_seules_les_deux_feuilles_remplies_changent(self):
        modele, rempli = zipfile.ZipFile(MODELE), zipfile.ZipFile(io.BytesIO(self._fichier()))
        chemins = _chemins_des_feuilles(modele)

        assert modele.namelist() == rempli.namelist()
        changees = {n for n in modele.namelist() if modele.read(n) != rempli.read(n)}
        assert changees == {chemins["BILAN PAYSAGE"], chemins["COMPTE DE RESULTAT"]}

    def test_les_montants_sont_sur_leur_ligne(self):
        rempli = zipfile.ZipFile(io.BytesIO(self._fichier()))
        chemins = _chemins_des_feuilles(rempli)
        bilan = rempli.read(chemins["BILAN PAYSAGE"]).decode()
        lignes = _codes_ref(bilan, _chaines(rempli), "A")

        def valeur(colonne: str, code: str) -> str:
            m = re.search(rf'<c r="{colonne}{lignes[code]}"[^>]*><v>([^<]*)</v></c>', bilan)
            return m.group(1) if m else ""

        assert (valeur("D", "AM"), valeur("E", "AM"), valeur("F", "AM")) == (
            "2400000",
            "800000",
            "1600000",
        )
        assert valeur("F", "BZ") == "32520000"
        # La raison sociale est échappée, pas injectée dans le XML.
        echappee = "CABINET NGUEMA &amp; ASSOCIÉS" in bilan  # booléen : voir `_contient`
        assert echappee

    def test_les_fiches_r1_a_r3_vont_dans_leurs_cadres(self):
        """15 octobre 2026 : cadres relevés sur le rendu du modèle (voir `dsf_fiches_r.py`)."""
        identite = IdentiteDsf(
            denomination="SARL BATIMENT PLUS",
            niu=BATIMENT,
            forme_juridique="SARL",
            ouverture=date(2026, 1, 1),
            cloture=date(2026, 12, 31),
            rccm="RC/DLA/2019/B/1234",
            numero_cnps_employeur="301-0123456-K",
            signataire_nom="NKOA Jean-Pierre",
            banque="Afriland First Bank",
            numero_de_compte="10005-00001-01234567890-12",
            controle="PRIVE_NATIONAL",
            activites=(
                ActiviteR2("Construction de bâtiments", "4100", D("30000000")),
                ActiviteR2("Location d'engins", "7732", D("10000000")),
            ),
            dirigeants=(PersonneR3(nom="NKOA Jean-Pierre", qualite="Gérant"),),
        )
        fichier = remplir_la_dsf_normal(
            etablir_les_etats_dgi(_soldes_de_la_demo()),
            precedents=None,
            denomination="SARL BATIMENT PLUS",
            niu=BATIMENT,
            cloture=date(2026, 12, 31),
            fiches=valeurs_des_fiches_r(identite),
        )
        rempli = zipfile.ZipFile(io.BytesIO(fichier))
        chemins = _chemins_des_feuilles(rempli)

        def cellule(feuille: str, coordonnee: str) -> str:
            xml = rempli.read(chemins[feuille]).decode()
            contenu = r"(?:<is><t[^>]*>([^<]*)</t></is>|<v>([^<]*)</v>)"
            m = re.search(rf'<c r="{coordonnee}"[^>]*>{contenu}', xml)
            return (m.group(1) or m.group(2)) if m else ""

        assert cellule("Fiche R1", "E15") == "RC/DLA/2019/B/1234"
        assert cellule("Fiche R1", "C17") == "301-0123456-K"
        assert cellule("Fiche R1", "I17") == "4100"  # le code de l'activité principale
        assert cellule("Fiche R1", "C39") == "NKOA Jean-Pierre"
        assert cellule("Fiche R1", "G41") == "Afriland First Bank"
        # Note 36 : SARL = 02 (puis 0, sans agrément), réel normal = 1, Cameroun = 09.
        assert [cellule("Fiche R2", f"{c}9") for c in "FGH"] == ["0", "2", "0"]
        assert cellule("Fiche R2", "F11") == "1"
        assert [cellule("Fiche R2", f"{c}13") for c in "FG"] == ["0", "9"]
        assert cellule("Fiche R2", "P11") == "X" and cellule("Fiche R2", "P9") == ""
        assert [cellule("Fiche R2", f"{c}33") for c in "FGHI"] == ["7", "7", "3", "2"]
        assert cellule("Fiche R2", "M30") == "30000000"
        assert cellule("Fiche R2", "P30") == "75,0 %"
        assert cellule("Fiche R2", "M48") == "40000000"
        assert cellule("Fiche R3", "A11") == "NKOA Jean-Pierre"
        assert cellule("Fiche R3", "C11") == "Gérant"

    def test_le_nom_est_celui_du_modele(self):
        assert NOM_DU_MODELE == "DSF_Normal_DGIFORMAT_VERROUILLEVF.xlsx"


MOTIF_DE_CLOTURE = "Comptes arrêtés après revue du dossier et rapprochement des soldes bancaires."


@exige_postgresql
class TestParLesRoutes:
    """Par les routes, sur un dossier AU RÉEL : Bâtiment Plus, dont l'exercice 2026 se
    clôt ici en janvier 2027, comme le font les cas de clôture."""

    URL = f"/cloture/dossiers/{BATIMENT}/dsf/2026/format-dgi"

    @pytest.fixture(autouse=True)
    def _en_janvier_2027(self):
        with horloge_figee(datetime(2027, 1, 15, 9, 0)):
            yield

    def _clore(self, client) -> None:
        ouvrir_une_session(client, "a.bouba@cga-brcg.cm")
        rapport = client.post(
            f"/cloture/dossiers/{BATIMENT}/exercices/2026/cloture?appliquer=true",
            json={"motif": MOTIF_DE_CLOTURE},
        )
        assert rapport.status_code == 200 and rapport.json()["applique"], rapport.text

    def test_l_exercice_clos_sort_au_format_dgi(self, plateforme):
        client = plateforme
        self._clore(client)

        corps = client.get(self.URL).json()
        assert corps["pret"] is True, corps
        # La fiche signalétique n'est pas validée : un avertissement, pas un blocage.
        assert any("Fiche signalétique" in a for a in corps["avertissements"]), corps

        fichier = client.get(f"{self.URL}/fichier")
        assert fichier.status_code == 200, fichier.text
        assert f'filename="{NOM_DU_MODELE}"' in fichier.headers["content-disposition"]
        assert zipfile.ZipFile(io.BytesIO(fichier.content)).testzip() is None
        # Fiche non validée : R3 reprend les dirigeants du dossier, R1 rien de la fiche.
        assert self._contient(fichier.content, "Fiche R3", "NKOA")
        assert not self._contient(fichier.content, "Fiche R1", "Afriland")

    @staticmethod
    def _contient(contenu: bytes, nom: str, texte: str) -> bool:
        """⚠️ Un booléen, pas le XML : une feuille s'étend jusqu'à la colonne XFD, et
        pytest qui expliquerait un `in` raté sur ces mégaoctets ne rendrait jamais la
        main (constaté le 15 octobre 2026, à l'épreuve par mutation)."""
        z = zipfile.ZipFile(io.BytesIO(contenu))
        return texte in z.read(_chemins_des_feuilles(z)[nom]).decode()

    def test_la_fiche_validee_entre_dans_le_fichier(self, plateforme):
        client = plateforme
        self._clore(client)
        fiche = f"/portefeuille/entreprises/{BATIMENT}/fiche-signaletique"
        client.cookies.clear()
        ouvrir_une_session(client, "jp.nkoa@batimentplus.cm")
        assert client.put(fiche, json=FICHE_COMPLETE).status_code == 200
        assert client.post(f"{fiche}/soumission").status_code == 200

        client.cookies.clear()
        ouvrir_une_session(client, "a.bouba@cga-brcg.cm")
        soumise = client.get(f"{self.URL}/fichier").content
        assert not self._contient(soumise, "Fiche R1", "Afriland")  # soumise, pas validée

        assert client.post(f"{fiche}/validation").status_code == 200
        corps = client.get(self.URL).json()
        assert not any("Fiche signalétique" in a for a in corps["avertissements"]), corps
        validee = client.get(f"{self.URL}/fichier").content
        assert self._contient(validee, "Fiche R1", "Afriland First Bank")
        assert self._contient(validee, "Fiche R1", "Immeuble Ekwalla")

    def test_la_liasse_affiche_les_codes_du_fichier_dgi(self, plateforme):
        """15 octobre 2026 : l'écran, le PDF et le fichier déposé disent la même chose."""
        client = plateforme
        ouvrir_une_session(client, "a.bouba@cga-brcg.cm")

        liasse = client.get(f"/cloture/dossiers/{BATIMENT}/liasse/2026").json()

        assert liasse["systeme"] == "NORMAL" and liasse["systeme_non_valide"] is False
        lignes = {ligne["poste"] for ligne in liasse["lignes"]}
        actif, passif, resultat = _codes_du_modele()
        assert lignes and lignes <= actif | passif | resultat
        assert "AZ" not in lignes  # un total officiel, jamais une ligne de détail

    def test_un_exercice_ouvert_ne_sort_pas(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "a.bouba@cga-brcg.cm")

        assert client.get(self.URL).json()["pret"] is False
        assert client.get(f"{self.URL}/fichier").status_code == 409

    def test_un_dossier_a_l_igs_n_a_pas_le_modele_normal(self, plateforme):
        """NGUEMA est à l'IGS : sa DSF relève du Système Minimal (décret 2019/262)."""
        client = plateforme
        ouvrir_une_session(client, "c.ndongo@cga-brcg.cm")
        url = f"/cloture/dossiers/{NGUEMA}/dsf/2025/format-dgi"

        corps = client.get(url).json()
        assert corps["pret"] is False
        assert any("Système Minimal" in o for o in corps["obstacles"]), corps
        assert client.get(f"/cloture/dossiers/{NGUEMA}/liasse/2025").json()["systeme"] == "MINIMAL"

    def test_un_autre_dossier_n_y_accede_pas(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "jp.nkoa@batimentplus.cm")
        autre = f"/cloture/dossiers/{NGUEMA}/dsf/2025/format-dgi"

        assert client.get(autre).status_code == 403
        assert client.get(f"{autre}/fichier").status_code == 403
