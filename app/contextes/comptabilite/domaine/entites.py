"""Entités du contexte E · Comptabilité SYSCOHADA.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE CONTEXTE MODÉLISE

Le journal d'une entreprise : des écritures équilibrées, numérotées sans trou,
rattachées à leur pièce justificative, et immuables une fois validées. Rien de
plus, mais rien de moins — ces quatre propriétés sont ce qui distingue une
comptabilité d'un tableur.

CE QU'IL NE MODÉLISE PAS

Ni la balance, ni le grand livre : ce sont des **projections** calculées sur les
écritures, et elles vivent dans `projections.py`. Les persister créerait une
seconde vérité, qui divergerait de la première au premier incident.

Ni l'exercice, ni l'entreprise : ils appartiennent au contexte B · Portefeuille.
Une écriture ne porte qu'un identifiant d'exercice, opaque pour elle.

Ni la loi fiscale : aucun taux, aucun seuil, aucune règle de déductibilité n'est
écrit ici. Les attributs fiscaux d'une ligne sont **reçus** du contexte
D · Conformité, jamais calculés. Voir `application/consequences_fiscales.py`.

POURQUOI TOUT EST GELÉ (`frozen=True`)

Parce que l'immutabilité comptable n'est pas une option de conception : une
écriture validée ne se modifie plus, elle se contre-passe. Le geler au niveau du
modèle rend l'invariant impossible à contourner par distraction.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.partage.copie import transiter

__all__ = [
    "AttributFiscal",
    "Compte",
    "DestinationCompte",
    "EcritureComptable",
    "EtatEcriture",
    "Journal",
    "LigneEcriture",
    "NatureJournal",
    "Sens",
    "TypeEcriture",
]


# ── Vocabulaire ──────────────────────────────────────────────────────────────────


class Sens(StrEnum):
    """Le côté de l'écriture où la ligne est portée.

    Ce n'est ni « plus » ni « moins » : c'est une colonne. L'actif et les charges
    augmentent au débit, le passif et les produits au crédit — voir
    `Compte.sens_naturel`.
    """

    DEBIT = "DEBIT"
    CREDIT = "CREDIT"

    @property
    def inverse(self) -> Sens:
        """Le sens opposé. C'est tout ce dont la contre-passation a besoin."""
        return Sens.CREDIT if self is Sens.DEBIT else Sens.DEBIT


class NatureJournal(StrEnum):
    """Les cinq journaux du plan de classement usuel.

    On sépare les journaux pour deux raisons pratiques : plusieurs personnes
    saisissent en parallèle, et surtout le journal de banque doit se rapprocher
    ligne à ligne du relevé de l'établissement. Tout mélanger rendrait ce
    pointage impraticable.
    """

    ACHATS = "ACHATS"
    VENTES = "VENTES"
    BANQUE = "BANQUE"
    CAISSE = "CAISSE"
    OPERATIONS_DIVERSES = "OPERATIONS_DIVERSES"


class DestinationCompte(StrEnum):
    """Où le solde d'un compte atterrit à la clôture.

    C'est la ligne de fracture du plan comptable : les comptes de situation
    survivent à la clôture et se reportent, les comptes de gestion sont soldés et
    repartent de zéro.
    """

    BILAN = "BILAN"
    RESULTAT = "RESULTAT"
    HORS_BILAN = "HORS_BILAN"


class EtatEcriture(StrEnum):
    """Une écriture est en brouillon, ou validée. Il n'y a pas de troisième état.

    En particulier, il n'existe **pas** d'état « annulée » : une écriture validée
    ne s'annule pas, elle se contre-passe par une écriture nouvelle. L'originale
    demeure telle quelle, pour toujours.
    """

    BROUILLON = "BROUILLON"
    VALIDEE = "VALIDEE"


class TypeEcriture(StrEnum):
    NORMALE = "NORMALE"
    CONTREPASSATION = "CONTREPASSATION"


# ── Le plan de comptes ───────────────────────────────────────────────────────────


