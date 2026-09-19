"""Le mode de recette, et surtout : ce qui l'empêche d'atteindre la production.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE MODE FAIT, ET POURQUOI C'EST DANGEREUX

Il fabrique des **encaissements sans argent** et rend lisibles les **liens
d'activation en clair**. Chacun des deux, seul, suffirait à ouvrir n'importe
quel compte du cabinet à qui saurait où regarder.

L'en-tête de `fournisseur_tara.py` posait la règle bien avant que ce mode
n'existe : « un mode simulé qui validerait automatiquement finirait un jour en
production ». Ce fichier existe pour que cette phrase reste fausse.

TROIS VERROUS, ET LES TESTS PORTENT SUR EUX AVANT TOUT

**1 · Le drapeau se déclare.** Il ne se déduit pas d'une clé Tara manquante.
L'absence de clé est un accident de configuration ; elle ne vaut pas
consentement à fabriquer des encaissements.

**2 · La production refuse de démarrer.** Pas un avertissement, pas une
dégradation : `Configuration` lève. Le seul moment où cette faute coûte peu est
le démarrage, pendant qu'un ingénieur regarde.

**3 · Hors du mode, la route n'existe pas.** `404`, et non `403` : un point
d'entrée absent se distingue mal d'un point d'entrée qui refuse.

CE QUE LES TESTS DE PARCOURS DÉFENDENT

Que la chaîne complète tienne : devis → paiement validé d'office → courriel
retenu → lien lisible → mot de passe défini → connexion. C'est ce parcours qui
n'avait **jamais** été exécuté de bout en bout, et c'est ainsi qu'une page
d'activation manquante a survécu : les deux moitiés fonctionnaient, leurs tests
passaient, personne n'avait fait le chemin entier.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contextes.transverse.adaptateurs.entrant.dependances import (
    reinitialiser_atelier,
)
from app.contextes.transverse.api import Role, roles_au
from app.infrastructure.config import Configuration, configuration
from app.main import creer_application
from app.partage.horloge import horloge_figee

INSTANT = datetime(2026, 8, 15, 10, 0)
NIU = "M081234567890P"

PROSPECT = {
    "nom": "KAMGA",
    "prenom": "Sylvie",
    "courriel": "sylvie.kamga@exemple.cm",
    "telephone": "699887766",
    "denomination": "SARL LES DEUX PALMIERS",
    "niu": NIU,
    "chiffre_affaires_declare": "18000000",
}


def _client(monkeypatch, *, demonstration: bool) -> Iterator[TestClient]:
    monkeypatch.setenv("CGA_MODE_DEMONSTRATION", "true" if demonstration else "false")
    configuration.cache_clear()
    reinitialiser_atelier()
    from app.contextes.souscription.adaptateurs.entrant.routes_http import (
        reinitialiser_comptoir,
    )

    reinitialiser_comptoir()
    with horloge_figee(INSTANT):
        yield TestClient(creer_application())
    configuration.cache_clear()


@pytest.fixture
def client(monkeypatch) -> Iterator[TestClient]:
    yield from _client(monkeypatch, demonstration=True)


@pytest.fixture
def client_ordinaire(monkeypatch) -> Iterator[TestClient]:
    yield from _client(monkeypatch, demonstration=False)


# ── Les verrous ─────────────────────────────────────────────────────────────


class TestVerrous:
    def _production(self, **surcharges: object) -> dict[str, object]:
        return {
            "environnement": "production",
            "persistance": "postgresql",
            "smtp_hote": "smtp.exemple.cm",
            "adresse_publique_site": "https://www.cga-brcg.cm",
            "origines_cors": ["https://www.cga-brcg.cm"],
            "cle_chiffrement": base64.b64encode(bytes(32)).decode("ascii"),
        } | surcharges

    def test_la_production_refuse_de_demarrer_en_mode_demonstration(self):
        """Le verrou qui compte plus que tous les autres.

        Pas un avertissement, pas une dégradation : le processus ne démarre pas.
        """
        with pytest.raises(ValueError, match="Configuration de production refusée"):
            Configuration(**self._production(mode_demonstration=True))

    def test_le_refus_dit_pourquoi(self):
        """Celui qui déploie à 3 h du matin n'ira pas lire le code."""
        try:
            Configuration(**self._production(mode_demonstration=True))
        except ValueError as refus:
            assert "fabrique des encaissements sans argent" in str(refus)
            assert "à aucune condition" in str(refus)

    def test_la_production_demarre_sans_ce_mode(self):
        assert Configuration(**self._production()).mode_demonstration is False

    def test_le_mode_est_faux_par_defaut(self):
        """Il se déclare. Il ne se déduit d'aucune autre absence de réglage."""
        assert Configuration().mode_demonstration is False

    def test_une_persistance_inconnue_est_refusee_au_chargement(self):
        """Le verrou qui manquait, et le défaut qu'il aurait empêché.

        ─────────────────────────────────────────────────────────────────────────
        `persistance` était un `str` libre. Le garnissage du répertoire des tenants
        le comparait à `"sql"`, une valeur que **rien n'a jamais acceptée** : la
        production exige `"postgresql"`. Le répertoire n'était donc jamais garni en
        production, et tout sous-domaine rendait 404 — sur une comparaison
        syntaxiquement correcte, dans un test vert qui forçait lui-même `"sql"`.

        Fermer le type déplace la faute là où elle se règle : au chargement, avec le
        nom du réglage et la liste des valeurs admises, avant qu'aucune requête
        n'arrive.
        ─────────────────────────────────────────────────────────────────────────
        """
        with pytest.raises(ValidationError) as refus:
            Configuration(persistance="sql")
        assert "persistance" in str(refus.value)

    def test_la_question_de_la_persistance_se_pose_par_en_base(self):
        """Une comparaison écrite à la main s'est déjà trompée de valeur.

        Le test tient donc l'équivalence entre le réglage et la propriété : si une
        troisième valeur de persistance apparaît un jour, il faudra décider ici si
        elle survit au redémarrage, plutôt que de la découvrir en production.
        """
        assert Configuration(persistance="postgresql").en_base is True
        assert Configuration(persistance="memoire").en_base is False

    def test_la_boite_aux_lettres_n_existe_pas_hors_du_mode(self, client_ordinaire: TestClient):
        """`404` et non `403`, délibérément.

        Un `403` apprendrait qu'il existe quelque part une route qui rend des
        liens d'activation en clair.
        """
        reponse = client_ordinaire.get("/transverse/courriels")
        assert reponse.status_code == 404
        assert reponse.json()["detail"] == "Not Found"

    def test_le_paiement_ne_se_valide_pas_seul_hors_du_mode(self, client_ordinaire: TestClient):
        """Le comportement d'origine, préservé : sans clé Tara, on n'encaisse pas.

        C'est ce que garantissait `fournisseur_tara.py`, et le mode de
        démonstration ne doit pas l'avoir affaibli au passage.
        """
        reference = client_ordinaire.post(
            "/souscription/devis",
            json={"prospect": PROSPECT, "lignes": [{"service": "ADHESION"}]},
        ).json()["reference"]
        reponse = client_ordinaire.post(f"/souscription/devis/{reference}/engagement", json={})
        assert reponse.status_code == 201
        assert reponse.json()["paiement"]["statut"] != "VALIDE"
        assert "téléphone" in reponse.json()["message"]

    def test_la_sonde_annonce_le_mode(self, client: TestClient):
        """Un serveur qui fabrique des encaissements ne doit pas passer pour une
        production."""
        assert client.get("/sante").json()["mode_demonstration"] is True

    def test_la_sonde_ne_l_annonce_pas_quand_il_est_absent(self, client_ordinaire: TestClient):
        assert "mode_demonstration" not in client_ordinaire.get("/sante").json()


