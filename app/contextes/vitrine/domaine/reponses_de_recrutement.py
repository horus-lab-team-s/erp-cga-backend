"""Les réponses types aux candidats (18 octobre 2026, point 25).

« Je vois que la candidature arrive, mais au niveau de la réponse ce n'est pas encore
ça. » Un modèle par décision, rempli avec le contexte de l'offre : le collaborateur
part toujours d'un texte juste, qu'il relit et corrige avant l'envoi. L'agent peut en
proposer un plus personnel (`redaction_reponse_claude.py`) ; sans lui, c'est ce texte.

⚠️ Un modèle ne promet rien que le cabinet n'ait décidé : ni salaire, ni date de prise
de poste. Ce sont des informations que l'humain ajoute, ou que l'entretien fixe.
"""

from __future__ import annotations

from app.contextes.vitrine.domaine.recrutement import (
    Candidature,
    EtatCandidature,
    OffreDePoste,
)

__all__ = ["CABINET", "modele_de_reponse"]

CABINET = "CGA Broad Range Consulting Group"

_MODELES: dict[EtatCandidature, tuple[str, str]] = {
    EtatCandidature.RECUE: (
        "Votre candidature au poste de {poste}",
        "Bonjour {prenom},\n\nNous avons bien reçu votre candidature au poste de {poste} "
        "({lieu}), et nous vous en remercions.\n\nNotre équipe l'étudie avec attention ; nous "
        "reviendrons vers vous dès que l'examen des candidatures sera terminé.\n\n"
        "Bien cordialement,\nL'équipe de {cabinet}",
    ),
    EtatCandidature.PRESELECTIONNEE: (
        "Votre candidature au poste de {poste} : la suite",
        "Bonjour {prenom},\n\nVotre candidature au poste de {poste} a retenu notre attention, "
        "et nous souhaitons poursuivre l'échange avec vous.\n\nNous vous contacterons très "
        "prochainement pour convenir d'un entretien.\n\nBien cordialement,\n"
        "L'équipe de {cabinet}",
    ),
    EtatCandidature.ENTRETIEN: (
        "Entretien pour le poste de {poste}",
        "Bonjour {prenom},\n\nNous avons le plaisir de vous inviter à un entretien pour le "
        "poste de {poste}, à {lieu}.\n\nDate et heure proposées : [à compléter].\nMerci de "
        "nous confirmer votre disponibilité en réponse à ce message.\n\nBien cordialement,\n"
        "L'équipe de {cabinet}",
    ),
    EtatCandidature.RETENUE: (
        "Votre candidature au poste de {poste} est retenue",
        "Bonjour {prenom},\n\nNous avons le plaisir de vous annoncer que votre candidature au "
        "poste de {poste} est retenue.\n\nNous vous contacterons pour les prochaines étapes de "
        "votre arrivée parmi nous.\n\nBien cordialement,\nL'équipe de {cabinet}",
    ),
    EtatCandidature.NON_RETENUE: (
        "Votre candidature au poste de {poste}",
        "Bonjour {prenom},\n\nNous vous remercions de l'intérêt que vous portez à {cabinet} et "
        "du temps consacré à votre candidature au poste de {poste}.\n\nAprès un examen "
        "attentif, nous ne sommes pas en mesure d'y donner une suite favorable. Cette décision "
        "ne retire rien à la qualité de votre parcours, et nous conservons volontiers votre "
        "dossier pour une prochaine opportunité.\n\nNous vous souhaitons pleine réussite.\n\n"
        "Bien cordialement,\nL'équipe de {cabinet}",
    ),
}


def modele_de_reponse(
    etat: EtatCandidature, candidature: Candidature, offre: OffreDePoste
) -> tuple[str, str]:
    """L'objet et le corps du texte type, remplis. Rien n'est envoyé ici."""
    objet, corps = _MODELES[etat]
    valeurs = {
        "prenom": candidature.appellation,
        "poste": offre.intitule,
        "lieu": offre.lieu,
        "cabinet": CABINET,
    }
    return objet.format(**valeurs), corps.format(**valeurs)
