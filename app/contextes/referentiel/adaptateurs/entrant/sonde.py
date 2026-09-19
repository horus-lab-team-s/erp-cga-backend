"""La sonde du contexte A : y a-t-il des paramètres à lire ?

⚠️ **C'est le service dont l'arrêt entraîne le plus d'autres** : douze en dépendent.
Sans paramètres chargés, aucun calcul fiscal, comptable ou social n'est possible.
"""

from __future__ import annotations

from app.contextes.referentiel.api import (
    DepotBaremesYaml,
    DepotParametresYaml,
    ServiceBaremes,
    ServiceParametres,
)
from app.infrastructure.config import configuration
from app.registre.etat import Verdict

__all__ = ["FICHIER_BAREMES", "FICHIER_PARAMETRES", "sonde_du_referentiel"]

FICHIER_PARAMETRES = "parametres.yaml"
FICHIER_BAREMES = "baremes.yaml"


def sonde_du_referentiel() -> Verdict | None:
    """Les paramètres d'abord, les barèmes ensuite. Le plus grave fait foi.

    ⚠️ **Les barèmes sont vérifiés ici, et non par le Social.** Ils sont une ressource du
    Référentiel : c'est son dépôt que le Social emprunte par l'`api`. Une sonde du Social
    qui relirait le même fichier créerait deux vérités sur une seule question, et le jour
    où elles divergeraient, personne ne saurait laquelle croire.
    """
    verdict = _les_parametres()
    return verdict if verdict is not None else _les_baremes()


def _les_parametres() -> Verdict | None:
    """Le dépôt des paramètres est-il chargé, et contient-il quelque chose ?

    ⚠️ **Une panne franche.** Un référentiel vide n'a aucune explication innocente :
    c'est un fichier du dépôt, présent ou absent.
    """
    fichier = configuration().dossier_referentiel / FICHIER_PARAMETRES
    try:
        service = ServiceParametres.depuis_depot(DepotParametresYaml(fichier))
    except Exception as panne:  # noqa: BLE001 — le motif importe, quel qu'il soit
        return Verdict.panne(
            f"le référentiel ne se charge pas ({type(panne).__name__}) : aucun "
            f"calcul ne serait possible. Vérifier {fichier}"
        )
    if not service.codes:
        return Verdict.panne(
            f"le référentiel est vide : aucun paramètre chargé depuis {fichier}, "
            "donc aucun calcul fiscal, comptable ou social n'est possible"
        )
    return None


def _les_baremes() -> Verdict | None:
    """Les barèmes progressifs se chargent-ils ?

    ─────────────────────────────────────────────────────────────────────────────────
    ⚠️ **ABSENT SE TAIT, PRÉSENT ET FAUTIF PARLE.**

    « Un déploiement peut n'avoir aucun barème » : c'est écrit dans `ServiceBaremes`, et
    c'est vrai d'une installation qui ne fait pas de paie. Alarmer sur l'absence
    apprendrait à ignorer cette sonde. Un fichier **présent** qui ne se charge pas est
    autre chose : quelqu'un l'a posé, donc quelqu'un compte dessus.

    ⚠️ **Suspect et non panne**, bien que le Référentiel soit le service dont douze autres
    dépendent. Une panne ici marquerait la plateforme entière en difficulté pour un
    fichier de paie, alors que les paramètres — l'essentiel — répondent encore. C'est le
    raisonnement déjà tenu par la sonde du Portefeuille sur sa grille de charge.
    ─────────────────────────────────────────────────────────────────────────────────
    """
    fichier = configuration().dossier_referentiel / FICHIER_BAREMES
    if not fichier.is_file():
        return None
    try:
        service = ServiceBaremes.depuis_depot(DepotBaremesYaml(fichier))
    except Exception as panne:  # noqa: BLE001 — le motif importe, quel qu'il soit
        return Verdict.suspect(
            f"les barèmes ne se chargent pas ({type(panne).__name__}) : aucun bulletin "
            f"de paie ne serait calculé. Vérifier {fichier}"
        )
    if not service.codes:
        return Verdict.suspect(
            f"{fichier} est présent mais ne déclare aucun barème : aucun bulletin de "
            "paie ne serait calculé, alors que le fichier laisse croire le contraire"
        )
    return None
