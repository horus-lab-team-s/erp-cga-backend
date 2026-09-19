"""Les notifications : ce que le journal d'audit a vu et qui concerne quelqu'un (pas 94).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI LES NOTIFICATIONS NE SONT PAS UN STOCK

La tentation serait qu'à chaque geste, le code du geste écrive une notification pour
chaque destinataire. Il faudrait alors, dans chaque contexte, savoir qui doit être
prévenu, et le savoir **au moment du geste** : un réviseur affecté le lendemain ne
recevrait rien, un collaborateur parti garderait ses notifications, et chaque nouvelle
notification demanderait de modifier le code d'un geste qui marchait.

Or tout geste qui compte écrit déjà au **journal d'audit** : qui, quoi, sur quel objet,
quand. Une notification n'est qu'une **lecture** de ce journal, filtrée par une question
simple : *cette entrée concerne-t-elle la personne qui regarde ?*

La réponse est déclarée, pas codée. Les **abonnements** vivent au référentiel
(`Docs/referentiel/notifications/abonnements.yaml`) : pour une action du journal, qui la
reçoit (une permission, un périmètre, ou un compte nommé dans l'entrée), et quel texte
l'annonce. Ajouter une notification, c'est ajouter un abonnement.

CE QUI EST CONSERVÉ : UNE POSITION DE LECTURE

Rien d'autre. Chaque compte a « lu jusqu'au rang » tel numéro du journal ; ce qui est plus
loin est non lu. Un compte sans position a tout à lire dans la fenêtre du réglage.

Le **rang** et non l'heure : deux entrées peuvent partager la même seconde, et une position
datée laisserait l'une lue et l'autre non selon l'arrondi. Le rang est unique et croissant,
c'est la garantie même du journal.

⚠️ CE QU'UNE NOTIFICATION NE DOIT JAMAIS PORTER

Le **motif** d'une entrée : c'est souvent une note interne (un réviseur qui écarte un
constat), et la notification est lue par des personnes qui n'ont pas `LIRE_AUDIT`. Les
gabarits ne peuvent lire que les champs `apres` et `objet_id` de l'entrée, et le motif
n'y est jamais. Ils ne voient pas non plus l'adresse IP.

Les destinataires sont résolus **pour la session qui lit**, à l'instant de la lecture :
une habilitation retirée ce matin ne voit plus rien des dossiers qu'elle couvrait.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import string
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contextes.transverse.domaine.audit import EntreeAudit

__all__ = [
    "Abonnement",
    "Destinataires",
    "Lecteur",
    "Notification",
    "PolitiqueDeNotification",
    "champs_du_gabarit",
    "notifications_pour",
]


class Lecteur(Protocol):
    """Ce que le domaine demande de la session qui lit. `Acces` y répond."""

    compte: str

    def voit(self, dossier: str) -> bool: ...


class Destinataires(BaseModel):
    """Qui reçoit une entrée. **Au moins une règle**, et elles se cumulent en « et ».

    * `permissions` : la session détient **l'une** d'elles ;
    * `dossier` : le nom du champ de l'entrée qui porte le NIU ; la session doit voir ce
      dossier. Une entrée sans ce champ n'est reçue par personne (sens prudent) ;
    * `compte` : le nom du champ de l'entrée qui porte un identifiant de compte ; seule
      cette personne reçoit.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    permissions: tuple[str, ...] = ()
    dossier: str | None = None
    compte: str | None = None

    @model_validator(mode="after")
    def _au_moins_une_regle(self) -> Destinataires:
        # Un abonnement sans règle notifierait tout le cabinet de tout : c'est l'erreur
        # de configuration la plus coûteuse, et la plus facile à écrire.
        if not self.permissions and self.compte is None:
            raise ValueError(
                "un abonnement doit nommer des permissions ou un compte destinataire : "
                "sans règle, il notifierait tout le monde."
            )
        return self


