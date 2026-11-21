"""La sonde du contexte I : un dossier de création peut-il être instruit ?

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE CONTEXTE A MAINTENANT UNE SONDE, ALORS QU'IL N'EN AVAIT PAS

La décision inverse était écrite, et elle était juste quand elle a été prise :

    « La Clôture et la Création d'entreprise n'ont aucune ressource à elles :
    elles lisent la Comptabilité, le Portefeuille et la base. Leur inventer une
    sonde qui relit la ressource d'un autre créerait deux vérités sur une même
    question. »

Deux choses ont changé.

**Ce contexte a désormais des ressources à lui.** `checklist.py` porte la liste
des pièces exigées par forme juridique, et `CAPITAL_EXIGE` porte le contrat que
ce contexte passe avec le référentiel : trois codes de capital minimum qu'il est
seul à demander. Ce ne sont pas les ressources d'un autre.

**Et la question posée n'est pas celle du Référentiel.** Sa sonde répond « le
fichier se charge et n'est pas vide ». Celle-ci répond « les trois valeurs sans
lesquelles ce contexte ne contrôle plus rien y sont-elles ». Un référentiel de
soixante paramètres auquel manque `CAPITAL_MINIMUM_SARL` est vert pour la
première et en panne pour la seconde. Ce n'est pas deux vérités sur une question,
c'est une vérité sur une question que personne ne posait.

⚠️ CE QUI REND CETTE SONDE NÉCESSAIRE PLUTÔT QUE CONFORTABLE

`_constater_le_capital` ne lève pas quand le paramètre manque : il rend un constat
**non bloquant** qui le dit, et c'est le bon choix au guichet — une ligne absente
d'un fichier YAML ne doit pas empêcher d'instruire tous les dossiers de SARL.

Le revers est que l'absence est alors **invisible**. Les dossiers passent, le
capital n'est plus comparé au minimum légal, et personne ne l'apprend avant qu'un
greffe ne refuse une immatriculation que le cabinet avait vendue comme acquise.
La sonde est le seul endroit d'où ce silence se voit.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from app.partage import horloge
from app.registre.etat import Verdict

__all__ = ["sonde_de_la_creation_d_entreprise"]


def sonde_de_la_creation_d_entreprise() -> Verdict | None:
    """Les pièces par forme, puis les capitaux minimaux. Le plus grave fait foi."""
    verdict = _les_checklists()
    return verdict if verdict is not None else _les_capitaux_minimaux()


def _les_checklists() -> Verdict | None:
    """Chaque forme juridique a-t-elle des pièces à réunir ?

    ⚠️ **Une panne franche.** Une forme sans pièces rend un dossier « complet »
    dès son ouverture : aucune pièce manquante, donc rien qui bloque, donc
    déposable au CFCE avec un dossier vide. C'est le pire des états, parce qu'il
    a l'apparence du succès.

    Le cas se produit le jour où quelqu'un ajoute une valeur à `FormeJuridique`
    sans l'ajouter à `_PAR_FORME`, ce qui est exactement ce qu'on fait quand le
    cabinet se met à vendre une forme de plus.
    """
    from app.contextes.creation_entreprise.domaine.checklist import checklist_de
    from app.contextes.portefeuille.contrats import FormeJuridique

    for forme in FormeJuridique:
        try:
            pieces = checklist_de(forme)
        except Exception as panne:  # noqa: BLE001 — le motif importe, quel qu'il soit
            return Verdict.panne(
                f"la checklist de {forme.value} ne se construit pas "
                f"({type(panne).__name__}) : aucun dossier de cette forme ne peut "
                "être instruit"
            )
        if not pieces:
            return Verdict.panne(
                f"aucune pièce exigée pour {forme.value} : un dossier de cette forme "
                "naîtrait « complet » et deviendrait déposable au CFCE sans qu'une "
                "seule pièce ait été réunie"
            )
    return None


def _les_capitaux_minimaux() -> Verdict | None:
    """Les trois codes de capital minimum se résolvent-ils aujourd'hui ?

    ⚠️ **Lu sur le référentiel COMMUN, et non sur celui du cabinet courant.**

    Deux raisons. La première est le coût : une sonde est appelée à chaque
    consultation du registre, et `parametres_du_cabinet()` interroge la base pour
    y appliquer les décisions du cabinet. La seconde est le propos : une sonde
    juge l'**installation**, pas le paramétrage d'un locataire. Le commun est
    mémoïsé sur l'horodatage du fichier, donc gratuit.

    ⚠️ **À la date du jour, et non à une date de dossier.** La question est « le
    cabinet peut-il instruire une création aujourd'hui », et un paramètre dont
    toutes les versions sont expirées répond non aussi sûrement qu'un paramètre
    absent.
    """
    from app.contextes.creation_entreprise.domaine.checklist import CAPITAL_EXIGE
    from app.contextes.referentiel.api import (
        AucuneVersionApplicable,
        ParametreInconnu,
        ServiceParametres,
        parametres_communs,
    )

    aujourd_hui = horloge.aujourd_hui()
    try:
        service = ServiceParametres(list(parametres_communs()))
    except Exception as panne:  # noqa: BLE001 — le motif importe, quel qu'il soit
        # Le Référentiel le dira mieux que nous ; on ne double pas son verdict.
        return Verdict.suspect(
            f"le référentiel commun ne se charge pas ({type(panne).__name__}) : "
            "voir la sonde du Référentiel, qui porte cette question"
        )

    # ⚠️ Trié, pour que le motif soit stable d'une consultation à l'autre. Un
    # message qui change de code d'une minute à l'autre ferait croire à deux
    # incidents là où il n'y en a qu'un.
    for forme, code in sorted(CAPITAL_EXIGE.items(), key=lambda couple: couple[1]):
        try:
            service.resoudre(code, aujourd_hui)
        except (ParametreInconnu, AucuneVersionApplicable) as absence:
            return Verdict.panne(
                f"{code} ne se résout pas au {aujourd_hui:%d/%m/%Y} ({absence}) : "
                f"le capital d'un dossier de {forme.value} ne serait plus comparé au "
                "minimum légal, et le dossier partirait au greffe sans que personne "
                "l'ait su"
            )
    return None
