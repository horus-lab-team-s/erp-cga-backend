"""Le lien signé protège le numéro, et le client voit ce qu'il accepte.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE (pas 67)

L'acceptation d'une proforma est publique : le lien signé est l'authentification. Son
docstring affirmait que le sceau est vérifié avant la proforma, pour qu'on ne puisse pas
sonder l'existence d'un numéro. Le code lisait la proforma d'abord : un numéro inventé
répondait 404, un numéro existant au sceau faux 422. Les numéros sont séquentiels, et
n'importe qui énumérait les proformas du cabinet.

Et aucune route ne montrait au client ce qu'il acceptait.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from tests.conftest import exige_postgresql, ouvrir_une_session
from tests.test_prix_du_dossier import (
    REPONSES,
    RESPONSABLE,
    _chiffrer,
    _dossier_affecte,
    _qualifier,
)

pytestmark = exige_postgresql

FAUX_SCEAU = "0" * 64


def _proforma_emise(client, telephone):
    ouvrir_une_session(client, RESPONSABLE)
    reference = _dossier_affecte(client, telephone)
    _qualifier(client, reference, REPONSES)
    montant = _chiffrer(client, reference).json()["reference"]
    emise = client.post(f"/acquisition/dossiers/{reference}/proforma", json={"montant": montant})
    assert emise.status_code == 201, emise.text
    return emise.json()


def _lien(proforma):
    return {
        "version": proforma["version"],
        "expire_le": proforma["expire_le"],
        "sceau": proforma["lien_acceptation"],
    }


class TestOnNEnumereRien:
    def test_sans_lien_un_numero_existant_et_un_numero_invente_repondent_pareil(self, plateforme):
        client = plateforme
        proforma = _proforma_emise(client, "699113311")
        visiteur = {"version": 1, "expire_le": proforma["expire_le"], "sceau": FAUX_SCEAU}
        existant = client.post(
            f"/acquisition/proformas/{proforma['numero']}/acceptation",
            json={**visiteur, "identite_declaree": "Quelqu'un"},
        )
        invente = client.post(
            "/acquisition/proformas/PRO-2026-9999/acceptation",
            json={**visiteur, "identite_declaree": "Quelqu'un"},
        )
        assert existant.status_code == invente.status_code == 422, (existant.text, invente.text)
        assert "inconnue" not in invente.text

        lecture_existante = client.get(
            f"/acquisition/proformas/{proforma['numero']}/consultation", params=visiteur
        )
        lecture_inventee = client.get(
            "/acquisition/proformas/PRO-2026-9999/consultation", params=visiteur
        )
        assert lecture_existante.status_code == lecture_inventee.status_code == 422


class TestLeClientVoitCeQuIlAccepte:
    def test_la_consultation_montre_le_prix_et_rien_d_interne(self, plateforme):
        client = plateforme
        proforma = _proforma_emise(client, "699113322")
        lecture = client.get(
            f"/acquisition/proformas/{proforma['numero']}/consultation", params=_lien(proforma)
        )
        assert lecture.status_code == 200, lecture.text
        corps = lecture.json()
        assert corps["montant"] == proforma["montant"]
        assert corps["acceptee"] is False
        for interne in ("plancher", "plafond", "chiffre_par", "valide_par", "faits"):
            assert interne not in corps, f"« {interne} » est interne au cabinet"

    def test_lire_ne_consomme_rien_et_l_acceptation_suit(self, plateforme):
        client = plateforme
        proforma = _proforma_emise(client, "699113333")
        chemin = f"/acquisition/proformas/{proforma['numero']}"
        for _ in range(2):
            assert client.get(f"{chemin}/consultation", params=_lien(proforma)).status_code == 200
        acceptation = client.post(
            f"{chemin}/acceptation", json={**_lien(proforma), "identite_declaree": "Le gérant"}
        )
        assert acceptation.status_code == 200, acceptation.text
        apres = client.get(f"{chemin}/consultation", params=_lien(proforma))
        assert apres.json()["acceptee"] is True

    def test_un_lien_d_une_autre_version_ne_montre_rien(self, plateforme):
        """Le sceau d'une autre version est faux : il est refusé avant toute lecture."""
        client = plateforme
        proforma = _proforma_emise(client, "699113344")
        autre = {**_lien(proforma), "version": proforma["version"] + 1}
        lecture = client.get(
            f"/acquisition/proformas/{proforma['numero']}/consultation", params=autre
        )
        assert lecture.status_code == 422, lecture.text


def test_la_fiche_du_dossier_liste_ses_proformas_sans_leur_lien(plateforme):
    """Pas 68 : la fiche d'un dossier accepté doit savoir quelle proforma faire régler."""
    client = plateforme
    proforma = _proforma_emise(client, "699113355")
    (dossier,) = [
        d for d in client.get("/acquisition/dossiers").json() if d["etat"] == "PROFORMA_EMISE"
    ]
    fiche = client.get(f"/acquisition/dossiers/{dossier['reference']}").json()
    assert [p["numero"] for p in fiche["proformas"]] == [proforma["numero"]]
    assert proforma["lien_acceptation"] not in str(fiche), "le lien ne se réaffiche jamais"
