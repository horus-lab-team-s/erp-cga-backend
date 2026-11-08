"""La remise par l'API de Brevo (8 novembre 2026) : voir `transport_brevo.py`.

Le service est le même que pour SMTP ; ces cas vérifient ce que le transport poste
à Brevo, sans réseau (`httpx.MockTransport`).
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.contextes.transverse.adaptateurs.sortant.gabarits_courriel import CID_DU_LOGO
from app.contextes.transverse.adaptateurs.sortant.notifications_smtp import (
    ServiceNotificationSmtp,
)
from app.contextes.transverse.adaptateurs.sortant.transport_brevo import (
    ADRESSE_API_BREVO,
    EnvoiBrevoRefuse,
    TransportBrevo,
)
from app.partage.marque import logo_disponible
from tests.test_courriel import CONTEXTES_DES_APPELANTS

LOGO_PUBLIC = "https://cga-brcgroup.com/marque/cga-logo-couleur.jpg"


class _Brevo:
    """Retient les requêtes et répond le statut voulu."""

    def __init__(self, statut: int = 201) -> None:
        self.requetes: list[httpx.Request] = []
        self._statut = statut

    def __call__(self, requete: httpx.Request) -> httpx.Response:
        self.requetes.append(requete)
        if self._statut >= 400:
            refus = {"code": "unauthorized", "message": "Key not found"}
            return httpx.Response(self._statut, json=refus)
        return httpx.Response(self._statut, json={"messageId": "<1@smtp-relay.mailin.fr>"})

    @property
    def charge(self) -> dict:
        assert len(self.requetes) == 1
        return json.loads(self.requetes[0].content)


def _service(brevo: _Brevo, *, logo: str = LOGO_PUBLIC) -> ServiceNotificationSmtp:
    return ServiceNotificationSmtp(
        transport=TransportBrevo(
            cle_api="xkeysib-essai",
            adresse_du_logo=logo,
            client=httpx.Client(transport=httpx.MockTransport(brevo)),
        ),
        expediteur="CGA Broad Range <noreply@cga-brcgroup.com>",
        repondre_a="contact@cga-brcgroup.com",
        adresse_site="https://console.cga-brcgroup.com",
    )


def test_le_message_part_a_l_api_avec_la_cle_dans_l_entete():
    brevo = _Brevo()
    parti = _service(brevo).envoyer(
        "compte.invitation",
        destinataire="tchambaedwin@gmail.com",
        contexte={
            "prenom": "Edwin",
            "role": "Administrateur",
            "lien": "/activation?jeton=abc",
            "expire_le": "15/11/2026 à 10h00",
        },
    )
    assert parti is True
    requete = brevo.requetes[0]
    assert str(requete.url) == ADRESSE_API_BREVO
    assert requete.headers["api-key"] == "xkeysib-essai"
    charge = brevo.charge
    assert charge["sender"] == {"email": "noreply@cga-brcgroup.com", "name": "CGA Broad Range"}
    assert charge["to"] == [{"email": "tchambaedwin@gmail.com"}]
    assert charge["replyTo"] == {"email": "contact@cga-brcgroup.com"}
    assert charge["subject"]
    # Le lien relatif est devenu absolu, dans les deux corps.
    lien = "https://console.cga-brcgroup.com/activation?jeton=abc"
    assert lien in charge["textContent"]
    assert lien in charge["htmlContent"]
    assert "attachment" not in charge


@pytest.mark.skipif(not logo_disponible(), reason="logo absent de l'installation")
def test_le_logo_cid_devient_l_adresse_publique_du_logo():
    brevo = _Brevo()
    _service(brevo).envoyer(
        "compte.invitation",
        destinataire="a@b.cm",
        contexte={
            "prenom": "A",
            "role": "Comptable",
            "lien": "/activation?jeton=x",
            "expire_le": "15/11/2026 à 10h00",
        },
    )
    html = brevo.charge["htmlContent"]
    trouve_cid = f"cid:{CID_DU_LOGO}" in html
    trouve_url = LOGO_PUBLIC in html
    assert not trouve_cid
    assert trouve_url
    # L'image du logo n'est pas envoyée en pièce jointe.
    assert "attachment" not in brevo.charge


def test_sans_adresse_de_logo_l_image_est_retiree_plutot_que_cassee():
    brevo = _Brevo()
    _service(brevo, logo="").envoyer(
        "compte.invitation",
        destinataire="a@b.cm",
        contexte={
            "prenom": "A",
            "role": "Comptable",
            "lien": "/activation?jeton=x",
            "expire_le": "15/11/2026 à 10h00",
        },
    )
    trouve_cid = "cid:" in brevo.charge["htmlContent"]
    assert not trouve_cid


def test_les_pieces_jointes_partent_en_base64_sous_leur_nom():
    brevo = _Brevo()
    parti = _service(brevo).envoyer(
        "proforma.envoi.detaillee",
        destinataire="a@b.cm",
        contexte=CONTEXTES_DES_APPELANTS["proforma.envoi.detaillee"],
    )
    assert parti is True
    pieces = brevo.charge["attachment"]
    assert pieces == [
        {"name": "Proforma-PRO-2026-0001.pdf", "content": base64.b64encode(b"%PDF-1.4").decode()}
    ]


def test_un_refus_de_brevo_rend_false_sans_lever_ni_montrer_la_cle(caplog):
    brevo = _Brevo(statut=401)
    parti = _service(brevo).envoyer(
        "compte.invitation",
        destinataire="a@b.cm",
        contexte={
            "prenom": "A",
            "role": "Comptable",
            "lien": "/activation?jeton=secret123",
            "expire_le": "15/11/2026 à 10h00",
        },
    )
    assert parti is False
    journal = caplog.text
    assert "401" in journal
    fuite_cle = "xkeysib-essai" in journal
    fuite_lien = "secret123" in journal
    assert not fuite_cle
    assert not fuite_lien


def test_le_transport_leve_sur_un_refus():
    """C'est le service qui rattrape, comme pour SMTP."""
    from email.message import EmailMessage

    message = EmailMessage()
    message["From"] = "x@cga-brcgroup.com"
    message["To"] = "a@b.cm"
    message["Subject"] = "essai"
    message.set_content("bonjour")
    transport = TransportBrevo(
        cle_api="k", client=httpx.Client(transport=httpx.MockTransport(_Brevo(statut=400)))
    )
    with pytest.raises(EnvoiBrevoRefuse):
        transport.remettre(message)
