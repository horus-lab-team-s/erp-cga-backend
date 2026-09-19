"""Le numéro camerounais, normalisé une bonne fois.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI UNE NORMALISATION, ET POURQUOI ICI

Le même abonné écrit son numéro de six façons : `699112233`, `+237699112233`,
`237 699 11 22 33`, `00237-699-11-22-33`, `06 99 11 22 33`. Toutes désignent la
même personne, et un système qui les traite comme cinq valeurs distinctes échoue
là où cela compte le plus : au **rapprochement d'un paiement mobile**.

Le prestataire de paiement rend le numéro dans son propre format. Si le nôtre
diffère d'un préfixe, la troisième stratégie de rapprochement — celle qui repêche
un encaissement dont l'identifiant s'est perdu — ne trouve rien, et l'argent reste
non affecté. C'est une des causes classiques du double-encaissement : le client
ne voit rien arriver, il repaie.

`app/partage` accueille des fonctions pures, sans entrée-sortie ni configuration,
utilisables par n'importe quelle couche y compris un domaine. Celle-ci en est une.

LE PLAN DE NUMÉROTATION RETENU

Neuf chiffres depuis le passage à neuf chiffres de 2015. Les mobiles commencent
par 6, les fixes par 2. L'indicatif est +237.

⚠️ La correspondance entre préfixe et opérateur n'est **pas** modélisée. Les
tranches se redistribuent, la portabilité existe, et déduire l'opérateur du
préfixe conduirait à router un paiement vers le mauvais réseau. C'est au
prestataire de le déterminer, pas à nous de le deviner.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re

__all__ = ["INDICATIF_CAMEROUN", "NumeroInvalide", "normaliser_telephone"]

INDICATIF_CAMEROUN = "237"

#: Neuf chiffres, commençant par 6 (mobile) ou 2 (fixe).
_NATIONAL = re.compile(r"^[62]\d{8}$")


class NumeroInvalide(ValueError):
    """Le numéro n'est pas un numéro camerounais exploitable."""


def normaliser_telephone(brut: str) -> str:
    """Rend la forme canonique `+237XXXXXXXXX`.

    Accepte les espaces, points, tirets et parenthèses, l'indicatif écrit `+237`,
    `00237` ou `237`, et le zéro initial que certains ajoutent par habitude
    française.

    Lève `NumeroInvalide` plutôt que de rendre le numéro tel quel : un numéro
    qu'on n'a pas su normaliser et qu'on enregistre quand même est un numéro sur
    lequel aucun rapprochement ne fonctionnera, et l'on ne s'en apercevra qu'au
    moment de chercher un paiement perdu.
    """
    chiffres = re.sub(r"[^\d+]", "", brut or "")
    chiffres = chiffres.removeprefix("+")
    chiffres = chiffres.removeprefix("00")
    chiffres = chiffres.removeprefix(INDICATIF_CAMEROUN)

    # Le zéro initial n'existe pas au plan camerounais ; il vient de l'habitude.
    if len(chiffres) == 10 and chiffres.startswith("0"):
        chiffres = chiffres[1:]

    if not _NATIONAL.match(chiffres):
        raise NumeroInvalide(
            f"« {brut} » n'est pas un numéro camerounais exploitable. Neuf chiffres "
            "commençant par 6 pour un mobile, par 2 pour un fixe, indicatif +237 "
            "facultatif."
        )
    return f"+{INDICATIF_CAMEROUN}{chiffres}"
