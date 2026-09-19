from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session as SessionSql

from app.contextes.conformite.api import DepotReglesYaml, MoteurConformite
from app.contextes.conformite.domaine.entites import Regle
from app.contextes.referentiel.api import DepotParametresYaml, ServiceParametres
from app.contextes.referentiel.contrats import StatutValidation
from app.infrastructure.config import RACINE_DEPOT

REFERENTIEL = RACINE_DEPOT / "Docs" / "referentiel"


@pytest.fixture(scope="session")
def dossier_referentiel() -> Path:
    assert REFERENTIEL.is_dir(), f"référentiel introuvable : {REFERENTIEL}"
    return REFERENTIEL


@pytest.fixture(scope="session")
def parametres(dossier_referentiel: Path) -> ServiceParametres:
    return ServiceParametres.depuis_depot(
        DepotParametresYaml(dossier_referentiel / "parametres.yaml")
    )


@pytest.fixture(scope="session")
def parametres_non_arretes(dossier_referentiel: Path) -> ServiceParametres:
    """Le référentiel réel, dont toute validation a été retirée.

    ─────────────────────────────────────────────────────────────────────────────
    POURQUOI CETTE FIXTURE EXISTE

    Plusieurs tests vérifient que le produit **signale** une valeur non validée :
    le bulletin de paie le dit, le classement de liasse le dit, le score de risque
    le dit. Ils se contentaient jusqu'ici du référentiel réel, parce qu'aucune de
    ses valeurs n'était validée.

    Le 18 août 2026, cinquante l'ont été. Neuf tests sont alors tombés d'un coup,
    non parce que la machinerie de signalement s'était cassée, mais parce qu'ils
    mesuraient l'état d'un fichier au lieu de mesurer un comportement. Un test qui
    ne peut passer que tant que le référentiel reste incomplet interdit de le
    compléter, ce qui est exactement l'inverse du service attendu.

    Cette fixture rend cette dépendance impossible : mêmes codes, mêmes valeurs,
    mêmes dates, aucun statut VALIDE. La machinerie de signalement se teste
    dessus, et le référentiel réel peut se remplir sans casser quoi que ce soit.
    ─────────────────────────────────────────────────────────────────────────────
    """
    reels = DepotParametresYaml(dossier_referentiel / "parametres.yaml").charger()
    return ServiceParametres(
        [
            parametre.model_copy(
                update={
                    "versions": [
                        version.model_copy(
                            update={
                                "statut": StatutValidation.A_VALIDER,
                                "valide_par": None,
                                "valide_le": None,
                            }
                        )
                        for version in parametre.versions
                    ]
                }
            )
            for parametre in reels
        ]
    )


@pytest.fixture(scope="session")
def regles(dossier_referentiel: Path) -> list[Regle]:
    return DepotReglesYaml(dossier_referentiel / "regles").charger()


@pytest.fixture(scope="session")
def moteur(regles: list[Regle], parametres: ServiceParametres) -> MoteurConformite:
    return MoteurConformite(regles, parametres)


# ── PostgreSQL ───────────────────────────────────────────────────────────────
#
# ⚠️ CE SONDAGE VIT ICI POUR N'EXISTER QU'UNE FOIS.
#
# Il décide si les tests de persistance s'exécutent ou se **sautent**. Un saut
# est silencieux et vert : deux copies qui divergent — une variable
# d'environnement renommée dans l'une, pas dans l'autre — donneraient une suite
# annonçant « tout passe » sans avoir vérifié un seul cloisonnement, une seule
# contrainte d'unicité, une seule migration.
#
# C'est le pire genre de faux vert, parce qu'il ne ressemble pas à une panne.
# La chaîne d'intégration compte d'ailleurs les tests de persistance passés,
# précisément pour attraper le cas où ce sondage échouerait à tort.

URL_BASE_TEST = os.environ.get(
    "CGA_URL_BASE_DE_DONNEES_TEST",
    "postgresql+psycopg://cga:cga@localhost:5432/cga_test",
)


def _sonder_la_base() -> str | None:
    """Le motif d'indisponibilité, ou `None` si la base répond."""
    try:
        moteur = create_engine(URL_BASE_TEST, pool_pre_ping=True)
        with moteur.connect() as connexion:
            connexion.execute(text("select 1"))
        moteur.dispose()
        return None
    except Exception as echec:  # noqa: BLE001 — on veut le motif, quel qu'il soit
        return str(echec).splitlines()[0]


MOTIF_INDISPONIBLE = _sonder_la_base()