# ── Le parcours, de bout en bout ────────────────────────────────────────────


def _verifier_par_le_cabinet(reference_souscription: str) -> None:
    """Le cabinet vérifie l'identité et ouvre l'accès (pas 83).

    ⚠️ Même en démonstration, le paiement validé d'office n'ouvre plus rien : la faille
    corrigée au pas 83 (un inconnu payait avec le NIU d'une autre entreprise et lisait
    son dossier) ne se rouvre pas par le mode de démonstration. Un client HTTP à part,
    pour ne pas remplacer la session du visiteur.
    """
    cabinet = TestClient(creer_application())
    connexion = cabinet.post(
        "/transverse/session",
        json={"courriel": "s.onana@cga-brcg.cm", "mot_de_passe": "cabinet brcg douala 2026"},
    )
    assert connexion.status_code == 200, connexion.text
    reponse = cabinet.post(
        f"/souscription/souscriptions/{reference_souscription}/activation",
        json={"verification": "RCCM et CNI du gérant présentés au cabinet ce jour."},
    )
    assert reponse.status_code == 200, reponse.text


def _souscrire_et_verifier(client: TestClient) -> None:
    reference = client.post(
        "/souscription/devis",
        json={"prospect": PROSPECT, "lignes": [{"service": "ADHESION"}]},
    ).json()["reference"]
    engagement = client.post(f"/souscription/devis/{reference}/engagement", json={})
    _verifier_par_le_cabinet(engagement.json()["souscription"]["reference"])


