"""Décider si un acte est permis, à une date, sur un dossier.

─────────────────────────────────────────────────────────────────────────────────
DEUX QUESTIONS, ET IL FAUT RÉPONDRE OUI AUX DEUX

**Le droit** — cette personne détient-elle la permission ? Elle vient de ses
rôles, eux-mêmes résolus à la date de l'acte et non à celle du jour.

**Le périmètre** — sur ce dossier-là ? Un comptable a bien le droit de valider une
écriture ; il ne l'a pas sur un dossier qui n'est pas le sien. Le cloisonnement
par portefeuille de `05-securite-multitenant.md` § 1 se joue ici.

Séparer les deux importe : confondues, elles produiraient soit un système où tout
comptable voit tout le cabinet, soit un système où l'on doit déclarer une
permission par dossier. Le premier est ce qu'on veut éviter, le second est
ingérable dès la trentième entreprise.

LA RÉSOLUTION SE FAIT À CHAQUE CONTRÔLE, PAS À L'OUVERTURE DE SESSION

Un `Acces` se construit à partir des habilitations lues **maintenant**. Le figer à
la connexion ferait qu'un droit retiré le matin resterait exercé jusqu'au soir,
et le journal d'audit enregistrerait des actes conformes à une habilitation qui
n'existait plus.

LE REFUS DIT POURQUOI — CÔTÉ SERVEUR

`AccesRefuse` porte un message précis, destiné au journal et au diagnostic. Ce
que la couche HTTP en montre est une autre décision, prise ailleurs : dire à un
adhérent « vous n'avez pas la permission VALIDER_ECRITURE sur le dossier
M071122334455J » lui apprend qu'un tel dossier existe.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict, computed_field

from app.contextes.transverse.domaine.habilitations import (
    Habilitation,
    dossiers_accessibles,
    habilitations_actives,
)
from app.contextes.transverse.domaine.identites import Compte
from app.contextes.transverse.domaine.roles import (
    EXIGE_MFA,
    EXIGE_MOTIF,
    ROLES_INTERNES,
    Permission,
    Role,
    permissions_de,
)
from app.contextes.transverse.domaine.sessions import Session

__all__ = [
    "Acces",
    "AccesRefuse",
    "MotifRequis",
    "SecondFacteurRequis",
    "resoudre_acces",
]


class AccesRefuse(RuntimeError):
    """L'acte n'est pas permis. Message destiné au serveur — voir l'en-tête."""


class SecondFacteurRequis(AccesRefuse):
    """La permission figure dans `EXIGE_MFA` et la session n'est pas renforcée.

    Le remède est à portée : présenter un code du second facteur élève la session
    pour quinze minutes. Ce n'est plus un blocage définitif — voir l'en-tête de
    `sessions.py` et `domaine/second_facteur.py`.
    """


class MotifRequis(AccesRefuse):
    """L'acte figure dans `EXIGE_MOTIF` et aucun motif n'a été fourni."""


class Acces(BaseModel):
    """Ce qu'une session permet, résolu à une date donnée.

    Objet de lecture : il ne décide rien de lui-même, il porte le résultat d'une
    résolution et sait le confronter à une demande.
    """

    model_config = ConfigDict(frozen=True)

    compte: str
    locataire: str
    session: str
    nom_complet: str

    roles: list[Role]
    permissions: list[Permission]

    #: `None` = tous les dossiers du cabinet.
    dossiers: list[str] | None
    #: Résolu à l'instant : une session renforcée à 9 h ne dépose pas à 14 h.
    facteur_fort: bool

    #: Le compte a-t-il enrôlé un second facteur ? Distinct de `facteur_fort`,
    #: et la distinction commande l'écran : sans enrôlement on propose de
    #: s'enrôler, avec enrôlement on demande un code. Les confondre ferait
    #: réclamer un code à quelqu'un qui n'a rien à ouvrir.
    second_facteur_enrole: bool = False

    a_la_date: date

    @computed_field
    @property
    def transverse(self) -> bool:
        return self.dossiers is None

    @computed_field
    @property
    def interne(self) -> bool:
        """Salarié du cabinet — commande le routage vers l'espace de travail
        plutôt que vers l'espace adhérent. Sérialisé parce que c'est le front qui
        décide où poser l'utilisateur après connexion, et il ne doit pas
        réinventer la règle."""
        return any(role in ROLES_INTERNES for role in self.roles)

    # ── Les deux questions ──────────────────────────────────────────────────

    def detient(self, permission: Permission) -> bool:
        return permission in self.permissions

    def voit(self, dossier: str) -> bool:
        return self.dossiers is None or dossier in self.dossiers

    def exiger(
        self,
        permission: Permission,
        *,
        dossier: str | None = None,
        motif: str | None = None,
    ) -> None:
        """Lève si l'acte n'est pas permis. Rend `None` s'il l'est.

        L'ordre des contrôles est celui du coût de la fuite d'information : on
        vérifie le droit **avant** le périmètre, pour qu'un refus ne révèle jamais
        qu'un dossier existe à quelqu'un qui n'a de toute façon pas la permission.
        """
        if permission not in self.permissions:
            raise AccesRefuse(
                f"{self.compte} ne détient pas {permission} au {self.a_la_date}. "
                f"Rôles à cette date : {', '.join(r.value for r in self.roles) or 'aucun'}."
            )
        if permission in EXIGE_MFA and not self.facteur_fort:
            raise SecondFacteurRequis(
                f"{permission} exige une authentification forte (tableau des actions "
                "sensibles, 05-securite-multitenant.md § 2). Présenter un code du second "
                "facteur pour renforcer la session : POST /transverse/session/renforcement."
            )
        if permission in EXIGE_MOTIF and not (motif and motif.strip()):
            raise MotifRequis(
                f"{permission} ne s'exécute pas sans motif écrit. Un vérificateur "
                "demandera pourquoi, et « le réviseur l'a jugé ainsi » n'est pas une "
                "réponse."
            )
        if dossier is not None and not self.voit(dossier):
            raise AccesRefuse(
                f"{self.compte} détient bien {permission}, mais son périmètre ne couvre "
                f"pas le dossier {dossier}. Périmètre : "
                f"{', '.join(self.dossiers or []) or 'aucun dossier'}."
            )


def resoudre_acces(
    compte: Compte,
    session: Session,
    habilitations: Sequence[Habilitation],
    a_la_date: date,
    a_l_instant: datetime | None = None,
) -> Acces:
    """Assemble les rôles, permissions et dossiers valides à cette date.

    `a_l_instant` sert au seul renforcement de session, qui se périme à la
    minute quand tout le reste se résout à la journée. À défaut, on prend minuit
    au début de la date — donc **non renforcée**, ce qui est le refus, et le
    refus est le bon défaut.
    """
    instant = a_l_instant or datetime.combine(a_la_date, time.min)
    actives = habilitations_actives(habilitations, a_la_date)
    roles = frozenset(h.role for h in actives)
    dossiers = dossiers_accessibles(actives, a_la_date)
    return Acces(
        compte=compte.identifiant,
        locataire=compte.locataire,
        session=session.identifiant,
        nom_complet=compte.nom_complet,
        roles=sorted(roles, key=lambda r: r.value),
        permissions=sorted(permissions_de(roles), key=lambda p: p.value),
        dossiers=None if dossiers is None else sorted(dossiers),
        facteur_fort=session.renforcee(instant),
        second_facteur_enrole=compte.second_facteur_actif,
        a_la_date=a_la_date,
    )
