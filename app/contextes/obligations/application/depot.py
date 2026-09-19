"""Préparer un dépôt, le contrôler, puis en enregistrer la preuve.

─────────────────────────────────────────────────────────────────────────────────
TROIS GESTES, ET ILS SONT DISTINCTS

**Préparer** — assembler les chiffres, les contrôler, produire un bordereau et
son empreinte. Aucune écriture chez l'administration, aucun changement d'état.
Se refait autant de fois qu'on veut.

**Déposer** — remettre au guichet. Aujourd'hui, un humain saisit sur le portail
de la DGI ; demain, peut-être un appel réseau. Le parcours diffère, le reste non.

**Constater le dépôt** — enregistrer l'accusé, et **seulement alors** faire
passer l'obligation à déclarée.

Les fondre serait l'erreur : une obligation marquée déclarée parce qu'on a
préparé le dossier est une obligation qu'on croira déposée alors que personne
n'est allé sur le portail. C'est exactement l'oubli qui produit une pénalité de
retard, et il ne se découvre qu'au courrier de l'administration.

CE QUI FAIT FOI EST L'ACCUSÉ, PAS NOTRE CONVICTION

`declaree_le` reçoit la date **de l'accusé**, pas celle du jour où on le saisit.
Un réviseur qui dépose le 14 et ne consigne le numéro que le 17 doit voir le 14 :
c'est cette date que l'administration retiendra pour dire si le dépôt est dans
les délais.

L'EMPREINTE EST CE QUI PERMETTRA DE SE DÉFENDRE

Le bordereau préparé est haché, et l'accusé porte cette empreinte. Trois ans plus
tard, on peut prouver que ce qui a été déposé est bien ce que le système a
produit — et qu'il n'a pas été retouché depuis. Sans elle, un litige sur les
chiffres déposés se règle à la parole.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.contextes.comptabilite.api import (
    EcritureComptable,
    balance,
    controle_balance_equilibree,
    sequences_incompletes,
)
from app.contextes.obligations.application.declaration_tva import DeclarationTVA
from app.contextes.obligations.domaine.echeances import TypeObligation
from app.contextes.obligations.domaine.obligations import (
    ObligationInstance,
    StatutObligation,
)
from app.contextes.obligations.domaine.recevabilite import (
    Anomalie,
    NiveauRecevabilite,
    Recevabilite,
)
from app.contextes.portefeuille.contrats import Entreprise
from app.contextes.transverse.api import (
    AccuseReception,
    DocumentATransmettre,
    FormatTransmission,
    JournalAudit,
    ModeDepot,
    Portail,
    PortailDeclaratif,
)

__all__ = [
    "CompletudeDeLaPeriode",
    "ExigenceDeRevue",
    "ReglagesDuDepotTVA",
    "RevueDeLaPeriode",
    "DepotImpossible",
    "DossierDeDepot",
    "constater_depot",
    "constater_un_depot_hors_tva",
    "controler_recevabilite",
    "preparer_depot_tva",
]


class DepotImpossible(RuntimeError):
    """Le dépôt ne peut pas avoir lieu — le message dit pourquoi et quoi faire."""


class DossierDeDepot(BaseModel):
    """Un dossier prêt à partir : les chiffres, les contrôles, le bordereau.

    Rendu à l'écran avant tout dépôt. C'est ce que le réviseur lit, et ce qu'il
    recopiera sur le portail tant que le dépôt est manuel.
    """

    model_config = ConfigDict(frozen=True)

    document: DocumentATransmettre
    recevabilite: Recevabilite
    #: L'accusé, quand le dépôt a déjà eu lieu. Sa présence est ce qui distingue
    #: « prêt » de « déposé ».
    accuse: AccuseReception | None = None
    #: Faux quand le guichet réclame une saisie humaine — voir `PortailDeclaratif`.
    depot_automatique: bool = False

    @computed_field
    @property
    def deja_depose(self) -> bool:
        return self.accuse is not None

    @computed_field
    @property
    def a_deposer(self) -> bool:
        """Recevable et pas encore déposé.

        Sérialisé : c'est ce champ qui allume le bouton. Le calculer à l'écran à
        partir de trois autres ferait diverger le front du back à la première
        règle ajoutée.
        """
        return self.recevabilite.deposable and not self.deja_depose


# ── Ce que la route relève pour la période (pas 109) ─────────────────────────


class CompletudeDeLaPeriode(BaseModel):
    """Les pièces de la période : reçues, traitées, et celles que l'adhérent doit encore.

    ⚠️ Pas 109 : jusqu'ici, `pieces_en_souffrance` valait **toujours zéro**. Le paramètre avait
    une valeur par défaut, et aucun appelant ne le passait : la réserve « pièces reçues et non
    comptabilisées » n'avait jamais été levée. Il n'a plus de défaut.
    """

    model_config = ConfigDict(frozen=True)

    pieces_recues: int = Field(ge=0)
    pieces_en_souffrance: list[str] = Field(default_factory=list)
    pieces_attendues: list[str] = Field(default_factory=list)

    @computed_field
    @property
    def pieces_traitees(self) -> int:
        return self.pieces_recues - len(self.pieces_en_souffrance)

    @computed_field
    @property
    def taux(self) -> float | None:
        """La part des pièces reçues déjà traitées. `None` sans pièce : ni 0 ni 100 %."""
        if not self.pieces_recues:
            return None
        return round(self.pieces_traitees / self.pieces_recues, 4)


class ExigenceDeRevue(StrEnum):
    AUCUNE = "AUCUNE"
    TRANSMISE = "TRANSMISE"
    VALIDEE = "VALIDEE"


class ReglagesDuDepotTVA(BaseModel):
    """`Docs/referentiel/obligations/depot_tva.yaml` (pas 109).

    « Le comptable transmet, il ne dépose pas : le dépôt appartient au réviseur, après
    contrôle » (maquette « Parcours comptable », vue D, note 3). Par défaut, le mois doit avoir
    été **transmis** au réviseur, et c'est bloquant.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    revue_du_mois_exigee: ExigenceDeRevue = ExigenceDeRevue.TRANSMISE
    niveau_si_manquante: NiveauRecevabilite = NiveauRecevabilite.BLOQUANT
    source: str = "valeurs par défaut"

    @model_validator(mode="after")
    def _pas_une_simple_information(self) -> ReglagesDuDepotTVA:
        if self.niveau_si_manquante is NiveauRecevabilite.INFORMATION:
            raise ValueError(
                "niveau_si_manquante vaut BLOQUANT ou RESERVE : une revue exigée dont l'absence "
                "ne serait qu'une information ne serait pas exigée."
            )
        return self


