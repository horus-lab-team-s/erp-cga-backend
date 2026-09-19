"""Générer le rapport mensuel (pas 106).

Le cas d'usage ne lit rien lui-même : l'appelant lui passe, pour chaque section, **la
lecture** qui alimente l'écran correspondant. C'est ce qui garantit l'absence de second
calcul, et ce qui le rend testable sans base : un test passe des lectures fabriquées.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from app.contextes.pilotage.domaine.ports import DepotRapports
from app.contextes.pilotage.domaine.rapport_mensuel import (
    RapportMensuel,
    ReglagesDuRapport,
    SectionDuRapport,
    empreinte_du_contenu,
)

__all__ = ["RapportRefuse", "bornes_du_mois", "generer_le_rapport"]


class RapportRefuse(ValueError):
    """Le rapport ne peut pas être généré pour ce mois. Le message dit pourquoi."""


def bornes_du_mois(mois: str) -> tuple[date, date]:
    annee, numero = (int(x) for x in mois.split("-"))
    return date(annee, numero, 1), date(annee, numero, monthrange(annee, numero)[1])


def generer_le_rapport(
    *,
    mois: str,
    jour: date,
    lectures: dict[SectionDuRapport, Callable[[date, date, date], Any]],
    reglages: ReglagesDuRapport,
    par: str,
    par_nom: str,
    le: datetime,
    depot: DepotRapports,
) -> RapportMensuel:
    """`lectures[section](du, au, a_la_date)` rend la réponse de l'écran, un modèle pydantic.

    ⚠️ Un mois à venir est refusé : il n'a rien à rapporter, et un rapport daté d'un mois qui
    n'a pas commencé serait un document de comité sans objet.
    """
    du, au = bornes_du_mois(mois)
    if du > jour:
        raise RapportRefuse(f"le mois {mois} n'a pas commencé : il n'y a rien à rapporter.")
    a_la_date = min(au, jour)
    sections: dict[str, Any] = {}
    for section in reglages.sections:
        reponse = lectures[section](du, au, a_la_date)
        # `mode="json"` : les décimaux et les dates deviennent des chaînes, exactement comme
        # l'écran les reçoit. C'est cette forme qui est figée et empreinte.
        sections[section.value] = reponse.model_dump(mode="json")
    version = 1 + sum(1 for r in depot.tous() if r.mois == mois)
    return _enregistrer(
        depot,
        RapportMensuel(
            identifiant=f"RM-{mois}-{version}",
            mois=mois,
            version=version,
            du=du,
            au=au,
            a_la_date=a_la_date,
            agrement=reglages.agrement,
            engagement=reglages.engagement,
            genere_le=le,
            genere_par=par,
            genere_par_nom=par_nom,
            sections=sections,
            empreinte=empreinte_du_contenu(mois, a_la_date, sections),
        ),
    )


def _enregistrer(depot: DepotRapports, rapport: RapportMensuel) -> RapportMensuel:
    depot.enregistrer(rapport)
    return rapport
