"""Le locataire de la requête en cours, en un seul endroit.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI UNE VARIABLE DE CONTEXTE PLUTÔT QU'UNE CONSTANTE

Parce qu'il faut pouvoir la changer par requête. Tant que la plateforme ne servait
qu'un cabinet, une constante importée dans quinze modules suffisait — et se lisait
même comme une intention honnête : le cloisonnement existait, il n'avait qu'un
locataire. Le jour où deux clients partagent le processus, cette constante devient
la façon la plus sûre de servir les données de l'un à l'autre.

Le locataire est donc **établi une fois, au bord**, par l'intergiciel qui ouvre
l'unité de travail, et **lu partout ailleurs**. C'est le même contrat que
`horloge.maintenant` : les couches `domaine` et `application` ne l'appellent
jamais, elles reçoivent le locataire en argument. La seule frontière où il se lit
est celle de l'adaptateur.

POURQUOI `courant()` LÈVE AU LIEU DE RENDRE UNE VALEUR PAR DÉFAUT

C'est la décision qui porte tout le module. Un défaut servirait silencieusement
les données d'un locataire à un autre, et **rien ne le signalerait** : la requête
n'échoue pas, elle rend simplement les lignes du mauvais client. Personne ne s'en
aperçoit avant qu'un adhérent ne reconnaisse le nom d'un concurrent sur son écran.

Une exception, elle, se voit tout de suite, en développement, sur la première
requête. Le coût est d'avoir à établir le locataire dans les tests qui touchent
la persistance ; le bénéfice est qu'aucun chemin ne peut lire des données sans
avoir dit pour qui.

CE QUE `contextvars` GARANTIT, ET CE QUE NON

Une variable de contexte est propre à la tâche asynchrone et au fil d'exécution.
Deux requêtes traitées en parallèle ne se marchent pas dessus, et une tâche de
fond qui n'a rien établi lève plutôt que d'hériter du locataire de la dernière
requête servie — ce qui est exactement le comportement voulu.

⚠️ Elle ne franchit pas une frontière de processus. Un travail déporté sur une
file doit **porter son locataire dans le message**, et le rétablir à la
consommation. C'est la règle « chaque message porte son tenant », et ce module
n'en dispense pas.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

__all__ = [
    "LocataireNonEtabli",
    "courant",
    "courant_ou_none",
    "etabli",
    "poser_le_mandat",
    "sous_mandat",
]


class LocataireNonEtabli(RuntimeError):
    """Une lecture de données a été tentée sans qu'on sache pour qui.

    Volontairement une erreur bruyante. Le défaut silencieux qu'elle remplace —
    servir un locataire arbitraire — ne se découvre qu'au moment où un client voit
    les données d'un autre, c'est-à-dire trop tard.
    """


#: `None` par défaut, et c'est ce qui permet à `courant()` de lever. Une valeur
#: par défaut ici annulerait toute la garantie du module.
_COURANT: ContextVar[str | None] = ContextVar("locataire_courant", default=None)

#: Le mandat sous lequel l'exécution se déroule, quand elle n'est pas chez elle.
#:
#: Séparé du locataire plutôt que fondu avec lui, parce qu'il répond à une autre question.
#: Le locataire dit **dans quelles données** on travaille ; le mandat dit **à quel titre**.
#: L'audit a besoin des deux, et les confondre rendrait indistinguables « le comptable de
#: la PME » et « le comptable du centre agissant pour elle ».
_SOUS_MANDAT: ContextVar[str | None] = ContextVar("mandat_courant", default=None)


@contextmanager
def etabli(locataire: str, mandat: str | None = None) -> Iterator[str]:
    """Établit le locataire pour la durée du bloc, et le restitue à la sortie.

    `mandat` porte l'identifiant du mandat sous lequel on agit, quand le locataire servi
    n'est pas celui du porteur du jeton. Il est **facultatif et sans valeur par défaut**
    au sens fort : agir chez soi ne demande aucun mandat, et en inventer un rendrait
    l'audit illisible en marquant toutes les actions comme déléguées.

    Le jeton de restitution est repris en `finally` plutôt que remis à `None` :
    imbriquer deux blocs — ce que fait un travail interne au nom d'un autre
    locataire — doit rendre le locataire extérieur en sortant, pas l'effacer.
    """
    if not locataire or not locataire.strip():
        raise ValueError("un locataire vide n'établit rien")
    jeton = _COURANT.set(locataire)
    jeton_mandat = _SOUS_MANDAT.set(mandat)
    try:
        yield locataire
    finally:
        _SOUS_MANDAT.reset(jeton_mandat)
        _COURANT.reset(jeton)


def courant() -> str:
    """Le locataire de la requête en cours. Lève s'il n'a pas été établi."""
    locataire = _COURANT.get()
    if locataire is None:
        raise LocataireNonEtabli(
            "aucun locataire n'est établi pour cette exécution. Une lecture de données "
            "sans locataire servirait des lignes arbitraires sans que rien ne le signale. "
            "L'établir au bord, avec `etabli(...)`, comme le fait l'intergiciel d'unité "
            "de travail."
        )
    return locataire


def courant_ou_none() -> str | None:
    """Le locataire, ou `None` s'il n'y en a pas.

    Réservé à ce qui doit fonctionner hors requête — un journal, une sonde de
    santé, un diagnostic. **Jamais pour lire des données** : c'est `courant()` qui
    sert à cela, et sa capacité à lever est ce qui protège.
    """
    return _COURANT.get()


def poser_le_mandat(identifiant: str) -> None:
    """Déclare que la suite de cette exécution se déroule sous ce mandat.

    ─────────────────────────────────────────────────────────────────────────────────
    ⚠️ **POURQUOI UN POSEUR PLUTÔT QU'UN BLOC**

    Le mandat n'est connu qu'après l'authentification, c'est-à-dire **après** que le
    locataire a été établi par l'intergiciel. Un bloc `with` supposerait que l'endroit qui
    découvre le mandat soit aussi celui qui englobe la suite de la requête, ce qui n'est
    pas le cas : la dépendance qui résout l'accès rend une valeur, elle n'enveloppe rien.

    ⚠️ **Rien ne fuit pour autant.** La variable est propre à la tâche, et le bloc `etabli`
    ouvert par l'intergiciel restitue en sortant l'état d'avant la requête. Une requête ne
    peut donc pas laisser son mandat à la suivante.
    ─────────────────────────────────────────────────────────────────────────────────
    """
    if not identifiant or not identifiant.strip():
        raise ValueError("un mandat vide n'autorise rien : employer `None` pour « chez soi »")
    _SOUS_MANDAT.set(identifiant)


def sous_mandat() -> str | None:
    """L'identifiant du mandat sous lequel l'exécution se déroule, ou `None`.

    `None` signifie « chez soi », pas « on ne sait pas ». C'est la distinction que l'audit
    exploite : une entrée sans mandat dit qu'un locataire a agi sur ses propres données,
    une entrée avec mandat nomme celui au titre duquel un autre l'a fait à sa place.
    """
    return _SOUS_MANDAT.get()
