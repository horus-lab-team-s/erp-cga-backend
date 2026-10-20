"""La cliente d'une création reçoit son compte au paiement, et le garde.

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE CABINET A DEMANDÉ, LE 14 OCTOBRE 2026

« Ouverture automatique du compte client au paiement, avec définition du mot de
passe. » Jusque-là, elle suivait sa création par un lien signé, sans compte, et
son compte n'arrivait qu'avec sa société, des semaines plus tard.

Un rôle nouveau, `CLIENT_CREATION`, porte une permission nouvelle,
`SUIVRE_MA_CREATION`, sur une portée : son seul dossier de formalité.

⚠️ CE QUI EST GARDÉ ICI, DANS L'ORDRE OÙ ELLE LE VIT

1. le paiement lui ouvre un compte, sur son seul dossier, et le message porte
   le lien pour choisir son mot de passe ET le lien de suivi sans compte ;
2. une adresse déjà prise par un autre compte n'en reçoit pas ce dossier ;
3. connectée, elle lit son dossier, y dépose ses pièces, et rien d'autre ;
4. à l'immatriculation, SON compte devient adhérent, sans en créer un second ;
   un compte qui ne suit pas cette création ne le devient pas.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest

from app.contextes.transverse.adaptateurs.entrant.abonne_acces_adherent import (
    ouvrir_l_acces_de_la_fondatrice,
)
from app.contextes.transverse.adaptateurs.entrant.compte_de_creation import (
    ouvrir_le_compte_de_la_creation,
)
from app.contextes.transverse.domaine.roles import Role
from app.infrastructure.config import configuration
from app.orchestration.boite_d_envoi import EvenementSortant
from tests.conftest import exige_postgresql, ouvrir_une_session

REFERENCE = "CRE-dos-essai-compte"
COURRIEL = "sylvie.ngono@exemple.cm"
NIU = "M091122334455X"
MOT_DE_PASSE = "Ma-boutique-ouvre-en-2026!"


@pytest.fixture
def cabinet():
    """Les dépôts du cabinet en mémoire, et un notifieur qui retient tout."""
    from app.contextes.transverse.adaptateurs.sortant.depots_memoire import (
        DepotComptesMemoire,
        DepotHabilitationsMemoire,
        DepotJetonsMemoire,
        JournalAuditMemoire,
    )

    locataire = configuration().locataire_par_defaut
    boutique = SimpleNamespace(
        comptes=DepotComptesMemoire(locataire),
        habilitations=DepotHabilitationsMemoire(locataire),
        jetons=DepotJetonsMemoire(locataire),
        journal=JournalAuditMemoire(locataire),
    )
    postes: list[dict] = []

    class Notifieur:
        def envoyer(self, gabarit, *, destinataire, contexte):
            postes.append({"gabarit": gabarit, "destinataire": destinataire, "contexte": contexte})
            return True

    @contextmanager
    def unite(_locataire: str | None = None):
        yield boutique

    return SimpleNamespace(boutique=boutique, unite=unite, notifieur=Notifieur(), postes=postes)


def _ouvrir_son_compte(cabinet, reference: str = REFERENCE, courriel: str = COURRIEL):
    return ouvrir_le_compte_de_la_creation(
        reference=reference, courriel=courriel, nom="Sylvie NGONO", unite_de_travail=cabinet.unite
    )


def _habilitations(cabinet, courriel: str = COURRIEL):
    compte = cabinet.boutique.comptes.par_courriel(courriel)
    assert compte is not None, f"aucun compte pour {courriel}"
    return compte, cabinet.boutique.habilitations.pour_compte(compte.identifiant)


def _immatriculee(**remplacements) -> EvenementSortant:
    charge = {
        "niu": NIU,
        "denomination": "NGONO COUTURE SARL",
        "courriel_fondateur": COURRIEL,
        "nom_fondateur": "NGONO",
        "prenom_fondateur": "Sylvie",
        "telephone_fondateur": "+237699001122",
        "adherente": True,
    }
    charge.update(remplacements)
    return EvenementSortant(
        identifiant=f"ent-{uuid4().hex[:6]}", nom="EntrepriseCréée", cle=REFERENCE, charge=charge
    )


class TestLePaiementOuvreSonCompte:
    def test_un_compte_sur_son_seul_dossier_et_un_lien_pour_le_mot_de_passe(self, cabinet):
        lien = _ouvrir_son_compte(cabinet)

        assert lien, "aucun lien : elle ne peut pas choisir son mot de passe"
        requete = parse_qs(urlparse(lien).query)
        assert requete["jeton"][0], lien
        # ⚠️ La page d'activation parle de SA création, et non d'une souscription.
        assert requete["parcours"] == ["creation"], lien
        compte, habilitations = _habilitations(cabinet)
        assert compte.etat.value == "EN_ATTENTE_ACTIVATION"
        ((habilitation,),) = [habilitations]
        assert habilitation.role is Role.CLIENT_CREATION
        assert habilitation.portee == frozenset({REFERENCE})

    def test_une_adresse_deja_prise_ne_recoit_pas_ce_dossier(self, cabinet):
        """⚠️ Un collaborateur, ou une autre cliente, peut porter l'adresse
        déclarée. Lui ajouter ce dossier donnerait une société en création à qui
        a saisi la même adresse."""
        _ouvrir_son_compte(cabinet, reference="CRE-dos-autre-cliente")

        assert _ouvrir_son_compte(cabinet) is None
        _, habilitations = _habilitations(cabinet)
        assert all(REFERENCE not in (h.portee or ()) for h in habilitations), habilitations

    def test_le_paiement_rejoue_lui_rend_un_lien_neuf_sans_second_compte(self, cabinet):
        """Le relais garantit « au moins une fois », et le premier courriel a pu
        se perdre : tant qu'elle n'a pas choisi son mot de passe, un lien neuf."""
        premier = _ouvrir_son_compte(cabinet)
        second = _ouvrir_son_compte(cabinet)

        assert second and second != premier
        assert len([c for c in cabinet.boutique.comptes.lister() if c.courriel == COURRIEL]) == 1


class TestLeMessageDOuverture:
    """L'abonné de l'encaissement envoie le message qui porte les deux liens."""

    @pytest.fixture
    def encaissement(self, monkeypatch):
        from app.contextes.creation_entreprise.adaptateurs.entrant import (
            routes_http as routes_creation,
        )
        from app.contextes.creation_entreprise.adaptateurs.entrant.abonne_de_souscription import (
            ouvrir_le_dossier_de_creation,
        )
        from app.contextes.transverse import api as transverse

        routes_creation._depot_memoire.cache_clear()
        postes: list[dict] = []

        class Notifieur:
            def envoyer(self, gabarit, *, destinataire, contexte):
                postes.append({"gabarit": gabarit, "contexte": contexte})
                return True

        monkeypatch.setattr(transverse, "service_de_notification", lambda: Notifieur())
        evenement = EvenementSortant(
            identifiant="ev-compte",
            nom="PaiementEncaissé",
            cle=f"dos-{uuid4().hex[:8]}",
            charge={
                "service": "CREATION",
                "faits_fondateurs": {
                    "denomination_souhaitee": "NGONO COUTURE SARL",
                    "forme_juridique": "SARL",
                    "activite": "Confection",
                    "siege": "Bonabéri, Douala",
                },
                "courriel_titulaire": COURRIEL,
                "nom_titulaire": "Sylvie NGONO",
                "telephone_titulaire": "+237699001122",
            },
        )
        yield SimpleNamespace(
            postes=postes,
            ouvrir=lambda: ouvrir_le_dossier_de_creation(evenement),
            monkeypatch=monkeypatch,
            transverse=transverse,
        )
        routes_creation._depot_memoire.cache_clear()

    def test_il_porte_le_mot_de_passe_et_le_suivi_sans_compte(self, encaissement):
        encaissement.monkeypatch.setattr(
            encaissement.transverse,
            "ouvrir_le_compte_de_la_creation",
            lambda **_: "https://app.exemple/activation?jeton=abc&parcours=creation",
        )
        encaissement.ouvrir()

        ((poste,),) = [encaissement.postes]
        assert poste["gabarit"] == "creation.dossier_ouvert_avec_compte"
        assert poste["contexte"]["lien_compte"].endswith("parcours=creation")
        # ⚠️ Le lien sans mot de passe reste : le compte s'ajoute, il ne remplace pas.
        assert "/mon-dossier/CRE-" in poste["contexte"]["lien"]

    def test_sans_compte_ouvert_le_message_d_avant_part_quand_meme(self, encaissement):
        encaissement.monkeypatch.setattr(
            encaissement.transverse, "ouvrir_le_compte_de_la_creation", lambda **_: None
        )
        encaissement.ouvrir()

        ((poste,),) = [encaissement.postes]
        assert poste["gabarit"] == "creation.dossier_ouvert"
        assert "/mon-dossier/CRE-" in poste["contexte"]["lien"]


class TestALImmatriculationSonCompteDevientAdherent:
    def _immatriculer(self, cabinet, evenement=None):
        ouvrir_l_acces_de_la_fondatrice(
            evenement or _immatriculee(), unite_de_travail=cabinet.unite, notifier=cabinet.notifieur
        )

    def test_le_meme_compte_recoit_l_espace_adherent(self, cabinet):
        """⚠️ L'abonné s'arrêtait en silence sur toute adresse connue : sa société
        entrait au portefeuille, et son espace adhérent ne s'ouvrait jamais."""
        _ouvrir_son_compte(cabinet)
        self._immatriculer(cabinet)

        _, habilitations = _habilitations(cabinet)
        roles = {h.role: h.portee for h in habilitations}
        assert roles.get(Role.ADHERENT) == frozenset({NIU}), habilitations
        # Elle garde la lecture de sa création, close, qui porte ses numéros.
        assert roles.get(Role.CLIENT_CREATION) == frozenset({REFERENCE})
        assert len(cabinet.boutique.comptes.lister()) == 1

    def test_jamais_activee_elle_recoit_un_lien_neuf(self, cabinet):
        """Le lien du paiement a pu expirer depuis des semaines."""
        _ouvrir_son_compte(cabinet)
        self._immatriculer(cabinet)

        ((poste,),) = [cabinet.postes]
        assert poste["gabarit"] == "compte.activation_creation"
        assert "jeton=" in poste["contexte"]["lien"]

    def test_deja_activee_on_lui_dit_de_se_connecter(self, cabinet):
        from app.contextes.transverse.domaine.identites import EtatCompte

        _ouvrir_son_compte(cabinet)
        compte, _ = _habilitations(cabinet)
        cabinet.boutique.comptes.enregistrer(compte.model_copy(update={"etat": EtatCompte.ACTIF}))
        self._immatriculer(cabinet)

        ((poste,),) = [cabinet.postes]
        assert poste["gabarit"] == "compte.adhesion_ajoutee"
        assert poste["contexte"]["lien"].endswith("/connexion")

    def test_l_evenement_rejoue_n_envoie_rien_de_plus(self, cabinet):
        _ouvrir_son_compte(cabinet)
        self._immatriculer(cabinet)
        self._immatriculer(cabinet)

        _, habilitations = _habilitations(cabinet)
        assert len([h for h in habilitations if h.role is Role.ADHERENT]) == 1
        assert len(cabinet.postes) == 1

    def test_un_compte_qui_ne_suit_pas_cette_creation_ne_devient_pas_adherent(self, cabinet):
        """⚠️ L'adresse seule ne prouve rien : c'est l'habilitation sur CE dossier
        de formalité qui dit que c'est elle."""
        _ouvrir_son_compte(cabinet, reference="CRE-dos-une-autre")
        self._immatriculer(cabinet)

        _, habilitations = _habilitations(cabinet)
        assert all(h.role is not Role.ADHERENT for h in habilitations), habilitations
        assert cabinet.postes == []


