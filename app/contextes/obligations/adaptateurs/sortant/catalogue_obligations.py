"""Le catalogue des obligations déclaratives, et ce qu'il faut savoir avant de s'en servir.

─────────────────────────────────────────────────────────────────────────────────
⚠️ AUCUNE DE CES ÉCHÉANCES N'A ÉTÉ VALIDÉE SUR LE CODE GÉNÉRAL DES IMPÔTS

Les jours limites proviennent du référentiel, qui porte lui-même le statut
`A_VALIDER`. Ce catalogue ne fait que les assembler en obligations : il n'ajoute
aucune autorité à des valeurs qui n'en ont pas encore.

Trois inconnues sont matérialisées plutôt que masquées :

* **L'échelonnement par centre** — `decalage_par_centre` est vide. La note du
  paramètre `DSF_DELAI_JOURS_APRES_CLOTURE` réclame « un paramètre par centre, pas
  une constante » ; le champ existe, les valeurs manquent. Un dictionnaire vide
  produit une échéance identique pour tous les centres, ce qui est faux mais
  **visible** — une valeur inventée serait fausse et invisible.

* **Le délai de la DSF** — 75 jours au référentiel, mais 31 décembre + 75 jours
  donne le 16 mars alors que la note annonce le 15. Soit la note contient une
  coquille, soit le décompte commence le lendemain de la clôture, soit le 15 mars
  est une **date civile fixe** et la formule ne vaut que pour les exercices
  décalés. La troisième hypothèse changerait la modélisation.

* **Le guichet des retenues sur salaires** — porté au catalogue depuis le pas 59.
  La CNPS se dépose au guichet CNPS ; les retenues IRPP sont rattachées à la DGI
  en attendant la réponse à la question Q20, le DIPE les réunissant.

* **Les seuils de périodicité** — une entreprise sous un certain chiffre d'affaires
  déclare-t-elle la TVA au trimestre plutôt qu'au mois ? Le catalogue ne le sait
  pas et n'invente rien : toutes les déclarations de TVA sont mensuelles.

CE QUI EST CERTAIN, EN REVANCHE

La déclaration de TVA est due **même sans opération**. C'est structurel et non
conjoncturel : l'obligation naît de l'assujettissement, pas de l'activité. Presque
aucun dirigeant ne le sait, et c'est une source de pénalités absurdes — on est
sanctionné pour n'avoir pas déclaré qu'on n'avait rien à déclarer.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date

from app.contextes.obligations.domaine.echeances import Periodicite, TypeObligation
from app.contextes.portefeuille.contrats import RegimeFiscal
from app.contextes.transverse.contrats import Portail

__all__ = ["CATALOGUE_OBLIGATIONS", "DepotTypesObligationMemoire"]


CATALOGUE_OBLIGATIONS: list[TypeObligation] = [
    TypeObligation(
        code="TVA",
        portail=Portail.DGI_TELEDECLARATION,
        libelle="Déclaration de taxe sur la valeur ajoutée",
        periodicite=Periodicite.MENSUELLE,
        jour_limite=15,
        regimes_concernes=[RegimeFiscal.REEL],
        exige_assujettissement_tva=True,
        # La ligne la plus importante du catalogue — voir l'en-tête.
        declaration_neant_due=True,
    ),
    TypeObligation(
        code="IRPP_ACOMPTE",
        portail=Portail.DGI_TELEDECLARATION,
        libelle="Acompte mensuel d'impôt sur le revenu",
        periodicite=Periodicite.MENSUELLE,
        jour_limite=15,
        regimes_concernes=[RegimeFiscal.REEL],
    ),
    TypeObligation(
        code="IRPP_SALAIRES",
        # ⚠️ Guichet à confirmer (question ouverte Q20) : les retenues sur salaires et
        # les cotisations se déclarent ensemble sur le DIPE. Une donnée, pas une règle.
        portail=Portail.DGI_TELEDECLARATION,
        libelle="Retenues sur salaires — IRPP et taxes assises",
        periodicite=Periodicite.MENSUELLE,
        jour_limite=15,
        exige_salaries=True,
    ),
    TypeObligation(
        code="CNPS",
        portail=Portail.CNPS_DIPE,
        libelle="Cotisations sociales CNPS",
        periodicite=Periodicite.MENSUELLE,
        jour_limite=15,
        exige_salaries=True,
        # ⚠️ Le piège : l'impôt libératoire n'efface pas les cotisations sociales.
        # Un adhérent au synthétique croit souvent être quitte de tout, et se
        # découvre débiteur de plusieurs années de CNPS. L'obligation ne dépend
        # donc pas du régime, mais de la présence de salariés.
        regimes_concernes=None,
    ),
    TypeObligation(
        code="IGS",
        portail=Portail.DGI_TELEDECLARATION,
        libelle="Impôt général synthétique — versement trimestriel",
        periodicite=Periodicite.TRIMESTRIELLE,
        jour_limite=15,
        regimes_concernes=[RegimeFiscal.IGS],
    ),
    TypeObligation(
        code="DSF",
        portail=Portail.DGI_TELEDECLARATION,
        libelle="Déclaration statistique et fiscale",
        periodicite=Periodicite.ANNUELLE_EXERCICE,
        delai_jours_apres_cloture=75,
        # Vide, et c'est délibéré — voir l'en-tête du module.
        decalage_par_centre={},
    ),
    TypeObligation(
        code="PATENTE",
        portail=Portail.DGI_TELEDECLARATION,
        libelle="Contribution des patentes",
        periodicite=Periodicite.ANNUELLE_CIVILE,
        jour_civil=28,
        mois_civil=2,
        # Due en début d'année pour l'année en cours : c'est un droit d'exercer
        # payé d'avance, pas la déclaration d'une période écoulée.
        payable_d_avance=True,
    ),
]


class DepotTypesObligationMemoire:
    """Réalisation du port `DepotTypesObligation`.

    ⚠️ La date est acceptée mais **ignorée** : le catalogue n'est pas encore
    versionné. Une loi de finances peut créer une obligation, en supprimer une, ou
    changer un jour limite, et un échéancier régénéré pour 2024 devrait employer
    le catalogue de 2024. Le port porte donc déjà la date ; c'est cette réalisation
    qui ne sait pas encore s'en servir, et non l'interface qui l'aurait oubliée.
    """

    def __init__(self, types: list[TypeObligation] | None = None) -> None:
        self._types = list(types) if types is not None else list(CATALOGUE_OBLIGATIONS)

    def charger(self, a_la_date: date) -> list[TypeObligation]:  # noqa: ARG002
        return list(self._types)