class RevueDeLaPeriode(BaseModel):
    """La revue du mois qui couvre la période, telle que la comptabilité la connaît."""

    model_config = ConfigDict(frozen=True)

    identifiant: str | None = None
    #: TRANSMISE, RENVOYEE, VALIDEE ; `None` si le mois n'a jamais été transmis.
    statut: str | None = None


# ── Les contrôles ────────────────────────────────────────────────────────────


def controler_recevabilite(
    obligation: ObligationInstance,
    entreprise: Entreprise,
    ecritures: list[EcritureComptable],
    declaration: DeclarationTVA | None,
    *,
    a_la_date: date,
    accuse_existant: AccuseReception | None,
    completude: CompletudeDeLaPeriode,
    revue: RevueDeLaPeriode,
    reglages: ReglagesDuDepotTVA,
    referentiel_valide: bool = False,
) -> Recevabilite:
    """Tout ce qui doit être vrai pour qu'un dépôt soit défendable.

    L'ordre des contrôles suit leur gravité, de sorte que l'écran montre d'abord
    ce qui empêche.
    """
    anomalies: list[Anomalie] = []

    # ── Ce qui empêche ──────────────────────────────────────────────────────

    if accuse_existant is not None:
        anomalies.append(
            Anomalie(
                code="DEPOT-DEJA-EFFECTUE",
                niveau=NiveauRecevabilite.BLOQUANT,
                libelle=(
                    f"Cette période a déjà été déposée le "
                    f"{accuse_existant.depose_le:%d/%m/%Y}, accusé "
                    f"{accuse_existant.numero}."
                ),
                remediation=(
                    "Un second dépôt produirait une déclaration rectificative non "
                    "demandée, et parfois un double appel de paiement. Pour corriger, "
                    "passer par une déclaration rectificative explicite."
                ),
                reference=accuse_existant.numero,
            )
        )

    if obligation.deposee:
        anomalies.append(
            Anomalie(
                code="OBLIGATION-DEJA-DECLAREE",
                niveau=NiveauRecevabilite.BLOQUANT,
                libelle=f"L'obligation porte déjà la date de dépôt {obligation.declaree_le}.",
                remediation="Vérifier l'échéancier avant de préparer un nouveau dossier.",
            )
        )

    soldes = balance(ecritures)
    if ecritures and not controle_balance_equilibree(soldes):
        debit = sum((s.total_debit for s in soldes), Decimal(0))
        credit = sum((s.total_credit for s in soldes), Decimal(0))
        anomalies.append(
            Anomalie(
                code="BALANCE-DESEQUILIBREE",
                niveau=NiveauRecevabilite.BLOQUANT,
                libelle=(
                    f"La balance ne s'équilibre pas : {debit} au débit contre "
                    f"{credit} au crédit."
                ),
                remediation=(
                    "Une déclaration assise sur une comptabilité déséquilibrée est fausse "
                    "par construction. Reprendre les écritures avant tout dépôt."
                ),
                enjeu=abs(debit - credit),
            )
        )

    trous = sequences_incompletes(ecritures)
    if trous:
        anomalies.append(
            Anomalie(
                code="SEQUENCE-INCOMPLETE",
                niveau=NiveauRecevabilite.BLOQUANT,
                libelle=(
                    f"{len(trous)} rupture(s) de numérotation dans les journaux. "
                    "Une écriture manquante, ou supprimée."
                ),
                remediation=(
                    "Un vérificateur lit la continuité des numéros avant tout le reste : "
                    "un trou est présumé être une écriture retirée. Retrouver les "
                    "numéros manquants ou justifier chaque rupture."
                ),
                reference=", ".join(str(t) for t in trous[:3]),
            )
        )

    if declaration is not None and not entreprise.assujettie_tva_au(
        obligation.periode_fin
    ):
        anomalies.append(
            Anomalie(
                code="HORS-ASSUJETTISSEMENT",
                niveau=NiveauRecevabilite.BLOQUANT,
                libelle=(
                    f"{entreprise.denomination} n'était pas assujettie à la TVA au "
                    f"{obligation.periode_fin} — régime "
                    f"{entreprise.regime_au(obligation.periode_fin)}."
                ),
                remediation=(
                    "Déposer une déclaration de TVA hors assujettissement revient à "
                    "réclamer une déduction à laquelle l'entreprise n'a pas droit. "
                    "Vérifier la date de franchissement du seuil au dossier."
                ),
            )
        )

    anomalie_de_revue = _controler_la_revue(revue, reglages)
    if anomalie_de_revue is not None and anomalie_de_revue.niveau is NiveauRecevabilite.BLOQUANT:
        anomalies.append(anomalie_de_revue)

    # ── Ce qui doit être assumé ─────────────────────────────────────────────

    if anomalie_de_revue is not None and anomalie_de_revue.niveau is NiveauRecevabilite.RESERVE:
        anomalies.append(anomalie_de_revue)

    if not referentiel_valide:
        anomalies.append(
            Anomalie(
                code="REFERENTIEL-NON-VALIDE",
                niveau=NiveauRecevabilite.RESERVE,
                libelle=(
                    "Aucune valeur du référentiel n'a été validée sur le Code général "
                    "des impôts. Les taux et seuils employés proviennent de sources "
                    "secondaires."
                ),
                remediation=(
                    "Faire confirmer chaque paramètre par le fiscaliste référent avant "
                    "de traiter un chiffre de ce système comme opposable. Voir "
                    "/referentiel/validation."
                ),
            )
        )

    if declaration is not None and declaration.tva_rejetee > 0:
        anomalies.append(
            Anomalie(
                code="TVA-REJETEE-PAR-LE-CONTROLE",
                niveau=NiveauRecevabilite.RESERVE,
                libelle=(
                    f"{declaration.tva_rejetee} de TVA écartée par le contrôle de "
                    f"conformité, sur {len(declaration.detail_rejets)} pièce(s)."
                ),
                remediation=(
                    "C'est la valeur du contrôle : cette TVA aurait été réclamée et "
                    "refusée. Chaque rejet porte son code de règle et son motif — les "
                    "faire corriger par l'adhérent plutôt que de les déclarer."
                ),
                enjeu=declaration.tva_rejetee,
            )
        )

    if completude.pieces_en_souffrance:
        anomalies.append(
            Anomalie(
                code="PIECES-NON-TRAITEES",
                niveau=NiveauRecevabilite.RESERVE,
                libelle=(
                    f"{len(completude.pieces_en_souffrance)} pièce(s) reçue(s) et non encore "
                    "comptabilisée(s) sur la période."
                ),
                reference=", ".join(completude.pieces_en_souffrance[:5]),
                remediation=(
                    "Une déclaration établie avant traitement de tout le flux sera "
                    "incomplète, et la TVA déductible sous-évaluée — au détriment de "
                    "l'adhérent."
                ),
            )
        )

    if completude.pieces_attendues:
        anomalies.append(
            Anomalie(
                code="PIECES-ATTENDUES",
                niveau=NiveauRecevabilite.RESERVE,
                libelle=(
                    f"{len(completude.pieces_attendues)} pièce(s) attendue(s) de l'adhérent ne "
                    "sont pas arrivées."
                ),
                remediation=(
                    "Déposer maintenant expose à une déclaration rectificative : relancer "
                    "l'adhérent, ou documenter le choix de déposer sans elles."
                ),
                reference=", ".join(completude.pieces_attendues[:5]),
            )
        )

    # ── Ce qu'il faut savoir ────────────────────────────────────────────────

    if a_la_date > obligation.echeance:
        anomalies.append(
            Anomalie(
                code="ECHEANCE-DEPASSEE",
                niveau=NiveauRecevabilite.INFORMATION,
                libelle=(
                    f"L'échéance du {obligation.echeance} est dépassée de "
                    f"{(a_la_date - obligation.echeance).days} jour(s). Une pénalité "
                    "court."
                ),
                remediation=(
                    "Déposer sans attendre : la pénalité s'accroît par mois entamé. "
                    "Simuler le montant sur /obligations/penalite."
                ),
            )
        )

    if declaration is not None and declaration.neant:
        anomalies.append(
            Anomalie(
                code="DECLARATION-NEANT",
                niveau=NiveauRecevabilite.INFORMATION,
                libelle="Aucune opération sur la période : la déclaration est à néant.",
                remediation=(
                    "Elle reste **due**. L'obligation naît de l'assujettissement, pas de "
                    "l'activité — et l'on est sanctionné pour n'avoir pas déclaré qu'on "
                    "n'avait rien à déclarer."
                ),
            )
        )

    return Recevabilite(anomalies=anomalies)


