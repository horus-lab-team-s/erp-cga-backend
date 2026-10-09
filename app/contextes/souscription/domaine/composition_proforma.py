"""La composition d'une proforma : rubriques, lignes, sous-totaux.

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE CABINET ENVOIE RÉELLEMENT

Deux documents, et ils se répondent :

    avec suivi   I. CFCE 177 000 · II. Impôts 53 000 · III. CNPS 25 000
                 IV. Suivi 12 mois 150 000 · V. DSF 100 000      → 505 000
    sans suivi   I, II, III identiques · IV. Frais de procédure 45 000 → 300 000

⚠️ **LA DIFFÉRENCE N'EST PAS ADDITIVE.** Prendre le suivi ajoute deux rubriques
ET fait disparaître les frais de procédure. C'est une décision commerciale du
cabinet, pas une règle qu'on peut déduire : elle vit donc au référentiel, sous
deux compositions nommées qui se vérifient d'un coup d'œil contre les deux PDF.

⚠️ **LES SOUS-TOTAUX NE SONT PAS AU RÉFÉRENTIEL**, et c'est délibéré. Les y
recopier créerait une seconde vérité à côté des lignes, et le jour où elles
divergeraient personne ne saurait laquelle croire. Ils se calculent ici, et le
total général est leur somme.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CompositionIntrouvable",
    "CompositionProforma",
    "LigneDeRubrique",
    "Rubrique",
    "StatutComposition",
]


class CompositionIntrouvable(LookupError):
    """Aucune composition ne porte ce code à cette date."""


class StatutComposition:
    """Ce que vaut une composition, et qui l'a décidée.

    ⚠️ `TRANSCRIT` n'est pas `VALIDE`. Il dit exactement ce qu'il est : recopié
    d'un document que le cabinet envoie déjà, donc bien plus sûr qu'une valeur de
    travail, mais pas encore confirmé dans le système par la direction. Les
    confondre ferait passer pour arrêté ce que personne n'a relu ici.
    """

    TRANSCRIT = "TRANSCRIT"
    VALIDE = "VALIDE"


class LigneDeRubrique(BaseModel):
    model_config = ConfigDict(frozen=True)

    libelle: str = Field(min_length=1)
    #: ⚠️ Entier : le franc CFA n'a pas de subdivision en circulation, et un
    #: montant à décimales sur un document commercial vient d'un calcul à
    #: corriger.
    montant: Decimal = Field(gt=0)


class Rubrique(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: En chiffres romains, comme sur le document du cabinet.
    numero: str = Field(min_length=1)
    intitule: str = Field(min_length=1)
    lignes: tuple[LigneDeRubrique, ...] = Field(min_length=1)

    @property
    def sous_total(self) -> Decimal:
        return sum((ligne.montant for ligne in self.lignes), Decimal(0))


class CompositionProforma(BaseModel):
    """Une composition nommée, telle que le référentiel la porte."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1)
    service: str = Field(min_length=1)
    forme_juridique: str = Field(min_length=1)
    #: Le sous-titre du document : ce que la proforma couvre, dans les mots du
    #: cabinet.
    libelle: str = Field(min_length=1)
    titre: str = Field(min_length=1)
    statut: str = StatutComposition.TRANSCRIT
    #: ⚠️ D'où vient chaque montant. Sans elle, personne ne peut vérifier la
    #: transcription six mois plus tard, ni savoir laquelle des deux proformas
    #: du cabinet a servi de modèle.
    source: str = Field(min_length=1)
    applicable_du: date
    applicable_au: date | None = None
    rubriques: tuple[Rubrique, ...] = Field(min_length=1)

    @property
    def total(self) -> Decimal:
        """La somme des sous-totaux. **Jamais une valeur saisie.**"""
        return sum((r.sous_total for r in self.rubriques), Decimal(0))

    def couvre(self, a_la_date: date) -> bool:
        return self.applicable_du <= a_la_date and (
            self.applicable_au is None or a_la_date < self.applicable_au
        )

    @property
    def arretee(self) -> bool:
        """⚠️ Vrai seulement quand la direction a confirmé les montants ici.

        Une composition `TRANSCRIT` s'imprime et se lit ; ce qui la distingue,
        c'est qu'on sait encore qu'elle vient d'un PDF et non d'une décision
        prise dans le système.
        """
        return self.statut == StatutComposition.VALIDE
