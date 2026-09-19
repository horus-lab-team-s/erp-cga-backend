"""Ouvrir, reprendre, suspendre, résilier un tenant.

Les cas d'usage du contexte N. Chacun rend un **nouveau** tenant : rien n'est modifié sur
place, ce qui permet de comparer l'avant et l'après et empêche un cas d'usage d'altérer un
tenant qu'un autre tient déjà.

⚠️ Aucun de ces cas d'usage ne parle à une base ni à une horloge. La date lui est donnée.
C'est ce qui les rend testables sans montage, et ce qui évite le défaut classique d'un
provisionnement qui lit l'horloge murale et se comporte autrement à minuit.

CE QU'ILS NE FONT PAS

Ils ne créent ni schéma, ni stockage, ni compte. Ils tiennent l'**état** de l'ouverture ;
le travail lui-même est fait par des adaptateurs qui appellent `avancer` quand ils ont
fini. Séparer les deux est ce qui permet de rejouer la machine à états dans un test sans
base, et de la relire sans savoir ce qu'est un schéma PostgreSQL.
"""

from __future__ import annotations

from datetime import date

from app.contextes.tenants.domaine.tenant import (
    ETAPES_ORDONNEES,
    STATUTS_TERMINAUX,
    EtapeOuverture,
    NatureTenant,
    StatutTenant,
    Tenant,
    TransitionInterdite,
    etape_suivante,
)

__all__ = [
    "REPRISES_MAXIMALES",
    "abandonner",
    "activer",
    "avancer",
    "ouvrir",
    "reactiver",
    "reprendre",
    "resilier",
    "suspendre",
]

#: Au delà, l'ordonnanceur cesse de reprendre et bascule en échec. Une reprise qui boucle
#: indéfiniment masque un défaut au lieu de le signaler, et consomme une file pour rien.
REPRISES_MAXIMALES = 5


def ouvrir(identifiant: str, slug: str, nature: NatureTenant) -> Tenant:
    """Le tenant naît avec son slug déjà réservé.

    L'ordre importe : le slug est pris en base **avant** que le tenant existe, par un index
    unique. C'est la base qui arbitre deux souscriptions simultanées, jamais une lecture
    suivie d'une écriture — entre les deux, l'autre a eu le temps d'écrire.
    """
    return Tenant(
        identifiant=identifiant,
        slug=slug,
        nature=nature,
        statut=StatutTenant.EN_OUVERTURE,
        etape_atteinte=EtapeOuverture.SLUG_RESERVE,
    )


def avancer(tenant: Tenant, etape: EtapeOuverture) -> Tenant:
    """Franchit une étape de l'ouverture, et une seule.

    Rejouer l'étape courante est **admis et sans effet** : c'est ce qui rend le
    provisionnement idempotent quand le bus livre un message deux fois. Sauter une étape ou
    revenir en arrière est refusé.
    """
    if tenant.statut is not StatutTenant.EN_OUVERTURE:
        raise TransitionInterdite(
            f"{tenant.slug} : on n'avance dans l'ouverture que depuis EN_OUVERTURE, "
            f"or le tenant est {tenant.statut.value}"
        )

    if etape is tenant.etape_atteinte:
        # Le cas du message rejoué. On rend le tenant tel quel plutôt qu'une erreur :
        # l'appelant a bien fait son travail, simplement il l'avait déjà fait.
        return tenant

    # Les trois refus sont distingués parce qu'ils appellent trois diagnostics différents :
    # un retour en arrière signale un ordonnancement fautif, un saut signale une étape
    # oubliée, et une avance au delà du bout signale une activation manquante.
    if ETAPES_ORDONNEES.index(etape) < ETAPES_ORDONNEES.index(tenant.etape_atteinte):
        raise TransitionInterdite(
            f"{tenant.slug} : retour de {tenant.etape_atteinte.value} à {etape.value} "
            "refusé. Rejouer une étape déjà dépassée créerait un second schéma pour le "
            "même tenant, et le premier resterait là sans que personne sache à quoi il sert."
        )

    attendue = etape_suivante(tenant.etape_atteinte)
    if attendue is None:
        raise TransitionInterdite(
            f"{tenant.slug} : l'ouverture est au bout de ses étapes, il reste à activer"
        )
    if etape is not attendue:
        raise TransitionInterdite(
            f"{tenant.slug} : étape {etape.value} demandée alors que {attendue.value} "
            f"est attendue après {tenant.etape_atteinte.value}. Sauter une étape laisse "
            "un tenant à moitié fait, dont le schéma ou le stockage manquent."
        )

    return tenant.model_copy(update={"etape_atteinte": etape})


