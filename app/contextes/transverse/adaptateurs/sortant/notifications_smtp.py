"""L'envoi réel des courriels transactionnels, par SMTP.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI SMTP ET NON UNE API DE PRESTATAIRE

Parce que SMTP marche partout. Brevo, Postmark, Amazon SES, un relais chez
l'hébergeur camerounais du cabinet : tous parlent SMTP, et changer de
prestataire devient un changement de variable d'environnement plutôt qu'un
changement d'adaptateur. Une API propriétaire ferait entrer un client HTTP, une
forme de charge utile et un mode d'authentification propres à un fournisseur,
pour livrer exactement le même message.

Ce qu'on y perd est réel et se retrouvera : les statistiques d'ouverture, les
retours de non-remise, la gestion des plaintes. Le jour où ils comptent, une
seconde réalisation du même port les apporte sans toucher aux appelants.

⚠️ LA DÉLIVRABILITÉ NE S'ACHÈTE PAS AVEC DU CODE

Un courriel d'activation qui atterrit en indésirable équivaut à un courriel non
envoyé, et rien ici ne peut le prévenir. Il y faut SPF, DKIM et DMARC publiés sur
le domaine du cabinet, et une adresse d'expédition sur ce même domaine. C'est une
tâche d'administration de zone DNS, pas de développement — elle est consignée
dans `Docs/architecture/09-questions-ouvertes.md`.

CE QUI NE DOIT JAMAIS ARRIVER, ET COMMENT C'EST TENU

**`envoyer` ne lève jamais.** Le pire scénario n'est pas « le message n'est pas
parti » — il se rejoue —, c'est « le paiement a été encaissé et le compte
n'existe pas ». Toute panne réseau, tout refus d'authentification, toute adresse
malformée se journalise et rend `False`.

**Aucun secret dans les traces.** Le lien d'activation *est* le secret : le
journal note le code, le destinataire et l'issue, jamais le contexte. Une trace
d'exécution qui contiendrait les liens d'activation vaudrait un vol de comptes.

**Un gabarit incomplet n'est pas envoyé.** Voir `gabarits_courriel`, règle 1.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any, Protocol

from app.contextes.transverse.adaptateurs.sortant.gabarits_courriel import (
    cles_manquantes,
    gabarit,
    rendre,
)

__all__ = ["ServiceNotificationSmtp", "Transport", "TransportSmtp"]

_journal = logging.getLogger("cga.courriel")

#: Le délai réseau, en secondes. Court volontairement : l'envoi se fait dans le
#: fil de la requête HTTP, et un relais injoignable ne doit pas y faire attendre
#: un adhérent qui vient de payer. L'échec est rattrapable, pas la minute perdue.
DELAI_RESEAU = 10.0

#: Le port de la remise implicitement chiffrée. Les autres (587, 25) commencent
#: en clair et se chiffrent par `STARTTLS`.
PORT_SSL_IMPLICITE = 465


class Transport(Protocol):
    """La remise d'un message construit.

    Extrait du service pour que les tests vérifient **ce qui part** sans ouvrir
    de connexion. Un adaptateur qui ne se teste qu'avec un vrai relais ne se
    teste pas.
    """

    def remettre(self, message: EmailMessage) -> None:
        """Remet le message. Lève en cas d'échec — le service rattrape."""
        ...


class TransportSmtp:
    """La remise réelle, par SMTP."""

    def __init__(
        self,
        *,
        hote: str,
        port: int,
        utilisateur: str = "",
        mot_de_passe: str = "",
        chiffrement: bool = True,
    ) -> None:
        self._hote = hote
        self._port = port
        self._utilisateur = utilisateur
        self._mot_de_passe = mot_de_passe
        self._chiffrement = chiffrement

    def remettre(self, message: EmailMessage) -> None:
        contexte = ssl.create_default_context()
        if self._port == PORT_SSL_IMPLICITE:
            with smtplib.SMTP_SSL(
                self._hote, self._port, timeout=DELAI_RESEAU, context=contexte
            ) as relais:
                self._authentifier_et_envoyer(relais, message)
            return

        with smtplib.SMTP(self._hote, self._port, timeout=DELAI_RESEAU) as relais:
            if self._chiffrement:
                # ⚠️ Sans cette ligne, l'authentification voyage en clair. Le
                # réglage existe parce qu'un relais local sans TLS est légitime
                # en développement — jamais en production, et la configuration
                # le refuse.
                relais.starttls(context=contexte)
            self._authentifier_et_envoyer(relais, message)

    def _authentifier_et_envoyer(
        self, relais: smtplib.SMTP, message: EmailMessage
    ) -> None:
        if self._utilisateur:
            relais.login(self._utilisateur, self._mot_de_passe)
        relais.send_message(message)


