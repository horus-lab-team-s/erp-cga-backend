"""La clôture d'un exercice, et le report de ses soldes sur le suivant.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE EXISTE

Sans lui, la plateforme ne survit pas à sa deuxième année.

Le contexte savait déjà **lire** un exercice terminé : la liasse fiscale
s'assemble, le tableau de passage se calcule, la balance boucle. Il ne savait pas
le **fermer**. L'entité `Exercice` porte un attribut `clos` depuis le premier
jour, et rien dans le produit ne le posait jamais : c'est une capacité sans
appelant, le même défaut que la veille des dossiers avant le pas 28.

Et fermer ne suffit pas. Un exercice fermé dont les soldes ne passent pas au
suivant laisse une entreprise qui recommence chaque janvier avec une caisse vide,
aucun fournisseur à payer et un capital disparu.

⚠️ CE QUE L'À-NOUVEAU REPORTE, ET CE QU'IL NE REPORTE PAS

    classes 1 à 5   comptes de **situation**   reportés, solde pour solde
    classes 6 à 8   comptes de **gestion**     remis à zéro, jamais reportés

C'est la distinction la plus structurante de la comptabilité, et la seule que ce
module ait besoin de connaître. Une charge de transport payée en 2026 ne pèse pas
sur 2027 : elle a joué son rôle, elle a formé le résultat, et c'est **le résultat**
qui passe, pas la charge.

Reporter un compte de gestion doublerait les charges de l'exercice suivant, et le
bénéfice imposable en serait faux dès le premier jour — d'un montant que personne
ne saurait retrouver, puisque rien ne distingue alors la charge reportée de la
charge réelle.

⚠️ LE BOUCLAGE N'EST PAS UNE VÉRIFICATION ANNEXE : C'EST LA PREUVE

`controle_bouclage` dit que la somme des soldes de situation, en débit moins
crédit, vaut exactement le résultat. Formulé autrement : **l'écriture d'à-nouveau
s'équilibre par construction**, dès lors que la balance de départ est équilibrée.

Ce n'est donc pas un contrôle qu'on ajoute par prudence. C'est la raison pour
laquelle l'opération est possible, et un bouclage qui échoue signale que la
balance est fausse, pas que l'à-nouveau l'est.

⚠️ POINT DE VARIATION LAISSÉ OUVERT : L'AFFECTATION DU RÉSULTAT

Le résultat est porté au compte `13 Résultat net de l'exercice`, et **il y reste**.
Le répartir entre réserves, report à nouveau et dividendes est une décision de
l'assemblée des associés, prise après la clôture, parfois des mois après, et qui
peut ne jamais être prise.

Ce module ne la prend pas à leur place. Un logiciel qui virerait d'office le
résultat en report à nouveau écrirait dans les comptes une décision que personne
n'a votée, et le procès-verbal de l'assemblée ne correspondrait plus aux livres.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.contextes.comptabilite.contrats import (
    Compte,
    DestinationCompte,
    EcritureComptable,
    EtatEcriture,
    LigneEcriture,
    Sens,
    SoldeCompte,
    controle_balance_equilibree,
    controle_bouclage,
    sequences_incompletes,
)

__all__ = [
    "COMPTE_RESULTAT_NET",
    "JOURNAL_DES_A_NOUVEAUX",
    "Empechement",
    "MotifEmpechement",
    "lignes_d_a_nouveau",
    "obstacles_a_la_cloture",
]

#: Le compte SYSCOHADA qui reçoit le résultat de l'exercice. Bénéfice au crédit,
#: perte au débit.
COMPTE_RESULTAT_NET = "13"

#: ⚠️ Le journal est **séparé des opérations diverses**, et la raison est dans
#: `plan_syscohada.py` : un journal des à-nouveaux doit porter une écriture par
#: exercice, et toute autre chose y est suspecte.
JOURNAL_DES_A_NOUVEAUX = "AN"


class MotifEmpechement(StrEnum):
    """Ce qui interdit de clore. Une valeur par cause, jamais un texte libre.

    ⚠️ Un écran doit pouvoir proposer **le bon geste suivant** selon la cause :
    valider les brouillons n'est pas la même action qu'ouvrir l'exercice suivant.
    Un message seul obligerait l'interface à le relire, et elle le relirait mal.
    """

    BROUILLON_SUBSISTANT = "BROUILLON_SUBSISTANT"
    BALANCE_DESEQUILIBREE = "BALANCE_DESEQUILIBREE"
    BOUCLAGE_ROMPU = "BOUCLAGE_ROMPU"
    SEQUENCE_TROUEE = "SEQUENCE_TROUEE"
    COMPTE_HORS_PLAN = "COMPTE_HORS_PLAN"
    EXERCICE_DEJA_CLOS = "EXERCICE_DEJA_CLOS"
    EXERCICE_ANTERIEUR_OUVERT = "EXERCICE_ANTERIEUR_OUVERT"
    EXERCICE_SUIVANT_ABSENT = "EXERCICE_SUIVANT_ABSENT"
    #: Ajouté au pas 55. La clôture acceptait un exercice dont la date de fin n'était
    #: pas passée : voir `clore_un_exercice`.
    EXERCICE_NON_TERMINE = "EXERCICE_NON_TERMINE"
    RIEN_A_CLORE = "RIEN_A_CLORE"


class Empechement(BaseModel):
    """Une cause de refus, nommée et expliquée."""

    model_config = ConfigDict(frozen=True)

    motif: MotifEmpechement
    explication: str = Field(min_length=1)
    #: Ce qui est en cause : les clés des écritures, les numéros de comptes. Vide
    #: quand la cause ne désigne rien de précis.
    en_cause: tuple[str, ...] = ()


def obstacles_a_la_cloture(
    ecritures: list[EcritureComptable],
    soldes: list[SoldeCompte],
    *,
    plan: list[Compte],
    deja_clos: bool,
) -> tuple[Empechement, ...]:
    """Tout ce qui empêche, d'un coup, jamais le premier seul.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LE REFUS EST COMPLET, POUR LA MÊME RAISON QUE LA REPRISE.**

    Clore est un acte de fin de mission, souvent conduit sous délai : la déclaration
    statistique et fiscale a une date. Rendre le premier obstacle seul obligerait le
    comptable à revenir autant de fois qu'il y a de causes, et chacune demande un
    travail différent — valider des brouillons, corriger une séquence, ouvrir un
    exercice.

    ⚠️ **LES BROUILLONS SONT LE PREMIER OBSTACLE, ET LE PLUS FRÉQUENT.**

    Un brouillon au moment de clore est soit une écriture à valider, soit une
    écriture à corriger puis valider, et personne d'autre que le comptable ne peut
    trancher. ⚠️ Ce texte disait « à supprimer » : aucune route ne supprime, et une
    suppression laisserait un trou de séquence que la clôture refuse aussi (pas 72).
    Le laisser produirait une comptabilité dont la balance ne dit pas la même chose
    que le journal : le brouillon n'entre pas dans les soldes, mais il occupe un
    numéro, et le vérificateur qui compare les deux trouve un trou.
    ─────────────────────────────────────────────────────────────────────────────
    """
    obstacles: list[Empechement] = []

    if deja_clos:
        obstacles.append(
            Empechement(
                motif=MotifEmpechement.EXERCICE_DEJA_CLOS,
                explication=(
                    "cet exercice est déjà clos. Le clore une seconde fois "
                    "produirait un second à-nouveau, et doublerait tous les soldes "
                    "de l'exercice suivant."
                ),
            )
        )

    if not ecritures:
        obstacles.append(
            Empechement(
                motif=MotifEmpechement.RIEN_A_CLORE,
                explication=(
                    "aucune écriture sur cet exercice. Clore un exercice vide "
                    "n'apporte rien et interdirait d'y saisir la comptabilité qui "
                    "reste peut-être à reprendre."
                ),
            )
        )

    brouillons = tuple(
        e.cle for e in ecritures if e.etat is not EtatEcriture.VALIDEE
    )
    if brouillons:
        obstacles.append(
            Empechement(
                motif=MotifEmpechement.BROUILLON_SUBSISTANT,
                explication=(
                    f"{len(brouillons)} écriture(s) non validée(s). Chacune est à "
                    "valider, ou à corriger puis valider, et le comptable seul peut "
                    "trancher : "
                    "un brouillon n'entre pas dans les soldes mais occupe un numéro, "
                    "et le vérificateur qui compare la balance au journal trouve un "
                    "trou."
                ),
                en_cause=brouillons[:20],
            )
        )

    trous = sequences_incompletes(
        [e for e in ecritures if e.etat is EtatEcriture.VALIDEE]
    )
    if trous:
        obstacles.append(
            Empechement(
                motif=MotifEmpechement.SEQUENCE_TROUEE,
                explication=(
                    "la numérotation présente des trous ou des doublons. "
                    "⚠️ C'est le signal d'alerte le plus fort en contrôle fiscal, et "
                    "l'administration le cherche systématiquement. Clore par-dessus "
                    "reviendrait à figer l'anomalie."
                ),
                en_cause=tuple(f"{t.journal} {t.exercice}" for t in trous)[:20],
            )
        )

    if not controle_balance_equilibree(soldes):
        obstacles.append(
            Empechement(
                motif=MotifEmpechement.BALANCE_DESEQUILIBREE,
                explication=(
                    "la balance ne s'équilibre pas. Chaque écriture étant équilibrée "
                    "par le domaine, c'est la projection qui est fautive, pas la "
                    "comptabilité : le signaler ici évite de reporter un écart."
                ),
            )
        )
    elif not controle_bouclage(soldes):
        # ⚠️ Le bouclage n'a de sens que sur une balance équilibrée : le tester
        # sur une balance fausse rendrait deux obstacles pour une seule cause, et
        # le comptable chercherait deux corrections là où il n'y en a qu'une.
        obstacles.append(
            Empechement(
                motif=MotifEmpechement.BOUCLAGE_ROMPU,
                explication=(
                    "le bilan ne boucle pas : la somme des comptes de situation ne "
                    "vaut pas le résultat. L'écriture d'à-nouveau serait "
                    "déséquilibrée, donc refusée, et la clôture échouerait plus loin "
                    "avec un message moins clair."
                ),
            )
        )

    au_plan = {compte.numero for compte in plan}
    hors_plan = tuple(sorted({s.compte for s in soldes} - au_plan))
    if hors_plan:
        obstacles.append(
            Empechement(
                motif=MotifEmpechement.COMPTE_HORS_PLAN,
                explication=(
                    "des comptes mouvementés ne figurent pas au plan. Leur solde ne "
                    "saurait pas où atterrir à l'à-nouveau, et disparaîtrait sans "
                    "que rien ne le dise."
                ),
                en_cause=hors_plan[:20],
            )
        )

    return tuple(obstacles)


def lignes_d_a_nouveau(
    soldes: list[SoldeCompte],
    *,
    plan: list[Compte],
    compte_resultat: str = COMPTE_RESULTAT_NET,
) -> list[LigneEcriture]:
    """Les lignes qui rouvrent l'exercice suivant sur les soldes du précédent.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LES COMPTES DE SITUATION PASSENT, LES COMPTES DE GESTION NON.**

    La destination vient du plan, jamais du premier chiffre lu ici : c'est
    l'entité `Compte` qui sait, et le lui redemander ferait un second classement
    qui divergerait du premier.

    ⚠️ **UN SOLDE NUL NE PRODUIT PAS DE LIGNE.**

    Un compte mouvementé qui retombe à zéro — un compte d'attente soldé, une TVA
    déclarée et payée — n'a rien à reporter. Lui écrire une ligne à zéro serait
    refusé par le pivot, qui exige un montant strictement positif, et ce refus
    serait juste : une ligne à zéro n'est pas une information, c'est du bruit dans
    un grand livre qui se lit à la main pendant dix ans.

    ⚠️ **LES COMPTES HORS BILAN NE PASSENT PAS DAVANTAGE.**

    Les engagements donnés et reçus de la classe 9 sont suivis en dehors du bilan
    et se reconstituent chaque année depuis les contrats. Les reporter
    mécaniquement ferait vivre un engagement éteint.
    ─────────────────────────────────────────────────────────────────────────────
    """
    destinations = {compte.numero: compte.destination for compte in plan}
    lignes: list[LigneEcriture] = []

    for solde in soldes:
        if destinations.get(solde.compte) is not DestinationCompte.BILAN:
            continue
        net = solde.total_debit - solde.total_credit
        if net == 0:
            continue
        lignes.append(
            LigneEcriture(
                compte=solde.compte,
                libelle="À-nouveau",
                sens=Sens.DEBIT if net > 0 else Sens.CREDIT,
                montant=abs(net),
            )
        )

    gain = sum(
        (s.total_credit - s.total_debit for s in soldes
         if destinations.get(s.compte) is DestinationCompte.RESULTAT),
        Decimal(0),
    )
    if gain != 0:
        # ⚠️ Bénéfice au **crédit**, perte au **débit**. Le compte 13 est un compte
        # de capitaux propres : un bénéfice enrichit l'entreprise, donc augmente le
        # passif, donc se porte au crédit. L'inverser ferait boucler le bilan à un
        # signe près, et le bilan bouclerait quand même — l'erreur ne se verrait
        # qu'au compte de résultat, c'est-à-dire à la liasse.
        lignes.append(
            LigneEcriture(
                compte=compte_resultat,
                libelle="Résultat de l'exercice précédent",
                sens=Sens.CREDIT if gain > 0 else Sens.DEBIT,
                montant=abs(gain),
            )
        )

    return lignes
