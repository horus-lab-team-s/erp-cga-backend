"""Le reçu de chaque versement, envoyé par courriel avec son PDF (13 novembre 2026).

Constaté en recette en ligne : la cliente payait une tranche par mobile money et ne recevait
rien. Voir `abonne_recu_de_versement.py` et `NOM_VERSEMENT_RECU` dans `versements.py`.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from email.message import EmailMessage

import httpx
import pytest

from app.contextes.souscription.adaptateurs.entrant import abonne_recu_de_versement as recu
from app.contextes.souscription.application import versements as v
from app.contextes.souscription.application.versements import NOM_VERSEMENT_RECU
from app.contextes.souscription.domaine.demande_de_contact import (
    Canal,
    Consentement,
    DemandeDeContact,
)
from app.contextes.souscription.domaine.dossier_commercial import ouvrir_un_dossier
from app.contextes.souscription.domaine.honoraires import CanalDeReglement
from app.contextes.souscription.domaine.versements_du_parcours import (
    REGLE_SANS_FRAIS,
    NatureVersement,
)
from app.contextes.transverse.adaptateurs.sortant import transport_brevo
from app.contextes.transverse.adaptateurs.sortant.notifications_memoire import (
    ServiceNotificationMemoire,
)
from app.orchestration.boite_d_envoi import EvenementSortant

CHARGE = {
    "identifiant": "ver-e7f8b85a6ec64ca3",
    "dossier": "dos-c54162b1905949bb",
    "nature": "TRANCHE",
    "montant": "353500",
    "canal": "MOBILE_MONEY",
    "reference_externe": "paiement:pay-8722ac45f5d24068",
    "proforma": "PRO-2026-0001",
    "constate_le": "2026-11-13T14:27:58",
    "nom": "Awa NDONGO",
    "courriel": "fondatrice@exemple.cm",
    "total": "505000",
    "paye": "353500",
    "reste": "151500",
    "solde": False,
}


def _evenement(**surcharge) -> EvenementSortant:
    charge = {**CHARGE, **surcharge}
    return EvenementSortant(
        identifiant=f"vr-{charge['identifiant']}",
        nom=NOM_VERSEMENT_RECU,
        cle=charge["identifiant"],
        charge=charge,
        depose_le=datetime(2026, 11, 13, 14, 27),
    )


class _Journal:
    def __init__(self) -> None:
        self.lignes: list[dict] = []

    def lister(self, *, objet_type: str, objet_id: str):
        return [
            x for x in self.lignes if x["objet_type"] == objet_type and x["objet_id"] == objet_id
        ]

    def ajouter(self, **ligne) -> None:
        self.lignes.append(ligne)


class _Boutique:
    def __init__(self) -> None:
        self.journal = _Journal()


def _unite(boutique: _Boutique):
    @contextmanager
    def unite(_locataire):
        yield boutique

    return unite


def _envoyer(poste, boutique=None, **surcharge):
    return recu.envoyer_le_recu(
        _evenement(**surcharge), unite=_unite(boutique or _Boutique()), notifier=poste
    )


def test_le_recu_part_une_fois_avec_son_pdf_et_la_situation():
    boutique, poste = _Boutique(), ServiceNotificationMemoire()
    assert _envoyer(poste, boutique) is True
    (message,) = poste.derniers("versement.recu")
    assert message.destinataire == "fondatrice@exemple.cm"
    assert message.contexte["prenom"] == "Awa NDONGO"
    assert message.contexte["montant"] == "353 500"
    assert "il reste 151 500 FCFA" in message.contexte["situation"]
    ((nom, octets, nature),) = message.contexte["pieces_jointes"]
    assert nom == "Recu-R-E7F8B85A6EC64CA3.pdf" and nature == "application/pdf"
    assert octets.startswith(b"%PDF")
    # Le relais garantit « au moins une fois » : le rejeu n'envoie rien de plus.
    assert _envoyer(poste, boutique) is False
    assert len(poste.derniers("versement.recu")) == 1


def test_sans_adresse_rien_ne_part_et_rien_ne_se_rejoue():
    poste = ServiceNotificationMemoire()
    assert _envoyer(poste, courriel="") is False
    assert poste.derniers("versement.recu") == []


def test_un_nom_absent_ne_laisse_pas_une_salutation_vide():
    poste = ServiceNotificationMemoire()
    _envoyer(poste, nom="")
    (message,) = poste.derniers("versement.recu")
    assert message.contexte["prenom"] == "Madame, Monsieur"


def test_un_poste_qui_refuse_fait_rejouer_l_evenement_sans_le_marquer():
    class _PosteEnPanne:
        def envoyer(self, *_a, **_k):
            return False

    boutique = _Boutique()
    with pytest.raises(recu.ReçuNonEnvoye):
        _envoyer(_PosteEnPanne(), boutique)
    assert boutique.journal.lignes == []


@pytest.mark.parametrize(
    ("surcharge", "attendu"),
    [
        ({"nature": "FRAIS_DE_TRAITEMENT", "total": "", "reste": "", "paye": "25000"}, "déduits"),
        ({"solde": True, "reste": "0"}, "entièrement réglée"),
        ({"nature": "TROP_PERCU"}, "vous la rendra"),
        ({"nature": "PENALITE_DE_RETARD"}, "ne s'impute pas"),
    ],
)
def test_la_situation_dit_ce_que_le_versement_change(surcharge, attendu):
    poste = ServiceNotificationMemoire()
    _envoyer(poste, **surcharge)
    (message,) = poste.derniers("versement.recu")
    assert attendu in message.contexte["situation"]


def _dossier():
    instant = datetime(2026, 11, 13, 9, 0)
    demande = DemandeDeContact(
        identifiant="dem-essai",
        deposee_le=instant,
        nom="Awa NDONGO",
        telephone="699112244",
        courriel="fondatrice@exemple.cm",
        service_souhaite="CREATION",
        canal_prefere=Canal.APPEL,
        consentement=Consentement(
            accorde=False, recueilli_le=instant, version_du_texte="consentement-whatsapp-v1"
        ),
    )
    return ouvrir_un_dossier("dos-essai", demande)


class _Boite:
    def __init__(self) -> None:
        self.evenements: list[EvenementSortant] = []

    def deposer(self, evenement) -> None:
        self.evenements.append(evenement)


class _Versements:
    def __init__(self) -> None:
        self.liste: list = []

    def pour(self, _dossier):
        return list(self.liste)

    def enregistrer(self, versement) -> None:
        self.liste.append(versement)


def test_recevoir_un_versement_publie_le_recu():
    """Le chemin réel : une somme constatée dépose `VersementReçu` dans la boîte d'envoi."""
    boite, dossier = _Boite(), _dossier()
    v.recevoir_un_versement(
        dossier=dossier,
        proforma=None,
        versements=_Versements(),
        regle=REGLE_SANS_FRAIS,
        nature=NatureVersement.PENALITE_DE_RETARD,
        montant=Decimal("10000"),
        canal=CanalDeReglement.GUICHET,
        reference_externe="Reçu de caisse 2026-0001",
        constate_par="caisse",
        a_l_instant=datetime(2026, 11, 13, 10, 0),
        boite=boite,
    )
    (evenement,) = [e for e in boite.evenements if e.nom == NOM_VERSEMENT_RECU]
    assert evenement.charge["nature"] == "PENALITE_DE_RETARD"
    assert evenement.charge["montant"] == "10000"
    assert evenement.charge["courriel"] == "fondatrice@exemple.cm"


