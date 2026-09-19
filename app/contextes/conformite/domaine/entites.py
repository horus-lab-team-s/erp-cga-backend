"""Modèles du contexte Conformité : règles, factures contrôlées, constats, rapports."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contextes.referentiel.contrats import Fondement, ParametreResolu, StatutValidation
from app.moteur.agregation import enjeu_maximal, niveau_le_plus_eleve
from app.moteur.consequence import Consequence, TypeConsequence
from app.moteur.jsonlogic import lire


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
    """Le filtre appliqué avant toute évaluation. Une règle hors portée n'est ni
    évaluée, ni comptée dans le total des règles appliquées.

    ⚠️ DEUX RÉGIMES, ET IL NE FAUT PAS LES CONFONDRE.

    `regimes_emetteur` porte sur le **fournisseur** : une règle qui exige une
    mention de TVA ne s'applique pas à un émetteur qui n'est pas assujetti.

    `regimes_destinataire` porte sur l'**adhérent** lui-même, et c'est lui qui
    commande la déductibilité. Un adhérent au régime synthétique ne récupère
    jamais la TVA : lui annoncer « TVA non déductible » énonce un préjudice qui
    n'existe pas. Une règle systématiquement écartée finit par être ignorée le
    jour où elle a raison — c'est le mécanisme par lequel un moteur de contrôle
    perd la confiance de son réviseur.
    """

    model_config = ConfigDict(frozen=True)

    type_document: list[TypeDocument] = Field(default_factory=lambda: list(TypeDocument))
    regimes_emetteur: list[RegimeEmetteur] | None = None
    regimes_destinataire: list[RegimeEmetteur] | None = None
    exclusions: list[str] = Field(default_factory=list)


class ConsequenceFiscale(Consequence):
    """Déclarative : la règle *décrit* la conséquence, elle ne l'applique pas.

    Un service en aval l'applique. C'est ce qui rend le moteur testable sans base et sans
    effet de bord.

    Elle spécialise `app.moteur.consequence.Consequence` pour le domaine fiscal. Son type
    est toujours MONTANT : ce qu'une facture non conforme coûte se compte en argent, et le
    chiffrage revient à `valoriser_fiscalement`, qui lit les faits du sujet.

    L'unité reste indéterminée à ce niveau : la monnaie est portée par le document, et
    l'inscrire ici en dur reviendrait à supposer qu'aucune facture n'est libellée
    autrement.
    """

    model_config = ConfigDict(frozen=True)

    type: TypeConsequence = TypeConsequence.MONTANT
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
    #: Qui engage sa responsabilité sur cette règle, et quand. Mêmes exigences que
    #: pour un paramètre du référentiel : une règle VALIDE sans signataire vaudrait
    #: moins qu'une règle A_VALIDER, parce qu'elle affirmerait sans engager
    #: personne. Une règle porte davantage qu'un paramètre — elle porte une
    #: *interprétation* du texte —, donc à plus forte raison.
    valide_par: str | None = None
    valide_le: date | None = None
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
        if self.statut is StatutValidation.VALIDE and not (self.valide_par and self.valide_le):
            raise ValueError(
                f"{self.code} : une règle VALIDE doit porter valide_par et valide_le. "
                "Une règle interprète un texte ; l'interprétation engage son auteur."
            )
        return self

    def en_vigueur(self, a_la_date: date) -> bool:
        if a_la_date < self.applicable_du:
            return False
        return self.applicable_au is None or a_la_date < self.applicable_au

    def concerne(self, faits: Mapping[str, Any]) -> bool:
        """La portée s'évalue sur les faits, comme les prédicats.

        Elle ne reçoit donc pas la facture : une portée qui lirait un champ que le schéma
        ne déclare pas écarterait des règles pour une raison invisible à leur auteur.
        """
        if lire(faits, "document.type") not in self.portee.type_document:
            return False
        regimes = self.portee.regimes_emetteur
        if regimes and lire(faits, "emetteur.regime") not in regimes:
            return False
        # Le régime de l'adhérent commande la déductibilité : une règle de TVA
        # n'a aucun objet pour un destinataire qui ne récupère jamais la taxe.
        regimes = self.portee.regimes_destinataire
        if regimes and lire(faits, "destinataire.regime") not in regimes:
            return False
        if "FOURNISSEUR_ETRANGER" in self.portee.exclusions and lire(faits, "emetteur.etranger"):
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

    def faits(self) -> dict[str, Any]:
        """Le sujet réduit à des faits, sous les chemins que déclare `SCHEMA_FACTURE`.

        Satisfait le protocole `app.moteur.faits.Sujet` : c'est par cette méthode, et
        par elle seule, que le noyau d'évaluation accède à une facture.

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


