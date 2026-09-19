"""L'instant présent, en un seul endroit.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI UNE FONCTION PLUTÔT QUE `datetime.now()` PARTOUT

Parce qu'il faut pouvoir la remplacer. Un système dont les règles dépendent de
dates — et celui-ci n'est que cela — se teste en choisissant l'instant. Répandre
`datetime.now()` dans les adaptateurs oblige à ruser pour tester le 31 décembre,
la veille d'une échéance, ou le lendemain d'une expiration.

Les couches `domaine` et `application` ne l'appellent jamais : elles reçoivent
l'instant en argument. C'est ce qui rend leurs tests directs et sans artifice. La
seule frontière où l'instant se lit est celle de l'adaptateur entrant, et c'est
ici.

POURQUOI UTC, ET POURQUOI SANS FUSEAU ENSUITE

Le Cameroun est à UTC+1 et n'applique pas d'heure d'été. L'écart est constant,
donc discret — et une erreur d'une heure qui ne se voit pas est une erreur qui
dure. Tout est donc horodaté en UTC, et la conversion à l'affichage est l'affaire
de la vitrine.

Le fuseau est retiré après coup pour que les comparaisons avec les `datetime`
naïfs du reste du système fonctionnent. Mélanger conscients et naïfs lève une
`TypeError` à l'exécution, souvent dans une branche rarement empruntée — le genre
de défaut qui se découvre un dimanche.

⚠️ Le jour où la persistance passe en base, ces horodatages devront y être stockés
en UTC eux aussi, et la colonne le dire.

LA COUTURE QUI MANQUAIT

Cet en-tête affirmait depuis l'origine qu'il fallait « pouvoir la remplacer », et
rien ne le permettait. Le manque s'est vu à minuit : un test d'API qui affirmait
qu'une souscription ouvre un accès **le 15 août** a viré au rouge le 16, sans
qu'une ligne de code ait bougé. La route lisait l'horloge murale, le test
comparait à une date figée dans un fichier.

Un test qui change de verdict avec le calendrier est pire qu'un test absent : il
échoue un jour où personne ne cherche un défaut, et l'équipe apprend à le
ressusciter en modifiant sa date au lieu de lire ce qu'il dit. `horloge_figee`
supprime la question.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

__all__ = ["horloge_figee", "maintenant"]

#: L'instant imposé, s'il y en a un.
#:
#: ⚠️ Une variable de **contexte** et non une globale : les routes synchrones de
#: FastAPI s'exécutent dans un fil du réservoir, et `anyio` y recopie le
#: contexte. Une globale ordinaire figerait aussi l'horloge des requêtes
#: voisines — invisible en test unitaire, désastreux dès qu'ils s'exécutent en
#: parallèle.
#:
#: ⚠️ Rien en production ne la renseigne, et rien ne doit le faire. Le défaut
#: `None` est l'horloge réelle ; c'est une couture de test, pas un réglage.
_INSTANT_IMPOSE: ContextVar[datetime | None] = ContextVar(
    "instant_impose", default=None
)


def maintenant() -> datetime:
    """L'instant, en UTC, sans fuseau attaché. Voir l'en-tête."""
    impose = _INSTANT_IMPOSE.get()
    if impose is not None:
        return impose
    return datetime.now(UTC).replace(tzinfo=None)


@contextmanager
def horloge_figee(instant: datetime) -> Iterator[datetime]:
    """Impose l'instant pour la durée du bloc. Réservé aux tests.

    ⚠️ N'accepte qu'un `datetime` naïf, comme tout le reste du système. Un
    instant conscient du fuseau se comparerait mal aux horodatages relus, et
    lèverait une `TypeError` dans une branche rare — le défaut qu'évite
    précisément la convention décrite plus haut.
    """
    if instant.tzinfo is not None:
        raise ValueError(
            "horloge_figee attend un instant naïf en UTC. "
            "Employez `instant.astimezone(UTC).replace(tzinfo=None)`."
        )
    jeton = _INSTANT_IMPOSE.set(instant)
    try:
        yield instant
    finally:
        _INSTANT_IMPOSE.reset(jeton)
