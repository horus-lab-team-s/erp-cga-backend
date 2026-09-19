"""Inscrire un changement de régime fiscal, et refuser ce qui n'en est pas un.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE EXISTE

Le pas 44 a donné au centre le moyen de **voir** qu'un dossier franchit le seuil.
Il n'avait aucun moyen de l'**inscrire** : le portefeuille était entièrement en
lecture seule, et le reclassement devait se poser à la main en base. La
surveillance disait ce qu'il fallait faire ; le faire restait hors du produit.

⚠️ CE QU'UNE INSCRIPTION EST, ET CE QU'ELLE N'EST PAS

Un statut daté **ne se modifie pas**. Changer de régime, c'est fermer la période
en cours à une date, et en ouvrir une nouvelle à la même date. La période fermée
reste, telle qu'elle était : c'est sous ce régime-là que les factures de l'époque
ont été contrôlées, et effacer la période effacerait la justification de ces
contrôles.

⚠️ QUATRE REFUS, ET CHACUN PROTÈGE QUELQUE CHOSE DE PRÉCIS

**Un exercice clos ne change pas de régime.** Ses comptes ont été arrêtés, et
souvent déclarés, sous le régime en vigueur. Inscrire un changement qui le
traverse ferait dire au portefeuille que ces comptes relevaient d'un autre régime
que celui sous lequel ils ont été établis. Une correction de cette nature passe
par l'administration, pas par un formulaire.

**Le même régime n'est pas un changement.** L'inscrire ajouterait une période
sans rien changer, et l'histoire du dossier deviendrait illisible : trois
périodes au réel qui se suivent, dont personne ne saura dire ce qui a changé.

**La cause doit pouvoir produire ce régime.** On ne passe pas à l'IGS par
dépassement de seuil, ni au réel par retour après période probatoire. La table
`CAUSES_ADMISES` le dit, et elle est dans le code parce qu'elle décrit la
mécanique de l'énumération `MotifChangement`, pas une valeur légale datée.

**On ne redescend pas à l'IGS avant la fin de la période probatoire.** C'est le
mécanisme qui empêche une entreprise d'osciller d'un régime à l'autre au gré de sa
conjoncture, et c'est ce qui donne enfin un appelant à
`retour_au_synthetique_admis`, écrite depuis le premier jour et jamais appelée.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from app.contextes.portefeuille.domaine.entites import (
    Entreprise,
    RegimeFiscal,
    StatutRegime,
)
from app.contextes.portefeuille.domaine.regimes import retour_au_synthetique_admis
from app.contextes.portefeuille.domaine.temporel import MotifChangement
from app.partage.copie import transiter

__all__ = [
    "CAUSES_ADMISES",
    "InscriptionRefusee",
    "inscrire_un_regime",
]


class InscriptionRefusee(ValueError):
    """Le changement demandé n'est pas un changement que ce dossier peut recevoir."""


#: Les causes qui peuvent produire chaque régime.
#:
#: ⚠️ **Le passage à l'IGS n'admet que deux causes**, et c'est la ligne qui compte.
#: Si l'option volontaire ou la correction y menaient aussi, elles serviraient de
#: porte de sortie à la période probatoire : on inscrirait « option » là où il
#: faudrait « retour après probatoire », et le contrôle ne jouerait jamais.
#:
#: `DECISION_ADMINISTRATION` est admise des deux côtés et **n'est soumise à aucune
#: période probatoire** : quand l'administration décide, le cabinet enregistre.
#:
#: `CORRECTION` n'est admise nulle part. Corriger un régime mal saisi à l'origine
#: est un besoin réel, mais un tel acte ne se distingue pas d'un retour déguisé, et
#: il réécrit ce que le cabinet croyait vrai au moment des contrôles. Il appartient
#: à une revue à part. **Question ouverte**, à trancher avec le cabinet.
CAUSES_ADMISES: dict[RegimeFiscal, frozenset[MotifChangement]] = {
    RegimeFiscal.REEL: frozenset(
        {
            MotifChangement.DEPASSEMENT_SEUIL,
            MotifChangement.RECLASSEMENT_AUTOMATIQUE,
            MotifChangement.OPTION,
            MotifChangement.DECISION_ADMINISTRATION,
        }
    ),
    RegimeFiscal.IGS: frozenset(
        {
            MotifChangement.RETOUR_APRES_PROBATOIRE,
            MotifChangement.DECISION_ADMINISTRATION,
        }
    ),
}


