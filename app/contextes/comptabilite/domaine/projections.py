"""Projections du contexte E · balance, grand livre, contrôles de continuité.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE SONT DES FONCTIONS ET NON DES ENTITÉS

La balance et le grand livre ne sont pas des objets qu'on enregistre : ce sont
deux lectures d'un même jeu d'écritures. Le grand livre les classe par compte, la
balance n'en garde que les soldes.

Les persister créerait une seconde vérité. Elle divergerait de la première au
premier incident — une écriture rejouée, une contre-passation oubliée — et il
serait alors impossible de savoir laquelle des deux ment. Une comptabilité n'a
qu'une source : le journal.

Ces fonctions sont donc **pures** : mêmes écritures, même résultat, sans
entrée-sortie, sans horloge, sans configuration. C'est ce qui les rend testables
sans base et vérifiables à la main.

CE QUI SE VÉRIFIE ICI

Trois contrôles que le § 5.5 du manuel appelle « ce que le contrôleur refera » :
l'équilibre de la balance, le bouclage du bilan par le résultat, et la continuité
des séquences de numérotation. Aucun ne dépend du droit fiscal ; tous relèvent de
la structure.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, computed_field

from app.contextes.comptabilite.domaine.entites import (
    Compte,
    EcritureComptable,
    EtatEcriture,
    Sens,
)

__all__ = [
    "LigneGrandLivre",
    "SoldeCompte",
    "TrouSequence",
    "COMPTES_DU_CHIFFRE_D_AFFAIRES",
    "balance",
    "balance_par_racine",
    "chiffre_affaires",
    "controle_balance_equilibree",
    "controle_bouclage",
    "grand_livre",
    "resultat",
    "sequences_incompletes",
]


# ── La balance ───────────────────────────────────────────────────────────────────


class SoldeCompte(BaseModel):
    """Le solde d'un compte : ses deux totaux, et la différence.

    On conserve les **deux totaux** et pas seulement leur différence. Un compte
    fournisseur soldé à zéro après un million de mouvements ne dit pas la même
    chose qu'un compte fournisseur jamais mouvementé, et la balance doit
    distinguer les deux.
    """

    model_config = ConfigDict(frozen=True)

    compte: str
    total_debit: Decimal
    total_credit: Decimal

    @computed_field
    @property
    def solde(self) -> Decimal:
        """Positif si débiteur, négatif si créditeur."""
        return self.total_debit - self.total_credit

    @computed_field
    @property
    def sens_solde(self) -> Sens | None:
        if self.solde > 0:
            return Sens.DEBIT
        if self.solde < 0:
            return Sens.CREDIT
        return None

    @computed_field
    @property
    def solde_debiteur(self) -> Decimal:
        return self.solde if self.solde > 0 else Decimal(0)

    @computed_field
    @property
    def solde_crediteur(self) -> Decimal:
        return -self.solde if self.solde < 0 else Decimal(0)

    @property
    def classe(self) -> int:
        return int(self.compte[0])


def balance(
    ecritures: list[EcritureComptable],
    *,
    brouillons_inclus: bool = False,
) -> list[SoldeCompte]:
    """Les soldes de tous les comptes mouvementés, triés par numéro.

    Par défaut, **seules les écritures validées sont retenues**. Un brouillon
    n'est pas de la comptabilité : c'est une intention. Mêler les deux dans une
    balance qui sert à établir une déclaration produirait un chiffre que personne
    ne pourrait justifier.

    `brouillons_inclus` existe pour un seul usage légitime : montrer au comptable
    l'effet de sa saisie en cours avant qu'il ne valide.
    """
    debits: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    credits: dict[str, Decimal] = defaultdict(lambda: Decimal(0))

    for ecriture in ecritures:
        if not brouillons_inclus and ecriture.etat is not EtatEcriture.VALIDEE:
            continue
        for ligne in ecriture.lignes:
            if ligne.au_debit:
                debits[ligne.compte] += ligne.montant
            else:
                credits[ligne.compte] += ligne.montant

    comptes = sorted(set(debits) | set(credits))
    return [
        SoldeCompte(compte=c, total_debit=debits[c], total_credit=credits[c]) for c in comptes
    ]


def balance_par_racine(
    soldes: list[SoldeCompte],
    plan: list[Compte],
) -> list[SoldeCompte]:
    """Agrège la balance sur les comptes OHADA de référence.

    ⚠️ C'EST CETTE PROJECTION, ET ELLE SEULE, QUI ALIMENTE LA LIASSE FISCALE.

    Un adhérent qui crée « 401100 Fournisseurs locaux » et « 401200 Fournisseurs
    étrangers » a raison de le faire pour son suivi. Mais le poste « Dettes
    fournisseurs » de la DSF attend le solde de 401, pas de deux sous-comptes
    qu'aucun modèle officiel ne connaît.

    Un compte absent du plan est **refusé**, jamais ignoré : un solde qui
    disparaîtrait silencieusement de la liasse est exactement le défaut que cette
    fonction existe pour empêcher.
    """
    racines = {compte.numero: compte.racine for compte in plan}
    inconnus = sorted({s.compte for s in soldes if s.compte not in racines})
    if inconnus:
        raise ValueError(
            f"comptes absents du plan : {', '.join(inconnus)}. Leur solde n'atteindrait "
            "aucun poste de la liasse. Les déclarer au plan et les rattacher à un compte "
            "OHADA de référence."
        )

    debits: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    credits: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for solde in soldes:
        racine = racines[solde.compte]
        debits[racine] += solde.total_debit
        credits[racine] += solde.total_credit

    return [
        SoldeCompte(compte=c, total_debit=debits[c], total_credit=credits[c])
        for c in sorted(set(debits) | set(credits))
    ]


# ── Le grand livre ───────────────────────────────────────────────────────────────


class LigneGrandLivre(BaseModel):
    """Un mouvement d'un compte, avec le solde progressif après ce mouvement."""

    model_config = ConfigDict(frozen=True)

    cle_ecriture: str
    #: Le rang de la ligne dans son écriture, à partir de 0 : c'est ce qui désigne une ligne
    #: à lettrer (pas 108), une écriture pouvant mouvementer deux fois le même compte.
    rang: int = 0
    journal: str
    date_operation: date
    libelle: str
    sens: Sens
    montant: Decimal
    tiers: str | None
    lettrage: str | None
    #: D'où vient la lettre (pas 108) : `PLATEFORME` (un lettrage fait ici, qui se défait),
    #: `REPRISE` (la lettre du logiciel du client, affichée telle quelle), ou rien.
    lettrage_origine: str | None = None
    #: L'identifiant du lettrage fait ici, pour le défaire.
    lettrage_identifiant: str | None = None
    #: La pièce justificative de l'écriture : « toute ligne remonte à sa pièce en un clic »
    #: (maquette, vue C, note 2).
    piece_justificative: str | None = None
    solde_progressif: Decimal


