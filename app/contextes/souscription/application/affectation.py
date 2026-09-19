"""L'étape 2 du parcours : désigner un responsable.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE MODULE ORCHESTRE, ET CE QU'IL NE DÉCIDE PAS

Il ne décide pas **qui**. Cela, c'est la grille du référentiel, évaluée par le
moteur, dans `domaine/affectation.py`. Ce module se contente d'enchaîner :

    lire les candidatures  ->  choisir  ->  poser l'affectation sur le dossier
                                    |
                                    +->  personne : file de pôle et alerte

La séparation vaut d'être tenue. Le jour où le centre change sa règle de routage,
rien ici ne bouge. Le jour où l'on change ce qu'on fait d'une demande sans
destinataire, la grille ne bouge pas non plus.

⚠️ UNE DEMANDE SANS DESTINATAIRE EST UN PROBLÈME VISIBLE, PAS UNE LIGNE OUBLIÉE

C'est la règle du document de conception, et elle a une conséquence de code
précise : `affecter_le_dossier` ne lève pas quand personne ne convient. Il rend un
résultat qui **dit** que personne ne convient, avec les empêchements rencontrés,
et le dossier reste à l'état `DÉPOSÉE`.

Le dossier qui reste `DÉPOSÉE` est déjà, en soi, le signalement : la veille des
deux heures le remontera. On n'invente donc pas un état « en file de pôle », qui
ferait sortir le dossier du champ de l'alerte qui doit précisément le voir.

LA RÉAFFECTATION PASSE PAR LA MÊME GRILLE

Et c'est ce qui rend le mécanisme utile plutôt que décoratif. Après vingt-quatre
heures sans contact, on rejoue le choix en **excluant celui qui n'a pas rappelé**.
Sans cette exclusion, la grille redésignerait le même, puisque rien n'a changé
dans ses critères, et la réaffectation automatique tournerait en rond jusqu'à la
limite du dossier.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.contextes.souscription.domaine.affectation import (
    Candidature,
    Choix,
    RegleDAffectation,
    Verdict,
    choix_parmi,
    classer,
)
from app.contextes.souscription.domaine.dossier_commercial import DossierCommercial

__all__ = ["ResultatAffectation", "affecter_le_dossier", "reaffecter_le_dossier"]


class ResultatAffectation(BaseModel):
    """Le dossier après passage, et de quoi expliquer ce qui s'est passé.

    Les verdicts sont portés ici et non dans le `Choix`, parce qu'ils existent
    aussi — et surtout — quand personne n'est retenu.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    dossier: DossierCommercial
    #: `None` quand personne ne convenait. Le dossier est alors inchangé.
    choix: Choix | None
    #: Le classement complet, du meilleur au moins bon, retenu compris.
    verdicts: tuple[Verdict, ...] = ()

    @property
    def affecte(self) -> bool:
        return self.choix is not None

    @property
    def empechements(self) -> tuple[str, ...]:
        """Pourquoi personne ne convenait, dédoublonné, dans l'ordre rencontré.

        C'est ce qu'affiche l'alerte au responsable de pôle. Lui rendre la liste
        complète des verdicts l'obligerait à la lire ; lui rendre « aucun
        responsable disponible » ne lui dirait pas quoi faire. Les empêchements
        distincts lui disent s'il manque une compétence ou des bras.
        """
        vus: list[str] = []
        for verdict in self.verdicts:
            for empechement in verdict.empechements:
                if empechement not in vus:
                    vus.append(empechement)
        return tuple(vus)

    @property
    def echecs(self) -> tuple[str, ...]:
        """Les règles qui n'ont pas pu être évaluées, tous candidats confondus.

        Remontées jusqu'ici parce qu'une grille cassée doit se voir au moment où
        elle route, et non le jour où l'on cherchera pourquoi tout le monde a
        atterri chez la même personne. Un critère silencieusement absent est
        indiscernable d'un critère satisfait.
        """
        vus: list[str] = []
        for verdict in self.verdicts:
            for echec in verdict.echecs:
                if echec not in vus:
                    vus.append(echec)
        return tuple(vus)


def affecter_le_dossier(
    dossier: DossierCommercial,
    candidatures: Sequence[Candidature],
    regles: Sequence[RegleDAffectation],
    a_l_instant: datetime,
    *,
    a_la_date: date | None = None,
) -> ResultatAffectation:
    """Désigne un responsable, ou constate que personne ne convient.

    `a_la_date` sépare la date de **vigueur des règles** de l'instant de
    l'affectation. Elles coïncident en exploitation, et diffèrent quand on rejoue
    une décision passée pour l'expliquer : rejouer avec la grille d'aujourd'hui
    donnerait la bonne réponse d'aujourd'hui à une question d'il y a six mois.
    """
    verdicts = classer(candidatures, regles, a_la_date or a_l_instant.date())
    choix = choix_parmi(verdicts)
    if choix is None:
        return ResultatAffectation(dossier=dossier, choix=None, verdicts=verdicts)
    return ResultatAffectation(
        dossier=dossier.affecter(choix.responsable, a_l_instant, motif=choix.motif),
        choix=choix,
        verdicts=verdicts,
    )


def reaffecter_le_dossier(
    dossier: DossierCommercial,
    candidatures: Sequence[Candidature],
    regles: Sequence[RegleDAffectation],
    a_l_instant: datetime,
    *,
    a_la_date: date | None = None,
) -> ResultatAffectation:
    """Passe la main, en écartant **tous** ceux qui l'ont déjà eue.

    ─────────────────────────────────────────────────────────────────────────────
    Sans exclusion, la grille redésignerait le même : rien n'a changé dans ses
    critères, et la réaffectation consommerait ses trois tours sans que personne
    de nouveau ne soit prévenu.

    ⚠️ **Écarter le seul titulaire du moment ne suffisait pas**, et c'était le
    défaut : la grille choisit le moins chargé, et celui qui vient de rendre le
    dossier redevient aussitôt le moins chargé. Mesuré sur cinq collaborateurs
    équivalents, les trois reprises allaient à deux d'entre eux — alpha, beta,
    alpha, beta — les trois autres n'étant jamais sollicités.

    Le refus du domaine nommait déjà le symptôme sans l'empêcher : « au-delà, la
    règle d'affectation tourne en rond ».

    Écarter ici plutôt que dans la grille reste délibéré : « ce n'est pas
    quelqu'un qui a déjà laissé passer son délai » n'est pas un critère de
    routage, c'est une propriété de ce geste-ci. L'écrire au référentiel
    obligerait chaque règle à connaître l'historique du dossier.

    ⚠️ **Quand tout le monde a déjà eu la main, rien n'est affecté**, et c'est le
    bon résultat : le dossier reste où il est et la veille le remontera à un
    humain. Une plateforme qui recommencerait le tour ferait passer un dossier
    trois fois entre les mêmes mains en prétendant chercher quelqu'un.
    ─────────────────────────────────────────────────────────────────────────────
    """
    deja_vus = {dossier.responsable, *dossier.responsables_passes}
    restants = [c for c in candidatures if c.responsable not in deja_vus]
    verdicts = classer(restants, regles, a_la_date or a_l_instant.date())
    choix = choix_parmi(verdicts)
    if choix is None:
        return ResultatAffectation(dossier=dossier, choix=None, verdicts=verdicts)
    return ResultatAffectation(
        dossier=dossier.reaffecter(choix.responsable, a_l_instant, motif=choix.motif),
        choix=choix,
        verdicts=verdicts,
    )
