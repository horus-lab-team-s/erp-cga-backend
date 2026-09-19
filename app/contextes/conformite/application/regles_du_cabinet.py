"""Construire, éprouver, proposer et trancher une règle du cabinet (pas 97).

Voir `domaine/constructeur.py` (la traduction des conditions en prédicat) et
`domaine/regles_du_cabinet.py` (la proposition et sa validation).

⚠️ QUI A LE DROIT : LE CIRCUIT, LU AU RÉFÉRENTIEL

Les permissions nommées par le circuit sont des chaînes ; la route passe celles que la
session détient. Ce module ne connaît pas la table des rôles.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.contextes.conformite.domaine.constructeur import (
    Combinaison,
    ConditionDAnomalie,
    compiler_l_anomalie,
    decrire,
)
from app.contextes.conformite.domaine.entites import (
    ConsequenceFiscale,
    FactureAControler,
    Portee,
    RapportConformite,
    RegimeEmetteur,
    Regle,
    Severite,
    TypeDocument,
)
from app.contextes.conformite.domaine.regles_du_cabinet import (
    EssaiDeRegle,
    PropositionDeRegle,
    PropositionRefusee,
    StatutProposition,
)
from app.contextes.conformite.domaine.schema_faits import SCHEMA_FACTURE
from app.contextes.referentiel.contrats import Fondement

#: La date d'effet de la règle pendant l'essai : avant toute facture connue.
_DEPUIS_TOUJOURS = date(2000, 1, 1)

__all__ = [
    "ConstructionDeRegle",
    "HorsDuCircuitDesRegles",
    "construire",
    "eprouver",
    "proposer_une_regle",
    "retirer_une_regle",
    "trancher_une_regle",
]


class HorsDuCircuitDesRegles(PermissionError):
    """La session ne détient aucune permission que le circuit désigne pour ce geste."""


class ConstructionDeRegle(BaseModel):
    """Tout ce que la personne décide, **sans une ligne de syntaxe**."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    libelle: str = Field(min_length=5, max_length=160)
    categorie: str = Field(default="REGLE_DU_CABINET", min_length=3, max_length=40)
    severite: Severite
    types_document: list[TypeDocument] = Field(min_length=1)
    #: Les régimes de l'**adhérent** concernés ; `None` : tous.
    #:
    #: ⚠️ Ajouté à l'essai du pas 97 : la même règle « espèces au-delà du seuil » construite
    #: ici réagissait sur 7 factures, celle du fichier sur 3. La différence était les
    #: adhérents au régime synthétique, qui ne récupèrent jamais la TVA : leur annoncer « TVA
    #: non déductible » énonce un préjudice inexistant. Voir `Portee.regimes_destinataire`.
    regimes_destinataire: list[RegimeEmetteur] | None = None
    combinaison: Combinaison = Combinaison.TOUTES
    conditions: list[ConditionDAnomalie] = Field(min_length=1)
    #: Ce que l'anomalie coûte : TVA, charge, rectification, vérification.
    tva_non_deductible: bool = False
    charge_non_deductible: bool = False
    rectification_requise: bool = False
    verification_requise: bool = False
    fondement: Fondement
    message: str = Field(min_length=10, max_length=600)
    remediation: str = Field(min_length=10, max_length=600)
    applicable_du: date


def construire(
    construction: ConstructionDeRegle, *, code: str, unite_du_parametre: dict[str, str]
) -> Regle:
    """La règle compilée, **à valider**. Lève `ConstructionRefusee` sur une condition impossible."""
    predicat = compiler_l_anomalie(
        construction.conditions,
        construction.combinaison,
        schema=SCHEMA_FACTURE,
        unite_du_parametre=unite_du_parametre,
    )
    return Regle(
        code=code,
        libelle=construction.libelle,
        categorie=construction.categorie,
        version="cabinet.1",
        applicable_du=construction.applicable_du,
        severite=construction.severite,
        fondement=construction.fondement,
        portee=Portee(
            type_document=list(construction.types_document),
            regimes_destinataire=construction.regimes_destinataire,
        ),
        predicat=predicat,
        consequence=ConsequenceFiscale(
            tva_deductible=False if construction.tva_non_deductible else None,
            charge_deductible=False if construction.charge_non_deductible else None,
            rectification_requise=construction.rectification_requise,
            verification_requise=construction.verification_requise,
        ),
        message=construction.message,
        remediation=construction.remediation,
    )


