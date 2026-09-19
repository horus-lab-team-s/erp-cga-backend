"""Le socle d'orchestration, vu de l'extérieur.

Trois routes, aucune publique. Déclencher une publication remet des événements à
des consommateurs qui ouvrent des tenants et envoient des messages : c'est une
opération d'exploitation, pas un geste de parcours client.

Ces tests vérifient ce que les tests de mécanisme ne peuvent pas dire : que les
routes sont montées, protégées, et qu'elles ne laissent filtrer aucun contenu
d'événement.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.contextes.transverse.adaptateurs.entrant.routes_orchestration import (
    Atelier,
    reinitialiser_orchestration,
)
from app.main import app
from app.orchestration.boite_d_envoi import deposer
from app.partage.horloge import maintenant


@pytest.fixture(autouse=True)
def socle_neuf():
    reinitialiser_orchestration()
    yield
    reinitialiser_orchestration()


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client


class TestRoutesMontees:
    def test_les_routes_du_socle_existent(self, client):
        """Le socle est joignable. Sans cela, la boîte se remplirait et ne se
        viderait jamais, et personne ne le verrait.

        ⚠️ La liste est **close**, et c'est ce qui a de la valeur : ajouter une
        route à ce préfixe fait échouer ce cas, et oblige à décider si elle doit
        être protégée comme les autres. Une assertion « au moins ces trois-là »
        laisserait passer une route d'orchestration ouverte à tous.
        """
        chemins = client.get("/openapi.json").json()["paths"]
        assert sorted(c for c in chemins if c.startswith("/orchestration")) == [
            "/orchestration/en-attente",
            "/orchestration/ordonnancement",
            "/orchestration/ordonnancement/{travail}/reprise",
            "/orchestration/publication",
            "/orchestration/quarantaine",
            # Pas 84 : la remise en circulation, protégée comme les autres (voir
            # `test_exploitation_gestes.py`).
            "/orchestration/quarantaine/{identifiant}/remise",
        ]


class TestProtection:
    @pytest.mark.parametrize(
        ("methode", "chemin"),
        [
            ("post", "/orchestration/publication"),
            ("post", "/orchestration/ordonnancement"),
            ("get", "/orchestration/ordonnancement"),
            ("get", "/orchestration/en-attente"),
            ("get", "/orchestration/quarantaine"),
            ("post", "/orchestration/ordonnancement/relais/reprise"),
            ("post", "/orchestration/quarantaine/EV-1/remise"),
        ],
    )
    def test_aucune_n_est_publique(self, client, methode, chemin):
        """Déclencher une publication ouvre des tenants et envoie des messages.
        Une route publique qui fait cela est une arme."""
        reponse = getattr(client, methode)(chemin)
        assert reponse.status_code in (401, 403)


class TestPassage:
    """Le mécanisme est éprouvé ailleurs. Ici, on vérifie l'assemblage."""

    def test_un_passage_sur_une_boite_vide_ne_fait_rien(self):
        """Rejouable sans dommage : c'est ce qui permet à l'ordonnanceur de
        l'appeler toutes les cinq secondes sans réfléchir."""
        from app.orchestration.relais import publier_un_lot

        atelier = Atelier()
        rapport = publier_un_lot(atelier.boite, atelier.abonnements, maintenant())
        assert rapport.traites == 0
        assert rapport.demande_un_regard is False

    def test_l_ouverture_est_bien_abonnee(self):
        """L'assemblage tient : un `PaiementEncaissé` déposé dans la boîte de
        l'atelier ouvre réellement un tenant."""
        from app.contextes.tenants.domaine.tenant import StatutTenant
        from app.orchestration.relais import publier_un_lot

        atelier = Atelier()
        assert "PaiementEncaissé" in atelier.abonnements.noms()

        deposer(
            atelier.boite, "ev-1", "PaiementEncaissé", "dos-1",
            {"tenant": "tnt-station", "slug": "station-bonaberi"}, maintenant(),
        )
        rapport = publier_un_lot(atelier.boite, atelier.abonnements, maintenant())

        assert rapport.publies == 1
        assert rapport.echecs == 0
        # L'atelier reconstruit ses dépôts, mais les mémoïsés sont partagés.
        from app.contextes.transverse.adaptateurs.entrant.routes_orchestration import (
            _registre_memoire,
        )

        tenant = _registre_memoire().par_slug("station-bonaberi")
        assert tenant is not None
        assert tenant.statut is StatutTenant.ACTIF


class TestAucunContenuNeFuit:
    def test_la_liste_en_attente_ne_porte_pas_la_charge(self):
        """⚠️ Un journal circule, se copie, part chez un prestataire d'analyse,
        et n'a pas le régime de protection d'une base métier. On rend des
        identifiants et des décisions, jamais des contenus."""
        atelier = Atelier()
        deposer(
            atelier.boite, "ev-1", "PaiementEncaissé", "dos-1",
            {"telephone": "+237699112233", "montant": 450000, "slug": "station"},
            maintenant(),
        )

        from app.contextes.transverse.adaptateurs.entrant.routes_orchestration import (
            Atelier as _Atelier,
        )

        rendu = str(
            [
                {
                    "identifiant": e.identifiant,
                    "nom": e.nom,
                    "cle": e.cle,
                    "cree_le": e.cree_le,
                    "tentatives": e.tentatives,
                    "dernier_echec": e.dernier_echec,
                }
                for e in _Atelier().boite.a_publier(50)
            ]
        )
        assert "699112233" not in rendu
        assert "450000" not in rendu
        assert "ev-1" in rendu
        assert "PaiementEncaissé" in rendu
