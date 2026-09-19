"""Le provisionneur : ce que les sept étapes font réellement aujourd'hui.

─────────────────────────────────────────────────────────────────────────────────
CE QU'IL FAIT VRAIMENT, ET CE QU'IL NE FAIT PAS

Quatre étapes sur sept sont de simples écritures en base, et elles sont
réellement exécutées : réserver le slug, créer la ligne, amorcer le métier,
basculer en actif.

Trois demandent une infrastructure qui n'existe pas encore : créer le schéma,
ouvrir le préfixe de stockage, créer le compte administrateur avec son lien
d'activation. Chacune est déléguée à un **port injecté**.

⚠️ **QUAND LE PORT N'EST PAS FOURNI, L'ÉTAPE EST SUBSTITUÉE, ET ELLE LE DIT**

Elle ne lève pas, parce qu'une saga qui échoue à la troisième étape sur toutes
les installations de développement n'apprend rien à personne. Elle ne réussit pas
silencieusement non plus, parce qu'un tenant marqué actif dont le schéma n'existe
pas est un mensonge que la première requête découvrira.

Elle **inscrit son nom dans le contexte de la saga**, et
`ouverture_reellement_complete` rend faux tant qu'il en reste une. La différence
entre « la saga est terminée » et « le tenant est utilisable » est explicite, et
un test l'affirme.

C'est le seul comportement honnête pour un provisionneur incomplet : le silence
mentirait, l'échec bloquerait, la substitution déclarée informe.

CE QU'IL N'EST PAS

Il n'orchestre rien. Il ne connaît ni la saga, ni l'ordre des étapes, ni les
compensations : il expose sept actions et six compensations, chacune idempotente,
et c'est la définition de la saga qui les enchaîne.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
from typing import Any, Protocol

from app.contextes.tenants.domaine.substitution import (
    CLE_SUBSTITUEES,
    avec_substitution,
    ouverture_reellement_complete,
    substituees,
)
from app.contextes.tenants.domaine.tenant import (
    ETAPES_ORDONNEES,
    EtapeOuverture,
    NatureTenant,
    StatutTenant,
    Tenant,
)

__all__ = [
    "CLE_SUBSTITUEES",
    "RegistreDesTenants",
    "ProvisionneurLocal",
    "SlugDejaPris",
    "ouverture_reellement_complete",
    "substituees",
]

#: Ce qu'un port d'infrastructure doit savoir faire : recevoir le contexte, le
#: rendre enrichi, et lever s'il n'a pas pu.
Port = Callable[[Mapping[str, Any]], Mapping[str, Any]]


class RegistreDesTenants(Protocol):
    """Lire **et écrire** un tenant.

    ⚠️ Distinct de `RepertoireDesTenants`, qui ne sait que lire, et ce n'est pas
    une redondance. Le répertoire est consulté par la passerelle à **chaque
    requête** et doit tenir en mémoire ; le registre est écrit par le
    provisionnement, quelques fois par jour, et doit être durable.

    Les confondre donnerait soit une passerelle qui écrit, soit un
    provisionnement qui perd ses tenants au redémarrage.
    """

    def par_slug(self, slug: str) -> Tenant | None: ...

    def enregistrer(self, tenant: Tenant) -> None: ...


class SlugDejaPris(RuntimeError):
    """Le slug appartient déjà à un autre tenant.

    Distinct d'un échec passager : le rejouer ne le libérera pas. La saga le
    comptera comme une tentative, et l'exploitation devra proposer un autre slug
    au client plutôt qu'attendre.
    """


class ProvisionneurLocal:
    """Réalisation du port `Provisionneur`. Voir l'en-tête pour ce qu'elle tient.

    Les trois ports d'infrastructure sont optionnels. Absents, leurs étapes sont
    substituées et déclarées comme telles.
    """

    def __init__(
        self,
        registre: RegistreDesTenants,
        *,
        a_la_date: date,
        schema: Port | None = None,
        schema_defait: Port | None = None,
        stockage: Port | None = None,
        stockage_ferme: Port | None = None,
        comptes: Port | None = None,
        jeton_revoque: Port | None = None,
    ) -> None:
        self._registre = registre
        self._a_la_date = a_la_date
        self._schema, self._schema_defait = schema, schema_defait
        self._stockage, self._stockage_ferme = stockage, stockage_ferme
        self._comptes, self._jeton_revoque = comptes, jeton_revoque

    # ── Les outils communs ──────────────────────────────────────────────────

    def _deleguer(
        self, port: Port | None, contexte: Mapping[str, Any], etape: EtapeOuverture
    ) -> Mapping[str, Any]:
        if port is None:
            return avec_substitution(contexte, etape.value)
        return dict(port(contexte))

    def _tenant(self, contexte: Mapping[str, Any]) -> Tenant | None:
        return self._registre.par_slug(str(contexte["slug"]))

    # ── Les sept actions ────────────────────────────────────────────────────

    def reserver_le_slug(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]:
        """Idempotente : un slug déjà réservé **pour ce tenant** se constate.

        ⚠️ Réservé pour un **autre** tenant, c'est autre chose, et la distinction
        est tout l'intérêt de cette étape : un slug est inréattribuable, et le
        rejeu ne le libérera jamais. On lève franchement plutôt que de laisser la
        saga espérer.
        """
        slug, identifiant = str(contexte["slug"]), str(contexte["tenant"])
        existant = self._registre.par_slug(slug)
        if existant is not None:
            if existant.identifiant != identifiant:
                raise SlugDejaPris(
                    f"« {slug} » appartient déjà au tenant {existant.identifiant}. "
                    "Un slug n'est jamais réattribué : le rejeu ne le libérera pas, "
                    "il faut en proposer un autre au client."
                )
            return contexte
        nature = NatureTenant(contexte.get("nature", NatureTenant.ENTREPRISE))
        self._registre.enregistrer(
            Tenant(identifiant=identifiant, slug=slug, nature=nature)
        )
        return contexte

    def creer_la_ligne(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._avancer(contexte, EtapeOuverture.LIGNE_CREEE)

    def creer_le_schema(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]:
        enrichi = self._deleguer(self._schema, contexte, EtapeOuverture.SCHEMA_CREE)
        return self._avancer(enrichi, EtapeOuverture.SCHEMA_CREE)

    def amorcer_le_metier(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._avancer(contexte, EtapeOuverture.METIER_AMORCE)

    def ouvrir_le_stockage(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]:
        enrichi = self._deleguer(
            self._stockage, contexte, EtapeOuverture.STOCKAGE_OUVERT
        )
        return self._avancer(enrichi, EtapeOuverture.STOCKAGE_OUVERT)

    def creer_l_administrateur(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]:
        enrichi = self._deleguer(
            self._comptes, contexte, EtapeOuverture.ADMINISTRATEUR_CREE
        )
        return self._avancer(enrichi, EtapeOuverture.ADMINISTRATEUR_CREE)

    def basculer_pret(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]:
        """Marque prêt, puis active. Le sous-domaine répond à cet instant.

        Idempotente : un tenant déjà actif se constate et rend le même contexte.
        """
        from app.contextes.tenants.application.cycle_de_vie import activer, avancer

        tenant = self._tenant(contexte)
        if tenant is None:
            raise RuntimeError(f"tenant « {contexte['slug']} » introuvable au répertoire")
        if tenant.statut is StatutTenant.ACTIF:
            return contexte
        if tenant.etape_atteinte is not EtapeOuverture.PRET:
            tenant = avancer(tenant, EtapeOuverture.PRET)
        self._registre.enregistrer(activer(tenant, self._a_la_date))
        return contexte

    def _avancer(
        self, contexte: Mapping[str, Any], etape: EtapeOuverture
    ) -> Mapping[str, Any]:
        """Fait progresser l'étape atteinte du tenant, et rien d'autre.

        ─────────────────────────────────────────────────────────────────────
        ON N'AVANCE QUE SI L'ÉTAPE EST DEVANT

        La première version comparait l'égalité : elle rejouait `avancer` dès
        que l'étape courante différait de la cible, **y compris quand la cible
        était derrière**. Le domaine refusait alors le retour en arrière, avec
        raison, et l'action n'était donc pas idempotente.

        Le cas n'est pas théorique : c'est exactement celui que la saga existe
        pour traiter. Le moteur enregistre l'avancement **après** l'effet ; une
        panne entre les deux fait rejouer une étape sur un tenant qui l'a déjà
        dépassée. Sans cette comparaison de rang, la reprise après incident
        échouerait sur une transition interdite, c'est-à-dire au moment où l'on
        en a le plus besoin.

        Le défaut a été trouvé par un test qui rejoue chaque action sur un
        tenant déjà ouvert. Les tests de la saga ne pouvaient pas le montrer :
        elle saute les étapes qu'elle a enregistrées, et c'est précisément
        l'enregistrement qui manque dans ce scénario.
        ─────────────────────────────────────────────────────────────────────
        """
        from app.contextes.tenants.application.cycle_de_vie import avancer

        tenant = self._tenant(contexte)
        if tenant is None:
            raise RuntimeError(f"tenant « {contexte['slug']} » introuvable au répertoire")
        rang = ETAPES_ORDONNEES.index
        if rang(tenant.etape_atteinte) < rang(etape):
            self._registre.enregistrer(avancer(tenant, etape))
        return contexte

    # ── Les six compensations ───────────────────────────────────────────────

    def liberer_le_slug(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]:
        """Abandonne l'ouverture. **Ne rend pas le slug réattribuable.**

        Le tenant passe en échec avec son motif, et son slug reste le sien. Le
        libérer enverrait les anciens liens, les signets et les courriels
        archivés d'un client chez un concurrent, ce qui est le pire incident de
        confidentialité que cette plateforme puisse produire.

        ⚠️ **Sans effet sur un tenant qui n'est plus en ouverture.** Le domaine
        refuse d'« abandonner » un tenant actif, et il a raison : voir
        `rebasculer_en_ouverture`. Quand la compensation de l'activation est
        passée avant celle-ci, le tenant est déjà résilié et il n'y a plus rien
        à faire.
        """
        from app.contextes.tenants.application.cycle_de_vie import abandonner

        tenant = self._tenant(contexte)
        if tenant is None or tenant.statut is not StatutTenant.EN_OUVERTURE:
            return contexte
        self._registre.enregistrer(
            abandonner(tenant, str(contexte.get("motif", "ouverture abandonnée")))
        )
        return contexte

    def supprimer_la_ligne(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]:
        """Ne supprime rien. Voir `liberer_le_slug` : la ligne porte le slug.

        Une compensation qui ne fait rien est déclarée telle quelle plutôt que
        d'être omise : son absence de la définition laisserait croire à un
        oubli, et le prochain lecteur l'ajouterait.
        """
        return contexte

    def supprimer_le_schema(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._deleguer(
            self._schema_defait, contexte, EtapeOuverture.SCHEMA_CREE
        )

    def vider_le_metier(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]:
        """Sans objet : le schéma emporte le métier qu'il contenait."""
        return contexte

    def fermer_le_stockage(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._deleguer(
            self._stockage_ferme, contexte, EtapeOuverture.STOCKAGE_OUVERT
        )

    def revoquer_le_jeton(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._deleguer(
            self._jeton_revoque, contexte, EtapeOuverture.ADMINISTRATEUR_CREE
        )

    def rebasculer_en_ouverture(self, contexte: Mapping[str, Any]) -> Mapping[str, Any]:
        """Défait l'activation. **C'est une résiliation, pas un échec.**

        ─────────────────────────────────────────────────────────────────────
        UNE DISTINCTION QUE LE DOMAINE A IMPOSÉE, ET QUI EST JUSTE

        Cette compensation appelait d'abord `abandonner`, comme celle du slug.
        Le domaine l'a refusé : « seule une ouverture en cours peut échouer, or
        le tenant est ACTIF ». C'était le domaine qui avait raison.

        Un tenant qui a été **actif** a existé. Son sous-domaine a répondu, son
        lien d'activation est parti, et le client a pu s'y connecter. Le déclarer
        « en échec d'ouverture » réécrirait cette histoire, et l'on ne saurait
        plus distinguer un provisionnement qui n'a jamais abouti d'un client
        auquel on a retiré son accès.

        Le nom de la méthode vient de la définition de la saga, où elle
        compense l'étape `PRET`. Il est trompeur, et il est conservé pour que la
        correspondance avec l'étape reste évidente ; cette docstring dit ce
        qu'elle fait réellement.
        ─────────────────────────────────────────────────────────────────────
        """
        from app.contextes.tenants.application.cycle_de_vie import resilier

        tenant = self._tenant(contexte)
        if tenant is None or tenant.statut is not StatutTenant.ACTIF:
            return contexte
        self._registre.enregistrer(
            resilier(
                tenant,
                str(contexte.get("motif", "ouverture annulée")),
                self._a_la_date,
            )
        )
        return contexte
