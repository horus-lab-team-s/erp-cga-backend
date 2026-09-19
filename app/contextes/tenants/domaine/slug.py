"""Le nom court qui identifie un tenant dans son sous-domaine.

`station-bonaberi.cga.cm` : `station-bonaberi` est le slug. C'est ce que le client lit sur
sa carte de visite, ce qu'il tape dans son navigateur, et ce que la passerelle résout à
chaque requête.

DEUX PROPRIÉTÉS QUI NE SE NÉGOCIENT PAS

**Un slug est attribué une fois et jamais réattribué.** Le libérer pour le donner à
quelqu'un d'autre enverrait les anciens liens, les signets et les courriels archivés d'un
client chez un concurrent. C'est le pire incident de confidentialité que cette plateforme
puisse produire, et il n'a aucune contrepartie : un slug coûte quelques octets à conserver.

**Un slug doit être un nom d'hôte valide.** Pas parce qu'une norme l'exige dans l'absolu,
mais parce qu'un nom invalide se comporte de façon *intermittente* : certains résolveurs
l'acceptent, d'autres le rejettent. Un client sur deux voit son espace, et le défaut est
introuvable parce qu'il ne se reproduit pas chez le développeur.

CE QUE CE MODULE NE FAIT PAS

Il ne vérifie pas l'unicité. Deux souscriptions simultanées sur le même slug doivent être
départagées par un **index unique en base**, jamais par une lecture suivie d'une écriture :
entre les deux, l'autre a eu le temps d'écrire. La fonction `proposer` évite les collisions
connues ; elle ne les empêche pas, et la base reste l'arbitre.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Iterable
from enum import StrEnum

__all__ = [
    "LONGUEUR_MAXIMALE",
    "LONGUEUR_MINIMALE",
    "MotifRejet",
    "SlugInvalide",
    "normaliser",
    "proposer",
    "valider",
]

#: En dessous, la collision avec un nom réservé devient probable et le nom perd son sens.
LONGUEUR_MINIMALE = 3

#: Au dessus, l'adresse cesse d'être utilisable dans un courriel ou sur une carte de visite.
LONGUEUR_MAXIMALE = 40

#: Lettres, chiffres et tiret simple. Un nom d'hôte n'accepte rien d'autre.
_FORME = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")

#: Le préfixe des noms de domaine internationalisés. Un slug qui le porte est interprété
#: comme du punycode par certains clients, qui affichent alors autre chose que le nom saisi.
_PREFIXE_INTERNATIONAL = "xn--"


class MotifRejet(StrEnum):
    """Pourquoi un slug est refusé.

    Nommé plutôt que laissé au message : l'interface de souscription doit pouvoir dire au
    client *ce qu'il faut corriger*, et traduire un texte français en consigne utile
    demande de savoir de quel cas il s'agit.
    """

    VIDE = "VIDE"
    TROP_COURT = "TROP_COURT"
    TROP_LONG = "TROP_LONG"
    FORME_INVALIDE = "FORME_INVALIDE"
    PREFIXE_INTERNATIONAL = "PREFIXE_INTERNATIONAL"
    RESERVE = "RESERVE"


class SlugInvalide(ValueError):
    """Le slug proposé ne peut pas être attribué.

    Porte son motif pour que l'appelant décide quoi en faire : proposer une correction,
    demander autre chose au client, ou suffixer automatiquement.
    """

    def __init__(self, motif: MotifRejet, message: str) -> None:
        super().__init__(message)
        self.motif = motif


def normaliser(brut: str) -> str:
    """Transforme une saisie libre en candidat de slug.

    Les accents sont dépliés plutôt que supprimés : « Société Générale » donne
    `societe-generale` et non `socit-gnrale`. C'est la différence entre un nom qu'un client
    reconnaît et un nom qu'il refuse.

    La normalisation se fait **à la saisie**, pas à la validation. Le client doit voir tout
    de suite l'adresse qu'il obtiendra, sinon il découvre après paiement que « Boulangerie
    du Wouri » est devenue `boulangerie-du-wouri` et croit à une erreur.
    """
    deplie = unicodedata.normalize("NFKD", brut)
    sans_accent = "".join(c for c in deplie if not unicodedata.combining(c))
    minuscules = sans_accent.lower()
    # Tout ce qui n'est ni lettre ni chiffre devient un tiret, puis les tirets se
    # rassemblent : « S.A.R.L.  Batiment + Plus » donne `s-a-r-l-batiment-plus`.
    avec_tirets = re.sub(r"[^a-z0-9]+", "-", minuscules)
    return avec_tirets.strip("-")


def valider(slug: str, reserves: Collection[str] = ()) -> None:
    """Refuse un slug inattribuable, en disant lequel des six cas s'applique.

    `reserves` est passé plutôt que constant : la liste des noms réservés grandit avec la
    plateforme — un sous-domaine technique de plus, une marque à protéger — et la faire
    grandir ne doit pas demander une livraison.
    """
    if not slug:
        raise SlugInvalide(MotifRejet.VIDE, "un slug ne peut pas être vide")

    if len(slug) < LONGUEUR_MINIMALE:
        raise SlugInvalide(
            MotifRejet.TROP_COURT,
            f"« {slug} » fait {len(slug)} caractères, le minimum est {LONGUEUR_MINIMALE}",
        )

    if len(slug) > LONGUEUR_MAXIMALE:
        raise SlugInvalide(
            MotifRejet.TROP_LONG,
            f"« {slug} » fait {len(slug)} caractères, le maximum est {LONGUEUR_MAXIMALE} "
            "pour rester utilisable dans un courriel",
        )

    if not _FORME.match(slug):
        raise SlugInvalide(
            MotifRejet.FORME_INVALIDE,
            f"« {slug} » n'est pas un nom d'hôte valide : minuscules, chiffres et tirets "
            "simples, sans tiret au début ni à la fin",
        )

    if slug.startswith(_PREFIXE_INTERNATIONAL):
        raise SlugInvalide(
            MotifRejet.PREFIXE_INTERNATIONAL,
            f"« {slug} » commence par {_PREFIXE_INTERNATIONAL}, réservé aux noms "
            "internationalisés : certains clients l'afficheraient autrement",
        )

    # Comparaison en minuscules des deux côtés : la réservation de « API » doit interdire
    # « api ». L'unicité en base suit la même règle, par un index insensible à la casse.
    if slug in {reserve.lower() for reserve in reserves}:
        raise SlugInvalide(
            MotifRejet.RESERVE,
            f"« {slug} » est réservé à la plateforme et ne peut pas être attribué",
        )


def proposer(
    raison_sociale: str,
    reserves: Collection[str] = (),
    deja_pris: Iterable[str] = (),
) -> str:
    """Le slug que la plateforme suggère pour une raison sociale donnée.

    Suffixe par un rang si le nom est déjà pris ou réservé : `station-service`,
    `station-service-2`, `station-service-3`. Le rang commence à deux parce que le premier
    n'a pas de numéro — personne n'appelle son entreprise « Machin 1 ».

    ⚠️ **Ceci ne garantit pas l'unicité.** Entre la lecture de `deja_pris` et l'écriture,
    une autre souscription peut avoir pris le nom. C'est l'index unique en base qui
    tranche, et l'appelant doit être prêt à recevoir son refus.
    """
    base = normaliser(raison_sociale)

    # Un nom trop court après normalisation ne se rattrape pas en devinant : « SA » donne
    # « sa », et inventer « sa-entreprise » produirait une adresse que personne n'a
    # demandée. On laisse la validation refuser et le client choisir lui-même.
    if len(base) < LONGUEUR_MINIMALE:
        return base

    base = base[:LONGUEUR_MAXIMALE].rstrip("-")
    interdits = {nom.lower() for nom in reserves} | {nom.lower() for nom in deja_pris}

    if base not in interdits:
        return base

    rang = 2
    while True:
        suffixe = f"-{rang}"
        candidat = base[: LONGUEUR_MAXIMALE - len(suffixe)].rstrip("-") + suffixe
        if candidat not in interdits:
            return candidat
        rang += 1
