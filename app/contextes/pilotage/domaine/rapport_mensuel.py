"""Le rapport mensuel de la direction : daté, archivé, et intègre (pas 106).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LA MAQUETTE EXIGE

Vue E du pilotage : « sommes-nous défendables, et que présente-t-on au comité ». Le rapport
mensuel est **la trace du pilotage exigible en cas de contrôle de l'agrément**. Trois
contraintes, qui font toute la forme de cet objet :

1. **Les mêmes chiffres que les écrans, sans second calcul.** Chaque section est la réponse
   exacte de la lecture qui alimente l'écran correspondant (tableau de bord du risque, charge
   et production, journal des dérogations, qualité des règles…), appelée au moment de la
   génération. Aucune somme n'est refaite ici.
2. **Daté et archivé.** Le rapport est **figé** à sa génération : il ne se recalcule pas à la
   lecture. Un rapport de juillet relu en décembre doit dire ce que la direction a vu en
   juillet, même si une dérogation a été levée depuis.
3. **Défendable.** Un document archivé qu'on pourrait modifier sans trace ne défend rien. Le
   rapport porte donc une **empreinte** (SHA-256 de son contenu canonique), écrite aussi au
   journal d'audit chaîné. À chaque lecture, l'empreinte est recalculée : un contenu altéré en
   base se voit.

⚠️ REGÉNÉRER NE REMPLACE PAS

Générer de nouveau le rapport d'un mois crée une **version suivante**. La précédente reste :
c'est peut-être elle qui a été présentée au comité.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

__all__ = [
    "RapportMensuel",
    "ReglagesDuRapport",
    "SectionDuRapport",
    "empreinte_du_contenu",
]


class SectionDuRapport(StrEnum):
    #: Le tableau de bord du risque : dossiers, niveaux, composantes.
    RISQUE = "RISQUE"
    #: La charge et la production des collaborateurs, et les réaffectations proposées.
    CHARGE = "CHARGE"
    #: Les dérogations du mois : les décisions qui engagent la signature du centre.
    DEROGATIONS = "DEROGATIONS"
    #: La qualité des règles de conformité sur le mois.
    QUALITE_DES_REGLES = "QUALITE_DES_REGLES"
    #: Les décisions de la direction prises dans le mois.
    DECISIONS = "DECISIONS"


class ReglagesDuRapport(BaseModel):
    """`Docs/referentiel/pilotage/rapport_mensuel.yaml`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Le numéro d'agrément du centre, tel qu'il figure sur la décision. Laissé à renseigner
    #: plutôt qu'inventé : un numéro plausible et faux sur un document de contrôle est pire
    #: qu'une mention « à renseigner ».
    agrement: str = "agrément à renseigner au référentiel"
    engagement: str = Field(
        "Le centre s'engage sur la sincérité des informations présentées.", min_length=10
    )
    #: Les sections incluses, dans l'ordre du document.
    sections: tuple[SectionDuRapport, ...] = tuple(SectionDuRapport)
    source: str = "valeurs par défaut"


def empreinte_du_contenu(mois: str, a_la_date: date, sections: dict[str, Any]) -> str:
    """SHA-256 de la forme **canonique** du contenu : clés triées, sans espaces superflus.

    ⚠️ Canonique, sinon deux sérialisations d'un même contenu (ordre des clés d'un
    dictionnaire relu en base) donneraient deux empreintes, et un rapport intact paraîtrait
    altéré.
    """
    canonique = json.dumps(
        {"mois": mois, "a_la_date": a_la_date.isoformat(), "sections": sections},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonique.encode("utf-8")).hexdigest()


class RapportMensuel(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: `RM-2026-07-1` : le mois et la version.
    identifiant: str
    mois: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    version: int = Field(ge=1)
    du: date
    au: date
    #: Le jour des chiffres : la fin du mois, ou le jour de génération si le mois est en cours.
    a_la_date: date
    agrement: str
    engagement: str
    genere_le: datetime
    genere_par: str
    genere_par_nom: str
    #: Chaque section, exactement comme la lecture de l'écran l'a rendue (JSON).
    sections: dict[str, Any]
    empreinte: str

    @computed_field
    @property
    def integre(self) -> bool:
        """Vrai si le contenu relu produit toujours l'empreinte écrite à la génération."""
        return empreinte_du_contenu(self.mois, self.a_la_date, self.sections) == self.empreinte
