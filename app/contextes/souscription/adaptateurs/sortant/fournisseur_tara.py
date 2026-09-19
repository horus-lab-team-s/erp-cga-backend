"""Tara Money — portage du fournisseur éprouvé du module `mail+paiement/`.

─────────────────────────────────────────────────────────────────────────────────
CE QUI EST REPRIS À L'IDENTIQUE, ET POURQUOI ON N'Y TOUCHE PAS

Le module d'origine est en Django ; celui-ci est en Python nu derrière un port.
Ce qui est **littéralement conservé** est tout ce qui relève du contrat avec
Tara, parce que chacune de ces lignes a été payée d'un incident :

* l'adresse de base est **`www.dklo.co`**, et non `taramoney.com` — la
  documentation développeur renvoie sur le premier, le second est le site
  vitrine ;
* `productPrice` est un **entier**, pas une chaîne ;
* `phoneNumber` s'écrit **`2376xxxxxxx`**, sans « + » ni espace ;
* `network` est envoyé **vide** : Tara déduit l'opérateur du préfixe, et le lui
  imposer conduit à router vers le mauvais réseau — décision client du
  25 juin 2026 ;
* le chemin du décaissement s'écrit **`paypout`**, avec cette faute de frappe :
  c'est le chemin réel de leur interface ;
* Tara répond parfois **200 avec un statut d'erreur** dans le corps ; il faut le
  lire, sans quoi une erreur métier passe pour un succès.

CE QUE TARA NE RENVOIE PAS, ET IL FAUT LE SAVOIR

**Pas d'identifiant de transaction à l'initiation.** Au Cameroun, la réponse est
`{status, message, vendor}` — rien d'autre. Tara pousse un menu USSD sur le
téléphone de l'abonné, qui valide avec son code. C'est pourquoi notre propre clé
d'idempotence sert de référence tant que la notification n'est pas arrivée.

**Pas de signature sur les notifications.** Ni HMAC, ni en-tête de signature, ni
secret partagé. La sécurité repose sur HTTPS, sur le fait que l'adresse de rappel
n'est connue que de Tara, et sur la vérification de l'identifiant marchand. C'est
peu, c'est ce qui existe, et c'est la raison pour laquelle un statut « payé » ne
se déduit jamais d'un appel entrant seul.

**Pas de montant dans la notification.** Le contrôle de cohérence du montant ne
peut donc pas s'exercer sur ce chemin-là. Il s'exerce sur l'interrogation de
statut quand elle rend un montant. ⚠️ C'est une faiblesse réelle : une
notification forgée par quelqu'un qui aurait deviné l'adresse de rappel et
l'identifiant marchand validerait le paiement sans qu'aucun montant ne soit
confronté. Le filet est la réconciliation, qui interroge Tara par un appel
sortant.

`collectionId` N'EST PAS UNE CLÉ DE RAPPROCHEMENT

C'est l'identifiant interne de Tara. Le nôtre est `productId`. Le repli sur
`collectionId` existe pour les anciennes notifications qui ne propageaient pas
`productId`, et il est conservé pour cette seule raison.

LE MODE SIMULÉ N'EST PAS UN JOUET

Sans identifiants configurés, aucun appel réseau n'est émis et une référence
factice est rendue. C'est le mode de développement du module d'origine, et il
**ne valide jamais un paiement tout seul** : la confirmation passe par une route
de simulation explicite. Un mode simulé qui validerait automatiquement finirait
un jour en production.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

from app.contextes.souscription.domaine.paiements import EvenementPaiement

__all__ = [
    "ADRESSE_TARA",
    "ErreurFournisseur",
    "FournisseurTara",
    "Initiation",
]

journal = logging.getLogger(__name__)

#: Voir l'en-tête : `dklo.co`, et non `taramoney.com`.
ADRESSE_TARA = "https://www.dklo.co"

DELAI_RESEAU = 15.0

#: Le vocabulaire de Tara, traduit une fois. Le domaine ne connaît ni
#: « SUCCESS », ni « VALIDATED », ni les six variantes qui coexistent selon les
#: versions de leur interface.
_REUSSITE: frozenset[str] = frozenset({"SUCCESS", "VALIDATED", "COMPLETED", "PAID"})
_ATTENTE: frozenset[str] = frozenset({"PENDING", "INITIATED"})
_ECHEC: frozenset[str] = frozenset({"FAILED", "REJECTED", "CANCELLED", "ERROR"})


class ErreurFournisseur(RuntimeError):
    """Le prestataire n'a pas répondu, ou a répondu une erreur.

    `reessayable` distingue ce qui vaut la peine d'être retenté — un délai
    dépassé, une panne — de ce qui ne le vaut pas — des identifiants absents,
    une requête refusée. Sans cette distinction, une réconciliation s'acharnerait
    sur des opérations définitivement perdues.
    """

    def __init__(self, message: str, *, reessayable: bool = False) -> None:
        super().__init__(message)
        self.reessayable = reessayable


@dataclass(frozen=True)
class Initiation:
    """Réalisation de `InitiationPaiement`."""

    accepte: bool
    reference_externe: str | None = None
    message: str | None = None
    #: L'adresse à ouvrir dans un navigateur. Renseignée pour Wave uniquement
    #: (Sénégal, Burkina, Côte d'Ivoire). Nulle pour MTN et Orange Cameroun, où
    #: le menu USSD arrive directement sur le téléphone.
    adresse_paiement: str | None = None
    brut: dict[str, Any] = field(default_factory=dict)


class FournisseurTara:
    """Réalisation de `FournisseurPaiement`."""

    def __init__(
        self,
        *,
        cle_api: str = "",
        identifiant_marchand_tara: str = "",
        adresse_publique: str = "",
        adresse_base: str = ADRESSE_TARA,
        client: httpx.Client | None = None,
    ) -> None:
        self._cle = cle_api
        self._marchand = identifiant_marchand_tara
        self._adresse_publique = adresse_publique.rstrip("/")
        self._base = adresse_base.rstrip("/")
        self._client = client

    @property
    def simule(self) -> bool:
        """Aucun identifiant : on n'appelle rien. Voir l'en-tête."""
        return not (self._cle and self._marchand)

    def identifiant_marchand(self) -> str | None:
        return self._marchand or None

    # ── Encaissement ────────────────────────────────────────────────────────

    def initier(
        self,
        *,
        montant: Decimal,
        telephone: str,
        cle_idempotence: str,
        libelle: str,
    ) -> Initiation:
        if self.simule:
            journal.warning(
                "Tara en mode simulé (aucune clé configurée) — aucun appel réseau, "
                "référence factice rendue. La confirmation passe par la route de "
                "simulation."
            )
            return Initiation(
                accepte=True,
                reference_externe=f"SIMULE-{cle_idempotence}",
                message="mode simulé : aucun débit réel n'a été demandé",
                brut={"simule": True},
            )

        charge = {
            "apiKey": self._cle,
            "businessId": self._marchand,
            "productId": cle_idempotence,
            "productName": libelle,
            # Entier, pas chaîne — voir l'en-tête.
            "productPrice": int(montant),
            "phoneNumber": _format_tara(telephone),
            "webHookUrl": self.adresse_de_rappel(),
            # Vide délibérément : Tara déduit l'opérateur du préfixe.
            "network": "",
            "productDescription": f"{libelle} — CGA Broad Range Consulting Group",
            "returnUrl": f"{self._adresse_publique}/souscription/retour",
        }
        try:
            reponse = self._poster("/api/tara/mobilepay", charge)
        except ErreurFournisseur as echec:
            return Initiation(accepte=False, message=str(echec))

        return Initiation(
            accepte=True,
            # Tara ne rend aucun identifiant de transaction à l'initiation au
            # Cameroun : notre propre clé fait office de référence jusqu'à la
            # notification. Voir l'en-tête.
            reference_externe=cle_idempotence,
            adresse_paiement=reponse.get("authUrl"),
            message=reponse.get("message"),
            brut=reponse,
        )

    def interroger_statut(
        self, *, cle_idempotence: str, reference_externe: str | None
    ) -> EvenementPaiement | None:
        """Appel **sortant**. C'est lui qui fait foi.

        Rend `None` quand Tara ne sait pas encore, ou quand l'appel échoue : dans
        les deux cas il n'y a rien à conclure, et conclure serait inventer.
        """
        if self.simule:
            return None
        charge = {
            "apiKey": self._cle,
            "businessId": self._marchand,
            "productId": cle_idempotence,
        }
        try:
            reponse = self._poster("/api/tara/transactions/status", charge)
        except ErreurFournisseur as echec:
            journal.warning("interrogation de statut impossible : %s", echec)
            return None

        statut = str(reponse.get("status") or "").upper()
        if statut in _ATTENTE or statut not in (_REUSSITE | _ECHEC):
            return None
        return EvenementPaiement(
            identifiant_produit=cle_idempotence,
            reference_externe=str(reponse.get("paymentId") or "") or reference_externe,
            reussi=statut in _REUSSITE,
            montant=_montant(reponse),
            telephone=reponse.get("phoneNumber"),
            identifiant_marchand=reponse.get("businessId"),
            motif=reponse.get("message"),
        )

    # ── Notification entrante ───────────────────────────────────────────────

    def lire_notification(self, charge_utile: dict[str, Any]) -> EvenementPaiement | None:
        """Traduit la charge utile de Tara. Rend `None` si elle est inexploitable.

        Ne lève jamais : une notification illisible ne doit pas produire d'erreur,
        qui déclencherait des renvois en boucle.
        """
        if not isinstance(charge_utile, dict):
            return None

        statut = str(charge_utile.get("status") or "").upper()
        if statut in _ATTENTE:
            # Tara annonce une opération en cours. Rien à faire : ni valider, ni
            # rejeter. Le silence est la bonne réponse.
            return None
        if statut not in (_REUSSITE | _ECHEC):
            return None

        # `productId` est notre clé. `collectionId` n'est qu'un repli pour les
        # anciennes notifications — voir l'en-tête.
        identifiant = charge_utile.get("productId") or charge_utile.get("collectionId")
        return EvenementPaiement(
            identifiant_produit=str(identifiant) if identifiant else None,
            reference_externe=str(charge_utile.get("paymentId") or "") or None,
            reussi=statut in _REUSSITE,
            montant=_montant(charge_utile),
            telephone=charge_utile.get("phoneNumber"),
            identifiant_marchand=charge_utile.get("businessId"),
            motif=charge_utile.get("message"),
        )

    def adresse_de_rappel(self) -> str:
        return f"{self._adresse_publique}/souscription/notification/tara"

    # ── Interne ─────────────────────────────────────────────────────────────

    def _poster(self, chemin: str, charge: dict[str, Any]) -> dict[str, Any]:
        client = self._client or httpx.Client(timeout=DELAI_RESEAU)
        try:
            reponse = client.post(f"{self._base}{chemin}", json=charge)
        except httpx.TimeoutException as echec:
            raise ErreurFournisseur(
                f"délai dépassé sur {chemin}", reessayable=True
            ) from echec
        except httpx.HTTPError as echec:
            raise ErreurFournisseur(
                f"erreur réseau sur {chemin} : {echec}", reessayable=True
            ) from echec
        finally:
            if self._client is None:
                client.close()

        if reponse.status_code >= 500:
            raise ErreurFournisseur(
                f"erreur serveur Tara sur {chemin} ({reponse.status_code})",
                reessayable=True,
            )
        try:
            donnees = reponse.json()
        except ValueError as echec:
            raise ErreurFournisseur(
                f"réponse non-JSON sur {chemin} : {reponse.text[:200]}"
            ) from echec

        if reponse.status_code >= 400:
            raise ErreurFournisseur(
                f"Tara refuse {chemin} : {donnees.get('message') or reponse.status_code}"
            )
        # Voir l'en-tête : Tara rend parfois 200 avec une erreur dans le corps.
        if str(donnees.get("status") or "").upper() in {"ERROR", "FAILED"}:
            raise ErreurFournisseur(
                f"Tara refuse l'opération : {donnees.get('message') or 'motif non précisé'}"
            )
        return donnees


def _format_tara(telephone: str) -> str:
    """`+237699112233` → `237699112233`. Tara n'accepte ni « + » ni espace."""
    return telephone.lstrip("+").replace(" ", "")


def _montant(charge: dict[str, Any]) -> Decimal | None:
    """Le montant, s'il figure — il est **généralement absent**. Voir l'en-tête."""
    for cle in ("amount", "productPrice", "montant"):
        valeur = charge.get(cle)
        if valeur is not None:
            try:
                return Decimal(str(valeur))
            except (ValueError, ArithmeticError):
                return None
    return None
