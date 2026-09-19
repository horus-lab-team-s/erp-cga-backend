"""La déclaration mensuelle des personnels employés — le DIPE.

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE DIPE EST, ET CE QU'IL N'EST PAS

C'est le récapitulatif mensuel que l'employeur dépose : qui était employé, pour
quel salaire, et quelles cotisations sont dues. Il **agrège** les bulletins ; il
ne les remplace pas et n'en produit aucun.

Comme le bulletin, il se **calcule** et ne se stocke pas. Deux conséquences :

* le recalculer sur un mois passé emploie les taux de ce mois-là, donc retombe
  sur le même chiffre — c'est ce qu'un contrôle CNPS demande ;
* corriger un contrat corrige la déclaration, sans qu'il faille penser à la
  régénérer. Une déclaration stockée aurait divergé silencieusement.

Ce qui se conserve, c'est le **dépôt** : la référence, la date, le montant versé.
Cela n'est pas modélisé ici et relève de F · Obligations, qui tient déjà les
accusés de dépôt des autres déclarations. G dit ce qui est dû ; F dit qu'il a été
déposé.

LES CONTRATS QUI COUVRENT PARTIELLEMENT LE MOIS SONT INCLUS

Un salarié embauché le 20 est déclaré au titre du mois, et sa cotisation est due.
Exiger le mois entier ferait disparaître de la déclaration les embauches et les
départs, c'est-à-dire exactement les mouvements que le DIPE existe pour signaler.

⚠️ Le montant, lui, n'est **pas proratisé** : le calcul emploie le salaire
mensuel entier. Proratiser suppose une règle — jours calendaires ? jours ouvrés ?
— qui n'appartient pas au code tant que le cabinet ne l'a pas arrêtée. Le
signaler est plus honnête que de choisir en silence, et `mouvements` porte
justement les contrats concernés pour que le collaborateur les revoie.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.contextes.referentiel.api import (
    AucuneVersionApplicable,
    ParametreInconnu,
    ServiceBaremes,
    ServiceParametres,
)
from app.contextes.social.application.paie import calculer_bulletin
from app.contextes.social.domaine.entites import (
    Bulletin,
    Contrat,
    GroupeRisque,
    Periode,
)

__all__ = ["Declaration", "MouvementSalarie", "etablir_la_declaration"]


@dataclass(frozen=True)
class MouvementSalarie:
    """Une entrée ou une sortie survenue dans le mois.

    Le DIPE les signale, et c'est sa principale utilité au-delà du montant : la
    CNPS ouvre et ferme les droits d'un travailleur sur ces mouvements. Un départ
    non déclaré laisse un salarié réputé en poste, et l'employeur continue de lui
    devoir des cotisations.
    """

    salarie: str
    #: `ENTREE` ou `SORTIE`. Une chaîne plutôt qu'une énumération : deux valeurs,
    #: lues et jamais comparées ailleurs qu'ici.
    sens: str
    survenu_le: date


@dataclass(frozen=True)
class Declaration:
    """Le DIPE d'un mois, calculé à partir des contrats en vigueur."""

    entreprise: str
    periode: Periode
    bulletins: tuple[Bulletin, ...]
    mouvements: tuple[MouvementSalarie, ...]
    #: Le jour limite de dépôt, lu au référentiel.
    a_deposer_avant: date

    @property
    def effectif(self) -> int:
        return len(self.bulletins)

    @property
    def masse_salariale_brute(self) -> Decimal:
        """Le brut taxable cumulé — l'assiette que la CNPS contrôle."""
        return sum((b.brut_taxable for b in self.bulletins), Decimal(0))

    @property
    def retenues_salariales(self) -> Decimal:
        return sum((b.retenues_salariales for b in self.bulletins), Decimal(0))

    @property
    def charges_patronales(self) -> Decimal:
        return sum((b.charges_patronales for b in self.bulletins), Decimal(0))

    @property
    def total_a_verser(self) -> Decimal:
        """Ce que l'employeur verse : sa part **et** ce qu'il a retenu au salarié.

        L'erreur de trésorerie classique est de ne provisionner que la part
        patronale. La retenue salariale n'appartient pas à l'employeur : il la
        détient pour le compte de l'administration, et il la doit intégralement.
        """
        return self.retenues_salariales + self.charges_patronales

    @property
    def cotisations_cnps(self) -> Decimal:
        """La part strictement CNPS, qui se verse à un guichet distinct du fisc."""
        return sum(
            (
                ligne.montant
                for bulletin in self.bulletins
                for ligne in bulletin.lignes
                if ligne.code.startswith("CNPS_")
            ),
            Decimal(0),
        )

    @property
    def repose_sur_des_valeurs_non_validees(self) -> bool:
        return any(b.repose_sur_des_valeurs_non_validees for b in self.bulletins)

    @property
    def est_en_retard(self) -> bool:
        """Toujours faux ici : le retard se juge à une date, pas dans l'absolu.

        Volontairement une propriété qui refuse plutôt qu'une comparaison à
        `date.today()`. Le retard se calcule avec `en_retard_au(jour)` — c'est la
        même discipline que partout ailleurs sur ce projet, et elle a une raison :
        un calcul qui lit l'horloge murale casse à minuit.
        """
        raise NotImplementedError(
            "le retard se juge à une date donnée : employer `en_retard_au(jour)`."
        )

    def en_retard_au(self, jour: date) -> bool:
        return jour > self.a_deposer_avant


