"""Le jeton de session porte son locataire, et le bord le confronte au domaine (pas 126).

─────────────────────────────────────────────────────────────────────────────────
LE DÉFAUT QUE CE FICHIER GARDE FERMÉ

Dernier reste du chantier multi-tenant, deuxième de sa liste : « la double vérification du
tenant du jeton contre celui du domaine, dans le chemin d'authentification ».

Mesuré avant d'écrire une ligne, sur la pile de démonstration :

    session ouverte sur   cabinet.cga.cm            → 200
    le même témoin sur    station-bonaberi.cga.cm   → 200, accès « locataire CGA-BRCG »

La requête était servie **dans le périmètre de station-bonaberi**, avec les permissions
d'un compte du cabinet, et rien ne le signalait.

⚠️ **En base, le filtre de cloisonnement fermait la porte par accident** : la ligne de
session n'est pas trouvée sous un autre locataire, et l'appelant reçoit 401. Un bon
résultat obtenu par un mécanisme qui ne visait pas cela disparaît le jour où l'on met les
sessions en cache, ou ailleurs qu'en base. C'est pourquoi la vérification est désormais
explicite, et pourquoi elle est testée ici en mémoire, là où aucun filtre ne protégeait.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.contextes.tenants.api import NatureTenant
from app.contextes.tenants.application.cycle_de_vie import activer, avancer, ouvrir
from app.contextes.tenants.domaine.tenant import ETAPES_ORDONNEES, Tenant
from app.contextes.transverse.adaptateurs.entrant.dependances import (
    NOM_TEMOIN,
    repertoire_des_tenants,
)
from app.contextes.transverse.adaptateurs.entrant.jeton import (
    SEPARATEUR,
    LocataireInscriptible,
    composer,
    lire,
)
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.main import creer_application

CABINET = "cabinet"
STATION = "station-bonaberi"
LE_JOUR = date(2026, 9, 9)
REVISEUR = "a.bouba@cga-brcg.cm"


class TestLeJeton:
    """Le composer et le relire, sans rien monter."""

    def test_aller_retour(self):
        assert lire(composer(CABINET, "S-42")) == (CABINET, "S-42")

    def test_sans_prefixe_le_locataire_est_inconnu(self):
        """⚠️ Inconnu, et non « celui de la requête » : c'est l'appelant qui décide quoi
        en faire, et lui seul connaît le locataire du domaine."""
        assert lire("S-42") == (None, "S-42")

    def test_la_coupe_se_fait_sur_la_premiere_occurrence(self):
        """Couper sur la dernière ferait d'un identifiant fantaisiste un moyen de
        déclarer un autre locataire que le sien."""
        assert lire(f"cabinet{SEPARATEUR}S-42{SEPARATEUR}x") == (CABINET, f"S-42{SEPARATEUR}x")

    def test_un_locataire_qui_porte_le_separateur_est_refuse(self):
        """Refusé à l'écriture plutôt que deviné à la lecture : l'ambiguïté se réglerait
        toujours au détriment de la vérification."""
        with pytest.raises(LocataireInscriptible):
            composer(f"cab{SEPARATEUR}inet", "S-42")

    def test_un_temoin_vide_ne_rend_pas_d_identifiant(self):
        assert lire("") == (None, "")


def _actif(slug: str) -> Tenant:
    tenant = ouvrir(f"tnt-{slug}", slug, NatureTenant.ENTREPRISE)
    for etape in ETAPES_ORDONNEES[1:]:
        tenant = avancer(tenant, etape)
    return activer(tenant, LE_JOUR)


@pytest.fixture
def deux_sous_domaines():
    """Deux locataires servis par le même processus, en mémoire.

    ⚠️ **En mémoire, et c'est le seul endroit où ce cas se prouve.** L'atelier est unique
    pour le processus : aucun filtre de cloisonnement ne protège, donc ce qui refuse le
    jeton est bien la vérification, et rien d'autre. En base, la même requête serait
    refusée pour une seconde raison, et le test ne dirait plus laquelle des deux agit.
    """
    reinitialiser_atelier()
    repertoire_des_tenants.cache_clear()
    repertoire = repertoire_des_tenants()
    for slug in (CABINET, STATION):
        repertoire.inscrire(_actif(slug))
    yield TestClient(creer_application())
    repertoire_des_tenants.cache_clear()
    reinitialiser_atelier()


def _ouvrir(client: TestClient, hote: str):
    return client.post(
        "/transverse/session",
        json={"courriel": REVISEUR, "mot_de_passe": MOT_DE_PASSE_DEMO},
        headers={"Host": f"{hote}.cga.cm"},
    )


class TestLeBordConfronte:
    def test_le_temoin_pose_porte_son_locataire(self, deux_sous_domaines):
        client = deux_sous_domaines
        reponse = _ouvrir(client, CABINET)
        assert reponse.status_code == 200, reponse.text
        assert client.cookies.get(NOM_TEMOIN).startswith(f"{CABINET}{SEPARATEUR}")
        # Le corps rend l'identifiant nu : c'est lui qui désigne la session partout ailleurs.
        assert SEPARATEUR not in reponse.json()["session"]

    def test_le_temoin_vaut_sur_son_propre_sous_domaine(self, deux_sous_domaines):
        client = deux_sous_domaines
        _ouvrir(client, CABINET)
        chez_soi = client.get("/transverse/moi", headers={"Host": f"{CABINET}.cga.cm"})
        assert chez_soi.status_code == 200

    def test_le_temoin_ne_vaut_pas_ailleurs(self, deux_sous_domaines):
        """⚠️ Le défaut d'origine. Avant la correction : 200, et la requête servie dans le
        périmètre de l'autre locataire."""
        client = deux_sous_domaines
        _ouvrir(client, CABINET)
        reponse = client.get("/transverse/moi", headers={"Host": f"{STATION}.cga.cm"})
        assert reponse.status_code == 401

    def test_le_refus_ne_dit_pas_pourquoi(self, deux_sous_domaines):
        """Dire « ce jeton appartient à un autre locataire » confirmerait que cet autre
        locataire existe. L'appelant reçoit ce qu'il recevrait d'un jeton expiré."""
        client = deux_sous_domaines
        _ouvrir(client, CABINET)
        reponse = client.get("/transverse/moi", headers={"Host": f"{STATION}.cga.cm"})
        assert CABINET not in reponse.text and STATION not in reponse.text

    def test_un_identifiant_nu_vaut_sur_son_hote(self, deux_sous_domaines):
        """L'outillage en ligne de commande présente l'identifiant nu, et il doit marcher.

        C'est aussi ce qui permet aux sessions déjà ouvertes de survivre à un déploiement
        au lieu de déconnecter tout le cabinet.
        """
        client = deux_sous_domaines
        identifiant = _ouvrir(client, CABINET).json()["session"]
        client.cookies.clear()
        reponse = client.get(
            "/transverse/moi",
            headers={"Host": f"{CABINET}.cga.cm", "Authorization": f"Bearer {identifiant}"},
        )
        assert reponse.status_code == 200

    def test_un_identifiant_nu_venu_d_ailleurs_ne_tient_qu_au_cloisonnement(
        self, deux_sous_domaines
    ):
        """⚠️ **La limite exacte de cette vérification, écrite plutôt que sous-entendue.**

        Un identifiant nu ne déclare aucun locataire : il est donc lu comme appartenant à
        celui de la requête, et c'est la recherche en base qui tranche. En mémoire,
        l'atelier est unique et ne tranche rien : il passe.

        Ce n'est pas un contournement de la production, où la persistance mémoire est
        **refusée au démarrage** par la configuration, et où la ligne de session n'existe
        pas sous un autre locataire. Le cas est écrit ici pour que la frontière soit
        connue, et pour qu'un déplacement des sessions hors de la base rende ce test
        rouge plutôt que silencieux.
        """
        client = deux_sous_domaines
        identifiant = _ouvrir(client, CABINET).json()["session"]
        client.cookies.clear()
        reponse = client.get(
            "/transverse/moi",
            headers={"Host": f"{STATION}.cga.cm", "Authorization": f"Bearer {identifiant}"},
        )
        assert reponse.status_code == 200, "en mémoire, seul le préfixe protège"

    def test_la_deconnexion_marche_avec_le_temoin_compose(self, deux_sous_domaines):
        client = deux_sous_domaines
        _ouvrir(client, CABINET)
        hote = {"Host": f"{CABINET}.cga.cm"}
        assert client.delete("/transverse/session", headers=hote).status_code == 204
        assert client.get("/transverse/moi", headers=hote).status_code == 401
