"""Entités du contexte J · Pilotage CGA.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE CONTEXTE MODÉLISE

Ce que la direction du cabinet a besoin de savoir : quels dossiers sont à risque,
qui porte quelle charge, et où le travail est en retard.

C'est le seul contexte qui **n'est lu par personne** — `test_architecture.py` le
vérifie explicitement : « J agrège et n'est lu par personne. Si un contexte venait
à le lire, c'est qu'un indicateur y aurait pris une valeur métier, à redescendre
chez son responsable. » Un score de risque n'est pas une donnée comptable, et le
jour où la comptabilité en aurait besoin, ce ne serait plus un score.

UN SCORE SANS DÉCOMPOSITION EST UN CHIFFRE MAGIQUE

C'est la décision structurante, et elle est écrite au § 01 du dossier
d'architecture : « dossiers à risque avec score décomposé et **traçable jusqu'à
la pièce** ».

Un tableau de bord qui afficherait « AGRO-NKOLO : 72/100 » sans dire pourquoi
serait pire qu'inutile. Le directeur qui voit 72 veut savoir ce qu'il doit faire,
et « le score est élevé » n'est pas une action. Chaque composante porte donc sa
mesure, son poids, sa contribution **et les éléments qui l'ont produite** — les
références des pièces, des obligations, des demandes. On descend du chiffre au
dossier en un clic, et du dossier à la facture.

CE QU'IL NE MODÉLISE PAS, ET POURQUOI C'EST DIT

**La rentabilité par adhérent.** Le dossier de conception la demande, et elle
suppose un suivi du temps passé par collaborateur et par dossier. Ce suivi
n'existe pas — ni saisie, ni source. Fabriquer une rentabilité en divisant les
honoraires par le nombre de pièces produirait un chiffre plausible et faux, que la
direction emploierait pour arbitrer. L'absence est donc assumée et écrite plutôt
que comblée par une approximation.

**Le dossier de gestion à restituer à l'adhérent.** Il suppose une mise en forme
éditoriale et un contenu que le cabinet doit arrêter avant qu'on le code.

Aucun poids, aucun seuil en dur : ils vivent au référentiel parce que la
pondération appartient à la direction, et qu'une direction qui doit demander un
déploiement pour changer un poids ne pilote pas.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "ChargeCollaborateur",
    "Composante",
    "MesureComposante",
    "NiveauRisque",
    "ScoreRisque",
    "SeuilsInverses",
]


class SeuilsInverses(ValueError):
    """Le seuil de risque modéré dépasse celui du risque élevé.

    Volontairement une erreur et non un classement par défaut : un dossier ne peut
    pas être « modéré » au-delà du seuil « élevé ». Laisser passer produirait un
    tableau de bord où les couleurs mentent, et un tableau de bord dont les
    couleurs mentent est plus dangereux qu'aucun tableau de bord.
    """


class Composante(StrEnum):
    """Ce qui fait monter le score d'un dossier.

    Fermée volontairement. Une composante s'ajoute quand la direction décide
    qu'un fait nouveau porte du risque — et cette décision s'accompagne d'un
    poids au référentiel. Les deux vont ensemble : une composante sans poids ne
    contribuerait à rien, un poids sans composante ne mesurerait rien.
    """

    #: Anomalie bloquante non levée : la pièce ne peut pas être comptabilisée.
    ANOMALIES_BLOQUANTES = "ANOMALIES_BLOQUANTES"
    #: Obligation échue et non déposée. La seule dont le coût est chiffrable.
    RETARD_DECLARATIF = "RETARD_DECLARATIF"
    #: Pièce reçue au cabinet et non encore traitée.
    PIECES_EN_SOUFFRANCE = "PIECES_EN_SOUFFRANCE"
    #: Pièce demandée à l'adhérent et jamais reçue.
    DEMANDES_SANS_REPONSE = "DEMANDES_SANS_REPONSE"


#: Le code du paramètre qui porte le poids de chaque composante.
CODE_POIDS: dict[Composante, str] = {
    Composante.ANOMALIES_BLOQUANTES: "POIDS_RISQUE_ANOMALIES_BLOQUANTES",
    Composante.RETARD_DECLARATIF: "POIDS_RISQUE_RETARD_DECLARATIF",
    Composante.PIECES_EN_SOUFFRANCE: "POIDS_RISQUE_PIECES_EN_SOUFFRANCE",
    Composante.DEMANDES_SANS_REPONSE: "POIDS_RISQUE_DEMANDES_SANS_REPONSE",
}

#: Ce que chaque composante appelle comme geste. Affiché tel quel : un score qui
#: ne dit pas quoi faire oblige le directeur à traduire, et il traduira mal.
ACTION_ATTENDUE: dict[Composante, str] = {
    Composante.ANOMALIES_BLOQUANTES: (
        "Faire lever l'anomalie ou l'écarter avec motif — la pièce ne peut pas "
        "être comptabilisée en l'état."
    ),
    Composante.RETARD_DECLARATIF: (
        "Déposer sans attendre : les pénalités courent au mois entamé."
    ),
    Composante.PIECES_EN_SOUFFRANCE: "Affecter le traitement à un collaborateur.",
    Composante.DEMANDES_SANS_REPONSE: "Relancer l'adhérent.",
}


class NiveauRisque(StrEnum):
    """Le classement d'un dossier, dérivé du score et des seuils.

    Trois niveaux et non cinq : un tableau de bord se lit à distance, et la
    direction agit sur ce qui est rouge. Multiplier les nuances déplacerait
    l'attention vers le classement plutôt que vers le dossier.
    """

    FAIBLE = "FAIBLE"
    MODERE = "MODERE"
    ELEVE = "ELEVE"


class MesureComposante(BaseModel):
    """Une composante du score, avec ce qui l'a produite.

    ⚠️ `elements` n'est pas décoratif : c'est la traçabilité exigée au § 01.
    Ce sont les références des pièces, des obligations ou des demandes qui ont
    fait monter cette composante. Sans elles, le score dit « il y a un problème »
    et laisse chercher où.
    """

    model_config = ConfigDict(frozen=True)

    composante: Composante
    libelle: str
    #: Le nombre d'occurrences observées.
    occurrences: int = Field(ge=0)
    #: Le poids unitaire lu au référentiel.
    poids: Decimal
    #: Les références des éléments concernés — pièces, obligations, demandes.
    elements: tuple[str, ...] = ()
    #: L'action que cette composante appelle.
    action: str = ""
    #: Vrai si le poids employé n'est pas encore arrêté par la direction.
    poids_non_valide: bool = False

    @property
    def contribution(self) -> Decimal:
        """Ce que cette composante ajoute au score brut."""
        return self.poids * Decimal(self.occurrences)

    @model_validator(mode="after")
    def _les_elements_correspondent_aux_occurrences(self) -> MesureComposante:
        """Autant d'éléments que d'occurrences, ou aucun.

        ─────────────────────────────────────────────────────────────────────
        POURQUOI CET INVARIANT

        Une composante à trois occurrences qui ne cite que deux pièces laisse la
        troisième introuvable, et le directeur qui cherche conclut que l'outil se
        trompe — alors que c'est le relevé qui est incomplet. Mieux vaut refuser
        la mesure que produire une traçabilité partielle silencieuse.

        Le cas « aucun élément » reste admis : certaines composantes se comptent
        sans se nommer, et l'invariant ne doit pas interdire d'en ajouter une.
        ─────────────────────────────────────────────────────────────────────
        """
        if self.elements and len(self.elements) != self.occurrences:
            raise ValueError(
                f"{self.composante} : {self.occurrences} occurrence(s) mais "
                f"{len(self.elements)} élément(s) cité(s). Une traçabilité partielle "
                "fait chercher au mauvais endroit."
            )
        return self


class ScoreRisque(BaseModel):
    """Le score d'un dossier, décomposé.

    Le score se **calcule**, il ne se stocke pas : il découle de l'état du dossier
    au jour de la lecture. Le figer produirait un tableau de bord qui vieillit
    sans le dire, et la direction agirait sur un risque déjà levé.
    """

    model_config = ConfigDict(frozen=True)

    entreprise: str
    denomination: str
    mesures: tuple[MesureComposante, ...] = ()
    #: Les seuils employés, conservés avec le score : sans eux, le niveau ne se
    #: rejoue pas. Un score de 40 est « modéré » ou « élevé » selon le réglage,
    #: et le réglage change.
    seuil_modere: Decimal = Decimal(25)
    seuil_eleve: Decimal = Decimal(60)

    @model_validator(mode="after")
    def _seuils_ordonnes(self) -> ScoreRisque:
        if self.seuil_modere >= self.seuil_eleve:
            raise SeuilsInverses(
                f"seuil modéré {self.seuil_modere} ≥ seuil élevé {self.seuil_eleve} : "
                "un dossier ne peut pas être modéré au-delà du seuil élevé."
            )
        return self

    @property
    def total(self) -> Decimal:
        """La somme des contributions. **Non borné à cent, et c'est voulu.**

        Un dossier cumulant douze anomalies bloquantes et six retards mérite un
        score qui le dise. Le ramener à cent l'aligneraient sur un dossier qui en
        compte trois, et la direction perdrait précisément l'information qui la
        ferait agir en premier.
        """
        return sum((m.contribution for m in self.mesures), Decimal(0))

    @property
    def niveau(self) -> NiveauRisque:
        if self.total >= self.seuil_eleve:
            return NiveauRisque.ELEVE
        if self.total >= self.seuil_modere:
            return NiveauRisque.MODERE
        return NiveauRisque.FAIBLE

    @property
    def repose_sur_des_poids_non_arretes(self) -> bool:
        """Au moins un poids n'a pas encore été arrêté par la direction.

        Un score calculé sur des poids que personne n'a choisis n'est pas un
        indicateur, c'est une opinion du développeur. Le tableau de bord le dit
        plutôt que de le taire.
        """
        return any(m.poids_non_valide for m in self.mesures)

    @property
    def mesures_actives(self) -> tuple[MesureComposante, ...]:
        """Les composantes qui contribuent réellement, de la plus lourde à la moins.

        L'ordre est celui de l'action : la direction traite ce qui pèse le plus.
        Trier par nom d'énumération produirait un ordre alphabétique, c'est-à-dire
        un ordre sans rapport avec la décision.
        """
        actives = [m for m in self.mesures if m.occurrences > 0]
        return tuple(sorted(actives, key=lambda m: m.contribution, reverse=True))


class ChargeCollaborateur(BaseModel):
    """Ce qu'un collaborateur porte, au jour de la lecture.

    ⚠️ **Ce n'est pas une évaluation de la personne**, et la distinction n'est
    pas de politesse. Un indicateur de charge employé comme mesure de performance
    se met à être optimisé : le collaborateur refuse les dossiers difficiles, ou
    traite en priorité ce qui fait baisser son compteur. Ce qui est mesuré ici est
    la **répartition du travail**, et sa seule action est de le rééquilibrer.
    """

    model_config = ConfigDict(frozen=True)

    compte: str
    nom_complet: str
    dossiers: int = Field(ge=0)
    #: Le cumul des scores de ses dossiers — ce qui distingue dix dossiers
    #: tranquilles de trois dossiers en feu.
    risque_porte: Decimal = Decimal(0)
    dossiers_a_risque_eleve: int = Field(default=0, ge=0)

    @property
    def risque_moyen(self) -> Decimal:
        """Le risque moyen par dossier. Zéro si le collaborateur n'en a aucun."""
        if self.dossiers == 0:
            return Decimal(0)
        return (self.risque_porte / Decimal(self.dossiers)).quantize(Decimal("0.1"))
