"""Ce qu'un déposant peut déclarer, et ce que le système pose lui-même.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

La route de dépôt acceptait l'entité du domaine comme corps de requête. Un
adhérent — le **client**, pas le cabinet — pouvait donc déposer une pièce en
déclarant lui-même son état, son empreinte, sa date de réception et son auteur.

Mesuré avant correction, sur le compte `jp.nkoa` du jeu de démonstration :

| Prétention du client | Enregistrée ? |
| --- | --- |
| `etat: COMPTABILISEE` | **oui** |
| `reference_ecriture: AC-000042` | **oui** |
| `empreinte: aaaa…` (fabriquée) | **oui** |
| `depose_le: 2020-01-01` (six ans plus tôt) | **oui** |
| `recue_le` de son choix | **oui** |
| `depose_par` | **personne** — l'acte n'avait aucun auteur |

⚠️ Deux choses n'étaient **pas** vulnérables, et elles méritent d'être dites :
déposer sur le dossier d'un autre était refusé, et le message ne confirmait même
pas que ce dossier existe. La portée tenait ; c'est le contenu qui ne tenait pas.

⚠️ **Le domaine nommait pourtant la règle.** `recue_le` y porte ce commentaire :
« horodatée par le système à l'arrivée effective. **Seule celle-ci fait foi** ».
Et le module de collecte affirme que « l'empreinte est recalculée par le serveur
et fait foi, ce qui rend la détection de doublon indépendante de ce que le client
affirme » — vrai de `/fichiers`, faux de `/pieces`.

*Le domaine écrivait la règle, et la route donnait la plume au client.*
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import hashlib
import io
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.contextes.transverse.api import MOT_DE_PASSE_DEMO
from app.main import app
from app.partage.horloge import maintenant

#: Un adhérent réel du jeu de démonstration, et son dossier. Un compte forgé
#: prouverait que la route se défend d'un attaquant imaginaire ; celui-ci prouve
#: qu'elle se défend de son propre client.
ADHERENT = "jp.nkoa@batimentplus.cm"
MON_DOSSIER = "M081234567890P"
DOSSIER_D_UN_AUTRE = "M065544332211L"

#: Un PDF minimal mais réel : la route détermine le type par lecture des premiers
#: octets, et refuse ce qu'elle ne reconnaît pas.
PDF = b"%PDF-1.4\n9 0 obj\n<<>>\nendobj\ntrailer\n%%EOF\n"


@pytest.fixture
def adherent():
    with TestClient(app) as client:
        reponse = client.post(
            "/transverse/session",
            json={"courriel": ADHERENT, "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        assert reponse.status_code == 200, reponse.text
        yield client


@pytest.fixture
def empreinte(adherent, request):
    """Un fichier réellement rangé, et la clé que le serveur en a calculée.

    ⚠️ Le contenu porte le nom du cas : deux cas qui déposeraient le même octet
    produiraient la même clé, donc la même pièce, et le second se croirait refusé
    pour doublon alors qu'il l'est par son voisin.
    """
    contenu = PDF + request.node.name.encode()
    reponse = adherent.post(
        f"/collecte/fichiers?entreprise={MON_DOSSIER}",
        files={"fichier": ("facture.pdf", io.BytesIO(contenu), "application/pdf")},
    )
    assert reponse.status_code == 201, reponse.text
    cle = reponse.json()["empreinte"]
    assert cle == hashlib.sha256(contenu).hexdigest()
    return cle


def _deposer(client, empreinte, **surcharges):
    corps = {"entreprise": MON_DOSSIER, "canal": "PORTAIL", "empreinte": empreinte}
    return client.post("/collecte/pieces", json={**corps, **surcharges})


class TestCeQueLeSystemePose:
    def test_l_etat_est_toujours_recue(self, adherent, empreinte):
        """⚠️ La complétude et le score de risque lisent l'état : une pièce
        manquante se cachait en se déclarant comptabilisée."""
        piece = _deposer(adherent, empreinte).json()["piece"]
        assert piece["etat"] == "RECUE"
        assert piece["reference_ecriture"] is None

    def test_l_auteur_est_la_session_ouverte(self, adherent, empreinte):
        """⚠️ L'acte n'avait **aucun auteur**, alors qu'une session était ouverte.

        Tout le projet tient qu'un acte est personnel — « la validation est un
        acte personnel, pas un changement d'état anonyme ». Un dépôt l'est aussi.
        """
        piece = _deposer(adherent, empreinte).json()["piece"]
        assert piece["depose_par"] == "A-001"

    def test_la_date_de_reception_vient_de_l_horloge_du_serveur(
        self, adherent, empreinte
    ):
        """« Horodatée par le système à l'arrivée effective. Seule celle-ci fait
        foi » — le domaine le dit, la route l'applique désormais."""
        avant = maintenant()
        piece = _deposer(adherent, empreinte).json()["piece"]
        assert piece["recue_le"] >= avant.isoformat()[:19]

    def test_l_empreinte_et_la_taille_viennent_du_fichier_range(
        self, adherent, empreinte
    ):
        piece = _deposer(adherent, empreinte).json()["piece"]
        assert piece["empreinte"] == empreinte
        assert piece["taille_octets"] > 0

    def test_l_identifiant_derive_de_l_empreinte(self, adherent, empreinte):
        """⚠️ Il n'est pas tiré au sort : deux dépôts du même fichier sur le même
        dossier produisent la même clé. C'est la propriété du magasin adressé par
        le contenu, appliquée à la pièce."""
        piece = _deposer(adherent, empreinte).json()["piece"]
        assert empreinte[:16] in piece["identifiant"]
        assert MON_DOSSIER[:6] in piece["identifiant"]


