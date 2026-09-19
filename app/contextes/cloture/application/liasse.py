"""L'assemblage de la liasse à partir de la balance définitive.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE MODULE FAIT, ET CE QU'IL REFUSE DE FAIRE

Il **ventile** les soldes de la balance dans les postes de la liasse, calcule le
résultat, et vérifie la cohérence des états. Il ne corrige rien : une balance
déséquilibrée produit une liasse déséquilibrée **et un contrôle en échec**, pas un
ajustement silencieux.

C'est le point qui sépare un outil comptable défendable d'un outil qui « marche » :
la tentation d'équilibrer d'office par un poste d'écart est grande, et elle
transforme une erreur visible en erreur invisible.

LE RÉSULTAT SE CALCULE DEUX FOIS, ET C'EST VOULU

Une fois par le compte de résultat (produits − charges), une fois par le bilan
(actif − passif hors résultat). Les deux doivent coïncider. C'est le contrôle
inter-états le plus important de SYSCOHADA, et il n'a de valeur que parce que les
deux chemins sont indépendants : les faire dériver l'un de l'autre rendrait le
contrôle toujours satisfait et parfaitement inutile.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.contextes.cloture.domaine.entites import (
    ControleCoherence,
    LigneLiasse,
    SensPoste,
    SystemeDsf,
)
from app.contextes.cloture.domaine.postes import poste_du_compte, postes_du_systeme
from app.contextes.comptabilite.api import SoldeCompte
from app.contextes.referentiel.api import (
    AucuneVersionApplicable,
    ParametreInconnu,
    ServiceParametres,
    StatutValidation,
)

__all__ = [
    "EtatsFinanciers",
    "assembler_les_etats",
    "determiner_le_systeme",
]


@dataclass(frozen=True)
class EtatsFinanciers:
    """Le bilan et le compte de résultat, ventilés et contrôlés."""

    systeme: SystemeDsf
    lignes: tuple[LigneLiasse, ...]
    #: Les comptes que le plan de correspondance ne couvre pas.
    #:
    #: ⚠️ Jamais rangés dans un poste « divers ». Le fourre-tout équilibre le
    #: bilan en dissimulant exactement ce qu'il faudrait voir.
    comptes_non_couverts: tuple[str, ...]
    controles: tuple[ControleCoherence, ...]
    #: Vrai si le classement en Système Normal ou Minimal repose sur un seuil
    #: non encore validé.
    systeme_non_valide: bool = False

    def _somme(self, sens: SensPoste) -> Decimal:
        return sum((ligne.montant for ligne in self.lignes if ligne.sens is sens), Decimal(0))

    @property
    def total_actif(self) -> Decimal:
        return self._somme(SensPoste.ACTIF)

    @property
    def total_passif(self) -> Decimal:
        return self._somme(SensPoste.PASSIF)

    @property
    def total_charges(self) -> Decimal:
        return self._somme(SensPoste.CHARGE)

    @property
    def total_produits(self) -> Decimal:
        return self._somme(SensPoste.PRODUIT)

    @property
    def resultat_comptable(self) -> Decimal:
        """Produits moins charges — le chemin du compte de résultat."""
        return self.total_produits - self.total_charges

    @property
    def resultat_par_le_bilan(self) -> Decimal:
        """Actif moins passif — l'autre chemin, indépendant du premier.

        Le résultat de l'exercice n'est pas encore affecté aux capitaux propres à
        la date de clôture : il est donc l'écart entre l'actif et le passif tel
        qu'il ressort de la balance.
        """
        return self.total_actif - self.total_passif

    @property
    def coherent(self) -> bool:
        return all(c.satisfait for c in self.controles)


def determiner_le_systeme(
    chiffre_affaires: Decimal, parametres: ServiceParametres, a_la_date
) -> tuple[SystemeDsf, bool]:
    """Le système de présentation, constaté et jamais choisi.

    Rend le système **et** un drapeau disant si le seuil employé est validé.

    ⚠️ Le seuil du référentiel est unique là où le texte OHADA en module trois
    selon l'activité — négoce, artisanat, services. La valeur retenue est la plus
    élevée des trois, ce qui classe **en Système Normal** une entité de services
    proche du seuil que le texte admettrait au SMT. Le sur-classement est le sens
    d'erreur le moins dommageable — une liasse plus détaillée que nécessaire est
    acceptée, l'inverse est rejeté — mais il est signalé plutôt que subi.

    Un seuil absent du référentiel rend le Système Normal : c'est le régime de
    droit commun, et le retenir par défaut ne fait courir aucun risque de rejet.
    """
    try:
        resolu = parametres.resoudre("SEUIL_SYSTEME_NORMAL", a_la_date)
    except (ParametreInconnu, AucuneVersionApplicable):
        return SystemeDsf.NORMAL, True
    # ⚠️ La borne du référentiel, et non un `>=` écrit ici : le texte réserve le
    # système minimal à qui « reste sous » le seuil, et c'est le référentiel qui le
    # dit. Un seuil sans borne lève plutôt que de classer au hasard.
    systeme = SystemeDsf.NORMAL if resolu.atteint(chiffre_affaires) else SystemeDsf.MINIMAL
    return systeme, resolu.statut is not StatutValidation.VALIDE


def assembler_les_etats(
    soldes: list[SoldeCompte],
    systeme: SystemeDsf,
    *,
    systeme_non_valide: bool = False,
) -> EtatsFinanciers:
    """Ventile la balance dans les postes, puis contrôle les états.

    ─────────────────────────────────────────────────────────────────────────
    LES MONTANTS SONT PORTÉS EN VALEUR ABSOLUE, LE SENS ÉTANT DANS LE POSTE

    Un actif négatif n'a pas de sens à la lecture d'un bilan : un compte
    fournisseur créditeur n'est pas « un actif de −2 millions », c'est « une
    dette de 2 millions ». Le poste porte le côté, la ligne porte le montant.

    L'exception est voulue et documentée : les amortissements (AZ) sont un poste
    d'actif à solde créditeur, et leur montant reste **négatif** parce qu'ils
    viennent en diminution de l'actif brut. Les mettre en valeur absolue
    doublerait l'actif immobilisé.
    ─────────────────────────────────────────────────────────────────────────
    """
    par_poste: dict[str, list[tuple[str, Decimal]]] = {}
    non_couverts: list[str] = []

    for solde in soldes:
        montant = solde.solde
        if montant == 0:
            # Un compte soldé n'alimente aucun poste. L'inclure ferait apparaître
            # des lignes à zéro qui n'apprennent rien et allongent l'état.
            continue
        poste = poste_du_compte(solde.compte, systeme, solde_debiteur=montant > 0)
        if poste is None:
            non_couverts.append(solde.compte)
            continue
        par_poste.setdefault(poste.code, []).append((solde.compte, montant))

    ordre = {p.code: rang for rang, p in enumerate(postes_du_systeme(systeme))}
    definitions = {p.code: p for p in postes_du_systeme(systeme)}

    lignes: list[LigneLiasse] = []
    for code, contributions in sorted(par_poste.items(), key=lambda e: ordre[e[0]]):
        poste = definitions[code]
        brut = sum((m for _, m in contributions), Decimal(0))
        lignes.append(
            LigneLiasse(
                poste=code,
                libelle=poste.libelle,
                sens=poste.sens,
                montant=_presenter(code, brut),
                comptes=tuple(sorted(c for c, _ in contributions)),
            )
        )

    etats = EtatsFinanciers(
        systeme=systeme,
        lignes=tuple(lignes),
        comptes_non_couverts=tuple(sorted(set(non_couverts))),
        controles=(),
        systeme_non_valide=systeme_non_valide,
    )
    return EtatsFinanciers(
        systeme=etats.systeme,
        lignes=etats.lignes,
        comptes_non_couverts=etats.comptes_non_couverts,
        controles=_controler(etats, soldes),
        systeme_non_valide=systeme_non_valide,
    )


#: Le seul poste dont le montant reste signé — voir `assembler_les_etats`.
_POSTE_SOUSTRACTIF = "AZ"


def _presenter(code: str, brut: Decimal) -> Decimal:
    if code == _POSTE_SOUSTRACTIF:
        # Solde créditeur d'un compte 28x : négatif, et il doit le rester.
        return brut
    return abs(brut)


def _controler(etats: EtatsFinanciers, soldes: list[SoldeCompte]) -> tuple[ControleCoherence, ...]:
    """Les contrôles inter-états, dans l'ordre où un comptable les fait.

    Le premier est celui de la balance elle-même : si elle ne tient pas, tous les
    autres sont sans objet, et le dire évite de chercher l'erreur dans la liasse
    alors qu'elle est en amont.
    """
    controles: list[ControleCoherence] = []

    debits = sum((s.total_debit for s in soldes), Decimal(0))
    credits = sum((s.total_credit for s in soldes), Decimal(0))
    ecart_balance = debits - credits
    controles.append(
        ControleCoherence(
            code="BALANCE_EQUILIBREE",
            libelle="La balance est équilibrée",
            satisfait=ecart_balance == 0,
            ecart=abs(ecart_balance),
            explication=None
            if ecart_balance == 0
            else (
                f"Débits {debits} ≠ crédits {credits}. L'écart vient de la saisie, "
                "pas de la liasse : les contrôles suivants sont sans objet tant "
                "qu'il n'est pas résorbé."
            ),
        )
    )

    ecart_resultat = etats.resultat_comptable - etats.resultat_par_le_bilan
    controles.append(
        ControleCoherence(
            code="RESULTAT_CONCORDANT",
            libelle="Le résultat du compte de résultat égale celui du bilan",
            satisfait=ecart_resultat == 0,
            ecart=abs(ecart_resultat),
            explication=None
            if ecart_resultat == 0
            else (
                f"Compte de résultat : {etats.resultat_comptable}. "
                f"Bilan : {etats.resultat_par_le_bilan}. Un écart signale "
                "généralement un compte non couvert par le plan de correspondance."
            ),
        )
    )

    controles.append(
        ControleCoherence(
            code="TOUS_COMPTES_VENTILES",
            libelle="Tous les comptes mouvementés sont ventilés",
            satisfait=not etats.comptes_non_couverts,
            ecart=Decimal(len(etats.comptes_non_couverts)),
            explication=None
            if not etats.comptes_non_couverts
            else (
                f"{len(etats.comptes_non_couverts)} compte(s) sans poste : "
                f"{', '.join(etats.comptes_non_couverts)}. Leur montant ne figure "
                "nulle part dans la liasse — c'est la cause la plus fréquente d'un "
                "résultat discordant."
            ),
        )
    )

    return tuple(controles)