@pytest.fixture(autouse=True)
def limitation_neuve():
    """Le compteur de débit repart à zéro avant chaque cas.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **POURQUOI CE DÉCOR EST AUTOMATIQUE, ET POURQUOI IL A FALLU L'ÉCRIRE**

    La limitation de débit autorise trente connexions par cinq minutes et par
    adresse. C'est large pour un cabinet, et étroit pour une suite de tests :
    quinze fichiers ouvrent une session, certains une par cas.

    Le compteur survivait d'un cas au suivant. Au-delà du seuil, des cas
    tombaient au **montage de leur décor** — loin de ce qu'ils vérifient, avec un
    message qui ne parle pas d'eux — et seulement quand on les lançait tous
    ensemble. C'est la pire forme d'échec : il ne se reproduit pas isolément,
    donc on le croit imaginaire.

    ⚠️ Le garde-fou n'est pas désactivé : il est **remis à zéro**. `test_limitation`
    continue de l'éprouver pour lui-même, et c'est le seul endroit où il doit
    l'être.
    ─────────────────────────────────────────────────────────────────────────────
    """
    from app.contextes.transverse.adaptateurs.entrant.limitation import (
        vider_le_limiteur,
    )

    vider_le_limiteur()
    yield
    vider_le_limiteur()


#: À poser en `pytestmark` dans tout fichier qui exige une vraie base.
exige_postgresql = pytest.mark.skipif(
    MOTIF_INDISPONIBLE is not None,
    reason=(
        f"PostgreSQL injoignable sur {URL_BASE_TEST} — {MOTIF_INDISPONIBLE}. "
        "Démarrer l'instance locale : eval \"$(outils/postgres-local.sh start)\""
    ),
)


@pytest.fixture
def moteur_test():
    """Un schéma neuf par test. Les tables sont créées puis détruites.

    ⚠️ `create_all` et non Alembic : les tests vérifient le **code**, les
    migrations sont vérifiées à part par la chaîne d'intégration. Les confondre
    ferait dépendre chaque test de l'historique complet des migrations.
    """
    from app.tables import METADONNEES

    moteur = create_engine(URL_BASE_TEST, pool_pre_ping=True)
    METADONNEES.drop_all(moteur)
    METADONNEES.create_all(moteur)
    yield moteur
    METADONNEES.drop_all(moteur)
    moteur.dispose()


@pytest.fixture
def session_sql(moteur_test):
    """Une session **non cloisonnée**, pour voir ce que chaque dépôt écrit.

    Le filtre ambiant de `session_du_locataire` se vérifie à part : ici, on veut
    pouvoir écrire pour deux cabinets et constater qu'aucun ne voit l'autre.
    """
    with SessionSql(moteur_test) as session:
        yield session
        # Explicite, bien que la fermeture le fasse : un test qui laisse une
        # écriture non validée la verrait ressurgir dans le suivant si la
        # fixture changeait un jour de portée.
        session.rollback()


@pytest.fixture
def plateforme_amorcee(monkeypatch, moteur_test):
    """L'application entière, en persistance PostgreSQL, sur une base amorcée.

    ─────────────────────────────────────────────────────────────────────────────
    POURQUOI ELLE VIT ICI PLUTÔT QUE DANS UN FICHIER DE CAS

    Elle a été écrite deux fois, dans les deux recettes, et une troisième était
    sur le point de naître pour la reprise comptable. **La troisième copie est
    celle qui dit qu'il faut la partager** : trois exemplaires d'un amorçage
    divergent au premier correctif, et les cas qui en dépendent commencent alors
    à mesurer des mondes différents sans que rien ne le dise.

    ⚠️ `moteur_test` est demandé pour son effet de bord : il crée le schéma. Sans
    lui, les cas tourneraient sur une base sans tables.

    ⚠️ **La base PostgreSQL naît vide**, contrairement au mode mémoire qui naît
    garni. Le jeu de démonstration doit y être versé, et `amorcer` existe pour
    cela : les cas emploient le même geste qu'une installation neuve, ce qui
    l'éprouve au passage.
    ─────────────────────────────────────────────────────────────────────────────
    """
    from fastapi.testclient import TestClient

    from app.contextes.transverse.adaptateurs.entrant.dependances import (
        reinitialiser_atelier,
    )
    from app.infrastructure import base_de_donnees, config
    from app.main import creer_application

    monkeypatch.setenv("CGA_PERSISTANCE", "postgresql")
    monkeypatch.setenv(
        "CGA_URL_BASE_DE_DONNEES", str(moteur_test.url).replace("***", "cga")
    )
    config.configuration.cache_clear()
    base_de_donnees.moteur.cache_clear()
    reinitialiser_atelier()

    from app.amorcage import amorcer
    from app.partage.locataire import etabli

    with etabli("CGA-BRCG"):
        amorcer("CGA-BRCG", forcer=True)

    application = creer_application()
    with TestClient(application) as client:
        # ⚠️ L'application est posée sur le client pour les cas qui ont besoin de
        # la relancer ou de la fouiller. Un attribut plutôt qu'une seconde
        # fixture : deux fixtures créeraient deux applications sur une même base.
        client.app_pour_admin = application
        yield client

    config.configuration.cache_clear()
    base_de_donnees.moteur.cache_clear()


