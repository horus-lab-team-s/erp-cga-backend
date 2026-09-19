"""Le mandat exercé : un compte d'ailleurs agit ici, et le journal le dit (pas 127).

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER PROUVE

Le mandat existait dans le domaine depuis le chantier multi-tenant, éprouvé, documenté, et
**branché nulle part**. Ce fichier éprouve la chaîne entière : un compte du cabinet ouvre
sa session chez lui, se présente sur le sous-domaine d'une entreprise, et y agit si, et
seulement si, cette entreprise lui a accordé un mandat qui couvre le rôle demandé.

⚠️ **CES CAS S'EXÉCUTENT SUR POSTGRESQL, ET C'EST OBLIGATOIRE.** En mémoire, l'atelier est
unique pour le processus et tous ses dépôts sont liés au locataire par défaut : deux
locataires n'y existent pas vraiment, et le mandat n'aurait rien à franchir. Un test vert
en mémoire ne prouverait rien du chemin que prend la production.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as SessionSql

from app.contextes.tenants.adaptateurs.sortant.depot_tenants_sql import DepotTenantsSql
from app.contextes.tenants.api import NatureTenant
from app.contextes.tenants.application.cycle_de_vie import activer, avancer, ouvrir
from app.contextes.tenants.domaine.tenant import ETAPES_ORDONNEES, Tenant
from app.contextes.transverse.adaptateurs.entrant.dependances import (
    oublier_le_garnissage,
    reinitialiser_atelier,
    repertoire_des_tenants,
    unite_de_travail,
)
from app.contextes.transverse.adaptateurs.sortant.empreinte_argon2 import (
    ServiceEmpreinteArgon2,
)
from app.contextes.transverse.domaine.habilitations import (
    Habilitation,
    MotifHabilitation,
)
from app.contextes.transverse.domaine.identites import Compte, EtatCompte
from app.contextes.transverse.domaine.mandats import Mandat, MotifMandat
from app.contextes.transverse.domaine.roles import Role
from app.infrastructure import base_de_donnees, config
from app.main import creer_application
from app.partage.horloge import horloge_figee
from app.partage.locataire import etabli
from tests.conftest import exige_postgresql

pytestmark = exige_postgresql

CABINET = "cabinet"
ENTREPRISE = "station-bonaberi"
MOT_DE_PASSE = "cabinet brcg douala 2026"
LE_JOUR = date(2026, 9, 9)
MIDI = datetime(2026, 9, 9, 12, 0)


def _tenant(slug: str) -> Tenant:
    tenant = ouvrir(f"tnt-{slug}", slug, NatureTenant.ENTREPRISE)
    for etape in ETAPES_ORDONNEES[1:]:
        tenant = avancer(tenant, etape)
    return activer(tenant, LE_JOUR)


def _compte_du_cabinet() -> Compte:
    return Compte(
        identifiant="C-900",
        courriel="mandate@cabinet.cm",
        nom="NKOLO",
        prenom="Paul",
        locataire=CABINET,
        empreinte_mot_de_passe=ServiceEmpreinteArgon2().deriver(MOT_DE_PASSE),
        etat=EtatCompte.ACTIF,
        cree_le=datetime(2026, 1, 1, 8, 0),
    )


@pytest.fixture
def deux_locataires(moteur_test, monkeypatch):
    """Deux locataires réels, un compte chez le cabinet, et rien chez l'entreprise."""
    monkeypatch.setenv("CGA_PERSISTANCE", "postgresql")
    monkeypatch.setenv("CGA_URL_BASE_DE_DONNEES", str(moteur_test.url).replace("***", "cga"))
    config.configuration.cache_clear()
    base_de_donnees.moteur.cache_clear()
    reinitialiser_atelier()
    # ⚠️ Le répertoire des locataires et l'instant de son dernier chargement sont des
    # états **de processus**. Sans ces deux remises à zéro, ce fichier passe seul et
    # échoue dans la suite complète : il hérite du répertoire d'un autre cas, et la
    # fenêtre de fraîcheur du pas 122 empêche le rechargement de le corriger.
    repertoire_des_tenants.cache_clear()
    oublier_le_garnissage()

    with SessionSql(moteur_test) as session:
        depot = DepotTenantsSql(session)
        depot.enregistrer(_tenant(CABINET))
        depot.enregistrer(_tenant(ENTREPRISE))
        session.commit()

    with etabli(CABINET), unite_de_travail(CABINET) as chez_le_cabinet:
        chez_le_cabinet.comptes.enregistrer(_compte_du_cabinet())
        for rang, role in enumerate((Role.COMPTABLE, Role.ADMINISTRATEUR), start=1):
            chez_le_cabinet.habilitations.enregistrer(
                Habilitation(
                    identifiant=f"H-90{rang}",
                    compte="C-900",
                    role=role,
                    debut=date(2026, 1, 1),
                    motif=MotifHabilitation.RECRUTEMENT,
                    accordee_par="Direction du cabinet",
                    accordee_le=datetime(2026, 1, 1, 8, 0),
                )
            )

    yield TestClient(creer_application())

    config.configuration.cache_clear()
    base_de_donnees.moteur.cache_clear()
    reinitialiser_atelier()
    repertoire_des_tenants.cache_clear()
    oublier_le_garnissage()


