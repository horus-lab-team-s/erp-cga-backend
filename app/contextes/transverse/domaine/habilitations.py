"""L'habilitation datée : qui pouvait quoi, sur quels dossiers, et à partir de quand.

─────────────────────────────────────────────────────────────────────────────────
LE MÊME PATRON QUE LE STATUT DATÉ DU CONTEXTE B, POUR UNE RAISON PLUS FORTE ENCORE

Une entreprise n'est pas au réel, elle y est depuis une date. Une personne n'est
pas réviseur, elle l'est depuis une date, sur un périmètre donné, et elle peut
cesser de l'être.

L'enjeu dépasse ici la justesse d'un calcul. Quand un vérificateur demande qui a
déposé la déclaration de mars, la réponse doit être **celle qui était vraie en
mars**. Un rôle stocké en colonne ne connaît que le présent : le jour où l'on
retire son rôle à un collaborateur qui part, toutes ses déclarations passées
apparaîtraient comme déposées par une personne non habilitée. Le cabinet aurait
fabriqué lui-même la preuve de son propre manquement.

**On ne supprime donc jamais une habilitation. On la ferme.** `fin` reçoit une
date, la ligne demeure, et l'historique reste lisible. La méthode `retirer`
n'existe pas dans le port : c'est délibéré.

LA PORTÉE EST UNE LISTE DE NIU, PAS UNE LISTE D'ENTREPRISES

Le contexte K appartient au **socle** : il est lisible par tous les contextes
métier et n'en lit aucun — sans quoi le graphe de dépendances contiendrait un
cycle, et douze contextes n'en feraient plus qu'un.

Un dossier est donc désigné ici par son NIU, une chaîne. K ne sait pas ce qu'est
une `Entreprise`, ne sait pas résoudre son régime, et n'a pas à le savoir : la
question qu'il tranche est « cette personne a-t-elle le droit d'ouvrir ce
dossier », et elle se répond sur un identifiant.

`portee = None` signifie **tous les dossiers du cabinet**. C'est le rôle
transverse dont parle 05-securite-multitenant.md § 1, et il est réservé : deux
rôles ne peuvent jamais l'obtenir, l'adhérent et l'inspecteur. Un adhérent dont la
portée serait universelle lirait la comptabilité de ses concurrents — c'est la
faute la plus coûteuse que ce contexte puisse laisser passer, et elle est vérifiée
à la construction plutôt qu'au moment de la lecture.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.contextes.transverse.domaine.roles import (
    ROLES_A_PORTEE_OBLIGATOIRE,
    Permission,
    Role,
    permissions_de,
)
from app.partage.copie import transiter

__all__ = [
    "Habilitation",
    "MotifHabilitation",
    "dossiers_accessibles",
    "habilitations_actives",
    "permissions_au",
    "roles_au",
]


class MotifHabilitation(StrEnum):
    """Pourquoi une habilitation s'ouvre ou se ferme.

    Distinct de `MotifChangement` du contexte B, et pas par oubli de
    factorisation : un régime fiscal change pour dépassement de seuil ou sur
    option, une habilitation change pour un recrutement ou un départ. Ce sont les
    mêmes formes et deux vocabulaires métier différents ; les fondre produirait une
    énumération où « DEPASSEMENT_SEUIL » serait proposé à un administrateur qui
    nomme un comptable.
    """

    RECRUTEMENT = "RECRUTEMENT"
    SOUSCRIPTION = "SOUSCRIPTION"
    CHANGEMENT_DE_POSTE = "CHANGEMENT_DE_POSTE"
    AFFECTATION_DOSSIER = "AFFECTATION_DOSSIER"
    REMPLACEMENT = "REMPLACEMENT"
    MISSION_CONTROLE = "MISSION_CONTROLE"
    DEPART = "DEPART"
    RESILIATION = "RESILIATION"
    FIN_DE_MISSION = "FIN_DE_MISSION"
    CORRECTION = "CORRECTION"


class Habilitation(BaseModel):
    """Un rôle tenu par un compte, sur un périmètre, entre deux dates.

    Intervalle `[debut, fin[`, borne haute exclue — même convention que le
    référentiel et que le contexte B. Sans elle, le dernier jour d'une habilitation
    appartiendrait à deux périmètres à la fois.
    """

    model_config = ConfigDict(frozen=True)

    identifiant: str = Field(min_length=1)
    compte: str = Field(min_length=1)
    role: Role

    #: `None` = tous les dossiers du cabinet. Voir l'en-tête.
    portee: frozenset[str] | None = None

    debut: date
    fin: date | None = None
    motif: MotifHabilitation

    #: Qui a accordé. Un rôle qui s'accorde sans nom n'engage personne, et c'est
    #: la première chose qu'un audit d'accès réclame.
    accordee_par: str = Field(min_length=1)

    #: Texte libre : référence de la note de service, du contrat, de la lettre de
    #: mission — ce qui rendra la décision compréhensible dans trois ans.
    precision: str | None = None

    @model_validator(mode="after")
    def _coherence(self) -> Habilitation:
        if self.fin is not None and self.fin <= self.debut:
            raise ValueError(
                f"habilitation {self.identifiant} : fin {self.fin} antérieure ou égale "
                f"au début {self.debut}. Une habilitation de durée nulle n'a jamais existé."
            )
        if self.role in ROLES_A_PORTEE_OBLIGATOIRE and not self.portee:
            raise ValueError(
                f"le rôle {self.role} exige une portée explicite. Une habilitation "
                f"{self.role} sans portée donnerait accès à l'ensemble du portefeuille "
                "du cabinet — voir l'en-tête de habilitations.py."
            )
        return self

    # ── Ce que l'habilitation sait dire d'elle-même ─────────────────────────

    @computed_field
    @property
    def transverse(self) -> bool:
        """Porte sur tous les dossiers. Sérialisé : l'écran d'administration
        affiche « tout le portefeuille » plutôt qu'une liste vide, et ces deux
        libellés ne doivent pas se confondre."""
        return self.portee is None

    @computed_field
    @property
    def permissions(self) -> list[Permission]:
        return sorted(permissions_de(frozenset({self.role})))

    def couvre(self, a_la_date: date) -> bool:
        if a_la_date < self.debut:
            return False
        return self.fin is None or a_la_date < self.fin

    def couvre_dossier(self, niu: str) -> bool:
        return self.portee is None or niu in self.portee

    # ── Transitions ─────────────────────────────────────────────────────────

    def fermer(self, le: date, *, motif: MotifHabilitation, par: str) -> Habilitation:
        """Ferme l'habilitation. Ne la supprime pas — voir l'en-tête."""
        if self.fin is not None:
            raise ValueError(
                f"habilitation {self.identifiant} déjà fermée le {self.fin}. "
                "Rouvrir puis refermer masquerait un intervalle d'accès."
            )
        return transiter(
            self,
            fin=le,
            motif=motif,
            precision=f"fermée par {par}" + (f" — {self.precision}" if self.precision else ""),
        )

    def affecter(
        self, niu: str, *, le: date, identifiant_successeur: str, par: str
    ) -> tuple[Habilitation, ...]:
        """Ajoute un dossier à la portée **à compter d'une date**, sans réécrire le passé.

        ─────────────────────────────────────────────────────────────────────
        ⚠️ POURQUOI CE N'EST PLUS UN SIMPLE AJOUT (pas 70)

        La portée n'est pas datée : elle vaut pour tout l'intervalle de
        l'habilitation. La première version ajoutait le NIU à la portée existante,
        et un dossier confié le 14 septembre 2026 au comptable C-004, recruté en
        2021, le faisait apparaître **habilité sur ce dossier depuis 2021**.
        `GET /dossiers/{niu}/acces?a_la_date=2024-01-10` le citait. Or c'est
        précisément la question que l'historique des habilitations existe pour
        trancher : qui était habilité le jour d'un dépôt.

        La règle, désormais :

            l'habilitation commence le jour même ou plus tard
                → la portée s'étend sur place : il n'y a aucun passé à réécrire
            l'habilitation a déjà couru
                → elle est fermée la veille au soir (fin = le, borne exclue),
                  et une habilitation successeur s'ouvre le jour même,
                  avec l'ancienne portée plus le dossier et la même fin prévue

        On rend **les habilitations enregistrées**, dans l'ordre : une seule
        (étendue), ou deux (la fermée, puis la successeur).

        ⚠️ QUATRE REFUS, CHACUN POUR UNE FAUTE PRÉCISE

        - **transverse** : y « ajouter » un dossier donnerait l'illusion d'un
          périmètre restreint alors qu'il reste universel ;
        - **adhérent ou inspecteur** : leur périmètre suit une souscription ou une
          mission. Ajouter un NIU à un adhérent lui ouvrirait la comptabilité d'une
          autre entreprise, la faute que l'en-tête de ce module appelle la plus
          coûteuse ;
        - **fermée à cette date** : l'étendre rouvrirait en silence un accès
          terminé, sur tout son passé ;
        - **dossier déjà dans la portée** : le journal dirait qu'on a affecté ce
          qui l'était déjà.
        ─────────────────────────────────────────────────────────────────────
        """
        if self.portee is None:
            raise ValueError(
                f"habilitation {self.identifiant} déjà transverse : elle couvre tous "
                "les dossiers, en ajouter un ne restreint rien."
            )
        if self.role in ROLES_A_PORTEE_OBLIGATOIRE:
            raise ValueError(
                f"habilitation {self.identifiant} de rôle {self.role} : son périmètre suit "
                "une souscription ou une mission, il ne s'étend pas par affectation. "
                "Ouvrir un autre dossier à cette personne lui donnerait la comptabilité "
                "d'une autre entreprise."
            )
        relayee = self.motif is MotifHabilitation.AFFECTATION_DOSSIER
        if self.fin is not None and self.fin <= le and relayee:
            # ⚠️ Relayée par une affectation antérieure : elle n'est pas « terminée »,
            # l'accès continue dans sa successeur. Dire « ouvrir une nouvelle
            # habilitation » ferait créer un doublon du rôle.
            raise ValueError(
                f"habilitation {self.identifiant} relayée le {self.fin:%d/%m/%Y} "
                f"({self.precision}) : confier le dossier à l'habilitation active du compte."
            )
        if self.fin is not None and self.fin <= le:
            raise ValueError(
                f"habilitation {self.identifiant} fermée le {self.fin:%d/%m/%Y} : "
                "l'étendre rouvrirait un accès terminé. Ouvrir une nouvelle habilitation."
            )
        if niu in self.portee:
            raise ValueError(
                f"le dossier {niu} est déjà dans la portée de l'habilitation {self.identifiant}."
            )

        if self.debut >= le:
            return (transiter(self, portee=self.portee | {niu}),)

        fermee = transiter(
            self,
            fin=le,
            motif=MotifHabilitation.AFFECTATION_DOSSIER,
            precision=f"relayée par {identifiant_successeur} le {le:%d/%m/%Y}"
            + (f" · {self.precision}" if self.precision else ""),
        )
        successeur = Habilitation(
            identifiant=identifiant_successeur,
            compte=self.compte,
            role=self.role,
            portee=self.portee | {niu},
            debut=le,
            fin=self.fin,
            motif=MotifHabilitation.AFFECTATION_DOSSIER,
            accordee_par=par,
            precision=f"succède à {self.identifiant}, dossier {niu} ajouté"
            + (f" · {self.precision}" if self.precision else ""),
        )
        return (fermee, successeur)

    def retirer(
        self, niu: str, *, le: date, identifiant_successeur: str, par: str
    ) -> tuple[Habilitation, ...]:
        """Retire un dossier de la portée **à compter d'une date** (pas 105).

        Le symétrique exact d'`affecter`, pour la même raison : la portée n'est pas datée. Retirer
        le NIU sur place ferait dire à l'historique que le collaborateur **n'a jamais** suivi le
        dossier, alors qu'il l'a tenu jusqu'à la veille ; or c'est précisément ce qu'on lui
        demandera le jour d'un contrôle.

            l'habilitation commence le jour même ou plus tard → la portée se réduit sur place
            l'habilitation a déjà couru → fermée à cette date, et une successeur s'ouvre le
                                          jour même, avec l'ancienne portée moins le dossier

        ⚠️ Les mêmes refus qu'`affecter` : transverse (on ne retire rien de « tout »), adhérent ou
        inspecteur (leur périmètre suit une souscription ou une mission), fermée, et dossier
        absent de la portée.
        """
        if self.portee is None:
            raise ValueError(
                f"habilitation {self.identifiant} transverse : elle couvre tous les dossiers, "
                "on ne lui en retire pas un."
            )
        if self.role in ROLES_A_PORTEE_OBLIGATOIRE:
            raise ValueError(
                f"habilitation {self.identifiant} de rôle {self.role} : son périmètre suit une "
                "souscription ou une mission, il ne se réduit pas par réaffectation."
            )
        if self.fin is not None and self.fin <= le:
            raise ValueError(
                f"habilitation {self.identifiant} fermée le {self.fin:%d/%m/%Y} : "
                "retirer le dossier à l'habilitation active du compte."
            )
        if niu not in self.portee:
            raise ValueError(
                f"le dossier {niu} n'est pas dans la portée de l'habilitation {self.identifiant}."
            )

        if self.debut >= le:
            return (transiter(self, portee=self.portee - {niu}),)

        fermee = transiter(
            self,
            fin=le,
            motif=MotifHabilitation.AFFECTATION_DOSSIER,
            precision=f"relayée par {identifiant_successeur} le {le:%d/%m/%Y}"
            + (f" · {self.precision}" if self.precision else ""),
        )
        successeur = Habilitation(
            identifiant=identifiant_successeur,
            compte=self.compte,
            role=self.role,
            portee=self.portee - {niu},
            debut=le,
            fin=self.fin,
            motif=MotifHabilitation.AFFECTATION_DOSSIER,
            accordee_par=par,
            precision=f"succède à {self.identifiant}, dossier {niu} retiré"
            + (f" · {self.precision}" if self.precision else ""),
        )
        return (fermee, successeur)


# ── Résolution à une date ────────────────────────────────────────────────────


def habilitations_actives(
    habilitations: Sequence[Habilitation], a_la_date: date
) -> list[Habilitation]:
    """Celles qui couvrent cette date. Triées par rôle, pour un affichage stable."""
    return sorted(
        (h for h in habilitations if h.couvre(a_la_date)),
        key=lambda h: (h.role.value, h.debut),
    )


def roles_au(habilitations: Sequence[Habilitation], a_la_date: date) -> frozenset[Role]:
    return frozenset(h.role for h in habilitations_actives(habilitations, a_la_date))


def permissions_au(habilitations: Sequence[Habilitation], a_la_date: date) -> frozenset[Permission]:
    return permissions_de(roles_au(habilitations, a_la_date))


def dossiers_accessibles(
    habilitations: Sequence[Habilitation], a_la_date: date
) -> frozenset[str] | None:
    """Les NIU lisibles à cette date, ou `None` pour « tous ».

    `None` l'emporte sur toute liste : une personne à la fois réviseur transverse
    et adhérente d'un dossier voit tout. C'est l'union des périmètres, cohérente
    avec l'union des permissions de `permissions_de` — restreindre serait plus
    surprenant qu'élargir, et un réviseur qui perdrait ses droits parce qu'on lui
    a ouvert un compte adhérent aurait toutes les raisons de croire à une panne.
    """
    actives = habilitations_actives(habilitations, a_la_date)
    if not actives:
        return frozenset()
    if any(h.portee is None for h in actives):
        return None
    return frozenset().union(*(h.portee for h in actives if h.portee is not None))
