"""Confier un dossier à compter d'aujourd'hui, et non depuis le recrutement.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE (pas 70)

Affecter un dossier ajoutait son NIU à la portée de l'habilitation. La portée n'étant
pas datée, un dossier confié le 14 septembre 2026 au comptable C-004, recruté en 2021,
le faisait apparaître **habilité sur ce dossier depuis 2021**. La route « qui a accès
à ce dossier à telle date » le citait pour janvier 2024. L'historique des
habilitations existe pour répondre à « qui était habilité le jour d'un dépôt » :
l'affectation lui faisait mentir.

La même méthode acceptait aussi une habilitation déjà fermée, celle d'un adhérent ou
d'un inspecteur, et un dossier qui y figurait déjà. Et un administrateur pouvait
fermer sa propre habilitation, comme il pouvait se suspendre avant le pas 69.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app.contextes.transverse.api import (
    MOT_DE_PASSE_DEMO,
    NIU_DEMO,
    FermetureRefusee,
    Habilitation,
    MotifHabilitation,
    Role,
    affecter_dossier,
    atelier,
    dossiers_accessibles,
    fermer_habilitation,
    reinitialiser_atelier,
)
from app.main import creer_application
from app.partage.horloge import horloge_figee
from tests.test_transverse import _acces, _administrateur_distinct, depots  # noqa: F401

LE_JOUR = date(2026, 9, 14)
INSTANT = datetime(2026, 9, 14, 10, 0)


def _hab(**champs) -> Habilitation:
    base = dict(
        identifiant="H-1", compte="C-7", role=Role.COMPTABLE, portee=frozenset({"M08"}),
        debut=date(2021, 9, 1), fin=None, motif=MotifHabilitation.RECRUTEMENT,
        accordee_par="C-0",
    )
    base.update(champs)
    return Habilitation(**base)


def _affecter(habilitation: Habilitation, niu: str = "M07", le: date = LE_JOUR):
    return habilitation.affecter(niu, le=le, identifiant_successeur="H-2", par="C-DIR")


class TestLePasseNEstPasReecrit:
    def test_une_habilitation_qui_a_couru_est_relayee_le_jour_meme(self):
        fermee, successeur = _affecter(_hab())
        assert (fermee.identifiant, fermee.fin) == ("H-1", LE_JOUR)
        assert fermee.portee == frozenset({"M08"})
        assert fermee.motif is MotifHabilitation.AFFECTATION_DOSSIER
        assert (successeur.identifiant, successeur.debut, successeur.fin) == ("H-2", LE_JOUR, None)
        assert successeur.portee == frozenset({"M08", "M07"})
        assert (successeur.compte, successeur.role) == ("C-7", Role.COMPTABLE)
        assert successeur.accordee_par == "C-DIR"

    def test_la_veille_le_dossier_n_etait_pas_accessible_le_jour_meme_il_l_est(self):
        """⚠️ La question que l'historique existe pour trancher, posée aux deux dates."""
        habilitations = list(_affecter(_hab()))
        assert "M07" not in dossiers_accessibles(habilitations, date(2024, 1, 10))
        assert "M07" not in dossiers_accessibles(habilitations, date(2026, 9, 13))
        assert dossiers_accessibles(habilitations, LE_JOUR) == frozenset({"M08", "M07"})
        # ⚠️ La contre-épreuve : le dossier d'origine reste lisible sans interruption.
        assert "M08" in dossiers_accessibles(habilitations, date(2024, 1, 10))

    def test_une_fin_prevue_passe_a_la_successeur(self):
        _, successeur = _affecter(_hab(fin=date(2026, 12, 31)))
        assert successeur.fin == date(2026, 12, 31)

    def test_une_habilitation_qui_commence_aujourd_hui_s_etend_sur_place(self):
        """Il n'y a aucun passé à réécrire : pas de successeur inutile."""
        (etendue,) = _affecter(_hab(debut=LE_JOUR))
        assert etendue.identifiant == "H-1" and etendue.portee == frozenset({"M08", "M07"})
        (a_venir,) = _affecter(_hab(debut=date(2026, 10, 1)))
        assert a_venir.identifiant == "H-1"


class TestLesRefus:
    def test_une_habilitation_fermee_ne_se_rouvre_pas_par_affectation(self):
        with pytest.raises(ValueError, match="fermée le 31/08/2026"):
            _affecter(_hab(fin=date(2026, 8, 31)))

    @pytest.mark.parametrize("role", [Role.ADHERENT, Role.INSPECTEUR])
    def test_ni_l_adherent_ni_l_inspecteur_ne_recoivent_un_autre_dossier(self, role):
        with pytest.raises(ValueError, match="ne s'étend pas par affectation"):
            _affecter(_hab(role=role))

    def test_un_dossier_deja_confie_n_est_pas_reaffecte(self):
        with pytest.raises(ValueError, match="déjà dans la portée"):
            _affecter(_hab(), niu="M08")


