"""Le registre des immobilisations et leur amortissement (17 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
LE MANQUE

Les amortissements se saisissaient à la main, en écritures sur les comptes 28 et 681 : rien
ne gardait la fiche du bien (sa valeur, sa mise en service, sa durée), rien ne calculait la
dotation de l'exercice, et le tableau des amortissements, annexe de la DSF, se versait en
pièce au lieu de se produire.

CE QUE CE MODULE FAIT

* une fiche par immobilisation : catégorie (le compte de classe 2 du plan), valeur d'origine
  hors taxe récupérable, date de mise en service, durée d'utilisation, sortie éventuelle ;
* l'amortissement **linéaire, prorata temporis**, du jour de la mise en service au jour de
  la sortie, en jours exacts sur une base de 365 jours par année d'utilisation ;
* la dotation d'un exercice, et le tableau de l'exercice (valeurs brutes, amortissements,
  valeurs nettes), par catégorie.

⚠️ LE CUMUL, ET NON L'ANNUITÉ, EST LA GRANDEUR CALCULÉE. La dotation d'un exercice est la
différence de deux cumuls : à la clôture, et à la veille de l'ouverture. Sommer des annuités
arrondies chacune à l'unité finirait à quelques francs de la valeur d'origine ; le cumul,
plafonné à la valeur d'origine, y arrive exactement, et la dernière dotation solde le bien.

⚠️ La durée est saisie bien par bien, et elle n'a **pas** de valeur par défaut dans le code :
les taux admis en déduction relèvent du Code général des impôts, et ils se valident avec le
fiscaliste du cabinet. Les terrains (catégorie 22) ne s'amortissent pas.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.partage.copie import transiter

__all__ = [
    "CategorieImmobilisation",
    "Immobilisation",
    "ImmobilisationRefusee",
    "LigneDuTableau",
    "TableauDesAmortissements",
    "cumul_au",
    "dotation_de_l_exercice",
    "tableau_des_amortissements",
]

_JOURS_PAR_AN = 365


class ImmobilisationRefusee(ValueError):
    """Un geste que la fiche interdit. Rendu en 409."""


class CategorieImmobilisation(StrEnum):
    """Le compte de classe 2 du plan du cabinet, et son libellé."""

    INCORPORELLE = "21"
    TERRAIN = "22"
    BATIMENT = "23"
    MATERIEL = "24"

    @property
    def libelle(self) -> str:
        return {
            "21": "Immobilisations incorporelles",
            "22": "Terrains",
            "23": "Bâtiments, installations techniques et agencements",
            "24": "Matériel, mobilier et actifs biologiques",
        }[self.value]

    @property
    def amortissable(self) -> bool:
        return self is not CategorieImmobilisation.TERRAIN


#: Les comptes de l'écriture de dotation, au plan du cabinet.
COMPTE_DOTATION = "681"
COMPTE_AMORTISSEMENT = "28"


class Immobilisation(BaseModel):
    model_config = ConfigDict(frozen=True)

    dossier: str = Field(min_length=1)
    code: str = Field(pattern=r"^[A-Z0-9][A-Z0-9-]{0,23}$")
    libelle: str = Field(min_length=2, max_length=160)
    categorie: CategorieImmobilisation
    #: Hors taxe récupérable, en francs CFA entiers.
    valeur_origine: Decimal = Field(gt=0, max_digits=15, decimal_places=0)
    mise_en_service: date
    #: Années d'utilisation. Absente pour un terrain.
    duree_annees: int | None = Field(default=None, ge=1, le=50)
    fournisseur: str | None = Field(default=None, max_length=160)
    piece: str | None = Field(default=None, max_length=64)
    sortie_le: date | None = None
    motif_sortie: str | None = Field(default=None, max_length=300)
    #: Les exercices dont la dotation a été passée en écriture : la base ne bouge plus.
    dotations_passees: tuple[str, ...] = ()
    enregistree_par: str
    enregistree_le: datetime

    @model_validator(mode="after")
    def _coherente(self) -> Immobilisation:
        if self.categorie.amortissable and self.duree_annees is None:
            raise ValueError("une immobilisation amortissable a une durée d'utilisation.")
        if not self.categorie.amortissable and self.duree_annees is not None:
            raise ValueError("un terrain ne s'amortit pas : il n'a pas de durée.")
        if self.sortie_le is not None:
            if self.sortie_le < self.mise_en_service:
                raise ValueError("la sortie ne précède pas la mise en service.")
            if not (self.motif_sortie or "").strip():
                raise ValueError("une sortie dit son motif (cession, mise au rebut, erreur).")
        return self

    @property
    def amortie_en_ecriture(self) -> bool:
        return bool(self.dotations_passees)

    def modifier(self, **champs) -> Immobilisation:
        """La base d'amortissement est figée dès qu'une dotation est passée en écriture :
        la changer rendrait faux un exercice déjà comptabilisé. Une erreur se corrige alors
        en sortant le bien (motif « erreur ») et en saisissant la bonne fiche."""
        if self.amortie_en_ecriture:
            figes = ("categorie", "valeur_origine", "mise_en_service", "duree_annees")
            changes = [c for c in figes if c in champs and champs[c] != getattr(self, c)]
            if changes:
                raise ImmobilisationRefusee(
                    f"{self.code} est déjà amorti en écriture "
                    f"({', '.join(self.dotations_passees)}) : sa base ne change plus "
                    f"({', '.join(changes)}). Sortez-le avec le motif "
                    "« erreur » et saisissez la bonne fiche."
                )
        return Immobilisation.model_validate(self.model_dump() | champs)

    def marquer_dotee(self, exercice: str) -> Immobilisation:
        if exercice in self.dotations_passees:
            return self
        return transiter(self, dotations_passees=tuple(sorted({*self.dotations_passees, exercice})))


def _franc(montant: Decimal) -> Decimal:
    return montant.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def cumul_au(immobilisation: Immobilisation, jour: date) -> Decimal:
    """Les amortissements cumulés à la fin de `jour`, plafonnés à la valeur d'origine.

    Le bien s'amortit du jour de sa mise en service, compris, au jour de sa sortie, compris.
    """
    if not immobilisation.categorie.amortissable or jour < immobilisation.mise_en_service:
        return Decimal(0)
    fin = min(jour, immobilisation.sortie_le) if immobilisation.sortie_le else jour
    jours = (fin - immobilisation.mise_en_service).days + 1
    assert immobilisation.duree_annees is not None
    base = immobilisation.valeur_origine
    cumul = _franc(base * jours / (immobilisation.duree_annees * _JOURS_PAR_AN))
    return min(cumul, base)


def dotation_de_l_exercice(
    immobilisation: Immobilisation, ouverture: date, cloture: date
) -> Decimal:
    return cumul_au(immobilisation, cloture) - cumul_au(
        immobilisation, ouverture - timedelta(days=1)
    )


class LigneDuTableau(BaseModel):
    """Une catégorie au tableau de l'exercice, ou le total."""

    compte: str
    libelle: str
    brut_ouverture: Decimal
    acquisitions: Decimal
    sorties: Decimal
    brut_cloture: Decimal
    amortissements_ouverture: Decimal
    dotations: Decimal
    amortissements_sortis: Decimal
    amortissements_cloture: Decimal
    valeur_nette_cloture: Decimal


