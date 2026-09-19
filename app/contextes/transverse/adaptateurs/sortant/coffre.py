"""Le chiffrement au repos des secrets qui doivent rester lisibles.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CHIFFRER LE SECRET TOTP ALORS QUE LES MOTS DE PASSE SONT HACHÉS

Parce que ce ne sont pas des données de même nature.

Un mot de passe n'a jamais besoin d'être relu : on vérifie qu'il correspond, et
Argon2id le hache **sans retour possible**. C'est plus solide que n'importe quel
chiffrement, et c'est le bon choix.

Un secret TOTP, lui, doit être **relu à chaque connexion** pour recalculer le
code à six chiffres attendu. Le hacher rendrait le second facteur inopérant. Il
reste donc en clair en base — et c'est précisément ce que ce module corrige.

CE QUE CELA CHANGE CONCRÈTEMENT

Un vidage de base qui fuite — sauvegarde égarée, accès en lecture d'un
prestataire, injection SQL — livre aujourd'hui **tous les secrets TOTP du
cabinet**. Quiconque les détient génère des codes valides indéfiniment : le
second facteur ne protège plus rien, et personne ne s'en aperçoit, puisque rien
ne change du point de vue des utilisateurs.

Les mots de passe, eux, resteraient hors d'atteinte. Chiffrer le secret TOTP
remet les deux facteurs au même niveau de résistance à une fuite de base.

⚠️ CE QUE CE MODULE NE PRÉTEND PAS FAIRE

Il protège contre une fuite **de la base**, pas contre une compromission du
serveur applicatif : la clé y est présente, par nécessité — il faut bien
déchiffrer pour vérifier un code. Un attaquant qui exécute du code dans le
processus lit tout, chiffré ou non.

C'est la limite de tout chiffrement au repos, et elle est acceptable : les
scénarios réalistes ici sont la sauvegarde égarée et l'accès en lecture, pas
l'exécution de code. ⚠️ Un module matériel de sécurité, seule réponse au second
scénario, ne se justifiera pas avant longtemps.

LE FORMAT, ET POURQUOI IL PORTE UN PRÉFIXE

    v1:<nonce base64><scellé base64>

Le préfixe permet trois choses qu'une chaîne nue interdirait :

* **reconnaître** une valeur chiffrée d'une valeur en clair, donc migrer une
  base existante sans script — voir `ouvrir` ;
* **changer d'algorithme** un jour en distinguant `v1` de `v2` ;
* **échouer bruyamment** plutôt que de rendre un déchiffrement silencieusement
  faux.

AES-GCM et non AES-CBC : GCM **authentifie**. Une valeur altérée en base est
rejetée au lieu de produire des octets quelconques qu'on prendrait pour un
secret — et un second facteur qui vérifie contre des octets quelconques refuse
tout le monde, sans que la cause soit visible nulle part.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

__all__ = ["Coffre", "CoffreAesGcm", "CoffreTransparent", "PREFIXE_V1", "coffre_depuis"]

_journal = logging.getLogger("cga.coffre")

PREFIXE_V1 = "v1:"

#: 96 bits, la taille recommandée pour GCM. Plus court affaiblit ; plus long
#: oblige la bibliothèque à un pré-calcul et n'apporte rien.
_TAILLE_NONCE = 12

#: AES-256. La clé se fournit en base64 dans `CGA_CLE_CHIFFREMENT`.
_TAILLE_CLE = 32


class Coffre(Protocol):
    """Sceller et ouvrir une valeur destinée à la base."""

    def sceller(self, valeur: str | None) -> str | None:
        """La valeur, prête pour la colonne. `None` passe intact."""
        ...

    def ouvrir(self, scelle: str | None) -> str | None:
        """La valeur relue. `None` passe intact."""
        ...


class CoffreTransparent:
    """Ne chiffre rien. Le mode de développement, et il doit se voir.

    ⚠️ La production ne peut pas démarrer sans clé — `Configuration` l'interdit.
    Ce coffre-ci ne peut donc pas y être choisi par inadvertance.
    """

    def sceller(self, valeur: str | None) -> str | None:
        return valeur

    def ouvrir(self, scelle: str | None) -> str | None:
        return scelle


class CoffreAesGcm:
    """Chiffrement authentifié AES-256-GCM."""

    def __init__(self, cle: bytes) -> None:
        if len(cle) != _TAILLE_CLE:
            raise ValueError(
                f"La clé de chiffrement fait {len(cle)} octets, "
                f"{_TAILLE_CLE} attendus. "
                "Produisez-en une avec : "
                "python -c \"import base64,os; print(base64.b64encode(os.urandom(32)).decode())\""
            )
        self._aes = AESGCM(cle)

    def sceller(self, valeur: str | None) -> str | None:
        if valeur is None:
            return None
        # ⚠️ Un nonce neuf à **chaque** scellement. Le réemployer avec la même
        # clé casse GCM complètement — ce n'est pas une dégradation, c'est une
        # perte totale de confidentialité et d'authenticité.
        nonce = os.urandom(_TAILLE_NONCE)
        scelle = self._aes.encrypt(nonce, valeur.encode("utf-8"), None)
        return PREFIXE_V1 + base64.b64encode(nonce + scelle).decode("ascii")

    def ouvrir(self, scelle: str | None) -> str | None:
        if scelle is None:
            return None
        if not scelle.startswith(PREFIXE_V1):
            # Une valeur écrite avant l'activation du chiffrement. On la rend
            # telle quelle : le second facteur continue de fonctionner, et la
            # valeur sera scellée à la prochaine écriture du compte.
            #
            # ⚠️ Migration silencieuse et **partielle** : un compte dont le
            # second facteur n'est jamais modifié garde son secret en clair
            # indéfiniment. La consigne d'exploitation qui l'accompagne est
            # dans `Docs/architecture/05-securite-multitenant.md`.
            _journal.warning(
                "Secret lu en clair : ce compte précède l'activation du coffre."
            )
            return scelle

        brut = base64.b64decode(scelle[len(PREFIXE_V1) :])
        nonce, corps = brut[:_TAILLE_NONCE], brut[_TAILLE_NONCE:]
        try:
            return self._aes.decrypt(nonce, corps, None).decode("utf-8")
        except InvalidTag as altere:
            # ⚠️ On lève. La tentation serait de rendre `None` pour « ne pas
            # casser la connexion » — ce serait désactiver le second facteur
            # d'un compte parce que sa donnée est corrompue ou que la clé a
            # changé. Un refus visible vaut mieux qu'une protection qui
            # s'évapore.
            raise ValueError(
                "Secret indéchiffrable : clé de chiffrement erronée, ou valeur "
                "altérée en base. Le second facteur de ce compte est "
                "inutilisable tant que ce n'est pas résolu."
            ) from altere


def coffre_depuis(cle_base64: str) -> Coffre:
    """Le coffre correspondant à la clé configurée, transparent si elle est vide."""
    if not cle_base64:
        return CoffreTransparent()
    try:
        cle = base64.b64decode(cle_base64, validate=True)
    except Exception as invalide:  # noqa: BLE001 — le message compte plus que le type.
        raise ValueError(
            "CGA_CLE_CHIFFREMENT n'est pas du base64 valide. Produisez-en une "
            'avec : python -c "import base64,os; '
            'print(base64.b64encode(os.urandom(32)).decode())"'
        ) from invalide
    return CoffreAesGcm(cle)
