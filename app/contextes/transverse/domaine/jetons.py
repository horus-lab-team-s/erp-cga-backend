"""Le lien à usage unique : activation d'un compte, ou réinitialisation d'un mot de passe.

─────────────────────────────────────────────────────────────────────────────────
ON N'ENREGISTRE JAMAIS LE JETON, ON ENREGISTRE SON EMPREINTE

Le secret n'existe en clair qu'une fois : à l'instant où il est engendré, le temps
de le mettre dans le courriel. Ce qui est conservé est son SHA-256.

La raison tient en une phrase : **une copie de la base ne doit donner accès à
aucun compte.** Si les jetons y figuraient en clair, quiconque obtiendrait une
sauvegarde — un prestataire d'hébergement, un ancien administrateur, un fichier
oublié sur un poste — pourrait activer les comptes en attente et définir leurs
mots de passe. Il n'aurait même pas besoin de casser quoi que ce soit.

Un SHA-256 nu suffit ici, là où un mot de passe exige Argon2 : le jeton fait 256
bits d'entropie tirés au sort et vit quelques jours, il n'y a pas de dictionnaire
à lui opposer.

TROIS DURÉES, PARCE QUE TROIS RISQUES

* **Activation — 7 jours.** Le lien part quand le paiement est validé. Celui qui
  le reçoit vient de payer et n'ouvrira peut-être pas sa boîte avant le week-end.
  Deux heures ici produiraient un flot d'appels au cabinet et l'habitude de
  renvoyer des liens sans vérifier qui demande.

* **Réinitialisation — 2 heures.** C'est une surface d'attaque vivante : le lien
  permet de prendre un compte **déjà actif**. Il doit se périmer avant qu'une
  boîte compromise ne soit exploitée.

* **Invitation d'un collaborateur — 14 jours.** Elle est émise par un
  administrateur qui connaît personnellement le destinataire, souvent avant sa
  prise de poste.

L'USAGE UNIQUE EST UNE TRANSITION, PAS UN DRAPEAU QU'ON REGARDE

`consommer()` refuse un jeton déjà consommé. Se contenter de lire un booléen avant
d'agir laisserait la place à un rejeu : deux requêtes simultanées liraient toutes
deux « pas encore consommé ». Le refus appartient donc à l'entité, et le dépôt
n'a qu'à écrire le résultat.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field

__all__ = [
    "DUREES",
    "JETONS_DE_MOT_DE_PASSE",
    "Jeton",
    "JetonInvalide",
    "TypeJeton",
    "empreinte_de",
    "engendrer_secret",
]


class TypeJeton(StrEnum):
    ACTIVATION = "ACTIVATION"
    REINITIALISATION = "REINITIALISATION"
    INVITATION = "INVITATION"
    #: Le premier enrôlement du second facteur (pas 62). Voir `confirmer_enrolement`.
    ENROLEMENT = "ENROLEMENT"

#: Les jetons qui permettent de définir un mot de passe. **Pas l'enrôlement** : un
#: lien d'enrôlement qui servirait aussi à changer de mot de passe donnerait le compte
#: à qui ne devait recevoir qu'une clé.
JETONS_DE_MOT_DE_PASSE: frozenset[TypeJeton] = frozenset(
    {TypeJeton.ACTIVATION, TypeJeton.REINITIALISATION, TypeJeton.INVITATION}
)


#: Voir l'en-tête : trois durées, parce que trois risques.
DUREES: dict[TypeJeton, timedelta] = {
    TypeJeton.ACTIVATION: timedelta(days=7),
    TypeJeton.REINITIALISATION: timedelta(hours=2),
    TypeJeton.INVITATION: timedelta(days=14),
    #: Trente minutes : il est demandé depuis une session ouverte, par quelqu'un qui
    #: attend le courriel devant l'écran. Plus long, il resterait exploitable dans une
    #: boîte compromise bien après que son titulaire a renoncé.
    TypeJeton.ENROLEMENT: timedelta(minutes=30),
}


class JetonInvalide(RuntimeError):
    """Le lien ne vaut pas.

    ⚠️ Un seul message pour toutes les causes — inconnu, expiré, déjà consommé.
    Distinguer « ce lien a expiré » de « ce lien n'existe pas » apprendrait à qui
    essaie des jetons au hasard lesquels de ses essais valaient quelque chose. La
    cause précise est journalisée côté serveur, où elle sert au diagnostic sans
    renseigner l'attaquant.
    """


def engendrer_secret() -> str:
    """32 octets tirés du générateur cryptographique du système.

    `secrets`, jamais `random` : le second est reproductible à partir de sa graine,
    ce qui est exactement ce qu'on ne veut pas d'un lien d'activation.
    """
    return secrets.token_urlsafe(32)


def empreinte_de(secret: str) -> str:
    """SHA-256 hexadécimal. Voir l'en-tête pour le choix de l'algorithme."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class Jeton(BaseModel):
    """Un lien à usage unique, borné dans le temps."""

    model_config = ConfigDict(frozen=True)

    identifiant: str = Field(min_length=1)

    #: SHA-256 du secret. Le secret lui-même n'est jamais stocké.
    empreinte: str = Field(min_length=64, max_length=64)

    compte: str = Field(min_length=1)
    type: TypeJeton
    emis_le: datetime
    expire_le: datetime
    consomme_le: datetime | None = None

    #: Qui l'a émis. Pour une activation issue d'un paiement, c'est le système ;
    #: pour une invitation, l'administrateur qui l'a envoyée.
    emis_par: str = Field(min_length=1)

    @computed_field
    @property
    def consomme(self) -> bool:
        return self.consomme_le is not None

    def expire(self, a_l_instant: datetime) -> bool:
        return a_l_instant >= self.expire_le

    def utilisable(self, a_l_instant: datetime) -> bool:
        return not self.consomme and not self.expire(a_l_instant)

    def consommer(self, a_l_instant: datetime) -> Jeton:
        """Marque le jeton comme utilisé, ou lève.

        Le message porte la cause exacte : il est destiné au journal, pas à
        l'écran. Voir `JetonInvalide`.
        """
        if self.consomme:
            raise JetonInvalide(
                f"jeton {self.identifiant} déjà consommé le {self.consomme_le}"
            )
        if self.expire(a_l_instant):
            raise JetonInvalide(f"jeton {self.identifiant} expiré le {self.expire_le}")
        return self.model_copy(update={"consomme_le": a_l_instant})

    @classmethod
    def emettre(
        cls,
        *,
        identifiant: str,
        compte: str,
        type: TypeJeton,
        secret: str,
        a_l_instant: datetime,
        emis_par: str,
        duree: timedelta | None = None,
    ) -> Jeton:
        """Construit le jeton à partir du secret, qu'il ne conserve pas.

        L'appelant garde le secret le temps de composer le courriel, et le perd
        ensuite. C'est la seule fenêtre où il existe en clair.
        """
        return cls(
            identifiant=identifiant,
            empreinte=empreinte_de(secret),
            compte=compte,
            type=type,
            emis_le=a_l_instant,
            expire_le=a_l_instant + (duree or DUREES[type]),
            emis_par=emis_par,
        )