class TableauDesAmortissements(BaseModel):
    dossier: str
    exercice: str
    ouverture: date
    cloture: date
    lignes: list[LigneDuTableau]
    total: LigneDuTableau
    #: Le détail bien par bien, pour la fiche de chaque ligne.
    biens: list[dict]


def _ligne(compte: str, libelle: str, biens: list[Immobilisation], ouverture, cloture):
    veille = ouverture - timedelta(days=1)

    def present_au(i: Immobilisation, jour: date) -> bool:
        return i.mise_en_service <= jour and (i.sortie_le is None or i.sortie_le > jour)

    brut_ouv = sum((i.valeur_origine for i in biens if present_au(i, veille)), Decimal(0))
    acq = sum(
        (i.valeur_origine for i in biens if ouverture <= i.mise_en_service <= cloture),
        Decimal(0),
    )
    sortis = [i for i in biens if i.sortie_le and ouverture <= i.sortie_le <= cloture]
    sor = sum((i.valeur_origine for i in sortis), Decimal(0))
    amort_ouv = sum((cumul_au(i, veille) for i in biens if present_au(i, veille)), Decimal(0))
    dot = sum((dotation_de_l_exercice(i, ouverture, cloture) for i in biens), Decimal(0))
    amort_sortis = sum((cumul_au(i, cloture) for i in sortis), Decimal(0))
    brut_clo = brut_ouv + acq - sor
    amort_clo = amort_ouv + dot - amort_sortis
    return LigneDuTableau(
        compte=compte,
        libelle=libelle,
        brut_ouverture=brut_ouv,
        acquisitions=acq,
        sorties=sor,
        brut_cloture=brut_clo,
        amortissements_ouverture=amort_ouv,
        dotations=dot,
        amortissements_sortis=amort_sortis,
        amortissements_cloture=amort_clo,
        valeur_nette_cloture=brut_clo - amort_clo,
    )


def tableau_des_amortissements(
    dossier: str,
    exercice: str,
    ouverture: date,
    cloture: date,
    immobilisations: list[Immobilisation],
) -> TableauDesAmortissements:
    """Le tableau de l'exercice, catégorie par catégorie, et son total.

    Un bien sorti avant l'ouverture ou mis en service après la clôture n'y figure pas."""
    retenus = [
        i
        for i in immobilisations
        if i.mise_en_service <= cloture and (i.sortie_le is None or i.sortie_le >= ouverture)
    ]
    lignes = [
        _ligne(c.value, c.libelle, [i for i in retenus if i.categorie is c], ouverture, cloture)
        for c in CategorieImmobilisation
        if any(i.categorie is c for i in retenus)
    ]
    total = _ligne("2", "Total des immobilisations", retenus, ouverture, cloture)
    biens = [
        {
            "code": i.code,
            "libelle": i.libelle,
            "compte": i.categorie.value,
            "mise_en_service": i.mise_en_service.isoformat(),
            "duree_annees": i.duree_annees,
            "valeur_origine": str(i.valeur_origine),
            "dotation": str(dotation_de_l_exercice(i, ouverture, cloture)),
            "cumul_cloture": str(cumul_au(i, cloture)),
            "valeur_nette_cloture": str(
                Decimal(0)
                if i.sortie_le and i.sortie_le <= cloture
                else i.valeur_origine - cumul_au(i, cloture)
            ),
            "sortie_le": i.sortie_le.isoformat() if i.sortie_le else None,
        }
        for i in sorted(retenus, key=lambda i: (i.categorie.value, i.code))
    ]
    return TableauDesAmortissements(
        dossier=dossier,
        exercice=exercice,
        ouverture=ouverture,
        cloture=cloture,
        lignes=lignes,
        total=total,
        biens=biens,
    )