def _controler_la_revue(
    revue: RevueDeLaPeriode, reglages: ReglagesDuDepotTVA
) -> Anomalie | None:
    """Le mois a-t-il été remis au réviseur, comme le référentiel l'exige ? (pas 109)"""
    exigee = reglages.revue_du_mois_exigee
    if exigee is ExigenceDeRevue.AUCUNE:
        return None
    suffisants = {"TRANSMISE", "VALIDEE"} if exigee is ExigenceDeRevue.TRANSMISE else {"VALIDEE"}
    if revue.statut in suffisants:
        return None
    constat = (
        "le mois n'a pas été transmis au réviseur"
        if revue.statut is None
        else f"la revue du mois est {revue.statut.lower()}"
    )
    attendu = (
        "transmis au réviseur" if exigee is ExigenceDeRevue.TRANSMISE else "validé par le réviseur"
    )
    return Anomalie(
        code="REVUE-DU-MOIS",
        niveau=reglages.niveau_si_manquante,
        libelle=f"Le mois doit être {attendu} avant le dépôt : {constat}.",
        remediation=(
            "Le comptable transmet, il ne dépose pas : le dépôt appartient au réviseur, après "
            "contrôle. Clôturer le mois (Comptabilité, Clôture mensuelle), puis le transmettre."
        ),
        reference=revue.identifiant,
    )