def eprouver(
    regle: Regle,
    factures: Iterable[FactureAControler],
    controler: Callable[[Regle, FactureAControler], RapportConformite],
) -> EssaiDeRegle:
    """Joue la règle **seule** sur chaque facture, à sa date d'émission.

    Seule : l'essai dit ce que **cette** règle ajoute, pas ce que font les autres. Une facture
    hors de la portée de la règle (autre type de document, autre régime) n'est pas comptée.

    ⚠️ L'ESSAI JUGE LA CONDITION, PAS LA DATE D'EFFET

    Une règle du cabinet prend effet aujourd'hui ou plus tard ; les factures connues sont
    antérieures. Jouée telle quelle, elle n'est **en vigueur sur aucune**, et l'essai rendait
    « ne réagit sur rien » pour « total TTC supérieur à 0 », qui réagit sur tout : la garde
    contre une règle qui accuse chaque facture ne voyait rien. L'essai joue donc la règle
    comme si elle avait toujours été en vigueur ; les paramètres, eux, restent résolus à la
    date de chaque facture.
    """
    regle = regle.model_copy(update={"applicable_du": _DEPUIS_TOUJOURS, "applicable_au": None})
    eprouvees = 0
    reagit: list[str] = []
    for facture in factures:
        if not regle.concerne(facture.faits()):
            continue
        eprouvees += 1
        rapport = controler(regle, facture)
        if any(c.code_regle == regle.code for c in rapport.constats):
            reagit.append(facture.document.reference)
    return EssaiDeRegle(eprouvees=eprouvees, reagit_sur=sorted(reagit))


def _exiger(permissions: Iterable[str], designees: tuple[str, ...], geste: str) -> None:
    if not frozenset(permissions).intersection(designees):
        raise HorsDuCircuitDesRegles(
            f"le circuit du cabinet ne vous désigne pas pour {geste} une règle."
        )


def proposer_une_regle(
    construction: ConstructionDeRegle,
    *,
    motif: str,
    par: str,
    nom: str,
    le: datetime,
    permissions: Iterable[str],
    designees: tuple[str, ...],
    motif_minimum: int,
    unite_du_parametre: dict[str, str],
    factures: Iterable[FactureAControler],
    controler: Callable[[Regle, FactureAControler], RapportConformite],
    depot,
) -> PropositionDeRegle:
    _exiger(permissions, designees, "proposer")
    if len(motif.strip()) < motif_minimum:
        raise PropositionRefusee(
            f"le motif doit compter au moins {motif_minimum} caractères : il dit quel texte "
            "la règle traduit."
        )
    if construction.applicable_du < le.date():
        raise PropositionRefusee(
            "une règle du cabinet ne prend pas effet dans le passé : les contrôles déjà rendus "
            "changeraient sans que personne ne les relise."
        )
    identifiant = uuid.uuid4().hex[:8].upper()
    regle = construire(
        construction, code=f"CAB-{identifiant}", unite_du_parametre=unite_du_parametre
    )
    essai = eprouver(regle, factures, controler)
    if essai.reagit_partout and essai.eprouvees >= 3:
        raise PropositionRefusee(
            f"la règle réagit sur les {essai.eprouvees} factures éprouvées. C'est le signe d'une "
            "condition inversée ou d'un seuil mal choisi : validée, elle accuserait chaque facture "
            "du cabinet. Revoir les conditions, puis l'éprouver de nouveau."
        )
    proposition = PropositionDeRegle(
        identifiant=f"REG-{identifiant}",
        regle=regle,
        conditions=list(construction.conditions),
        combinaison=construction.combinaison,
        phrase=decrire(construction.conditions, construction.combinaison, SCHEMA_FACTURE),
        essai=essai,
        motif=motif.strip(),
        propose_par=par,
        propose_par_nom=nom,
        propose_le=le,
        statut=StatutProposition.PROPOSEE,
    )
    depot.enregistrer(proposition)
    return proposition


def trancher_une_regle(
    identifiant: str,
    *,
    valider: bool,
    motif: str,
    par: str,
    nom: str,
    le: datetime,
    permissions: Iterable[str],
    designees: tuple[str, ...],
    quatre_yeux: bool,
    motif_minimum: int,
    depot,
) -> PropositionDeRegle:
    proposition = depot.trouver(identifiant)
    if proposition is None:
        raise LookupError(f"proposition « {identifiant} » inconnue.")
    _exiger(permissions, designees, "valider")
    tranchee = proposition.trancher(
        valider=valider,
        par=par,
        nom=nom,
        le=le,
        motif=motif,
        quatre_yeux=quatre_yeux,
        motif_minimum=motif_minimum,
    )
    depot.enregistrer(tranchee)
    return tranchee


def retirer_une_regle(
    identifiant: str,
    *,
    a_compter_du: date,
    motif: str,
    par: str,
    nom: str,
    le: datetime,
    permissions: Iterable[str],
    designees: tuple[str, ...],
    motif_minimum: int,
    depot,
) -> PropositionDeRegle:
    """Réservé à qui peut valider une règle : retirer engage autant que mettre en vigueur."""
    proposition = depot.trouver(identifiant)
    if proposition is None:
        raise LookupError(f"proposition « {identifiant} » inconnue.")
    _exiger(permissions, designees, "retirer")
    retiree = proposition.retirer(
        par=par,
        nom=nom,
        le=le,
        a_compter_du=a_compter_du,
        motif=motif,
        motif_minimum=motif_minimum,
    )
    depot.enregistrer(retiree)
    return retiree
