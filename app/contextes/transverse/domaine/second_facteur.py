"""Le second facteur, par mot de passe à usage unique fondé sur le temps.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI IL FALLAIT LE FAIRE MAINTENANT

`EXIGE_MFA` déclare depuis le premier jour que **le dépôt d'une déclaration
réclame une authentification forte**. Tant qu'aucun second facteur n'existait,
l'action était refusée — blocage assumé, visible, et sans conséquence tant qu'il
n'y avait rien à déposer.

Il y a maintenant quelque chose à déposer. Le blocage cesse d'être une précaution
et devient l'obstacle. On lève donc l'obstacle plutôt que la garde.

POURQUOI TOTP, ET RIEN D'AUTRE

**Le SMS ne convient pas.** L'échange de carte SIM est le mode d'attaque le plus
courant sur les comptes protégés par SMS, et il est particulièrement praticable
là où l'identification à l'achat d'une puce est inégale. Il suppose en outre un
réseau et un fournisseur, donc un coût par envoi et une panne possible au pire
moment — la veille d'une échéance.

**TOTP fonctionne hors ligne.** Une application d'authentification sur le
téléphone du réviseur, aucun réseau, aucun tiers, aucun coût. C'est la norme
RFC 6238, implémentée par toutes les applications du marché.

**Rien n'est inventé ici.** L'algorithme est public et tient en quinze lignes :
HMAC-SHA1 sur un compteur de tranches de trente secondes, troncature dynamique,
six chiffres. Écrire soi-même de la cryptographie est une faute ; recopier une
norme dont chaque étape est spécifiée n'en est pas une, et évite une dépendance
de plus.

LE PIÈGE DU FUSEAU, ET IL EST RÉEL

`datetime.timestamp()` sur un `datetime` **naïf** suppose le fuseau local de la
machine. Le Cameroun étant à UTC+1, un serveur mal configuré produirait des codes
décalés de cent vingt tranches — jamais valides, sans le moindre message
d'erreur. Tous les horodatages du système étant naïfs et en UTC par convention,
on attache explicitement UTC avant de convertir.

LA TOLÉRANCE EST D'UNE TRANCHE, PAS DE TROIS

Une tranche avant, une après : le code reste valable environ une minute et demie.
Assez pour couvrir la dérive d'horloge d'un téléphone et le temps de le recopier ;
assez peu pour qu'un code intercepté ne serve plus quand il est employé.

⚠️ LE SECRET SE STOCKE EN CLAIR, ET C'EST INÉVITABLE

Contrairement à un mot de passe, un secret TOTP doit être **relu** pour recalculer
le code attendu : on ne peut pas en conserver seulement l'empreinte. Il doit donc
être chiffré au repos dans la base, avec une clé qui n'y figure pas. Ce n'est pas
fait — la persistance est en mémoire — et c'est écrit ici pour que la migration ne
l'oublie pas.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

__all__ = [
    "CHIFFRES",
    "DUREE_RENFORCEMENT",
    "PAS_SECONDES",
    "TOLERANCE_TRANCHES",
    "CodeInvalide",
    "code_attendu",
    "engendrer_secret_totp",
    "uri_provisionnement",
    "verifier_code",
]

#: Trente secondes, six chiffres : les valeurs par défaut de la RFC 6238, et
#: celles qu'attendent les applications d'authentification. En changer rendrait
#: le système incompatible avec les outils que les collaborateurs ont déjà.
PAS_SECONDES = 30
CHIFFRES = 6

#: Une tranche avant, une après — voir l'en-tête.
TOLERANCE_TRANCHES = 1

#: Combien de temps une session reste renforcée après présentation d'un code.
#:
#: Quinze minutes. Un réviseur qui dépose cinq déclarations à la suite ne ressort
#: pas son téléphone cinq fois ; quelqu'un qui s'éloigne de son poste ne laisse
#: pas une session capable de déposer pendant toute une journée.
DUREE_RENFORCEMENT = timedelta(minutes=15)


class CodeInvalide(ValueError):
    """Le code présenté ne correspond à aucune tranche acceptée.

    ⚠️ Message unique, comme pour l'authentification : dire « code expiré » plutôt
    que « code faux » apprendrait à celui qui essaie que sa méthode de génération
    est la bonne et qu'il lui manque seulement la synchronisation.
    """


def engendrer_secret_totp() -> str:
    """Vingt octets tirés du générateur cryptographique, en base 32.

    Vingt octets — cent soixante bits — est la taille recommandée par la RFC 4226
    pour HMAC-SHA1. La base 32 sans remplissage est ce que les applications
    d'authentification savent lire, et ce qui se recopie à la main sans confusion
    entre `0` et `O`.
    """
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _cle(secret: str) -> bytes:
    """Décode le secret en base 32, remplissage rétabli."""
    nettoye = secret.strip().replace(" ", "").upper()
    remplissage = "=" * ((8 - len(nettoye) % 8) % 8)
    try:
        return base64.b32decode(nettoye + remplissage)
    except Exception as illisible:  # noqa: BLE001 — base64 lève plusieurs types
        raise ValueError(
            "secret TOTP illisible : une chaîne en base 32 est attendue"
        ) from illisible


def _tranche(a_l_instant: datetime) -> int:
    """Le numéro de tranche de trente secondes depuis l'époque Unix.

    Voir l'en-tête : les horodatages du système sont naïfs et en UTC par
    convention ; on l'attache explicitement avant de convertir, sans quoi le
    fuseau de la machine s'inviterait dans le calcul.
    """
    return int(a_l_instant.replace(tzinfo=UTC).timestamp()) // PAS_SECONDES


def code_attendu(secret: str, a_l_instant: datetime, decalage: int = 0) -> str:
    """Le code de la tranche courante, décalée de `decalage` tranches.

    Troncature dynamique de la RFC 4226 : les quatre bits de poids faible du
    dernier octet désignent où lire les quatre octets qui portent le code, ce qui
    évite de toujours puiser au même endroit de l'empreinte.
    """
    compteur = struct.pack(">Q", _tranche(a_l_instant) + decalage)
    empreinte = hmac.new(_cle(secret), compteur, hashlib.sha1).digest()
    depart = empreinte[-1] & 0x0F
    tronque = struct.unpack(">I", empreinte[depart : depart + 4])[0] & 0x7FFF_FFFF
    return str(tronque % (10**CHIFFRES)).zfill(CHIFFRES)


def verifier_code(secret: str, code: str, a_l_instant: datetime) -> bool:
    """Vrai si le code vaut pour la tranche courante ou l'une des voisines.

    La comparaison passe par `hmac.compare_digest` : une comparaison de chaînes
    ordinaire s'arrête au premier caractère différent, et la durée de l'échec
    renseigne sur le nombre de caractères justes. Six chiffres se devineraient
    alors bien plus vite que par force brute.
    """
    presente = (code or "").strip().replace(" ", "")
    if len(presente) != CHIFFRES or not presente.isdigit():
        return False
    return any(
        hmac.compare_digest(presente, code_attendu(secret, a_l_instant, decalage))
        for decalage in range(-TOLERANCE_TRANCHES, TOLERANCE_TRANCHES + 1)
    )


def uri_provisionnement(secret: str, *, compte: str, emetteur: str) -> str:
    """L'adresse `otpauth://` que les applications d'authentification lisent.

    Elle se présente en code-barres bidimensionnel à l'écran d'enrôlement.
    ⚠️ Elle **contient le secret en clair** : elle ne s'affiche qu'une fois, ne
    se journalise jamais, et ne doit figurer dans aucun courriel.
    """
    etiquette = quote(f"{emetteur}:{compte}", safe="")
    return (
        f"otpauth://totp/{etiquette}?secret={secret}"
        f"&issuer={quote(emetteur, safe='')}"
        f"&algorithm=SHA1&digits={CHIFFRES}&period={PAS_SECONDES}"
    )
