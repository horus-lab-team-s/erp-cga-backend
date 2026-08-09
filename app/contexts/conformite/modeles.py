"""Modèles du contexte Conformité : règles, factures contrôlées, constats, rapports."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..referentiel.api import Fondement, ParametreResolu, StatutValidation


class Severite(StrEnum):
    """Système de gravité du § 9 du dossier de design.

    Règle absolue d'affichage : la couleur ne porte jamais seule l'information. Glyphe et
    libellé sont toujours présents, y compris dans les tableaux les plus denses.
    """

    BLOQUANT = "BLOQUANT"
    MAJEUR = "MAJEUR"
    AVERTISSEMENT = "AVERTISSEMENT"
    INFORMATION = "INFORMATION"


#: Ordre de gravité décroissante. Le verdict d'un rapport est la plus grave rencontrée.
ORDRE_SEVERITE: dict[Severite, int] = {
    Severite.BLOQUANT: 4,
    Severite.MAJEUR: 3,
    Severite.AVERTISSEMENT: 2,
    Severite.INFORMATION: 1,
}

GLYPHE: dict[Severite, str] = {
    Severite.BLOQUANT: "⬣",
    Severite.MAJEUR: "▲",
    Severite.AVERTISSEMENT: "△",
    Severite.INFORMATION: "ⓘ",
}


class TypeDocument(StrEnum):
    FACTURE_ACHAT = "FACTURE_ACHAT"
    FACTURE_VENTE = "FACTURE_VENTE"
    AVOIR = "AVOIR"
    PROFORMA = "PROFORMA"


class RegimeEmetteur(StrEnum):
    REEL = "REEL"
    IGS = "IGS"
    INCONNU = "INCONNU"


class ModeReglement(StrEnum):
    ESPECES = "ESPECES"
    VIREMENT = "VIREMENT"
    CHEQUE = "CHEQUE"
    ORANGE_MONEY = "ORANGE_MONEY"
    MTN_MOMO = "MTN_MOMO"
    COMPENSATION = "COMPENSATION"
    INCONNU = "INCONNU"


# ── La règle ─────────────────────────────────────────────────────────────────────


class Portee(BaseModel):
    model_config = ConfigDict(frozen=True)

    type_document: list[TypeDocument] = Field(default_factory=lambda: list(TypeDocument))
    regimes_emetteur: list[RegimeEmetteur] | None = None
    exclusions: list[str] = Field(default_factory=list)


class ConsequenceFiscale(BaseModel):
    """Déclarative : la règle *décrit* la conséquence, elle ne l'applique pas.

    Un service en aval l'applique. C'est ce qui rend le moteur testable sans base et sans
    effet de bord.
    """

    model_config = ConfigDict(frozen=True)

    tva_deductible: bool | None = None
    charge_deductible: bool | None = None
    poste_reintegration: str | None = None
    rectification_requise: bool = False
    verification_requise: bool = False

    @property
    def rejette_tva(self) -> bool:
        return self.tva_deductible is False

    @property
    def rejette_charge(self) -> bool:
        return self.charge_deductible is False


class Regle(BaseModel):
    """Une règle de conformité, versionnée dans le temps.

    `version` et `applicable_du` sont deux choses distinctes : une règle rédigée en 2026
    peut porter sur du droit en vigueur depuis 2019.
    """

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1)
    libelle: str = Field(min_length=1)
    categorie: str
    version: str
    applicable_du: date
    applicable_au: date | None = None
    severite: Severite
    statut: StatutValidation = StatutValidation.A_VALIDER
    fondement: Fondement
    portee: Portee = Field(default_factory=Portee)
    #: JSONLogic. **VRAI = conforme. FAUX = constat émis.**
    predicat: dict[str, Any]
    consequence: ConsequenceFiscale = Field(default_factory=ConsequenceFiscale)
    message: str = Field(min_length=1)
    remediation: str = Field(min_length=1)

    @model_validator(mode="after")
    def _coherence(self) -> Regle:
        if self.applicable_au is not None and self.applicable_au <= self.applicable_du:
            raise ValueError(f"{self.code} : borne de validité incohérente")
        if not self.predicat:
            raise ValueError(f"{self.code} : prédicat vide")
        return self

    def en_vigueur(self, a_la_date: date) -> bool:
        if a_la_date < self.applicable_du:
            return False
        return self.applicable_au is None or a_la_date < self.applicable_au

    def concerne(self, facture: FactureAControler) -> bool:
        if facture.document.type not in self.portee.type_document:
            return False
        regimes = self.portee.regimes_emetteur
        if regimes and facture.emetteur.regime not in regimes:
            return False
        if "FOURNISSEUR_ETRANGER" in self.portee.exclusions and facture.emetteur.etranger:
            return False
        return True


# ── La facture soumise au contrôle ───────────────────────────────────────────────


class Document(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: TypeDocument = TypeDocument.FACTURE_ACHAT
    reference: str
    date_emission: date
    devise: str = "XAF"


class Partie(BaseModel):
    model_config = ConfigDict(frozen=True)

    denomination: str | None = None
    niu: str | None = None
    #: None = vérification DGI indisponible. Distinct de False, qui vaut « radié ».
    niu_actif: bool | None = None
    rccm: str | None = None
    regime: RegimeEmetteur = RegimeEmetteur.INCONNU
    etranger: bool = False


class Montants(BaseModel):
    model_config = ConfigDict(frozen=True)

    total_ht: Decimal
    total_tva: Decimal
    total_ttc: Decimal
    #: Somme des lignes, recalculée par le moteur si les lignes sont fournies.
    somme_lignes_ht: Decimal | None = None


class Reglement(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: ModeReglement = ModeReglement.INCONNU
    date_reglement: date | None = None


class LigneFacture(BaseModel):
    model_config = ConfigDict(frozen=True)

    designation: str
    quantite: Decimal | None = None
    prix_unitaire_ht: Decimal | None = None
    montant_ht: Decimal
    taux_tva: Decimal | None = None


class ContexteControle(BaseModel):
    """Données que la facture ne porte pas mais que le contrôle exige.

    Elles viennent du contexte Collecte (doublons) ou du Portefeuille (adhésion).
    """

    model_config = ConfigDict(frozen=True)

    doublons_potentiels: int = 0
    exercice_clos: bool = False


class FactureAControler(BaseModel):
    model_config = ConfigDict(frozen=True)

    document: Document
    emetteur: Partie
    destinataire: Partie = Field(default_factory=Partie)
    montants: Montants
    reglement: Reglement = Field(default_factory=Reglement)
    lignes: list[LigneFacture] = Field(default_factory=list)
    contexte: ContexteControle = Field(default_factory=ContexteControle)

    def donnees_predicat(self) -> dict[str, Any]:
        """Projection consommée par les prédicats JSONLogic.

        `somme_lignes_ht` est recalculée ici plutôt que crue sur parole : c'est
        précisément ce que la règle FAC-CAL-002 contrôle.
        """
        brut = self.model_dump(mode="python")
        if self.lignes:
            brut["montants"]["somme_lignes_ht"] = sum(
                (ligne.montant_ht for ligne in self.lignes), Decimal(0)
            )
        elif brut["montants"]["somme_lignes_ht"] is None:
            # Sans détail de lignes, le contrôle arithmétique n'a pas de prise :
            # on aligne sur le total pour ne pas produire un faux positif.
            brut["montants"]["somme_lignes_ht"] = self.montants.total_ht
        return brut


# ── Le résultat du contrôle ──────────────────────────────────────────────────────


class Constat(BaseModel):
    model_config = ConfigDict(frozen=True)

    code_regle: str
    libelle: str
    severite: Severite
    categorie: str
    fondement: Fondement
    message: str
    remediation: str
    consequence: ConsequenceFiscale
    #: Enjeu chiffré, ou None quand le constat reste qualitatif.
    enjeu: Decimal | None = None
    regle_a_valider: bool = False

    @property
    def glyphe(self) -> str:
        return GLYPHE[self.severite]


class RegleEnEchec(BaseModel):
    """Une règle qui n'a pas pu être évaluée.

    Signalée explicitement plutôt qu'ignorée : un contrôle silencieusement absent est
    plus dangereux qu'un contrôle en erreur.
    """

    model_config = ConfigDict(frozen=True)

    code_regle: str
    motif: str


class RapportConformite(BaseModel):
    """Immuable. Réévaluer une facture produit un nouveau rapport, jamais une mise à jour
    de l'ancien : c'est ce qui rend la décision d'un comptable défendable six mois plus
    tard."""

    model_config = ConfigDict(frozen=True)

    reference_document: str
    date_operation: date
    constats: list[Constat] = Field(default_factory=list)
    regles_appliquees: int = 0
    regles_en_echec: list[RegleEnEchec] = Field(default_factory=list)
    #: Paramètres du référentiel employés, avec leur valeur et leur date d'effet.
    parametres_employes: list[ParametreResolu] = Field(default_factory=list)

    @property
    def conforme(self) -> bool:
        return not self.constats

    @property
    def severite_maximale(self) -> Severite | None:
        if not self.constats:
            return None
        return max((c.severite for c in self.constats), key=lambda s: ORDRE_SEVERITE[s])

    @property
    def comptabilisation_interdite(self) -> bool:
        return any(c.severite is Severite.BLOQUANT for c in self.constats)

    @property
    def tva_deductible(self) -> bool:
        return not any(c.consequence.rejette_tva for c in self.constats)

    @property
    def charge_deductible(self) -> bool:
        return not any(c.consequence.rejette_charge for c in self.constats)

    @property
    def enjeu_total(self) -> Decimal:
        """Somme des enjeux, sans double compte : une même facture ne peut pas voir sa
        TVA rejetée deux fois par deux règles différentes."""
        return max((c.enjeu for c in self.constats if c.enjeu is not None), default=Decimal(0))

    @property
    def repose_sur_des_valeurs_non_validees(self) -> bool:
        """Vrai tant qu'un paramètre ou une règle employés n'a pas été confirmé sur le
        texte officiel. Le rapport n'est alors pas opposable."""
        parametres_douteux = any(
            p.statut is not StatutValidation.VALIDE for p in self.parametres_employes
        )
        return parametres_douteux or any(c.regle_a_valider for c in self.constats)
