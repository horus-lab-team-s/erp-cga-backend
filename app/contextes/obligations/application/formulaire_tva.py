"""Les lignes de la déclaration de TVA, chacune avec les écritures qui la composent (pas 109).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LA MAQUETTE DEMANDE

« Parcours comptable », vue D : une déclaration présentée **ligne par ligne** (ventes taxables et
leur base, ventes exonérées, TVA collectée, TVA déductible sur biens et sur services, TVA rejetée
par le contrôle, crédit reporté), et une note : « le comptable ne saisit aucun montant : la
déclaration est constituée à partir des journaux, et chaque ligne renvoie aux écritures qui la
composent ».

Le décompte existait (`declaration_tva.py`) : il disait combien, pas **d'où**.

⚠️ LE CODE D'UNE LIGNE N'EST PAS DANS LE CODE

La plateforme sait calculer des **grandeurs** (ventes taxables, TVA déductible sur services…).
Le **code** et le **libellé** sous lesquels chacune figure sur le formulaire de la DGI sont une
convention administrative, qui change avec le formulaire. Ils sont au référentiel
(`Docs/referentiel/obligations/formulaire_tva.yaml`) ; les codes livrés reprennent ceux de la
maquette, **à confirmer avec le formulaire en vigueur**. Une grandeur absente du fichier n'est
pas affichée ; un code inconnu du calcul est refusé au chargement.

COMMENT UNE ÉCRITURE EST RANGÉE

* Une écriture **de vente** mouvemente un compte de ventes (70) au crédit. Si elle porte aussi de
  la TVA collectée (443), sa base va aux ventes **taxables** ; sinon, aux ventes **exonérées**.
* Une ligne de TVA **déductible** (445, au débit) va à sa catégorie selon son compte : biens
  (4451), immobilisations (4452), services (4454), autres. La **base** d'une catégorie est la somme
  des charges et immobilisations (classes 6 et 2), nette de leurs crédits, des écritures dont
  **toute** la TVA
  déductible est de cette catégorie. Une écriture qui mêle deux catégories ne peut pas être
  répartie sans inventer une clé : sa base n'est comptée nulle part, et la ligne le dit
  (`base_partielle`).
* Seules les écritures **validées** de la période comptent, comme pour le décompte.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contextes.comptabilite.api import EcritureComptable, EtatEcriture, Sens

__all__ = [
    "FormulaireTVA",
    "Grandeur",
    "LigneDeDeclaration",
    "LigneDuFormulaire",
    "lignes_de_la_declaration",
]


class Grandeur(StrEnum):
    VENTES_TAXABLES = "VENTES_TAXABLES"
    VENTES_EXONEREES = "VENTES_EXONEREES"
    TVA_COLLECTEE = "TVA_COLLECTEE"
    DEDUCTIBLE_BIENS = "DEDUCTIBLE_BIENS"
    DEDUCTIBLE_IMMOBILISATIONS = "DEDUCTIBLE_IMMOBILISATIONS"
    DEDUCTIBLE_SERVICES = "DEDUCTIBLE_SERVICES"
    DEDUCTIBLE_AUTRES = "DEDUCTIBLE_AUTRES"
    TVA_REJETEE = "TVA_REJETEE"
    CREDIT_REPORTE = "CREDIT_REPORTE"


class LigneDuFormulaire(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1, max_length=12)
    grandeur: Grandeur
    libelle: str = Field(min_length=3, max_length=120)


class FormulaireTVA(BaseModel):
    """`Docs/referentiel/obligations/formulaire_tva.yaml`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lignes: tuple[LigneDuFormulaire, ...]
    #: Les racines des comptes, par grandeur (SYSCOHADA).
    comptes_de_ventes: tuple[str, ...] = ("70",)
    comptes_de_base_deductible: tuple[str, ...] = ("6", "2")
    tva_collectee: tuple[str, ...] = ("443",)
    tva_deductible: tuple[str, ...] = ("445",)
    deductible_biens: tuple[str, ...] = ("4451",)
    deductible_immobilisations: tuple[str, ...] = ("4452",)
    deductible_services: tuple[str, ...] = ("4454",)
    source: str = "valeurs par défaut"

    @model_validator(mode="after")
    def _une_ligne_par_grandeur_et_par_code(self) -> FormulaireTVA:
        codes = [l_.code for l_ in self.lignes]
        grandeurs = [l_.grandeur for l_ in self.lignes]
        if len(set(codes)) != len(codes) or len(set(grandeurs)) != len(grandeurs):
            raise ValueError(
                "un code et une grandeur n'apparaissent qu'une fois : deux lignes pour la même "
                "grandeur la feraient compter deux fois sur le formulaire."
            )
        return self


