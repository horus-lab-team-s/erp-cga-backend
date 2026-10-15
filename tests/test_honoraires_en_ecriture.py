"""Un règlement d'honoraires propose son écriture dans les livres de l'adhérent (15 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
DEMANDE DU CABINET : « pas de double saisie ». L'honoraire réglé au guichet ou par
mobile money se retrouve, en brouillon, dans la comptabilité de l'adhérent.

⚠️ CE QUI EST GARDÉ
* le guichet comme le mobile money publient le règlement, par un seul chemin ;
* l'écriture naît en BROUILLON (632 au débit, la trésorerie du canal au crédit) ;
* rejouée, elle ne se double pas (référence HON-<règlement>) ;
* un double règlement ne publie rien.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.contextes.souscription.application.reglement_des_honoraires import (
    NOM_HONORAIRES_REGLES,
    SuiteDHonoraires,
    constater_le_reglement,
)
from app.contextes.souscription.domaine.honoraires import (
    CanalDeReglement,
    PeriodeDejaReglee,
    ReglementDHonoraires,
)
from app.contextes.transverse.api import NIU_DEMO, JournalAuditMemoire
from tests.conftest import exige_postgresql, ouvrir_une_session

BATIMENT = NIU_DEMO["BATIMENT"]


class _Boite:
    def __init__(self):
        self.evenements = []

    def deposer(self, evenement):
        self.evenements.append(evenement)


class _Reglements:
    def __init__(self):
        self.tous = []

    def enregistrer(self, r):
        if any(x.periode == r.periode and x.niu == r.niu for x in self.tous):
            raise PeriodeDejaReglee(f"{r.niu} {r.periode} déjà réglée")
        self.tous.append(r)

    def pour(self, niu):
        return [r for r in self.tous if r.niu == niu]


def _reglement(**champs):
    base = {
        "identifiant": "RH-1",
        "niu": BATIMENT,
        "periode": date(2026, 10, 1),
        "montant": Decimal("45000"),
        "canal": CanalDeReglement.GUICHET,
        "reference_externe": "Reçu 2026-0601",
        "constate_le": datetime(2026, 10, 15, 9, 0),
        "constate_par": "C-009",
    }
    return ReglementDHonoraires(**(base | champs))


class TestLaPublication:
    def test_un_reglement_constate_est_publie(self):
        boite = _Boite()
        constater_le_reglement(_Reglements(), boite, _reglement())

        (evenement,) = boite.evenements
        assert evenement.nom == NOM_HONORAIRES_REGLES
        assert evenement.charge["montant"] == "45000" and evenement.charge["canal"] == "GUICHET"

    def test_un_double_reglement_ne_publie_rien(self):
        boite, reglements = _Boite(), _Reglements()
        constater_le_reglement(reglements, boite, _reglement())

        with pytest.raises(PeriodeDejaReglee):
            constater_le_reglement(reglements, boite, _reglement(identifiant="RH-2"))
        assert len(boite.evenements) == 1

    def test_le_mobile_money_passe_par_le_meme_chemin(self):
        boite = _Boite()
        suite = SuiteDHonoraires(
            reglements=_Reglements(), journal=JournalAuditMemoire(), boite=boite
        )
        paiement = SimpleNamespace(
            reference_reglee=BATIMENT,
            echeance="2026-10-01",
            reference_externe="TARA-77",
            identifiant="P-77",
            montant=Decimal("45000"),
        )

        assert suite.encaisser(paiement, datetime(2026, 10, 15, 10, 0)).abouti
        (evenement,) = boite.evenements
        assert evenement.charge["canal"] == "MOBILE_MONEY"


@exige_postgresql
class TestLEcritureProposee:
    def test_le_guichet_propose_un_brouillon_chez_l_adherent_sans_doublon(self, plateforme):
        from app.contextes.comptabilite.adaptateurs.entrant.abonne_honoraires import (
            proposer_l_ecriture_des_honoraires,
        )
        from app.orchestration.boite_d_envoi import EvenementSortant

        client = plateforme
        ouvrir_une_session(client, "jp.nkoa@batimentplus.cm")
        vue = client.get(f"/souscription/honoraires/{BATIMENT}").json()
        echeance = next(e for e in vue["echeances"] if e["etat"] in ("A_REGLER", "EN_RETARD"))
        client.cookies.clear()
        ouvrir_une_session(client, "n.essama@cga-brcg.cm")
        assert (
            client.post(
                f"/souscription/honoraires/{BATIMENT}/encaissement",
                json={
                    "periode": echeance["debut"],
                    "reference_externe": "Reçu de caisse 2026-0777",
                },
            ).status_code
            == 200
        )
        client.cookies.clear()
        ouvrir_une_session(client, "s.onana@cga-brcg.cm")
        assert client.post("/orchestration/publication").status_code == 200

        client.cookies.clear()
        ouvrir_une_session(client, "l.fotso@cga-brcg.cm")

        def proposees():
            reponse = client.get(
                f"/comptabilite/dossiers/{BATIMENT}/ecritures", params={"exercice": "2026"}
            )
            corps = reponse.json()
            ecritures = corps if isinstance(corps, list) else corps.get("ecritures", [])
            return [e for e in ecritures if (e.get("reference_externe") or "").startswith("HON-")]

        (ecriture,) = proposees()
        assert ecriture["etat"] == "BROUILLON" and ecriture["journal"] == "CA"
        lignes = {
            (ligne["compte"], ligne["sens"]): Decimal(ligne["montant"])
            for ligne in ecriture["lignes"]
        }
        montant = Decimal(echeance["montant"])
        assert lignes == {("632", "DEBIT"): montant, ("571", "CREDIT"): montant}

        # Rejouée (le relais garantit « au moins une fois ») : pas de seconde écriture.
        identifiant = ecriture["reference_externe"].removeprefix("HON-")
        rejouee = EvenementSortant(
            identifiant=f"hr-{identifiant}",
            nom=NOM_HONORAIRES_REGLES,
            cle=identifiant,
            charge={
                "identifiant": identifiant,
                "niu": BATIMENT,
                "periode": echeance["debut"],
                "montant": str(montant),
                "canal": "GUICHET",
                "reference": "Reçu de caisse 2026-0777",
                "constate_le": ecriture["date_operation"] + "T09:00:00",
            },
            depose_le=datetime(2026, 10, 15, 9, 0),
        )
        assert proposer_l_ecriture_des_honoraires(rejouee) is None
        assert len(proposees()) == 1

        # L'avis à l'adhérent : parti une fois, pas une seconde au rejeu.
        from app.contextes.souscription.adaptateurs.entrant.abonne_avis_honoraires import (
            prevenir_du_reglement,
        )
        from app.contextes.transverse.adaptateurs.entrant.dependances import (
            service_de_notification,
        )

        (avis,) = service_de_notification().derniers("adherent.honoraires_regles")
        assert avis.destinataire == "jp.nkoa@batimentplus.cm"
        assert avis.contexte["lien"].endswith("/fr/mon-espace/honoraires")
        assert prevenir_du_reglement(rejouee) is False
        assert len(service_de_notification().derniers("adherent.honoraires_regles")) == 1
