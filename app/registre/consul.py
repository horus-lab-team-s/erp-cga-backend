"""Ce que nos quatorze services deviennent pour HashiCorp Consul.

─────────────────────────────────────────────────────────────────────────────────
LA DÉCLARATION EST DANS LE DÉPÔT, ET CONSUL LA REÇOIT

Ce module **engendre** la configuration Consul depuis `app/registre/services.py`.
Il ne la lit pas, ne la synchronise pas, et surtout n'invite personne à la modifier
dans l'interface de Consul.

⚠️ **C'est la règle qui décide de tout le reste.** Une intention corrigée à la main
dans Consul serait invisible dans le dépôt, absente de la revue, perdue au prochain
déploiement, et le test d'architecture continuerait d'affirmer une découpe qui ne
serait plus celle appliquée. Ce serait exactement le défaut que le pas 14 vient de
corriger, avec un tour de plus : la vérité serait alors hors de portée du code.

Le sens est donc unique : le dépôt écrit, Consul applique.

CE QUE CONSUL SAIT FAIRE DE CE QU'ON A DÉJÀ

Le rapprochement est presque terme à terme, et ce n'est pas un hasard : le registre
a été écrit en pensant à cette étape.

    SERVICES ............... les enregistrements de service
    prefixes ............... les tags de routage de la passerelle
    Sonde et ses verdicts .. les contrôles de santé, avec leurs trois niveaux
    ARETES_AUTORISEES ...... les intentions du maillage, appliquées par mTLS
    EtatDeConstruction ..... rien. Consul ne connaît pas cette question

Le dernier point mérite d'être dit plutôt que tu : « jusqu'où ce service est-il
écrit » n'a aucun équivalent chez Consul, qui décrit ce qui tourne et non ce qui
est fait. Cette question reste au registre, et c'est très bien ainsi.

⚠️ LE MONOLITHE ENREGISTRE QUATORZE SERVICES, PAS UN

C'est le point qui surprend, et c'est le plus utile. Un agent Consul peut porter
plusieurs services ; rien n'oblige à un processus par service. Les quatorze
s'enregistrent donc dès aujourd'hui, à la même adresse et au même port, **chacun
avec son propre contrôle de santé**.

Le bénéfice est immédiat : Consul montre la santé service par service alors que
tout vit encore dans un processus. Et le jour où l'un part vivre ailleurs, seule son
adresse change. L'extraction cesse d'être une refonte pour devenir un déménagement.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from typing import Any

from app.registre.services import SERVICES, Service, autorises_pour

__all__ = [
    "ESPACE_DE_NOMS",
    "INTERVALLE_CONTROLE",
    "chemin_de_sante",
    "definitions_de_services",
    "intentions",
    "nom_consul",
]

#: Le préfixe des noms dans Consul. Une instance Consul sert souvent plusieurs
#: applications, et « referentiel » tout court entrerait en collision avec le
#: référentiel de n'importe qui d'autre. Le préfixe est du code plutôt qu'un
#: réglage : le changer renommerait tous les services et casserait toutes les
#: intentions d'un coup, ce qui ne se fait pas depuis un fichier d'environnement.
ESPACE_DE_NOMS = "cga"

#: ⚠️ **Dix secondes, et c'est un compromis à comprendre avant de le changer.**
#:
#: Plus court, chaque service est sondé plus souvent que servi pendant les heures
#: creuses, et les sondes deviennent une charge. Plus long, un service tombé reste
#: annoncé sain, et le maillage continue de lui envoyer du trafic pendant ce temps.
#:
#: Dix secondes veut dire qu'une panne se voit en dix secondes au pire, et que
#: quatorze services produisent 1,4 appel par seconde. C'est tenable, et c'est ce
#: que Consul propose lui-même par défaut.
INTERVALLE_CONTROLE = "10s"

#: Le délai au delà duquel un contrôle est tenu pour échoué. Nettement plus court
#: que l'intervalle : un contrôle qui met plus d'une seconde à répondre est déjà le
#: signe de ce qu'on cherche à détecter, et l'attendre davantage retarderait
#: simplement le verdict.
DELAI_CONTROLE = "1s"

#: Combien de temps Consul garde un service critique avant de le retirer du
#: catalogue. ⚠️ **Jamais court.** Un service retiré disparaît de l'écran de
#: l'exploitant au moment précis où il le cherche, et le message « service inconnu »
#: est bien pire que « service en panne ».
RETRAIT_APRES = "72h"


def nom_consul(service: Service) -> str:
    """Le nom du service dans le catalogue Consul.

    Le tiret plutôt que le tiret bas : Consul emploie les noms de service dans son
    interface DNS, où le tiret bas n'est pas admis par la RFC 1123. Le service
    `creation_entreprise` serait irrésolvable sous son nom Python.
    """
    return f"{ESPACE_DE_NOMS}-{service.nom.replace('_', '-')}"


def chemin_de_sante(service: Service) -> str:
    """Le chemin que Consul interroge pour connaître l'état de ce service.

    ⚠️ Un chemin **par service**, et non la sonde globale `/sante`. Une sonde unique
    ferait que les quatorze services tombent ou tiennent ensemble, ce qui est
    précisément l'information qu'on ne veut pas : savoir que « la plateforme est en
    panne » n'aide personne à décider quoi redémarrer.
    """
    return f"/transverse/services/{service.nom}/sante"


def definitions_de_services(
    *, adresse: str, port: int, jeton_de_sonde: str | None = None
) -> list[dict[str, Any]]:
    """Les enregistrements à remettre à l'agent Consul, un par service.

    ─────────────────────────────────────────────────────────────────────────────
    `adresse` et `port` sont les mêmes pour les quatorze aujourd'hui : ils désignent
    le processus qui les héberge tous. Ce sont des **arguments** et non des
    constantes précisément pour que l'extraction d'un service ne demande qu'un appel
    différent, sans toucher à ce module.

    ⚠️ **`jeton_de_sonde` n'a pas de valeur par défaut, et c'est délibéré.**

    Le chemin de santé d'un service est protégé, comme tout ce qui décrit
    l'architecture : la liste de ce qui tombe avec quoi est une carte des points de
    rupture. Consul sait porter un en-tête d'autorisation sur ses contrôles ; le
    jeton vient donc de l'exploitation, jamais du dépôt.

    Sans jeton, les définitions sont engendrées quand même, avec des contrôles qui
    échoueront en `401`. C'est franc : Consul dira que les quatorze services sont
    critiques, et l'exploitant cherchera le jeton. Fournir un défaut ferait pire, en
    laissant croire que la protection est levée.
    ─────────────────────────────────────────────────────────────────────────────
    """
    definitions = []
    for service in SERVICES:
        controle: dict[str, Any] = {
            "name": f"santé de {service.libelle}",
            "http": f"http://{adresse}:{port}{chemin_de_sante(service)}",
            "interval": INTERVALLE_CONTROLE,
            "timeout": DELAI_CONTROLE,
            # Voir `RETRAIT_APRES` : un service inconnu est pire qu'un service en panne.
            "deregister_critical_service_after": RETRAIT_APRES,
        }
        if jeton_de_sonde:
            controle["header"] = {"Authorization": [f"Bearer {jeton_de_sonde}"]}

        definitions.append(
            {
                "id": nom_consul(service),
                "name": nom_consul(service),
                "address": adresse,
                "port": port,
                # Les tags sont ce sur quoi la passerelle et les requêtes de
                # catalogue filtrent. La lettre et le plan y figurent parce que ce
                # sont les deux façons dont ce projet désigne ses services depuis
                # l'origine, et parce qu'un tag absent ne se rattrape pas côté
                # requête.
                "tags": [
                    f"lettre={service.lettre}",
                    f"plan={service.plan}",
                    *(f"prefixe={p}" for p in service.prefixes),
                ],
                "meta": {"objet": service.objet[:512]},
                "checks": [controle],
            }
        )
    return definitions


def intentions() -> list[dict[str, Any]]:
    """Les intentions du maillage : qui a le droit d'appeler qui.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LE GRAPHE DOIT ÊTRE INVERSÉ, ET C'EST LA SUBTILITÉ DE CE MODULE.**

    `ARETES_AUTORISEES` est déclaré **source vers destinations** : « la Comptabilité
    lit le Portefeuille, la Conformité, la Collecte ». C'est la forme qui se relit
    quand on écrit du code, puisqu'on part du service qu'on modifie.

    Consul déclare l'inverse : une entrée par **destination**, listant ses sources
    admises. C'est la forme qui s'applique, puisque c'est la destination qui décide
    de laisser entrer.

    Engendrer sans inverser produirait des intentions syntaxiquement valides et
    sémantiquement retournées : le Référentiel aurait le droit d'appeler douze
    services, et personne n'aurait le droit de l'appeler. Le maillage refuserait
    alors **tout le trafic réel** en laissant passer celui qui n'existe pas.

    ⚠️ **ET LE SOCLE DOIT ÊTRE DÉVELOPPÉ.**

    `ARETES_AUTORISEES` ne mentionne pas le socle : `autorises_pour` l'ajoute
    implicitement, parce que le recopier dans dix entrées ferait dix endroits où
    l'oublier. Un générateur qui lirait le dictionnaire brut produirait un maillage
    où plus personne ne peut lire le Référentiel — c'est-à-dire où plus aucun calcul
    n'est possible. Ce module passe donc par `autorises_pour`, jamais par le
    dictionnaire.

    LE REFUS PAR DÉFAUT EST LA MOITIÉ DU MÉCANISME

    Ces intentions n'ont de valeur que si Consul refuse ce qu'elles ne nomment pas.
    Voir `politique_par_defaut` : sans elle, une liste d'autorisations sur un
    maillage permissif ne décrit rien du tout.
    ─────────────────────────────────────────────────────────────────────────────
    """
    sources_par_destination: dict[str, set[str]] = {s.nom: set() for s in SERVICES}
    for service in SERVICES:
        # `autorises_pour` et non `ARETES_AUTORISEES` : voir l'en-tête, le socle.
        for destination in autorises_pour(service.nom):
            sources_par_destination[destination].add(service.nom)

    entrees = []
    for service in SERVICES:
        sources = sorted(sources_par_destination[service.nom])
        if not sources:
            # Aucune entrée pour un service que personne n'appelle. Une entrée vide
            # se lirait comme « tout est refusé », ce qui est vrai mais que la
            # politique par défaut dit déjà, et elle le dirait deux fois.
            continue
        entrees.append(
            {
                "Kind": "service-intentions",
                "Name": nom_consul(service),
                "Sources": [
                    {
                        "Name": nom_consul(_par_nom(source)),
                        "Action": "allow",
                        "Description": f"{_par_nom(source).lettre} lit "
                        f"{service.lettre} · graphe établi flux par flux, voir "
                        "Docs/architecture/10-flux-fonctionnels.md",
                    }
                    for source in sources
                ],
            }
        )
    return entrees


def politique_par_defaut() -> dict[str, Any]:
    """Le refus par défaut, sans lequel les intentions ne décrivent rien.

    ⚠️ Une liste d'autorisations sur un maillage permissif est une liste de
    commentaires. Ce n'est pas une précaution théorique : Consul admet les deux
    réglages, et le permissif est celui qui laisse une installation fonctionner
    pendant qu'on croit avoir cloisonné.

    C'est la même leçon que les politiques de sécurité au niveau des lignes du
    pas 12 : une règle parfaitement écrite qui ne s'applique à personne ne se
    signale jamais.
    """
    return {"Kind": "mesh", "AllowEnablingPermissiveMutualTLS": False}


def _par_nom(nom: str) -> Service:
    for service in SERVICES:
        if service.nom == nom:
            return service
    raise KeyError(nom)  # pragma: no cover — le graphe est vérifié au test d'architecture


if __name__ == "__main__":  # pragma: no cover — outil d'exploitation
    import argparse
    import json

    analyseur = argparse.ArgumentParser(
        description="Engendre la configuration Consul depuis le registre des services."
    )
    analyseur.add_argument("--adresse", default="127.0.0.1")
    analyseur.add_argument("--port", type=int, default=8000)
    analyseur.add_argument("--jeton-de-sonde", default=None)
    arguments = analyseur.parse_args()

    print(
        json.dumps(
            {
                "services": definitions_de_services(
                    adresse=arguments.adresse,
                    port=arguments.port,
                    jeton_de_sonde=arguments.jeton_de_sonde,
                ),
                "intentions": intentions(),
                "mesh": politique_par_defaut(),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