def grand_livre(
    ecritures: list[EcritureComptable],
    compte: str,
    *,
    brouillons_inclus: bool = False,
    lettrages: dict | None = None,
) -> list[LigneGrandLivre]:
    """L'histoire complète d'un compte, dans l'ordre chronologique.

    Le tri se fait sur la date d'opération puis sur le numéro d'écriture : deux
    écritures du même jour se lisent dans l'ordre où elles ont été passées, ce qui
    est l'ordre du journal papier.

    `lettrages` (pas 108) : les lettrages actifs indexés par `(clé, rang)`, voir
    `application/lettrage.lettrages_par_ligne`. Leur lettre prime sur celle d'une reprise,
    qu'une ligne lettrée ici ne peut de toute façon pas porter.
    """
    lettrages = lettrages or {}
    retenues = [
        e
        for e in ecritures
        if brouillons_inclus or e.etat is EtatEcriture.VALIDEE
    ]
    retenues.sort(key=lambda e: (e.date_operation, e.journal, e.numero))

    lignes: list[LigneGrandLivre] = []
    solde = Decimal(0)
    for ecriture in retenues:
        for rang, ligne in enumerate(ecriture.lignes):
            if ligne.compte != compte:
                continue
            solde += ligne.montant if ligne.au_debit else -ligne.montant
            fait_ici = lettrages.get((ecriture.cle, rang))
            lignes.append(
                LigneGrandLivre(
                    cle_ecriture=ecriture.cle,
                    rang=rang,
                    journal=ecriture.journal,
                    date_operation=ecriture.date_operation,
                    libelle=ligne.libelle,
                    sens=ligne.sens,
                    montant=ligne.montant,
                    tiers=ligne.tiers,
                    lettrage=fait_ici.lettre if fait_ici else ligne.lettrage,
                    lettrage_origine="PLATEFORME"
                    if fait_ici
                    else "REPRISE"
                    if ligne.lettrage
                    else None,
                    lettrage_identifiant=fait_ici.identifiant if fait_ici else None,
                    piece_justificative=ecriture.piece_justificative,
                    solde_progressif=solde,
                )
            )
    return lignes


