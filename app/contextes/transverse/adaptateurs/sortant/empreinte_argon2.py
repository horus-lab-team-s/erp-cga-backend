"""Dérivation du mot de passe par Argon2id.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI PAS UN SHA-256, ET POURQUOI PAS UN SEL MAISON

Un SHA-256 est conçu pour être **rapide** : c'est sa qualité pour signer un
fichier, et c'est exactement le défaut recherché par celui qui essaie des
milliards de mots de passe par seconde sur une carte graphique. Une fonction de
dérivation est conçue pour être **lente et gourmande en mémoire**, de sorte que
paralléliser coûte cher.

Argon2id est le lauréat du concours de 2015 et la recommandation courante. La
variante `id` combine la résistance aux attaques par canal auxiliaire d'Argon2i et
la résistance au compromis temps-mémoire d'Argon2d.

Le sel est engendré et stocké par la bibliothèque, dans la chaîne d'empreinte
elle-même. Écrire son propre sel est l'erreur classique : on le réutilise, ou on
le stocke ailleurs, et l'on perd la propriété qui compte — deux personnes ayant
choisi le même mot de passe n'ont pas la même empreinte.

LES PARAMÈTRES SONT DE L'INFRASTRUCTURE, ET ILS BOUGERONT

19 Mio de mémoire, deux passes, un fil. C'est le profil recommandé par l'OWASP
pour Argon2id, et il tient sur les machines modestes qu'on trouve en hébergement
local — considération qui n'est pas théorique ici.

Ces valeurs augmenteront avec le matériel. C'est pourquoi le port expose
`a_rederiver` : à chaque connexion réussie, le mot de passe est connu en clair
l'espace d'un instant, et c'est la seule occasion de le redériver avec des
paramètres durcis. Sans ce rattrapage, un renforcement ne profiterait qu'aux
comptes créés après.

AUCUN REPLI N'EST PRÉVU SI LA BIBLIOTHÈQUE MANQUE

L'import échoue, et l'application ne démarre pas. Un repli sur un algorithme plus
faible produirait un système qui fonctionne, qui paraît sûr, et qui ne l'est pas —
la pire des trois situations. Mieux vaut un démarrage qui refuse et se corrige en
une commande.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

try:  # argon2-cffi ≥ 23.1
    from argon2.exceptions import InvalidHashError
except ImportError:  # pragma: no cover — argon2-cffi < 23.1, encore courant
    # Le nom a changé entre les deux versions, la classe non. L'alias évite
    # d'imposer une montée de version pour une question d'orthographe.
    from argon2.exceptions import InvalidHash as InvalidHashError

__all__ = ["PROFIL_OWASP", "ServiceEmpreinteArgon2"]

#: Profil OWASP pour Argon2id — voir l'en-tête.
PROFIL_OWASP: dict[str, int] = {
    "time_cost": 2,
    "memory_cost": 19_456,
    "parallelism": 1,
    "hash_len": 32,
    "salt_len": 16,
}


class ServiceEmpreinteArgon2:
    """Réalisation de `ServiceEmpreinte`."""

    def __init__(self, **parametres: int) -> None:
        self._hacheur = PasswordHasher(**{**PROFIL_OWASP, **parametres})

    def deriver(self, mot_de_passe: str) -> str:
        return self._hacheur.hash(mot_de_passe)

    def verifier(self, mot_de_passe: str, empreinte: str) -> bool:
        """Rend `False` sur un échec, quelle qu'en soit la nature.

        `InvalidHashError` est traitée comme un échec ordinaire et non propagée :
        une empreinte corrompue en base ne doit pas produire une erreur 500 là où
        un mot de passe faux produit un refus. La différence se lirait dans les
        réponses, et distinguerait les comptes dont les données sont abîmées.
        """
        try:
            return self._hacheur.verify(empreinte, mot_de_passe)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def a_rederiver(self, empreinte: str) -> bool:
        try:
            return self._hacheur.check_needs_rehash(empreinte)
        except InvalidHashError:
            # Illisible : à redériver dès qu'on le pourra, c'est-à-dire à la
            # prochaine connexion réussie. Comme `verifier` rend `False`, cette
            # connexion n'aura pas lieu, et le compte devra passer par un lien de
            # réinitialisation — ce qui est le comportement correct.
            return True
