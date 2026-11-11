"""La quittance de l'adhérent, vérifiée par le cabinet : « payée », ou refusée avec son motif.

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE CABINET A DEMANDÉ, LE 14 OCTOBRE 2026

« Paiement de la TVA et de la CNPS avec changement de statut. » Quand l'adhérent règle
lui-même l'administration, sa quittance restait « le cabinet la vérifie » jusqu'au dépôt de
la déclaration : personne ne disait jamais « c'est payé », et personne n'avait de geste pour
dire « cette quittance ne va pas ».

⚠️ CE QUI EST GARDÉ

* validée, l'échéance passe à PAYÉE pour l'adhérent ;
* refusée, elle redevient à régler, et l'adhérent lit le motif ;
* un refus sans motif est refusé ; un verdict ne se donne qu'une fois ;
* l'adhérent ne juge pas sa propre quittance.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.contextes.obligations.domaine.echeances_adherent import (
    EtatPourLAdherent,
    PreuveEnvoyee,
    VerdictDeLaPreuve,
)
from app.contextes.transverse.api import reinitialiser_atelier
from app.partage.horloge import horloge_figee

# ⚠️ Le MODULE : ses classes `Test…` importées ici seraient collectées deux fois.
from tests import test_echeances_adherent as echeances

VERDICT = f"/obligations/dossiers/{echeances.BATIMENT}/preuves-de-paiement/verdict"
A_VERIFIER = "/obligations/preuves-a-verifier"
QUITTANCE = {**echeances.CNPS_JUILLET, "piece": "PJ-2026-0013"}


def _preuve(**verdict):
    return PreuveEnvoyee(
        code_obligation="CNPS",
        periode_debut=date(2026, 5, 1),
        piece="PJ-1",
        envoyee_le=datetime(2026, 8, 19, 10, 0),
        par="A-001",
        **verdict,
    )


class TestLaCarteSuitLeVerdict:
    def test_validee_l_echeance_est_payee(self):
        vue = echeances._vue(
            [echeances._mois("CNPS", 5)], preuves=[_preuve(verdict=VerdictDeLaPreuve.VALIDEE)]
        )
        (carte,) = vue
        assert carte.etat is EtatPourLAdherent.PAYEE
        assert carte.preuve.verdict is VerdictDeLaPreuve.VALIDEE

    def test_refusee_elle_redevient_a_regler_et_porte_le_motif(self):
        """⚠️ Une quittance refusée ne prouve rien ; sans le motif, l'adhérent renverrait la même."""
        vue = echeances._vue(
            [echeances._mois("CNPS", 5)],
            preuves=[_preuve(verdict=VerdictDeLaPreuve.REFUSEE, motif="Quittance du mois d'avril")],
        )
        (carte,) = vue
        assert carte.etat is EtatPourLAdherent.EN_RETARD
        assert carte.preuve.motif == "Quittance du mois d'avril"


@pytest.fixture(autouse=True)
def neuf():
    reinitialiser_atelier()
    with horloge_figee(datetime(2026, 8, 20, 9, 0)):
        yield


def _quittance_envoyee():
    adherent = echeances._client(echeances.ADHERENT)
    assert adherent.post(echeances.PREUVES, json=QUITTANCE).status_code == 201
    return adherent


class TestLeCabinetJuge:
    def test_il_valide_et_l_adherent_lit_payee(self):
        adherent = _quittance_envoyee()
        reviseur = echeances._client(echeances.REVISEUR)
        (attente,) = [q for q in reviseur.get(A_VERIFIER).json() if q["piece"] == "PJ-2026-0013"]
        assert attente["dossier"] == echeances.BATIMENT

        verdict = reviseur.post(VERDICT, json={**QUITTANCE, "verdict": "VALIDEE"})

        assert verdict.status_code == 200, verdict.text
        carte = echeances._carte(adherent.get(echeances.ECHEANCES).json(), "CNPS", "2026-07-01")
        assert carte["etat"] == "PAYEE"
        assert all(q["piece"] != "PJ-2026-0013" for q in reviseur.get(A_VERIFIER).json())
        from app.contextes.transverse.api import atelier

        (avis,) = atelier().notifications.derniers("adherent.quittance_verifiee")
        assert avis.destinataire == "jp.nkoa@batimentplus.cm"
        assert "SARL BATIMENT PLUS" == avis.contexte["denomination"]

    def test_un_refus_dit_pourquoi_et_l_adherent_peut_renvoyer(self):
        adherent = _quittance_envoyee()
        reviseur = echeances._client(echeances.REVISEUR)

        sans_motif = reviseur.post(VERDICT, json={**QUITTANCE, "verdict": "REFUSEE"})
        refus = reviseur.post(
            VERDICT,
            json={**QUITTANCE, "verdict": "REFUSEE", "motif": "Quittance de juin, pas de juillet."},
        )

        assert sans_motif.status_code == 422, sans_motif.text
        assert refus.status_code == 200, refus.text
        carte = echeances._carte(adherent.get(echeances.ECHEANCES).json(), "CNPS", "2026-07-01")
        assert carte["etat"] == "EN_RETARD"
        assert carte["preuve"]["motif"] == "Quittance de juin, pas de juillet."
        # 15 octobre 2026 : l'adhérent l'apprend par courriel, avec le motif.
        from app.contextes.transverse.api import atelier

        (avis,) = atelier().notifications.derniers("adherent.quittance_refusee")
        assert avis.destinataire == "jp.nkoa@batimentplus.cm"
        assert avis.contexte["motif"] == "Quittance de juin, pas de juillet."
        assert avis.contexte["lien"].endswith("/fr/mon-espace/echeances")
        # La bonne quittance, même pièce corrigée ou non : l'envoi est de nouveau reçu.
        assert adherent.post(echeances.PREUVES, json=QUITTANCE).status_code == 201

    def test_un_avis_en_panne_ne_fait_pas_perdre_le_verdict(self, monkeypatch):
        """Revue du 15 octobre 2026 : l'avis levait après l'écriture du verdict, et la
        transaction annulée l'emportait avec elle."""
        from app.contextes.obligations.adaptateurs.entrant import routes_espace_adherent

        def en_panne(*_a, **_k):
            raise RuntimeError("serveur de courriel en panne")

        monkeypatch.setattr(routes_espace_adherent, "_prevenir_du_verdict", en_panne)
        adherent = _quittance_envoyee()
        reviseur = echeances._client(echeances.REVISEUR)

        verdict = reviseur.post(VERDICT, json={**QUITTANCE, "verdict": "VALIDEE"})

        assert verdict.status_code == 200, verdict.text
        carte = echeances._carte(adherent.get(echeances.ECHEANCES).json(), "CNPS", "2026-07-01")
        assert carte["etat"] == "PAYEE"

    def test_un_verdict_ne_se_donne_qu_une_fois(self):
        _quittance_envoyee()
        reviseur = echeances._client(echeances.REVISEUR)
        assert reviseur.post(VERDICT, json={**QUITTANCE, "verdict": "VALIDEE"}).status_code == 200

        second = reviseur.post(VERDICT, json={**QUITTANCE, "verdict": "REFUSEE", "motif": "Erreur"})

        assert second.status_code == 409, second.text

    def test_l_adherent_ne_juge_pas_sa_propre_quittance(self):
        adherent = _quittance_envoyee()

        assert adherent.post(VERDICT, json={**QUITTANCE, "verdict": "VALIDEE"}).status_code == 403
        assert adherent.get(A_VERIFIER).status_code == 403


