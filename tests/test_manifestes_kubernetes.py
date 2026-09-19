"""Les manifestes de déploiement, confrontés au code qu'ils déploient.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI DES CAS SUR DES FICHIERS YAML

Parce qu'un manifeste ment sans rien casser.

⚠️ Une variable `CGA_ORDONNANCEUR_EN_PROCESUS` — un « S » de moins — est acceptée
par l'orchestrateur, ignorée par l'application, et la boucle de fond ne démarre
jamais. Aucune erreur, aucun journal, aucun test : simplement des relances qui ne
partent pas et des tenants qui ne s'ouvrent pas, découverts par un client.

Ces cas ont été écrits après que le contrôle en a trouvé **deux dans mes propres
manifestes** : `CGA_SECRET_JETONS`, qui n'existe pas — la vraie clé est
`CGA_CLE_CHIFFREMENT` —, et une seconde adresse de base nommée comme une variable
d'environnement alors que c'est une clé de secret.

*Un fichier de configuration qui nomme une clé inexistante ne fait rien, en
silence. C'est la panne la moins chère à éviter et la plus chère à trouver.*
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re

import pytest
import yaml

from app.infrastructure.config import RACINE_DEPOT, Configuration

MANIFESTES = RACINE_DEPOT / "deploiement" / "kubernetes"

#: Le préfixe que la configuration impose à toutes ses variables.
PREFIXE = "CGA_"


def _documents() -> list[dict]:
    trouves: list[dict] = []
    for fichier in sorted(MANIFESTES.glob("*.yaml")):
        trouves += [d for d in yaml.safe_load_all(fichier.read_text(encoding="utf-8")) if d]
    return trouves


def _par_genre(genre: str, nom: str) -> dict:
    for document in _documents():
        if document["kind"] == genre and document["metadata"]["name"] == nom:
            return document
    raise AssertionError(f"aucun {genre} nommé {nom}")


class TestLesManifestesSontLisibles:
    def test_ils_se_lisent_tous(self):
        """⚠️ La contre-épreuve de tous les autres cas : sans documents, ils
        passeraient tous en ne vérifiant rien."""
        documents = _documents()
        assert len(documents) >= 8, documents

    def test_les_genres_attendus_sont_tous_presents(self):
        genres = {d["kind"] for d in _documents()}
        assert genres >= {
            "Namespace",
            "ConfigMap",
            "Secret",
            "PersistentVolumeClaim",
            "Job",
            "Deployment",
            "Service",
            "Ingress",
        }


class TestAucuneCleInventee:
    """⚠️ **Le cas qui a trouvé deux défauts dans les manifestes qu'il garde.**"""

    def test_chaque_variable_existe_dans_la_configuration(self):
        connues = {f"{PREFIXE}{nom.upper()}" for nom in Configuration.model_fields}
        employees: set[str] = set()
        for fichier in sorted(MANIFESTES.glob("*.yaml")):
            employees |= set(
                re.findall(rf"\b{PREFIXE}[A-Z0-9_]+\b", fichier.read_text(encoding="utf-8"))
            )

        inventees = sorted(employees - connues)
        assert not inventees, (
            f"ces variables ne correspondent à aucun champ de `Configuration` : "
            f"{inventees}. L'orchestrateur les accepte, l'application les ignore, "
            "et le réglage voulu ne s'applique jamais."
        )

    def test_le_balayage_trouve_bien_des_variables(self):
        """Sans cela, un chemin faux ferait passer le cas précédent en ne
        balayant rien."""
        employees: set[str] = set()
        for fichier in sorted(MANIFESTES.glob("*.yaml")):
            employees |= set(
                re.findall(rf"\b{PREFIXE}[A-Z0-9_]+\b", fichier.read_text(encoding="utf-8"))
            )
        assert len(employees) >= 6, employees


