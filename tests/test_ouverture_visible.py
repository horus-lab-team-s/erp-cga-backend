"""Ce que l'ouverture a réellement fait, rendu visible.

─────────────────────────────────────────────────────────────────────────────────
⚠️ POURQUOI CES CAS EXISTENT

Le produit SAVAIT que l'espace d'un client pouvait être ouvert sans compte
administrateur — la saga inscrit l'étape comme « substituée », et le domaine
l'écrit noir sur blanc :

    « Le tenant existe, son sous-domaine répond, et il n'a ni schéma, ni
    stockage, ni compte administrateur. Annoncer au client un espace ouvert
    dans ce cas serait un mensonge que sa première connexion découvrirait. »

Cette information était calculée, stockée, et exposée NULLE PART. La console
annonçait « l'espace s'ouvre » dans tous les cas. Ces cas gardent la route qui
la rend enfin lisible.

⚠️ LA DISTINCTION QUI COMMANDE TOUT : `terminee` N'EST PAS `utilisable`. Une
saga terminée avec trois étapes substituées est terminée, et l'espace n'est pas
utilisable. Les confondre reproduirait exactement le défaut qu'on corrige.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.contextes.souscription.adaptateurs.entrant.routes_acquisition import (
    reinitialiser_dossiers,
)
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO
from app.main import app

CHEMIN = "/acquisition/dossiers/{reference}/ouverture"

#: `b.mballa` porte DIRECTION, donc `LIRE_PROSPECT`. Un compte réel du jeu de
#: démonstration plutôt qu'une habilitation forgée : c'est ce qui vérifie que
#: quelqu'un, dans le cabinet, a réellement le droit de lire cet état.
LECTEUR = "b.mballa@cga-brcg.cm"
#: L'adhérent n'a rien à savoir de l'ouverture d'un espace commercial.
ADHERENT = "jp.nkoa@batimentplus.cm"
#: Le locataire du jeu de démonstration, celui qu'amorce la fixture.
LOCATAIRE_DEMO = "CGA-BRCG"


@pytest.fixture(autouse=True)
def parcours_neuf():
    reinitialiser_dossiers()
    yield
    reinitialiser_dossiers()


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client


def _connecter(client: TestClient, courriel: str) -> TestClient:
    reponse = client.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert reponse.status_code == 200, reponse.text
    return client


def test_une_saga_jamais_demarree_n_est_pas_une_erreur(client):
    """⚠️ Le relais publie toutes les cinq secondes : entre la confirmation de
    l'encaissement et le premier tour, la saga n'existe pas. Rendre 404 ferait
    afficher une erreur au collaborateur qui vient de réussir son geste."""
    lecteur = _connecter(client, LECTEUR)

    reponse = lecteur.get(CHEMIN.format(reference="dos-inconnu"))

    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["demarree"] is False
    assert corps["terminee"] is False
    assert corps["utilisable"] is False
    assert corps["etapes_substituees"] == []
    assert corps["slug"] is None


def test_la_route_exige_de_lire_les_prospects(client):
    """Un adhérent n'a rien à savoir de l'ouverture d'un espace commercial."""
    adherent = _connecter(client, ADHERENT)

    reponse = adherent.get(CHEMIN.format(reference="dos-quelconque"))

    assert reponse.status_code == 403, reponse.text


def test_une_session_absente_est_refusee(client):
    reponse = client.get(CHEMIN.format(reference="dos-quelconque"))
    assert reponse.status_code in (401, 403), reponse.text


# ── La distinction qui commande tout, éprouvée sur une base réelle ───────────


@pytest.mark.parametrize(
    ("substituees", "utilisable"),
    [
        pytest.param((), True, id="aucune etape substituee : espace utilisable"),
        pytest.param(
            ("ADMINISTRATEUR_CREE",),
            False,
            id="le compte manque : espace NON utilisable",
        ),
        pytest.param(
            ("SCHEMA_CREE", "STOCKAGE_OUVERT", "ADMINISTRATEUR_CREE"),
            False,
            id="trois etapes substituees, comme avant le branchement des ports",
        ),
        pytest.param(
            # ⚠️ Pas 95 : `SCHEMA_CREE` est substituée sur TOUTES les
            # installations depuis que la décision D3 a été révisée — il n'y a
            # aucun schéma à créer. La compter comme un manque rendrait
            # `utilisable` faux pour l'éternité, y compris pour un client qui a
            # son compte, son lien et son stockage.
            ("SCHEMA_CREE",),
            True,
            id="seule l'etape sans objet est substituee : espace utilisable",
        ),
    ],
)
def test_terminee_n_est_pas_utilisable(plateforme, substituees, utilisable):
    """⚠️ LE CŒUR DE LA CORRECTION.

    Une saga TERMINÉE avec des étapes substituées reste terminée. Ce qui décide
    de ce qu'on annonce au client, c'est `utilisable` — et lui seul. Les
    confondre reproduirait exactement le défaut qu'on corrige : la console
    annonçait « l'espace s'ouvre » sur un tenant sans compte administrateur.

    ⚠️ Le troisième cas est celui de la pile de démonstration, relevé en base le
    25 septembre : `SCHEMA_CREE`, `STOCKAGE_OUVERT`, `ADMINISTRATEUR_CREE`
    substituées, saga terminée, statut ACTIF.
    """
    reference = "dos-essai-ouverture"
    _poser_une_execution(reference=reference, slug="essai-ouverture", substituees=substituees)

    with TestClient(app) as client:
        _connecter(client, LECTEUR)
        reponse = client.get(CHEMIN.format(reference=reference))

    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["demarree"] is True
    assert corps["terminee"] is True
    assert corps["utilisable"] is utilisable
    # ⚠️ La route ne rend que les manques RÉELS : citer une étape sans objet
    # ferait croire à un défaut au chargé de clientèle.
    attendus = [e for e in substituees if e != "SCHEMA_CREE"]
    assert corps["etapes_substituees"] == attendus
    assert corps["slug"] == "essai-ouverture"


def _poser_une_execution(*, reference: str, slug: str, substituees: tuple[str, ...]) -> None:
    """Écrit une exécution de saga terminée, telle que le relais la laisse."""
    from app.contextes.tenants.api import NOM_SAGA
    from app.contextes.transverse.adaptateurs.entrant.dependances import (
        session_de_travail,
        unite_de_travail,
    )
    from app.infrastructure.depots_orchestration import DepotExecutionsSql
    from app.orchestration.saga import EtatSaga, Execution
    from app.partage.locataire import etabli

    # ⚠️ LES DEUX SONT NÉCESSAIRES, et ce n'est pas une redondance :
    # `unite_de_travail` ouvre une session cloisonnée côté BASE, `etabli` pose
    # le locataire pour l'EXÉCUTION. Sans le second, le dépôt lève
    # « LocataireNonEtabli », et le cas échoue sur autre chose que ce qu'il
    # mesure.
    with etabli(LOCATAIRE_DEMO), unite_de_travail(LOCATAIRE_DEMO):
        session = session_de_travail()
        assert session is not None
        DepotExecutionsSql(session, LOCATAIRE_DEMO).enregistrer(
            Execution(
                saga=NOM_SAGA,
                cle=reference,
                etat=EtatSaga.TERMINEE,
                contexte={"slug": slug, "etapes_substituees": list(substituees)},
            )
        )