class Compte(BaseModel):
    """Un compte du plan, de référence ou dérivé.

    ⚠️ LE POINT LE PLUS IMPORTANT DE CE MODULE.

    Chaque adhérent peut créer ses propres sous-comptes — « 401100 Fournisseurs
    locaux », « 401200 Fournisseurs étrangers ». C'est légitime et utile pour son
    suivi. Mais le mapping vers les postes de la liasse fiscale se fait sur le
    **compte OHADA de référence**, jamais sur le sous-compte : sans cette
    contrainte, chaque sous-compte créé par un comptable ferait disparaître son
    solde de la DSF, silencieusement.

    D'où `compte_reference`, obligatoirement renseigné sur tout compte dérivé, et
    vérifié : le numéro dérivé doit commencer par celui de sa référence.
    """

    model_config = ConfigDict(frozen=True)

    numero: str = Field(pattern=r"^[1-9][0-9]{0,7}$")
    intitule: str = Field(min_length=1)

    #: Numéro du compte OHADA dont celui-ci dérive. `None` si le compte *est*
    #: lui-même un compte de référence du plan.
    compte_reference: str | None = Field(default=None, pattern=r"^[1-9][0-9]{0,7}$")

    #: Un compte lettrable est un compte de tiers : on y apparie les factures avec
    #: leurs règlements pour faire apparaître ce qui reste réellement ouvert.
    lettrable: bool = False

    #: Un compte rapprochable est un compte de trésorerie : son journal se pointe
    #: contre un relevé émis par un tiers indépendant. C'est le contrôle le plus
    #: efficace qui existe en comptabilité.
    rapprochable: bool = False

    @model_validator(mode="after")
    def _derivation_coherente(self) -> Compte:
        if self.compte_reference is None:
            return self
        if self.compte_reference == self.numero:
            raise ValueError(
                f"compte {self.numero} : un compte de référence ne dérive pas de lui-même. "
                "Laisser compte_reference à None."
            )
        if not self.numero.startswith(self.compte_reference):
            raise ValueError(
                f"compte {self.numero} : le numéro d'un compte dérivé doit commencer par "
                f"celui de sa référence, or la référence est {self.compte_reference}. "
                "Sans cette contrainte, le mapping vers la liasse perdrait ce solde."
            )
        return self

    @property
    def classe(self) -> int:
        """Le premier chiffre. Il suffit à savoir où le compte atterrira."""
        return int(self.numero[0])

    @property
    def destination(self) -> DestinationCompte:
        if self.classe <= 5:
            return DestinationCompte.BILAN
        if self.classe <= 8:
            return DestinationCompte.RESULTAT
        return DestinationCompte.HORS_BILAN

    @property
    def sens_naturel(self) -> Sens | None:
        """Le sens dans lequel le compte augmente, ou `None` s'il est bidirectionnel.

        Les classes 4 — tiers — et 8 — hors activités ordinaires — n'ont pas de
        sens naturel : un compte fournisseur est créditeur, un compte client
        débiteur, et les deux vivent en classe 4. Rendre `None` plutôt que de
        deviner évite un contrôle faux.
        """
        if self.classe in (2, 3, 5, 6):
            return Sens.DEBIT
        if self.classe in (1, 7):
            return Sens.CREDIT
        return None

    @property
    def racine(self) -> str:
        """Le numéro sur lequel se fait le mapping vers la liasse."""
        return self.compte_reference or self.numero


