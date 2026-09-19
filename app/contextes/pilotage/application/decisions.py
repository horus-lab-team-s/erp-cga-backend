"""Les cas d'usage des décisions de direction (pas 100).

─────────────────────────────────────────────────────────────────────────────────
DEUX GESTES

* `prendre_une_mesure` : la direction, devant le score **tel qu'il vient d'être
  calculé**, décide une mesure du catalogue avec un motif et, si la mesure l'exige, une
  échéance.
* `clore_une_decision` : la mesure a produit son effet, ou n'a plus lieu d'être.

Le cas d'usage ne calcule pas le score et ne lit pas l'horloge : l'appelant (la route)
lui passe le score du jour, l'instant et la personne. C'est ce qui le rend testable sans
base, sans session et sans date réelle, comme `evaluer_le_risque`.

⚠️ POURQUOI UNE MESURE DÉJÀ EN COURS EST REFUSÉE

Deux « Exiger une régularisation datée » ouvertes en même temps sur un dossier ne disent
pas deux choses : elles en disent une, avec deux échéances, et le collaborateur ne sait
plus laquelle tenir. Pour changer l'échéance, on clôt la première en disant pourquoi, et
on en décide une nouvelle. L'histoire garde les deux.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime

from app.contextes.pilotage.domaine.decisions import (
    CatalogueDesMesures,
    DecisionDeDirection,
    DecisionIntrouvable,
    DecisionRefusee,
    InstantaneDuScore,
    verifier_le_motif_de_direction,
)
from app.contextes.pilotage.domaine.entites import ScoreRisque
from app.contextes.pilotage.domaine.ports import DepotDecisions

__all__ = ["clore_une_decision", "prendre_une_mesure"]

#: Les libellés des niveaux, pour les messages : l'écran dit « À traiter », pas « ELEVE ».
_NIVEAUX_LISIBLES = {"FAIBLE": "sous contrôle", "MODERE": "à surveiller", "ELEVE": "à traiter"}


def prendre_une_mesure(
    *,
    score: ScoreRisque,
    a_la_date: date,
    catalogue: CatalogueDesMesures,
    code: str,
    motif: str,
    echeance: date | None,
    par: str,
    par_nom: str,
    le: datetime,
    depot: DepotDecisions,
) -> DecisionDeDirection:
    mesure = catalogue.mesure(code)
    niveau = score.niveau
    if niveau not in mesure.niveaux:
        permis = ", ".join(_NIVEAUX_LISIBLES[n.value] for n in mesure.niveaux)
        raise DecisionRefusee(
            f"« {mesure.libelle} » est prévue pour un dossier {permis} ; ce dossier est "
            f"{_NIVEAUX_LISIBLES[niveau.value]} ({score.total} points). Le catalogue du "
            "cabinet se règle au référentiel."
        )
    propre = verifier_le_motif_de_direction(motif, catalogue.motif_minimum)
    if mesure.echeance_requise and echeance is None:
        raise DecisionRefusee(
            f"« {mesure.libelle} » se décide avec une échéance : sans date, personne ne "
            "saura quand constater qu'elle n'a pas été tenue."
        )
    if echeance is not None and echeance <= a_la_date:
        raise DecisionRefusee(
            f"l'échéance du {echeance:%d/%m/%Y} n'est pas postérieure au "
            f"{a_la_date:%d/%m/%Y} : une mesure ne peut pas être échue le jour où elle est prise."
        )

    precedentes = depot.du_dossier(score.entreprise)
    for decision in precedentes:
        if decision.mesure == code and decision.en_cours:
            raise DecisionRefusee(
                f"« {mesure.libelle} » est déjà en cours sur ce dossier ({decision.identifiant}, "
                f"décidée le {decision.prise_le:%d/%m/%Y}). La clore d'abord, en disant pourquoi."
            )
    rang = 1 + sum(1 for d in precedentes if d.mesure == code)

    decision = DecisionDeDirection(
        identifiant=f"{score.entreprise}:{code}:{rang}",
        dossier=score.entreprise,
        mesure=code,
        libelle=mesure.libelle,
        motif=propre,
        echeance=echeance,
        prise_par=par,
        prise_par_nom=par_nom,
        prise_le=le,
        score=InstantaneDuScore(
            a_la_date=a_la_date,
            total=score.total,
            niveau=niveau,
            occurrences={m.composante.value: m.occurrences for m in score.mesures},
        ),
    )
    depot.enregistrer(decision)
    return decision


def clore_une_decision(
    *,
    dossier: str,
    identifiant: str,
    motif: str,
    par: str,
    par_nom: str,
    le: datetime,
    catalogue: CatalogueDesMesures,
    depot: DepotDecisions,
) -> DecisionDeDirection:
    """Clôt une décision en cours. Le motif minimum est celui du catalogue **actuel**.

    ⚠️ Une décision close reste lisible, et le catalogue peut avoir perdu sa mesure depuis :
    la clôture ne relit donc pas la mesure, seulement la longueur du motif.
    """
    for decision in depot.du_dossier(dossier):
        if decision.identifiant == identifiant:
            close = decision.clore(
                par=par,
                par_nom=par_nom,
                le=le,
                motif=motif,
                motif_minimum=catalogue.motif_minimum,
            )
            depot.enregistrer(close)
            return close
    raise DecisionIntrouvable(f"décision « {identifiant} » inconnue sur ce dossier.")
