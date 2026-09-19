"""Le mandat : agir dans le périmètre d'un autre locataire, et le dire.

─────────────────────────────────────────────────────────────────────────────────
LE PROBLÈME QU'IL RÉSOUT

Le cas le plus courant de la plateforme : **le centre tient la comptabilité d'une PME qui
dispose aussi de son propre accès.** La station-service dépose ses factures depuis son
espace, le comptable du centre les traite depuis le sien. Deux locataires, deux jeux de
données, et pourtant les mêmes écritures.

Fusionner les deux locataires ferait perdre le cloisonnement. Dupliquer les données ferait
perdre la vérité. La troisième voie est le mandat : un objet explicite, daté, révocable,
qui autorise un compte du centre à agir **dans le périmètre** de l'entreprise.

CE QU'IL ÉVITE, ET C'EST TOUT SON INTÉRÊT

Sans lui, la seule façon de faire travailler le centre sur les données de la PME serait de
donner à ses collaborateurs un **accès transversal à tous les locataires**. Le jour d'un
litige — un client qui part fâché, un contrôle de l'administration —, personne ne saurait
dire qui a touché quoi ni au nom de qui.

Avec lui, la question est triviale : chaque action exercée sous mandat est journalisée
comme telle, et l'audit dit « le comptable X du centre Y, agissant pour le locataire Z ».

TROIS QUESTIONS, DANS CET ORDRE

    1. un mandat existe-t-il entre ces deux locataires, valide à cet instant ?
    2. couvre-t-il le rôle nécessaire au geste demandé ?
    3. le compte porteur fait-il partie des personnes qu'il désigne ?

L'ordre compte pour le diagnostic : un mandat révoqué et un mandat qui ne couvre pas le
rôle demandé appellent deux réponses différentes à l'utilisateur — l'un est fini, l'autre
se complète. La réponse **au demandeur**, elle, reste la même dans les trois cas : 404,
jamais 403. Un 403 apprendrait que le locataire visé existe.

⚠️ UN MANDAT NE DONNE PAS DE RÔLE, IL EN AUTORISE L'EXERCICE AILLEURS

Un comptable mandaté reste comptable : il ne devient pas réviseur en franchissant la
frontière. Le mandat dit *où* les rôles déjà tenus peuvent s'exercer, jamais *lesquels*.
Confondre les deux ferait du mandat une porte d'élévation de privilèges, ce qui est
exactement ce qu'il doit empêcher.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contextes.transverse.domaine.roles import Role

__all__ = [
    "Mandat",
    "MotifMandat",
    "RefusDeMandat",
    "mandat_applicable",
]


class MotifMandat(StrEnum):
    """Pourquoi le mandat a été accordé. Il commande sa révocabilité."""

    #: Accordé par la PME, qui peut le retirer quand elle veut.
    CONSENTEMENT = "CONSENTEMENT"
    #: Né du contrat de suivi, pour un dossier dont le centre est l'unique opérateur.
    #: L'adhérent le voit sans pouvoir le retirer tant que le contrat court.
    CONTRAT_DE_SUIVI = "CONTRAT_DE_SUIVI"
    #: Intervention ponctuelle, bornée dans le temps. C'est ce qui remplace un rôle
    #: d'administration transversale : le dépannage se trace et il finit.
    ASSISTANCE = "ASSISTANCE"


class RefusDeMandat(StrEnum):
    """Pourquoi aucun mandat ne s'applique.

    Nommé pour le **diagnostic**, jamais pour la réponse au demandeur : celle-ci reste
    404 dans tous les cas. Un 403, ou pire un message distinguant « révoqué » de
    « inexistant », apprendrait que le locataire visé existe.
    """

    AUCUN = "AUCUN"
    EXPIRE = "EXPIRE"
    REVOQUE = "REVOQUE"
    ROLE_NON_COUVERT = "ROLE_NON_COUVERT"
    COMPTE_NON_DESIGNE = "COMPTE_NON_DESIGNE"


class Mandat(BaseModel):
    """L'autorisation d'agir dans le périmètre d'un autre locataire.

    Intervalle `[debut, fin[`, borne haute exclue — même convention que l'habilitation, le
    référentiel et le portefeuille. Sans elle, le dernier jour d'un mandat appartiendrait
    à deux périodes à la fois.
    """

    model_config = ConfigDict(frozen=True)

    identifiant: str = Field(min_length=1)

    #: Celui qui **accorde** : le locataire dont les données sont en jeu.
    mandant: str = Field(min_length=1)
    #: Celui qui **reçoit** : le locataire dont les comptes vont agir.
    mandataire: str = Field(min_length=1)

    #: Les rôles dont le mandat autorise l'exercice chez le mandant. Jamais vide : un
    #: mandat qui n'autorise aucun rôle n'autorise rien, et le laisser exister ferait
    #: croire à un accès qui n'existe pas.
    roles: frozenset[Role]

    #: `None` = tous les comptes du mandataire. Un ensemble = ceux-là seulement, ce qui
    #: permet à une PME de mandater un centre en désignant l'équipe qui la suit.
    comptes: frozenset[str] | None = None

    debut: date
    fin: date | None = None
    motif: MotifMandat

    #: Qui a accordé, nommément. Un mandat qui s'accorde sans nom n'engage personne, et
    #: c'est la première chose qu'un audit d'accès réclame.
    accorde_par: str = Field(min_length=1)

    #: La révocation est **distincte de la fin** : elle survient avant terme et porte sa
    #: propre date. Écraser `fin` ferait perdre l'information qu'un mandat a été retiré
    #: plutôt qu'arrivé à échéance, et c'est exactement ce qu'un litige demande de savoir.
    revoque_le: date | None = None
    revoque_par: str | None = None

    precision: str | None = None

    @model_validator(mode="after")
    def _coherence(self) -> Mandat:
        if self.mandant == self.mandataire:
            raise ValueError(
                f"{self.identifiant} : un locataire n'a pas besoin d'un mandat sur lui-même"
            )
        if not self.roles:
            raise ValueError(
                f"{self.identifiant} : un mandat sans rôle n'autorise rien. Le supprimer "
                "plutôt que de le laisser faire croire à un accès."
            )
        if self.fin is not None and self.fin <= self.debut:
            raise ValueError(f"{self.identifiant} : borne de validité incohérente")
        if self.revoque_le is not None and self.revoque_le < self.debut:
            raise ValueError(
                f"{self.identifiant} : révoqué avant d'avoir commencé"
            )
        if bool(self.revoque_le) != bool(self.revoque_par):
            raise ValueError(
                f"{self.identifiant} : une révocation porte sa date **et** son auteur. "
                "L'une sans l'autre laisse un retrait que personne n'assume."
            )
        if self.comptes is not None and not self.comptes:
            raise ValueError(
                f"{self.identifiant} : une désignation vide n'autorise personne. "
                "Employer `None` pour dire « tous les comptes du mandataire »."
            )
        return self

    def en_vigueur(self, a_la_date: date) -> bool:
        if a_la_date < self.debut:
            return False
        if self.revoque_le is not None and a_la_date >= self.revoque_le:
            return False
        return self.fin is None or a_la_date < self.fin

    def couvre(self, role: Role) -> bool:
        return role in self.roles

    def designe(self, compte: str) -> bool:
        return self.comptes is None or compte in self.comptes


def mandat_applicable(
    mandats: list[Mandat],
    *,
    mandataire: str,
    mandant: str,
    compte: str,
    role: Role,
    a_la_date: date,
) -> tuple[Mandat | None, RefusDeMandat | None]:
    """Le mandat qui autorise ce geste, ou le motif du refus.

    Rend le motif **le plus avancé** rencontré : si un mandat existe mais ne couvre pas le
    rôle, on le dit plutôt que de répondre « aucun ». C'est ce qui permet à un
    administrateur de comprendre qu'il faut élargir un mandat, et non en créer un second.

    ⚠️ Ce motif sert au **diagnostic et au journal**, jamais à la réponse rendue au
    demandeur : celle-ci reste 404 dans tous les cas.
    """
    if mandant == mandataire:
        # Agir chez soi ne demande aucun mandat. Le dire ici évite à chaque appelant de
        # se poser la question, et évite surtout qu'un oubli rende l'accès impossible.
        return None, None

    candidats = [m for m in mandats if m.mandant == mandant and m.mandataire == mandataire]
    if not candidats:
        return None, RefusDeMandat.AUCUN

    refus = RefusDeMandat.AUCUN
    for mandat in candidats:
        if not mandat.en_vigueur(a_la_date):
            refus = _plus_avance(
                refus,
                RefusDeMandat.REVOQUE if mandat.revoque_le is not None else RefusDeMandat.EXPIRE,
            )
            continue
        if not mandat.couvre(role):
            refus = _plus_avance(refus, RefusDeMandat.ROLE_NON_COUVERT)
            continue
        if not mandat.designe(compte):
            refus = _plus_avance(refus, RefusDeMandat.COMPTE_NON_DESIGNE)
            continue
        return mandat, None

    return None, refus


#: Du plus vague au plus précis. Un mandat expiré en dit moins qu'un rôle non couvert.
_RANG_REFUS: dict[RefusDeMandat, int] = {
    RefusDeMandat.AUCUN: 0,
    RefusDeMandat.EXPIRE: 1,
    RefusDeMandat.REVOQUE: 2,
    RefusDeMandat.ROLE_NON_COUVERT: 3,
    RefusDeMandat.COMPTE_NON_DESIGNE: 4,
}


def _plus_avance(gauche: RefusDeMandat, droite: RefusDeMandat) -> RefusDeMandat:
    return droite if _RANG_REFUS[droite] > _RANG_REFUS[gauche] else gauche
