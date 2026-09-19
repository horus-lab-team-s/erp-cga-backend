"""La soudure entre le contexte D · Conformité et le contexte E · Comptabilité.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE MODULE FAIT, ET POURQUOI IL EXISTE

C'est ici — et nulle part ailleurs — que la conséquence fiscale **décrite** par
une règle devient une conséquence **appliquée** sur une écriture.

Le principe d'architecture n° 3 du projet dit : « la règle décrit, un service en
aval applique ». Ce module est ce service. Sans lui, le moteur de conformité
produit des constats que personne ne consomme, et la chaîne de valeur du produit

    constat → attribut fiscal sur la ligne → TVA du mois → réintégration de la liasse

est rompue au deuxième maillon.

CE QU'IL NE FAIT PAS

Il ne juge rien. Il ne lit aucun taux, aucun seuil, aucune règle. Il reçoit un
`RapportConformite` déjà produit par le contexte D — lequel a résolu les
paramètres du référentiel à la date de l'opération — et se contente de traduire
ses conclusions en attributs portés par les lignes.

Il ne décide pas non plus **quel compte** reçoit une TVA devenue non récupérable.
C'est une décision d'imputation comptable, qui appartient au cabinet et qui reste
ouverte : voir le point de variation documenté plus bas.

LE SENS DE LA DÉPENDANCE

`comptabilite → conformite`, jamais l'inverse. La comptabilité ne demande jamais
un contrôle : elle reçoit un rapport. C'est ce que déclare le graphe de
`tests/test_architecture.py`, et c'est ce qui permet au moteur de conformité de
rester une fonction pure de la facture et du droit.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.contextes.comptabilite.domaine.entites import (
    AttributFiscal,
    EcritureComptable,
    Sens,
)
from app.contextes.conformite.api import RapportConformite, Severite

__all__ = [
    "ComptabilisationInterdite",
    "PREFIXE_TVA_RECUPERABLE",
    "SyntheseFiscale",
    "appliquer_rapport",
    "montant_tva_rejetee",
    "reperer_lignes",
    "synthetiser",
    "verifier_comptabilisation_autorisee",
]

#: Préfixe des comptes de TVA récupérable dans le plan OHADA.
#:
#: ⚠️ Ce n'est **pas** une valeur légale — c'est une convention du plan comptable,
#: qui est structurel et supranational. Elle a donc sa place dans le code, à la
#: différence d'un taux ou d'un seuil. Elle deviendra un attribut du plan importé
#: au référentiel le jour où le contexte A portera `PlanComptableReference`, et
#: cet argument disparaîtra.
PREFIXE_TVA_RECUPERABLE = "445"


class ComptabilisationInterdite(RuntimeError):
    """Une pièce portant une anomalie bloquante ne se comptabilise pas.

    C'est la traduction du flux décrit au § 2 de `10-flux-fonctionnels.md` :
    « une pièce bloquante refuse la comptabilisation — **D** décide, **E**
    applique ».

    Lever plutôt que rendre un booléen est délibéré : une comptabilisation qui
    passerait outre par distraction est exactement le genre d'erreur que le
    Centre ne peut pas se permettre, puisqu'il engage son agrément sur ce qu'il
    présente.
    """


class SyntheseFiscale(BaseModel):
    """Ce qu'un rapport de conformité impose à la comptabilité, en une seule vue.

    Un rapport peut porter plusieurs constats, chacun avec sa conséquence.
    L'écriture, elle, n'a besoin que de savoir trois choses : la TVA est-elle
    récupérable, la charge est-elle déductible, et pourquoi. Cette synthèse fait
    la réduction, une fois, plutôt que de la refaire à chaque ligne.
    """

    model_config = ConfigDict(frozen=True)

    reference_rapport: str
    tva_deductible: bool
    charge_deductible: bool

    #: Les messages des constats qui refusent quelque chose, concaténés. Destiné à
    #: être lu par un humain : le comptable qui reprend le dossier, ou le
    #: contrôleur trois ans plus tard.
    motif: str

    #: Les codes des règles à l'origine du refus. C'est le fil qui permet de
    #: remonter de la liasse jusqu'à la facture.
    codes_regles: list[str]

    poste_reintegration: str | None
    comptabilisation_interdite: bool

    @property
    def sans_consequence(self) -> bool:
        return self.tva_deductible and self.charge_deductible


def synthetiser(rapport: RapportConformite) -> SyntheseFiscale:
    """Réduit un rapport de conformité à ce qui concerne la comptabilité.

    Les constats purement qualitatifs — désignation imprécise, doublon possible —
    n'apparaissent pas ici : ils ne refusent aucune déduction, donc ils ne
    changent aucune écriture. Ils restent au dossier, dans le contexte D, où le
    réviseur les traite.
    """
    refusants = [
        constat
        for constat in rapport.constats
        if constat.consequence.rejette_tva or constat.consequence.rejette_charge
    ]
    postes = [
        constat.consequence.poste_reintegration
        for constat in refusants
        if constat.consequence.poste_reintegration
    ]
    return SyntheseFiscale(
        reference_rapport=rapport.reference_document,
        tva_deductible=rapport.tva_deductible,
        charge_deductible=rapport.charge_deductible,
        motif=" · ".join(constat.message.strip() for constat in refusants),
        codes_regles=[constat.code_regle for constat in refusants],
        poste_reintegration=postes[0] if postes else None,
        comptabilisation_interdite=rapport.comptabilisation_interdite,
    )


def verifier_comptabilisation_autorisee(rapport: RapportConformite) -> None:
    """Lève si la pièce porte une anomalie bloquante.

    À appeler **avant** de construire l'écriture, pas après : une écriture
    fabriquée puis jetée laisse des numéros de séquence consommés, donc des trous.
    """
    if rapport.comptabilisation_interdite:
        codes = ", ".join(
            constat.code_regle
            for constat in rapport.constats
            if constat.severite is Severite.BLOQUANT
        )
        raise ComptabilisationInterdite(
            f"pièce {rapport.reference_document} : anomalie bloquante ({codes}). "
            "La comptabilisation est refusée tant qu'elle subsiste. L'action attendue "
            "est la demande de facture rectificative, pas le contournement."
        )


def reperer_lignes(
    ecriture: EcritureComptable,
    *,
    prefixe_tva: str = PREFIXE_TVA_RECUPERABLE,
) -> tuple[list[int], list[int]]:
    """Classe les lignes d'une écriture d'achat : TVA récupérable, puis charges.

    Le repérage se fait sur le **numéro de compte**, c'est-à-dire sur la structure
    du plan OHADA, qui est stable et supranationale. Aucune connaissance fiscale
    n'est mobilisée ici.

    Rend deux listes d'indices : celles des lignes de TVA récupérable au débit, et
    celles des lignes de charge au débit. Les lignes de tiers et de trésorerie ne
    portent jamais d'attribut fiscal — la conséquence frappe la charge et la taxe,
    pas la dette.
    """
    tva: list[int] = []
    charges: list[int] = []
    for index, ligne in enumerate(ecriture.lignes):
        if ligne.sens is not Sens.DEBIT:
            continue
        if ligne.compte.startswith(prefixe_tva):
            tva.append(index)
        elif ligne.compte.startswith("6"):
            charges.append(index)
    return tva, charges


def appliquer_rapport(
    ecriture: EcritureComptable,
    rapport: RapportConformite,
    *,
    prefixe_tva: str = PREFIXE_TVA_RECUPERABLE,
) -> EcritureComptable:
    """Pose sur l'écriture les attributs fiscaux que le rapport impose.

    Rend une **nouvelle** écriture ; l'originale est gelée. Si le rapport ne
    refuse rien, l'écriture est rendue inchangée : un attribut neutre n'apporte
    aucune information et alourdirait la lecture du grand livre.

    ⚠️ POINT DE VARIATION LAISSÉ OUVERT — ET C'EST DÉLIBÉRÉ

    Quand la TVA est rejetée, elle cesse d'être une créance sur l'État et devient
    un coût. Deux traitements sont défendables, et le cabinet doit trancher :

    * l'incorporer au compte de charge d'origine, ce qui la noie dans l'achat ;
    * la porter sur un compte de charge dédié, ce qui la rend traçable et
      permet de la retrouver au tableau de passage.

    Ce module **ne tranche pas** : il marque la ligne de TVA comme non
    récupérable et laisse l'imputation à une stratégie séparée. Décider ici
    figerait dans le code une décision qui appartient au métier — et qui commande
    par ailleurs une question fiscale encore ouverte : la TVA non récupérable
    devenue charge est-elle elle-même déductible du résultat ?
    """
    synthese = synthetiser(rapport)
    if synthese.sans_consequence:
        return ecriture

    lignes_tva, lignes_charge = reperer_lignes(ecriture, prefixe_tva=prefixe_tva)
    resultat = ecriture

    if not synthese.tva_deductible:
        attribut = AttributFiscal(
            tva_deductible=False,
            motif_non_deductibilite=synthese.motif,
            poste_reintegration=synthese.poste_reintegration,
            code_regle_origine=synthese.codes_regles[0],
            reference_rapport=synthese.reference_rapport,
        )
        for index in lignes_tva:
            resultat = resultat.avec_attribut_fiscal(index, attribut)

    if not synthese.charge_deductible:
        for index in lignes_charge:
            attribut = AttributFiscal(
                charge_deductible=False,
                motif_non_deductibilite=synthese.motif,
                # Le montant à réintégrer est celui de la ligne elle-même : c'est
                # la charge refusée qui remonte au tableau de passage, pas le
                # total de la facture.
                montant_a_reintegrer=resultat.lignes[index].montant,
                poste_reintegration=synthese.poste_reintegration,
                code_regle_origine=synthese.codes_regles[0],
                reference_rapport=synthese.reference_rapport,
            )
            resultat = resultat.avec_attribut_fiscal(index, attribut)

    return resultat


def montant_tva_rejetee(ecriture: EcritureComptable) -> Decimal:
    """Ce que cette écriture retire de la TVA déductible du mois.

    C'est la valeur que le contexte F · Obligations viendra chercher pour établir
    la ligne « TVA rejetée par le contrôle de conformité » de la déclaration
    mensuelle.
    """
    return sum(
        (
            ligne.montant
            for ligne in ecriture.lignes
            if ligne.attribut_fiscal is not None and ligne.attribut_fiscal.rejette_tva
        ),
        Decimal(0),
    )
