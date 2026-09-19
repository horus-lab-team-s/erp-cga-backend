"""Le jeton de session porte le locataire où il a été ouvert.

─────────────────────────────────────────────────────────────────────────────────
LE DÉFAUT QUE CE MODULE FERME

Le chantier multi-tenant l'avait écrit dans ce qu'il laissait derrière lui : « la double
vérification du tenant du jeton contre celui du domaine, dans le chemin
d'authentification ». Mesuré avant d'écrire une ligne, sur la pile de démonstration :

    session ouverte sur   cabinet.cga.cm            → 200
    le même témoin sur    station-bonaberi.cga.cm   → 200, et l'accès rendu dit
                                                      « locataire CGA-BRCG »

La requête était donc servie **dans le périmètre de station-bonaberi**, avec les
permissions d'un compte du cabinet. Rien ne le signalait.

⚠️ **EN BASE, LE FILTRE DE LOCATAIRE FERMAIT LA PORTE PAR ACCIDENT.** La table des
sessions est cloisonnée : sous un autre locataire, la ligne n'est simplement pas trouvée,
et l'appelant reçoit 401. C'est un bon résultat obtenu par un mécanisme qui ne visait pas
cela. Le jour où une session serait mise en cache, partagée entre répliques, ou stockée
hors de la base, la protection disparaîtrait sans que personne ne s'en aperçoive.

En mémoire, c'est-à-dire en démonstration et dans une partie des tests, il n'y avait
aucune porte du tout : l'atelier est unique pour le processus.

CE QUE FAIT CE MODULE, ET CE QU'IL NE FAIT PAS

Il compose et relit un témoin de la forme `locataire~identifiant`. Il ne chiffre rien et
ne signe rien : **le secret reste l'identifiant de session**, exactement comme avant. Le
préfixe n'est pas une protection, c'est une **déclaration** que le bord vérifie.

⚠️ **Un témoin sans préfixe reste accepté**, et ce n'est pas un contournement. Il est alors
lu comme appartenant au locataire de la requête, donc soumis à la même recherche qu'avant :
en base, le filtre de cloisonnement le refuse ailleurs. Cette tolérance sert l'outillage en
ligne de commande, qui présente l'identifiant nu en `Authorization: Bearer`, et elle permet
aux sessions déjà ouvertes de survivre à un déploiement au lieu de déconnecter tout le
monde.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

__all__ = ["SEPARATEUR", "composer", "lire"]

#: ⚠️ Un caractère qu'aucun slug ne peut porter, et qu'un témoin transporte sans être cité.
#: Un slug est fait de minuscules, de chiffres et de tirets simples ; le locataire par
#: défaut n'ajoute qu'une capitale. Le point, lui, aurait été ambigu avec un nom d'hôte.
SEPARATEUR = "~"


class LocataireInscriptible(ValueError):
    """Le nom du locataire ne peut pas être écrit dans un témoin."""


def composer(locataire: str, identifiant: str) -> str:
    """Le témoin à poser : où la session a été ouverte, puis le secret.

    ⚠️ Un locataire qui porterait le séparateur rendrait la relecture ambiguë, et
    l'ambiguïté se réglerait au détriment de la vérification. On refuse à l'écriture
    plutôt que de deviner à la lecture.
    """
    if SEPARATEUR in locataire:
        raise LocataireInscriptible(
            f"le locataire {locataire!r} porte {SEPARATEUR!r}, qui sépare le témoin"
        )
    return f"{locataire}{SEPARATEUR}{identifiant}"


def lire(valeur: str) -> tuple[str | None, str]:
    """Le locataire déclaré par le témoin, ou `None`, et l'identifiant de session.

    ⚠️ La coupe se fait sur la **première** occurrence du séparateur : un identifiant qui
    en contiendrait un resterait entier. Couper sur la dernière ferait d'un identifiant
    fantaisiste un moyen de changer le locataire déclaré.
    """
    locataire, trouve, identifiant = valeur.partition(SEPARATEUR)
    if not trouve:
        return None, valeur
    return locataire, identifiant