def _message() -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = "Reçu"
    message["From"] = "noreply@cga-brcgroup.com"
    message["To"] = "a@b.cm"
    message.set_content("texte")
    return message


def _transport(brevo) -> transport_brevo.TransportBrevo:
    return transport_brevo.TransportBrevo(
        cle_api="k", client=httpx.Client(transport=httpx.MockTransport(brevo))
    )


def test_brevo_rejoue_un_delai_depasse_et_un_503_puis_remet(monkeypatch):
    """13 novembre 2026 : la proforma d'une cliente s'est perdue sur un `ReadTimeout`."""
    monkeypatch.setattr(transport_brevo, "_attendre", lambda _s: None)
    appels = []

    def brevo(requete: httpx.Request) -> httpx.Response:
        appels.append(requete)
        if len(appels) == 1:
            raise httpx.ReadTimeout("lent", request=requete)
        if len(appels) == 2:
            return httpx.Response(503, json={"message": "occupé"})
        return httpx.Response(201, json={"messageId": "<1@brevo>"})

    _transport(brevo).remettre(_message())
    assert len(appels) == 3


def test_brevo_ne_rejoue_pas_un_refus(monkeypatch):
    monkeypatch.setattr(transport_brevo, "_attendre", lambda _s: None)
    appels = []

    def brevo(requete):
        appels.append(requete)
        return httpx.Response(401, json={"message": "Key not found"})

    with pytest.raises(transport_brevo.EnvoiBrevoRefuse):
        _transport(brevo).remettre(_message())
    assert len(appels) == 1
