"""La saga d'ouverture d'un tenant : sept étapes, de l'encaissement au lien envoyé.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI UNE SAGA ICI, ET NULLE PART AILLEURS POUR L'INSTANT

Le socle d'orchestration le dit : **une saga ne sert que là où la transaction
s'arrête.** Ce système est un monolithe modulaire ; deux écritures dans deux
contextes partagent la même transaction, et un `COMMIT` suffit.

L'ouverture d'un tenant est le premier endroit où cela ne suffit plus. Sur ses
sept étapes, cinq produisent un effet qu'aucun `ROLLBACK` ne défait :

    SLUG_RESERVE          en base            le rollback le défait
    LIGNE_CREEE           en base            le rollback le défait
    SCHEMA_CREE           DDL                un schéma créé survit au rollback
    METIER_AMORCE         en base            le rollback le défait
    STOCKAGE_OUVERT       hors base          un préfixe objet reste, et se facture
    ADMINISTRATEUR_CREE   hors base          un courriel envoyé ne se rappelle pas
    PRET                  en base            le rollback le défait

Trois effets externes, trois compensations à écrire, et une étape qu'on ne défait
pas. C'est exactement le cas d'usage de la saga, et c'est pourquoi elle est ici
plutôt qu'ailleurs.

⚠️ L'ÉTAPE QU'ON NE DÉFAIT PAS

`ADMINISTRATEUR_CREE` envoie un lien d'activation. **Un courriel parti ne se
rappelle pas.** L'étape est donc déclarée irréversible, avec sa raison, et le
moteur la compte pour ce qu'elle est : à l'abandon, son nom figure dans les
compensations en échec, et l'exploitation le sait plutôt que de le supposer.

Ce que la compensation fait à la place — révoquer le jeton — appartient à l'étape
`STOCKAGE_OUVERT` et aux suivantes : le lien devient inopérant, même si le message
reste dans une boîte.

LA REPRISE EN AVANT EST LE DÉFAUT

Un échec de provisionnement est presque toujours passager : un service lent, un
verrou, une coupure. `avancer` rejoue. La compensation ne sert qu'à l'abandon
**demandé** — client rétracté, paiement contesté, exploitation qui renonce.

Détruire un tenant à moitié créé pour une coupure réseau de trois secondes serait
le pire comportement possible, et c'est celui qu'on obtient en confondant les
deux.

CE MODULE NE RÉALISE AUCUNE ÉTAPE

Il déclare la **forme** de la saga et branche des adaptateurs qu'on lui passe.
C'est ce qui permet de l'éprouver entièrement sans DNS, sans stockage objet et
sans serveur de courriel, et c'est ce qui permettra de changer d'hébergeur sans
toucher au déroulé.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from app.contextes.tenants.domaine.tenant import ETAPES_ORDONNEES, EtapeOuverture
from app.orchestration.saga import Etape, Saga

__all__ = [
    "NOM_SAGA",
    "Provisionneur",
    "saga_d_ouverture",
]

NOM_SAGA = "ouverture-de-tenant"


class Provisionneur(Protocol):
    """Ce que l'infrastructure doit savoir faire, et rien de plus.

    Sept méthodes d'action, trois de compensation. Chacune reçoit le contexte de
    la saga et le rend enrichi ; aucune ne connaît la saga elle-même.

    ⚠️ **Toutes doivent être idempotentes.** Le moteur enregistre l'avancement
    après l'effet, et une panne entre les deux fait rejouer l'étape. Sans
    idempotence, ce rejeu ouvre un second préfixe de stockage et envoie un second
    lien d'activation.
    """

    def reserver_le_slug(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def creer_la_ligne(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def creer_le_schema(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def amorcer_le_metier(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def ouvrir_le_stockage(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def creer_l_administrateur(
        self, contexte: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...
    def basculer_pret(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]: ...

    # ── Les compensations ───────────────────────────────────────────────────

    def liberer_le_slug(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]:
        """⚠️ Libère la **réservation**, jamais le slug d'un tenant qui a vécu.

        Un slug attribué est inréattribuable : le rendre enverrait les anciens
        liens, les signets et les courriels archivés d'un client chez un
        concurrent. Cette compensation ne vaut que pour une ouverture abandonnée
        avant d'avoir servi.
        """
        ...

    def supprimer_la_ligne(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def supprimer_le_schema(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def vider_le_metier(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def fermer_le_stockage(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]:
        """Le préfixe objet reste facturé tant qu'il existe. C'est la compensation
        dont l'oubli coûte de l'argent tous les mois, sans que personne ne le
        voie."""
        ...

    def revoquer_le_jeton(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]:
        """Le message envoyé reste dans la boîte du client ; le lien, lui, devient
        inopérant. C'est tout ce qu'on peut défaire, et il faut le faire."""
        ...

    def rebasculer_en_ouverture(
        self, contexte: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...


def saga_d_ouverture(provisionneur: Provisionneur) -> Saga:
    """La saga, branchée sur un provisionneur.

    ⚠️ **L'ordre des étapes est celui de `ETAPES_ORDONNEES`**, et un contrôle le
    vérifie plutôt que de l'espérer. Les deux listes vivent dans deux fichiers ;
    en réordonner une sans l'autre produirait un provisionnement qui franchit les
    étapes dans un ordre et les enregistre dans un autre. Le défaut ne se verrait
    qu'à la première reprise après incident, c'est-à-dire au pire moment.
    """
    etapes = (
        Etape(
            nom=EtapeOuverture.SLUG_RESERVE,
            action=provisionneur.reserver_le_slug,
            compensation=provisionneur.liberer_le_slug,
            # En base, mais la réservation vaut aussi hors base le jour où le
            # sous-domaine sera posé chez l'hébergeur. Déclarée externe par
            # prudence : une saga qui sous-estime la portée d'une étape ne se
            # trompe qu'une fois, et elle se trompe en production.
            externe=True,
        ),
        Etape(
            nom=EtapeOuverture.LIGNE_CREEE,
            action=provisionneur.creer_la_ligne,
            compensation=provisionneur.supprimer_la_ligne,
            externe=False,
        ),
        Etape(
            nom=EtapeOuverture.SCHEMA_CREE,
            action=provisionneur.creer_le_schema,
            compensation=provisionneur.supprimer_le_schema,
            # Du DDL. PostgreSQL le rend transactionnel, mais la migration qui
            # suit ne l'est pas toujours, et un schéma à moitié migré survit.
            externe=True,
        ),
        Etape(
            nom=EtapeOuverture.METIER_AMORCE,
            action=provisionneur.amorcer_le_metier,
            compensation=provisionneur.vider_le_metier,
            externe=False,
        ),
        Etape(
            nom=EtapeOuverture.STOCKAGE_OUVERT,
            action=provisionneur.ouvrir_le_stockage,
            compensation=provisionneur.fermer_le_stockage,
            externe=True,
        ),
        Etape(
            nom=EtapeOuverture.ADMINISTRATEUR_CREE,
            action=provisionneur.creer_l_administrateur,
            compensation=provisionneur.revoquer_le_jeton,
            externe=True,
        ),
        Etape(
            nom=EtapeOuverture.PRET,
            action=provisionneur.basculer_pret,
            compensation=provisionneur.rebasculer_en_ouverture,
            externe=False,
        ),
    )
    saga = Saga(nom=NOM_SAGA, etapes=etapes)
    _verifier_l_ordre(saga)
    return saga


def _verifier_l_ordre(saga: Saga) -> None:
    """Le déroulé de la saga et l'énumération du domaine disent la même chose.

    Vérifié à la construction, donc à chaque démarrage : un décalage entre les
    deux ne doit pas attendre le premier incident pour se manifester.
    """
    attendu = tuple(e.value for e in ETAPES_ORDONNEES)
    if saga.noms != attendu:
        raise ValueError(
            f"la saga « {saga.nom} » franchit {saga.noms} alors que le domaine "
            f"déclare {attendu}. Les deux listes vivent dans deux fichiers ; en "
            "réordonner une sans l'autre produit un provisionnement qui franchit "
            "les étapes dans un ordre et les enregistre dans un autre, et le "
            "défaut ne se voit qu'à la première reprise après incident."
        )
