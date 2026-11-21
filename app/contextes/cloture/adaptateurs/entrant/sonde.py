"""La sonde du contexte H : l'abattement CGA peut-il encore être calculé ?

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE CONTEXTE A MAINTENANT UNE SONDE

Il était rangé parmi ceux qui « n'ont aucune ressource à eux ». C'est vrai de sa
matière — il ne persiste rien, tout ce qu'il produit est une fonction de la
balance et du référentiel. Ce n'est pas vrai de son **contrat** : trois codes du
référentiel lui appartiennent, et il est seul à les demander.

⚠️ CE QU'UN ABATTEMENT ABSENT COÛTE, ET POURQUOI PERSONNE NE LE VOIT

L'abattement CGA est **la seule chose que l'adhésion achète**. Un adhérent paie
sa cotisation pour que son bénéfice imposable soit réduit ; c'est l'argument de
vente du site, et c'est ce que le cabinet promet.

`_abattement_cga` ne lève pas quand le taux manque : il rend `None` et une phrase
qui l'explique, jointe au dossier. C'est le bon choix — un taux supposé serait une
valeur légale inventée, et la liasse d'un seul adhérent ne doit pas faire tomber
la clôture de tous les autres.

Le revers est que l'explication vit **dans le dossier**, où elle est lue par la
personne qui traite ce dossier-là, une fois. Si le taux manque, chaque adhérent du
cabinet paie l'impôt plein, chacun avec sa petite phrase, et la plateforme se
déclare en bonne santé. Cette sonde est le seul endroit d'où l'on voit que ce
n'est pas un dossier, c'est tous.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from app.partage import horloge
from app.registre.etat import Verdict

__all__ = ["sonde_de_la_cloture"]


def sonde_de_la_cloture() -> Verdict | None:
    """Le taux d'abattement d'abord, son plafond ensuite.

    ⚠️ **`SEUIL_SYSTEME_NORMAL` n'est volontairement pas vérifié.** Son absence
    rend le Système Normal, qui est le régime de droit commun : `_systeme_dsf` le
    dit, et le retenir par défaut « ne fait courir aucun risque de rejet ». Une
    liasse plus détaillée que nécessaire est acceptée au guichet.

    Alarmer sur un manque sans conséquence est le plus sûr moyen d'apprendre aux
    exploitants à ne plus lire ces alertes, et c'est le raisonnement que la sonde
    du Référentiel tient déjà sur son fichier de barèmes.
    """
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
        return Verdict.suspect(
            f"le référentiel commun ne se charge pas ({type(panne).__name__}) : "
            "voir la sonde du Référentiel, qui porte cette question"
        )

    try:
        service.resoudre("ABATTEMENT_CGA_BENEFICE", aujourd_hui)
    except (ParametreInconnu, AucuneVersionApplicable) as absence:
        return Verdict.panne(
            f"ABATTEMENT_CGA_BENEFICE ne se résout pas au {aujourd_hui:%d/%m/%Y} "
            f"({absence}) : aucun adhérent n'obtiendrait l'abattement qu'il paie, "
            "et chaque liasse partirait avec le bénéfice plein"
        )

    try:
        service.resoudre("ABATTEMENT_CGA_PLAFOND", aujourd_hui)
    except (ParametreInconnu, AucuneVersionApplicable):
        # ─────────────────────────────────────────────────────────────────────
        # ⚠️ SUSPECT, ET DANS L'AUTRE SENS QUE LE PRÉCÉDENT.
        #
        # `_abattement_cga` se rabat sur `plafond = 0`, et ne plafonne que si le
        # plafond est strictement positif. Un plafond absent **supprime donc le
        # plafonnement** : l'abattement s'applique en entier, si gros soit-il.
        #
        # Le code confond ainsi deux états que le référentiel ne distingue pas :
        # « la loi ne plafonne pas » et « personne n'a écrit la ligne ». Le
        # premier est légitime et fréquent ; le second fait accorder un avantage
        # qui n'est pas dû, ce qu'un contrôle reprendrait au centime.
        #
        # Suspect plutôt que panne, précisément parce que l'explication innocente
        # existe — c'est la règle que `Verdict.suspect` pose.
        # ─────────────────────────────────────────────────────────────────────
        return Verdict.suspect(
            f"ABATTEMENT_CGA_PLAFOND ne se résout pas au {aujourd_hui:%d/%m/%Y} : "
            "l'abattement s'appliquera SANS plafond. Si la loi n'en fixe aucun, "
            "écrire la ligne à zéro le dira ; sinon l'avantage accordé sera "
            "supérieur à celui qui est dû"
        )
    return None
