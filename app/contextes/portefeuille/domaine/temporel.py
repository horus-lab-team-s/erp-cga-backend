"""Le statut daté : le patron qui structure tout le contexte B.

─────────────────────────────────────────────────────────────────────────────────
LE PROBLÈME QU'IL RÉSOUT

Une entreprise n'**est** pas au régime du réel. Elle **y est depuis une date**,
pour un motif donné, et elle peut en sortir.

Modéliser le régime comme une colonne `regime` dans une table est une erreur qui
ne se voit pas la première année et devient irréparable la troisième : le jour où
l'on contrôle une facture de 2024 pour une entreprise passée au réel en 2025, le
système applique les règles du réel à une opération qui relevait du synthétique.
Le rapport est faux, et il est faux **silencieusement**.

C'est le troisième des huit pièges du dossier de vision — « traiter le régime
fiscal comme un attribut figé » — et c'est exactement le même raisonnement que
celui qui interdit d'écrire une valeur légale en dur : **une donnée légale est une
fonction du temps, pas une constante.**

CE QUE CE MODULE FOURNIT

Une période `[debut, fin[`, borne haute exclue comme au référentiel, et deux
vérifications de succession. La distinction entre les deux compte :

* le régime et le rattachement forment une suite **continue** — une entreprise a
  toujours un régime, du jour de sa création à aujourd'hui, sans interruption ;
* l'adhésion admet des **trous** — un adhérent peut partir et revenir, et il ne
  faut surtout pas boucher le vide, sinon on lui accorderait rétroactivement des
  avantages fiscaux qu'il n'avait pas.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

__all__ = [
    "MotifChangement",
    "Periode",
    "resoudre",
    "ecart_d_histoire",
    "verifier_succession",
]


class MotifChangement(StrEnum):
    """Pourquoi un statut a changé.

    Le motif n'est pas décoratif : c'est lui qui permet de distinguer un
    reclassement subi d'une option volontaire, et donc de savoir si la période
    probatoire s'applique. Sans lui, on ne pourrait pas répondre à la question
    « cette entreprise a-t-elle le droit de redescendre au synthétique ? ».
    """

    CREATION = "CREATION"
    ADHESION = "ADHESION"
    DEPASSEMENT_SEUIL = "DEPASSEMENT_SEUIL"
    RECLASSEMENT_AUTOMATIQUE = "RECLASSEMENT_AUTOMATIQUE"
    OPTION = "OPTION"
    RETOUR_APRES_PROBATOIRE = "RETOUR_APRES_PROBATOIRE"
    DECISION_ADMINISTRATION = "DECISION_ADMINISTRATION"
    RESILIATION = "RESILIATION"
    CORRECTION = "CORRECTION"


class Periode(BaseModel):
    """Un intervalle `[debut, fin[` — borne haute **exclue**.

    Même convention qu'au référentiel normatif, et pour la même raison : sans
    elle, le dernier jour d'une période appartiendrait à deux statuts à la fois.
    Une entreprise dont le régime change le 1er janvier 2026 relève encore de
    l'ancien le 31 décembre 2025.

    `fin` à `None` signifie « toujours en cours ».
    """

    model_config = ConfigDict(frozen=True)

    debut: date
    fin: date | None = None
    motif: MotifChangement

    #: Texte libre : la référence de l'acte, de la notification, ou la précision
    #: qui rendra la décision compréhensible dans trois ans.
    precision: str | None = None

    @model_validator(mode="after")
    def _bornes_coherentes(self) -> Periode:
        if self.fin is not None and self.fin <= self.debut:
            raise ValueError(
                f"période incohérente : fin {self.fin} antérieure ou égale au début "
                f"{self.debut}. Une période de durée nulle n'a jamais existé."
            )
        return self

    @property
    def ouverte(self) -> bool:
        return self.fin is None

    def couvre(self, a_la_date: date) -> bool:
        if a_la_date < self.debut:
            return False
        return self.fin is None or a_la_date < self.fin


def verifier_succession(
    periodes: Sequence[Periode],
    *,
    quoi: str,
    continue_: bool,
) -> list[Periode]:
    """Trie les périodes et vérifie qu'elles se succèdent correctement.

    Quatre contrôles, dans cet ordre :

    1. aucune période ne se chevauche ;
    2. une seule période est ouverte, et c'est la dernière ;
    3. si `continue_`, il n'y a **aucun trou** entre deux périodes consécutives ;
    4. deux périodes ne commencent pas le même jour.

    Rend la liste triée par date de début. Lève avec un message qui dit **où**
    est le défaut, pas seulement qu'il y en a un : la personne qui reprend un
    dossier repris d'un autre cabinet doit pouvoir corriger sans deviner.
    """
    if not periodes:
        return []

    triees = sorted(periodes, key=lambda p: p.debut)

    ouvertes = [p for p in triees if p.ouverte]
    if len(ouvertes) > 1:
        raise ValueError(
            f"{quoi} : {len(ouvertes)} périodes sont ouvertes en même temps "
            f"(débuts {', '.join(str(p.debut) for p in ouvertes)}). "
            "Fermer les précédentes avant d'en ouvrir une nouvelle."
        )
    if ouvertes and triees[-1] is not ouvertes[0]:
        raise ValueError(
            f"{quoi} : la période ouverte commence le {ouvertes[0].debut} alors qu'une "
            "période postérieure existe. Une période ouverte est nécessairement la dernière."
        )

    for precedente, suivante in zip(triees, triees[1:], strict=False):
        if precedente.debut == suivante.debut:
            raise ValueError(
                f"{quoi} : deux périodes commencent le {suivante.debut}. "
                "Un statut ne change pas deux fois le même jour."
            )
        if precedente.fin is None:
            raise ValueError(
                f"{quoi} : la période du {precedente.debut} n'est pas fermée alors qu'une "
                f"autre commence le {suivante.debut}."
            )
        if precedente.fin > suivante.debut:
            raise ValueError(
                f"{quoi} : chevauchement entre la période close le {precedente.fin} et "
                f"celle ouverte le {suivante.debut}."
            )
        if continue_ and precedente.fin < suivante.debut:
            raise ValueError(
                f"{quoi} : trou entre le {precedente.fin} et le {suivante.debut}. "
                "Ce statut doit être continu — une entreprise en relève à chaque instant "
                "de son existence, et une lecture dans le trou ne rendrait rien."
            )

    return triees


def resoudre(periodes: Sequence[Periode], a_la_date: date) -> Periode | None:
    """La période qui couvre cette date, ou `None`.

    Rendre `None` plutôt que lever est ici correct, à la différence du
    référentiel : une entreprise peut légitimement n'avoir jamais adhéré, et
    l'absence d'adhésion est une réponse, pas une erreur. C'est à l'appelant de
    décider si l'absence est acceptable — et les entités de ce contexte le font
    explicitement, chacune selon sa nature.
    """
    for periode in periodes:
        if periode.couvre(a_la_date):
            return periode
    return None


def ecart_d_histoire(
    avant: Sequence[Periode], apres: Sequence[Periode], *, quoi: str
) -> str | None:
    """Ce qui, dans la nouvelle histoire, n'est pas un simple prolongement de l'ancienne.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LE CONTENU COMPTE, PAS SEULEMENT LE NOMBRE.**

    Le garde-fou des dépôts ne comparait que le nombre de périodes, et sa
    documentation le disait. Tant qu'aucune route n'écrivait dans le portefeuille,
    c'était sans conséquence. Dès qu'une route écrit, une mise à jour qui garde
    trois périodes mais fait passer la première de l'IGS au réel passerait : même
    nombre, histoire réécrite, et les factures de 2021 se retrouveraient contrôlées
    sous un régime qu'elles n'ont jamais connu.

    UN PROLONGEMENT, C'EST TROIS CHOSES ET RIEN D'AUTRE

        une période close   se retrouve à l'identique
        la période ouverte  se retrouve à l'identique, ou reçoit une date de fin
        après elles         de nouvelles périodes peuvent s'ajouter

    Rend `None` quand l'histoire est prolongée, et sinon une phrase qui dit **où**
    elle ne l'est pas : la personne qui reprend un dossier doit pouvoir corriger
    sans deviner.
    ─────────────────────────────────────────────────────────────────────────────
    """
    anciennes = sorted(avant, key=lambda p: p.debut)
    nouvelles = sorted(apres, key=lambda p: p.debut)

    if len(nouvelles) < len(anciennes):
        return (
            f"{quoi} : l'enregistrement ferait passer les périodes de "
            f"{len(anciennes)} à {len(nouvelles)}. Un statut daté ne se remplace pas, "
            "il s'ajoute."
        )

    for ancienne, nouvelle in zip(anciennes, nouvelles, strict=False):
        if ancienne.fin is not None:
            if nouvelle != ancienne:
                return (
                    f"{quoi} : la période close [{ancienne.debut} → {ancienne.fin}[ "
                    "serait réécrite. Une période close est l'histoire du dossier : "
                    "c'est sous ce statut-là que les pièces de l'époque ont été "
                    "contrôlées."
                )
        elif nouvelle.model_copy(update={"fin": None}) != ancienne:
            # ⚠️ `model_copy` est employé ici en **lecture**, pour comparer, jamais
            # pour produire une entité : aucun invariant n'est en jeu, et la copie
            # ne sort pas de cette ligne.
            return (
                f"{quoi} : la période ouverte depuis le {ancienne.debut} serait "
                "modifiée au-delà de sa date de fin. Seule sa fermeture est admise."
            )
    return None
