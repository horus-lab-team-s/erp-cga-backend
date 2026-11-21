"""Les déclarations annuelles par tiers de la DGI : achats par fournisseur, sommes versées
aux tiers (15 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LA DGI DEMANDE

Deux modèles publics (impots.cm, « MODELE FOURNISSEURS » et « MODELE TIERS ») : une ligne
par tiers, et pour chacun quatre montants, chacun en HT et en TVA :
    solde d'ouverture · facturation de l'exercice · paiement de l'exercice · solde de clôture

D'OÙ VIENT CHAQUE MONTANT (écritures VALIDÉES seules, sur les comptes 40)
    facturation   une écriture qui CRÉDITE le compte du fournisseur : le HT est la somme
                  de ses lignes de charge ou d'immobilisation, la TVA celle de ses lignes
                  445. EXACTE. Un avoir (le compte du fournisseur débité, sans banque ni
                  caisse dans l'écriture) vient en diminution.
    paiement      une écriture qui DÉBITE le compte du fournisseur avec une ligne de
                  trésorerie (classe 5) : un règlement, TTC.
    ouverture     le journal des à-nouveaux (AN), TTC.
    clôture       ouverture + facturation − paiement, TTC.

⚠️ CE QUI EST UNE RÉPARTITION, ET LE DIT
Un règlement ou un solde ne porte pas de TVA en comptabilité : c'est un montant TTC. Il
est réparti entre HT et TVA à la proportion des factures du même tiers sur l'exercice
(sans facture de l'exercice : tout en HT). Le fichier le dit dans `avertissements`.

⚠️ LES « TIERS » : les sommes versées à des tiers pour des LOCATIONS (622) et des
HONORAIRES, COMMISSIONS ET COURTAGES (632), lecture du modèle à faire valider par le
fiscaliste. Un tiers y figure dès qu'une de ses factures de l'exercice touche ces comptes,
et il reste aussi dans les achats par fournisseur.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.contextes.comptabilite.domaine.balance_auxiliaire import JOURNAL_DES_A_NOUVEAUX
from app.contextes.comptabilite.domaine.entites import EcritureComptable, EtatEcriture, Sens

__all__ = ["LigneParTiers", "MontantHtTva", "declaration_par_tiers"]

_FOURNISSEURS = "40"
_TVA = "445"
_TRESORERIE = "5"
#: Les charges qui font d'un fournisseur un « tiers » au sens du modèle TIERS.
RACINES_DES_SOMMES_VERSEES = ("622", "632")
_ZERO = Decimal(0)


@dataclass(frozen=True)
class MontantHtTva:
    ht: Decimal = _ZERO
    tva: Decimal = _ZERO

    @property
    def ttc(self) -> Decimal:
        return self.ht + self.tva


@dataclass
class _Cumul:
    ouverture: Decimal = _ZERO
    facture_ht: Decimal = _ZERO
    facture_tva: Decimal = _ZERO
    paiements: Decimal = _ZERO
    sommes_versees: bool = False


@dataclass(frozen=True)
class LigneParTiers:
    #: Le code du tiers porté par les écritures (son NIU quand l'écriture vient d'une facture).
    tiers: str
    ouverture: MontantHtTva
    facturation: MontantHtTva
    paiement: MontantHtTva
    cloture: MontantHtTva
    #: Une de ses factures de l'exercice touche les locations ou les honoraires.
    sommes_versees: bool
    #: Ouverture, paiement et clôture répartis à la proportion des factures (voir en tête).
    repartition_estimee: bool = field(default=True)


def _arrondi(x: Decimal) -> Decimal:
    return x.quantize(Decimal(1), rounding=ROUND_HALF_UP)


def _repartir(ttc: Decimal, part_de_tva: Decimal) -> MontantHtTva:
    tva = _arrondi(ttc * part_de_tva)
    return MontantHtTva(ht=ttc - tva, tva=tva)


def declaration_par_tiers(ecritures: list[EcritureComptable]) -> list[LigneParTiers]:
    """Une ligne par fournisseur identifié, triée par code. Les lignes sans tiers ne se
    déclarent pas : la balance auxiliaire les montre, c'est là qu'elles se corrigent."""
    cumuls: dict[str, _Cumul] = {}
    for e in ecritures:
        if e.etat is not EtatEcriture.VALIDEE:
            continue
        les_40 = [lg for lg in e.lignes if lg.compte.startswith(_FOURNISSEURS) and lg.tiers]
        if not les_40:
            continue
        if e.journal == JOURNAL_DES_A_NOUVEAUX:
            for lg in les_40:
                c = cumuls.setdefault(lg.tiers, _Cumul())
                c.ouverture += lg.montant if lg.sens is Sens.CREDIT else -lg.montant
            continue
        tresorerie = any(lg.compte.startswith(_TRESORERIE) for lg in e.lignes)
        autres = [lg for lg in e.lignes if not lg.compte.startswith(_FOURNISSEURS)]
        tva = sum((_signe(lg) for lg in autres if lg.compte.startswith(_TVA)), _ZERO)
        ht = sum(
            (
                _signe(lg)
                for lg in autres
                if not lg.compte.startswith(_TVA) and not lg.compte.startswith(_TRESORERIE)
            ),
            _ZERO,
        )
        versees = any(lg.compte.startswith(RACINES_DES_SOMMES_VERSEES) for lg in autres)
        total_40 = sum((lg.montant for lg in les_40), _ZERO)
        for lg in les_40:
            c = cumuls.setdefault(lg.tiers, _Cumul())
            if lg.sens is Sens.DEBIT and tresorerie:
                c.paiements += lg.montant
                continue
            # Facture (crédit) ou avoir (débit sans trésorerie) : la part de ce tiers dans
            # l'écriture, si elle en concerne plusieurs.
            part = lg.montant / total_40 if total_40 else Decimal(1)
            c.facture_ht += _arrondi(ht * part)
            c.facture_tva += _arrondi(tva * part)
            c.sommes_versees = c.sommes_versees or versees

    lignes = []
    for tiers, c in sorted(cumuls.items()):
        facturation = MontantHtTva(ht=c.facture_ht, tva=c.facture_tva)
        part_de_tva = facturation.tva / facturation.ttc if facturation.ttc else _ZERO
        ouverture = _repartir(c.ouverture, part_de_tva)
        paiement = _repartir(c.paiements, part_de_tva)
        cloture = MontantHtTva(
            ht=ouverture.ht + facturation.ht - paiement.ht,
            tva=ouverture.tva + facturation.tva - paiement.tva,
        )
        lignes.append(
            LigneParTiers(
                tiers=tiers,
                ouverture=ouverture,
                facturation=facturation,
                paiement=paiement,
                cloture=cloture,
                sommes_versees=c.sommes_versees,
            )
        )
    return lignes


def _signe(ligne) -> Decimal:
    """Côté charge d'une facture : un débit compte en plus, un crédit (avoir) en moins."""
    return ligne.montant if ligne.sens is Sens.DEBIT else -ligne.montant