class TestCeQueLeClientNePeutPlusDeclarer:
    @pytest.mark.parametrize(
        "champ, valeur",
        [
            ("etat", "COMPTABILISEE"),
            ("reference_ecriture", "AC-000042"),
            ("reference_rapport", "RC-2026-0001"),
            ("depose_par", "quelqu-un-d-autre"),
            ("recue_le", "2020-01-01T00:00:00"),
            ("identifiant", "PJ-INVENTEE"),
            ("taille_octets", 999999999),
        ],
    )
    def test_un_champ_du_systeme_est_refuse_et_nomme(self, adherent, empreinte, champ, valeur):
        """⚠️ **Refusé, et non ignoré.**

        Ignorer rendrait un 201 à un client qui croit avoir posé l'état. Il ne
        l'a pas posé, mais rien ne le lui dit, et l'intégrateur qui a écrit ce
        script ne l'apprendra jamais.

        *Un critère silencieusement absent est indiscernable d'un critère
        satisfait.*
        """
        reponse = _deposer(adherent, empreinte, **{champ: valeur})
        assert reponse.status_code == 422, reponse.text
        assert champ in reponse.text

    def test_le_depot_nominal_reste_accepte(self, adherent, empreinte):
        """⚠️ La contre-épreuve : sans elle, un modèle qui refuserait tout
        passerait chacun des cas précédents."""
        assert _deposer(adherent, empreinte).status_code == 201


class TestLEmpreinteEstRelue:
    def test_une_empreinte_inventee_est_refusee(self, adherent):
        """⚠️ Le magasin est adressé par le contenu, et le module l'affirme :
        « l'empreinte est recalculée par le serveur et fait foi ». C'était vrai
        de la route des fichiers, et faux de celle-ci."""
        reponse = _deposer(adherent, "a" * 64)
        assert reponse.status_code == 404, reponse.text
        assert "aucun fichier" in reponse.json()["detail"]

    def test_le_message_dit_quoi_faire(self, adherent):
        """Un refus qui ne dit pas le geste suivant fait ouvrir un ticket."""
        detail = _deposer(adherent, "b" * 64).json()["detail"]
        assert "/collecte/fichiers" in detail


