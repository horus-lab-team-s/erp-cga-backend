"""Ports du contexte E · Comptabilité SYSCOHADA.

**Inversion de dépendance.** Le cas d'usage a besoin d'un plan de comptes, de
journaux et d'un registre d'écritures ; il ne doit pas savoir où ils sont rangés.
Les ports sont déclarés ici, dans le cercle le plus interne ; les adaptateurs qui
les réalisent vivent dans le cercle 3 et dépendent de ces interfaces — jamais
l'inverse.

Trois remarques de conception :

* Le **plan comptable de référence** appartient au contexte A · Référentiel, qui
  le versionne au même titre qu'un paramètre légal. La comptabilité déclare
  malgré tout son propre port : elle exprime ce dont elle a besoin, et un
  adaptateur ira le chercher chez A. C'est ce qui permettra de brancher un plan
  importé, un plan en base ou un plan de test sans toucher au métier.

* Il n'existe **aucun port d'écriture vers la conformité**. La comptabilité ne
  demande jamais un contrôle : elle reçoit un rapport déjà produit. C'est le sens
  de l'arête `comptabilite → conformite` et non l'inverse.

* `prochain_numero` appartient au dépôt et non au domaine, parce que la
  continuité d'une séquence est une propriété du registre entier, pas d'une
  écriture prise isolément.
"""

from __future__ import annotations

from typing import Protocol

from app.contextes.comptabilite.domaine.entites import Compte, EcritureComptable, Journal
from app.contextes.comptabilite.domaine.imputation import PlanImputation
from app.contextes.comptabilite.domaine.lettrage import LettrageDeLignes
from app.contextes.comptabilite.domaine.rapprochement import RapprochementBancaire
from app.contextes.comptabilite.domaine.revue import RevueDeDossier

__all__ = [
    "DepotEcritures",
    "DepotJournaux",
    "DepotPlanComptable",
    "DepotPlanImputation",
    "DepotRapprochements",
    "DepotRevues",
]


class DepotPlanComptable(Protocol):
    """Source du plan de comptes, quelle qu'elle soit."""

    def charger(self) -> list[Compte]:
        """Rend l'intégralité des comptes, de référence et dérivés.

        Le chargement est global et non paginé : un plan compte quelques
        centaines d'entrées et il est lu à chaque imputation. Le garder en
        mémoire coûte moins qu'un aller-retour par ligne d'écriture.
        """
        ...


class DepotJournaux(Protocol):
    """Source des journaux ouverts pour l'entreprise."""

    def charger(self) -> list[Journal]: ...


class DepotEcritures(Protocol):
    """Registre des écritures.

    Trois garanties sont attendues de toute réalisation, et elles ne sont pas
    négociables :

    1. **Aucune suppression.** La méthode n'existe pas, et c'est délibéré.
    2. **Aucune modification d'une écriture validée.** `enregistrer` refuse
       d'écraser une écriture déjà validée.
    3. **Une numérotation continue** par exercice et par journal, sans trou ni
       doublon, même en cas d'accès concurrent.
    """

    def enregistrer(self, ecriture: EcritureComptable) -> None:
        """Écrit une écriture, ou lève si elle en écraserait une validée."""
        ...

    def lire(self, cle: str) -> EcritureComptable:
        """Rend l'écriture portant cette clé, ou lève.

        Jamais `None` : une écriture désignée par une clé et introuvable est une
        rupture de traçabilité, pas un cas limite.
        """
        ...

    def lister(self, exercice: str, journal: str | None = None) -> list[EcritureComptable]:
        """Rend les écritures d'un exercice, éventuellement d'un seul journal.

        L'ordre est celui du numéro dans chaque journal — c'est-à-dire l'ordre
        chronologique de saisie, qui est celui du journal papier.
        """
        ...

    def prochain_numero(self, exercice: str, journal: str) -> int:
        """Rend le numéro à attribuer à la prochaine écriture de ce journal.

        La continuité de la séquence dépend de cette méthode : deux appels
        concurrents ne doivent jamais rendre le même numéro, faute de quoi un
        doublon apparaîtrait — aussi grave qu'un trou.
        """
        ...


class DepotPlanImputation(Protocol):
    """Source des règles d'imputation d'un dossier.

    Elles sont propres à chaque adhérent : une entreprise de BTP et une clinique
    n'imputent pas les mêmes achats sur les mêmes comptes. D'où un plan par
    dossier, et non un plan unique pour le cabinet.
    """

    def charger(self, entreprise: str) -> PlanImputation:
        """Rend le plan d'imputation de ce dossier.

        Lève plutôt que de rendre un plan vide : imputer tout un flux entrant sur
        un compte par défaut inventé produirait une comptabilité qu'il faudrait
        entièrement reprendre.
        """
        ...


#: ⚠️ Pas 104 : ce port vivait dans `application/rapprochement.py`.
#: `test_conformite_des_ports` ne lit que `domaine/ports.py` : ses réalisations en
#: mémoire et SQL n'étaient jamais confrontées.
class DepotRapprochements(Protocol):
    def du_dossier(self, dossier: str) -> list[RapprochementBancaire]:
        """Du plus ancien au plus récent (par fin de période)."""
        ...

    def enregistrer(self, rapprochement: RapprochementBancaire) -> None: ...


#: ⚠️ Pas 104 : ce port vivait dans `application/revue.py`.
#: `test_conformite_des_ports` ne lit que `domaine/ports.py` : ses réalisations en
#: mémoire et SQL n'étaient jamais confrontées.
class DepotRevues(Protocol):
    def du_dossier(self, dossier: str) -> list[RevueDeDossier]: ...

    def toutes(self) -> list[RevueDeDossier]:
        """Toutes les revues du cabinet : la file du réviseur les parcourt."""
        ...

    def enregistrer(self, revue: RevueDeDossier) -> None: ...


class DepotLettrages(Protocol):
    """Les lettrages d'un cabinet (pas 108). Un lettrage défait reste : sa lettre ne se
    réutilise pas, et l'on doit pouvoir dire ce qui avait été apparié."""

    def du_compte(self, dossier: str, exercice: str, compte: str) -> list[LettrageDeLignes]:
        """Tous, défaits compris, dans l'ordre de leur lettre."""
        ...

    def du_dossier(self, dossier: str, exercice: str) -> list[LettrageDeLignes]: ...

    def enregistrer(self, lettrage: LettrageDeLignes) -> None: ...
