"""Le passage du résultat comptable au résultat fiscal.

─────────────────────────────────────────────────────────────────────────────────
LE MAILLON QUI DONNE SON SENS À TOUT LE PRODUIT

Le moteur de conformité chiffre une anomalie sur une facture d'octobre — « TVA non
déductible : 379 350 FCFA ». Jusqu'ici, ce chiffrage informait. Ici, il devient une
**ligne du tableau de passage**, donc une somme réintégrée au résultat imposable,
donc de l'impôt réellement dû.

C'est ce que la fiche de présentation appelle « une conséquence fiscale chiffrée
qui se propage jusqu'à la liasse annuelle ». Ce module est l'endroit où cette
phrase devient vraie ou reste creuse.

    résultat comptable
      + réintégrations   ce que la comptabilité a déduit et que le fisc refuse
      − déductions       ce que le fisc admet en plus, dont l'abattement CGA
      = résultat fiscal

DEUX ERREURS DE SENS OPPOSÉ, ET ELLES NE COÛTENT PAS LA MÊME CHOSE

Une **réintégration oubliée** sous-estime l'impôt : l'adhérent paie moins que dû,
et le redressement viendra avec ses pénalités. Une **déduction oubliée** le fait
payer trop : personne ne s'en plaindra à l'administration, et l'erreur peut vivre
des années.

La première est donc traitée sans indulgence — toute conséquence chiffrée par D
est réintégrée. La seconde l'est avec prudence : l'abattement CGA n'est accordé
que si l'adhésion couvre réellement l'exercice, et jamais sur un déficit.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.contextes.cloture.domaine.entites import LignePassage, NaturePassage
from app.contextes.referentiel.api import (
    AucuneVersionApplicable,
    ParametreInconnu,
    ServiceParametres,
    StatutValidation,
)

__all__ = ["ConsequenceAReintegrer", "PassageFiscal", "etablir_le_passage"]

_FRANC = Decimal("1")


@dataclass(frozen=True)
class ConsequenceAReintegrer:
    """Une conséquence fiscale chiffrée, telle que D · Conformité la produit.

    ─────────────────────────────────────────────────────────────────────────
    POURQUOI UNE STRUCTURE LOCALE ET NON LE RAPPORT DE CONFORMITÉ

    H pourrait lire `RapportConformite` : le graphe l'autorise. Mais le rapport
    porte trente champs dont H n'emploie que trois, et l'emprunter obligerait à
    construire un rapport complet — avec ses règles, ses paramètres employés,
    ses constats — pour tester une seule réintégration.

    Surtout, cela figerait le contrat : toute évolution du rapport de D casserait
    la clôture. La conversion se fait donc à la frontière, dans l'adaptateur
    entrant, et le cas d'usage reçoit exactement ce dont il a besoin.
    ─────────────────────────────────────────────────────────────────────────
    """

    #: La référence de la pièce — c'est elle que le vérificateur demandera.
    reference_piece: str
    #: Le motif en langage clair, tel qu'il figurera au tableau.
    motif: str
    montant: Decimal
    #: Le code du poste de réintégration désigné par la règle, s'il y en a un.
    poste: str | None = None
    #: Les codes des règles qui l'ont produite.
    regles: tuple[str, ...] = ()


@dataclass(frozen=True)
class PassageFiscal:
    """Le tableau de passage, ligne à ligne, avec ses deux résultats."""

    resultat_comptable: Decimal
    lignes: tuple[LignePassage, ...]
    #: Pourquoi l'abattement CGA ne figure pas **alors que le droit est ouvert**.
    #:
    #: ⚠️ `None` dans les deux autres cas : l'abattement figure, ou le droit est
    #: fermé, et c'est alors le motif du droit qui explique son absence. Ce champ
    #: couvre le troisième cas, qui était muet : un droit ouvert, un motif qui dit
    #: « adhésion sur tout l'exercice, chiffre d'affaires sous le seuil », et pas
    #: d'abattement. Le réviseur y lisait un oubli, et il l'aurait « corrigé ».
    abattement_cga_ecarte: str | None = None

    @property
    def total_reintegrations(self) -> Decimal:
        return sum(
            (ligne.montant for ligne in self.lignes if ligne.nature is NaturePassage.REINTEGRATION),
            Decimal(0),
        )

    @property
    def total_deductions(self) -> Decimal:
        return sum(
            (ligne.montant for ligne in self.lignes if ligne.nature is NaturePassage.DEDUCTION),
            Decimal(0),
        )

    @property
    def resultat_fiscal(self) -> Decimal:
        return self.resultat_comptable + sum(
            (ligne.signe for ligne in self.lignes), Decimal(0)
        )

    @property
    def repose_sur_des_valeurs_non_validees(self) -> bool:
        return any(ligne.non_valide for ligne in self.lignes)


def etablir_le_passage(
    resultat_comptable: Decimal,
    consequences: list[ConsequenceAReintegrer],
    parametres: ServiceParametres,
    cloture: date,
    *,
    droit_a_l_abattement_cga: bool,
) -> PassageFiscal:
    """Établit le tableau de passage de l'exercice.

    `droit_a_l_abattement_cga` est reçu **déjà apprécié**, par
    `apprecier_le_droit` : adhésion sur tout l'exercice, et chiffre d'affaires sous
    le seuil d'adhésion.

    ⚠️ **Ce paramètre s'appelait `adherent_sur_l_exercice`, et le nom mentait par
    omission.** Il laissait croire que l'adhésion suffisait, alors que le seuil
    d'adhésion ferme le droit à une entreprise adhérente devenue trop grande. Et
    la liasse le recevait de la requête, avec `True` par défaut.
    """
    lignes: list[LignePassage] = [
        _reintegrer(consequence) for consequence in consequences
    ]

    abattement, ecarte = _abattement_cga(
        resultat_comptable + sum((ligne.signe for ligne in lignes), Decimal(0)),
        parametres,
        cloture,
        droit_a_l_abattement_cga=droit_a_l_abattement_cga,
    )
    if abattement is not None:
        lignes.append(abattement)

    return PassageFiscal(
        resultat_comptable=resultat_comptable,
        lignes=tuple(lignes),
        abattement_cga_ecarte=ecarte,
    )


def _reintegrer(consequence: ConsequenceAReintegrer) -> LignePassage:
    """Transforme une conséquence chiffrée en ligne de réintégration.

    Le libellé reprend le motif de la règle plutôt que d'en fabriquer un : c'est
    ce motif que l'adhérent a déjà lu sur le rapport de conformité de la pièce, et
    le retrouver mot pour mot au tableau de passage lui montre que le contrôle de
    janvier et l'impôt de mars sont la même chose.
    """
    return LignePassage(
        code=consequence.poste or "REINT_CONFORMITE",
        libelle=consequence.motif,
        nature=NaturePassage.REINTEGRATION,
        montant=abs(consequence.montant),
        origine=" · ".join(
            filter(None, [consequence.reference_piece, ", ".join(consequence.regles)])
        )
        or None,
    )


def _abattement_cga(
    resultat_avant_abattement: Decimal,
    parametres: ServiceParametres,
    cloture: date,
    *,
    droit_a_l_abattement_cga: bool,
) -> tuple[LignePassage | None, str | None]:
    """L'abattement consenti aux adhérents d'un centre de gestion agréé, ou pourquoi il est écarté.

    Rend `(ligne, None)` quand l'abattement s'applique, `(None, motif)` quand le
    droit est ouvert mais que l'abattement ne peut pas s'appliquer, et
    `(None, None)` quand le droit est fermé : son propre motif l'explique déjà.

    ─────────────────────────────────────────────────────────────────────────
    TROIS CONDITIONS, ET CHACUNE ÉVITE UNE FAUTE DIFFÉRENTE

    1. **Le droit doit être ouvert** : adhésion sur tout l'exercice, et chiffre
       d'affaires sous le seuil d'adhésion. Voir `droit_cga.py`. Accorder
       l'abattement hors de ces conditions ferait au Centre une attestation fausse,
       et c'est son agrément qui répond.
    2. **Le résultat doit être bénéficiaire.** Un abattement sur un déficit
       l'aggraverait, donc augmenterait le report déficitaire, donc réduirait
       l'impôt des exercices suivants d'un montant auquel l'adhérent n'a pas
       droit. L'erreur est invisible l'année où elle est commise.
    3. **L'abattement porte sur le résultat APRÈS réintégrations**, jamais sur le
       résultat comptable. L'appliquer avant réduirait la base sur laquelle les
       réintégrations viennent ensuite s'ajouter, et l'avantage serait à la fois
       plus faible et faux.

    Un plafond à zéro signifie **« aucun plafond connu »** et non « plafond
    nul » — voir la note du paramètre. C'est un choix discutable, et c'est
    pourquoi il est écrit ici : le jour où le fiscaliste établit qu'un plafond
    existe, il renseigne la valeur et le calcul le respecte sans modification.
    ─────────────────────────────────────────────────────────────────────────
    """
    if not droit_a_l_abattement_cga:
        return None, None
    if resultat_avant_abattement <= 0:
        return None, (
            "résultat nul ou déficitaire après réintégrations : un abattement "
            "aggraverait le déficit reportable, donc réduirait l'impôt des exercices "
            "suivants d'un montant auquel l'adhérent n'a pas droit."
        )

    try:
        taux = parametres.resoudre("ABATTEMENT_CGA_BENEFICE", cloture)
    except (ParametreInconnu, AucuneVersionApplicable):
        # ⚠️ **Ce refus était muet.** Le droit ouvert, le taux introuvable, et la
        # liasse sans abattement ni explication. S'abstenir reste le bon choix : un
        # taux supposé serait une valeur légale inventée. Mais l'abstention se dit,
        # sinon elle ressemble à un oubli.
        return None, (
            f"le référentiel ne fixe aucun taux d'abattement CGA au {cloture:%d/%m/%Y} : "
            "le droit est ouvert, mais l'abattement ne peut pas être calculé."
        )

    montant = (
        resultat_avant_abattement * taux.valeur_decimale / Decimal(100)
    ).quantize(_FRANC, rounding=ROUND_HALF_UP)

    plafonne = False
    try:
        plafond = parametres.resoudre("ABATTEMENT_CGA_PLAFOND", cloture).valeur_decimale
    except (ParametreInconnu, AucuneVersionApplicable):
        plafond = Decimal(0)
    if plafond > 0 and montant > plafond:
        montant = plafond
        plafonne = True

    libelle = f"Abattement CGA — {taux.valeur_decimale:g} % du bénéfice après réintégrations"
    if plafonne:
        libelle += f", plafonné à {plafond:,.0f} FCFA".replace(",", " ")

    return LignePassage(
        code="ABATT_CGA",
        libelle=libelle,
        nature=NaturePassage.DEDUCTION,
        montant=montant,
        origine="ABATTEMENT_CGA_BENEFICE",
        non_valide=taux.statut is not StatutValidation.VALIDE,
    ), None