class TestLaDateDeclaree:
    def test_une_date_recente_est_admise(self, adherent, empreinte):
        """Une pièce remise par coursier a été déposée avant d'être saisie : le
        domaine admet la déclaration, et il a raison."""
        hier = (maintenant().date() - timedelta(days=1)).isoformat()
        reponse = _deposer(adherent, empreinte, depose_le=hier)
        assert reponse.status_code == 201, reponse.text
        assert reponse.json()["piece"]["depose_le"] == hier

    def test_un_historique_de_six_ans_est_refuse(self, adherent, empreinte):
        """⚠️ Les délais de collecte, les relances et la veille lisent cette date.
        Un dépôt antidaté fabriquait un historique."""
        reponse = _deposer(adherent, empreinte, depose_le="2020-01-01")
        assert reponse.status_code == 422, reponse.text
        assert "hors des bornes" in reponse.json()["detail"]

    def test_une_date_future_est_refusee(self, adherent, empreinte):
        """Elle n'existe pas. L'accepter ferait qu'une pièce n'est jamais en
        souffrance, puisqu'elle n'est pas encore déposée."""
        assert _deposer(adherent, empreinte, depose_le="2030-01-01").status_code == 422


class TestLaPorteeTenaitDeja:
    """⚠️ Ces deux cas ne corrigent rien : ils gardent ce qui marchait.

    Sans eux, une correction future du dépôt pourrait relâcher la portée sans que
    personne ne s'en aperçoive — et c'est la seule des faiblesses trouvées qui
    aurait exposé les données d'un autre cabinet.
    """

    def test_deposer_sur_le_dossier_d_un_autre_est_refuse(self, adherent, empreinte):
        reponse = _deposer(adherent, empreinte, entreprise=DOSSIER_D_UN_AUTRE)
        assert reponse.status_code in (403, 404), reponse.text

    def test_le_refus_ne_confirme_pas_que_le_dossier_existe(self, adherent, empreinte):
        """Un message qui distinguerait « inconnu » de « hors de votre périmètre »
        permettrait d'énumérer les dossiers du cabinet."""
        reponse = _deposer(adherent, empreinte, entreprise=DOSSIER_D_UN_AUTRE)
        if reponse.status_code == 404:
            detail = reponse.json()["detail"]
            assert "n'est pas" in detail or "accessible" in detail


class TestLeMagasinAltere:
    """⚠️ **Le seul cas qui distingue « relu » de « cru ».**

    Tant que le client envoie la vraie clé, recalculer l'empreinte et la recopier
    donnent le même résultat : aucun cas ne les sépare. Une mutation remplaçant le
    recalcul par la valeur reçue survivait donc à tout le fichier.

    Ce qui les sépare est un magasin dont le contenu ne correspond plus à sa clé.
    C'est précisément ce que l'adressage par le contenu existe pour détecter, et
    le dépôt est le seul moment du parcours où la vérification coûte une lecture
    **déjà nécessaire**.
    """

    def test_un_contenu_qui_ne_correspond_pas_a_sa_cle_est_refuse(self, adherent):
        from app.contextes.collecte.adaptateurs.entrant.routes_http import (
            magasin_fichiers,
        )

        # Une clé valide en forme — soixante-quatre hexadécimaux — sous laquelle
        # on range un contenu qui n'a pas cette empreinte.
        cle_menteuse = hashlib.sha256(b"ce que la cle pretend").hexdigest()
        magasin_fichiers().deposer(
            cle_menteuse, b"ce que le fichier contient", type_mime="application/pdf"
        )

        reponse = _deposer(adherent, cle_menteuse)

        assert reponse.status_code == 409, reponse.text
        detail = reponse.json()["detail"]
        assert "altéré" in detail
        # ⚠️ Le message dit de **ne rien déposer** : poursuivre sur un document
        # dont l'intégrité est rompue est pire que s'arrêter.
        assert "ne rien déposer" in detail