class TestLeCasDUsage:
    def test_la_successeur_est_enregistree_et_rendue(self, depots):  # noqa: F811
        _, habilitations, _, _, journal = depots
        habilitations.enregistrer(_hab())
        active = affecter_dossier(
            par=_acces([Role.DIRECTION]), identifiant_habilitation="H-1",
            identifiant_successeur="H-2", niu="M07",
            habilitations=habilitations, journal=journal, a_l_instant=INSTANT,
        )
        assert active.identifiant == "H-2"
        assert habilitations.lire("H-1").fin == LE_JOUR
        assert {h.identifiant for h in habilitations.pour_compte("C-7")} == {"H-1", "H-2"}
        entree = journal.lister()[-1]
        assert entree.action == "dossier.affecte"
        assert entree.apres["habilitation_active"] == "H-2"

    def test_on_ne_ferme_pas_sa_propre_habilitation(self, depots):  # noqa: F811
        _, habilitations, _, _, journal = depots
        habilitations.enregistrer(_hab(compte="C-ADMIN", role=Role.ADMINISTRATEUR, portee=None))
        with pytest.raises(FermetureRefusee):
            fermer_habilitation(
                par=_administrateur_distinct(), identifiant="H-1", le=LE_JOUR,
                motif=MotifHabilitation.DEPART, habilitations=habilitations,
                journal=journal, a_l_instant=INSTANT,
            )
        assert habilitations.lire("H-1").fin is None


class TestParLaRoute:
    @pytest.fixture
    def client(self):
        reinitialiser_atelier()
        with horloge_figee(INSTANT):
            yield TestClient(creer_application())

    def _connecter(self, client, courriel):
        assert client.post(
            "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
        ).status_code == 200

    def _acces_au(self, client, niu, jour):
        reponse = client.get(f"/transverse/dossiers/{niu}/acces", params={"a_la_date": jour})
        assert reponse.status_code == 200, reponse.text
        return {h["compte"] for h in reponse.json()}

    def test_le_comptable_n_apparait_pas_sur_le_dossier_avant_son_affectation(self, client):
        niu = NIU_DEMO["CLINIQUE"]
        self._connecter(client, "b.mballa@cga-brcg.cm")  # direction
        assert "C-004" not in self._acces_au(client, niu, "2024-01-10")

        reponse = client.post(f"/transverse/habilitations/H-004/dossiers/{niu}")
        assert reponse.status_code == 200, reponse.text
        active = reponse.json()
        assert active["identifiant"] != "H-004" and active["debut"] == "2026-09-14"

        assert "C-004" not in self._acces_au(client, niu, "2024-01-10"), "le passé réécrit"
        assert "C-004" in self._acces_au(client, niu, "2026-09-14")

    def test_la_seconde_affectation_du_meme_dossier_est_refusee(self, client):
        niu = NIU_DEMO["CLINIQUE"]
        self._connecter(client, "b.mballa@cga-brcg.cm")
        active = client.post(f"/transverse/habilitations/H-004/dossiers/{niu}").json()
        refus = client.post(f"/transverse/habilitations/{active['identifiant']}/dossiers/{niu}")
        assert refus.status_code == 422
        assert "déjà dans la portée" in refus.json()["detail"]
        # L'ancienne, relayée ce jour, ne se rouvre pas, et le refus désigne la successeur.
        ancienne = client.post(f"/transverse/habilitations/H-004/dossiers/{niu}")
        assert ancienne.status_code == 422
        assert active["identifiant"] in ancienne.json()["detail"]
        assert "nouvelle habilitation" not in ancienne.json()["detail"]

    def test_la_liste_des_comptes_rend_les_habilitations_actives(self, client):
        self._connecter(client, "s.onana@cga-brcg.cm")
        lignes = client.get("/transverse/comptes", params={"a_la_date": "2026-09-14"}).json()
        fotso = next(x for x in lignes if x["compte"]["identifiant"] == "C-004")
        assert [h["identifiant"] for h in fotso["habilitations"]] == ["H-004"]

    def test_l_administrateur_ne_ferme_pas_sa_propre_habilitation(self, client):
        self._connecter(client, "s.onana@cga-brcg.cm")
        reponse = client.post(
            "/transverse/habilitations/H-002/fermeture",
            json={"le": "2026-09-30", "motif": "DEPART"},
        )
        assert reponse.status_code == 409, reponse.text
        assert atelier().habilitations.lire("H-002").fin is None