class TestAucunSecretVersionne:
    def test_le_gabarit_de_secret_ne_porte_aucune_valeur(self):
        """⚠️ Un mot de passe versionné reste dans l'historique après correction,
        part avec chaque clone, et survit à une rotation."""
        secret = _par_genre("Secret", "cga-secrets")
        valeurs = {c: v for c, v in secret["stringData"].items() if v}
        assert not valeurs, f"valeurs versionnées : {sorted(valeurs)}"

    def test_aucune_adresse_de_base_complete_ne_traine(self):
        """Une chaîne de connexion porte l'hôte, l'utilisateur et le mot de passe.
        Aucun manifeste ne doit en contenir une qui soit utilisable."""
        for fichier in sorted(MANIFESTES.glob("*.yaml")):
            texte = fichier.read_text(encoding="utf-8")
            for trouve in re.findall(r"postgresql\+psycopg://[^\s'\"]+", texte):
                # Les exemples de commande portent des points de suspension : ce
                # sont des gabarits, et ils doivent le rester.
                assert "..." in trouve, f"{fichier.name} : adresse complète {trouve}"


class TestLesSondes:
    """⚠️ **La décision centrale du déploiement, et elle tient en une ligne.**

    `/sante` interroge la base et rend 503 quand elle est injoignable. C'est ce
    qu'il faut pour la **disponibilité** — le pod sort du service. C'est
    exactement ce qu'il ne faut pas pour la **vivacité** : redémarrer un processus
    parce que la base est tombée ne répare rien, et transforme une panne de base
    en flotte entière en redémarrage, journaux perdus à chaque cycle.
    """

    @pytest.mark.parametrize("deploiement", ["cga-api", "cga-ordonnanceur"])
    def test_la_vivacite_ne_teste_jamais_la_base(self, deploiement):
        conteneur = _par_genre("Deployment", deploiement)["spec"]["template"]["spec"][
            "containers"
        ][0]
        vivacite = conteneur["livenessProbe"]
        assert "httpGet" not in vivacite, (
            f"{deploiement} : la sonde de vivacité interroge une route HTTP. "
            "Si cette route touche la base, une panne de base devient une panne "
            "générale."
        )
        assert "tcpSocket" in vivacite

    def test_l_api_sort_du_service_quand_la_base_tombe(self):
        """La contre-épreuve : sans sonde de disponibilité sur `/sante`, un pod
        dont la base est injoignable recevrait des adhérents et leur rendrait des
        500, le tableau de bord restant vert."""
        conteneur = _par_genre("Deployment", "cga-api")["spec"]["template"]["spec"][
            "containers"
        ][0]
        assert conteneur["readinessProbe"]["httpGet"]["path"] == "/sante"

    def test_le_demarrage_a_sa_propre_sonde(self):
        """⚠️ Confondre démarrage et vivacité oblige à choisir entre un démarrage
        étranglé et une panne détectée trois minutes trop tard."""
        conteneur = _par_genre("Deployment", "cga-api")["spec"]["template"]["spec"][
            "containers"
        ][0]
        assert "startupProbe" in conteneur