# ── Connectée ────────────────────────────────────────────────────────────────

CHARGEE = "p.moukouri@cga-brcg.cm"
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def _un_dossier(client, courriel: str) -> str:
    """Un dossier de formalité, ouvert comme l'encaissement l'ouvre."""
    ouvrir_une_session(client, CHARGEE)
    reponse = client.post(
        "/creations",
        json={
            "reference": f"CRE-{uuid4().hex[:12]}",
            "fondateur": {
                "nom": "NGONO",
                "prenom": "Sylvie",
                "courriel": courriel,
                "telephone": "+237699001122",
            },
            "denomination_souhaitee": "NGONO COUTURE SARL",
            "forme_juridique": "SARL",
            "activite": "Confection",
            "siege": "Bonabéri, Douala IV",
        },
    )
    assert reponse.status_code in (200, 201), reponse.text
    client.cookies.clear()
    return reponse.json()["reference"]


def _connectee(client, reference: str, courriel: str) -> None:
    """Son compte ouvert, son mot de passe choisi par le lien, sa session ouverte."""
    lien = ouvrir_le_compte_de_la_creation(
        reference=reference, courriel=courriel, nom="Sylvie NGONO"
    )
    assert lien, "le compte ne s'est pas ouvert"
    secret = parse_qs(urlparse(lien).query)["jeton"][0]
    definition = client.post(
        "/transverse/mot-de-passe/definition", json={"secret": secret, "mot_de_passe": MOT_DE_PASSE}
    )
    assert definition.status_code == 200, definition.text
    session = client.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE}
    )
    assert session.status_code == 200, session.text


