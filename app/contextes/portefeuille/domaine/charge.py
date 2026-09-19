"""L'évaluation de charge d'un dossier : combien de travail il représente.

La réponse sert deux fois — à fixer les honoraires, et à répartir les dossiers entre les
collaborateurs. Le centre se pose déjà la question sans outil ; ce module la formalise.

CE QU'IL DÉMONTRE

C'est le second domaine à tourner sur `app/moteur/`, et il n'a rien de fiscal. Il évalue
un dossier, pas une pièce ; ses conséquences se comptent en points, pas en francs ; son
agrégateur additionne là où celui de la conformité prend le maximum ; et ses seuils
appartiennent au centre, pas à la loi, donc aucun paramètre daté n'est résolu.

Quatre différences, un seul moteur. **Rien n'a été ajouté au noyau pour l'accueillir** :
ce fichier n'emploie que ce qui existait après le pas 5.

LE MÉCANISME

Une liste de critères, chacun découpé en paliers, chaque palier valant un nombre de
points. On additionne, on regarde dans quelle tranche le total tombe, et la tranche donne
une fourchette d'honoraires et une charge en jours par mois.

⚠️ **Convention du projet : un prédicat exprime la normalité.** Vrai = rien à signaler,
faux = le critère est atteint et les points s'ajoutent. La formulation surprend au premier
abord — on écrit « le volume est inférieur à deux cents » pour compter les gros volumes —
mais c'est la même convention que la conformité, et en changer pour un domaine reviendrait
à avoir deux moteurs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contextes.referentiel.contrats import Fondement, StatutValidation
from app.moteur.agregation import somme_des_enjeux
from app.moteur.consequence import Consequence, TypeConsequence, enjeu_declare
from app.moteur.faits import Fait, SchemaDeFaits, TypeFait

__all__ = [
    "AGREGER_LA_CHARGE",
    "CHIFFRER_LA_CHARGE",
    "DossierAEvaluer",
    "RegleDeCharge",
    "SCHEMA_CHARGE",
    "Tranche",
    "tranche_de",
]


# ── Ce que le dossier expose ─────────────────────────────────────────────────────

SCHEMA_CHARGE = SchemaDeFaits(
    domaine="EVALUATION_CHARGE",
    faits=(
        Fait(
            code="pieces_par_mois",
            type=TypeFait.ENTIER,
            libelle="Pièces déposées par mois",
            unite="pièces",
        ),
        Fait(code="comptes_bancaires", type=TypeFait.ENTIER, libelle="Comptes bancaires suivis"),
        Fait(code="salaries", type=TypeFait.ENTIER, libelle="Salariés à traiter en paie"),
        Fait(
            code="regime_fiscal",
            type=TypeFait.ENUM,
            libelle="Régime fiscal du dossier",
            valeurs=("REEL_NORMAL", "SIMPLIFIE", "LIBERATOIRE"),
        ),
        Fait(code="etablissements", type=TypeFait.ENTIER, libelle="Établissements distincts"),
        Fait(
            code="qualite_pieces",
            type=TypeFait.ENUM,
            libelle="Qualité des pièces fournies",
            valeurs=("BONNE", "MOYENNE", "MAUVAISE"),
        ),
        Fait(
            code="exercices_en_retard",
            type=TypeFait.ENTIER,
            libelle="Exercices non clôturés en retard",
        ),
    ),
)


class DossierAEvaluer(BaseModel):
    """Le sujet de l'évaluation de charge.

    Satisfait `app.moteur.faits.Sujet` par la même méthode que la facture, `faits()`, et
    c'est tout ce que le moteur exige de lui.

    Les valeurs viennent d'ailleurs : le volume de pièces est constaté par le service
    Pièces, le nombre de salariés par la Paie, la qualité des dépôts se mesure toute
    seule. Ce modèle ne les calcule pas, il les rassemble à un instant donné.
    """

    model_config = ConfigDict(frozen=True)

    reference: str = Field(min_length=1)
    pieces_par_mois: int = 0
    comptes_bancaires: int = 0
    salaries: int = 0
    regime_fiscal: str = "SIMPLIFIE"
    etablissements: int = 1
    qualite_pieces: str = "BONNE"
    exercices_en_retard: int = 0

    def faits(self) -> Mapping[str, Any]:
        return self.model_dump(mode="python", exclude={"reference"})


# ── Les règles ───────────────────────────────────────────────────────────────────


class RegleDeCharge(BaseModel):
    """Un critère de la grille de charge.

    Satisfait `app.moteur.evaluation.RegleEvaluable`. Comparée à la règle de conformité,
    elle est plus pauvre sur la **présentation** — pas de sévérité, pas de message, pas de
    remédiation, parce qu'un critère de charge ne s'adresse pas à un comptable qui doit
    corriger quelque chose.

    Elle porte en revanche exactement la même discipline de **justification** : un
    fondement, un statut de validation, et le nom de qui engage sa responsabilité. Le
    fondement n'est pas légal ici mais interne, et la raison d'être du principe reste la
    même : un responsable qui annonce un score sans pouvoir dire d'où viennent ses points
    ne peut pas le défendre devant un client qui trouve ses honoraires élevés.
    """

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1)
    libelle: str = Field(min_length=1)
    applicable_du: date
    applicable_au: date | None = None
    #: JSONLogic. **VRAI = ordinaire. FAUX = le critère est atteint, les points s'ajoutent.**
    predicat: dict[str, Any]
    points: int
    #: D'où vient ce critère et ce qui le justifie. Obligatoire, comme partout.
    fondement: Fondement
    statut: StatutValidation = StatutValidation.A_VALIDER
    #: Qui engage sa responsabilité sur ce critère, et quand. Un critère VALIDE sans
    #: signataire vaudrait moins qu'un critère A_VALIDER : il affirmerait sans engager
    #: personne.
    valide_par: str | None = None
    valide_le: date | None = None

    @model_validator(mode="after")
    def _coherence(self) -> RegleDeCharge:
        if self.applicable_au is not None and self.applicable_au <= self.applicable_du:
            raise ValueError(f"{self.code} : borne de validité incohérente")
        if not self.predicat:
            raise ValueError(f"{self.code} : prédicat vide")
        if self.points <= 0:
            raise ValueError(f"{self.code} : un critère qui ne pèse rien n'a pas lieu d'être")
        if self.statut is StatutValidation.VALIDE and not (self.valide_par and self.valide_le):
            raise ValueError(
                f"{self.code} : un critère VALIDE doit porter valide_par et valide_le. "
                "Sans signataire nommé, la grille n'est opposable à personne."
            )
        return self

    @property
    def consequence(self) -> Consequence:
        return Consequence(
            type=TypeConsequence.POINTS,
            libelle=self.libelle,
            unite="points",
            valeur_numerique=Decimal(self.points),
        )

    def en_vigueur(self, a_la_date: date) -> bool:
        if a_la_date < self.applicable_du:
            return False
        return self.applicable_au is None or a_la_date < self.applicable_au

    def concerne(self, faits: Mapping[str, Any]) -> bool:
        """Tout critère porte sur tout dossier.

        Aucune portée à filtrer, contrairement à la conformité où une règle de taxe n'a
        pas d'objet pour un émetteur non assujetti. La méthode existe parce que la boucle
        l'exige ; qu'elle rende toujours vrai est un fait du domaine, pas un oubli.
        """
        return True


# ── Les tranches ─────────────────────────────────────────────────────────────────


class Tranche(BaseModel):
    """Un palier de charge, et ce qu'il commande.

    Les montants sont ceux de la grille du centre. Ils vivront au référentiel avec les
    barèmes commerciaux le jour où celui-ci accueillera les paquets de règles complets ;
    ils sont ici tant que la grille n'a pas été transmise, et un test rappelle qu'ils sont
    provisoires.
    """

    model_config = ConfigDict(frozen=True)

    code: str
    libelle: str
    minimum: int
    maximum: int | None
    jours_par_mois: Decimal

    def contient(self, score: Decimal) -> bool:
        if score < self.minimum:
            return False
        return self.maximum is None or score <= self.maximum


def tranche_de(tranches: Sequence[Tranche], score: Decimal) -> Tranche:
    """La tranche où tombe un score.

    Rend toujours quelque chose : la dernière tranche n'a pas de plafond, précisément
    pour qu'aucun dossier ne se retrouve sans classement. Un dossier hors grille serait
    un dossier qu'on ne sait ni facturer ni planifier.
    """
    for tranche in tranches:
        if tranche.contient(score):
            return tranche
    return tranches[-1]


# ── Les deux ports du domaine ────────────────────────────────────────────────────

#: Les points sont déclarés par la règle : aucun besoin de regarder le dossier.
CHIFFRER_LA_CHARGE = enjeu_declare

#: On additionne, contrairement à la conformité. Deux critères qui pèsent chacun dix
#: points en pèsent vingt : les charges de travail se cumulent, les rejets de taxe non.
AGREGER_LA_CHARGE = somme_des_enjeux(unite="points")