@pytest.fixture
def plateforme(plateforme_amorcee):
    """Le nom court, pour les fichiers qui n'ont rien à ajouter à l'amorçage.

    ⚠️ Deux noms pour une seule plateforme, et la raison est mécanique : un
    fichier de cas qui veut enrichir la fixture ne peut pas le faire sous le même
    nom, il se demanderait lui-même. La recette du parcours redéfinit donc
    `plateforme` en s'appuyant sur `plateforme_amorcee`, et les autres fichiers
    prennent ce nom-ci sans rien savoir de la manœuvre.
    """
    return plateforme_amorcee


def ouvrir_une_session(client, courriel: str) -> None:
    """Ouvre une session applicative pour ce compte, et vérifie qu'elle s'ouvre.

    ⚠️ L'assertion fait partie de l'aide. Sans elle, un mot de passe changé ferait
    échouer le cas **trois appels plus loin**, sur un 403 que le lecteur
    attribuerait à une permission manquante.
    """
    from app.contextes.transverse.api import MOT_DE_PASSE_DEMO

    reponse = client.post(
        "/transverse/session",
        json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO},
    )
    assert reponse.status_code == 200, reponse.text


def enroler_par_le_courriel(client) -> str:
    """Enrôle le second facteur du compte connecté comme le ferait son titulaire, et rend le secret.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ PAR LE LIEN REÇU, ET NON PAR UN RACCOURCI (pas 62)

    Un premier enrôlement ne rend plus le secret : il envoie un lien à l'adresse du
    compte. Cette aide demande l'enrôlement, relit le dernier courriel de confirmation
    retenu, et présente son jeton. Un raccourci qui fabriquerait l'enrôlement en base
    ferait passer les tests d'une règle qu'aucun utilisateur ne contourne.
    ─────────────────────────────────────────────────────────────────────────────
    """
    from app.contextes.transverse.adaptateurs.entrant.dependances import service_de_notification

    demande = client.post("/transverse/second-facteur")
    assert demande.status_code == 200, demande.text
    assert demande.json()["confirmation_par_courriel"] is True, demande.text
    (dernier, *_) = service_de_notification().derniers("compte.second_facteur_confirmation")
    jeton = dernier.contexte["lien"].split("jeton=", 1)[1]
    confirmation = client.post("/transverse/second-facteur/confirmation", json={"jeton": jeton})
    assert confirmation.status_code == 200, confirmation.text
    return confirmation.json()["secret"]


@pytest.fixture
def depot_tva_sans_revue_exigee(monkeypatch):
    """Le dépôt de TVA sans l'exigence d'un mois transmis au réviseur (pas 109).

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ À N'EMPLOYER QUE PAR UN CAS QUI ÉPROUVE AUTRE CHOSE QUE CETTE EXIGENCE

    Depuis le pas 109, le référentiel exige que le mois ait été transmis au réviseur avant
    le dépôt, et c'est bloquant. Les cas écrits avant éprouvent le bordereau, l'accusé,
    l'échéancier, la persistance : leur faire d'abord clore et transmettre juillet
    mêlerait deux sujets. Ils règlent donc le référentiel comme un cabinet qui n'exigerait
    rien, **et le disent** en demandant cette fixture. L'exigence elle-même est gardée par
    `test_declaration_tva_preparee.py`.
    ─────────────────────────────────────────────────────────────────────────────
    """
    from app.contextes.obligations.adaptateurs.entrant import routes_http
    from app.contextes.obligations.application.depot import ExigenceDeRevue, ReglagesDuDepotTVA

    sans = ReglagesDuDepotTVA(revue_du_mois_exigee=ExigenceDeRevue.AUCUNE)
    monkeypatch.setattr(routes_http, "charger_les_reglages_du_depot_tva", lambda _chemin: sans)
