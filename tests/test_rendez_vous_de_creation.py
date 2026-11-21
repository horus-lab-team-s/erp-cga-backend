"""Le rendez-vous de création, le code de dossier, la saisie au cabinet (9 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
CE QUE L'UTILISATEUR A DEMANDÉ

« Un sélecteur visible qui fixe un rendez-vous en présentiel ou en ligne, avec les heures,
pénalisé en cas de non-respect ; il paie comme l'autre les frais de dossier. Quand il revient
remplir le formulaire, il dit qu'il a déjà payé, et le système fait le rapprochement : même
s'il y a deux paiements, la somme est déduite. » Les créneaux sont proposés par défaut et le
responsable de la création ferme ce qu'il veut. Il saisit aussi les demandes à la main.

⚠️ CE QUI EST GARDÉ
* l'agenda par défaut propose les jours ouvrés, 9 h–12 h et 14 h–16 h, au moins 24 h à
  l'avance ; un créneau fermé, un jour fermé, un créneau pris disparaissent ;
* un rendez-vous exige une adresse électronique ; un créneau ne se prend qu'une fois ;
* manqué (après son heure seulement), il rend due la pénalité fixée par le super
  administrateur ; tant qu'elle n'est pas réglée, pas de nouveau rendez-vous ;
* le code de dossier rattache la demande suivante, et les frais déjà versés y sont
  retrouvés : rien à repayer ;
* le responsable saisit une demande au cabinet ; la caisse, non.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import text

from app.contextes.souscription.domaine.rendez_vous import (
    AGENDA_PAR_DEFAUT,
    CreneauRefuse,
    EtatRendezVous,
    ModeRendezVous,
    RendezVous,
    creneaux_libres,
    verifier_le_creneau,
)
from app.partage.horloge import horloge_figee
from tests.conftest import exige_postgresql, ouvrir_une_session
from tests.test_situation_de_reglement import SUPER_ADMIN, _renforcer

#: Lundi 9 novembre 2026, 8 h à Douala.
LUNDI_8H = datetime(2026, 11, 9, 8, 0)


def _rdv(debut: datetime, etat=EtatRendezVous.PREVU) -> RendezVous:
    return RendezVous(
        identifiant=f"rdv-{debut:%d%H}",
        dossier="dos-x",
        mode=ModeRendezVous.PRESENTIEL,
        debut=debut,
        fin=debut + timedelta(hours=1),
        etat=etat,
        pris_le=LUNDI_8H,
    )


class TestLAgenda:
    def test_la_proposition_par_defaut(self):
        libres = creneaux_libres(AGENDA_PAR_DEFAUT, [], LUNDI_8H)
        # Pas avant 24 h : mardi 9 h est le premier.
        assert libres[0] == datetime(2026, 11, 10, 9, 0)
        mardi = [c for c in libres if c.date() == date(2026, 11, 10)]
        assert [c.hour for c in mardi] == [9, 10, 11, 14, 15]
        # Ni samedi ni dimanche.
        assert not [c for c in libres if c.weekday() >= 5]

    def test_ferme_pris_ou_annule(self):
        mardi_9 = datetime(2026, 11, 10, 9, 0)
        mardi_10 = datetime(2026, 11, 10, 10, 0)
        agenda = AGENDA_PAR_DEFAUT.model_copy(
            update={"creneaux_fermes": (mardi_9,), "jours_fermes": (date(2026, 11, 11),)}
        )
        libres = creneaux_libres(agenda, [_rdv(mardi_10)], LUNDI_8H)
        assert mardi_9 not in libres and mardi_10 not in libres
        assert not [c for c in libres if c.date() == date(2026, 11, 11)]
        # Annulé, le créneau se libère.
        libres = creneaux_libres(agenda, [_rdv(mardi_10, EtatRendezVous.ANNULE)], LUNDI_8H)
        assert mardi_10 in libres

    def test_un_creneau_non_propose_est_refuse(self):
        with pytest.raises(CreneauRefuse, match="plus disponible"):
            verifier_le_creneau(
                AGENDA_PAR_DEFAUT,
                [],
                datetime(2026, 11, 10, 12, 30),
                ModeRendezVous.PRESENTIEL,
                LUNDI_8H,
            )


# ── Par les routes ──────────────────────────────────────────────────────────────────

DIRECTRICE = "b.mballa@cga-brcg.cm"
CAISSIERE = "n.essama@cga-brcg.cm"
MOTIF = "décision du 9 novembre 2026 sur les rendez-vous"

#: 7 h UTC = 8 h à Douala, le lundi 9 novembre 2026.
INSTANT = datetime(2026, 11, 9, 7, 0)
MARDI_9H = datetime(2026, 11, 10, 9, 0)


def _demande(**autres) -> dict:
    corps = {
        "nom": "Ngono Couture",
        "telephone": "699445566",
        "courriel": "ngono@exemple.cm",
        "service_souhaite": "CREATION",
        "canal_prefere": "APPEL",
    }
    corps.update(autres)
    return corps


def _regle(client, **autres):
    client.cookies.clear()
    ouvrir_une_session(client, SUPER_ADMIN)
    _renforcer(client)
    corps = {
        "frais_par_service": {"CREATION": "25000"},
        "seuil_demarrage_pourcent": 100,
        "motif": MOTIF,
    }
    corps.update(autres)
    reponse = client.put("/souscription/versements/regle", json=corps)
    assert reponse.status_code == 200, reponse.text


def _en(client, courriel):
    client.cookies.clear()
    ouvrir_une_session(client, courriel)


def _code_et_dossier(moteur_test) -> tuple[str, str]:
    with moteur_test.connect() as connexion:
        return tuple(connexion.execute(text("SELECT code, dossier FROM code_de_dossier")).one())


@exige_postgresql
class TestParLesRoutes:
    def test_un_rendez_vous_se_prend_une_fois_et_avec_une_adresse(self, plateforme):
        client = plateforme
        with horloge_figee(INSTANT):
            creneaux = client.get("/acquisition/rendez-vous/creneaux").json()
            assert creneaux["creneaux"][0] == MARDI_9H.isoformat()
            rdv = {"mode": "PRESENTIEL", "debut": MARDI_9H.isoformat()}

            sans_adresse = client.post(
                "/acquisition/demandes", json=_demande(courriel=None, rendez_vous=rdv)
            )
            assert sans_adresse.status_code == 422, sans_adresse.text

            pris = client.post("/acquisition/demandes", json=_demande(rendez_vous=rdv))
            assert pris.status_code == 201, pris.text
            assert pris.json()["rendez_vous"]["debut"] == MARDI_9H.isoformat()

            autre = client.post(
                "/acquisition/demandes",
                json=_demande(telephone="677889900", courriel="b@exemple.cm", rendez_vous=rdv),
            )
            assert autre.status_code == 409, autre.text
            libres = client.get("/acquisition/rendez-vous/creneaux").json()["creneaux"]
            assert MARDI_9H.isoformat() not in libres

    def test_le_responsable_ferme_un_creneau_propose(self, plateforme):
        client = plateforme
        with horloge_figee(INSTANT):
            _en(client, DIRECTRICE)
            assert client.get("/acquisition/agenda").json()["par_defaut"] is True
            ferme = client.post(
                "/acquisition/agenda/fermetures", json={"debut": MARDI_9H.isoformat()}
            )
            assert ferme.status_code == 200, ferme.text
            libres = client.get("/acquisition/rendez-vous/creneaux").json()["creneaux"]
            assert MARDI_9H.isoformat() not in libres
            # La caisse ne tient pas l'agenda.
            _en(client, CAISSIERE)
            refus = client.post(
                "/acquisition/agenda/fermetures", json={"debut": MARDI_9H.isoformat()}
            )
            assert refus.status_code == 403, refus.text

    def test_manque_la_penalite_bloque_puis_se_regle(self, plateforme, moteur_test):
        client = plateforme
        rdv = {"mode": "EN_LIGNE", "debut": MARDI_9H.isoformat()}
        with horloge_figee(INSTANT):
            _regle(client, penalite_rendez_vous="10000")
            client.cookies.clear()
            assert (
                client.post("/acquisition/demandes", json=_demande(rendez_vous=rdv)).status_code
                == 201
            )
            code, dossier = _code_et_dossier(moteur_test)
            _en(client, DIRECTRICE)
            (pris,) = client.get("/acquisition/rendez-vous").json()
            identifiant = pris["rendez_vous"]["identifiant"]
            trop_tot = client.post(
                f"/acquisition/rendez-vous/{identifiant}/constat", json={"issue": "MANQUE"}
            )
            assert trop_tot.status_code == 409, trop_tot.text

        # Mardi, 11 h à Douala : le client n'est pas venu.
        with horloge_figee(datetime(2026, 11, 10, 10, 0)):
            _en(client, DIRECTRICE)
            manque = client.post(
                f"/acquisition/rendez-vous/{identifiant}/constat", json={"issue": "MANQUE"}
            )
            assert manque.status_code == 200, manque.text

            client.cookies.clear()
            nouveau = {"mode": "EN_LIGNE", "debut": datetime(2026, 11, 12, 9, 0).isoformat()}
            bloque = client.post(
                "/acquisition/demandes",
                json=_demande(code_de_dossier=code, rendez_vous=nouveau),
            )
            assert bloque.status_code == 409 and "pénalité" in bloque.text, bloque.text

            _en(client, CAISSIERE)
            reglee = client.post(
                f"/acquisition/dossiers/{dossier}/penalite/encaissement",
                json={"reference_externe": "Reçu 2026-3001"},
            )
            assert reglee.status_code == 201, reglee.text
            # Hors du « payé » : la pénalité ne paie pas la prestation.
            assert reglee.json()["paye"] == "0" and reglee.json()["penalites_versees"] == "10000"

            client.cookies.clear()
            repris = client.post(
                "/acquisition/demandes",
                json=_demande(code_de_dossier=code, rendez_vous=nouveau),
            )
            assert repris.status_code == 201, repris.text

    def test_le_code_rattache_et_retrouve_les_frais_deja_verses(self, plateforme, moteur_test):
        client = plateforme
        _regle(client)
        client.cookies.clear()
        premier = client.post("/acquisition/demandes", json=_demande())
        assert premier.status_code == 201 and premier.json()["jeton_de_frais"], premier.text
        code, dossier = _code_et_dossier(moteur_test)

        _en(client, CAISSIERE)
        frais = client.post(
            f"/acquisition/dossiers/{dossier}/frais-de-traitement/encaissement",
            json={"reference_externe": "Reçu 2026-3002"},
        )
        assert frais.status_code == 201, frais.text

        client.cookies.clear()
        second = client.post(
            "/acquisition/demandes",
            json=_demande(
                telephone="677112233",
                code_de_dossier=f"{code[:4].lower()}-{code[4:]}",
                declarations={"forme_juridique": "SARL"},
            ),
        )
        assert second.status_code == 201, second.text
        accuse = second.json()
        assert accuse["rattachee"] is True
        assert accuse["frais_deja_verses"] == "25000"
        # Déjà versés : rien à repayer.
        assert accuse["jeton_de_frais"] is None

        faux = client.post("/acquisition/demandes", json=_demande(code_de_dossier="ZZZZ-ZZZZ"))
        assert faux.status_code == 404, faux.text

    def test_le_responsable_saisit_une_demande_au_cabinet(self, plateforme):
        client = plateforme
        _en(client, DIRECTRICE)
        saisie = client.post(
            "/acquisition/demandes/au-cabinet",
            json=_demande(declarations={"forme_juridique": "SARLU"}),
        )
        assert saisie.status_code == 201, saisie.text
        reference = saisie.json()["reference"]
        fiche = client.get(f"/acquisition/dossiers/{reference}").json()
        assert fiche["demande"]["origine"] == "cabinet"

        _en(client, CAISSIERE)
        refus = client.post("/acquisition/demandes/au-cabinet", json=_demande())
        assert refus.status_code == 403, refus.text


def test_les_heures_de_l_agenda_sont_celles_de_douala():
    # 8 h à Douala = 7 h UTC : le premier créneau reste mardi 9 h, pas 10 h.
    from app.partage.horloge import a_douala

    assert a_douala(INSTANT) == LUNDI_8H
    assert time(9) == MARDI_9H.time()


@exige_postgresql
class TestLeLienEtLeRappel:
    """9 novembre 2026 : le lien de visioconférence part chez le client dès qu'il est posé,
    et la veille, un rappel — une seule fois."""

    def _rendez_vous_en_ligne(self, client) -> str:
        client.cookies.clear()
        pris = client.post(
            "/acquisition/demandes",
            json=_demande(rendez_vous={"mode": "EN_LIGNE", "debut": MARDI_9H.isoformat()}),
        )
        assert pris.status_code == 201, pris.text
        _en(client, DIRECTRICE)
        (ligne,) = client.get("/acquisition/rendez-vous").json()
        return ligne["rendez_vous"]["identifiant"]

    def test_le_lien_pose_part_chez_le_client(self, plateforme):
        from app.contextes.transverse.api import service_de_notification

        client = plateforme
        with horloge_figee(INSTANT):
            identifiant = self._rendez_vous_en_ligne(client)
            pose = client.put(
                f"/acquisition/rendez-vous/{identifiant}/lien",
                json={"lien": "https://meet.exemple.cm/cga-1234"},
            )
            assert pose.status_code == 200, pose.text
            assert pose.json()["rendez_vous"]["lien_envoye_le"] is not None
        (message,) = service_de_notification().derniers("rendez_vous.lien")
        assert message.destinataire == "ngono@exemple.cm"

    def test_le_rappel_part_la_veille_et_une_seule_fois(self, plateforme):
        from app.contextes.souscription.adaptateurs.entrant import (
            travail_de_rappel_des_rendez_vous as travail,
        )
        from app.contextes.transverse.api import service_de_notification, unite_de_travail
        from app.partage.locataire import etabli

        def rappeler() -> str:
            # Comme l'ordonnanceur : hors requête, dans une unité de travail du cabinet.
            with etabli("CGA-BRCG"), unite_de_travail("CGA-BRCG"):
                return travail.rappeler()

        client = plateforme
        with horloge_figee(INSTANT):
            self._rendez_vous_en_ligne(client)
            # Lundi 8 h : le rendez-vous est mardi 9 h, à 25 h — trop tôt.
            assert rappeler().startswith("0 ")
        # Lundi 10 h à Douala : à 23 h du rendez-vous, le rappel part.
        with horloge_figee(INSTANT + timedelta(hours=2)):
            assert rappeler().startswith("1 ")
            assert rappeler().startswith("0 ")
        (message,) = service_de_notification().derniers("rendez_vous.rappel")
        assert message.destinataire == "ngono@exemple.cm"
