"""Le cabinet corrige le service après l'échange, sans effacer ce qu'elle a demandé.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

La visiteuse coche une case sur un formulaire public, **avant tout échange**.
C'est précisément ce que le responsable découvre en l'appelant : elle a coché
« prestation ponctuelle » et il lui faut une adhésion.

Aucune route ne permettait de le corriger. Le dossier était coincé — le
questionnaire est celui du mauvais service, le chiffrage aussi — et sa seule
sortie était le classement sans suite, qui perd l'échange et oblige la cliente à
tout redéposer.

Trouvé en fermant la porte d'entrée : en cherchant à empêcher les codes inconnus
d'entrer, on voit que le code juste ne peut pas non plus être rétabli.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from tests.conftest import exige_postgresql, ouvrir_une_session

pytestmark = exige_postgresql

RESPONSABLE = "b.mballa@cga-brcg.cm"


def _dossier(client, *, service: str, telephone: str) -> str:
    """Un dossier déposé par la porte publique, affecté, prêt pour l'échange."""
    depot = client.post(
        "/acquisition/demandes",
        json={
            "nom": f"Essai {telephone}",
            "telephone": telephone,
            "service_souhaite": service,
            "canal_prefere": "APPEL",
            "message": "déposé depuis le site",
        },
    )
    assert depot.status_code in (200, 201), depot.text
    ouvrir_une_session(client, RESPONSABLE)
    (vu,) = [
        d
        for d in client.get("/acquisition/dossiers").json()
        if d["nom"] == f"Essai {telephone}"
    ]
    client.post(f"/acquisition/dossiers/{vu['reference']}/affectation", json={})
    return vu["reference"]


class TestLeServiceSeReprendApresLEchange:
    def test_elle_a_demande_ponctuel_il_retient_adhesion(self, plateforme):
        client = plateforme
        reference = _dossier(client, service="PONCTUEL", telephone="699551101")

        repris = client.post(
            f"/acquisition/dossiers/{reference}/service",
            json={
                "service": "ADHESION",
                "motif": "Au téléphone : elle veut un suivi mensuel, pas une mission.",
            },
        )
        assert repris.status_code == 200, repris.text
        corps = repris.json()
        assert corps["service"] == "ADHESION"
        # ⚠️ LE POINT DU PAS : ce qu'elle a demandé n'est pas réécrit.
        assert corps["service_souhaite"] == "PONCTUEL"
        assert "suivi mensuel" in corps["motif_du_service_retenu"]

    def test_la_fiche_montre_les_deux(self, plateforme):
        """⚠️ Sans les deux, le collaborateur croit à une erreur de saisie."""
        client = plateforme
        reference = _dossier(client, service="PONCTUEL", telephone="699551102")
        client.post(
            f"/acquisition/dossiers/{reference}/service",
            json={"service": "ADHESION", "motif": "elle veut un suivi"},
        )
        (vu,) = [
            d
            for d in client.get("/acquisition/dossiers").json()
            if d["reference"] == reference
        ]
        assert vu["service_souhaite"] == "PONCTUEL"
        assert vu["service"] == "ADHESION"
        assert vu["service_retenu"] == "ADHESION"

    def test_le_travail_suit_le_service_retenu(self, plateforme):
        """⚠️ LA RAISON D'ÊTRE DU GESTE, ET LA SEULE QUI COMPTE.

        `PONCTUEL` n'a pas de questionnaire : le dossier ne pouvait pas être
        qualifié. Après la reprise vers `ADHESION`, qui en a un, il doit pouvoir
        l'être. Si cette assertion tombe, le geste est décoratif.
        """
        client = plateforme
        reference = _dossier(client, service="PONCTUEL", telephone="699551103")

        avant = client.post(
            f"/acquisition/dossiers/{reference}/qualification",
            json={"reponses": []},
        )
        assert avant.status_code == 404, avant.text
        assert "PONCTUEL" in avant.json()["detail"]

        client.post(
            f"/acquisition/dossiers/{reference}/service",
            json={"service": "ADHESION", "motif": "elle veut un suivi mensuel"},
        )
        apres = client.post(
            f"/acquisition/dossiers/{reference}/qualification",
            json={"reponses": []},
        )
        assert apres.status_code != 404, (
            "le questionnaire suit encore le service demandé, pas le service retenu"
        )


class TestCeQueLeGesteRefuse:
    def test_un_code_inconnu_du_catalogue(self, plateforme):
        """⚠️ Sans ce contrôle, cette route rouvrirait par l'arrière la porte
        qu'on vient de fermer côté public."""
        client = plateforme
        reference = _dossier(client, service="PONCTUEL", telephone="699551104")
        refus = client.post(
            f"/acquisition/dossiers/{reference}/service",
            json={"service": "JURIDIQUE", "motif": "elle parle de son contrat"},
        )
        assert refus.status_code == 422, refus.text
        detail = refus.json()["detail"]
        assert isinstance(detail, str), "le refus doit être une phrase, pas une trace"
        assert "JURIDIQUE" in detail and "ADHESION" in detail

    def test_un_motif_vide(self, plateforme):
        """C'est la seule trace de la raison, et elle se relit au litige."""
        client = plateforme
        reference = _dossier(client, service="PONCTUEL", telephone="699551105")
        refus = client.post(
            f"/acquisition/dossiers/{reference}/service",
            json={"service": "ADHESION", "motif": "   "},
        )
        assert refus.status_code in (409, 422), refus.text

    def test_le_meme_service(self, plateforme):
        """Rejouer sans rien changer écrirait un motif qui ne correspond à
        aucune décision."""
        client = plateforme
        reference = _dossier(client, service="PONCTUEL", telephone="699551106")
        refus = client.post(
            f"/acquisition/dossiers/{reference}/service",
            json={"service": "PONCTUEL", "motif": "pour voir"},
        )
        assert refus.status_code == 409, refus.text

    def test_apres_la_qualification(self, plateforme):
        """⚠️ Les réponses recueillies l'ont été au questionnaire du service
        précédent : les garder sous un autre service les rendrait fausses sans
        que rien ne le dise."""
        from tests.test_prix_du_dossier import REPONSES, _qualifier

        client = plateforme
        reference = _dossier(client, service="CREATION", telephone="699551107")
        _qualifier(client, reference, REPONSES)

        refus = client.post(
            f"/acquisition/dossiers/{reference}/service",
            json={"service": "ADHESION", "motif": "changement d'avis"},
        )
        assert refus.status_code == 409, refus.text
        assert "questionnaire" in refus.json()["detail"]

    def test_sans_l_habilitation_de_celui_qui_mene_l_echange(self, plateforme):
        client = plateforme
        reference = _dossier(client, service="PONCTUEL", telephone="699551108")
        client.cookies.clear()
        assert (
            client.post(
                f"/acquisition/dossiers/{reference}/service",
                json={"service": "ADHESION", "motif": "elle veut un suivi"},
            ).status_code
            == 401
        )


def test_un_dossier_inconnu_rend_404(plateforme):
    client = plateforme
    ouvrir_une_session(client, RESPONSABLE)
    assert (
        client.post(
            "/acquisition/dossiers/dos-inexistant/service",
            json={"service": "ADHESION", "motif": "peu importe"},
        ).status_code
        == 404
    )
