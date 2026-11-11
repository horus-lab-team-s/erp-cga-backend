"""Les soldes à regarder et l'agent comptable (18 octobre 2026, point 15).

⚠️ CE QUI EST GARDÉ
* une caisse créditrice, un fournisseur débiteur, un client créditeur, une charge
  créditrice, un produit débiteur se relèvent ;
* les exceptions du plan ne se relèvent pas : rabais (60x9, 70x9), variations de stocks
  (603, 73), avances déjà reclassées (409, 419) ;
* le compte d'attente ne se relève qu'à la clôture ;
* sans clé, l'agent le dit et rien ne bloque ; avec une réponse, il propose ;
* ce qui part chez l'agent ne porte ni le NIU ni la dénomination ;
* l'adhérent n'y accède pas.
"""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace

from app.contextes.comptabilite.domaine.projections import SoldeCompte
from app.contextes.comptabilite.domaine.soldes_a_regarder import soldes_a_regarder
from tests.conftest import exige_postgresql, ouvrir_une_session

BATIMENT = "M081234567890P"
REVISEUR = "a.bouba@cga-brcg.cm"
ADHERENT = "jp.nkoa@batimentplus.cm"


def _solde(compte: str, solde: int) -> SoldeCompte:
    d, c = (Decimal(solde), Decimal(0)) if solde > 0 else (Decimal(0), Decimal(-solde))
    return SoldeCompte(compte=compte, total_debit=d, total_credit=c)


def _releves(*soldes, cloture=False) -> set[str]:
    return {r.compte for r in soldes_a_regarder(list(soldes), cloture=cloture)}


class TestLesRegles:
    def test_les_sens_contraires_a_la_nature_se_relevent(self):
        assert _releves(
            _solde("5711", -5000),
            _solde("4011", 12000),
            _solde("4111", -3000),
            _solde("6011", -100),
            _solde("7011", 200),
            _solde("2845", 50),
        ) == {"5711", "4011", "4111", "6011", "7011", "2845"}

    def test_le_bon_sens_ne_se_releve_pas(self):
        assert not _releves(_solde("5711", 5000), _solde("4011", -12000), _solde("4111", 3000))

    def test_les_exceptions_du_plan(self):
        assert not _releves(
            _solde("6019", -400),  # rabais obtenus, créditeurs par nature
            _solde("7019", 300),  # rabais accordés, débiteurs par nature
            _solde("6031", -900),  # variation des stocks de marchandises
            _solde("7340", 900),  # variation des stocks de produits
            _solde("4091", 1000),  # avance déjà reclassée
            _solde("4191", -1000),
        )

    def test_le_compte_d_attente_ne_se_releve_qu_a_la_cloture(self):
        assert not _releves(_solde("4710", 800))
        assert _releves(_solde("4710", 800), cloture=True) == {"4710"}


class _ClientFactice:
    def __init__(self, reponse: dict) -> None:
        self.appels: list[dict] = []
        bloc = SimpleNamespace(type="text", text=json.dumps(reponse))

        def creer(**demande):
            self.appels.append(demande)
            return SimpleNamespace(content=[bloc], stop_reason="end_turn")

        self.beta = SimpleNamespace(messages=SimpleNamespace(create=creer))


@exige_postgresql
class TestParLesRoutes:
    BASE = f"/comptabilite/dossiers/{BATIMENT}/balance"

    def test_le_releve_tourne_sans_agent(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, REVISEUR)
        releve = client.get(f"{self.BASE}/soldes-a-regarder", params={"exercice": "2026"})
        assert releve.status_code == 200, releve.text
        assert isinstance(releve.json()["soldes"], list)

        sans_cle = client.post(f"{self.BASE}/relecture-de-l-agent", params={"exercice": "2026"})
        assert sans_cle.status_code == 200, sans_cle.text
        assert sans_cle.json()["disponible"] is False

    def test_l_agent_propose_sans_lire_l_identite_du_dossier(self, plateforme, monkeypatch):
        from app.contextes.comptabilite.adaptateurs.entrant import routes_http
        from app.contextes.comptabilite.adaptateurs.sortant.relecture_balance_claude import (
            RelectureDeLaBalance,
        )

        faux = _ClientFactice(
            {
                "remarques": [
                    {
                        "compte": "6811",
                        "constat": "Aucune dotation aux amortissements.",
                        "suggestion": "Vérifier le tableau des immobilisations.",
                    },
                    {"compte": "", "constat": "", "suggestion": ""},
                ]
            }
        )
        monkeypatch.setattr(
            routes_http,
            "service_de_relecture_de_balance",
            lambda: RelectureDeLaBalance(cle_api="x", modele="m", client=faux),
        )
        client = plateforme
        ouvrir_une_session(client, REVISEUR)
        relue = client.post(f"{self.BASE}/relecture-de-l-agent", params={"exercice": "2026"})
        assert relue.status_code == 200, relue.text
        (remarque,) = relue.json()["remarques"]
        assert remarque["compte"] == "6811" and relue.json()["disponible"] is True

        (appel,) = faux.appels
        envoye = json.dumps(appel["messages"], ensure_ascii=False)
        assert "Balance (compte" in envoye
        assert BATIMENT not in envoye and "BATIMENT PLUS" not in envoye

    def test_l_adherent_n_y_accede_pas(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, ADHERENT)
        params = {"exercice": "2026"}
        assert client.get(f"{self.BASE}/soldes-a-regarder", params=params).status_code == 403
        assert client.post(f"{self.BASE}/relecture-de-l-agent", params=params).status_code == 403
