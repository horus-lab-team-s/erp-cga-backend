"""Du nom d'hôte au slug, et du statut du tenant à ce que la requête mérite.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE MODULE FAIT, ET POURQUOI IL EST PUR

Deux décisions, prises des milliers de fois par jour, sur le chemin critique de chaque
requête : quel tenant est demandé, et a-t-il le droit d'être servi.

Elles s'écrivent sans base, sans réseau et sans horloge. C'est ce qui permet d'écrire les
vingt cas limites d'un nom d'hôte — le port collé, la casse, le point final, le
sous-sous-domaine — sans monter quoi que ce soit, et c'est là qu'ils doivent être écrits :
une résolution fautive ne se voit pas, elle sert simplement le mauvais client.

LE SLUG NE PORTE QU'UN SEUL NIVEAU

`station.cga.cm` est un tenant. `compta.station.cga.cm` n'en est pas un, et ce n'est pas
une préférence : **le certificat générique ne couvre qu'un niveau.** Un sous-sous-domaine
n'aurait pas de certificat valide, et le navigateur du client afficherait un avertissement
de sécurité avant même d'atteindre l'application. Le refuser ici est donc cohérent avec ce
que l'infrastructure peut réellement servir.

POURQUOI 404 ET NON 403 SUR UN TENANT INCONNU

Un 403 apprendrait au demandeur que la ressource existe, donc qu'un tenant porte ce nom,
donc que ce client travaille avec ce centre. Énumérer les sous-domaines deviendrait un
moyen de découvrir le portefeuille du cabinet.

Un tenant en cours d'ouverture rend donc 404 lui aussi : il n'existe pas encore
publiquement, et son sous-domaine **répond déjà** grâce à l'enregistrement DNS générique,
bien avant qu'aucun tenant ne soit créé.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from enum import IntEnum

from app.contextes.tenants.domaine.tenant import StatutTenant, Tenant

__all__ = ["Verdict", "slug_depuis_hote", "verdict_pour"]


class Verdict(IntEnum):
    """Ce que la passerelle rend à une requête, selon l'état du tenant visé.

    Des entiers parce qu'ils **sont** des codes de statut : les traduire ailleurs
    ajouterait une table de correspondance à tenir à jour, et le jour où l'on ajoute un
    statut de tenant, l'oubli de la traduction se verrait en production.
    """

    #: Le tenant sert. Que la requête continue.
    SERVIR = 200
    #: Suspendu. Le client doit régulariser, et il faut le lui dire.
    REGULARISER = 402
    #: Résilié. La ressource a existé, elle n'existe plus. Le dire est correct : le client
    #: sait déjà qu'il est parti, on ne lui apprend rien qu'il ignore.
    DISPARU = 410
    #: Inconnu, ou pas encore ouvert. On n'apprend rien à personne.
    INTROUVABLE = 404


def slug_depuis_hote(hote: str | None, domaine_racine: str) -> str | None:
    """Le slug porté par ce nom d'hôte, ou None s'il n'en porte pas.

    Rend None plutôt que de lever : un nom d'hôte inattendu est un cas courant — une sonde,
    un scanner, un client mal configuré — et lever ferait de chacun une erreur à
    diagnostiquer. L'appelant décide quoi faire d'une absence.
    """
    if not hote or not domaine_racine:
        return None

    # L'en-tête `Host` porte le port quand il n'est pas celui par défaut, et la forme
    # pleinement qualifiée porte un point final. Ni l'un ni l'autre n'appartient au nom.
    nom = hote.strip().lower().split(":", 1)[0].rstrip(".")
    racine = domaine_racine.strip().lower().rstrip(".")

    # Une adresse littérale IPv6 arrive entre crochets. Elle ne porte jamais de slug, et
    # la découper sur le premier deux-points ci-dessus l'a de toute façon mutilée.
    if not nom or nom.startswith("["):
        return None

    suffixe = f".{racine}"
    if not nom.endswith(suffixe):
        # Le domaine nu, un domaine étranger, `localhost` : aucun slug.
        return None

    etiquette = nom[: -len(suffixe)]
    if not etiquette or "." in etiquette:
        # Vide, ou plusieurs niveaux. Voir l'en-tête : le certificat générique n'en
        # couvre qu'un, et un sous-sous-domaine échouerait avant d'atteindre le code.
        return None

    return etiquette


def verdict_pour(tenant: Tenant | None) -> Verdict:
    """Ce que mérite une requête adressée à ce tenant.

    ⚠️ **Un tenant inconnu et un tenant en cours d'ouverture rendent la même chose.** C'est
    délibéré : distinguer les deux dirait à un inconnu qu'un slug est pris, donc qu'un
    client est en train d'arriver.
    """
    if tenant is None:
        return Verdict.INTROUVABLE
    if tenant.statut is StatutTenant.ACTIF:
        return Verdict.SERVIR
    if tenant.statut is StatutTenant.SUSPENDU:
        return Verdict.REGULARISER
    if tenant.statut is StatutTenant.RESILIE:
        return Verdict.DISPARU
    # EN_OUVERTURE et ECHEC : le sous-domaine répond déjà, grâce à l'enregistrement DNS
    # générique, mais rien ne s'y trouve encore.
    return Verdict.INTROUVABLE