def formulaire_par_defaut() -> FormulaireTVA:
    """Les lignes de la maquette. ⚠️ À confirmer avec le formulaire de la DGI en vigueur."""
    return FormulaireTVA(
        lignes=(
            LigneDuFormulaire(
                code="L01",
                grandeur=Grandeur.VENTES_TAXABLES,
                libelle="Ventes taxables au taux général",
            ),
            LigneDuFormulaire(
                code="L04", grandeur=Grandeur.VENTES_EXONEREES, libelle="Ventes exonérées"
            ),
            LigneDuFormulaire(code="L10", grandeur=Grandeur.TVA_COLLECTEE, libelle="TVA collectée"),
            LigneDuFormulaire(
                code="L20",
                grandeur=Grandeur.DEDUCTIBLE_BIENS,
                libelle="TVA déductible sur achats de biens",
            ),
            LigneDuFormulaire(
                code="L21",
                grandeur=Grandeur.DEDUCTIBLE_SERVICES,
                libelle="TVA déductible sur services",
            ),
            LigneDuFormulaire(
                code="L22",
                grandeur=Grandeur.DEDUCTIBLE_IMMOBILISATIONS,
                libelle="TVA déductible sur immobilisations",
            ),
            LigneDuFormulaire(
                code="L23",
                grandeur=Grandeur.DEDUCTIBLE_AUTRES,
                libelle="TVA déductible, autres comptes",
            ),
            LigneDuFormulaire(
                code="L24",
                grandeur=Grandeur.TVA_REJETEE,
                libelle="TVA rejetée par le contrôle de conformité",
            ),
            LigneDuFormulaire(
                code="L30",
                grandeur=Grandeur.CREDIT_REPORTE,
                libelle="Crédit de TVA reporté du mois précédent",
            ),
        ),
    )


