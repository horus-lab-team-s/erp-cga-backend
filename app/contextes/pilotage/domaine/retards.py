"""Les retards du cabinet, en rouge, par responsable (lot 4, 9 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LA RESPONSABLE A DEMANDÉ (réunion du 4 novembre 2026, demandes 20 et 22)

« Signaler les retards par un code couleur (rouge), avec les délais, pour que le
responsable repère tout de suite les dossiers en souffrance. » Et suivre le travail de
chaque gestionnaire.

CE QUE CET OBJET EST, ET CE QU'IL N'EST PAS

Un retard est un fait daté : ce qui devait être fait, depuis quand, et sous quel délai. Il
se RELIT à chaque lecture, comme le plan de travail : une tâche faite disparaît parce que
l'état a changé, jamais parce qu'on l'a cochée.

⚠️ CE N'EST PAS UNE NOTE DES PERSONNES. La doctrine de la charge (`charge_et_production`)
vaut ici : la vue montre les retards DU PORTEFEUILLE de chacun, sans score ni classement. Un
dossier sans porteur est un retard « non attribué », visible de la direction.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["CategorieDeRetard", "Responsable", "Retard"]


class CategorieDeRetard(StrEnum):
    COMPTABILITE = "COMPTABILITE"
    DECLARATION = "DECLARATION"
    CREATION = "CREATION"
    COMMERCE = "COMMERCE"
    RENDEZ_VOUS = "RENDEZ_VOUS"
    REGLEMENT = "REGLEMENT"


class Responsable(BaseModel):
    model_config = ConfigDict(frozen=True)

    compte: str
    nom: str


class Retard(BaseModel):
    model_config = ConfigDict(frozen=True)

    categorie: CategorieDeRetard
    #: Le NIU, ou la référence d'un dossier commercial ou de création.
    dossier: str
    denomination: str
    libelle: str
    #: La date à laquelle la chose était due.
    echeance: date
    #: Jours écoulés depuis l'échéance (au moins 1 : un retard d'aujourd'hui n'en est pas).
    jours_de_retard: int = Field(ge=1)
    #: L'écran qui la fait, sans le préfixe de langue.
    lien: str
    #: Qui porte le dossier. Vide : non attribué.
    responsables: tuple[Responsable, ...] = ()