class Abonnement(BaseModel):
    """Une action du journal, ceux qu'elle concerne, et comment l'annoncer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: L'action exacte du journal (`conformite.ecart_propose`).
    action: str = Field(min_length=3)
    #: Conditions d'égalité sur les champs de l'entrée, toutes requises. Une même action
    #: peut ainsi s'annoncer autrement selon son issue : un écart **en attente** appelle
    #: un second regard, un écart **effectif** n'appelle rien qu'une information.
    si: dict[str, str] = Field(default_factory=dict)
    destinataires: Destinataires
    #: Gabarits : `{champ}` est lu dans `apres`, `{objet_id}` dans l'entrée.
    titre: str = Field(min_length=1)
    texte: str = Field(min_length=1)
    #: Chemin d'écran, sans préfixe de langue ; gabarit lui aussi. Facultatif.
    lien: str | None = None


class PolitiqueDeNotification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Jusqu'où l'on remonte dans le journal, en jours. Au-delà, une notification non
    #: lue n'est plus une information, c'est de l'archive : elle est au journal d'audit.
    fenetre_jours: int = Field(default=30, ge=1, le=365)
    abonnements: tuple[Abonnement, ...] = ()
    #: « abonnements.yaml » ou « aucun fichier » : dit à l'écran pourquoi rien n'arrive.
    source: str = "aucun fichier d'abonnements : aucune notification"


class Notification(BaseModel):
    """Une entrée du journal, telle qu'elle est annoncée à une personne."""

    #: Le rang de l'entrée au journal : unique, croissant, stable.
    rang: int
    action: str
    horodatage: datetime
    titre: str
    texte: str
    lien: str | None = None
    lue: bool


class _Valeurs(dict):
    """Un champ absent de l'entrée s'affiche « ? » plutôt que de faire échouer la liste.

    Une notification mal remplie se voit et se corrige ; une liste qui tombe en erreur
    parce qu'une ancienne entrée n'avait pas un champ ferait disparaître toutes les autres.
    """

    def __missing__(self, cle: str) -> str:
        return "?"


def champs_du_gabarit(gabarit: str | None) -> set[str]:
    """Les noms `{champ}` qu'un gabarit emploie. Sert au test d'intégrité des abonnements."""
    if not gabarit:
        return set()
    return {nom for _, nom, _, _ in string.Formatter().parse(gabarit) if nom}


def _remplir(gabarit: str, entree: EntreeAudit) -> str:
    valeurs: dict[str, Any] = {k: v for k, v in (entree.apres or {}).items()}
    valeurs["objet_id"] = entree.objet_id or "?"
    return gabarit.format_map(_Valeurs(valeurs))


def _concerne(
    abonnement: Abonnement,
    entree: EntreeAudit,
    lecteur: Lecteur,
    permissions: frozenset[str],
) -> bool:
    regles = abonnement.destinataires
    apres = entree.apres or {}
    # On ne se notifie pas soi-même de son propre geste.
    if entree.acteur == lecteur.compte:
        return False
    if regles.permissions and not permissions.intersection(regles.permissions):
        return False
    if regles.dossier is not None:
        niu = apres.get(regles.dossier)
        if not isinstance(niu, str) or not lecteur.voit(niu):
            return False
    if regles.compte is not None and apres.get(regles.compte) != lecteur.compte:
        return False
    return True


def notifications_pour(
    entrees: Iterable[EntreeAudit],
    *,
    lecteur: Lecteur,
    permissions: Iterable[str],
    politique: PolitiqueDeNotification,
    lu_jusqu_au_rang: int | None,
) -> list[Notification]:
    """Les notifications de ce lecteur, **de la plus récente à la plus ancienne**.

    Les entrées sont celles de la fenêtre, déjà lues au journal par l'appelant. Une entrée
    qui répond à deux abonnements n'est annoncée qu'une fois, par le premier dans l'ordre
    du fichier : deux fois le même avis se lit comme deux faits.

    ⚠️ Le premier qui **correspond** (action et conditions), et non le premier qui
    **concerne** le lecteur : sinon, une personne exclue du premier abonnement recevrait
    le second, écrit pour un autre cas.
    """
    detenues = frozenset(permissions)
    resultat = []
    for entree in entrees:
        apres = entree.apres or {}
        # Le **premier** abonnement dont l'action et les conditions correspondent : l'ordre
        # du fichier décide, et c'est lisible par qui l'écrit.
        abonnement = next(
            (
                a
                for a in politique.abonnements
                if a.action == entree.action
                and all(str(apres.get(k)) == v for k, v in a.si.items())
            ),
            None,
        )
        if abonnement is None or not _concerne(abonnement, entree, lecteur, detenues):
            continue
        resultat.append(
            Notification(
                rang=entree.rang,
                action=entree.action,
                horodatage=entree.horodatage,
                titre=_remplir(abonnement.titre, entree),
                texte=_remplir(abonnement.texte, entree),
                lien=_remplir(abonnement.lien, entree) if abonnement.lien else None,
                lue=lu_jusqu_au_rang is not None and entree.rang <= lu_jusqu_au_rang,
            )
        )
    return sorted(resultat, key=lambda n: n.rang, reverse=True)
