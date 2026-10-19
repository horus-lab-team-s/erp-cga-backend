"""Un sceau sur une référence, que deux contextes emploient sans se connaître.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE EST ICI, ET NON DANS UN CONTEXTE

La fondatrice suit sa création par un **lien signé, sans compte** : le cabinet le
lui envoie, elle clique, elle dépose ses pièces. Deux contextes interviennent :

* **Création d'entreprise** émet le lien et sait ce que la référence désigne ;
* **Collecte** range le fichier — c'est son magasin, et `PieceConstitution`
  le dit depuis toujours : « empreinte du fichier au magasin de C · Collecte ».

Or le graphe des dépendances n'autorise la Création qu'à voir le Portefeuille et
la Conformité. Lui ouvrir la Collecte, ou l'inverse, se ferait en une ligne — et
c'est exactement ce que ce graphe existe pour empêcher : une arête ajoutée par
commodité ne se retire jamais.

⚠️ **CE MODULE NE DÉPEND DE RIEN**, comme ses voisins de `partage/`. Il ne connaît
ni dossier, ni pièce, ni magasin : il sait sceller une chaîne et vérifier un
sceau. Les deux contextes s'en servent chacun de leur côté, et ne se voient
toujours pas.

CE QUE LE SCEAU PROUVE, ET CE QU'IL NE PROUVE PAS

Il prouve que **le cabinet a émis ce lien** : nul ne peut le calculer sans la clé.
Il ne prouve **pas** qui le présente. C'est la même garantie que le lien
d'acceptation d'une proforma, et le même arbitrage : demander un mot de passe
pour envoyer la photo d'une carte d'identité ferait abandonner la moitié des
dossiers.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import hashlib
import hmac

__all__ = ["SceauInvalide", "cle_d_usage", "exiger_le_sceau", "sceller", "valide"]


class SceauInvalide(ValueError):
    """Le sceau présenté ne correspond pas à la référence."""


def cle_d_usage(maitresse: str, usage: str) -> bytes:
    """La clé d'un seul usage, dérivée de la clé du cabinet (19 octobre 2026).

    ⚠️ La même clé servait à CHIFFRER les secrets du second facteur (le coffre) et à
    SIGNER les liens de suivi et d'acceptation. Une clé n'a qu'un emploi : un défaut
    dans l'un, ou une valeur qu'il laisse fuiter, ne doit rien apprendre de l'autre.
    Chaque usage reçoit donc sa propre clé, dérivée par HMAC-SHA256 sur un libellé
    fixe (l'étape « expand » de HKDF, la clé du cabinet étant déjà uniforme).

    ⚠️ Le libellé ne change jamais : le changer invaliderait tous les liens émis.
    """
    return hmac.new(maitresse.encode(), f"cga/sceau/{usage}".encode(), hashlib.sha256).digest()


def sceller(reference: str, *, secret: str) -> str:
    """Le sceau d'une référence.

    ⚠️ **Sur la référence seule.** Le lien d'acceptation d'une proforma scelle
    aussi la version, parce qu'une proforma corrigée est un autre engagement. Un
    dossier qui avance reste le même dossier : y sceller l'étape invaliderait le
    lien à chaque jalon franchi, c'est-à-dire précisément quand son porteur a une
    raison de revenir.
    """
    cle = cle_d_usage(secret, "lien-de-suivi")
    return hmac.new(cle, reference.encode(), hashlib.sha256).hexdigest()


def valide(reference: str, sceau: str, *, secret: str) -> bool:
    """⚠️ `compare_digest`, jamais `==`.

    Une comparaison ordinaire s'arrête au premier octet différent, et le temps
    qu'elle met trahit combien d'octets étaient justes. Sur un sceau, cela se
    remonte octet par octet.
    """
    return bool(sceau) and hmac.compare_digest(sceau, sceller(reference, secret=secret))


def exiger_le_sceau(reference: str, sceau: str, *, secret: str) -> None:
    """Lève `SceauInvalide` si le sceau ne vaut pas.

    ⚠️ **LE NOM EST LONG EXPRÈS.** Il s'est d'abord appelé `exiger`, comme la
    garde de permission du Transverse — et le cas qui tient close la liste des
    routes publiques s'y est trompé : il a cru la route gardée parce qu'elle
    appelait « exiger ». Un lecteur pressé s'y serait trompé de même, et aurait
    cru qu'un sceau vaut une habilitation. Il ne vaut pas : il prouve que le
    cabinet a émis un lien, jamais qui le présente.

    ⚠️ **L'appelant doit rendre le MÊME refus qu'un objet inconnu.** Distinguer
    « référence inconnue » de « sceau invalide » permettrait d'énumérer les
    références en lisant les refus. C'est le raisonnement déjà tenu par la
    consultation de proforma et par la résolution des locataires.
    """
    if not valide(reference, sceau, secret=secret):
        raise SceauInvalide(
            "Ce lien ne s'ouvre pas. Vérifiez que vous l'avez copié en entier, "
            "ou demandez-en un nouveau au cabinet."
        )
