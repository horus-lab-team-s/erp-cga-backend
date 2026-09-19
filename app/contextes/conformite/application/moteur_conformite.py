"""Moteur de conformité de facture.

Une facture ne se juge pas « conforme / non conforme » de façon binaire. Elle produit un
rapport contenant des constats gradués, chacun assorti d'une conséquence fiscale
chiffrée. C'est cette conséquence qui fait la valeur du produit : elle alimente la TVA
déductible du mois puis les réintégrations de la liasse annuelle.
"""

from __future__ import annotations

from datetime import date

from app.contextes.conformite.application.resolution_parametres import resoudre_parametres
from app.contextes.conformite.domaine.entites import (
    Constat,
    FactureAControler,
    RapportConformite,
    Regle,
    RegleEnEchec,
)
from app.contextes.conformite.domaine.schema_faits import SCHEMA_FACTURE
from app.contextes.conformite.domaine.valorisation import valoriser_fiscalement
from app.contextes.referentiel.api import (
    ParametreResolu,
    ServiceParametres,
    StatutValidation,
)
from app.moteur.chemins import chemins_cites
from app.moteur.consequence import Valorisation
from app.moteur.evaluation import Declenchement, evaluer_regles

__all__ = ["MoteurConformite"]



class MoteurConformite:
    """Le même moteur sert aux trois moments de contrôle — émission, réception, revue
    périodique. Seul le jeu de règles change."""

    def __init__(
        self,
        regles: list[Regle],
        parametres: ServiceParametres,
        valorisation: Valorisation = valoriser_fiscalement,
    ) -> None:
        # Confronter chaque prédicat au schéma **ici**, et non à la première facture :
        # une règle qui interroge un fait inexistant ne lève rien à l'évaluation, elle
        # rend faux et produit un constat sur toutes les pièces contrôlées. Le refus au
        # montage est le seul moment où l'erreur coûte encore zéro.
        for regle in regles:
            SCHEMA_FACTURE.valider_predicat(
                chemins_cites(regle.predicat), origine=f"la règle {regle.code}"
            )
        self._regles = regles
        self._parametres = parametres
        self._valoriser = valorisation


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
        faits = facture.faits()

        evaluation = evaluer_regles(
            self._regles,
            faits,
            date_operation,
            resolveur=lambda predicat, a_la_date: resoudre_parametres(
                predicat, self._parametres, a_la_date
            ),
            valorisation=self._valoriser,
            ignorer=lambda regle: regle.statut is StatutValidation.ABROGE,
        )

        # Dédoublonnage par code : deux règles peuvent s'appuyer sur le même paramètre,
        # et le rapport doit dire quelles valeurs ont servi, pas combien de fois.
        employes: dict[str, ParametreResolu] = {
            parametre.code: parametre for parametre in evaluation.references_employees
        }

        return RapportConformite(
            reference_document=facture.document.reference,
            date_operation=date_operation,
            constats=[self._en_constat(d) for d in evaluation.declenchements],
            regles_appliquees=evaluation.regles_appliquees,
            regles_en_echec=[
                RegleEnEchec(code_regle=e.code_regle, motif=e.motif) for e in evaluation.echecs
            ],
            parametres_employes=sorted(employes.values(), key=lambda p: p.code),
        )

    @staticmethod
    def _en_constat(declenchement: Declenchement) -> Constat:
        """Habille un déclenchement en constat de conformité.

        C'est ici que le domaine ajoute ce que la boucle ignore : le libellé, la sévérité,
        la catégorie, le fondement, le message et la remédiation. La boucle rend ce qui
        s'est produit, le domaine dit ce que cela signifie pour un comptable.
        """
        regle = declenchement.regle
        return Constat(
            code_regle=regle.code,
            libelle=regle.libelle,
            severite=regle.severite,
            categorie=regle.categorie,
            fondement=regle.fondement,
            message=regle.message,
            remediation=regle.remediation,
            consequence=regle.consequence,
            enjeu=declenchement.enjeu,
            regle_a_valider=regle.statut is not StatutValidation.VALIDE,
        )
