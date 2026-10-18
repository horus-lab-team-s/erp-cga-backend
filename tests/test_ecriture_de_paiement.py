"""L'écriture de paiement qu'une quittance validée propose (18 octobre 2026, point 7).

⚠️ CE QUI EST GARDÉ
* le montant de la quittance se relève au verdict, et se relit avec la preuve ;
* seule une quittance validée propose son écriture ; débit du compte soldé (431 pour la
  CNPS), crédit de la trésorerie choisie ;
* une trésorerie inconnue, ou un montant jamais relevé, est refusé plutôt que deviné.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.contextes.transverse.api import reinitialiser_atelier
from app.partage.horloge import horloge_figee

# ⚠️ Les MODULES : leurs classes `Test…` importées ici seraient collectées deux fois.
from tests import test_echeances_adherent as echeances
from tests import test_verdict_des_quittances as verdicts

PROPOSITION = f"/obligations/dossiers/{echeances.BATIMENT}/preuves-de-paiement/ecriture-proposee"
CLE = {k: verdicts.QUITTANCE[k] for k in ("code_obligation", "periode_debut", "piece")}


@pytest.fixture(autouse=True)
def neuf():
    reinitialiser_atelier()
    with horloge_figee(datetime(2026, 8, 20, 9, 0)):
        yield


def test_la_quittance_validee_propose_son_ecriture():
    verdicts._quittance_envoyee()
    reviseur = echeances._client(echeances.REVISEUR)
    avant = reviseur.get(PROPOSITION, params=CLE)
    assert avant.status_code == 409, avant.text

    juge = reviseur.post(
        verdicts.VERDICT,
        json={**verdicts.QUITTANCE, "verdict": "VALIDEE", "montant_quittance": "84000"},
    )
    assert juge.status_code == 200, juge.text

    proposee = reviseur.get(PROPOSITION, params={**CLE, "compte_tresorerie": "5231"})
    assert proposee.status_code == 200, proposee.text
    lignes = {(x["compte"], x["sens"]): x["montant"] for x in proposee.json()["lignes"]}
    assert lignes == {("431", "DEBIT"): "84000", ("5231", "CREDIT"): "84000"}
    assert proposee.json()["piece_justificative"] == verdicts.QUITTANCE["piece"]

    inconnue = reviseur.get(PROPOSITION, params={**CLE, "compte_tresorerie": "999"})
    assert inconnue.status_code == 422


def test_sans_montant_releve_rien_ne_se_devine():
    verdicts._quittance_envoyee()
    reviseur = echeances._client(echeances.REVISEUR)
    reviseur.post(verdicts.VERDICT, json={**verdicts.QUITTANCE, "verdict": "VALIDEE"})

    sans = reviseur.get(PROPOSITION, params=CLE)
    avec = reviseur.get(PROPOSITION, params={**CLE, "montant": "84000"})

    assert sans.status_code == 422 and "montant" in sans.text
    assert avec.status_code == 200, avec.text