class Journal(BaseModel):
    """Un registre chronologique, pour une nature d'opération."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1, max_length=8, pattern=r"^[A-Z0-9]+$")
    intitule: str = Field(min_length=1)
    nature: NatureJournal

    #: Pour un journal de trésorerie, le compte qui se retrouve dans chaque
    #: écriture. Renseigné pour BANQUE et CAISSE, absent ailleurs.
    compte_contrepartie: str | None = Field(default=None, pattern=r"^[1-9][0-9]{0,7}$")

    @model_validator(mode="after")
    def _contrepartie_coherente(self) -> Journal:
        tresorerie = self.nature in (NatureJournal.BANQUE, NatureJournal.CAISSE)
        if tresorerie and self.compte_contrepartie is None:
            raise ValueError(
                f"journal {self.code} : un journal de trésorerie doit déclarer son compte "
                "de contrepartie, sans quoi le rapprochement est impossible."
            )
        if not tresorerie and self.compte_contrepartie is not None:
            raise ValueError(
                f"journal {self.code} : seul un journal de trésorerie porte un compte de "
                "contrepartie."
            )
        return self


# ── L'attribut fiscal : la soudure avec le contexte D ────────────────────────────


class AttributFiscal(BaseModel):
    """Ce qu'une ligne d'écriture retient d'un constat de conformité.

    ⚠️ CE MODULE NE CALCULE RIEN. Ces valeurs sont **reçues** du contexte
    D · Conformité, qui les a produites en évaluant une règle sur le référentiel
    daté. La comptabilité les porte, les transmet et les conserve ; elle ne les
    décide jamais.

    C'est la matérialisation du principe n° 3 du projet : la règle *décrit* la
    conséquence fiscale, un service en aval l'*applique*. Ici, on est en aval.

    C'est aussi le maillon 3 de la traçabilité exigée au § 04 du dossier
    d'architecture :

        PieceJustificative → RapportConformite → Constat
            → **LigneEcriture (attribut fiscal)** → LigneDeclaration → PosteLiasse

    Sans `code_regle_origine` et `reference_rapport`, la direction ne pourrait pas
    remonter d'une ligne de réintégration de la liasse jusqu'à la photo de la
    facture. C'est pourquoi ils ne sont pas facultatifs quand une déduction est
    refusée.
    """

    # ⚠️ Pas 110 : `extra="forbid"`. Un attribut construit avec `deductibilite_tva=False` (une
    # faute de nom) était accepté sans rien dire, et ne rejetait aucune TVA : la faute se serait
    # vue à la déclaration, jamais à la saisie.
    model_config = ConfigDict(frozen=True, extra="forbid")

    #: `False` = la TVA de cette ligne n'est pas récupérable. `None` = la question
    #: ne se pose pas pour cette ligne. Ne jamais confondre les deux.
    tva_deductible: bool | None = None
    charge_deductible: bool | None = None

    #: En clair, destiné à être lu par un humain — le comptable qui reprend le
    #: dossier, ou le contrôleur. Jamais un code seul.
    motif_non_deductibilite: str | None = None

    #: Ce qui remontera au tableau de passage à la clôture, et le poste où il
    #: atterrira. Renseigné par le contexte H le moment venu.
    montant_a_reintegrer: Decimal | None = None
    poste_reintegration: str | None = None

    #: Traçabilité vers l'origine du constat, dans le contexte D.
    code_regle_origine: str | None = None
    reference_rapport: str | None = None

    @property
    def rejette_tva(self) -> bool:
        return self.tva_deductible is False

    @property
    def rejette_charge(self) -> bool:
        return self.charge_deductible is False

    @property
    def est_neutre(self) -> bool:
        """Vrai si l'attribut ne refuse rien. Un attribut neutre n'a pas d'intérêt
        à être porté : autant laisser la ligne sans attribut."""
        return not self.rejette_tva and not self.rejette_charge

    @model_validator(mode="after")
    def _un_refus_se_motive(self) -> AttributFiscal:
        if (self.rejette_tva or self.rejette_charge) and not self.motif_non_deductibilite:
            raise ValueError(
                "un refus de déduction doit porter son motif en clair. Sans lui, "
                "personne ne pourra justifier le rejet devant l'adhérent ni devant "
                "l'administration."
            )
        if (self.rejette_tva or self.rejette_charge) and not self.code_regle_origine:
            raise ValueError(
                "un refus de déduction doit désigner la règle qui l'a produit. C'est ce "
                "qui permet de remonter de la liasse jusqu'à la facture."
            )
        if self.montant_a_reintegrer is not None and self.montant_a_reintegrer < 0:
            raise ValueError("un montant à réintégrer ne peut pas être négatif")
        return self


# ── L'écriture ───────────────────────────────────────────────────────────────────


class LigneEcriture(BaseModel):
    """Une ligne : un compte, un sens, un montant.

    Le montant est **toujours positif**. C'est le `sens` qui porte la direction,
    jamais le signe. Un modèle à montants signés paraît plus simple et se paie
    très cher : il autorise des lignes à zéro, des débits négatifs, et rend
    l'équilibre d'une écriture ambigu à vérifier.
    """

    model_config = ConfigDict(frozen=True)

    compte: str = Field(pattern=r"^[1-9][0-9]{0,7}$")
    libelle: str = Field(min_length=1)
    sens: Sens
    montant: Decimal

    #: Code du tiers, sur un compte lettrable. C'est ce qui permet de dire « ce
    #: que doit la Quincaillerie du Wouri » et non « ce que doit le compte 401 ».
    tiers: str | None = None

    #: Marque de lettrage, posée quand la ligne est appariée avec sa contrepartie.
    lettrage: str | None = None

    attribut_fiscal: AttributFiscal | None = None

    @field_validator("montant")
    @classmethod
    def _montant_utilisable(cls, valeur: Decimal) -> Decimal:
        if valeur <= 0:
            raise ValueError(
                "le montant d'une ligne est strictement positif : c'est le sens qui "
                "porte la direction, jamais le signe."
            )
        if valeur != valeur.to_integral_value():
            raise ValueError(
                f"montant {valeur} : le franc CFA ne comporte pas de décimale. "
                "L'arrondi se fait au calcul, jamais à l'enregistrement."
            )
        return valeur

    @property
    def au_debit(self) -> bool:
        return self.sens is Sens.DEBIT

    def inversee(self) -> LigneEcriture:
        """La même ligne, dans l'autre sens. Sert à la contre-passation.

        Le lettrage n'est pas repris : une écriture d'annulation n'hérite pas de
        l'appariement de celle qu'elle annule.
        """
        return self.model_copy(update={"sens": self.sens.inverse, "lettrage": None})


class EcritureComptable(BaseModel):
    """Un fait économique enregistré. Équilibré, numéroté, daté, justifié.

    Quatre invariants sont vérifiés ici même, et aucun ne peut être contourné :

    1. **L'équilibre** — au niveau de l'écriture, jamais de la ligne. Une écriture
       peut compter deux lignes ou quinze ; c'est l'ensemble qui s'équilibre.
    2. **Le numéro** — chronologique et continu par journal et par exercice.
       L'unicité et la continuité relèvent du dépôt, mais le fait qu'un numéro
       existe et soit positif relève de l'entité.
    3. **La justification** — une écriture validée porte sa pièce.
    4. **La validation nommée** — comme pour une version de paramètre au
       référentiel, valider engage une personne, pas le logiciel.
    """

    model_config = ConfigDict(frozen=True)

    journal: str = Field(min_length=1, max_length=8)
    exercice: str = Field(min_length=1)
    numero: int = Field(gt=0)

    date_operation: date
    libelle: str = Field(min_length=1)

    #: Référence de la pièce dans le contexte C · Collecte. C'est le maillon 1 de
    #: la traçabilité de bout en bout.
    piece_justificative: str | None = None

    #: Numéro de la facture ou du document d'origine, tel qu'il figure dessus.
    reference_externe: str | None = None

    lignes: list[LigneEcriture] = Field(min_length=2)

    etat: EtatEcriture = EtatEcriture.BROUILLON
    type: TypeEcriture = TypeEcriture.NORMALE

    #: Clé de l'écriture annulée, et pourquoi. Obligatoires sur une contre-passation.
    ecriture_contrepassee: str | None = None
    motif_contrepassation: str | None = None

    #: Identités portées comme de simples chaînes : l'identité elle-même appartient
    #: au contexte K · Transverse, que la comptabilité n'a pas à connaître.
    saisie_par: str | None = None
    validee_par: str | None = None
    validee_le: datetime | None = None

    # ── Invariants ──────────────────────────────────────────────────────────

    @model_validator(mode="after")
    def _equilibre(self) -> EcritureComptable:
        debit = sum((ligne.montant for ligne in self.lignes if ligne.au_debit), Decimal(0))
        credit = sum((ligne.montant for ligne in self.lignes if not ligne.au_debit), Decimal(0))
        if debit != credit:
            raise ValueError(
                f"écriture {self.journal}/{self.numero} déséquilibrée : "
                f"débit {debit}, crédit {credit}, écart {abs(debit - credit)}."
            )
        # Inutile de vérifier que le total n'est pas nul : chaque ligne est
        # strictement positive et il y en a au moins deux.
        return self

    @model_validator(mode="after")
    def _contrepassation_documentee(self) -> EcritureComptable:
        est_contrepassation = self.type is TypeEcriture.CONTREPASSATION
        if est_contrepassation and not (self.ecriture_contrepassee and self.motif_contrepassation):
            raise ValueError(
                "une contre-passation désigne l'écriture qu'elle annule et dit pourquoi. "
                "C'est le seul mode de correction admis, et il doit rester lisible des "
                "années plus tard."
            )
        if not est_contrepassation and self.ecriture_contrepassee:
            raise ValueError(
                "seule une écriture de type CONTREPASSATION référence une écriture annulée."
            )
        return self

    @model_validator(mode="after")
    def _validation_engage_quelqu_un(self) -> EcritureComptable:
        if self.etat is not EtatEcriture.VALIDEE:
            return self
        if not (self.validee_par and self.validee_le):
            raise ValueError(
                "une écriture validée porte le nom de qui l'a validée et la date. "
                "Le Centre engage sa responsabilité sur ce qu'il présente : la validation "
                "est un acte personnel, pas un changement d'état anonyme."
            )
        if not self.piece_justificative:
            raise ValueError(
                "une écriture validée porte sa pièce justificative. Sans elle, la "
                "traçabilité est rompue dès le premier maillon."
            )
        return self

    # ── Lecture ─────────────────────────────────────────────────────────────

    @property
    def cle(self) -> str:
        """Identifiant stable, lisible, et trivial à trier.

        Forme `2026/AC/000042`. C'est cette chaîne que porte
        `ecriture_contrepassee`, et celle qu'on retrouve dans un journal papier.
        """
        return f"{self.exercice}/{self.journal}/{self.numero:06d}"

    @property
    def total_debit(self) -> Decimal:
        return sum((ligne.montant for ligne in self.lignes if ligne.au_debit), Decimal(0))

    @property
    def total_credit(self) -> Decimal:
        return sum((ligne.montant for ligne in self.lignes if not ligne.au_debit), Decimal(0))

    @property
    def montant(self) -> Decimal:
        """Le montant de l'écriture. Débit et crédit étant égaux, l'un des deux suffit."""
        return self.total_debit

    @property
    def modifiable(self) -> bool:
        return self.etat is EtatEcriture.BROUILLON

    @property
    def comptes_mouvementes(self) -> list[str]:
        return sorted({ligne.compte for ligne in self.lignes})

    @property
    def porte_une_consequence_fiscale(self) -> bool:
        return any(
            ligne.attribut_fiscal is not None and not ligne.attribut_fiscal.est_neutre
            for ligne in self.lignes
        )

    @property
    def montant_a_reintegrer(self) -> Decimal:
        """Ce que cette écriture enverra au tableau de passage à la clôture."""
        return sum(
            (
                ligne.attribut_fiscal.montant_a_reintegrer
                for ligne in self.lignes
                if ligne.attribut_fiscal is not None
                and ligne.attribut_fiscal.montant_a_reintegrer is not None
            ),
            Decimal(0),
        )

    # ── Transitions ─────────────────────────────────────────────────────────

    def valider(self, par: str, le: datetime) -> EcritureComptable:
        """Rend une copie validée. L'originale n'est pas touchée — elle est gelée.

        Après validation, l'écriture devient immuable : la seule correction
        possible est la contre-passation.
        """
        if self.etat is EtatEcriture.VALIDEE:
            raise ValueError(f"écriture {self.cle} déjà validée le {self.validee_le}")
        # ⚠️ `transiter`, et non `model_copy` : c'est ici que l'invariant « une
        # écriture validée porte sa pièce justificative » doit jouer, et
        # `model_copy` ne rejoue aucun validateur. Voir `app/partage/copie.py`.
        return transiter(
            self, etat=EtatEcriture.VALIDEE, validee_par=par, validee_le=le
        )

    def contrepasser(
        self,
        numero: int,
        date_operation: date,
        motif: str,
        saisie_par: str | None = None,
    ) -> EcritureComptable:
        """Fabrique l'écriture inverse qui annule celle-ci.

        Elle porte la date du jour où l'on s'aperçoit de l'erreur, jamais celle de
        l'écriture d'origine : on ne retouche pas le passé. C'est le principe
        d'intangibilité, et c'est ce qui rend une piste d'audit défendable.
        """
        if self.etat is not EtatEcriture.VALIDEE:
            raise ValueError(
                f"écriture {self.cle} : on ne contre-passe qu'une écriture validée. "
                "Un brouillon se corrige avant validation (route de correction, pas 72)."
            )
        if not motif.strip():
            raise ValueError("une contre-passation sans motif est une trace illisible")
        return EcritureComptable(
            journal=self.journal,
            exercice=self.exercice,
            numero=numero,
            date_operation=date_operation,
            libelle=f"Contre-passation de {self.cle} — {self.libelle}",
            piece_justificative=self.piece_justificative,
            reference_externe=self.reference_externe,
            lignes=[ligne.inversee() for ligne in self.lignes],
            etat=EtatEcriture.BROUILLON,
            type=TypeEcriture.CONTREPASSATION,
            ecriture_contrepassee=self.cle,
            motif_contrepassation=motif,
            saisie_par=saisie_par,
        )

    def avec_attribut_fiscal(self, index: int, attribut: AttributFiscal) -> EcritureComptable:
        """Rend une copie dont une ligne porte l'attribut fiscal donné.

        Réservé au brouillon : poser un attribut sur une écriture validée
        reviendrait à la modifier après coup.
        """
        if not self.modifiable:
            raise ValueError(
                f"écriture {self.cle} validée : un attribut fiscal ne se pose plus. "
                "Contre-passer et ressaisir."
            )
        lignes: list[Any] = list(self.lignes)
        lignes[index] = transiter(lignes[index], attribut_fiscal=attribut)
        return transiter(self, lignes=lignes)