# ── Les agrégateurs du domaine ───────────────────────────────────────────────────
#
# Montés une fois : ce sont des fonctions sans état, et les reconstruire à chaque lecture
# de propriété serait du gaspillage sur un rapport qui porte des centaines de constats.

#: Sans addition. Voir `enjeu_total`.
_ENJEU_DE_CONFORMITE = enjeu_maximal(unite=None)

#: L'ordre va du moins grave au plus grave, contrairement à `ORDRE_SEVERITE` qui numérote
#: dans l'autre sens pour l'affichage.
_PLUS_HAUTE_SEVERITE = niveau_le_plus_eleve(
    [membre.value for membre in sorted(Severite, key=lambda s: ORDRE_SEVERITE[s])]
)


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


class ConstatEcarte(BaseModel):
    """Un constat que le moteur a produit et qu'un écart effectif neutralise (pas 92).

    ⚠️ Ni le motif ni l'auteur ne figurent ici. Le rapport est lu par l'adhérent
    aussi : il peut savoir qu'un constat a été écarté par le cabinet, il n'a pas à lire
    la note interne du réviseur. Le détail vit dans l'écart lui-même, que seul le
    cabinet consulte (voir `domaine/ecarts.py`).
    """

    model_config = ConfigDict(frozen=True)

    constat: Constat
    #: L'identifiant de la décision : c'est le fil qui remonte au motif.
    identifiant_ecart: str


class RapportConformite(BaseModel):
    """Immuable. Réévaluer une facture produit un nouveau rapport, jamais une mise à jour
    de l'ancien : c'est ce qui rend la décision d'un comptable défendable six mois plus
    tard."""

    model_config = ConfigDict(frozen=True)

    reference_document: str
    date_operation: date
    constats: list[Constat] = Field(default_factory=list)
    #: Pas 92 : les constats qu'un écart effectif a retirés de `constats`. Ils sortent
    #: de tout calcul (conformité, TVA, charge, enjeu, blocage) et restent lisibles.
    #: Toujours vide à la sortie du moteur : c'est `appliquer_les_ecarts` qui la remplit.
    constats_ecartes: list[ConstatEcarte] = Field(default_factory=list)
    regles_appliquees: int = 0
    regles_en_echec: list[RegleEnEchec] = Field(default_factory=list)
    #: Paramètres du référentiel employés, avec leur valeur et leur date d'effet.
    parametres_employes: list[ParametreResolu] = Field(default_factory=list)

    @property
    def conforme(self) -> bool:
        return not self.constats

    @property
    def severite_maximale(self) -> Severite | None:
        nominal = _PLUS_HAUTE_SEVERITE(self.constats).nominal
        return Severite(nominal) if nominal else None

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
        """L'enjeu le plus élevé, **sans addition**.

        Une même facture ne peut pas voir sa taxe rejetée deux fois par deux règles
        différentes. La règle d'agrégation vit dans `app.moteur.agregation` avec les
        trois autres, parce que c'est là qu'on voit qu'elle diffère de celles-ci : la
        charge additionne, la conformité prend le maximum, et aucune règle générale ne
        départage les deux.
        """
        valeur = _ENJEU_DE_CONFORMITE(self.constats).valeur
        return valeur if valeur is not None else Decimal(0)

    @property
    def repose_sur_des_valeurs_non_validees(self) -> bool:
        """Vrai tant qu'un paramètre ou une règle employés n'a pas été confirmé sur le
        texte officiel. Le rapport n'est alors pas opposable."""
        parametres_douteux = any(
            p.statut is not StatutValidation.VALIDE for p in self.parametres_employes
        )
        return parametres_douteux or any(c.regle_a_valider for c in self.constats)