class LigneDeDeclaration(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    grandeur: Grandeur
    libelle: str
    #: La base hors taxe, quand la grandeur en a une.
    base: Decimal | None
    #: Le montant de TVA ; `None` pour une base sans taxe (ventes exonérées). Une TVA rejetée ou
    #: un crédit reporté sont des montants **positifs** qui se retranchent : le signe est dans
    #: la grandeur, pas dans le nombre (même règle que les lignes d'écriture).
    montant: Decimal | None
    #: Les écritures qui composent la ligne, dans l'ordre du journal.
    ecritures: list[str]
    #: La base omet des écritures mêlant plusieurs catégories de TVA déductible.
    base_partielle: bool = False


def _commence(compte: str, racines: tuple[str, ...]) -> bool:
    return compte.startswith(racines)


def lignes_de_la_declaration(
    ecritures: list[EcritureComptable],
    *,
    periode_debut,
    periode_fin,
    tva_rejetee: Decimal,
    ecritures_rejetees: list[str],
    credit_reporte: Decimal,
    formulaire: FormulaireTVA,
) -> list[LigneDeDeclaration]:
    """Les lignes, dans l'ordre du formulaire. Les montants de TVA sont ceux du décompte :
    ce module ne recalcule ni la collectée ni le rejet, il les ventile et les justifie."""
    base: dict[Grandeur, Decimal] = {g: Decimal(0) for g in Grandeur}
    montant: dict[Grandeur, Decimal] = {g: Decimal(0) for g in Grandeur}
    sources: dict[Grandeur, list[str]] = {g: [] for g in Grandeur}
    partielle: set[Grandeur] = set()

    def categorie(compte: str) -> Grandeur:
        if _commence(compte, formulaire.deductible_biens):
            return Grandeur.DEDUCTIBLE_BIENS
        if _commence(compte, formulaire.deductible_immobilisations):
            return Grandeur.DEDUCTIBLE_IMMOBILISATIONS
        if _commence(compte, formulaire.deductible_services):
            return Grandeur.DEDUCTIBLE_SERVICES
        return Grandeur.DEDUCTIBLE_AUTRES

    retenues = sorted(
        (
            e
            for e in ecritures
            if e.etat is EtatEcriture.VALIDEE and periode_debut <= e.date_operation <= periode_fin
        ),
        key=lambda e: (e.date_operation, e.journal, e.numero),
    )
    for e in retenues:
        ventes = sum(
            (
                l_.montant if l_.sens is Sens.CREDIT else -l_.montant
                for l_ in e.lignes
                if _commence(l_.compte, formulaire.comptes_de_ventes)
            ),
            Decimal(0),
        )
        # Nettes de leur sens contraire, comme le décompte (pas 110) : une vente annulée
        # retranche sa TVA collectée, une contre-passation d'achat sa TVA déductible.
        collectee = sum(
            (
                l_.montant if l_.sens is Sens.CREDIT else -l_.montant
                for l_ in e.lignes
                if _commence(l_.compte, formulaire.tva_collectee)
            ),
            Decimal(0),
        )
        if collectee:
            montant[Grandeur.TVA_COLLECTEE] += collectee
            sources[Grandeur.TVA_COLLECTEE].append(e.cle)
        if ventes:
            cible = Grandeur.VENTES_TAXABLES if collectee else Grandeur.VENTES_EXONEREES
            base[cible] += ventes
            sources[cible].append(e.cle)
            if collectee:
                montant[Grandeur.VENTES_TAXABLES] += collectee

        deductibles = [l_ for l_ in e.lignes if _commence(l_.compte, formulaire.tva_deductible)]
        categories = {categorie(l_.compte) for l_ in deductibles}
        for l_ in deductibles:
            montant[categorie(l_.compte)] += l_.montant if l_.sens is Sens.DEBIT else -l_.montant
        for c in categories:
            sources[c].append(e.cle)
        if len(categories) == 1:
            (seule,) = categories
            # ⚠️ Nette : un rabais obtenu au crédit (609) réduit la base hors taxe. Compter les
            # seuls débits la surestimait (vu par une mutation que rien ne tuait, pas 109).
            base[seule] += sum(
                (
                    l_.montant if l_.sens is Sens.DEBIT else -l_.montant
                    for l_ in e.lignes
                    if _commence(l_.compte, formulaire.comptes_de_base_deductible)
                ),
                Decimal(0),
            )
        elif len(categories) > 1:
            partielle |= categories

    montant[Grandeur.TVA_REJETEE] = tva_rejetee
    sources[Grandeur.TVA_REJETEE] = sorted(set(ecritures_rejetees))
    montant[Grandeur.CREDIT_REPORTE] = credit_reporte

    sans_base = {Grandeur.TVA_COLLECTEE, Grandeur.TVA_REJETEE, Grandeur.CREDIT_REPORTE}
    lignes = []
    for ligne in formulaire.lignes:
        g = ligne.grandeur
        lignes.append(
            LigneDeDeclaration(
                code=ligne.code,
                grandeur=g,
                libelle=ligne.libelle,
                base=None if g in sans_base else base[g],
                montant=None if g is Grandeur.VENTES_EXONEREES else montant[g],
                ecritures=sources[g],
                base_partielle=g in partielle,
            )
        )
    return lignes