# ── Les contrôles ────────────────────────────────────────────────────────────────


def controle_balance_equilibree(soldes: list[SoldeCompte]) -> bool:
    """Total des débits égal au total des crédits.

    C'est la conséquence mécanique de l'équilibre de chaque écriture. Si ce
    contrôle échoue alors que toutes les écritures sont équilibrées, c'est la
    projection qui est fautive, pas la comptabilité.
    """
    return sum((s.total_debit for s in soldes), Decimal(0)) == sum(
        (s.total_credit for s in soldes), Decimal(0)
    )


def resultat(soldes: list[SoldeCompte]) -> Decimal:
    """Le résultat de l'exercice : produits moins charges.

    Calculé sur les classes 6, 7 et 8 en sommant `crédit − débit`. La formule est
    la même pour les trois classes, et c'est ce qui la rend juste : un compte de
    produit est créditeur donc contribue positivement, un compte de charge est
    débiteur donc négativement, et la classe 8 — qui mélange charges et produits
    hors activités ordinaires — se répartit d'elle-même.

    Positif = bénéfice, négatif = perte.
    """
    return sum(
        (s.total_credit - s.total_debit for s in soldes if 6 <= s.classe <= 8),
        Decimal(0),
    )


#: Les racines qui composent le chiffre d'affaires.
#:
#: ⚠️ **LE CHIFFRE D'AFFAIRES N'EST PAS LA CLASSE 7.**
#:
#: La classe 7 porte tous les produits, y compris ceux qui ne sont pas du chiffre
#: d'affaires : les intérêts de prêts reçus (77) et les transferts de charges
#: (78). Les compter gonflerait le chiffre d'affaires d'une entreprise qui place
#: sa trésorerie ou qui refacture des frais.
#:
#: La conséquence est précise et coûteuse : c'est ce chiffre qui se compare au
#: seuil d'assujettissement. Un chiffre gonflé déclenche un reclassement au réel
#: que l'entreprise ne doit pas, lui fait prendre un numéro de TVA, facturer avec
#: taxe, et déposer des déclarations dont elle n'était pas redevable.
#:
#: Le compte 70 seul, donc : ventes de marchandises, de produits finis, services
#: vendus et produits accessoires.
COMPTES_DU_CHIFFRE_D_AFFAIRES: tuple[str, ...] = ("70",)