def _accorder(roles=(Role.COMPTABLE,), comptes=None, fin=None, identifiant="MDT-001"):
    """Le mandat, écrit dans le périmètre de l'entreprise, qui est celle qui l'accorde."""
    with etabli(ENTREPRISE), unite_de_travail(ENTREPRISE) as chez_elle:
        chez_elle.mandats.enregistrer(
            Mandat(
                identifiant=identifiant,
                mandant=ENTREPRISE,
                mandataire=CABINET,
                roles=frozenset(roles),
                comptes=comptes,
                debut=date(2026, 1, 1),
                fin=fin,
                motif=MotifMandat.CONTRAT_DE_SUIVI,
                accorde_par="Direction de la station-service",
            )
        )


def _session_au_cabinet(client: TestClient):
    return client.post(
        "/transverse/session",
        json={"courriel": "mandate@cabinet.cm", "mot_de_passe": MOT_DE_PASSE},
        headers={"Host": f"{CABINET}.cga.cm"},
    )


def _chez_l_entreprise(client: TestClient):
    return client.get("/transverse/moi", headers={"Host": f"{ENTREPRISE}.cga.cm"})


class TestLeCasNominal:
    def test_sans_mandat_le_compte_d_ailleurs_est_refuse(self, deux_locataires):
        client = deux_locataires
        with horloge_figee(MIDI):
            assert _session_au_cabinet(client).status_code == 200
            assert _chez_l_entreprise(client).status_code == 401

    def test_avec_un_mandat_il_agit(self, deux_locataires):
        client = deux_locataires
        _accorder()
        with horloge_figee(MIDI):
            assert _session_au_cabinet(client).status_code == 200
            reponse = _chez_l_entreprise(client)
        assert reponse.status_code == 200, reponse.text
        # ⚠️ Le compte tient deux rôles ; le mandat n'en couvre qu'un. Un mandat
        # n'ajoute aucun rôle, et il n'en laisse passer aucun qu'il ne nomme.
        assert reponse.json()["roles"] == ["COMPTABLE"]

    def test_le_refus_ne_dit_pas_pourquoi(self, deux_locataires):
        """Nommer le motif apprendrait que ce locataire existe, et qu'il a des mandats."""
        client = deux_locataires
        with horloge_figee(MIDI):
            _session_au_cabinet(client)
            reponse = _chez_l_entreprise(client)
        assert "mandat" not in reponse.text.lower()


class TestCeQueLeMandatNAutorisePas:
    def test_un_mandat_qui_ne_couvre_pas_le_role_n_autorise_rien(self, deux_locataires):
        """⚠️ Le compte est comptable ; le mandat ne couvre que la révision. Il ne devient
        pas réviseur en franchissant la frontière, et il n'exerce donc rien."""
        client = deux_locataires
        _accorder(roles=(Role.REVISEUR,))
        with horloge_figee(MIDI):
            _session_au_cabinet(client)
            assert _chez_l_entreprise(client).status_code == 401

    def test_un_compte_non_designe_est_refuse(self, deux_locataires):
        client = deux_locataires
        _accorder(comptes=frozenset({"C-999"}))
        with horloge_figee(MIDI):
            _session_au_cabinet(client)
            assert _chez_l_entreprise(client).status_code == 401

    def test_un_compte_designe_passe(self, deux_locataires):
        client = deux_locataires
        _accorder(comptes=frozenset({"C-900"}))
        with horloge_figee(MIDI):
            _session_au_cabinet(client)
            assert _chez_l_entreprise(client).status_code == 200

    def test_un_mandat_expire_ne_vaut_plus(self, deux_locataires):
        client = deux_locataires
        _accorder(fin=date(2026, 6, 30))
        with horloge_figee(MIDI):
            _session_au_cabinet(client)
            assert _chez_l_entreprise(client).status_code == 401

    def test_un_mandat_revoque_tombe_a_la_requete_suivante(self, deux_locataires):
        """⚠️ La propriété qui fait tenir la promesse faite à l'adhérent : « vous pouvez le
        retirer ». Pas au prochain redémarrage, pas à la prochaine session : tout de suite."""
        client = deux_locataires
        _accorder()
        with horloge_figee(MIDI):
            _session_au_cabinet(client)
            assert _chez_l_entreprise(client).status_code == 200

        with etabli(ENTREPRISE), unite_de_travail(ENTREPRISE) as chez_elle:
            mandat = chez_elle.mandats.lire("MDT-001")
            chez_elle.mandats.enregistrer(
                mandat.model_copy(update={"revoque_le": LE_JOUR, "revoque_par": "La direction"})
            )

        with horloge_figee(MIDI + timedelta(minutes=1)):
            assert _chez_l_entreprise(client).status_code == 401


