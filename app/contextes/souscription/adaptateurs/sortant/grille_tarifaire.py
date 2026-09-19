"""Charge les barèmes et les règles de tarification depuis le référentiel.

Un adaptateur, et rien d'autre.

⚠️ **LE SCHÉMA EST VÉRIFIÉ AU CHARGEMENT, ET IL DÉPEND DU SERVICE**

Une règle de tarification cite des faits qui viennent du questionnaire du service
qu'elle tarife. Le contrôle ne peut donc pas se faire sans savoir de quel service
il s'agit, contrairement aux quatre autres domaines dont le schéma est unique.

`valider_contre` prend le questionnaire et refuse une règle qui cite un fait
absent, avec une suggestion. Sans lui, un prédicat qui lit `{"var": "associe"}`
au lieu de `{"var": "associes"}` rendrait le critère silencieusement toujours
faux, et **le prix serait ajusté sur chaque dossier** sans que rien ne le dise.

C'est le défaut le plus coûteux du lot : il ne casse rien, il fait perdre de
l'argent à chaque devis.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from app.contextes.souscription.domaine.qualification import Questionnaire
from app.contextes.souscription.domaine.tarification import (
    Bareme,
    RegleDeTarification,
    schema_de_tarification,
)
from app.moteur.chemins import chemins_cites

__all__ = [
    "FICHIER_BAREMES",
    "charger_les_baremes",
    "charger_les_regles_de_tarification",
    "valider_contre",
]

#: Le fichier des barèmes, distinct des règles qui vivent un par fichier.
FICHIER_BAREMES = "baremes.yaml"


def charger_les_baremes(dossier: Path) -> list[Bareme]:
    """Les barèmes, dans l'ordre du fichier.

    L'ordre du fichier fait foi : c'est lui qui décide quel barème est essayé en
    premier quand deux périodes se chevaucheraient. Le trier ici masquerait un
    fichier mal rédigé au lieu de le laisser se voir.
    """
    donnees = yaml.safe_load((dossier / FICHIER_BAREMES).read_text(encoding="utf-8"))
    return [
        Bareme(
            **{
                **entree,
                "base": Decimal(str(entree["base"])),
                "baisse_maximale": Decimal(str(entree.get("baisse_maximale", "0.20"))),
                "hausse_maximale": Decimal(str(entree.get("hausse_maximale", "0.50"))),
            }
        )
        for entree in donnees["baremes"]
    ]


def charger_les_regles_de_tarification(dossier: Path) -> list[RegleDeTarification]:
    """Les règles du dossier, triées par code pour que l'ordre soit reproductible.

    ⚠️ L'ordre ne change **pas** le prix : les ajustements en proportion portent
    tous sur la base, donc ils commutent. Il change en revanche l'ordre des
    lignes affichées au client, et deux devis identiques doivent se ressembler.
    """
    regles: list[RegleDeTarification] = []
    for fichier in sorted(dossier.glob("*.yaml")):
        if fichier.name == FICHIER_BAREMES:
            continue
        donnees = yaml.safe_load(fichier.read_text(encoding="utf-8"))
        if not isinstance(donnees, dict) or "predicat" not in donnees:
            continue
        for cle in ("montant", "taux"):
            if donnees.get(cle) is not None:
                donnees[cle] = Decimal(str(donnees[cle]))
        regles.append(RegleDeTarification.model_validate(donnees))
    return regles


def valider_contre(
    regles: list[RegleDeTarification], questionnaire: Questionnaire
) -> None:
    """Refuse une règle qui cite un fait que ce service n'expose pas.

    Levée au chargement, avec le nom du fichier et une suggestion. Voir
    l'en-tête : un fait mal orthographié ne casse rien, il fait perdre de
    l'argent à chaque devis.

    ⚠️ Une règle peut légitimement ne pas s'appliquer à tous les services : celle
    qui cite `apports_en_nature` n'a pas de sens pour une tenue comptable. C'est
    à l'appelant de ne confronter une règle qu'aux services qu'elle tarife, et
    c'est pourquoi cette fonction prend les deux en argument plutôt que de
    parcourir seule le référentiel.
    """
    schema = schema_de_tarification(questionnaire)
    for regle in regles:
        schema.valider_predicat(
            chemins_cites(regle.predicat),
            origine=f"{regle.code} (service {questionnaire.service})",
        )