def chiffre_affaires(
    soldes: list[SoldeCompte],
    *,
    racines: tuple[str, ...] = COMPTES_DU_CHIFFRE_D_AFFAIRES,
) -> Decimal:
    """Le chiffre d'affaires de la période, lu sur les soldes.

    ─────────────────────────────────────────────────────────────────────────────
    Somme de `crédit − débit` sur les comptes de vente. Le sens compte : une vente
    est créditrice, un avoir est débiteur, et la soustraction fait que **les avoirs
    diminuent le chiffre d'affaires**, ce qui est le résultat voulu. Prendre le
    seul total au crédit donnerait le chiffre d'affaires brut d'une entreprise qui
    annule la moitié de ses ventes.

    ⚠️ **Aucun arrondi, aucune conversion.** Le franc CFA n'a pas de décimale, et
    le pivot le garantit déjà à la ligne. Ajouter ici un arrondi ferait une
    seconde règle d'arrondi, qui divergerait de la première le jour où l'une
    changerait.

    ⚠️ **Rendu même négatif.** Une entreprise dont les avoirs excèdent les ventes
    d'une période a un chiffre d'affaires négatif, et c'est une information : la
    masquer à zéro ferait croire à une activité nulle là où il y a eu des
    annulations massives.
    ─────────────────────────────────────────────────────────────────────────────
    """
    return sum(
        (
            s.total_credit - s.total_debit
            for s in soldes
            if s.compte.startswith(racines)
        ),
        Decimal(0),
    )


def controle_bouclage(soldes: list[SoldeCompte]) -> bool:
    """Le bilan boucle une fois le résultat reporté au passif.

    Traduction : la somme des soldes des classes 1 à 5, exprimée en débit moins
    crédit, doit être **égale au résultat**.

    La démonstration tient en trois lignes. La balance étant équilibrée, la somme
    de tous les soldes en débit moins crédit est nulle. En séparant les comptes de
    situation des comptes de gestion :

        Σ(1→5) (D − C)  +  Σ(6→8) (D − C)  =  0

    Or le résultat est défini comme Σ(6→8) (C − D), soit l'opposé du second terme.
    Il vient donc `situation = résultat` — ce qui est exactement la traduction
    comptable du fait que l'actif n'égale le passif qu'une fois le résultat
    reporté.

    C'est le premier contrôle que fait un contrôleur, et le premier qu'il faut
    faire avant tout dépôt.
    """
    situation = sum(
        (s.total_debit - s.total_credit for s in soldes if s.classe <= 5),
        Decimal(0),
    )
    return situation == resultat(soldes)


class TrouSequence(BaseModel):
    """Un défaut de continuité dans la numérotation d'un journal."""

    model_config = ConfigDict(frozen=True)

    exercice: str
    journal: str
    numeros_manquants: list[int]
    numeros_en_double: list[int]


def sequences_incompletes(ecritures: list[EcritureComptable]) -> list[TrouSequence]:
    """Détecte les trous et les doublons de numérotation, par exercice et journal.

    Un trou signale une écriture supprimée — ce que le système interdit, mais
    qu'une reprise de données ou une migration peut produire. C'est le signal
    d'alerte le plus fort qui soit en contrôle : l'administration le cherche
    systématiquement, et sur les factures de vente comme sur les journaux.

    La numérotation est réputée commencer à 1 dans chaque journal.
    """
    par_journal: dict[tuple[str, str], list[int]] = defaultdict(list)
    for ecriture in ecritures:
        par_journal[(ecriture.exercice, ecriture.journal)].append(ecriture.numero)

    anomalies: list[TrouSequence] = []
    for (exercice, journal), numeros in sorted(par_journal.items()):
        vus = sorted(numeros)
        attendus = set(range(1, max(vus) + 1))
        manquants = sorted(attendus - set(vus))
        doublons = sorted({n for n in vus if vus.count(n) > 1})
        if manquants or doublons:
            anomalies.append(
                TrouSequence(
                    exercice=exercice,
                    journal=journal,
                    numeros_manquants=manquants,
                    numeros_en_double=doublons,
                )
            )
    return anomalies
