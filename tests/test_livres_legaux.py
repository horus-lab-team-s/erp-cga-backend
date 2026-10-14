"""Les livres obligatoires, imprimables : livre-journal, grand livre, balance générale.

─────────────────────────────────────────────────────────────────────────────────
DEMANDE DU CABINET, 14 OCTOBRE 2026 : « toutes les actions comptables, y compris les
grands livres ». L'Acte uniforme OHADA relatif au droit comptable (art. 19) exige les
trois livres ; un contrôleur les demande arrêtés à une date.

⚠️ CE QUI EST GARDÉ, ET OÙ UNE ERREUR COÛTERAIT

* un brouillon n'entre dans aucun livre ;
* le livre-journal s'équilibre, par journal et au total ;
* le grand livre d'une période porte le solde reporté, et le solde progressif part
  de lui ;
* la balance cumule depuis l'ouverture : les mouvements antérieurs à la période y
  entrent ; rien après la date d'arrêté ;
* deux éditions des mêmes écritures donnent les mêmes octets ;
* chacun ses dossiers : la route garde le portefeuille.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import base64
import re
import zlib
from datetime import date, datetime
from decimal import Decimal as D

import pytest

from app.contextes.comptabilite.adaptateurs.sortant.livres_legaux import (
    EnteteDuLivre,
    Livre,
    rendre_le_livre,
)
from app.contextes.comptabilite.contrats import (
    EcritureComptable,
    EtatEcriture,
    LigneEcriture,
    Sens,
)
from app.contextes.transverse.api import NIU_DEMO
from tests.conftest import exige_postgresql, ouvrir_une_session

D_, C_ = Sens.DEBIT, Sens.CREDIT
EDITE_LE = datetime(2026, 10, 14, 9, 30)


def _texte(octets: bytes) -> str:
    """Le texte dessiné (même lecteur rustique que la proforma : pas de dépendance)."""
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
    textes = re.findall(r"\((.*?)\)\s*Tj", rendu, re.S)
    return "\n".join(
        re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), t).replace("\\", "")
        for t in textes
    )


def _e(journal, numero, jour, lignes, *, validee=True, libelle=None) -> EcritureComptable:
    base = {
        "journal": journal,
        "exercice": "2026",
        "numero": numero,
        "date_operation": jour,
        "libelle": libelle or f"Écriture {journal} {numero}",
        "piece_justificative": f"P-{journal}-{numero}",
        "lignes": [
            LigneEcriture(compte=c, libelle=lib, sens=s, montant=D(m)) for c, s, m, lib in lignes
        ],
    }
    if validee:
        base |= {
            "etat": EtatEcriture.VALIDEE,
            "validee_par": "Léonard FOTSO",
            "validee_le": datetime(2026, 9, 30, 10, 0),
        }
    return EcritureComptable(**base)


#: Un petit exercice cohérent : un apport, un achat en juin, un achat en août, un
#: règlement en août, et un brouillon qui ne doit apparaître nulle part.
ECRITURES = [
    _e("BQ", 1, date(2026, 1, 2), [("521", D_, 1000000, "Apport"), ("101", C_, 1000000, "Apport")]),
    _e(
        "AC",
        1,
        date(2026, 6, 10),
        [
            ("604", D_, 150000, "Ciment"),
            ("4451", D_, 28875, "TVA récupérable"),
            ("401", C_, 178875, "QUINCAILLERIE DU WOURI"),
        ],
    ),
    _e(
        "AC",
        2,
        date(2026, 8, 5),
        [("604", D_, 50000, "Fer à béton"), ("401", C_, 50000, "QUINCAILLERIE DU WOURI")],
    ),
    _e(
        "BQ",
        2,
        date(2026, 8, 20),
        [("401", D_, 178875, "Règlement FA-0815"), ("521", C_, 178875, "Règlement FA-0815")],
    ),
    _e(
        "AC",
        3,
        date(2026, 8, 25),
        [
            ("604", D_, 999999, "BROUILLON A NE PAS IMPRIMER"),
            ("401", C_, 999999, "BROUILLON A NE PAS IMPRIMER"),
        ],
        validee=False,
    ),
]

INTITULES = {
    "101": "Capital social",
    "401": "Fournisseurs",
    "4451": "TVA récupérable sur achats",
    "521": "Banques locales",
    "604": "Achats stockés de matières et fournitures",
}


def _livre(livre, *, du=None, au=None, ecritures=ECRITURES) -> str:
    entete = EnteteDuLivre(
        niu="M081234567890P",
        denomination="SARL BATIMENT PLUS",
        exercice="2026",
        du=du,
        au=au,
        edite_le=EDITE_LE,
    )
    return _texte(rendre_le_livre(livre, ecritures, entete, lambda c: INTITULES.get(c, "")))


class TestLeLivreJournal:
    def test_il_s_equilibre_par_journal_et_au_total_sans_brouillon(self):
        texte = _livre(Livre.JOURNAL)

        assert "Livre-journal" in texte and "SARL BATIMENT PLUS" in texte
        assert "BROUILLON" not in texte
        # AC : 150 000 + 28 875 + 50 000 = 228 875 de part et d'autre.
        assert re.search(r"Total du journal AC\s+228 875\s+228 875", texte), texte
        assert re.search(r"Total du journal BQ\s+1 178 875\s+1 178 875", texte), texte
        assert re.search(r"Total général\s+1 407 750\s+1 407 750", texte), texte

    def test_la_periode_ne_retient_que_ses_ecritures(self):
        texte = _livre(Livre.JOURNAL, du=date(2026, 8, 1), au=date(2026, 8, 31))

        assert "Apport" not in texte and "Ciment" not in texte
        assert re.search(r"Total général\s+228 875\s+228 875", texte), texte


class TestLeGrandLivre:
    def test_le_solde_reporte_et_le_solde_progressif_qui_en_part(self):
        """⚠️ Août seul : le fournisseur doit déjà 178 875 au 1er août."""
        texte = _livre(Livre.GRAND_LIVRE, du=date(2026, 8, 1), au=date(2026, 8, 31))

        compte_401 = texte[texte.index("401 · Fournisseurs") :]
        assert re.search(r"Solde reporté\s+178 875 C", compte_401), compte_401
        # + 50 000 au crédit puis 178 875 au débit : 228 875 C, puis 50 000 C.
        assert "228 875 C" in compte_401
        assert re.search(r"Total du compte 401\s+178 875\s+50 000\s+50 000 C", compte_401), (
            compte_401
        )

    def test_un_compte_sans_mouvement_sur_la_periode_garde_son_report(self):
        texte = _livre(Livre.GRAND_LIVRE, du=date(2026, 8, 1))

        assert "101 · Capital social" in texte
        assert "Ciment" not in texte  # le mouvement de juin n'est que dans le report

    def test_rien_apres_la_date_d_arrete(self):
        texte = _livre(Livre.GRAND_LIVRE, au=date(2026, 7, 31))

        assert "Fer à béton" not in texte and "Règlement" not in texte


class TestLaBalance:
    def test_elle_cumule_depuis_l_ouverture_et_s_equilibre(self):
        """⚠️ Une balance d'août qui oublierait l'apport de janvier ne dirait aucun solde vrai."""
        texte = _livre(Livre.BALANCE, du=date(2026, 8, 1), au=date(2026, 8, 31))

        assert "arrêtée au 31/08/2026" in texte
        assert re.search(r"Total général\s+1 407 750\s+1 407 750\s+1 050 000\s+1 050 000", texte), (
            texte
        )
        assert re.search(r"521\s+Banques locales\s+1 000 000\s+178 875\s+821 125", texte), texte
        assert "BROUILLON" not in texte

    def test_un_total_par_classe(self):
        texte = _livre(Livre.BALANCE)

        for classe in (1, 4, 5, 6):
            assert f"Total de la classe {classe}" in texte


class TestLeDocument:
    def test_deux_editions_donnent_les_memes_octets(self):
        entete = EnteteDuLivre(
            niu="N", denomination="D", exercice="2026", du=None, au=None, edite_le=EDITE_LE
        )
        un = rendre_le_livre(Livre.GRAND_LIVRE, ECRITURES, entete, lambda c: "")
        deux = rendre_le_livre(Livre.GRAND_LIVRE, ECRITURES, entete, lambda c: "")
        assert un == deux

    def test_sans_ecriture_validee_le_livre_le_dit(self):
        texte = _livre(Livre.JOURNAL, ecritures=[ECRITURES[-1]])
        assert "Aucune écriture validée" in texte

    def test_un_long_journal_se_pagine(self):
        beaucoup = [
            _e("OD", n, date(2026, 3, 1), [("604", D_, 1000, "Ligne"), ("521", C_, 1000, "Ligne")])
            for n in range(1, 200)
        ]
        texte = _livre(Livre.JOURNAL, ecritures=beaucoup)
        assert "Page 2" in texte and "Page 1" in texte
        # L'en-tête de colonnes se répète sur chaque page.
        assert texte.count("Journal · n°") >= 2


BATIMENT = NIU_DEMO["BATIMENT"]
LIVRES = f"/comptabilite/dossiers/{BATIMENT}/livres"


@exige_postgresql
class TestLaRoute:
    @pytest.mark.parametrize("livre", ["journal", "grand-livre", "balance"])
    def test_le_comptable_telecharge_les_trois_livres(self, plateforme, livre):
        client = plateforme
        ouvrir_une_session(client, "l.fotso@cga-brcg.cm")

        reponse = client.get(f"{LIVRES}/{livre}", params={"exercice": "2026"})

        assert reponse.status_code == 200, reponse.text
        assert reponse.headers["content-type"] == "application/pdf"
        assert f'filename="{BATIMENT}-2026-{livre}.pdf"' in reponse.headers["content-disposition"]
        texte = _texte(reponse.content)
        assert "Écritures validées uniquement" in texte
        assert BATIMENT in texte

    def test_un_livre_inconnu_ou_une_periode_a_l_envers(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "l.fotso@cga-brcg.cm")

        inconnu = client.get(f"{LIVRES}/inventaire", params={"exercice": "2026"})
        envers = client.get(
            f"{LIVRES}/journal", params={"exercice": "2026", "du": "2026-08-31", "au": "2026-08-01"}
        )

        assert inconnu.status_code == 422, inconnu.text
        assert envers.status_code == 422, envers.text

    def test_un_adherent_ne_lit_pas_les_livres_d_un_autre(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "mc.essomba@lacolombe.cm")

        refus = client.get(f"{LIVRES}/balance", params={"exercice": "2026"})

        assert refus.status_code in (403, 404), refus.text