class TestLAudit:
    def test_l_action_exercee_sous_mandat_le_dit(self, deux_locataires):
        """⚠️ « Le comptable X du centre Y, agissant pour le locataire Z ». C'est la
        première question d'un litige, et elle ne se reconstitue pas après coup."""
        client = deux_locataires
        _accorder(roles=(Role.ADMINISTRATEUR,))
        with horloge_figee(MIDI):
            _session_au_cabinet(client)
            # Un geste qui écrit dans le journal **du mandant** : accorder un mandat à son
            # tour, ce que l'administration du locataire servi permet.
            pose = client.post(
                "/transverse/mandats",
                json={
                    "mandataire": "un-autre-cabinet",
                    "roles": ["COMPTABLE"],
                    "debut": "2026-02-01",
                    "motif": "ASSISTANCE",
                },
                headers={"Host": f"{ENTREPRISE}.cga.cm"},
            )
            assert pose.status_code == 201, pose.text

        with etabli(ENTREPRISE), unite_de_travail(ENTREPRISE) as chez_elle:
            entrees = chez_elle.journal.lister(objet_type="mandat")
        assert entrees, "le geste doit être journalisé chez le mandant"
        assert all(e.mandat == "MDT-001" for e in entrees)
        assert all(e.locataire == ENTREPRISE for e in entrees)
        assert all(e.acteur == "C-900" for e in entrees)

    def test_la_deconnexion_ferme_bien_la_session_du_mandataire(self, deux_locataires):
        """⚠️ Le défaut trouvé en écrivant ces cas : la session vit chez le mandataire.

        Fermée dans le périmètre du mandant, où elle n'existe pas, le geste rendait 204
        sans rien faire, et l'utilisateur restait connecté en croyant le contraire.
        """
        client = deux_locataires
        _accorder()
        with horloge_figee(MIDI):
            _session_au_cabinet(client)
            assert (
                client.delete(
                    "/transverse/session", headers={"Host": f"{ENTREPRISE}.cga.cm"}
                ).status_code
                == 204
            )
            assert _chez_l_entreprise(client).status_code == 401
            chez_lui = client.get("/transverse/moi", headers={"Host": f"{CABINET}.cga.cm"})
        assert chez_lui.status_code == 401, "la session est fermée là où elle vivait"

    def test_une_action_chez_soi_ne_porte_aucun_mandat(self, deux_locataires):
        client = deux_locataires
        with horloge_figee(MIDI):
            _session_au_cabinet(client)
        with etabli(CABINET), unite_de_travail(CABINET) as chez_lui:
            entrees = chez_lui.journal.lister(objet_type="session")
        assert entrees
        assert all(e.mandat is None for e in entrees)

    def test_la_chaine_reste_verifiable(self, deux_locataires):
        """⚠️ Un champ ajouté au corps canonique d'un journal chaîné casse toutes les
        empreintes déjà écrites. Celui-ci n'y figure que lorsqu'il vaut quelque chose."""
        from app.contextes.transverse.domaine.audit import verifier_chaine

        client = deux_locataires
        _accorder()
        with horloge_figee(MIDI):
            _session_au_cabinet(client)
            client.delete("/transverse/session", headers={"Host": f"{ENTREPRISE}.cga.cm"})
        for locataire in (CABINET, ENTREPRISE):
            with etabli(locataire), unite_de_travail(locataire) as boutique:
                verifier_chaine(boutique.journal.lister())
