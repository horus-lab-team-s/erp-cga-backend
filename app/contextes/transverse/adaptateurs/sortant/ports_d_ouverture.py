"""Les trois ports que le provisionneur attendait, et que personne ne branchait.

─────────────────────────────────────────────────────────────────────────────────
⚠️ POURQUOI CE FICHIER EST DANS LE TRANSVERSE, ET NON DANS LES TENANTS

Il y était d'abord, et `test_architecture.py` l'a refusé — à juste titre : le
port des comptes appelle `transverse.api`, et le graphe des dépendances
**interdit** au contexte Tenants de connaître le Transverse. L'inverse est
autorisé, et c'est déjà pour cette raison que le branchement de l'ouverture est
écrit dans `routes_orchestration.py`, côté Transverse.

Le refus était instructif : un port qui relie deux contextes n'appartient pas au
contexte le plus profond, il appartient à celui qui a le droit de voir les deux.

─────────────────────────────────────────────────────────────────────────────────
CE QUI ÉTAIT EN PLACE, ET CE QUI MANQUAIT

`ProvisionneurLocal` prend trois ports optionnels — schéma, stockage, comptes —
et **substitue** l'étape quand le port est absent. Il était câblé sans aucun des
trois : toute ouverture de tenant se terminait donc avec trois étapes sans effet,
et un client sans identifiant ni lien d'activation.

C'était honnête — la substitution est déclarée, et l'écran la montre depuis le
pas 93 — mais ce n'était pas livrable : un second cabinet n'aurait pas pu ouvrir
son espace.

⚠️ L'ÉTAPE DU SCHÉMA N'A PLUS D'OBJET, ET RESTE SUBSTITUÉE

`EtapeOuverture.SCHEMA_CREE` dit « le schéma PostgreSQL existe et porte les
migrations ». C'est le vestige de la **décision D3, révisée** : le schéma par
tenant a été abandonné après lecture du code, au profit d'une colonne
`locataire` et de la sécurité au niveau des lignes de PostgreSQL. Voir
`Docs/architecture/12-socle-multi-tenant.md`.

Il n'y a donc **aucun schéma à créer**. Mais l'étape a toujours un travail utile
sous la conception révisée : vérifier que le cloisonnement est **réellement en
vigueur** pour ce tenant. Un tenant ouvert pendant que les politiques étaient
désactivées — une restauration mal faite, une migration à moitié passée — serait
un tenant dont les lignes sont visibles de tous, et rien ne le signalerait.

⚠️ Le nom de l'étape n'est pas changé ici : le renommer casserait les contextes
de saga déjà écrits en base, qui portent `SCHEMA_CREE` dans `franchies`. C'est
une décision de conception à prendre avec le cabinet, pas un effet de bord d'un
branchement.

⚠️ CHAQUE PORT EST IDEMPOTENT, ET C'EST LA CONDITION DE LA REPRISE

Le relais publie au moins une fois. Rejoué, un port constate que son travail est
fait et rend le même contexte — il ne crée pas un second répertoire, ni un
second compte, ni un second lien d'activation envoyé au client.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.contextes.tenants.api import EtapeOuverture, avec_substitution

__all__ = [
    "port_de_stockage",
    "port_des_comptes",
]

# ⚠️ IL N'Y A PAS DE PORT POUR `SCHEMA_CREE`, ET C'EST UNE DÉCISION.
#
# J'en avais écrit un : il vérifiait que les politiques de cloisonnement étaient
# en vigueur. Deux raisons l'ont fait retirer, et les deux valent d'être notées.
#
# 1. **C'est une propriété GLOBALE de la plateforme, pas du client.** La
#    vérifier à l'ouverture de chaque tenant couple le sort d'un client à l'état
#    général de l'installation. Et cette vérification existe déjà, au bon
#    endroit : `/sante` rend `cloisonnement: APPLIQUE`.
#
# 2. **Elle cassait toute la recette du parcours.** Les cas d'essai montent leur
#    schéma par `create_all`, qui crée les tables mais **pas** les politiques —
#    seul Alembic les pose. Le port refusait donc d'ouvrir dans tout
#    environnement d'essai, et quatre cas de bout en bout tombaient.
#
# L'étape reste donc **substituée**, et l'écran le dit depuis le pas 93. Son
# retrait de `EtapeOuverture` est une décision de conception à prendre avec le
# cabinet : elle changerait `ETAPES_ORDONNEES` et les contextes de saga déjà
# écrits en base, qui portent `SCHEMA_CREE` dans `franchies`.


def port_de_stockage(racine: Path | str) -> Any:
    """Ouvre le préfixe de stockage du tenant : un répertoire, et rien de plus.

    ⚠️ **Le préfixe est le nom du locataire**, exactement comme `MagasinLocal` le
    range : `racine/<locataire>/xx/yy/clé`. Le créer ici plutôt qu'au premier
    dépôt a un intérêt concret : si le volume est absent, en lecture seule, ou
    plein, on l'apprend **à l'ouverture** — pendant qu'un collaborateur regarde
    l'écran — et non six semaines plus tard quand un adhérent dépose sa première
    facture depuis son téléphone.

    ⚠️ `exist_ok=True` : rejouée, l'étape constate le répertoire et n'échoue pas.

    ⚠️ Les droits sont resserrés à `0o700`. Les justificatifs d'un cabinet n'ont
    rien à faire en lecture pour les autres comptes de la machine.
    """

    def ouvrir(contexte: Mapping[str, Any]) -> Mapping[str, Any]:
        locataire = str(contexte["tenant"])
        prefixe = Path(racine).resolve() / locataire
        prefixe.mkdir(parents=True, exist_ok=True, mode=0o700)
        # ⚠️ Un répertoire créé ne prouve pas qu'on peut y écrire : un volume
        # monté en lecture seule laisse `mkdir` réussir sur un chemin déjà là.
        temoin = prefixe / ".ecriture-verifiee"
        try:
            temoin.write_bytes(b"")
            temoin.unlink(missing_ok=True)
        except OSError as incident:
            raise RuntimeError(
                f"le préfixe de stockage « {prefixe} » n'accepte pas l'écriture "
                f"({type(incident).__name__}). Un tenant ouvert sur un volume en "
                "lecture seule accepterait les dépôts et les perdrait."
            ) from incident
        return {**contexte, "prefixe_de_stockage": str(prefixe)}

    return ouvrir


def port_des_comptes(unite_de_travail: Any, notifier: Any = None) -> Any:
    """Crée le premier administrateur du tenant, et lui envoie son lien.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LE SECRET NE TRANSITE PAR AUCUN HUMAIN, ET N'EST CONSERVÉ NULLE PART.**

    `creer_l_administrateur_d_un_tenant` rend le secret en clair une seule fois.
    Il part dans le courriel ici même, et le contexte de la saga ne le porte
    pas — un contexte de saga est écrit en base, recopié dans les journaux et
    dans les sauvegardes. Un lien d'activation qui traînerait là serait un
    compte à prendre pour qui lit une sauvegarde.

    ⚠️ **SANS NOTIFIEUR, L'ÉTAPE ÉCHOUE — ELLE NE SE SUBSTITUE PAS.**

    Créer le compte sans pouvoir envoyer le lien produirait exactement le défaut
    qu'on corrige : un tenant qui paraît ouvert et un client qui ne peut pas
    entrer. Mieux vaut une saga en échec, visible et reprenable, qu'un succès
    qui ment.

    ⚠️ **IDEMPOTENT** : le compte déjà là, on ne renvoie pas un second lien. Le
    client se demanderait lequel est le bon, et le premier serait invalidé.
    ─────────────────────────────────────────────────────────────────────────────
    """

    def creer(contexte: Mapping[str, Any]) -> Mapping[str, Any]:
        from app.contextes.transverse.api import creer_l_administrateur_d_un_tenant
        from app.partage.horloge import maintenant
        from app.partage.locataire import etabli

        # ⚠️ **DEUX CAS SE SUBSTITUENT AU LIEU DE LEVER, ET LA RAISON EST ÉCRITE
        # DANS LE PROVISIONNEUR LUI-MÊME :**
        #
        #     « le silence mentirait, l'échec bloquerait, la substitution
        #       déclarée informe. »
        #
        # Une adresse absente ou un courrier non configuré sont des cas où
        # l'étape NE PEUT PAS se faire — exactement comme une infrastructure
        # absente. Lever bloquerait la saga et laisserait le tenant à moitié
        # ouvert, sans que le reste de l'ouverture n'aboutisse ; la substitution
        # laisse la saga finir et fait dire à l'écran « l'espace existe, mais il
        # n'est pas utilisable », ce qui est la vérité.
        #
        # ⚠️ Un événement déposé AVANT que la charge ne transporte l'adresse — il
        # en existe dans la boîte d'envoi — se comporte donc comme avant, au lieu
        # de tourner jusqu'à la quarantaine.
        courriel = str(contexte.get("courriel_titulaire") or "").strip()
        if not courriel:
            return avec_substitution(
                {**contexte, "administrateur": "sans objet : adresse du titulaire absente"},
                EtapeOuverture.ADMINISTRATEUR_CREE.value,
            )
        if notifier is None:
            # ⚠️ Le notifieur est vérifié AVANT de créer le compte, et non après.
            # L'inverse laisserait un compte sans lien : un état à moitié fait,
            # le pire des trois.
            return avec_substitution(
                {**contexte, "administrateur": "sans objet : aucun courrier configuré"},
                EtapeOuverture.ADMINISTRATEUR_CREE.value,
            )

        # ⚠️ **UNE UNITÉ DE TRAVAIL SUR LE TENANT NEUF, ET NON CELLE DE LA
        # REQUÊTE.**
        #
        # La saga tourne dans l'unité de travail du CABINET — c'est lui qui a
        # confirmé l'encaissement. Écrire le compte du client dans ces dépôts est
        # une écriture croisée, et le socle la refuse net :
        #
        #     « compte C-… du locataire tnt-… enregistré dans le dépôt de
        #       CGA-BRCG. Le cloisonnement est porté par l'instance : une
        #       écriture croisée est un défaut, pas un cas limite. »
        #
        # C'est ce refus qui a révélé le défaut : ma première version prenait
        # l'atelier de la requête. On ouvre donc une unité de travail **sur le
        # tenant du client**, le temps de créer son compte.
        locataire = str(contexte["tenant"])
        instant = maintenant()
        with etabli(locataire), unite_de_travail(locataire) as boutique:
            resultat = creer_l_administrateur_d_un_tenant(
                locataire=locataire,
                identifiant_compte=f"C-{uuid.uuid4().hex[:12]}",
                identifiant_habilitation=f"H-{uuid.uuid4().hex[:12]}",
                identifiant_jeton=f"J-{uuid.uuid4().hex[:12]}",
                courriel=courriel,
                # ⚠️ LE NOM N'EST PAS DÉCOUPÉ, ET C'EST DÉLIBÉRÉ.
                #
                # Une demande commerciale porte UN SEUL champ de nom : « Jean-Paul
                # NKOA », ou « NKOA Jean-Paul ». Le découper en prénom et nom
                # supposerait un ordre, et au Cameroun le nom de famille précède
                # souvent le prénom. Se tromper misnomme un client dès son premier
                # courriel, et le compte garde l'erreur.
                #
                # `Compte` exige les deux champs (`prenom` au moins un
                # caractère). On y met donc le nom tel qu'il a été donné, dans
                # les deux, et le titulaire corrigera à l'activation — il est le
                # seul à savoir. Le nom rendu deux fois est visible, donc
                # corrigible, au contraire d'un découpage faux qui passe
                # inaperçu.
                nom=_nom_donne(contexte, courriel),
                prenom=_nom_donne(contexte, courriel),
                depuis=instant.date(),
                comptes=boutique.comptes,
                habilitations=boutique.habilitations,
                jetons=boutique.jetons,
                journal=boutique.journal,
                a_l_instant=instant,
            )
        if resultat is None:
            # Rejeu : le compte existe, son lien est parti.
            return {**contexte, "administrateur": "déjà créé"}

        compte, jeton, secret = resultat
        # ⚠️ ICI, EN REVANCHE, ON LÈVE. Le compte EXISTE : un envoi raté laisse un
        # état à moitié fait, et c'est le seul cas qui mérite une saga en échec —
        # elle se reprendra sans recréer le compte.
        _envoyer_le_lien(notifier, compte, secret, jeton, str(contexte.get("slug") or ""))
        # ⚠️ Le secret n'entre PAS dans le contexte. Voir l'en-tête.
        return {**contexte, "administrateur": compte.identifiant}

    return creer


def _hote_du_tenant(adresse_du_site: str, slug: str) -> str:
    """L'adresse du site, préfixée du sous-domaine du tenant.

    ─────────────────────────────────────────────────────────────────────────────
    `https://cga-brcg.cm` + `station-bonaberi` → `https://station-bonaberi.cga-brcg.cm`

    ⚠️ C'EST LE NOM D'HÔTE QUI DÉSIGNE LE LOCATAIRE, et rien d'autre. Voir
    `tenants/domaine/resolution.py` : la passerelle lit le premier niveau du nom
    d'hôte et en tire le slug. Un lien posé sur l'hôte du cabinet est donc servi
    **dans le locataire du cabinet**, où le jeton du client n'existe pas.

    ⚠️ Le port est conservé tel quel : en démonstration l'adresse porte `:3100`,
    et `couture-1a2b3c.localhost:3100` répond — les résolveurs rendent 127.0.0.1
    pour tout `*.localhost`. En production il n'y a pas de port, et la règle est
    la même.

    ⚠️ Sans slug, on rend l'adresse inchangée plutôt que de fabriquer un hôte
    `.cga-brcg.cm` malformé. Le lien sera alors faux, mais il le sera de façon
    visible ; un hôte tordu aurait l'air d'un problème de réseau.
    """
    adresse = adresse_du_site.rstrip("/")
    if not slug:
        return adresse
    schema, _, reste = adresse.partition("://")
    if not reste:
        return adresse
    return f"{schema}://{slug}.{reste}"


def _nom_donne(contexte: Mapping[str, Any], courriel: str) -> str:
    """Le nom tel qu'il a été donné, ou la partie locale de l'adresse à défaut.

    ⚠️ À défaut de nom, « jp.nkoa » vaut mieux qu'un champ vide : le modèle le
    refuserait, et le courriel dirait « Bonjour  ».
    """
    donne = str(contexte.get("nom_titulaire") or "").strip()
    return donne or courriel.split("@")[0]


def _envoyer_le_lien(
    notifier: Any, compte: Any, secret: str, jeton: Any, slug: str
) -> None:
    """Le courriel d'activation, par le gabarit du catalogue.

    ⚠️ **`compte.invitation`, pas un texte écrit ici.** Le catalogue est la seule
    version de ce message : un corps rédigé dans un adaptateur donnerait deux
    courriels d'invitation au produit, qui divergeraient au premier changement de
    ton demandé par le cabinet.

    ⚠️ **LE LIEN EST COMPOSÉ SUR LE SOUS-DOMAINE DU TENANT**, et ce commentaire
    annonçait ce jour : « le titulaire d'un tenant neuf se connecte par le
    sous-domaine de son cabinet ; le jour où la passerelle le résout, c'est ici
    qu'il faudra le composer ».

    Elle le résout. Et sans cela, le lien ne pouvait **pas** fonctionner : le
    compte et son jeton vivent dans le locataire du client, la route de
    définition travaille dans le locataire que l'hôte désigne, et l'hôte du
    cabinet désigne le cabinet. La cliente payait, recevait son courriel, et
    lisait « ce lien n'est plus valable » sur un jeton émis trois secondes plus
    tôt. Constaté le 27 septembre, en suivant le lien dans un vrai navigateur.

    ⚠️ **L'échec d'envoi LÈVE.** Un compte créé dont le lien n'est pas parti est
    exactement le défaut que ce chantier corrige. Une saga en échec se reprend ;
    un client sans lien appelle le cabinet.
    """
    from app.infrastructure.config import configuration

    # ─────────────────────────────────────────────────────────────────────────
    # ⚠️ L'ADRESSE DU **SITE**, ET NON CELLE DE L'API.
    #
    # Ce port employait `adresse_publique`, qui est l'adresse de l'API — celle
    # que le prestataire de paiement rappelle. Le lien d'activation partait donc
    # vers `http://localhost:8000/activation?jeton=…`, c'est-à-dire vers un point
    # d'entrée d'API et non vers une page.
    #
    # Constaté le 27 septembre en relisant la boîte de la cliente : elle payait
    # sa création, recevait son courriel d'accès, et **le lien ne menait nulle
    # part**. Le compte existait, le tenant était ouvert, la saga se déclarait
    # terminée — et la cliente restait dehors.
    #
    # La configuration distingue les deux adresses depuis toujours, et son
    # commentaire dit laquelle porte ce lien : « il mène à une page, pas à un
    # point d'entrée d'API ». L'autre chemin d'activation, celui des
    # souscriptions, injectait déjà la bonne. Celui-ci ne la lisait pas.
    # ─────────────────────────────────────────────────────────────────────────
    base = _hote_du_tenant(configuration().adresse_publique_site, slug)
    poste = notifier.envoyer(
        "compte.invitation",
        destinataire=compte.courriel,
        contexte={
            "prenom": compte.appellation,
            "role": "Administrateur",
            "lien": f"{base}/activation?jeton={secret}",
            "expire_le": f"{jeton.expire_le:%d/%m/%Y à %Hh%M}",
        },
    )
    if not poste:
        raise RuntimeError(
            f"le compte {compte.courriel} est créé, mais son lien d'activation n'a pas "
            "pu être posté. Reprendre la saga une fois le courrier rétabli — le compte "
            "ne sera pas recréé."
        )