# ── Préparer ─────────────────────────────────────────────────────────────────


def preparer_depot_tva(
    obligation: ObligationInstance,
    entreprise: Entreprise,
    declaration: DeclarationTVA,
    ecritures: list[EcritureComptable],
    *,
    portail: PortailDeclaratif,
    a_la_date: date,
    completude: CompletudeDeLaPeriode,
    revue: RevueDeLaPeriode,
    reglages: ReglagesDuDepotTVA,
    referentiel_valide: bool = False,
) -> DossierDeDepot:
    """Assemble le dossier, le contrôle, et le fige.

    Ne dépose rien et ne change aucun état : préparer se refait autant de fois
    qu'on veut, et doit pouvoir se refaire — c'est en préparant qu'on découvre ce
    qu'il reste à corriger.
    """
    document = DocumentATransmettre(
        portail=Portail.DGI_TELEDECLARATION,
        code_document=obligation.code_obligation,
        entreprise=obligation.entreprise,
        periode_debut=obligation.periode_debut,
        periode_fin=obligation.periode_fin,
        format=FormatTransmission.SAISIE_MANUELLE
        if not portail.depose_automatiquement()
        else FormatTransmission.JSON_INTERNE,
        contenu=_bordereau(obligation, entreprise, declaration),
        montant_a_payer=declaration.tva_a_payer or None,
    )
    accuse = portail.retrouver(document.reference)
    recevabilite = controler_recevabilite(
        obligation,
        entreprise,
        ecritures,
        declaration,
        a_la_date=a_la_date,
        accuse_existant=accuse,
        completude=completude,
        revue=revue,
        reglages=reglages,
        referentiel_valide=referentiel_valide,
    )
    return DossierDeDepot(
        document=document,
        recevabilite=recevabilite,
        accuse=accuse,
        depot_automatique=portail.depose_automatiquement(),
    )