def etablir_la_declaration(
    entreprise: str,
    periode: Periode,
    contrats: list[Contrat],
    parametres: ServiceParametres,
    baremes: ServiceBaremes,
    *,
    groupe_risque: GroupeRisque = GroupeRisque.A,
) -> Declaration:
    """Établit le DIPE du mois à partir des contrats en vigueur.

    Rend une déclaration même **vide** — effectif zéro — plutôt que de lever. Un
    dossier qui n'a plus de salarié doit pouvoir constater qu'il n'a rien à
    déclarer, et c'est une information : la CNPS attend une déclaration à néant,
    pas une absence de déclaration.
    """
    retenus = [c for c in contrats if c.couvre_la_periode(periode)]
    bulletins = tuple(
        calculer_bulletin(
            contrat, entreprise, periode, parametres, baremes, groupe_risque=groupe_risque
        )
        for contrat in sorted(retenus, key=lambda c: c.salarie)
    )
    return Declaration(
        entreprise=entreprise,
        periode=periode,
        bulletins=bulletins,
        mouvements=_relever_les_mouvements(retenus, periode),
        a_deposer_avant=_echeance(periode, parametres),
    )


def _relever_les_mouvements(
    contrats: list[Contrat], periode: Periode
) -> tuple[MouvementSalarie, ...]:
    """Les entrées et sorties tombant dans le mois."""
    mouvements: list[MouvementSalarie] = []
    for contrat in contrats:
        if periode.premier_jour <= contrat.debut <= periode.dernier_jour:
            mouvements.append(
                MouvementSalarie(
                    salarie=contrat.salarie, sens="ENTREE", survenu_le=contrat.debut
                )
            )
        # ⚠️ `fin` est **exclue** — c'est la convention du projet pour tous les
        # intervalles. Le dernier jour travaillé est donc la veille, et c'est
        # cette date-là qui se déclare comme sortie.
        if contrat.fin is not None:
            dernier_jour = contrat.fin - timedelta(days=1)
            if periode.premier_jour <= dernier_jour <= periode.dernier_jour:
                mouvements.append(
                    MouvementSalarie(
                        salarie=contrat.salarie, sens="SORTIE", survenu_le=dernier_jour
                    )
                )
    return tuple(sorted(mouvements, key=lambda m: (m.survenu_le, m.salarie)))


def _echeance(periode: Periode, parametres: ServiceParametres) -> date:
    """Le jour limite de dépôt : le N du mois suivant la période.

    Un jour limite absent du référentiel rend le dernier jour du mois suivant —
    la borne la plus tardive plausible. Lever ici empêcherait d'établir une
    déclaration pour une ligne manquante dans un fichier, alors que le calcul
    lui-même est complet ; et retenir une date trop précoce afficherait des
    retards imaginaires.
    """
    suivant = periode.suivante()
    try:
        jour = int(parametres.valeur_numerique("DIPE_JOUR_LIMITE_DEPOT", suivant.premier_jour))
    except (ParametreInconnu, AucuneVersionApplicable):
        return suivant.dernier_jour
    return min(date(suivant.annee, suivant.mois, jour), suivant.dernier_jour)