class TestCeQuiDoitEtreVraiPourQueLaPlateformeFonctionne:
    def test_le_volume_des_justificatifs_est_partageable(self):
        """⚠️ `ReadWriteOnce` marche à une réplique et casse à la seconde — le
        deuxième pod reste en attente de montage, sans message utile.

        Le défaut n'apparaît jamais en essai, et toujours le jour où la charge
        monte.
        """
        pvc = _par_genre("PersistentVolumeClaim", "cga-fichiers-deposes")
        assert pvc["spec"]["accessModes"] == ["ReadWriteMany"]

    def test_les_deux_deploiements_montent_le_meme_volume(self):
        """Une pièce déposée par l'API doit être lisible par l'ordonnanceur."""
        for nom in ("cga-api", "cga-ordonnanceur"):
            volumes = _par_genre("Deployment", nom)["spec"]["template"]["spec"]["volumes"]
            revendications = {
                v["persistentVolumeClaim"]["claimName"]
                for v in volumes
                if "persistentVolumeClaim" in v
            }
            assert "cga-fichiers-deposes" in revendications, nom

    def test_seul_l_ordonnanceur_fait_tourner_la_boucle(self):
        """⚠️ Le drapeau est faux dans la configuration partagée et vrai dans le
        seul déploiement qui doit l'avoir.

        Vrai partout, chaque réplique de l'API se réveillerait toutes les cinq
        secondes pour contendre sur le verrou consultatif. Rien de faux — le
        verrou tient — mais du bruit qu'on finit par cesser de lire.
        """
        commun = _par_genre("ConfigMap", "cga-configuration")["data"]
        assert commun["CGA_ORDONNANCEUR_EN_PROCESSUS"] == "false"

        ordonnanceur = _par_genre("Deployment", "cga-ordonnanceur")["spec"]["template"][
            "spec"
        ]["containers"][0]
        drapeau = {v["name"]: v.get("value") for v in ordonnanceur["env"]}
        assert drapeau["CGA_ORDONNANCEUR_EN_PROCESSUS"] == "true"

        api = _par_genre("Deployment", "cga-api")["spec"]["template"]["spec"][
            "containers"
        ][0]
        assert "env" not in api or all(
            v["name"] != "CGA_ORDONNANCEUR_EN_PROCESSUS" for v in api.get("env", [])
        )

    def test_la_migration_est_un_travail_et_non_un_conteneur_d_initialisation(self):
        """⚠️ Un conteneur d'initialisation tourne sur **chaque** pod : trois
        répliques, trois migrations simultanées, et une migration qui s'exécute
        pendant qu'une ancienne version sert encore le trafic."""
        _par_genre("Job", "cga-migration")
        for nom in ("cga-api", "cga-ordonnanceur"):
            pod = _par_genre("Deployment", nom)["spec"]["template"]["spec"]
            assert "initContainers" not in pod, nom

    def test_la_migration_emploie_un_autre_role_que_l_application(self):
        """⚠️ **La séparation dont dépend tout le cloisonnement.**

        `cga_migration` possède les tables ; `cga_app` ne les possède pas, donc ne
        contourne pas leurs politiques. Le même rôle pour les deux rend le
        cloisonnement inopérant sans qu'aucune erreur ne se produise — c'est le
        piège que `roles.py` diagnostique au démarrage.
        """
        conteneur = _par_genre("Job", "cga-migration")["spec"]["template"]["spec"][
            "containers"
        ][0]
        adresse = {v["name"]: v for v in conteneur["env"]}["CGA_URL_BASE_DE_DONNEES"]
        cle = adresse["valueFrom"]["secretKeyRef"]["key"]
        assert cle != "CGA_URL_BASE_DE_DONNEES", (
            "la migration emploie la même clé de secret que l'application, donc "
            "le même rôle : le cloisonnement serait contourné en silence."
        )

    def test_l_entree_porte_un_certificat_generique(self):
        """⚠️ Sans générique, chaque nouveau cabinet demanderait un certificat,
        donc une intervention, et l'ouverture d'un tenant cesserait d'être
        immédiate — toute la saga d'ouverture perdrait son intérêt.

        ⚠️ Un générique n'est délivrable que par DNS-01 : c'est une contrainte sur
        le choix du registrar, et elle se décide avant l'achat du domaine.
        """
        entree = _par_genre("Ingress", "cga")
        noms = {n for tls in entree["spec"]["tls"] for n in tls["hosts"]}
        assert any(n.startswith("*.") for n in noms), noms

        emetteur = entree["metadata"]["annotations"]["cert-manager.io/cluster-issuer"]
        assert "dns01" in emetteur, (
            f"émetteur « {emetteur} » : un certificat générique ne s'obtient pas "
            "par HTTP-01, qui prouve la possession d'un nom en le servant."
        )

    def test_aucune_image_ne_porte_une_etiquette_mouvante(self):
        """⚠️ `:latest` rend un déploiement irreproductible et un retour en
        arrière impossible : deux pods de la même révision peuvent porter deux
        codes différents."""
        for document in _documents():
            if document["kind"] not in ("Deployment", "Job"):
                continue
            for conteneur in document["spec"]["template"]["spec"]["containers"]:
                assert not conteneur["image"].endswith(":latest"), conteneur["image"]