def _bordereau(
    obligation: ObligationInstance,
    entreprise: Entreprise,
    declaration: DeclarationTVA,
) -> str:
    """Le contenu déposé, sérialisé de façon déterministe.

    `sort_keys` et séparateurs sans espace : c'est ce contenu qui est haché, et
    deux sérialisations différentes du même dossier produiraient deux empreintes.
    L'accusé ne se rattacherait alors plus au document, et le contrôle de
    cohérence refuserait un dépôt pourtant correct.

    ⚠️ La structure est **la nôtre**. Elle n'imite aucun formulaire officiel,
    puisqu'aucun n'est publié sous forme exploitable. Elle porte les grandeurs
    que la déclaration de TVA appelle, nommées en clair, de sorte qu'un humain
    puisse les reporter case par case sur le portail.
    """
    return json.dumps(
        {
            "obligation": obligation.code_obligation,
            "niu": entreprise.niu,
            "denomination": entreprise.denomination,
            "regime": entreprise.regime_au(obligation.periode_fin),
            "periode_debut": obligation.periode_debut,
            "periode_fin": obligation.periode_fin,
            "tva_collectee": declaration.tva_collectee,
            "tva_deductible_admise": declaration.tva_deductible_admise,
            "credit_reporte_anterieur": declaration.credit_reporte_anterieur,
            "tva_a_payer": declaration.tva_a_payer,
            "credit_a_reporter": declaration.credit_a_reporter,
            "neant": declaration.neant,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


# ── Constater ────────────────────────────────────────────────────────────────


def verifier_la_date_du_depot(
    obligation: ObligationInstance, depose_le: datetime, a_l_instant: datetime
) -> None:
    """Refuse une date d'accusé impossible. Commune aux deux parcours de constat (pas 87).

    ─────────────────────────────────────────────────────────────────────────
    ⚠️ ELLE N'EXISTAIT QUE POUR LES OBLIGATIONS HORS TVA

    Les deux gardes ci-dessous étaient écrites dans `constater_un_depot` seul. Le
    parcours de la TVA, `constater_depot`, n'en avait aucune. Essai avant correction :
    la TVA de janvier 2026 de SARL BATIMENT PLUS a été consignée **déposée le
    01/12/2026**, le 15/09/2026. L'obligation passait à « déclarée » sur la foi d'un
    dépôt qui n'avait pas eu lieu, et sa date décidait du retard et de la pénalité.

    La date de l'accusé est la seule date que la plateforme reçoit de l'utilisateur
    dans ce parcours, parce que c'est celle que l'administration retient. Elle est
    donc la seule à contrôler, et elle l'est au même endroit pour tous.
    ─────────────────────────────────────────────────────────────────────────
    """
    if depose_le > a_l_instant:
        raise DepotImpossible(
            f"dépôt daté du {depose_le:%d/%m/%Y %H:%M}, postérieur à maintenant. On ne "
            "consigne pas un dépôt qui n'a pas encore eu lieu."
        )
    if not obligation.payable_d_avance and depose_le.date() <= obligation.periode_fin:
        raise DepotImpossible(
            f"dépôt daté du {depose_le:%d/%m/%Y}, alors que la période se termine le "
            f"{obligation.periode_fin:%d/%m/%Y}. Une déclaration porte sur une période "
            "écoulée : vérifier la date de l'accusé, ou la période choisie."
        )


def constater_depot(
    dossier: DossierDeDepot,
    obligation: ObligationInstance,
    accuse: AccuseReception,
    *,
    portail: PortailDeclaratif,
    journal: JournalAudit,
    par: str,
    a_l_instant: datetime,
) -> tuple[ObligationInstance, AccuseReception]:
    """Enregistre l'accusé et fait passer l'obligation à déclarée.

    Refuse si le dossier n'est pas recevable : constater un dépôt qu'on n'aurait
    pas dû faire ne le rend pas régulier, cela ne fait qu'en effacer la trace.
    """
    verifier_la_date_du_depot(obligation, accuse.depose_le, a_l_instant)
    if not dossier.recevabilite.deposable:
        bloquants = ", ".join(a.code for a in dossier.recevabilite.bloquants)
        raise DepotImpossible(
            f"dossier non recevable : {bloquants}. Corriger avant de consigner un "
            "accusé — un dépôt irrégulier consigné comme régulier n'est plus visible "
            "de personne."
        )

    consigne = portail.enregistrer_accuse(accuse, document=dossier.document)
    declaree = obligation.avancer(
        StatutObligation.DECLAREE,
        # La date de l'accusé, jamais celle du jour — voir l'en-tête.
        declaree_le=consigne.depose_le.date(),
        reference_depot=consigne.numero,
    )
    journal.ajouter(
        horodatage=a_l_instant,
        acteur=par,
        action="obligation.deposee",
        objet_type="obligation",
        objet_id=f"{obligation.entreprise}/{obligation.code_obligation}",
        apres={
            # Pas 94 : le dossier en clair, et non seulement dans `objet_id`. Une
            # notification désigne ses destinataires par un champ de l'entrée ; le
            # découper dans « NIU/CODE » ferait dépendre l'avis d'un format d'identifiant.
            "dossier": obligation.entreprise,
            "obligation": obligation.code_obligation,
            "accuse": consigne.numero,
            "depose_le": consigne.depose_le,
            "mode": consigne.mode,
            # ⚠️ Pas 106 : sous `sha256_du_bordereau`, et non `empreinte`. Le journal expurge
            # toute clé `empreinte` (le nom de l'empreinte d'un mot de passe) **avant**
            # l'écriture : l'empreinte du bordereau déposé, « ce qui permettra de se défendre »,
            # n'était donc jamais inscrite dans la trace chaînée. Trouvé au rapport mensuel.
            "sha256_du_bordereau": consigne.empreinte_deposee,
            "reserves": [a.code for a in dossier.recevabilite.reserves],
        },
    )
    return declaree, consigne


# ── Constater un dépôt qui n'a pas de dossier préparé ────────────────────────


#: Les obligations qui ont leur propre parcours de dépôt, contrôles compris. Les
#: constater ici contournerait ces contrôles.
PARCOURS_DEDIES = {"TVA": "/obligations/dossiers/{entreprise}/depot-tva"}


def constater_un_depot_hors_tva(
    obligation: ObligationInstance,
    type_obligation: TypeObligation,
    *,
    numero: str,
    depose_le: datetime,
    montant_constate: Decimal | None,
    piece_jointe: str | None,
    precision: str | None,
    portail: PortailDeclaratif,
    journal: JournalAudit,
    par: str,
    a_l_instant: datetime,
) -> tuple[ObligationInstance, AccuseReception]:
    """Consigne l'accusé d'une obligation déposée hors de la plateforme.

    ─────────────────────────────────────────────────────────────────────────────
    POURQUOI CE CAS D'USAGE EXISTE (pas 59)

    Depuis le pas 58, une obligation dont l'accusé existe est déclarée. Mais seule la
    TVA avait un moyen de consigner son accusé. La CNPS, les retenues sur salaires,
    l'IGS, la DSF et la patente restaient donc « en retard » pour toujours, relancées
    chaque jour, dès lors qu'elles étaient visibles.

    ⚠️ CE QUE CE CONSTAT NE PROUVE PAS

    Le dépôt de TVA confronte l'accusé à un bordereau préparé : même référence, même
    empreinte, donc les chiffres déposés sont ceux que la plateforme a calculés. Ici,
    **aucun bordereau n'existe**. Le document consigné est le constat lui-même,
    obligation, période, numéro, montant, et son empreinte prouve ce que le réviseur
    a déclaré, pas ce que l'administration a reçu. Sans pièce jointe, l'accusé le dit
    par `verifiable`.

    ⚠️ CE QUI EST REFUSÉ

    * la TVA, qui a son parcours et ses contrôles de recevabilité ;
    * une obligation déjà déclarée ;
    * un dépôt daté de l'avenir : on ne consigne pas ce qui n'a pas eu lieu ;
    * un dépôt antérieur à la fin de la période, sauf obligation payable d'avance :
      on ne déclare pas des cotisations de juin le 20 juin.

    Le guichet vient du catalogue, jamais de l'appelant.
    ─────────────────────────────────────────────────────────────────────────────
    """
    if obligation.code_obligation in PARCOURS_DEDIES:
        chemin = PARCOURS_DEDIES[obligation.code_obligation].format(
            entreprise=obligation.entreprise
        )
        raise DepotImpossible(
            f"{obligation.code_obligation} se dépose par son parcours dédié ({chemin}), "
            "qui contrôle la "
            "recevabilité avant de consigner l'accusé. Le constater ici contournerait "
            "ces contrôles."
        )
    if obligation.deposee:
        raise DepotImpossible(
            f"{obligation.code_obligation} du {obligation.periode_debut:%d/%m/%Y} au "
            f"{obligation.periode_fin:%d/%m/%Y} est déjà déclarée le "
            f"{obligation.declaree_le:%d/%m/%Y}, accusé {obligation.reference_depot}. "
            "Un second accusé ferait croire à une déclaration rectificative."
        )
    verifier_la_date_du_depot(obligation, depose_le, a_l_instant)

    constat = {
        "obligation": obligation.code_obligation,
        "entreprise": obligation.entreprise,
        "periode_debut": obligation.periode_debut,
        "periode_fin": obligation.periode_fin,
        "numero": numero,
        "depose_le": depose_le,
        "montant_constate": montant_constate,
    }
    document = DocumentATransmettre(
        portail=type_obligation.portail,
        code_document=obligation.code_obligation,
        entreprise=obligation.entreprise,
        periode_debut=obligation.periode_debut,
        periode_fin=obligation.periode_fin,
        format=FormatTransmission.SAISIE_MANUELLE,
        contenu=json.dumps(
            {"constat_de_depot": constat},
            sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str,
        ),
        montant_a_payer=montant_constate,
    )
    accuse = AccuseReception(
        numero=numero,
        portail=type_obligation.portail,
        reference_document=document.reference,
        depose_le=depose_le,
        mode=ModeDepot.MANUEL,
        empreinte_deposee=document.empreinte,
        depose_par=par,
        montant_constate=montant_constate,
        piece_jointe=piece_jointe,
        precision=precision,
    )
    consigne = portail.enregistrer_accuse(accuse, document=document)
    declaree = obligation.avancer(
        StatutObligation.DECLAREE,
        declaree_le=consigne.depose_le.date(),
        reference_depot=consigne.numero,
    )
    journal.ajouter(
        horodatage=a_l_instant,
        acteur=par,
        action="obligation.deposee",
        objet_type="obligation",
        objet_id=f"{obligation.entreprise}/{obligation.code_obligation}",
        apres={
            # Pas 94 : le dossier en clair, et non seulement dans `objet_id`. Une
            # notification désigne ses destinataires par un champ de l'entrée ; le
            # découper dans « NIU/CODE » ferait dépendre l'avis d'un format d'identifiant.
            "dossier": obligation.entreprise,
            "obligation": obligation.code_obligation,
            "accuse": consigne.numero,
            "depose_le": consigne.depose_le,
            "mode": consigne.mode,
            "portail": consigne.portail,
            # ⚠️ Pas 106 : sous `sha256_du_bordereau`, et non `empreinte`. Le journal expurge
            # toute clé `empreinte` (le nom de l'empreinte d'un mot de passe) **avant**
            # l'écriture : l'empreinte du bordereau déposé, « ce qui permettra de se défendre »,
            # n'était donc jamais inscrite dans la trace chaînée. Trouvé au rapport mensuel.
            "sha256_du_bordereau": consigne.empreinte_deposee,
            "sans_bordereau": True,
        },
    )
    return declaree, consigne
