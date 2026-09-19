"""L'exclusion entre instances, par verrou consultatif PostgreSQL.

─────────────────────────────────────────────────────────────────────────────────
LE PROBLÈME, EN UNE PHRASE

Deux instances de l'application qui font tourner le même ordonnanceur balaient les
mêmes proformas, et le client reçoit deux relances identiques à la même seconde.

⚠️ **Ce n'est pas un cas d'école.** Le déploiement décrit à la section 37 du document
de conception prévoit deux conteneurs derrière un répartiteur, ne serait-ce que le
temps d'une mise à jour sans coupure : pendant quelques secondes, l'ancienne et la
nouvelle version tournent ensemble.

POURQUOI UN VERROU CONSULTATIF PLUTÔT QU'UNE TABLE

Une table de verrous demande d'écrire une ligne, de la relire, de la supprimer, et
surtout de la **nettoyer quand le processus meurt sans la rendre**. C'est ce dernier
point qui pourrit : la ligne reste, elle dit « quelqu'un travaille », et personne ne
travaille plus. Il faut alors un délai d'expiration, donc une horloge, donc un
second mécanisme pour arbitrer les horloges qui divergent.

Un verrou consultatif PostgreSQL **tombe tout seul** quand la connexion se ferme,
que ce soit proprement ou parce que le processus a été tué. Il n'y a rien à
nettoyer, rien à expirer, et aucune horloge à arbitrer.

POURQUOI LA VARIANTE « XACT »

`pg_try_advisory_xact_lock` rend le verrou à la fin de la transaction, toujours,
sans qu'aucun code n'ait à le faire. La variante de session, elle, se rend
explicitement — et **se perd** dès qu'une connexion mise en réserve est rendue au
pool puis reprise par un autre appel : le déverrouillage part alors sur une autre
connexion que le verrouillage, ne trouve rien, et le verrou reste pris jusqu'à ce
que la connexion soit recyclée. C'est un défaut typique des applications qui
mettent leurs connexions en réserve, et celle-ci le fait.

⚠️ **Conséquence à connaître avant d'employer ce module** : le travail protégé doit
tenir dans **une** transaction. C'est le cas de tous les travaux de l'ordonnanceur,
et si l'un cessait de l'être, il faudrait le découper plutôt que passer au verrou de
session.

CE QUE « CONSULTATIF » VEUT DIRE, ET NE VEUT PAS DIRE

PostgreSQL ne fait qu'arbitrer : il ne protège aucune table, aucune ligne, et
n'empêche personne d'écrire. Deux processus qui prennent le même verrou
s'entendent parce qu'ils demandent tous les deux ; un troisième qui ne demande
rien travaillera en même temps sans que rien ne l'en empêche. Le verrou est donc
une **convention appliquée par la base**, et sa valeur tient à ce que tous les
chemins la respectent.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session

__all__ = ["cle_de_verrou", "verrou_exclusif"]


def cle_de_verrou(nom: str) -> int:
    """Transforme un nom en entier signé sur 64 bits, comme PostgreSQL l'attend.

    ─────────────────────────────────────────────────────────────────────────
    ⚠️ **`hash()` de Python ne convient pas.** Il est **salé par processus** depuis
    la 3.3 : deux instances de l'application calculeraient deux clés différentes
    pour le même nom, chacune prendrait son verrou, et l'exclusion n'existerait
    pas. Le défaut serait invisible sur une machine de développement à un seul
    processus, et se manifesterait en production par des relances en double.

    Une empreinte cryptographique est stable entre processus, entre machines et
    entre versions de Python. Elle est employée ici pour sa **stabilité**, pas
    pour sa résistance : un nom de travail n'est pas un secret.

    Le décalage de 64 bits ramène l'empreinte dans l'intervalle signé attendu par
    `pg_try_advisory_xact_lock`, qui prend un `bigint`.
    ─────────────────────────────────────────────────────────────────────────
    """
    empreinte = hashlib.blake2b(nom.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(empreinte, "big", signed=True)


@contextmanager
def verrou_exclusif(session: Session, nom: str) -> Iterator[bool]:
    """Tente de prendre le verrou. Rend `True` s'il est pris, `False` sinon.

    ─────────────────────────────────────────────────────────────────────────
    IL NE LÈVE PAS QUAND LE VERROU EST DÉJÀ PRIS, ET C'EST LE POINT

    Une instance qui n'obtient pas le verrou n'est pas en erreur : elle constate
    qu'une autre fait le travail, et passe son tour. C'est le fonctionnement
    normal, pas une panne, et lever une exception obligerait chaque appelant à la
    rattraper pour ne rien en faire.

    ⚠️ **`try_` et non la variante bloquante.** Attendre son tour ferait la file
    exactement de ce qu'on cherche à éviter : dix instances qui attendent
    produiraient dix passages consécutifs au lieu d'un seul, avec le seul mérite
    de ne pas les faire en même temps.

    S'emploie ainsi :

        with verrou_exclusif(session, "ordonnanceur") as pris:
            if not pris:
                return
            ...

    ⚠️ Le verrou tombe à la fin de la transaction de cette session, pas à la sortie
    du bloc. C'est pourquoi cette fonction ne rend rien elle-même : le rendre ici
    donnerait l'illusion que le bloc borne la protection.
    ─────────────────────────────────────────────────────────────────────────
    """
    pris = session.execute(
        text("SELECT pg_try_advisory_xact_lock(:cle)"), {"cle": cle_de_verrou(nom)}
    ).scalar_one()
    yield bool(pris)
