"""L'accès du client se suspend après trois mois d'impayés (réunion du 4 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
DEMANDES 14 ET 18 DE LA RESPONSABLE

« Après trois mois d'impayés, le compte doit se bloquer automatiquement. Le client n'a
alors plus accès à son espace de gestion. Le gestionnaire peut continuer à travailler
dans le dossier, mais pour retrouver l'accès, le client doit payer. » Et l'avertir à
l'avance.

⚠️ CE QUI EST GARDÉ
* trois mois après l'exigibilité de la plus ancienne échéance impayée : SUSPENDU ;
  quinze jours avant : AVERTI, avec la date ; un règlement ou une grâce rouvre ;
* suspendu, le client lit ses honoraires et peut payer ; le reste de son espace rend 423 ;
* le cabinet, lui, continue de travailler sur le dossier.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime

from app.contextes.souscription.domaine.situation_de_reglement import (
    EtatDAccesClient,
    GraceDeReglement,
    RegleDeDepassement,
    situation_de_reglement,
)
from tests.conftest import exige_postgresql
from tests.test_situation_de_reglement import (
    BATIMENT,
    COMPTABLE,
    ECRITURE_HORS_EXERCICE,
    MOTIF,
    NIU,
    REGLE_JANVIER,
    SUPER_ADMIN,
    _contrat,
    _en_tant_que,
    _reglement,
    _renforcer,
)

LE = datetime(2026, 1, 1, 9)
ADHERENT_BATIMENT = "jp.nkoa@batimentplus.cm"


def _regle(**autres) -> RegleDeDepassement:
    return RegleDeDepassement(
        delai_jours=365, motif="règle d'essai", fixee_par="C-010", fixee_le=LE, **autres
    )


def _situation(a_la_date: date, regle: RegleDeDepassement | None = None, **autres):
    return situation_de_reglement(
        NIU,
        _contrat(),
        autres.get("reglements", REGLE_JANVIER),
        regle or _regle(),
        autres.get("graces", []),
        a_la_date,
    )


class TestLaSuspension:
    # Janvier réglé ; février, exigible le 1er février, ne l'est pas.

    def test_ouvert_puis_averti_puis_suspendu(self):
        assert _situation(date(2026, 4, 15)).acces_client is EtatDAccesClient.OUVERT
        averti = _situation(date(2026, 4, 16))
        assert averti.acces_client is EtatDAccesClient.AVERTI
        assert averti.acces_suspendu_le == date(2026, 5, 1)
        assert averti.acces_ouvert
        suspendu = _situation(date(2026, 5, 1))
        assert suspendu.acces_client is EtatDAccesClient.SUSPENDU
        assert not suspendu.acces_ouvert

    def test_la_regle_du_cabinet_regle_le_delai_et_le_preavis(self):
        regle = _regle(acces_client_mois=2, preavis_acces_jours=0)
        assert _situation(date(2026, 3, 31), regle).acces_client is EtatDAccesClient.OUVERT
        assert _situation(date(2026, 4, 1), regle).acces_client is EtatDAccesClient.SUSPENDU

    def test_le_reglement_rouvre_l_acces(self):
        reglements = [*REGLE_JANVIER, *(_reglement(date(2026, m, 1)) for m in (2, 3, 4, 5))]
        s = _situation(date(2026, 5, 2), reglements=reglements)
        assert s.acces_client is EtatDAccesClient.OUVERT

    def test_une_grace_rouvre_l_acces(self):
        grace = GraceDeReglement(
            identifiant="GR-1",
            niu=NIU,
            jusqu_au=date(2026, 5, 31),
            motif="accord du gérant, paiement promis",
            accordee_par="C-010",
            accordee_le=LE,
        )
        s = _situation(date(2026, 5, 10), graces=[grace])
        assert s.acces_client is EtatDAccesClient.OUVERT

    def test_une_regle_enregistree_avant_garde_trois_mois(self):
        """Les règles en base avant le 4 novembre n'ont pas ces champs."""
        ancienne = RegleDeDepassement.model_validate(
            {"delai_jours": 30, "motif": "ancienne règle", "fixee_par": "C-010", "fixee_le": LE}
        )
        assert ancienne.acces_client_mois == 3 and ancienne.preavis_acces_jours == 15


@exige_postgresql
class TestParLesRoutes:
    def test_le_client_suspendu_ne_garde_que_ses_honoraires(self, plateforme):
        client = plateforme
        # Bâtiment Plus est en retard d'un mois : une règle à un mois le suspend.
        _en_tant_que(client, SUPER_ADMIN)
        _renforcer(client)
        regle = client.put(
            "/souscription/reglement/regle",
            json={
                "delai_jours": 365,
                "acces_client_mois": 1,
                "preavis_acces_jours": 0,
                "motif": MOTIF,
            },
        )
        assert regle.status_code == 200, regle.text
        assert regle.json()["regle"]["acces_client_mois"] == 1

        _en_tant_que(client, ADHERENT_BATIMENT)
        refus = client.get(f"/portefeuille/entreprises/{BATIMENT}/mon-entreprise")
        assert refus.status_code == 423, refus.text
        assert "suspendu" in refus.json()["detail"]
        vue = client.get(f"/souscription/honoraires/{BATIMENT}")
        assert vue.status_code == 200, vue.text
        assert vue.json()["situation"]["acces_client"] == "SUSPENDU"

        # Le cabinet travaille toujours sur le dossier.
        _en_tant_que(client, COMPTABLE)
        assert (
            client.post(
                f"/comptabilite/dossiers/{BATIMENT}/ecritures", json=ECRITURE_HORS_EXERCICE
            ).status_code
            != 423
        )

    def test_une_regle_refixee_sans_ces_champs_les_garde(self, plateforme):
        client = plateforme
        _en_tant_que(client, SUPER_ADMIN)
        secret = _renforcer(client)
        client.put(
            "/souscription/reglement/regle",
            json={"delai_jours": 30, "acces_client_mois": 5, "motif": MOTIF},
        )
        _renforcer(client, secret, decalage=30)
        regle = client.put(
            "/souscription/reglement/regle", json={"delai_jours": 10, "motif": MOTIF}
        ).json()["regle"]
        assert regle["acces_client_mois"] == 5 and regle["delai_jours"] == 10