def activer(tenant: Tenant, a_la_date: date) -> Tenant:
    """Le sous-domaine se met à répondre, à cet instant précis.

    Aucun redéploiement, aucune écriture DNS, aucune demande de certificat. Le tenant
    devient joignable parce qu'une ligne a changé de statut, et l'invalidation du cache des
    passerelles se propage par événement.
    """
    if tenant.statut is not StatutTenant.EN_OUVERTURE:
        raise TransitionInterdite(
            f"{tenant.slug} : seul un tenant EN_OUVERTURE s'active, "
            f"or celui-ci est {tenant.statut.value}"
        )
    if not tenant.ouverture_achevee:
        raise TransitionInterdite(
            f"{tenant.slug} : l'ouverture s'arrête à {tenant.etape_atteinte.value}. "
            "Activer maintenant donnerait un tenant joignable dont le schéma, le stockage "
            "ou le compte administrateur manquent."
        )
    return tenant.model_copy(
        update={"statut": StatutTenant.ACTIF, "ouvert_le": a_la_date, "motif": None}
    )


def abandonner(tenant: Tenant, motif: str) -> Tenant:
    """L'ouverture échoue définitivement.

    **Rien n'est détruit.** Le schéma déjà créé reste, le stockage aussi. Un schéma
    orphelin coûte quelques mégaoctets ; un schéma détruit à tort coûte une comptabilité.

    Le paiement correspondant est marqué comme à régulariser par le contexte qui l'a
    encaissé : personne ne garde l'argent d'un client dont le compte ne s'est pas ouvert.
    """
    if tenant.statut is not StatutTenant.EN_OUVERTURE:
        raise TransitionInterdite(
            f"{tenant.slug} : seule une ouverture en cours peut échouer, "
            f"or le tenant est {tenant.statut.value}"
        )
    return tenant.model_copy(update={"statut": StatutTenant.ECHEC, "motif": motif})


def reprendre(tenant: Tenant) -> Tenant:
    """Relance une ouverture échouée, **à l'étape atteinte** et non depuis le début.

    C'est la distinction qui justifie tout le champ `etape_atteinte`. Recommencer à zéro
    créerait un second schéma pour le même tenant, et le premier resterait là sans que
    personne sache à quoi il sert.
    """
    if tenant.statut is not StatutTenant.ECHEC:
        raise TransitionInterdite(
            f"{tenant.slug} : on ne reprend qu'une ouverture en échec, "
            f"or le tenant est {tenant.statut.value}"
        )
    if tenant.reprises >= REPRISES_MAXIMALES:
        raise TransitionInterdite(
            f"{tenant.slug} : {tenant.reprises} reprises déjà tentées. Au delà, la reprise "
            "masque un défaut au lieu de le signaler. Diagnostiquer avant de relancer."
        )
    return tenant.model_copy(
        update={
            "statut": StatutTenant.EN_OUVERTURE,
            "reprises": tenant.reprises + 1,
            "motif": None,
        }
    )


def suspendre(tenant: Tenant, motif: str, a_la_date: date) -> Tenant:
    """Le sous-domaine répond une page de régularisation, en lecture seule.

    **Les données restent intactes**, et c'est le point. Une suspension est réversible
    instantanément au paiement ; elle n'est pas une résiliation anticipée. Un client qui
    régularise doit retrouver son espace exactement comme il l'a laissé.
    """
    if tenant.statut is not StatutTenant.ACTIF:
        raise TransitionInterdite(
            f"{tenant.slug} : seul un tenant actif se suspend, "
            f"or celui-ci est {tenant.statut.value}"
        )
    return tenant.model_copy(
        update={"statut": StatutTenant.SUSPENDU, "suspendu_le": a_la_date, "motif": motif}
    )


def reactiver(tenant: Tenant) -> Tenant:
    """Le service revient, sans redéploiement.

    La date d'ouverture ne bouge pas : c'est celle du premier jour, pas celle du retour. La
    date de suspension est effacée parce qu'elle ne décrit plus rien ; l'historique des
    suspensions, s'il faut le garder, relève du journal d'audit et non de cette ligne.
    """
    if tenant.statut is not StatutTenant.SUSPENDU:
        raise TransitionInterdite(
            f"{tenant.slug} : seul un tenant suspendu se réactive, "
            f"or celui-ci est {tenant.statut.value}"
        )
    return tenant.model_copy(
        update={"statut": StatutTenant.ACTIF, "suspendu_le": None, "motif": None}
    )


def resilier(tenant: Tenant, motif: str, a_la_date: date) -> Tenant:
    """Fin de la relation. État terminal.

    Le slug **reste réservé définitivement**. Le libérer enverrait les anciens liens, les
    signets et les courriels archivés d'un client chez un concurrent — le pire incident de
    confidentialité que cette plateforme puisse produire, et il n'a aucune contrepartie.

    Les données ne sont pas détruites : le client dispose d'un délai pour récupérer un
    export complet de son schéma, et la purge n'intervient qu'à l'échéance de rétention,
    dix ans après la clôture du dernier exercice.
    """
    if tenant.statut in STATUTS_TERMINAUX:
        raise TransitionInterdite(f"{tenant.slug} : déjà {tenant.statut.value}")
    if tenant.statut is StatutTenant.EN_OUVERTURE:
        raise TransitionInterdite(
            f"{tenant.slug} : une ouverture en cours s'abandonne, elle ne se résilie pas. "
            "La distinction compte : un abandon n'a jamais eu de client à prévenir."
        )
    return tenant.model_copy(
        update={"statut": StatutTenant.RESILIE, "resilie_le": a_la_date, "motif": motif}
    )
