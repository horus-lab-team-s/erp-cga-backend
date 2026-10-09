"""Une demande déposée est confirmée par écrit, référence comprise.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Le parcours entier a été rejoué le 28 septembre, du formulaire du site jusqu'à
l'espace de la fondatrice. Compté depuis sa boîte, la cliente recevait **deux**
messages : sa proposition commerciale, puis son accès. Le premier arrive après
l'échange avec l'expert, c'est-à-dire des jours après son dépôt.

Entre les deux, rien. Elle quittait la page sur « un responsable vous recontacte »
et n'avait plus aucune trace de son geste : ni référence, ni confirmation, rien à
relire. C'est le silence le plus long du parcours, et celui où elle doute le plus.

Ces cas passent par le VRAI relais (`POST /orchestration/publication`), sur
PostgreSQL : un abonné inscrit mais jamais remis ne se verrait pas autrement —
c'est exactement le défaut que `DemandeDéposée` a déjà connu.

  1. une demande avec adresse reçoit son accusé, et il porte la référence ;
  2. une republication n'en envoie pas un second ;
  3. sans adresse, pas d'accusé, et pas d'incident non plus ;
  4. sans consentement écrit, pas d'accusé ;
  5. l'accusé ne dépend pas de l'affectation, et ne promet aucun délai.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import pytest

from app.contextes.transverse.adaptateurs.entrant.dependances import (
    service_de_notification,
)
from tests.conftest import exige_postgresql, ouvrir_une_session

pytestmark = exige_postgresql

ADMINISTRATION = "s.onana@cga-brcg.cm"
CODE = "demande.accuse_de_depot"


@pytest.fixture(autouse=True)
def _boite_vide():
    """Chaque cas lit ce que LUI a produit, pas ce que le précédent a laissé."""
    service_de_notification().vider()
    yield


def _deposer(client, telephone, **extra):
    corps = {
        "nom": "Arrivée Essai",
        "telephone": telephone,
        "service_souhaite": "CREATION",
        "canal_prefere": "APPEL",
    } | extra
    r = client.post("/acquisition/demandes", json=corps)
    assert r.status_code == 201, r.text
    return r.json()


def _publier(client):
    r = client.post("/orchestration/publication")
    assert r.status_code == 200, r.text
    return r.json()


def _dossier(client, telephone):
    (dossier,) = [
        d
        for d in client.get("/acquisition/dossiers").json()
        if d["telephone"].endswith(telephone)
    ]
    return dossier


def _accuses(courriel=None):
    envoyes = service_de_notification().derniers(CODE)
    if courriel is None:
        return envoyes
    return [m for m in envoyes if m.destinataire == courriel]


def test_une_demande_avec_adresse_recoit_son_accuse(plateforme):
    client = plateforme
    _deposer(client, "699330011", courriel="arrivee1@exemple.cm")
    ouvrir_une_session(client, ADMINISTRATION)
    reference = _dossier(client, "699330011")["reference"]

    passage = _publier(client)
    assert passage["echecs"] == 0, passage

    (accuse,) = _accuses("arrivee1@exemple.cm")
    # ⚠️ LA RÉFÉRENCE EST LE CŒUR DU MESSAGE. C'est ce qu'elle citera au
    # téléphone, et ce qui permet de retrouver son dossier en un geste.
    assert accuse.contexte["reference"] == reference
    # Le libellé du catalogue, jamais le code brut si le catalogue le connaît.
    assert accuse.contexte["service"] == "Création d'entreprise"
    assert accuse.contexte["telephone"].endswith("699330011")


def test_une_republication_n_envoie_pas_un_second_accuse(plateforme):
    """⚠️ Le relais remet **au moins une fois**, et c'est le régime normal."""
    client = plateforme
    _deposer(client, "699330022", courriel="arrivee2@exemple.cm")
    ouvrir_une_session(client, ADMINISTRATION)

    _publier(client)
    assert len(_accuses("arrivee2@exemple.cm")) == 1

    # Une seconde remise du même fait : la cliente n'a déposé qu'une demande, et
    # un second « nous avons bien reçu » lui ferait croire le contraire.
    _publier(client)
    _publier(client)
    assert len(_accuses("arrivee2@exemple.cm")) == 1


def test_sans_adresse_aucun_accuse_et_aucun_incident(plateforme):
    """L'adresse est facultative au formulaire, et le téléphone reste le canal."""
    client = plateforme
    _deposer(client, "699330033")
    ouvrir_une_session(client, ADMINISTRATION)

    passage = _publier(client)
    # ⚠️ CONSOMMÉ, PAS EN ÉCHEC. Une demande sans adresse n'est pas une panne :
    # c'est une cliente qu'on rappellera, ce qu'elle a demandé. Lever ferait
    # retenter l'événement puis le mettre en quarantaine, pour un fait métier.
    assert passage["echecs"] == 0, passage
    assert _accuses() == []


def test_la_case_whatsapp_decochee_ne_prive_pas_de_l_accuse(plateforme):
    """⚠️ LE PIÈGE, ET IL EST GROS.

    Il y a un objet `Consentement` sur la demande, il porte `vaut_maintenant`,
    et le brancher ici ferait sérieux. Ce serait faux : ce consentement est
    celui de **WhatsApp** — champ `consentement_whatsapp`, version
    `consentement-whatsapp-v1` — et il est décoché par défaut.

    S'y adosser priverait d'accusé la grande majorité des clientes, celles qui
    ont pourtant écrit leur adresse dans le formulaire. Ce qui autorise ce
    message, c'est qu'elle a donné cette adresse, à l'instant, dans ce
    formulaire-là.
    """
    client = plateforme
    _deposer(
        client,
        "699330044",
        courriel="arrivee4@exemple.cm",
        consentement_whatsapp=False,
    )
    ouvrir_une_session(client, ADMINISTRATION)

    passage = _publier(client)
    assert passage["echecs"] == 0, passage
    assert len(_accuses("arrivee4@exemple.cm")) == 1


def test_l_accuse_ne_depend_pas_de_l_affectation(plateforme):
    client = plateforme
    _deposer(client, "699330055", courriel="arrivee5@exemple.cm")
    ouvrir_une_session(client, ADMINISTRATION)
    _publier(client)

    (accuse,) = _accuses("arrivee5@exemple.cm")
    # ⚠️ L'accusé ne nomme PERSONNE, et c'est délibéré. Un dossier que l'annuaire
    # n'a pas su affecter reçoit quand même sa confirmation ; c'est la veille qui
    # remonte l'absence de responsable, pas le silence fait à la cliente.
    assert "responsable" not in accuse.contexte
    # ⚠️ ET IL NE PROMET AUCUN DÉLAI. Le référentiel porte les délais de veille —
    # ce qu'on se donne avant de s'alarmer — et non un engagement pris au client.
    ecrit = " ".join(str(v) for v in accuse.contexte.values())
    assert "heures" not in ecrit and "jours" not in ecrit
