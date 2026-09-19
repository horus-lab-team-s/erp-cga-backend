"""Un extrait du plan de comptes SYSCOHADA révisé, et les journaux du cabinet.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE MODULE EST, ET CE QU'IL N'EST PAS

Ce n'est **pas** le plan comptable OHADA. C'est l'extrait dont la plateforme a
besoin aujourd'hui pour imputer un flux d'achats, tenir une balance et boucler une
déclaration de TVA — quelques dizaines de comptes sur les plusieurs centaines que
compte le plan complet.

Le plan complet appartient au contexte A · Référentiel, qui le versionnera au même
titre qu'un paramètre légal : l'importer est une tâche ouverte, et le port
`DepotPlanComptable` existe précisément pour que ce jour-là rien d'autre ne bouge.

⚠️ **La nomenclature OHADA n'est pas une valeur légale camerounaise.** Elle est
supranationale et ne change pas à la loi de finances : elle peut donc porter des
valeurs par défaut dans le code, à la différence d'un taux ou d'un seuil. C'est la
même distinction que celle du `PlanImputation`.

⚠️ Les intitulés sont repris d'usage courant et **n'ont pas été confrontés au texte
de l'Acte uniforme**. Ils sont indicatifs tant que le plan officiel n'est pas
importé.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from app.contextes.comptabilite.domaine.entites import Compte, Journal, NatureJournal

__all__ = ["COMPTES_SYSCOHADA", "JOURNAUX_CABINET", "DepotJournauxMemoire", "DepotPlanMemoire"]


def _compte(
    numero: str, intitule: str, *, lettrable: bool = False, rapprochable: bool = False
) -> Compte:
    return Compte(
        numero=numero, intitule=intitule, lettrable=lettrable, rapprochable=rapprochable
    )


#: Les comptes employés par les imputations et les projections actuelles.
COMPTES_SYSCOHADA: list[Compte] = [
    # ── Classe 1 · Ressources durables ──────────────────────────────────────
    _compte("101", "Capital social"),
    _compte("11", "Réserves"),
    _compte("12", "Report à nouveau"),
    _compte("13", "Résultat net de l'exercice"),
    _compte("16", "Emprunts et dettes assimilées"),
    # ── Classe 2 · Actif immobilisé ─────────────────────────────────────────
    _compte("21", "Immobilisations incorporelles"),
    _compte("22", "Terrains"),
    _compte("23", "Bâtiments, installations techniques et agencements"),
    _compte("24", "Matériel, mobilier et actifs biologiques"),
    _compte("28", "Amortissements"),
    # ── Classe 3 · Stocks ───────────────────────────────────────────────────
    _compte("31", "Marchandises"),
    _compte("32", "Matières premières et fournitures liées"),
    _compte("33", "Autres approvisionnements"),
    # ── Classe 4 · Tiers ────────────────────────────────────────────────────
    _compte("401", "Fournisseurs, dettes en compte", lettrable=True),
    _compte("408", "Fournisseurs, factures non parvenues", lettrable=True),
    _compte("411", "Clients", lettrable=True),
    _compte("418", "Clients, produits à recevoir", lettrable=True),
    _compte("421", "Personnel, avances et acomptes", lettrable=True),
    _compte("422", "Personnel, rémunérations dues", lettrable=True),
    _compte("431", "Sécurité sociale (CNPS)", lettrable=True),
    _compte("4431", "État, TVA facturée sur ventes"),
    _compte("4432", "État, TVA facturée sur prestations de services"),
    _compte("4441", "État, IRPP retenu à la source"),
    _compte("4451", "État, TVA récupérable sur achats"),
    _compte("4452", "État, TVA récupérable sur immobilisations"),
    _compte("4454", "État, TVA récupérable sur services extérieurs"),
    _compte("447", "État, impôts retenus à la source"),
    _compte("449", "État, créances et dettes diverses"),
    # ── Classe 5 · Trésorerie ───────────────────────────────────────────────
    _compte("521", "Banques locales", rapprochable=True),
    _compte("523", "Établissements financiers et assimilés", rapprochable=True),
    # Le Mobile Money est un moyen de paiement de premier plan au Cameroun, et il
    # se rapproche exactement comme un compte bancaire : contre un relevé émis par
    # un tiers indépendant. Le traiter comme de la caisse ferait perdre ce contrôle.
    _compte("5231", "Comptes de monnaie électronique (Mobile Money)", rapprochable=True),
    _compte("571", "Caisse siège social"),
    # ── Classe 6 · Charges ──────────────────────────────────────────────────
    _compte("601", "Achats de marchandises"),
    _compte("602", "Achats de matières premières et fournitures liées"),
    _compte("604", "Achats stockés de matières et fournitures consommables"),
    _compte("605", "Autres achats (eau, électricité, carburants)"),
    _compte("608", "Achats d'emballages"),
    _compte("612", "Transports sur achats"),
    _compte("613", "Transports pour le compte de tiers"),
    _compte("622", "Locations et charges locatives"),
    _compte("624", "Entretien, réparations et maintenance"),
    _compte("625", "Primes d'assurance"),
    _compte("626", "Études, recherches et documentation"),
    _compte("627", "Publicité, publications, relations publiques"),
    _compte("628", "Frais de télécommunications"),
    _compte("631", "Frais bancaires"),
    _compte("632", "Rémunérations d'intermédiaires et de conseils"),
    _compte("638", "Autres charges externes"),
    _compte("641", "Impôts et taxes directs"),
    _compte("646", "Droits d'enregistrement"),
    _compte("647", "Pénalités et amendes fiscales"),
    _compte("661", "Rémunérations directes versées au personnel national"),
    _compte("664", "Charges sociales"),
    _compte("681", "Dotations aux amortissements d'exploitation"),
    # ── Classe 7 · Produits ─────────────────────────────────────────────────
    _compte("701", "Ventes de marchandises"),
    _compte("702", "Ventes de produits finis"),
    _compte("706", "Services vendus"),
    _compte("707", "Produits accessoires"),
    _compte("771", "Intérêts de prêts"),
    _compte("781", "Transferts de charges d'exploitation"),
    # ── Classe 8 · Autres charges et produits ───────────────────────────────
    _compte("851", "Dotations aux provisions réglementées"),
    _compte("871", "Reprises de provisions réglementées"),
    _compte("891", "Impôts sur le résultat"),
]


#: Les journaux ouverts par défaut sur un dossier. Le plan de classement usuel :
#: on sépare les journaux parce que plusieurs personnes saisissent en parallèle,
#: et surtout parce que le journal de banque se pointe ligne à ligne contre le
#: relevé de l'établissement.
JOURNAUX_CABINET: list[Journal] = [
    Journal(code="AC", intitule="Journal des achats", nature=NatureJournal.ACHATS),
    Journal(code="VE", intitule="Journal des ventes", nature=NatureJournal.VENTES),
    Journal(
        code="BQ",
        intitule="Journal de banque",
        nature=NatureJournal.BANQUE,
        compte_contrepartie="521",
    ),
    Journal(
        code="MM",
        intitule="Journal Mobile Money",
        nature=NatureJournal.BANQUE,
        compte_contrepartie="5231",
    ),
    Journal(
        code="CA",
        intitule="Journal de caisse",
        nature=NatureJournal.CAISSE,
        compte_contrepartie="571",
    ),
    Journal(
        code="OD",
        intitule="Journal des opérations diverses",
        nature=NatureJournal.OPERATIONS_DIVERSES,
    ),
    # ⚠️ **LE JOURNAL DES À-NOUVEAUX EST SÉPARÉ, ET CE N'EST PAS DU RANGEMENT.**
    #
    # L'écriture de reprise des soldes n'est pas une opération de l'entreprise :
    # aucune facture, aucun règlement, aucun tiers ne lui correspond. La mêler aux
    # opérations diverses la rendrait indiscernable d'une régularisation saisie à
    # la main, alors qu'un vérificateur la cherche en premier — c'est elle qui
    # relie deux exercices, et c'est par elle qu'on fait entrer un solde inventé.
    #
    # Séparée, elle se lit d'un coup d'œil : un journal des à-nouveaux doit porter
    # **une** écriture par exercice, et toute autre chose y est suspecte.
    Journal(
        code="AN",
        intitule="Journal des à-nouveaux",
        nature=NatureJournal.OPERATIONS_DIVERSES,
    ),
]


class DepotPlanMemoire:
    """Réalisation du port `DepotPlanComptable`.

    Le chargement est global et non paginé, comme le port le prévoit : un plan
    compte quelques centaines d'entrées et il est lu à chaque imputation.
    """

    def __init__(self, comptes: list[Compte] | None = None) -> None:
        self._comptes = list(comptes) if comptes is not None else list(COMPTES_SYSCOHADA)

    def charger(self) -> list[Compte]:
        return list(self._comptes)

    def ajouter(self, compte: Compte) -> None:
        """Ouvre un sous-compte propre à un dossier.

        Le contrôle qui compte — un compte dérivé porte son compte de référence,
        et son numéro en est un préfixe — est appliqué par l'entité `Compte`
        elle-même. Sans lui, chaque sous-compte créé ferait disparaître son solde
        de la liasse, silencieusement.
        """
        self._comptes.append(compte)


class DepotJournauxMemoire:
    """Réalisation du port `DepotJournaux`."""

    def __init__(self, journaux: list[Journal] | None = None) -> None:
        self._journaux = list(journaux) if journaux is not None else list(JOURNAUX_CABINET)

    def charger(self) -> list[Journal]:
        return list(self._journaux)
