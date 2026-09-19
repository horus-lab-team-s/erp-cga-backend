"""Évaluer la charge d'un dossier, avec le moteur qui contrôle les factures.

Le cas d'usage tient en quinze lignes utiles, et c'est le résultat qu'on cherchait : tout
ce qui n'est pas propre au domaine vit dans `app/moteur/`.

CE QUI EST PROPRE À CE DOMAINE

    le schéma de faits      un dossier, pas une pièce
    la grille de règles     des critères de charge, pas du droit fiscal
    la valorisation         les points sont déclarés, rien à calculer
    l'agrégateur            on additionne
    les tranches            ce que le score commande, en honoraires et en jours

CE QUI VIENT DU NOYAU

Tout le reste : le filtre par date de vigueur, l'évaluation des prédicats, le rattrapage
des règles en échec, le dénombrement. **Rien n'a été ajouté au noyau pour accueillir ce
domaine.**

AUCUN PARAMÈTRE DATÉ N'EST RÉSOLU

C'est la seconde différence avec la conformité, et elle est instructive. Les seuils d'une
grille de charge appartiennent au centre : il les révise quand il veut, sans qu'aucune loi
ne l'y oblige. Ils sont donc écrits dans les règles plutôt que référencés au référentiel,
et le résolveur par défaut du moteur suffit.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.contextes.portefeuille.domaine.charge import (
    AGREGER_LA_CHARGE,
    CHIFFRER_LA_CHARGE,
    SCHEMA_CHARGE,
    DossierAEvaluer,
    RegleDeCharge,
    Tranche,
    tranche_de,
)
from app.contextes.referentiel.contrats import StatutValidation
from app.moteur.chemins import chemins_cites
from app.moteur.consequence import Consequence
from app.moteur.evaluation import Declenchement, evaluer_regles

__all__ = ["CritereRetenu", "EvaluationDeCharge", "evaluer_la_charge"]


@dataclass(frozen=True)
class _ConstatDeCharge:
    """Le constat minimal qu'un agrégateur exige : sévérité, enjeu, conséquence.

    Ce domaine n'a pas de sévérité — un critère de charge n'est ni grave ni bénin. On
    emploie le code du critère comme clé de dénombrement, ce qui donne au passage le
    détail par critère sans structure supplémentaire.
    """

    severite: str
    enjeu: Decimal | None
    consequence: Consequence

    @classmethod
    def depuis(cls, declenchement: Declenchement) -> _ConstatDeCharge:
        return cls(
            severite=declenchement.regle.code,
            enjeu=declenchement.enjeu,
            consequence=declenchement.regle.consequence,
        )


class CritereRetenu(BaseModel):
    """Un critère atteint, et ce qu'il pèse.

    Le détail importe autant que le total : un responsable qui annonce un score sans
    pouvoir dire ce qui le compose ne peut pas le défendre devant un client qui trouve
    ses honoraires élevés.
    """

    model_config = ConfigDict(frozen=True)

    code: str
    libelle: str
    points: Decimal


class EvaluationDeCharge(BaseModel):
    """Le résultat, immuable. Réévaluer un dossier produit une nouvelle évaluation."""

    model_config = ConfigDict(frozen=True)

    reference_dossier: str
    date_evaluation: date
    criteres_retenus: list[CritereRetenu] = Field(default_factory=list)
    score: Decimal = Decimal(0)
    tranche: Tranche
    criteres_evalues: int = 0
    criteres_en_echec: list[str] = Field(default_factory=list)
    #: Vrai tant qu'un critère retenu, ou la grille de tranches, n'a pas été confirmé par
    #: une personne nommée. Le score n'est alors pas opposable : on peut s'en servir pour
    #: répartir la charge en interne, pas pour justifier un honoraire à un client.
    repose_sur_des_criteres_non_valides: bool = True

    @property
    def jours_par_mois(self) -> Decimal:
        return self.tranche.jours_par_mois


def evaluer_la_charge(
    dossier: DossierAEvaluer,
    grille: Sequence[RegleDeCharge],
    tranches: Sequence[Tranche],
    a_la_date: date,
) -> EvaluationDeCharge:
    """Applique la grille en vigueur à cette date, et rend le score et sa tranche."""
    # Le même refus au montage que pour la conformité : un critère qui interroge un fait
    # inexistant ne lève rien à l'évaluation, il rend faux, et il ajoute ses points à
    # tous les dossiers. Un score faussé se répercute sur les honoraires facturés.
    for regle in grille:
        SCHEMA_CHARGE.valider_predicat(
            chemins_cites(regle.predicat), origine=f"le critère {regle.code}"
        )

    faits = dossier.faits()
    evaluation = evaluer_regles(
        grille,
        faits,
        a_la_date,
        valorisation=CHIFFRER_LA_CHARGE,
    )

    agregat = AGREGER_LA_CHARGE(
        [_ConstatDeCharge.depuis(d) for d in evaluation.declenchements]
    )
    score = agregat.valeur if agregat.valeur is not None else Decimal(0)

    return EvaluationDeCharge(
        reference_dossier=dossier.reference,
        date_evaluation=a_la_date,
        criteres_retenus=[
            CritereRetenu(
                code=d.regle.code,
                libelle=d.regle.libelle,
                points=d.enjeu if d.enjeu is not None else Decimal(0),
            )
            for d in evaluation.declenchements
        ],
        score=score,
        tranche=tranche_de(tranches, score),
        repose_sur_des_criteres_non_valides=any(
            d.regle.statut is not StatutValidation.VALIDE for d in evaluation.declenchements
        ),
        criteres_evalues=evaluation.regles_appliquees,
        criteres_en_echec=[echec.code_regle for echec in evaluation.echecs],
    )
