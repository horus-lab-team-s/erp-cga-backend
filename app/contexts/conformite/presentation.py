"""Rendu du bandeau de verdict de l'écran E02.

Le bandeau est le premier élément que lit le comptable. Il annonce la gravité maximale
rencontrée **et la conséquence fiscale chiffrée, en langage clair** — pas un code, pas un
pourcentage de conformité.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ...shared.formats import montant_fcfa
from .modeles import GLYPHE, RapportConformite, Severite

__all__ = ["Verdict", "composer_verdict"]

#: Jetons du § 10 du dossier de design. Le front les lit comme des noms, pas comme des
#: valeurs hexadécimales : la couleur appartient au design system, pas au backend.
_JETON_FOND: dict[Severite | None, str] = {
    Severite.BLOQUANT: "danger",
    Severite.MAJEUR: "warning",
    Severite.AVERTISSEMENT: "warning-100",
    Severite.INFORMATION: "brand-indigo-100",
    None: "success-100",
}


class Verdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    glyphe: str
    titre: str
    detail: str
    jeton_fond: str
    comptabilisation_interdite: bool
    avertissement_validation: str | None = None


def composer_verdict(rapport: RapportConformite) -> Verdict:
    severite = rapport.severite_maximale
    nb = len(rapport.constats)

    if severite is None:
        titre = "Conforme — aucun constat"
        detail = (
            f"TVA déductible en totalité · charge intégralement déductible · "
            f"{rapport.regles_appliquees} règles appliquées"
        )
    elif severite is Severite.BLOQUANT:
        titre = "Anomalie bloquante — comptabilisation interdite"
        detail = f"TVA et charge non déductibles : {montant_fcfa(rapport.enjeu_total)}"
    elif severite is Severite.MAJEUR:
        titre = f"Anomalie majeure — TVA non déductible : {montant_fcfa(rapport.enjeu_total)}"
        detail = (
            f"{nb} constat{'s' if nb > 1 else ''} sur {rapport.regles_appliquees} règles · "
            "comptabilisation possible avec conséquence fiscale"
        )
    elif severite is Severite.AVERTISSEMENT:
        titre = "Avertissement — risque à documenter"
        detail = "Comptabilisation possible, constat à conserver au dossier"
    else:
        titre = "Information — bonne pratique"
        detail = f"{nb} remarque{'s' if nb > 1 else ''}, sans conséquence fiscale"

    avertissement = None
    if rapport.repose_sur_des_valeurs_non_validees:
        avertissement = (
            "Ce rapport s'appuie sur des règles ou des paramètres qui n'ont pas encore été "
            "confirmés sur le texte officiel. Il n'est pas opposable en l'état."
        )

    return Verdict(
        glyphe=GLYPHE[severite] if severite else "✓",
        titre=titre,
        detail=detail,
        jeton_fond=_JETON_FOND[severite],
        comptabilisation_interdite=rapport.comptabilisation_interdite,
        avertissement_validation=avertissement,
    )