class TestParcoursComplet:
    def test_du_devis_a_la_connexion(self, client: TestClient):
        """Le chemin entier, celui que personne n'avait jamais parcouru.

        C'est ainsi qu'une page d'activation manquante a survécu : les deux
        moitiés fonctionnaient, leurs tests passaient, et le lien du courriel
        menait à un 404 que rien n'exerçait.
        """
        # 1 · Le devis fige le prix du jour.
        devis = client.post(
            "/souscription/devis",
            json={"prospect": PROSPECT, "lignes": [{"service": "ADHESION"}]},
        )
        assert devis.status_code == 201
        reference = devis.json()["reference"]

        # 2 · L'engagement encaisse — d'office, et il le dit.
        engagement = client.post(f"/souscription/devis/{reference}/engagement", json={})
        assert engagement.status_code == 201
        assert engagement.json()["paiement"]["statut"] == "VALIDE"
        assert "MODE DÉMONSTRATION" in engagement.json()["message"]
        assert "aucun argent" in engagement.json()["message"]

        # 3 · ⚠️ Pas 83 : aucun lien ne part avant que le cabinet ait vérifié l'identité.
        assert client.get("/transverse/courriels").json() == []
        assert "vérification" in engagement.json()["message"]
        _verifier_par_le_cabinet(engagement.json()["souscription"]["reference"])

        # 4 · Le courriel d'activation est retenu, et lisible.
        courriels = client.get("/transverse/courriels").json()
        assert len(courriels) == 1
        message = courriels[0]
        assert message["code"] == "compte.activation"
        assert message["destinataire"] == PROSPECT["courriel"]

        # 4 · Le lien porte un jeton exploitable.
        lien = message["contexte"]["lien"]
        assert "/activation?jeton=" in lien
        jeton = lien.split("jeton=")[1]

        # 5 · Le mot de passe se définit avec ce jeton.
        #     ⚠️ Sans le patronyme : la politique refuse un mot de passe qui
        #     contient le nom, et c'est un refus légitime — pas un défaut.
        definition = client.post(
            "/transverse/mot-de-passe/definition",
            json={"secret": jeton, "mot_de_passe": "cocotier vert 9 lunes"},
        )
        assert definition.status_code == 200, definition.text

        # 6 · Et la connexion aboutit, avec le bon rôle et le bon périmètre.
        session = client.post(
            "/transverse/session",
            json={
                "courriel": PROSPECT["courriel"],
                "mot_de_passe": "cocotier vert 9 lunes",
            },
        )
        assert session.status_code == 200
        acces = session.json()
        assert acces["roles"] == ["ADHERENT"]
        assert acces["dossiers"] == [NIU]
        assert acces["interne"] is False

    def test_le_lien_ne_sert_qu_une_fois(self, client: TestClient):
        """Un lien rejouable serait un mot de passe permanent, envoyé en clair."""
        _souscrire_et_verifier(client)
        jeton = client.get("/transverse/courriels").json()[0]["contexte"]["lien"].split("jeton=")[1]

        client.post(
            "/transverse/mot-de-passe/definition",
            json={"secret": jeton, "mot_de_passe": "cocotier vert 9 lunes"},
        )
        rejeu = client.post(
            "/transverse/mot-de-passe/definition",
            json={"secret": jeton, "mot_de_passe": "autre chose 12 lunes"},
        )
        assert rejeu.status_code == 410

    def test_l_habilitation_est_bien_celle_d_un_adherent(self, client: TestClient):
        """Une souscription ne fabrique pas un collaborateur.

        Le contrôle est déjà fait ailleurs ; il est repris ici parce que ce
        parcours-ci passe par la validation d'office, qui est un chemin distinct.
        """
        from app.contextes.transverse.adaptateurs.entrant.dependances import atelier

        _souscrire_et_verifier(client)

        boutique = atelier()
        compte = boutique.comptes.par_courriel(PROSPECT["courriel"])
        assert compte is not None
        habilitations = boutique.habilitations.pour_compte(compte.identifiant)
        assert roles_au(habilitations, INSTANT.date()) == frozenset({Role.ADHERENT})
        assert habilitations[0].portee == frozenset({NIU})

    def test_la_boite_se_filtre_par_code(self, client: TestClient):
        _souscrire_et_verifier(client)

        assert client.get("/transverse/courriels", params={"code": "compte.activation"}).json()
        assert client.get("/transverse/courriels", params={"code": "compte.suspendu"}).json() == []
