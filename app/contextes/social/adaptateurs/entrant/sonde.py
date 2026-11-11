"""La sonde du contexte G : un bulletin de paie peut-il être calculé ce mois-ci ?

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE CONTEXTE A MAINTENANT UNE SONDE, ALORS QU'IL N'EN AVAIT PAS

La décision inverse était écrite, et son raisonnement tient toujours :

    « Le Social lit les barèmes, mais ils appartiennent au Référentiel, dont la
    sonde les couvre. Leur inventer une sonde qui relit la ressource d'un autre
    créerait deux vérités sur une même question. »

⚠️ **Cette sonde ne relit pas la ressource du Référentiel : elle pose une autre
question.** La sonde du Référentiel demande « le fichier se charge-t-il, et
porte-t-il quelque chose ». Celle-ci demande « porte-t-il les **onze valeurs
nommées** sans lesquelles aucun bulletin ne sort ».

Un référentiel de soixante paramètres auquel manque `CNPS_PLAFOND_MENSUEL` est
vert pour la première et en panne pour la seconde. Les deux verdicts sont exacts,
et ils ne se contredisent pas : ils ne portent pas sur la même chose.

CE QUE CETTE SONDE ACHÈTE

`_lire` lève `ParametrePaieAbsent` quand un code manque. La paie échoue donc
franchement — mais elle échoue **le jour de la paie**, sur le premier bulletin du
mois, devant un comptable qui a une échéance DIPE. Cette sonde déplace le constat
au déploiement, où il ne coûte que le temps d'ajouter une ligne au référentiel.

⚠️ ELLE EST BON MARCHÉ

Le registre appelle toutes les sondes à chaque consultation. Les paramètres
communs sont mémoïsés sur l'horodatage du fichier : onze résolutions en mémoire,
aucune lecture disque, aucune requête.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date

from app.partage import horloge
from app.registre.etat import Verdict

__all__ = ["CODES_SANS_LESQUELS_AUCUN_BULLETIN", "sonde_du_social"]


def _codes_indispensables() -> tuple[tuple[str, str], ...]:
    """Les codes que `calculer_les_retenues` exige, et ce que chacun bloque.

    ⚠️ **Recopiés depuis les appels de `_lire`, et non devinés.** Une liste écrite
    de mémoire finirait par contenir un code qui n'existe plus et par manquer celui
    qu'on vient d'ajouter — c'est-à-dire par alarmer à tort et rassurer à tort le
    même jour.
    """
    from app.contextes.social.api import CODE_ACCIDENTS_TRAVAIL, GroupeRisque

    socle = (
        ("CNPS_PLAFOND_MENSUEL", "le plafond des cotisations plafonnées"),
        ("CNPS_PVID_TAUX_SALARIE", "la pension vieillesse, part salariale"),
        ("CNPS_PVID_TAUX_EMPLOYEUR", "la pension vieillesse, part patronale"),
        ("CNPS_PRESTATIONS_FAMILIALES_TAUX", "les prestations familiales"),
        ("CFC_TAUX_SALARIE", "le Crédit foncier, part salariale"),
        ("CFC_TAUX_EMPLOYEUR", "le Crédit foncier, part patronale"),
    )
    # ⚠️ LES TROIS GROUPES DE RISQUE, ET PAS SEULEMENT CELUI DU DOSSIER OUVERT.
    # Le taux d'accidents du travail dépend du groupe de l'employeur : vérifier
    # le seul groupe A laisserait tomber la paie du premier client classé en C.
    accidents = tuple(
        (CODE_ACCIDENTS_TRAVAIL[groupe], f"les accidents du travail, groupe {groupe.value}")
        for groupe in GroupeRisque
    )
    return socle + accidents


#: Exporté pour que la recette puisse nommer ce qui est surveillé.
CODES_SANS_LESQUELS_AUCUN_BULLETIN = _codes_indispensables


def sonde_du_social() -> Verdict | None:
    """Les paramètres d'abord, les deux barèmes de paie ensuite."""
    verdict = _les_parametres_de_paie()
    return verdict if verdict is not None else _les_baremes_de_paie()


def _les_parametres_de_paie() -> Verdict | None:
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
        # ⚠️ On ne double pas le verdict du Référentiel : c'est sa question, et
        # sa sonde la pose mieux. Suspect suffit à renvoyer vers elle.
        return Verdict.suspect(
            f"le référentiel commun ne se charge pas ({type(panne).__name__}) : "
            "voir la sonde du Référentiel, qui porte cette question"
        )

    for code, ce_qu_il_bloque in _codes_indispensables():
        try:
            service.resoudre(code, aujourd_hui)
        except (ParametreInconnu, AucuneVersionApplicable) as absence:
            return Verdict.panne(
                f"{code} ne se résout pas au {aujourd_hui:%d/%m/%Y} ({absence}) : "
                f"{ce_qu_il_bloque} ne peut pas être calculé, donc aucun bulletin "
                "ne sortira ce mois-ci"
            )
    return None


def _les_baremes_de_paie() -> Verdict | None:
    """L'IRPP sur salaires et la taxe de développement local.

    ⚠️ **Une panne, là où le Référentiel se contente d'un soupçon.** Sa sonde
    hésite à juste titre : « un déploiement peut n'avoir aucun barème », ce qui
    est vrai d'une installation qui ne fait pas de paie. Ici la question ne se
    pose plus — c'est le service de la paie qui répond, et sans ces deux barèmes
    il ne produit rien.
    """
    from app.contextes.referentiel.api import (
        AucuneVersionApplicable,
        BaremeInconnu,
        DepotBaremesYaml,
        ParametreInconnu,
        ServiceBaremes,
    )
    from app.infrastructure.config import configuration

    aujourd_hui: date = horloge.aujourd_hui()
    # ⚠️ Le fichier est relu ici, comme la sonde du Référentiel le relit. C'est la
    # même lecture, de quelques kilo-octets, et il n'existe pas d'accesseur mémoïsé
    # pour les barèmes — les routes de la paie le construisent aussi à chaque appel.
    # Le jour où l'un arrivera, les trois s'y brancheront ensemble.
    fichier = configuration().dossier_referentiel / "baremes.yaml"
    try:
        baremes = ServiceBaremes.depuis_depot(DepotBaremesYaml(fichier))
    except Exception as panne:  # noqa: BLE001 — le motif importe, quel qu'il soit
        return Verdict.panne(
            f"les barèmes de paie ne se chargent pas ({type(panne).__name__}) : "
            f"aucun bulletin ne sortira. Vérifier {fichier}"
        )

    for code, quoi in (
        ("IRPP_SALAIRES", "l'impôt sur le revenu retenu à la source"),
        ("TDL_SALAIRES", "la taxe de développement local"),
    ):
        try:
            baremes.resoudre(code, aujourd_hui)
        except (BaremeInconnu, ParametreInconnu, AucuneVersionApplicable) as absence:
            return Verdict.panne(
                f"le barème {code} ne se résout pas au {aujourd_hui:%d/%m/%Y} "
                f"({absence}) : {quoi} ne peut pas être calculé"
            )
    return None