def inscrire_un_regime(
    entreprise: Entreprise,
    regime: RegimeFiscal,
    a_compter_du: date,
    cause: MotifChangement,
    *,
    justification: str,
    exercices_probatoires: Callable[[date], int],
) -> Entreprise:
    """Rend l'entreprise avec son nouveau régime, ou dit pourquoi il ne s'inscrit pas.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **`exercices_probatoires` est reçu, pas connu, et il est reçu comme une
    question plutôt que comme une réponse.** Il vient du référentiel,
    `IGS_PERIODE_PROBATOIRE_EXERCICES`, résolu à la date d'effet.

    La première version recevait le nombre, déjà résolu par la route. Or le
    référentiel ne connaît ce paramètre qu'à partir du 1er janvier 2026 : toute
    inscription datée avant échouait **sur la lecture du paramètre**, avant même
    que le domaine ait pu dire qu'elle traversait un exercice clos. Le réviseur
    recevait une erreur interne là où il aurait dû lire la vraie raison.

    Le paramètre n'est donc lu **que si la règle en a besoin** : pour un retour à
    l'IGS après période probatoire, et pour rien d'autre.

    ⚠️ **Une date d'effet future est admise, et c'est le cas normal.** Un
    franchissement constaté en novembre prend effet au 1er janvier suivant, et le
    cabinet qui fait son travail l'inscrit dès qu'il le voit. Refuser l'avenir
    obligerait à attendre le 1er janvier pour inscrire, et à s'en souvenir.

    ⚠️ **La date d'effet peut aussi être passée**, pourvu qu'aucun exercice clos
    ne la traverse : un reclassement au 1er janvier se constate souvent en mars,
    une fois les comptes de l'année précédente arrêtés.
    ─────────────────────────────────────────────────────────────────────────────
    """
    if not justification.strip():
        raise InscriptionRefusee(
            "un changement de régime se justifie : lettre de l'administration, "
            "liasse qui constate le dépassement, option signée. Sans elle, personne "
            "ne saura dans trois ans pourquoi ce dossier a changé de régime ce jour-là."
        )

    en_cours = entreprise.regime_courant

    if cause is MotifChangement.CREATION:
        raise InscriptionRefusee(
            "la cause « création » ne sert qu'au premier statut d'une entreprise. "
            "Un changement de régime a une autre cause, et c'est elle qui dira si "
            "la période probatoire s'applique."
        )

    if cause not in CAUSES_ADMISES[regime]:
        admises = ", ".join(sorted(c.value for c in CAUSES_ADMISES[regime]))
        raise InscriptionRefusee(
            f"on ne passe pas au régime {regime.value} pour la cause "
            f"« {cause.value} ». Causes admises : {admises}."
        )

    if a_compter_du <= en_cours.debut:
        raise InscriptionRefusee(
            f"la date d'effet {a_compter_du:%d/%m/%Y} n'est pas postérieure au début "
            f"du statut en cours, le {en_cours.debut:%d/%m/%Y}. Inscrire à cette date "
            "effacerait une période existante, et avec elle le régime sous lequel "
            "les factures de l'époque ont été contrôlées."
        )

    # ⚠️ Comparé au régime **en vigueur à la date d'effet**, et non au régime en
    # cours aujourd'hui : ils sont les mêmes tant qu'aucun changement futur n'est
    # déjà inscrit, et c'est justement quand ils diffèrent que la comparaison
    # compte.
    if entreprise.regime_au(a_compter_du) is regime:
        raise InscriptionRefusee(
            f"le dossier relève déjà du régime {regime.value} au "
            f"{a_compter_du:%d/%m/%Y}. Inscrire le même régime ajouterait une période "
            "sans rien changer, et l'histoire du dossier deviendrait illisible."
        )

    traverses = sorted(
        e.libelle for e in entreprise.exercices if e.clos and e.cloture >= a_compter_du
    )
    if traverses:
        raise InscriptionRefusee(
            f"le changement prendrait effet le {a_compter_du:%d/%m/%Y}, pendant ou avant "
            f"l'exercice clos {', '.join(traverses)}. Ses comptes ont été arrêtés sous "
            "le régime en vigueur ; les faire relever d'un autre après coup "
            "contredirait ce que le cabinet a signé. Une telle correction passe par "
            "l'administration."
        )

    if (
        regime is RegimeFiscal.IGS
        and cause is MotifChangement.RETOUR_APRES_PROBATOIRE
        and not retour_au_synthetique_admis(
            entreprise, a_compter_du, exercices_probatoires(a_compter_du)
        )
    ):
        raise InscriptionRefusee(
            f"le retour à l'IGS n'est pas encore admis : il faut "
            f"{exercices_probatoires(a_compter_du)} exercice(s) clos au réel depuis le "
            f"{en_cours.debut:%d/%m/%Y}. C'est ce qui empêche une entreprise "
            "d'osciller d'un régime à l'autre au gré de sa conjoncture."
        )

    # ⚠️ **Fermer, puis ouvrir.** Le statut en cours reçoit sa date de fin, rien
    # d'autre ; le nouveau commence le même jour. La convention `[debut, fin[` fait
    # que le dernier jour de l'ancien régime est la veille de la date d'effet.
    return transiter(
        entreprise,
        regimes=[
            *[s for s in entreprise.regimes if s is not en_cours],
            transiter(en_cours, fin=a_compter_du),
            StatutRegime(
                debut=a_compter_du,
                regime=regime,
                motif=cause,
                precision=justification.strip(),
            ),
        ],
    )
