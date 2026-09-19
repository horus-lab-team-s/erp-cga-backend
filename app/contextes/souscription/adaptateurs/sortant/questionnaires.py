"""Charge les questionnaires de qualification depuis le référentiel sur disque.

Un adaptateur, et rien d'autre.

⚠️ **Le contrôle de cohérence est fait ici, au chargement.** Un questionnaire mal
rédigé — un ENUM sans valeurs, deux questions du même code — ne doit pas se
découvrir au moment où un responsable a le client au téléphone. La construction du
schéma de faits est donc tentée dès le chargement : elle applique les mêmes règles
que le moteur, et échoue au démarrage plutôt qu'en entretien.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.souscription.domaine.qualification import Question, Questionnaire

__all__ = [
    "QuestionnaireIntrouvable",
    "RepertoireDeQuestionnaires",
    "charger_les_questionnaires",
]


class QuestionnaireIntrouvable(LookupError):
    """Aucun questionnaire pour ce service."""


def charger_les_questionnaires(dossier: Path) -> list[Questionnaire]:
    """Les questionnaires du dossier, triés par service.

    Les fichiers sans questions sont ignorés sans bruit : c'est ainsi que le
    README cohabite avec eux.
    """
    questionnaires: list[Questionnaire] = []
    for fichier in sorted(dossier.glob("*.yaml")):
        donnees = yaml.safe_load(fichier.read_text(encoding="utf-8"))
        if not isinstance(donnees, dict) or "questions" not in donnees:
            continue
        questionnaire = Questionnaire(
            service=donnees["service"],
            version=str(donnees["version"]),
            questions=tuple(
                Question.model_validate(q) for q in donnees["questions"]
            ),
        )
        # Voir l'en-tête : on éprouve la construction du schéma au chargement.
        questionnaire.schema()
        questionnaires.append(questionnaire)
    return questionnaires


class RepertoireDeQuestionnaires:
    """Les questionnaires, indexés par service.

    ⚠️ `prendre` lève sur un service inconnu, et ne se replie sur aucun
    questionnaire par défaut. Un questionnaire générique poserait les mauvaises
    questions, et le montant qui en découlerait serait chiffré sur des faits qui
    ne décrivent pas la prestation demandée.
    """

    def __init__(self, questionnaires: list[Questionnaire]) -> None:
        self._par_service = {q.service: q for q in questionnaires}

    @classmethod
    def depuis(cls, dossier: Path) -> RepertoireDeQuestionnaires:
        return cls(charger_les_questionnaires(dossier))

    def prendre(self, service: str) -> Questionnaire:
        questionnaire = self._par_service.get(service)
        if questionnaire is None:
            connus = ", ".join(sorted(self._par_service)) or "aucun"
            raise QuestionnaireIntrouvable(
                f"aucun questionnaire pour le service « {service} ». Services "
                f"qualifiables : {connus}. Ajouter un fichier au référentiel, "
                "jamais un questionnaire générique : il poserait les mauvaises "
                "questions et le chiffrage porterait sur des faits hors sujet."
            )
        return questionnaire

    def services(self) -> list[str]:
        return sorted(self._par_service)

    def __len__(self) -> int:
        return len(self._par_service)