class TestLaQuittanceSeCompareALAccuse:
    """⚠️ 19 octobre 2026 (point 7) : le montant relevé ne se comparait à rien. L'échéance
    porte désormais la référence de son accusé : la comparaison est sûre, et un écart ne
    passe pas sans un mot de celui qui valide."""

    @staticmethod
    def _accuse_cnps_de_juillet(montant: str):
        from tests.conftest import enroler_par_le_courriel

        reviseur = echeances._client(echeances.REVISEUR)
        enroler_par_le_courriel(reviseur)
        consigne = reviseur.post(
            f"/obligations/dossiers/{echeances.BATIMENT}/depots",
            json={
                **echeances.CNPS_JUILLET,
                "numero": "CNPS-2026-07-000417",
                "depose_le": "2026-08-12T09:00:00",
                "montant_constate": montant,
            },
        )
        assert consigne.status_code == 200, consigne.text
        return reviseur

    def test_la_liste_montre_ce_que_porte_l_accuse(self):
        _quittance_envoyee()
        reviseur = self._accuse_cnps_de_juillet("110618")
        (attente,) = [q for q in reviseur.get(A_VERIFIER).json() if q["piece"] == "PJ-2026-0013"]
        assert attente["montant_attendu"] == "110618"

    def test_un_ecart_se_justifie_ou_ne_se_valide_pas(self):
        from app.contextes.transverse.api import atelier

        _quittance_envoyee()
        reviseur = self._accuse_cnps_de_juillet("110618")
        sans_mot = reviseur.post(
            VERDICT, json={**QUITTANCE, "verdict": "VALIDEE", "montant_quittance": "108000"}
        )
        assert sans_mot.status_code == 422, sans_mot.text
        assert "écart" in sans_mot.json()["detail"]
        assert "110" in sans_mot.json()["detail"] and "108" in sans_mot.json()["detail"]

        justifie = reviseur.post(
            VERDICT,
            json={
                **QUITTANCE,
                "verdict": "VALIDEE",
                "montant_quittance": "108000",
                "motif": "Paiement partiel, le solde suit le 30 août.",
            },
        )
        assert justifie.status_code == 200, justifie.text
        (entree,) = [
            e for e in atelier().journal.lister(objet_type="preuve_de_paiement")
            if e.action == "preuve.jugee" or e.apres.get("verdict") == "VALIDEE"
        ][-1:]
        assert entree.apres["ecart"] == "-2618"

    def test_le_bon_montant_se_valide_sans_rien_dire(self):
        _quittance_envoyee()
        reviseur = self._accuse_cnps_de_juillet("110618")
        verdict = reviseur.post(
            VERDICT, json={**QUITTANCE, "verdict": "VALIDEE", "montant_quittance": "110618"}
        )
        assert verdict.status_code == 200, verdict.text

    def test_deposee_la_quittance_reste_a_juger_puis_se_lit_payee(self):
        """⚠️ 20 octobre 2026, constaté au tournage : le dépôt consigné retirait la carte, et
        sa quittance avec elle. Le cabinet ne la voyait plus à juger, au moment même où
        l'accusé permettait de la comparer ; l'adhérent ne lisait jamais « Payée »."""
        adherent = _quittance_envoyee()
        reviseur = self._accuse_cnps_de_juillet("110618")

        def juillet():
            vue = adherent.get(f"/obligations/dossiers/{echeances.BATIMENT}/mes-echeances").json()
            return [
                e
                for e in vue["echeances"]
                if e["code_obligation"] == "CNPS" and e["periode_debut"] == "2026-07-01"
            ]

        (carte,) = juillet()
        assert carte["etat"] == "PREUVE_ENVOYEE"
        assert carte["montant_constate"] == "110618"
        verdict = reviseur.post(
            VERDICT, json={**QUITTANCE, "verdict": "VALIDEE", "montant_quittance": "110618"}
        )
        assert verdict.status_code == 200, verdict.text
        (carte,) = juillet()
        assert carte["etat"] == "PAYEE"
