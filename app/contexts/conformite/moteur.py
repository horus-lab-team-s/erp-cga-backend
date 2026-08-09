"""Moteur de conformité de facture.

Une facture ne se juge pas « conforme / non conforme » de façon binaire. Elle produit un
rapport contenant des constats gradués, chacun assorti d'une conséquence fiscale
chiffrée. C'est cette conséquence qui fait la valeur du produit : elle alimente la TVA
déductible du mois puis les réintégrations de la liasse annuelle.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

from ..referentiel.modeles import ParametreResolu, StatutValidation
from ..referentiel.service import ServiceParametres
from .jsonlogic import ErreurPredicat, evaluer
from .modeles import (
    ConsequenceFiscale,
    Constat,
    FactureAControler,
    RapportConformite,
    Regle,
    RegleEnEchec,
)
from .resolution import resoudre_parametres

__all__ = ["MoteurConformite", "charger_regles"]


def charger_regles(dossier: Path) -> list[Regle]:
    """Charge toutes les règles d'un dossier. Une règle malformée fait échouer le
    chargement : mieux vaut un démarrage refusé qu'un contrôle silencieusement absent."""
    regles: list[Regle] = []
    for chemin in sorted(dossier.glob("*.yaml")):
        brut = yaml.safe_load(chemin.read_text(encoding="utf-8"))
        if not isinstance(brut, dict):
            raise ValueError(f"{chemin.name} : un objet YAML est attendu à la racine")
        try:
            regles.append(Regle.model_validate(brut))
        except Exception as exc:
            raise ValueError(f"{chemin.name} : règle invalide — {exc}") from exc

    doublons = {r.code for r in regles if sum(x.code == r.code for x in regles) > 1}
    if doublons:
        raise ValueError(f"codes de règle en double : {sorted(doublons)}")
    return regles


def calculer_enjeu(consequence: ConsequenceFiscale, facture: FactureAControler) -> Decimal | None:
    """Chiffrage de l'enjeu, § 4 de Docs/architecture/03-moteur-conformite.md.

    TVA seule rejetée → montant de TVA. Charge seule → montant HT. Les deux → TTC.
    Aucune → None : le constat reste qualitatif et ne doit pas afficher de montant.
    """
    tva, charge = consequence.rejette_tva, consequence.rejette_charge
    if tva and charge:
        return facture.montants.total_ttc
    if tva:
        return facture.montants.total_tva
    if charge:
        return facture.montants.total_ht
    return None


class MoteurConformite:
    """Le même moteur sert aux trois moments de contrôle — émission, réception, revue
    périodique. Seul le jeu de règles change."""

    def __init__(self, regles: list[Regle], parametres: ServiceParametres) -> None:
        self._regles = regles
        self._parametres = parametres

    @classmethod
    def depuis_dossier(cls, referentiel: Path) -> MoteurConformite:
        return cls(
            regles=charger_regles(referentiel / "regles"),
            parametres=ServiceParametres.depuis_yaml(referentiel / "parametres.yaml"),
        )

    @property
    def regles(self) -> list[Regle]:
        return list(self._regles)

    def controler(
        self,
        facture: FactureAControler,
        a_la_date: date | None = None,
    ) -> RapportConformite:
        """Contrôle une facture avec le jeu de règles en vigueur à la date de l'opération.

        Par défaut, la date d'émission de la facture : une facture de 2024 se contrôle
        avec les règles de 2024, jamais avec celles d'aujourd'hui.
        """
        date_operation = a_la_date or facture.document.date_emission
        donnees = facture.donnees_predicat()

        constats: list[Constat] = []
        echecs: list[RegleEnEchec] = []
        parametres_employes: dict[str, ParametreResolu] = {}
        appliquees = 0

        for regle in self._regles:
            if not regle.en_vigueur(date_operation) or not regle.concerne(facture):
                continue
            if regle.statut is StatutValidation.ABROGE:
                continue

            try:
                predicat, employes = resoudre_parametres(
                    regle.predicat, self._parametres, date_operation
                )
                for parametre in employes:
                    parametres_employes[parametre.code] = parametre
                conforme = bool(evaluer(predicat, donnees))
            except (ErreurPredicat, LookupError, TypeError, ValueError) as exc:
                echecs.append(RegleEnEchec(code_regle=regle.code, motif=str(exc)))
                continue

            appliquees += 1
            if conforme:
                continue

            constats.append(
                Constat(
                    code_regle=regle.code,
                    libelle=regle.libelle,
                    severite=regle.severite,
                    categorie=regle.categorie,
                    fondement=regle.fondement,
                    message=regle.message,
                    remediation=regle.remediation,
                    consequence=regle.consequence,
                    enjeu=calculer_enjeu(regle.consequence, facture),
                    regle_a_valider=regle.statut is not StatutValidation.VALIDE,
                )
            )

        return RapportConformite(
            reference_document=facture.document.reference,
            date_operation=date_operation,
            constats=constats,
            regles_appliquees=appliquees,
            regles_en_echec=echecs,
            parametres_employes=sorted(parametres_employes.values(), key=lambda p: p.code),
        )