@exige_postgresql
class TestConnecteeElleSuitSaCreation:
    def test_elle_lit_son_dossier_et_lui_seul(self, plateforme):
        client = plateforme
        courriel = f"sylvie.{uuid4().hex[:6]}@exemple.cm"
        sien = _un_dossier(client, courriel)
        _un_dossier(client, "autre@exemple.cm")
        _connectee(client, sien, courriel)

        vue = client.get("/creations/mes-creations")

        assert vue.status_code == 200, vue.text
        assert [d["reference"] for d in vue.json()] == [sien]
        assert vue.json()[0]["pieces"], "elle doit savoir ce qu'on attend d'elle"

    def test_elle_depose_une_piece_depuis_son_compte(self, plateforme):
        client = plateforme
        courriel = f"sylvie.{uuid4().hex[:6]}@exemple.cm"
        sien = _un_dossier(client, courriel)
        _connectee(client, sien, courriel)
        code = client.get("/creations/mes-creations").json()[0]["pieces"][0]["code"]

        fichier = client.post(
            "/collecte/fichiers-de-creation",
            params={"reference": sien},
            files={"fichier": ("cni.pdf", PDF, "application/pdf")},
        )
        assert fichier.status_code == 201, fichier.text
        depot = client.post(
            f"/creations/mes-creations/{sien}/pieces",
            json={"code": code, "empreinte": fichier.json()["empreinte"]},
        )

        assert depot.status_code == 200, depot.text
        (piece,) = [p for p in depot.json()["pieces"] if p["code"] == code]
        assert piece["fournie"] is True

    def test_le_dossier_d_une_autre_n_existe_pas_pour_elle(self, plateforme):
        """⚠️ 404, et non 403 : un refus qui distinguerait apprendrait qu'il existe."""
        client = plateforme
        courriel = f"sylvie.{uuid4().hex[:6]}@exemple.cm"
        sien = _un_dossier(client, courriel)
        autre = _un_dossier(client, "autre@exemple.cm")
        _connectee(client, sien, courriel)
        # ⚠️ Un code que la liste de l'autre dossier ATTEND (même forme, même
        # liste) : un code inconnu rendrait 404 pour une autre raison, et le cas
        # resterait vert sans la garde de portée. Éprouvé : il l'était.
        code = client.get("/creations/mes-creations").json()[0]["pieces"][0]["code"]

        fichier = client.post(
            "/collecte/fichiers-de-creation",
            params={"reference": autre},
            files={"fichier": ("cni.pdf", PDF, "application/pdf")},
        )
        depot = client.post(
            f"/creations/mes-creations/{autre}/pieces", json={"code": code, "empreinte": "0" * 64}
        )
        assert fichier.status_code == 404, fichier.text
        assert depot.status_code == 404, depot.text

    def test_elle_n_ouvre_aucun_ecran_du_cabinet(self, plateforme):
        """Un rôle nouveau se juge sur ce qu'il n'ouvre pas."""
        client = plateforme
        courriel = f"sylvie.{uuid4().hex[:6]}@exemple.cm"
        sien = _un_dossier(client, courriel)
        _connectee(client, sien, courriel)

        for chemin in ("/creations/pipeline", f"/creations/{sien}", "/acquisition/dossiers"):
            reponse = client.get(chemin)
            assert reponse.status_code in (403, 404), (chemin, reponse.status_code)
