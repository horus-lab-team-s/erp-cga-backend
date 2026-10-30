"""Le bulletin de paie imprimable (15 octobre 2026) : le même calcul que l'écran.

⚠️ CE QUI EST GARDÉ
* le PDF porte les montants de `calculer_bulletin`, au franc : net, retenues, charges ;
* une ligne « à valider » le dit, et le bulletin se déclare non opposable ;
* l'alerte SMIG y figure ;
* la route garde l'accès à la paie du dossier.
"""

from __future__ import annotations

import base64
import re
import zlib
from datetime import date
from decimal import Decimal

import pytest

from app.contextes.referentiel.api import (
    DepotBaremesYaml,
    DepotParametresYaml,
    ServiceBaremes,
    ServiceParametres,
)
from app.contextes.social.adaptateurs.sortant.document_bulletin import rendre_le_bulletin
from app.contextes.social.api import Contrat, Periode, TypeContrat, calculer_bulletin
from app.infrastructure.config import configuration
from app.partage.formats import ESPACE_FINE, montant
from tests.conftest import exige_postgresql, ouvrir_une_session


def _texte(octets: bytes) -> str:
    morceaux = []
    for flux in re.findall(rb"stream\r?\n(.*?)endstream", octets, re.S):
        brut = flux.strip()
        try:
            brut = base64.a85decode(brut, adobe=True)
        except ValueError:
            pass
        try:
            brut = zlib.decompress(brut)
        except zlib.error:
            pass
        morceaux.append(brut)
    rendu = b"\n".join(morceaux).decode("latin-1", "replace")
    return " ".join(
        re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), t).replace("\\", "")
        for t in re.findall(r"\((.*?)\)\s*Tj", rendu, re.S)
    )


@pytest.fixture(scope="module")
def references():
    dossier = configuration().dossier_referentiel
    return (
        ServiceParametres.depuis_depot(DepotParametresYaml(dossier / "parametres.yaml")),
        ServiceBaremes.depuis_depot(DepotBaremesYaml(dossier / "baremes.yaml")),
    )


def _bulletin(references, base: str):
    parametres, baremes = references
    contrat = Contrat(
        salarie="SAL-0099",
        type_contrat=TypeContrat.CDI,
        debut=date(2026, 1, 1),
        salaire_base=Decimal(base),
        primes=Decimal(0),
        poste="Gardien",
    )
    return calculer_bulletin(
        contrat, "M081234567890P", Periode(annee=2026, mois=10), parametres, baremes
    )


def _f(valeur) -> str:
    return montant(valeur).replace(ESPACE_FINE, " ")


class TestLeDocument:
    def test_les_montants_sont_ceux_du_calcul(self, references):
        bulletin = _bulletin(references, "400000")
        texte = _texte(
            rendre_le_bulletin(
                bulletin,
                employeur="SARL BATIMENT PLUS",
                niu="M081234567890P",
                salarie="Paul ESSOMBA",
                matricule_cnps="1234567",
                poste="Gardien",
            )
        )

        assert "BULLETIN DE PAIE · OCTOBRE 2026" in texte
        assert "SARL BATIMENT PLUS" in texte and "Paul ESSOMBA" in texte
        assert _f(bulletin.net_a_payer) in texte
        assert _f(bulletin.retenues_salariales) in texte
        assert _f(bulletin.cout_employeur) in texte
        assert "(à valider)" in texte and "pas opposable" in texte

    def test_l_alerte_smig_y_figure(self, references):
        texte = _texte(
            rendre_le_bulletin(
                _bulletin(references, "55000"),
                employeur="E",
                niu="N",
                salarie="S",
                matricule_cnps=None,
                poste=None,
            )
        )
        assert "SMIG" in texte


@exige_postgresql
class TestLaRoute:
    def test_le_comptable_imprime_et_l_adherent_d_un_autre_dossier_non(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "l.fotso@cga-brcg.cm")
        url = "/social/dossiers/M081234567890P/bulletins/2026/9/SAL-0004/pdf"

        reponse = client.get(url)
        assert reponse.status_code == 200, reponse.text
        assert reponse.headers["content-type"] == "application/pdf"
        assert "SEPTEMBRE 2026" in _texte(reponse.content)

        client.cookies.clear()
        ouvrir_une_session(client, "mc.essomba@lacolombe.cm")
        assert client.get(url).status_code in (403, 404)
