"""Configuration de l'application."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Racine du dépôt : Backend_erp_cga/app/core/config.py → trois niveaux au-dessus.
RACINE_DEPOT = Path(__file__).resolve().parents[3]


class Configuration(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CGA_", env_file=".env", extra="ignore")

    nom_application: str = "Plateforme CGA Broad Range Consulting Group"
    environnement: str = "developpement"

    #: Le référentiel est aujourd'hui un dossier de fichiers YAML versionnés en Git.
    #: Cible : une table PostgreSQL éditable par le fiscaliste. Le service de lecture ne
    #: connaît pas l'origine — voir Docs/architecture/02-referentiel-normatif.md § 3.
    dossier_referentiel: Path = RACINE_DEPOT / "Docs" / "referentiel"

    #: Le contenu éditorial de la vitrine — articles, annonces, institutions.
    #: Volontairement **hors de Docs/** : ce n'est pas de la documentation de
    #: projet, c'est la matière que le cabinet édite lui-même. Un dossier à la
    #: racine se trouve sans explication, et se sauvegarde sans se demander ce
    #: qu'on emporte. Cible : une table PostgreSQL et un écran d'administration —
    #: seul l'adaptateur sortant du contexte L changera ce jour-là.
    dossier_contenu_vitrine: Path = RACINE_DEPOT / "Contenu_vitrine"

    #: Où sont rangés les justificatifs déposés. ⚠️ **À sauvegarder.** Sans
    #: copie de ce dossier, une panne disque fait perdre les pièces de tous les
    #: adhérents, et la comptabilité qui s'appuie dessus devient indéfendable
    #: devant un vérificateur. Voir `collecte/adaptateurs/sortant/magasin_local.py`.
    #:
    #: Hors du dépôt Git, et volontairement : ce sont les documents du cabinet,
    #: pas du code. En conteneur, un volume doit y être monté — sinon les pièces
    #: disparaissent au redéploiement.
    dossier_fichiers: Path = RACINE_DEPOT / "Fichiers_deposes"

    origines_cors: list[str] = ["http://localhost:3000"]

    #: L'adresse publique de l'API. Sert à construire l'adresse de rappel que le
    #: prestataire de paiement appellera. Elle doit être joignable depuis
    #: l'extérieur : en développement, aucune notification n'arrivera sur
    #: `localhost`, et c'est la raison d'être du mode simulé.
    adresse_publique: str = "http://localhost:8000"

    #: L'adresse publique du **site**, distincte de celle de l'API. C'est elle
    #: qui porte le lien d'activation reçu par courriel : il mène à une page,
    #: pas à un point d'entrée d'API.
    adresse_publique_site: str = "http://localhost:3000"

    #: Où vivent les données. `memoire` amorce le jeu de démonstration et perd
    #: tout au redémarrage ; `postgresql` persiste.
    #:
    #: Les treize dépôts sont désormais migrés — la condition qui retenait ce
    #: défaut est levée. Il reste néanmoins `memoire`, pour une autre raison :
    #: un développeur qui clone le dépôt doit pouvoir lancer l'application sans
    #: installer PostgreSQL, et le jeu de démonstration s'amorce seul.
    #:
    #: ⚠️ La **production** refuse de démarrer sur ce défaut — voir
    #: `_exigences_production`. C'est ce qui rend la commodité sans danger.
    #: Le domaine sous lequel les sous-domaines de tenants sont servis. La passerelle
    #: en extrait le slug : `station.cga.cm` sous la racine `cga.cm` désigne `station`.
    #:
    #: Un nom d'hôte qui n'en dépend pas ne porte aucun slug — ni `localhost`, ni
    #: `testserver`, ni le domaine nu. La requête retombe alors sur le locataire par
    #: défaut ci-dessous, ce qui est l'affordance de développement et rien de plus.
    domaine_racine: str = "cga.cm"

    #: Le locataire servi quand la requête n'en désigne pas d'autre. Tant que la
    #: plateforme ne sert qu'un centre, c'est le sien ; le jour où le sous-domaine
    #: le désignera, ce n'est plus qu'un repli de développement.
    #:
    #: Ici plutôt que dans un module de dépôts, parce que c'est une valeur de
    #: déploiement : elle change avec l'installation, pas avec le code.
    locataire_par_defaut: str = "CGA-BRCG"

    #: Combien de secondes le répertoire des tenants peut rester périmé.
    #:
    #: ─────────────────────────────────────────────────────────────────────────────
    #: ⚠️ **C'est une fenêtre d'incohérence assumée, pas un réglage de performance.**
    #:
    #: Le répertoire vit dans chaque processus d'API, garni au démarrage. Un tenant
    #: ouvert, suspendu ou résilié pendant que le processus tourne ne s'y voit pas :
    #: le client qui vient de payer reçoit `404`, et le tenant suspendu continue
    #: d'être servi. Passé cette fenêtre, la première requête qui arrive recharge la
    #: table. Elle borne donc **le temps pendant lequel la plateforme peut se
    #: tromper**.
    #:
    #: Trente secondes : assez court pour qu'aucun humain ne le remarque — un lien
    #: d'activation met plus longtemps à arriver par courriel — et assez long pour
    #: que trois répliques qui servent mille requêtes par minute ne fassent que six
    #: lectures de la table par minute à elles trois.
    #:
    #: ⚠️ La mettre à zéro n'est pas admis : ce serait un aller-retour en base sur le
    #: chemin critique de **tout** le trafic, ce que le répertoire existe pour éviter.
    #: ─────────────────────────────────────────────────────────────────────────────
    fenetre_repertoire_tenants_secondes: int = Field(default=30, ge=1, le=3600)

    #: ⚠️ **Fermé à deux valeurs, et non `str`.** Une chaîne libre a laissé vivre
    #: pendant tout un chantier un `persistance != "sql"` dans le garnissage du
    #: répertoire des tenants : la production, qui exige `"postgresql"`, ne
    #: garnissait donc jamais, et tout sous-domaine rendait 404. Le défaut ne
    #: pouvait pas se voir, puisque la comparaison était syntaxiquement correcte
    #: et que le test forçait lui-même la valeur fautive.
    #:
    #: Un `Literal` déplace la faute à l'endroit où elle se règle : au chargement
    #: de la configuration, avec le nom du réglage et la liste des valeurs
    #: admises. Comparer à ces valeurs se fait par `en_base`, jamais à la main.
    persistance: Literal["memoire", "postgresql"] = "memoire"

    #: ⚠️ **La cadence du relais, en secondes.** Elle règle le délai entre un
    #: paiement encaissé et le sous-domaine qui répond : c'est le seul réglage de
    #: ce fichier qu'un client perçoit directement.
    #:
    #: Elle est ici et non au référentiel, et le partage se fait sur deux
    #: questions. « Est-ce que cela varie sans le code ? » — oui, d'un déploiement
    #: à l'autre. « Et qui décide ? » — l'exploitant, pas un fiscaliste. Le
    #: référentiel porte ce que la loi prévoit ; ceci est un réglage de machine.
    cadence_relais_secondes: int = 5

    #: La série des numéros de proforma. `PRO-2026-0001`, `PRO-2026-0002`…
    #:
    #: ⚠️ Un réglage et non une constante : un cabinet qui reprend une numérotation
    #: existante doit pouvoir la poursuivre, et un changement de série se décide au
    #: 1er janvier, pas à une livraison. La continuité est vérifiée par le domaine,
    #: quelle que soit la série choisie.
    serie_proforma: str = "PRO"

    #: Combien de jours un lien d'acceptation reste valide.
    #:
    #: ⚠️ **Un lien d'acceptation est un consentement contractuel à usage unique.**
    #: Trop court, le client trouve un lien mort et rappelle le cabinet ; trop long,
    #: un lien retrouvé dans une vieille boîte aux lettres engage encore. Quinze
    #: jours couvrent le délai de réflexion habituel sans laisser traîner un
    #: engagement.
    validite_lien_acceptation_jours: int = 15

    #: ⚠️ **La cadence du balayage de relance, en minutes.**
    #:
    #: Une heure par défaut, et le choix se justifie par ce que le balayage produit :
    #: des messages à de vrais clients, selon un plan dont les paliers se comptent en
    #: **jours**. Balayer toutes les minutes ne rendrait aucune relance plus juste,
    #: et parcourrait des centaines de proformas soixante fois par heure pour ne rien
    #: trouver.
    #:
    #: ⚠️ Elle ne commande pas *quand* un client est relancé : cela vient du plan de
    #: relance, au référentiel. Elle commande seulement la finesse avec laquelle
    #: l'échéance est rattrapée. Un palier à trois jours reste à trois jours, à une
    #: heure près.
    cadence_relance_minutes: int = 60

    #: Toutes les combien la veille des dossiers commerciaux passe, en minutes.
    #:
    #: ⚠️ **Quinze minutes, et non soixante comme la relance**, parce que les deux
    #: balayages ne coûtent pas la même chose. La relance envoie de vrais messages
    #: à de vrais clients ; la veille dépose une alerte interne. Le pire d'une
    #: veille trop fréquente est une requête indexée de plus, le pire d'une relance
    #: trop fréquente est le canal de messagerie du cabinet.
    #:
    #: Elle ne commande pas *à partir de quand* un dossier est réputé dormir : cela
    #: vient de `acquisition/veille.yaml`, au référentiel.
    cadence_veille_minutes: int = 15

    #: Toutes les combien le filet de réconciliation est jeté, en minutes.
    #:
    #: ⚠️ **Cinq minutes**, soit le délai minimal avant qu'un paiement soit
    #: interrogeable : un règlement dont la notification s'est perdue est donc
    #: rattrapé au pire cinq minutes après être devenu rattrapable.
    #:
    #: Elle ne commande pas *combien* d'appels partent vers le prestataire : cela
    #: vient de l'espacement porté par chaque paiement, qui double à chaque
    #: tentative et plafonne à une heure.
    cadence_reconciliation_minutes: int = 5

    #: L'ordonnanceur tourne-t-il **dans** le processus qui sert les requêtes ?
    #:
    #: ⚠️ Faux par défaut, et ce n'est pas de la prudence : c'est que les deux
    #: façons de le faire tourner sont légitimes, et que la mauvaise par défaut
    #: coûte cher. Une installation qui pilote ses travaux par un ordonnanceur
    #: extérieur, appelant `POST /orchestration/ordonnancement`, verrait ici une
    #: seconde source d'appels dont elle ignore l'existence.
    #:
    #: Le verrou consultatif rend les deux sûrs, y compris ensemble. Ce réglage
    #: dit lequel on veut, pas lequel est correct.
    ordonnanceur_en_processus: bool = False

    #: L'adresse de la base. ⚠️ Le mot de passe n'a rien à faire en dépôt : la
    #: valeur par défaut vise une base de développement locale, et la production
    #: la surcharge par `CGA_URL_BASE_DE_DONNEES`.
    url_base_de_donnees: str = "postgresql+psycopg://cga:cga@localhost:5432/cga_dev"

    #: Trace le SQL émis. Lire les requêtes est le premier geste de diagnostic,
    #: et il ne doit pas demander un changement de code.
    tracer_le_sql: bool = False

    #: ⚠️ Aucun secret en dépôt. Sans ces deux valeurs, le fournisseur Tara passe
    #: en **mode simulé** : aucun appel réseau, aucune validation automatique.
    #: Voir `adaptateurs/sortant/fournisseur_tara.py`.
    tara_cle_api: str = ""
    tara_identifiant_marchand: str = ""

    #: Le relais de messagerie. **Vide ⇒ aucun courriel ne part** : l'adaptateur
    #: en mémoire prend le relais et retient les messages, ce qui est le mode de
    #: développement voulu. Voir `adaptateurs/sortant/notifications_smtp.py`.
    smtp_hote: str = ""
    smtp_port: int = 587
    smtp_utilisateur: str = ""
    smtp_mot_de_passe: str = ""
    #: `STARTTLS` sur les ports en clair. Le port 465 chiffre d'emblée et ignore
    #: ce réglage. ⚠️ La production refuse `false` — voir `_exigences_production`.
    smtp_chiffrement: bool = True

    #: La clé de chiffrement au repos, en base64 — 32 octets décodés.
    #:
    #: ⚠️ **Vide ⇒ les secrets TOTP sont écrits en clair en base.** Acceptable
    #: en développement, refusé en production : un vidage de base qui fuite
    #: livrerait alors tous les seconds facteurs du cabinet, et personne ne s'en
    #: apercevrait. Voir `adaptateurs/sortant/coffre.py`.
    #:
    #: Produire une clé :
    #:     python -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())"
    cle_chiffrement: str = ""

    #: L'expéditeur des courriels transactionnels. Doit appartenir au domaine du
    #: cabinet, sans quoi SPF et DMARC feront classer les messages en
    #: indésirables quel que soit le code.
    courriel_expediteur: str = "CGA Broad Range <ne-pas-repondre@cga-brcg.cm>"
    #: L'adresse de réponse. Distincte de l'expéditeur : un adhérent répond
    #: toujours, et sa réponse ne doit pas tomber dans une boîte que personne
    #: ne relève.
    courriel_repondre_a: str = ""

    #: Le mode de recette : les paiements se valident seuls et les courriels
    #: retenus deviennent lisibles par une route dédiée.
    #:
    #: ─────────────────────────────────────────────────────────────────────
    #: ⚠️ POURQUOI UN DRAPEAU À PART, ET NON « PAS DE CLÉ TARA ⇒ ON VALIDE »
    #:
    #: L'en-tête de `fournisseur_tara.py` pose la règle et elle est juste :
    #: « un mode simulé qui validerait automatiquement finirait un jour en
    #: production ». L'absence de clé est un **accident de configuration** ;
    #: elle ne doit jamais valoir consentement à fabriquer des encaissements.
    #:
    #: Ce drapeau-ci se déclare, se lit dans l'environnement, apparaît dans
    #: `/sante`, s'affiche en bandeau sur chaque écran, et **empêche la
    #: production de démarrer**. Trois façons de s'en apercevoir, contre zéro
    #: pour un comportement déduit d'une clé manquante.
    #: ─────────────────────────────────────────────────────────────────────
    mode_demonstration: bool = False

    @property
    def en_base(self) -> bool:
        """Les écritures survivent-elles au redémarrage ?

        La question posée une fois, ici, plutôt qu'une comparaison de chaîne
        répétée dans chaque module. Voir le commentaire de `persistance` : la
        comparaison écrite à la main s'est déjà trompée de valeur.
        """
        return self.persistance == "postgresql"

    @property
    def en_production(self) -> bool:
        return self.environnement.lower() in {"production", "prod"}

    @model_validator(mode="after")
    def _exigences_production(self) -> Configuration:
        """Les réglages qu'un déploiement de production ne peut pas se permettre.

        ─────────────────────────────────────────────────────────────────────
        POURQUOI ÉCHOUER AU DÉMARRAGE

        Chacun de ces défauts est **silencieux à l'exécution**. Une application
        démarrée en persistance mémoire répond correctement à tout, et perd
        l'intégralité de la comptabilité au premier redéploiement — on
        l'apprend au moment de produire une déclaration. Un mot de passe SMTP
        qui voyage en clair ne se voit jamais.

        Un démarrage refusé se voit immédiatement, pendant qu'un ingénieur
        regarde. C'est la seule fenêtre où ces fautes coûtent peu.
        ─────────────────────────────────────────────────────────────────────
        """
        if not self.en_production:
            return self

        fautes: list[str] = []
        if self.mode_demonstration:
            fautes.append(
                "CGA_MODE_DEMONSTRATION=true fabrique des encaissements sans "
                "argent et expose les courriels retenus. Il n'a rien à faire "
                "en production, à aucune condition."
            )
        if not self.en_base:
            fautes.append(
                "CGA_PERSISTANCE doit valoir 'postgresql' : en mémoire, toutes "
                "les écritures comptables disparaissent au redémarrage."
            )
        if not self.smtp_hote:
            fautes.append(
                "CGA_SMTP_HOTE est vide : aucun lien d'activation ne partirait, "
                "et aucun compte ne pourrait être activé."
            )
        if self.smtp_hote and not self.smtp_chiffrement and self.smtp_port != 465:
            fautes.append("CGA_SMTP_CHIFFREMENT=false expose l'authentification SMTP en clair.")
        if not self.cle_chiffrement:
            fautes.append(
                "CGA_CLE_CHIFFREMENT est vide : les secrets TOTP seraient "
                "écrits en clair, et une fuite de base livrerait tous les "
                "seconds facteurs du cabinet."
            )
        if self.adresse_publique_site.startswith("http://"):
            fautes.append(
                "CGA_ADRESSE_PUBLIQUE_SITE doit être en https : les liens "
                "d'activation portent un secret d'usage unique."
            )
        if any(origine.startswith("http://") for origine in self.origines_cors):
            fautes.append(
                "CGA_ORIGINES_CORS contient une origine en clair ; le témoin de "
                "session serait exposé."
            )
        if fautes:
            raise ValueError("Configuration de production refusée :\n  · " + "\n  · ".join(fautes))
        return self


@lru_cache
def configuration() -> Configuration:
    return Configuration()
