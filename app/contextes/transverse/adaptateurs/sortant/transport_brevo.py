"""La remise des courriels par l'API transactionnelle de Brevo (8 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI UN SECOND TRANSPORT

Sur le serveur de la cliente, le compte Brevo est celui de Gathe Finance, qui n'en
utilise que la CLÉ D'API (`xkeysib-…`). Le relais SMTP exige une autre clé (`xsmtpsib-…`)
et l'identifiant SMTP, qui n'existent pas sur ce serveur. Décision de l'exploitant : CGA
réutilise la clé d'API. Le service (`ServiceNotificationSmtp`) ne change pas : il construit
le même message, et seul le transport — la remise — change. Ce que `notifications_smtp`
annonçait : « une seconde réalisation du même port, sans toucher aux appelants ».

CE QUI DIFFÈRE DE SMTP, ET COMMENT C'EST TENU

* **Le logo.** L'API ne range pas une image `cid:` avec le corps. Le `cid:` est remplacé
  par l'adresse publique du logo sur le site (`/marque/cga-logo-couleur.jpg`, le même
  fichier), et la partie image n'est pas envoyée. Sans adresse de logo, l'image est retirée
  plutôt que laissée cassée.
* **Les pièces jointes** partent en base64 (`attachment`), sous leur nom de fichier.
* **L'expéditeur** doit être une adresse d'un domaine authentifié dans ce compte Brevo
  (`cga-brcgroup.com`) : sinon Brevo refuse, et ce refus se lit dans le journal.

⚠️ `remettre` LÈVE en cas d'échec, comme `TransportSmtp` : c'est le service qui rattrape et
journalise (« envoyer ne lève jamais »). La clé n'apparaît dans aucun message d'erreur.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import base64
import logging
import re
import time
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from typing import Any

import httpx

from app.contextes.transverse.adaptateurs.sortant.gabarits_courriel import CID_DU_LOGO

__all__ = ["ADRESSE_API_BREVO", "EnvoiBrevoRefuse", "TransportBrevo", "charge_brevo"]

ADRESSE_API_BREVO = "https://api.brevo.com/v3/smtp/email"

#: ⚠️ 13 novembre 2026 : 10 s ne suffisaient pas. En recette en ligne, la proforma d'une
#: cliente (PDF joint) est restée sans réponse de Brevo au-delà de 10 s, et le courriel
#: s'est perdu (`ReadTimeout`). 30 s laissent passer un envoi lent avec pièce jointe.
DELAI_RESEAU = 30.0

#: Les essais d'une remise : le premier, puis deux nouveaux en cas d'incident passager
#: (coupure réseau, délai dépassé, 429 ou 5xx de Brevo). Un refus 4xx (expéditeur non
#: validé, clé refusée) ne se rejoue pas : il ne changera pas d'avis.
ESSAIS = 3
PAUSES_ENTRE_ESSAIS = (2.0, 5.0)
_journal = logging.getLogger(__name__)
#: Remplaçable par les tests, qui n'attendent pas.
_attendre = time.sleep

#: Le logo tel que le site le sert (`erp-cga-vitrine/public/marque/`).
CHEMIN_DU_LOGO = "/marque/cga-logo-couleur.jpg"

_IMAGE_DU_LOGO = re.compile(r"<img\b[^>]*\bsrc=\"cid:" + re.escape(CID_DU_LOGO) + r"\"[^>]*>")


class EnvoiBrevoRefuse(RuntimeError):
    """Brevo a refusé le message (statut ≥ 400)."""


def _adresse(valeur: str) -> dict[str, str]:
    nom, courriel = parseaddr(valeur)
    adresse: dict[str, str] = {"email": courriel}
    if nom:
        adresse["name"] = nom
    return adresse


def charge_brevo(message: EmailMessage, *, adresse_du_logo: str = "") -> dict[str, Any]:
    """Le message construit par le service, dans la forme de l'API de Brevo."""
    charge: dict[str, Any] = {
        "sender": _adresse(str(message["From"])),
        "to": [
            {"email": courriel, **({"name": nom} if nom else {})}
            for nom, courriel in getaddresses([str(message["To"])])
        ],
        "subject": str(message["Subject"]),
    }
    if message["Reply-To"]:
        charge["replyTo"] = _adresse(str(message["Reply-To"]))

    texte = message.get_body(preferencelist=("plain",))
    if texte is not None:
        charge["textContent"] = texte.get_content()
    corps_html = message.get_body(preferencelist=("html",))
    if corps_html is not None:
        html = corps_html.get_content()
        if adresse_du_logo:
            html = html.replace(f"cid:{CID_DU_LOGO}", adresse_du_logo)
        else:
            html = _IMAGE_DU_LOGO.sub("", html)
        charge["htmlContent"] = html

    pieces = [
        {
            "name": partie.get_filename(),
            "content": base64.b64encode(partie.get_content()).decode("ascii"),
        }
        for partie in message.iter_attachments()
        if partie.get_filename()
    ]
    if pieces:
        charge["attachment"] = pieces
    return charge


class TransportBrevo:
    """La remise par l'API de Brevo. `client` n'est passé que par les tests."""

    def __init__(
        self,
        *,
        cle_api: str,
        adresse_du_logo: str = "",
        client: httpx.Client | None = None,
    ) -> None:
        self._cle_api = cle_api
        self._adresse_du_logo = adresse_du_logo
        self._client = client

    def remettre(self, message: EmailMessage) -> None:
        charge = charge_brevo(message, adresse_du_logo=self._adresse_du_logo)
        entetes = {"api-key": self._cle_api, "accept": "application/json"}
        reponse = None
        for essai in range(1, ESSAIS + 1):
            try:
                if self._client is not None:
                    reponse = self._client.post(ADRESSE_API_BREVO, json=charge, headers=entetes)
                else:
                    with httpx.Client(timeout=DELAI_RESEAU) as client:
                        reponse = client.post(ADRESSE_API_BREVO, json=charge, headers=entetes)
            except httpx.TransportError as incident:
                # ⚠️ Un délai dépassé à la lecture peut cacher un message déjà accepté :
                # le rejouer risque un doublon. C'est le moindre mal face à une proforma
                # ou un reçu perdus sans que personne ne le sache.
                if essai == ESSAIS:
                    raise
                _journal.warning("Brevo injoignable (%s), essai %s sur %s", incident, essai, ESSAIS)
                _attendre(PAUSES_ENTRE_ESSAIS[min(essai, len(PAUSES_ENTRE_ESSAIS)) - 1])
                continue
            if (reponse.status_code == 429 or reponse.status_code >= 500) and essai < ESSAIS:
                _journal.warning("Brevo %s, essai %s sur %s", reponse.status_code, essai, ESSAIS)
                _attendre(PAUSES_ENTRE_ESSAIS[min(essai, len(PAUSES_ENTRE_ESSAIS)) - 1])
                continue
            break
        assert reponse is not None
        if reponse.status_code >= 400:
            # Le corps de la réponse dit pourquoi (expéditeur non validé, clé refusée…) ;
            # il ne contient ni la clé ni le message.
            raise EnvoiBrevoRefuse(f"Brevo {reponse.status_code} : {reponse.text[:300]}")
