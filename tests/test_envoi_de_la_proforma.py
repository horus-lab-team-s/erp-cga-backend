"""Le prix arrêté après l'échange part chez le client, par courriel, avec son lien.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Le prix d'un service sur étude n'est pas au catalogue : il est arrêté par le
responsable **après l'échange** avec le client, puis la proforma part chez lui.
Jusqu'ici, la console affichait le lien une fois, et le responsable devait le
recopier dans un courriel à la main, puis cocher « j'ai envoyé ». Un lien mal
recopié, et le client ouvrait une page d'erreur ; une case oubliée, et personne ne
le relançait.

L'émission peut désormais écrire elle-même au client. Ces cas tiennent :

  1. le courriel part vers l'adresse du dossier, avec un lien qui OUVRE la proforma ;
  2. un courriel parti vaut transmission : la relance est armée ;
  3. sans adresse, rien ne part, et la réponse le dit (le responsable passe à WhatsApp) ;
  4. sans la demande, rien ne change : les appels existants gardent leur comportement ;
  5. le gabarit se rend avec exactement le contexte que la route lui donne.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from app.contextes.transverse.adaptateurs.entrant.dependances import service_de_notification
from app.contextes.transverse.adaptateurs.sortant.gabarits_courriel import (
    cles_manquantes,
    gabarit,
    rendre,
)
from app.infrastructure.config import configuration
from tests.conftest import exige_postgresql, ouvrir_une_session
from tests.test_prix_du_dossier import REPONSES, RESPONSABLE, _chiffrer, _qualifier

pytestmark = exige_postgresql

COURRIEL = "gerant@station-bonaberi.cm"


def _dossier_chiffre(client, telephone, courriel=None):
    ouvrir_une_session(client, RESPONSABLE)
    depot = client.post(
        "/acquisition/demandes",
        json={
            "nom": "Station Bonabéri",
            "telephone": telephone,
            "courriel": courriel,
            "service_souhaite": "CREATION",
            "canal_prefere": "COURRIEL" if courriel else "APPEL",
            "message": "je veux créer une SARL",
        },
    )
    assert depot.status_code in (200, 201), depot.text
    (dossier,) = [d for d in client.get("/acquisition/dossiers").json() if d["etat"] == "DEPOSEE"]
    reference = dossier["reference"]
    assert client.post(f"/acquisition/dossiers/{reference}/affectation", json={}).json()["affecte"]
    _qualifier(client, reference, REPONSES)
    return reference, _chiffrer(client, reference).json()["reference"]


@pytest.fixture
def boite():
    service = service_de_notification()
    service.vider()
    yield service
    service.vider()


def test_le_courriel_part_avec_un_lien_qui_ouvre_la_proforma(plateforme, boite):
    client = plateforme
    reference, montant = _dossier_chiffre(client, "699114411", COURRIEL)
    emise = client.post(
        f"/acquisition/dossiers/{reference}/proforma",
        json={"montant": montant, "envoyer_par_courriel": True},
    )
    assert emise.status_code == 201, emise.text
    corps = emise.json()
    assert corps["courriel"] == "ENVOYE"
    assert corps["courriel_masque"] == "g***@station-bonaberi.cm"

    (message,) = boite.derniers("proforma.envoi")
    assert message.destinataire == COURRIEL
    assert message.contexte["proforma"] == corps["numero"]

    # ⚠️ Le lien s'ouvre sur la VITRINE, là où vit la page du client, et ses
    # paramètres sont ceux qu'elle lit. On le rejoue sur la route de consultation :
    # un lien qui ne s'ouvre pas ne vaut rien, même bien formé.
    lien = urlsplit(message.contexte["lien"])
    vitrine = urlsplit(configuration().adresse_publique_vitrine)
    assert (lien.scheme, lien.netloc) == (vitrine.scheme, vitrine.netloc)
    assert lien.path == f"/fr/proforma/{corps['numero']}"
    parametres = {cle: valeurs[0] for cle, valeurs in parse_qs(lien.query).items()}
    lecture = client.get(
        f"/acquisition/proformas/{corps['numero']}/consultation",
        params={"version": parametres["v"], "expire_le": parametres["e"], "sceau": parametres["s"]},
    )
    assert lecture.status_code == 200, lecture.text


def test_un_courriel_parti_vaut_transmission(plateforme, boite):
    client = plateforme
    reference, montant = _dossier_chiffre(client, "699114422", COURRIEL)
    corps = client.post(
        f"/acquisition/dossiers/{reference}/proforma",
        json={"montant": montant, "envoyer_par_courriel": True},
    ).json()
    assert corps["etat"] == "TRANSMISE"
    assert corps["transmise_le"] is not None
    (fiche,) = client.get(f"/acquisition/dossiers/{reference}").json()["proformas"]
    assert fiche["transmise_le"] is not None, "la relance ne serait jamais armée"


def test_sans_adresse_rien_ne_part_et_la_reponse_le_dit(plateforme, boite):
    client = plateforme
    reference, montant = _dossier_chiffre(client, "699114433")
    corps = client.post(
        f"/acquisition/dossiers/{reference}/proforma",
        json={"montant": montant, "envoyer_par_courriel": True},
    ).json()
    assert corps["courriel"] == "SANS_ADRESSE"
    assert corps["etat"] == "EMISE", "rien n'est parti : ce n'est pas une transmission"
    assert corps["lien_acceptation"], "le responsable doit pouvoir l'envoyer autrement"
    assert boite.derniers("proforma.envoi") == []


def test_sans_la_demande_rien_ne_change(plateforme, boite):
    client = plateforme
    reference, montant = _dossier_chiffre(client, "699114444", COURRIEL)
    corps = client.post(
        f"/acquisition/dossiers/{reference}/proforma", json={"montant": montant}
    ).json()
    assert corps["courriel"] is None
    assert corps["etat"] == "EMISE"
    assert boite.derniers("proforma.envoi") == []


def test_le_gabarit_se_rend_avec_le_contexte_de_la_route():
    modele = gabarit("proforma.envoi")
    assert modele is not None
    contexte = {
        "nom": "Station <Bonabéri>",
        "proforma": "PRO-2026-0001",
        "montant": "350 000",
        "lien": "https://www.cga-brcgroup.com/fr/proforma/PRO-2026-0001?v=1",
        "expire_le": "22/10/2026",
    }
    assert cles_manquantes(modele, contexte) == frozenset()
    rendu = rendre(modele, contexte)
    assert contexte["lien"] in rendu.texte
    # ⚠️ La dénomination est échappée dans le HTML : un nom saisi par un prospect
    # ne devient jamais du balisage.
    assert "Station &lt;Bonabéri&gt;" in rendu.html