class ServiceNotificationSmtp:
    """Réalisation de `ServiceNotification` qui poste vraiment.

    `adresse_site` sert de préfixe aux liens relatifs : les appelants passent
    `/activation?jeton=…` et n'ont pas à connaître l'adresse publique du site,
    qui diffère entre développement, recette et production.
    """

    def __init__(
        self,
        *,
        transport: Transport,
        expediteur: str,
        repondre_a: str = "",
        adresse_site: str = "",
    ) -> None:
        self._transport = transport
        self._expediteur = expediteur
        self._repondre_a = repondre_a
        self._adresse_site = adresse_site.rstrip("/")

    def envoyer(
        self, code: str, *, destinataire: str, contexte: dict[str, Any]
    ) -> bool:
        """Rend `True` si le message est parti. Ne lève jamais — voir l'en-tête."""
        modele = gabarit(code)
        if modele is None:
            # Une faute de configuration, pas un incident d'exploitation : le
            # code appelé n'existe pas au catalogue. Le niveau `error` est
            # voulu, c'est un défaut à corriger.
            _journal.error("Gabarit de courriel introuvable : code=%r", code)
            return False

        enrichi = self._enrichir(contexte)
        manquantes = cles_manquantes(modele, enrichi)
        if manquantes:
            _journal.error(
                "Courriel %r non envoyé à %s : clés absentes du contexte %s",
                code,
                _masquer(destinataire),
                sorted(manquantes),
            )
            return False

        try:
            rendu = rendre(modele, enrichi)
            message = EmailMessage()
            message["Subject"] = rendu.objet
            message["From"] = self._expediteur
            message["To"] = destinataire
            if self._repondre_a:
                message["Reply-To"] = self._repondre_a
            # ⚠️ L'ordre compte : le corps texte d'abord, le HTML en variante.
            # Un client qui n'affiche pas le HTML lit le premier, et c'est aussi
            # ce que lisent les filtres anti-pourriel.
            message.set_content(rendu.texte)
            message.add_alternative(rendu.html, subtype="html")
            self._transport.remettre(message)
        except Exception:  # noqa: BLE001 — voir l'en-tête : ne jamais lever.
            _journal.exception(
                "Échec d'envoi du courriel %r à %s", code, _masquer(destinataire)
            )
            return False

        _journal.info("Courriel %r envoyé à %s", code, _masquer(destinataire))
        return True

    def _enrichir(self, contexte: dict[str, Any]) -> dict[str, Any]:
        """Complète le contexte de ce que l'appelant n'a pas à savoir.

        Un lien relatif devient absolu. Un lien déjà absolu passe intact : le
        contexte M construit le sien, parce qu'il connaît la page d'activation.
        """
        enrichi = dict(contexte)
        lien = enrichi.get("lien")
        if isinstance(lien, str) and lien.startswith("/") and self._adresse_site:
            enrichi["lien"] = f"{self._adresse_site}{lien}"
        return enrichi


def _masquer(adresse: str) -> str:
    """L'adresse réduite à ce qu'il faut pour diagnostiquer.

    Une trace d'exploitation conservée des mois n'a pas à constituer un fichier
    d'adresses exploitable. `m***@cga-brcg.cm` suffit à rapprocher un envoi d'une
    réclamation ; l'adresse entière est dans le compte.
    """
    nom, _, domaine = adresse.partition("@")
    if not domaine:
        return "adresse-invalide"
    return f"{nom[:1]}***@{domaine}"
