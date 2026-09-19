"""Les quatorze services, leurs lettres, et le graphe de leurs dépendances.

─────────────────────────────────────────────────────────────────────────────────
LA DÉCLARATION VIT ICI, ET NULLE PART AILLEURS

Elle était dans `tests/test_architecture.py`. Elle y était juste et vérifiée, et
elle était **invisible à l'exécution** : aucune route ne pouvait dire quels
services existent, ni ce qui tombe avec l'un d'eux.

Le test l'importe désormais d'ici. C'est le même graphe qui interdit une arête à la
relecture et qui répond à la sonde : deux vérités recopiées finissent toujours par
diverger, et celle qui dérive est celle que personne ne relit.

⚠️ HOMONYME À CONNAÎTRE

`app.contextes.souscription.domaine.offre.Service` est une **prestation vendue** :
l'adhésion, la paie, une formation. Le `Service` de ce module est un **service
logiciel** : le Référentiel, la Comptabilité. Deux sujets, deux paquets, aucun
rapport. Le chemin d'import les distingue, et cette note évite de le découvrir dans
une pile d'appels.

POURQUOI UNE LETTRE EN PLUS DU NOM

Les documents d'architecture désignent les contextes par une lettre depuis le
premier jour : « D · Conformité », « le contexte A ». La lettre est donc une donnée
du projet, pas une décoration — et la porter ici permet de vérifier qu'aucune
n'est employée deux fois, ce qu'une relecture ne voit pas.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ARETES_AUTORISEES",
    "SERVICES",
    "SOCLE",
    "Service",
    "SourceDeRecherche",
    "autorises_pour",
    "dependances_transitives",
    "qui_tombe_avec",
    "service",
]


class SourceDeRecherche(BaseModel):
    """Une route de recherche qu'un service déclare, et qui peut l'appeler (pas 93).

    ⚠️ **Déclarer ici, c'est brancher.** L'écran de recherche lit ces déclarations et
    interroge chaque source que la session peut appeler. Ajouter une source, c'est
    écrire sa route sous le préfixe du service et la déclarer : ni la coquille ni un
    module central ne changent. Le test d'architecture vérifie que la route déclarée
    existe, sous le bon préfixe, et rend bien le contrat commun.
    """

    model_config = ConfigDict(frozen=True)

    #: Le chemin de la route, qui accepte `?q=`.
    chemin: str = Field(pattern=r"^/")
    #: Ce que l'écran affiche en tête du groupe : « Dossiers », « Pièces ».
    libelle: str = Field(min_length=1)
    #: La permission sans laquelle la route refuse. Une chaîne : le registre ne
    #: connaît pas la table des rôles, c'est le transverse qui la confronte à la session.
    permission: str = Field(min_length=1)


class Service(BaseModel):
    """Un service de la plateforme, tel qu'il est déclaré.

    ⚠️ **Rien de ce que porte cet objet n'est un état.** Le nom, la lettre, le plan
    et l'objet ne changent pas selon que le service répond ou non. Les états sont
    constatés ailleurs, à `app/registre/etat.py`, et les mélanger ferait qu'une
    déclaration figée porterait des champs qui changent à chaque appel.
    """

    model_config = ConfigDict(frozen=True)

    #: Le nom du répertoire sous `app/contextes/`. C'est la clé, parce que c'est
    #: ce que le système de fichiers connaît : un registre dont la clé serait un
    #: libellé ne pourrait rien constater.
    nom: str = Field(min_length=1)
    #: La lettre employée par les documents d'architecture depuis l'origine.
    lettre: str = Field(min_length=1, max_length=2)
    libelle: str = Field(min_length=1)
    #: Le plan de la carte des services. Il dit le profil de charge et le cycle de
    #: livraison, pas la couche technique.
    plan: str = Field(min_length=1)
    #: Ce qu'un arrêt de ce service empêche. Écrit pour l'exploitant, pas pour le
    #: développeur : « le Référentiel est arrêté » ne dit pas ce qui cesse de
    #: fonctionner.
    objet: str = Field(min_length=1)

    #: Les préfixes HTTP sous lesquels ce service répond. Vide quand il n'expose
    #: aucune route de lui-même.
    #:
    #: ⚠️ **C'est une donnée du service, pas une commodité de test.** Le jour où la
    #: passerelle routera vers des services séparés, c'est là-dessus qu'elle
    #: routera. Elle sert aujourd'hui à constater qu'un service est réellement
    #: joignable : un module de routes présent sur le disque mais dont le routeur
    #: n'est monté nulle part ne rend le service joignable par personne.
    #:
    #: Un service peut en porter plusieurs : la Souscription répond sous
    #: `/souscription` pour les devis et `/acquisition` pour le parcours public.
    prefixes: tuple[str, ...] = ()
    #: Pas 93 : ce que ce service offre à la recherche globale, s'il offre quelque chose.
    #: Voir `app/partage/recherche.py` sur la recherche fédérée.
    recherche: SourceDeRecherche | None = None


#: ⚠️ **Le socle ne dépend de rien, et tout le monde peut en dépendre.**
#:
#: Trois services y figurent, et chacun pour une raison distincte. Le Référentiel
#: dit ce que la loi prévoit, et une valeur légale n'a pas de métier. Le Transverse
#: dit qui parle et ce qu'il a le droit de faire. Les Tenants disent qu'un
#: sous-domaine répond et à qui.
#:
#: Un service du socle qui dépendrait d'un contexte métier créerait un cycle à
#: l'exécution : le métier a besoin de savoir qui parle avant de répondre, et
#: l'identité aurait besoin du métier pour le dire.
SOCLE: frozenset[str] = frozenset({"referentiel", "transverse", "tenants"})


#: Les quatorze services, dans l'ordre des lettres, qui est celui de la chaîne de
#: valeur : ce que la loi prévoit, qui est qui, ce qui arrive, ce qu'on contrôle,
#: ce qu'on enregistre, ce qu'on déclare.
SERVICES: tuple[Service, ...] = (
    Service(
        nom="referentiel",
        prefixes=("/referentiel",),
        lettre="A",
        libelle="Référentiel",
        plan="Plan de contrôle",
        objet=(
            "porte les paramètres datés, les paquets de règles et les barèmes. "
            "Arrêté, plus aucun calcul fiscal, comptable ou social n'est possible : "
            "tous les autres services le lisent, aucun n'y écrit"
        ),
    ),
    Service(
        nom="portefeuille",
        recherche=SourceDeRecherche(
            chemin="/portefeuille/recherche", libelle="Dossiers", permission="LIRE_DOSSIER"
        ),
        prefixes=("/portefeuille",),
        lettre="B",
        libelle="Portefeuille",
        plan="Production comptable",
        objet=(
            "dit qui est qui : entreprises, dossiers, exercices, régimes, adhésions. "
            "Arrêté, plus rien ne se rattache à un client"
        ),
    ),
    Service(
        nom="collecte",
        recherche=SourceDeRecherche(
            chemin="/collecte/recherche", libelle="Pièces", permission="LIRE_PIECE"
        ),
        prefixes=("/collecte",),
        lettre="C",
        libelle="Collecte",
        plan="Production comptable",
        objet=(
            "reçoit les pièces et les demande quand elles manquent. Arrêté, les "
            "adhérents ne peuvent plus déposer, et les relances de pièces cessent"
        ),
    ),
    Service(
        nom="conformite",
        prefixes=("/conformite",),
        lettre="D",
        libelle="Conformité",
        plan="Production comptable",
        objet=(
            "contrôle une pièce et rend des constats chiffrés. Arrêté, rien n'est "
            "vérifié avant enregistrement, et le centre engage son agrément à l'aveugle"
        ),
    ),
    Service(
        nom="comptabilite",
        prefixes=("/comptabilite",),
        lettre="E",
        libelle="Comptabilité",
        plan="Production comptable",
        objet=(
            "tient les écritures, le grand livre, la balance et les états financiers. "
            "Arrêté, plus aucune saisie ni aucun état"
        ),
    ),
    Service(
        nom="obligations",
        prefixes=("/obligations",),
        lettre="F",
        libelle="Obligations",
        plan="Production comptable",
        objet=(
            "tient l'échéancier fiscal et prépare les dépôts. Arrêté, une échéance "
            "manquée ne se voit plus, et les pénalités courent"
        ),
    ),
    Service(
        nom="social",
        prefixes=("/social",),
        lettre="G",
        libelle="Social",
        plan="Production comptable",
        objet=(
            "salariés, contrats, bulletins, déclarations sociales. Arrêté, aucune "
            "paie ne sort"
        ),
    ),
    Service(
        nom="cloture",
        prefixes=("/cloture",),
        lettre="H",
        libelle="Clôture",
        plan="Production comptable",
        objet=(
            "conduit la clôture d'exercice et la déclaration statistique et fiscale. "
            "Arrêté, l'exercice ne se ferme pas"
        ),
    ),
    Service(
        nom="creation_entreprise",
        prefixes=("/creations",),
        lettre="I",
        libelle="Création d'entreprise",
        plan="Relation client",
        objet=(
            "conduit une création, ses jalons et ses formalités. Arrêté, un dossier "
            "en cours reste en l'état, sans perte"
        ),
    ),
    Service(
        nom="pilotage",
        prefixes=("/pilotage",),
        lettre="J",
        libelle="Pilotage",
        plan="Plan de contrôle",
        objet=(
            "agrège pour la direction du cabinet. ⚠️ Arrêté, **rien de la production "
            "ne s'arrête** : il lit tout le monde et personne ne le lit"
        ),
    ),
    Service(
        nom="transverse",
        recherche=SourceDeRecherche(
            chemin="/transverse/recherche", libelle="Comptes du cabinet", permission="GERER_COMPTES"
        ),
        prefixes=("/transverse", "/orchestration"),
        lettre="K",
        libelle="Transverse",
        plan="Plan de contrôle",
        objet=(
            "identité, sessions, habilitations, journal d'audit, passerelle. Arrêté, "
            "plus personne n'entre : c'est le service dont l'arrêt se voit le plus vite"
        ),
    ),
    Service(
        nom="vitrine",
        prefixes=("/vitrine",),
        lettre="L",
        libelle="Vitrine",
        plan="Accès",
        objet=(
            "le contenu public du site. Arrêté, le site ne se remplit plus, et aucun "
            "prospect n'arrive — mais rien de ce qui est déjà vendu ne s'interrompt"
        ),
    ),
    Service(
        nom="souscription",
        recherche=SourceDeRecherche(
            chemin="/acquisition/recherche",
            libelle="Demandes et prospects",
            permission="LIRE_PROSPECT",
        ),
        prefixes=("/souscription", "/acquisition"),
        lettre="M",
        libelle="Souscription",
        plan="Relation client",
        objet=(
            "le parcours d'acquisition : demande, qualification, chiffrage, proforma, "
            "encaissement, relance. Arrêté, plus aucune vente ne se conclut"
        ),
    ),
    Service(
        nom="tenants",
        prefixes=(),
        lettre="N",
        libelle="Tenants",
        plan="Plan de contrôle",
        objet=(
            "provisionnement, sous-domaines, plans, suspension, résiliation. Arrêté, "
            "la passerelle ne résout plus aucun sous-domaine et tout rend 404"
        ),
    ),
)

#: Accès par nom. Construit une fois : chercher dans un tuple à chaque appel est
#: sans conséquence à quatorze, et deviendrait une habitude à cent.
_PAR_NOM: Mapping[str, Service] = {s.nom: s for s in SERVICES}


def service(nom: str) -> Service:
    """Le service déclaré sous ce nom.

    Lève plutôt que de rendre `None` : un nom inconnu ici est une faute de frappe
    dans du code, pas une donnée absente. Rendre `None` la ferait voyager jusqu'à
    un `AttributeError` loin de sa cause.
    """
    try:
        return _PAR_NOM[nom]
    except KeyError:
        raise KeyError(
            f"service inconnu : « {nom} ». Les services déclarés sont "
            f"{sorted(_PAR_NOM)}."
        ) from None


# ── Le graphe métier, établi flux par flux dans 10-flux-fonctionnels.md ───────

ARETES_AUTORISEES: dict[str, set[str]] = {
    "referentiel": set(),
    # K lit N parce qu'il héberge l'intergiciel qui fait office de passerelle : c'est lui
    # qui résout le sous-domaine en tenant avant toute chose. L'arête ne crée pas de cycle,
    # N ne déclarant aucune dépendance. Le jour où la passerelle sera un service à part,
    # elle disparaîtra avec l'intergiciel.
    "transverse": {"referentiel", "tenants"},
    "portefeuille": set(),
    "conformite": set(),
    "collecte": {"portefeuille", "conformite"},
    "comptabilite": {"portefeuille", "conformite", "collecte"},
    # ⚠️ F lit G pour **une seule question** depuis le pas 56 : le dossier a-t-il
    # employé quelqu'un sur la période ? La CNPS et les retenues sur salaires en
    # dépendent, quel que soit le régime. Sans cette arête, un booléen faux par défaut
    # les rendait invisibles partout. La lecture ne bloque jamais : le social en panne
    # laisse l'échéancier entier, obligations sociales marquées « à confirmer ».
    "obligations": {"portefeuille", "comptabilite", "conformite", "collecte", "social"},
    "social": {"portefeuille"},
    "cloture": {"portefeuille", "comptabilite", "conformite", "collecte", "obligations"},
    "creation_entreprise": {"portefeuille", "conformite"},
    "pilotage": {
        "portefeuille",
        "conformite",
        "collecte",
        "comptabilite",
        "obligations",
        "cloture",
        "creation_entreprise",
        "social",
    },
    # La vitrine ne lit aucun contexte métier, et c'est structurant : du contenu
    # éditorial qui aurait besoin d'un paramètre légal ne serait plus du contenu,
    # ce serait un calcul — et il appartiendrait au contexte qui le porte. Le jour
    # où l'on voudrait afficher un barème sur le site, la bonne réponse sera une
    # route du Référentiel appelée par le site, pas une arête ajoutée ici.
    "vitrine": set(),
    # M · Souscription lit le portefeuille pour une seule question : ce NIU est-il
    # déjà suivi ? Elle évite de vendre deux fois le même suivi à la même
    # entreprise. Il ne lit rien d'autre, et surtout n'écrit nulle part : une
    # souscription dit qu'un accès a été payé, pas que l'entreprise est connue.
    # Voir Docs/architecture/10-flux-fonctionnels.md.
    "souscription": {"portefeuille"},
    # N appartient au socle, au même titre que A et K, et pour la même raison : il ne
    # dépend de rien. Le plan de contrôle décide qu'un sous-domaine répond et à qui ;
    # la passerelle le lit à chaque requête, et un contexte métier peut légitimement
    # avoir besoin de savoir que son tenant est suspendu.
    #
    # Il ne lit même pas le référentiel : un slug n'a pas de fondement légal, une
    # suspension non plus.
    "tenants": set(),
}


def autorises_pour(nom: str) -> set[str]:
    """Les services que celui-ci a le droit de lire, socle compris.

    Le socle est ajouté implicitement plutôt que recopié dans chaque entrée : le
    recopier ferait dix entrées identiques où une seule oubliée passerait
    inaperçue, et rendrait illisible ce qui est une vraie dépendance métier.

    ⚠️ Un service **du socle** n'hérite pas de ce privilège : voir `SOCLE`, un socle
    qui lirait le métier créerait un cycle à l'exécution.
    """
    if nom in SOCLE:
        return set(ARETES_AUTORISEES[nom])
    return ARETES_AUTORISEES[nom] | (SOCLE - {nom})


def dependances_transitives(nom: str) -> set[str]:
    """Tout ce dont ce service dépend, directement ou non.

    ⚠️ **Le parcours retient les visités**, et pas seulement pour la vitesse : le
    graphe est acyclique aujourd'hui, et le test d'architecture le garde ainsi,
    mais une fonction qui boucle indéfiniment sur un cycle transformerait une
    erreur de déclaration en processus qui ne rend jamais la main. Un registre qui
    fige le service est pire qu'un registre qui se trompe.
    """
    vus: set[str] = set()
    a_voir = list(autorises_pour(nom))
    while a_voir:
        courant = a_voir.pop()
        if courant in vus or courant == nom:
            continue
        vus.add(courant)
        a_voir.extend(autorises_pour(courant))
    return vus


def qui_tombe_avec(nom: str) -> set[str]:
    """Les services qui cessent de fonctionner si celui-ci s'arrête.

    ─────────────────────────────────────────────────────────────────────────────
    C'EST LA QUESTION QU'AUCUN TABLEAU DE BORD NE SAIT POSER

    « Le Référentiel ne répond plus » n'apprend rien à qui doit décider s'il faut
    réveiller quelqu'un à trois heures du matin. « Le Référentiel ne répond plus,
    et onze services en dépendent, dont la Comptabilité et les Obligations »
    l'apprend.

    ⚠️ C'est la **fermeture inverse** du graphe, et non la liste des voisins
    directs. La Clôture ne lit pas le Référentiel directement ; elle lit la
    Comptabilité, qui le lit. Elle tombe quand même, et ne pas le dire donnerait
    une fausse tranquillité.

    Ce que cette fonction ne dit pas : combien de temps chacun tient. Un service
    qui garde une projection peut survivre des heures à l'arrêt de sa source. Le
    registre ne le sait pas, ne prétend pas le savoir, et c'est pourquoi il rend
    « qui dépend » et non « qui est mort ».
    ─────────────────────────────────────────────────────────────────────────────
    """
    return {
        autre.nom
        for autre in SERVICES
        if autre.nom != nom and nom in dependances_transitives(autre.nom)
    }
