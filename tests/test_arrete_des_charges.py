"""L'arrêté des charges sociales du mois (18 octobre 2026, point 8).

⚠️ CE QUI EST GARDÉ
* la proposition additionne les bonnes lignes du bulletin, parts salariale et patronale ;
* arrêter sans écart n'exige rien ; un écart sans motif est refusé ;
* par les routes : le comptable lit la proposition, arrête en corrigeant avec un motif,
  et relit l'arrêté ; l'adhérent lit, il n'arrête pas.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from app.contextes.social.domaine.arrete_des_charges import (
    ArreteDesCharges,
    RubriqueSociale,
)
from tests.conftest import exige_postgresql, ouvrir_une_session

BATIMENT = "M081234567890P"
COMPTABLE = "l.fotso@cga-brcg.cm"
ADHERENT = "jp.nkoa@batimentplus.cm"
URL = f"/social/dossiers/{BATIMENT}/declaration/2026/8/arrete"


def _montants(**ecarts) -> dict[RubriqueSociale, Decimal]:
    base = {r: Decimal(1000) for r in RubriqueSociale}
    return base | {RubriqueSociale(k): Decimal(v) for k, v in ecarts.items()}


class TestLArrete:
    def test_sans_ecart_aucun_motif_n_est_demande(self):
        arrete = ArreteDesCharges(
            entreprise=BATIMENT,
            periode="2026-08",
            montants=_montants(),
            proposition=_montants(),
            arrete_par="C-004",
            arrete_le=datetime(2026, 9, 5),
        )
        assert arrete.ecarts == []

    def test_un_ecart_sans_motif_est_refuse(self):
        with pytest.raises(ValueError, match="CNPS"):
            ArreteDesCharges(
                entreprise=BATIMENT,
                periode="2026-08",
                montants=_montants(CNPS=1500),
                proposition=_montants(),
                arrete_par="C-004",
                arrete_le=datetime(2026, 9, 5),
            )


@exige_postgresql
class TestParLesRoutes:
    def test_le_comptable_corrige_et_arrete_l_adherent_lit(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)
        lu = client.get(URL)
        assert lu.status_code == 200, lu.text
        proposition = lu.json()["proposition"]
        assert Decimal(proposition["CNPS"]) > 0 and Decimal(proposition["IRPP"]) >= 0
        assert lu.json()["arrete"] is None

        corrige = {**proposition, "CNPS": str(Decimal(proposition["CNPS"]) + 5000)}
        sans_motif = client.put(URL, json={"montants": corrige})
        assert sans_motif.status_code == 422 and "motif" in sans_motif.text
        arrete = client.put(
            URL, json={"montants": corrige, "motif": "rappel CNPS d'août notifié le 3 septembre"}
        )
        assert arrete.status_code == 200, arrete.text
        assert Decimal(arrete.json()["arrete"]["montants"]["CNPS"]) == Decimal(corrige["CNPS"])

        client.cookies.clear()
        ouvrir_une_session(client, ADHERENT)
        relu = client.get(URL).json()
        assert relu["arrete"]["motif"] == "rappel CNPS d'août notifié le 3 septembre"
        assert client.put(URL, json={"montants": proposition}).status_code in (403, 404)

    def test_l_arrete_devient_le_montant_de_l_echeance(self, plateforme):
        """18 octobre 2026 : « seul l'arrêté sert ensuite ». Il préremplit l'appel de fonds
        par le montant de l'échéance ; un mois sans arrêté n'a pas de montant."""
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)
        proposition = client.get(URL).json()["proposition"]
        corrige = {**proposition, "CNPS": str(Decimal(proposition["CNPS"]) + 5000)}
        client.put(URL, json={"montants": corrige, "motif": "rappel CNPS d'août notifié"})

        lignes = client.get(
            f"/obligations/dossiers/{BATIMENT}/echeancier",
            params={"exercice": "2026", "a_la_date": "2026-10-18"},
        ).json()

        def obligation(code, debut):
            return next(
                ligne["obligation"]
                for ligne in lignes
                if ligne["obligation"]["code_obligation"] == code
                and ligne["obligation"]["periode_debut"] == debut
            )

        cnps = obligation("CNPS", "2026-08-01")
        assert Decimal(cnps["montant_estime"]) == Decimal(corrige["CNPS"])
        assert "Arrêté par le cabinet" in cnps["explication_estimation"]
        retenues = obligation("IRPP_SALAIRES", "2026-08-01")
        attendu = sum(Decimal(corrige[r]) for r in ("IRPP", "CFC", "FNE", "TDL", "RAV"))
        assert Decimal(retenues["montant_estime"]) == attendu
        assert obligation("CNPS", "2026-07-01")["montant_estime"] is None
