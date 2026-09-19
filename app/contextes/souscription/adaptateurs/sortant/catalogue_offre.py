"""Le catalogue de l'offre du cabinet, et son barème daté.

─────────────────────────────────────────────────────────────────────────────────
D'OÙ VIENNENT CES MONTANTS

De la vitrine, qui les portait jusqu'ici dans `messages/fr/vitrine.json` et
`messages/fr/pages.json` — c'est-à-dire **dans les fichiers de traduction**.

Un tarif rangé dans un fichier de traduction ne se change pas sans redéploiement,
n'a aucune date d'effet, et vit en double dès qu'une seconde langue existe : la
version anglaise et la version française auraient chacune leur prix, et rien ne
dirait lequel fait foi. Il est ici, daté, et la vitrine le lira.

DEUX BARÈMES HISTORIQUES SONT CONSERVÉS

L'adhésion au tarif de 2024 et celui de 2026 figurent tous deux. Ce n'est pas du
décor : c'est ce qui permet de vérifier qu'un devis de 2025 se relit au prix de
2025, et le test s'appuie dessus. Un barème daté dont on n'aurait jamais qu'une
version ne prouverait rien.

⚠️ Les montants antérieurs à 2026 sont **reconstitués** : le cabinet n'a pas
fourni son historique tarifaire. Ils servent à démontrer le mécanisme, pas à
facturer quoi que ce soit.

CE QUI N'EST PAS ICI, ET POURQUOI

Les **frais officiels** d'une création d'entreprise — caisse du guichet unique,
droits d'enregistrement, RCCM, journal officiel, timbres, taux proportionnel sur
le capital. Ce sont des valeurs légales, elles relèvent du contexte A, et le
principe n° 1 du projet interdit de les écrire ailleurs.

Elles ne sont pas non plus au référentiel, et il y a une seconde raison de ne pas
les y verser en l'état : `bareme-creation.ts` signale lui-même que les montants
de la maquette, agrégés en trois lignes, **divergent de la proforma réelle du
cabinet**, qui en compte huit pour le même total. Verser des chiffres qu'on sait
approximatifs dans un référentiel dont la raison d'être est l'exactitude serait
pire que de ne rien y verser.

La création reste donc `SUR_ETUDE`. Elle produit une demande de devis, pas un
encaissement — ce qui est déjà ce que la vitrine annonce.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.contextes.souscription.domaine.offre import (
    Formule,
    NatureService,
    Periodicite,
    Service,
    Tarif,
)

__all__ = ["CATALOGUE", "DepotServicesMemoire"]

_DEPUIS_2026 = date(2026, 1, 1)
_DEPUIS_2024 = date(2024, 1, 1)


CATALOGUE: list[Service] = [
    Service(
        code="ADHESION",
        libelle="Suivi comptable et fiscal — adhésion au centre agréé",
        nature=NatureService.ABONNEMENT,
        periodicite=Periodicite.MENSUELLE,
        unite="FCFA par mois",
        resume=(
            "Tenue des comptes, déclarations, contrôle des pièces et adhésion au "
            "centre de gestion agréé."
        ),
        # Le seul service qui ouvre un accès au dossier : c'est celui qui crée la
        # relation de suivi. Une domiciliation ou une formation payées n'ont
        # aucune raison de donner accès à une comptabilité.
        ouvre_un_dossier=True,
        formules=[
            Formule(
                code="LIBERATOIRE",
                libelle="Impôt libératoire",
                plancher=Decimal(0),
                plafond=Decimal(50_000_000),
                inclus=[
                    "Comptabilité simplifiée",
                    "Déclaration trimestrielle",
                    "Contrôle de vos pièces",
                    "Relances par WhatsApp",
                    "Un interlocuteur nommé",
                ],
                tarifs=[
                    Tarif(
                        montant=Decimal(10_000),
                        du=_DEPUIS_2024,
                        au=_DEPUIS_2026,
                        motif="⚠️ Tarif reconstitué — voir l'en-tête du module",
                    ),
                    Tarif(montant=Decimal(12_500), du=_DEPUIS_2026),
                ],
            ),
            Formule(
                code="REEL_SIMPLIFIE",
                libelle="Réel simplifié",
                plancher=Decimal(50_000_000),
                plafond=Decimal(100_000_000),
                inclus=[
                    "Comptabilité SYSCOHADA complète",
                    "Déclarations mensuelles de TVA",
                    "Déclaration annuelle DSF",
                    "Rapprochement bancaire mensuel",
                    "Assistance en cas de contrôle",
                    "Un comptable et un réviseur",
                ],
                tarifs=[
                    Tarif(
                        montant=Decimal(30_000),
                        du=_DEPUIS_2024,
                        au=_DEPUIS_2026,
                        motif="⚠️ Tarif reconstitué — voir l'en-tête du module",
                    ),
                    Tarif(montant=Decimal(35_000), du=_DEPUIS_2026),
                ],
            ),
            Formule(
                code="REEL",
                libelle="Régime du réel",
                plancher=Decimal(100_000_000),
                plafond=None,
                inclus=[
                    "Comptabilité complète et analytique",
                    "Toutes déclarations fiscales et sociales",
                    "Liasse fiscale et clôture",
                    "Tableau de bord mensuel",
                    "Accompagnement bancaire",
                    "Hors régime du centre agréé",
                ],
                # Sans montant, et ce n'est pas un oubli : au-delà de cent millions
                # l'entreprise sort du régime du centre agréé, la prestation change
                # de nature, et un prix affiché serait un prix faux.
                tarifs=[Tarif(montant=None, du=_DEPUIS_2024)],
            ),
        ],
    ),
    Service(
        code="DOMICILIATION",
        libelle="Domiciliation commerciale",
        nature=NatureService.ANNUEL,
        periodicite=Periodicite.ANNUELLE,
        unite="FCFA pour douze mois",
        resume="Adresse professionnelle, réception et réexpédition du courrier.",
        tarifs=[
            Tarif(
                montant=Decimal(160_000),
                du=_DEPUIS_2024,
                au=_DEPUIS_2026,
                motif="⚠️ Tarif reconstitué — voir l'en-tête du module",
            ),
            Tarif(montant=Decimal(180_000), du=_DEPUIS_2026),
        ],
    ),
    Service(
        code="FORMATION",
        libelle="Formation — session interentreprises",
        nature=NatureService.PONCTUEL,
        periodicite=Periodicite.UNIQUE,
        unite="FCFA par personne",
        resume="Une journée, en présentiel, sur un thème fiscal ou comptable.",
        tarifs=[Tarif(montant=Decimal(75_000), du=_DEPUIS_2024)],
    ),
    Service(
        code="CREATION",
        libelle="Création d'entreprise",
        nature=NatureService.SUR_ETUDE,
        periodicite=Periodicite.UNIQUE,
        resume=(
            "Statuts, immatriculation, RCCM, NIU. Le montant dépend de la forme "
            "juridique, du capital et du greffe compétent."
        ),
        # Voir l'en-tête : sans montant tant que les frais officiels ne sont pas
        # au référentiel normatif.
        tarifs=[Tarif(montant=None, du=_DEPUIS_2024)],
    ),
    Service(
        code="PONCTUEL",
        libelle="Prestation ponctuelle",
        nature=NatureService.SUR_ETUDE,
        periodicite=Periodicite.UNIQUE,
        resume=(
            "Une mission délimitée : rattrapage de comptabilité, régularisation, "
            "assistance à un contrôle. Chiffrée après examen."
        ),
        tarifs=[Tarif(montant=None, du=_DEPUIS_2024)],
    ),
]


class DepotServicesMemoire:
    """Réalisation de `DepotServices`.

    Le catalogue est en dur ici, comme le plan SYSCOHADA l'est dans le contexte E.
    La cible est une table éditable par la direction, avec un écran : un tarif que
    le cabinet ne peut pas changer lui-même est un tarif qu'il ne changera pas, et
    il finira par facturer autre chose que ce que le système annonce.
    """

    def __init__(self, services: list[Service] | None = None) -> None:
        self._services = list(services) if services is not None else list(CATALOGUE)

    def charger(self, a_la_date: date) -> list[Service]:
        """⚠️ La date est acceptée et **n'écarte encore aucun service** : le
        catalogue n'a pas de bornes d'ouverture et de fermeture, seuls ses tarifs
        sont datés. Un service retiré de l'offre devra porter les siennes, et
        c'est ici que le filtre s'ajoutera."""
        return list(self._services)
