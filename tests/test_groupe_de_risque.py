"""Le groupe de risque professionnel notifié par la CNPS, porté par le dossier (16 octobre 2026).

⚠️ CE QUI EST GARDÉ
* le groupe s'enregistre une fois au dossier ; bulletins et DIPE le lisent d'office ;
* tant qu'il manque, le calcul suppose A, le plus bas, et LE DIT : le bulletin porte une
  alerte, le DIPE un drapeau ; sous-cotiser sans le dire ne se verrait qu'au contrôle ;
* un groupe demandé dans l'URL reste une simulation et l'emporte ;
* l'employeur peut l'enregistrer depuis son espace (c'est lui qui reçoit la notification),
  pas un collaborateur sans la tenue de la paie ; une date à venir est refusée.
"""

from __future__ import annotations

from tests.conftest import exige_postgresql, ouvrir_une_session

pytestmark = exige_postgresql

BATIMENT = "M081234567890P"
COMPTABLE = "l.fotso@cga-brcg.cm"
ADHERENT = "jp.nkoa@batimentplus.cm"
BULLETIN = f"/social/dossiers/{BATIMENT}/bulletins/2026/9/SAL-0004"
GROUPE = f"/social/dossiers/{BATIMENT}/groupe-risque"


def _ligne_at(bulletin: dict) -> dict:
    return next(x for x in bulletin["lignes"] if "accidents du travail" in x["libelle"])


def test_sans_groupe_au_dossier_a_est_suppose_et_le_bulletin_le_dit(plateforme):
    client = plateforme
    ouvrir_une_session(client, COMPTABLE)
    assert client.get(GROUPE).json() == {"groupe": None, "notifie_le": None}

    bulletin = client.get(BULLETIN).json()
    assert "groupe A" in _ligne_at(bulletin)["libelle"]
    assert any("non renseigné" in a for a in bulletin["alertes"])
    dipe = client.get(
        f"/social/dossiers/{BATIMENT}/declaration/2026/9", params={"a_la_date": "2026-10-10"}
    ).json()
    assert (dipe["groupe_risque"], dipe["groupe_risque_suppose"]) == ("A", True)


def test_le_groupe_notifie_s_applique_partout_sans_alerte(plateforme):
    client = plateforme
    ouvrir_une_session(client, COMPTABLE)
    a = _ligne_at(client.get(BULLETIN).json())

    reponse = client.put(GROUPE, json={"groupe": "C", "notifie_le": "2026-03-02"})
    assert reponse.status_code == 200, reponse.text
    assert client.get(GROUPE).json() == {"groupe": "C", "notifie_le": "2026-03-02"}

    bulletin = client.get(BULLETIN).json()
    c = _ligne_at(bulletin)
    assert "groupe C" in c["libelle"] and float(c["taux"]) > float(a["taux"])
    assert not any("non renseigné" in x for x in bulletin["alertes"])
    dipe = client.get(
        f"/social/dossiers/{BATIMENT}/declaration/2026/9", params={"a_la_date": "2026-10-10"}
    ).json()
    assert (dipe["groupe_risque"], dipe["groupe_risque_suppose"]) == ("C", False)
    # Une simulation par l'URL l'emporte, sans toucher au dossier.
    simule = client.get(BULLETIN, params={"groupe_risque": "B"}).json()
    assert "groupe B" in _ligne_at(simule)["libelle"]
    assert client.get(GROUPE).json()["groupe"] == "C"


def test_l_employeur_l_enregistre_depuis_son_espace(plateforme):
    client = plateforme
    ouvrir_une_session(client, ADHERENT)
    reponse = client.put(GROUPE, json={"groupe": "B", "notifie_le": "2026-01-15"})
    assert reponse.status_code == 200, reponse.text


def test_ni_date_a_venir_ni_collaborateur_sans_la_tenue_de_la_paie(plateforme):
    client = plateforme
    ouvrir_une_session(client, COMPTABLE)
    futur = client.put(GROUPE, json={"groupe": "B", "notifie_le": "2099-01-01"})
    assert futur.status_code == 422
    client.cookies.clear()
    ouvrir_une_session(client, "p.moukouri@cga-brcg.cm")  # chargée de clientèle
    assert client.put(GROUPE, json={"groupe": "B", "notifie_le": "2026-01-15"}).status_code in (
        403,
        404,
    )
    client.cookies.clear()
    ouvrir_une_session(client, COMPTABLE)
    assert client.get(GROUPE).json()["groupe"] is None
