"""La déclaration de TVA du mois — le troisième maillon de la chaîne.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE EST LE PLUS IMPORTANT DU PROJET

La proposition de valeur de la plateforme tient en une chaîne :

    constat → attribut fiscal sur la ligne → **TVA du mois** → réintégration DSF

Le contexte D produit les constats. Le contexte E les pose sur les lignes
d'écriture. C'est **ici** qu'ils deviennent de l'argent : la ligne « TVA rejetée
par le contrôle de conformité » de la déclaration mensuelle.

C'est très exactement la ligne L24 de la maquette du parcours comptable, et c'est
elle qui a imposé l'arête `obligations → conformite` lors de l'audit des flux du
9 août. Sans elle, le moteur de conformité produirait des constats que personne ne
consommerait, et le produit perdrait sa raison d'être.

CE QUE CE MODULE APPORTE, ET QU'UNE DÉCLARATION ORDINAIRE NE MONTRE PAS

Dans une déclaration classique, une TVA rejetée est **invisible** : la case
« TVA déductible » porte simplement un chiffre plus faible, et rien n'explique
pourquoi. Personne ne sait jamais ce qui a été perdu.

Ici, le rejet est isolé, chiffré, et justifié pièce par pièce — avec le code de la
règle et le motif en clair. C'est ce que le dirigeant n'a jamais vu, et c'est
l'argument commercial du cabinet.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.contextes.comptabilite.api import EcritureComptable, EtatEcriture, Sens

__all__ = [
    "ComptesTVA",
    "DeclarationTVA",
    "LigneRejet",
    "credit_reporte_au_debut_de",
    "mois_declarables_avant",
    "etablir_declaration_tva",
]


class ComptesTVA(BaseModel):
    """Les comptes où la TVA transite.

    ⚠️ Conventions du plan OHADA, structurelles et supranationales : elles ne
    changent pas à la loi de finances. Ce ne sont donc **pas** des valeurs
    légales, et elles peuvent porter des valeurs par défaut — à la différence
    d'un taux ou d'un seuil.

    Elles deviendront des attributs du plan comptable importé au référentiel le
    jour où le contexte A portera `PlanComptableReference`.
    """

    model_config = ConfigDict(frozen=True)

    prefixe_collectee: str = "443"
    prefixe_deductible: str = "445"


class LigneRejet(BaseModel):
    """Une TVA refusée, avec de quoi la justifier trois ans plus tard."""

    model_config = ConfigDict(frozen=True)

    piece: str | None
    ecriture: str
    compte: str
    montant: Decimal
    code_regle: str | None
    motif: str | None


class DeclarationTVA(BaseModel):
    """Ce que l'entreprise doit au titre d'une période, et pourquoi."""

    model_config = ConfigDict(frozen=True)

    entreprise: str
    periode_debut: date
    periode_fin: date

    tva_collectee: Decimal
    tva_deductible_theorique: Decimal

    #: La ligne qui fait toute la valeur du produit.
    tva_rejetee: Decimal

    tva_deductible_admise: Decimal
    credit_reporte_anterieur: Decimal

    #: Positif : montant à verser au Trésor. Négatif : crédit de TVA.
    solde: Decimal

    detail_rejets: list[LigneRejet] = Field(default_factory=list)

    @computed_field
    @property
    def tva_a_payer(self) -> Decimal:
        return self.solde if self.solde > 0 else Decimal(0)

    @computed_field
    @property
    def credit_a_reporter(self) -> Decimal:
        """L'excédent de TVA déductible, reportable sur la période suivante.

        Arrive quand l'entreprise a beaucoup investi ou beaucoup exporté. Ce n'est
        pas une anomalie : c'est le fonctionnement normal du paiement fractionné.
        """
        return -self.solde if self.solde < 0 else Decimal(0)

    @computed_field
    @property
    def neant(self) -> bool:
        """Aucune opération taxable dans la période.

        ⚠️ La déclaration reste **due**. L'obligation naît de l'assujettissement,
        pas de l'activité — et l'on est sanctionné pour n'avoir pas déclaré qu'on
        n'avait rien à déclarer.
        """
        return self.tva_collectee == 0 and self.tva_deductible_theorique == 0

    @computed_field
    @property
    def cout_de_la_non_conformite(self) -> Decimal:
        """Ce que les constats du contexte D ont coûté sur cette seule période."""
        return self.tva_rejetee


def etablir_declaration_tva(
    ecritures: list[EcritureComptable],
    *,
    entreprise: str,
    periode_debut: date,
    periode_fin: date,
    credit_reporte_anterieur: Decimal = Decimal(0),
    comptes: ComptesTVA | None = None,
) -> DeclarationTVA:
    """Établit la déclaration à partir des écritures de la période.

    Seules les écritures **validées** sont retenues. Un brouillon n'est pas de la
    comptabilité : c'est une intention, et fonder une déclaration dessus
    produirait un chiffre que personne ne pourrait justifier.

    Le rejet n'est pas recalculé ici : il est **lu** sur les attributs fiscaux que
    le contexte E a posés à partir des constats du contexte D. Ce module ne
    connaît aucune règle de déductibilité, et c'est ce qui garantit que la
    déclaration dit exactement la même chose que le rapport de conformité.
    """
    plan = comptes or ComptesTVA()

    collectee = Decimal(0)
    deductible = Decimal(0)
    rejetee = Decimal(0)
    rejets: list[LigneRejet] = []

    for ecriture in ecritures:
        if ecriture.etat is not EtatEcriture.VALIDEE:
            continue
        if not (periode_debut <= ecriture.date_operation <= periode_fin):
            continue

        for ligne in ecriture.lignes:
            # ⚠️ PAS 110 : LES MONTANTS SONT NETS DE LEUR SENS CONTRAIRE.
            #
            # Seuls comptaient les crédits du 443 et les débits du 445. Une contre-passation
            # d'achat (le 445 au crédit), un avoir reçu, une vente annulée (le 443 au débit)
            # étaient **ignorés** : essai, une facture de juillet dont la TVA de 269 500 FCFA
            # avait été déduite, contre-passée et validée en août, laissait la déclaration
            # d'août inchangée. La TVA d'une facture annulée restait déduite.
            signe = 1 if ligne.sens is Sens.CREDIT else -1
            if ligne.compte.startswith(plan.prefixe_collectee):
                collectee += signe * ligne.montant

            elif ligne.compte.startswith(plan.prefixe_deductible):
                deductible -= signe * ligne.montant
                attribut = ligne.attribut_fiscal
                if attribut is not None and attribut.rejette_tva:
                    # L'inverse d'un rejet (une contre-passation reprend l'attribut de sa
                    # ligne) annule le rejet : il se retranche, et se montre en négatif.
                    rejetee -= signe * ligne.montant
                    rejets.append(
                        LigneRejet(
                            piece=ecriture.piece_justificative,
                            ecriture=ecriture.cle,
                            compte=ligne.compte,
                            montant=-signe * ligne.montant,
                            code_regle=attribut.code_regle_origine,
                            motif=attribut.motif_non_deductibilite,
                        )
                    )

    admise = deductible - rejetee
    solde = collectee - admise - credit_reporte_anterieur

    return DeclarationTVA(
        entreprise=entreprise,
        periode_debut=periode_debut,
        periode_fin=periode_fin,
        tva_collectee=collectee,
        tva_deductible_theorique=deductible,
        tva_rejetee=rejetee,
        tva_deductible_admise=admise,
        credit_reporte_anterieur=credit_reporte_anterieur,
        solde=solde,
        detail_rejets=sorted(rejets, key=lambda r: (-r.montant, r.ecriture)),
    )


def credit_reporte_au_debut_de(
    periodes_anterieures: list[tuple[date, date]],
    ecritures: list[EcritureComptable],
    *,
    comptes: ComptesTVA | None = None,
) -> Decimal:
    """Le crédit de TVA à reporter au début d'une période, enchaîné depuis la première (pas 109).

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ LE DÉFAUT QUE CETTE FONCTION CORRIGE

    Le crédit reporté arrivait **en paramètre de la requête**, avec zéro pour défaut, et aucun
    écran ne le passait. Un mois de gros investissement produisait un crédit, et le mois suivant
    le déclarait perdu : la TVA à payer était **surestimée** d'autant, au détriment de
    l'adhérent, et le bordereau déposé le figeait. N'importe quel appelant pouvait aussi y écrire
    le montant de son choix.

    Le crédit se calcule donc ici, période après période : chaque déclaration reçoit le crédit
    que la précédente laisse. `periodes_anterieures` : toutes les périodes déclarables avant
    celle qu'on établit, dans l'ordre, d'assujettissement uniquement ; `ecritures` : celles de
    tous les exercices qui les couvrent.

    ⚠️ Le point de départ est la première période connue au dossier, avec un crédit nul. Un
    dossier repris avec un crédit antérieur à la plateforme ne le retrouve pas encore : il faudra
    l'inscrire comme une donnée de reprise, datée et justifiée, et non le recevoir d'une requête.
    ─────────────────────────────────────────────────────────────────────────────
    """
    credit = Decimal(0)
    for debut, fin in sorted(periodes_anterieures):
        credit = etablir_declaration_tva(
            ecritures,
            entreprise="",
            periode_debut=debut,
            periode_fin=fin,
            credit_reporte_anterieur=credit,
            comptes=comptes,
        ).credit_a_reporter
    return credit


def mois_declarables_avant(
    premier_jour: date, periode_debut: date, assujettie_au: Callable[[date], bool]
) -> list[tuple[date, date]]:
    """Les mois calendaires de `premier_jour` au mois qui précède `periode_debut`, où le dossier
    était assujetti à la fin du mois (pas 109).

    ⚠️ Un mois sans régime connu (`assujettie_au` lève `LookupError` : un exercice ouvert avant
    l'adhésion) n'est **pas** déclarable. Le compter ferait naître du crédit d'écritures qu'aucune
    déclaration n'a jamais portées.
    """
    periodes = []
    mois = premier_jour.replace(day=1)
    while mois < periode_debut.replace(day=1):
        fin = (mois + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        try:
            assujettie = assujettie_au(fin)
        except LookupError:
            assujettie = False
        if assujettie:
            periodes.append((mois, fin))
        mois = fin + timedelta(days=1)
    return periodes
