# Backend — Plateforme CGA Broad Range Consulting Group

FastAPI, monolithe modulaire. Un package Python par contexte borné.

## Démarrer

```bash
cd Backend_erp_cga
python -m venv .venv
.venv/Scripts/activate          # Windows ;  source .venv/bin/activate sous Unix
pip install -e ".[dev]"

uvicorn app.main:app --reload   # http://127.0.0.1:8000/docs
pytest                          # 1000 tests
ruff check .
```

## Ce qui est implémenté

| Contexte | Package | État |
|---|---|---|
| **K · Transverse** — *socle* | `app/contextes/transverse/` | Comptes, habilitations datées, cloisonnement par portefeuille, sessions révocables, second facteur TOTP, liens à usage unique, journal d'audit chaîné, registre des accusés de dépôt. **Dépôts PostgreSQL et mémoire**, routes HTTP |
| **A · Référentiel normatif** | `app/contextes/referentiel/` | Paramètres légaux datés, lecture à une date, statut de validation |
| **C · Collecte** | `app/contextes/collecte/` | Pièce et cycle de traitement, détection des doublons, extraction OCR à validation humaine, demandes et relances, complétude. Dépôts en mémoire, routes HTTP |
| **D · Conformité** | `app/contextes/conformite/` | Moteur de règles, rapport, conséquence fiscale chiffrée, verdict E02 |
| **B · Portefeuille** | `app/contextes/portefeuille/` | Statuts datés, exercices, franchissement de seuil, période probatoire. Dépôt en mémoire, routes HTTP |
| **E · Comptabilité** | `app/contextes/comptabilite/` | Domaine, projections, imputation, soudure avec D. Plan SYSCOHADA, dépôt en mémoire, routes HTTP |
| **F · Obligations** | `app/contextes/obligations/` | Échéances calculées, échéancier, relances, pénalités, déclaration de TVA. Catalogue, dépôts en mémoire, routes HTTP |
| **L · Vitrine** | `app/contextes/vitrine/` | Contenu éditorial du site public |
| **M · Souscription** | `app/contextes/souscription/` | Barème daté, devis figeant le prix du jour, encaissement mobile par Tara avec clé d'idempotence, rapprochement à trois stratégies, réconciliation, ouverture de l'accès adhérent, **échéancier d'abonnement, appel des mensualités, relances et arrêt du service**. Dépôts en mémoire, routes HTTP |
| G, H, I, J, K | — | À faire, voir `Docs/architecture/06-phases-et-sequencement.md` |

Le référentiel est aujourd'hui un dossier de fichiers YAML versionnés
(`Docs/referentiel/`). Cible : une table PostgreSQL éditable par le fiscaliste via
l'écran E11. Le service de lecture ne connaît pas l'origine, la bascule n'affectera pas
les appelants.

## Les six principes que le code fait respecter

### 1 · Aucune valeur légale en dur, aucune lecture sans date

```python
parametres.valeur_numerique("SEUIL_ESPECES_DEDUCTIBILITE_TVA", facture.date_emission)
```

Il n'existe volontairement aucune méthode permettant de lire « la valeur courante » sans
préciser laquelle. Une facture de 2024 se contrôle avec les règles de 2024.

Un paramètre absent, ou sans version couvrant la date, lève une erreur — jamais une
valeur par défaut. `tests/test_integrite_referentiel.py` interdit par ailleurs la
présence des seuils et taux connus dans les modules de domaine.

### 2 · Un rôle n'est pas un attribut, c'est une période

La question que pose un contrôle n'est jamais « qui est comptable » mais **« qui était
habilité le 12 mars, jour de ce dépôt »**. Une habilitation porte donc `[debut, fin[`, et
ne se supprime jamais — elle se ferme.

```python
roles_au(habilitations, date(2026, 3, 12))   # {COMPTABLE}
roles_au(habilitations, date(2026, 6, 1))    # set() — parti le 30 avril
```

Même patron que le statut daté du contexte B, appliqué à l'identité. Retirer le rôle d'un
collaborateur qui part ferait apparaître rétroactivement toutes ses déclarations comme
déposées sans habilitation.

### 3 · Hors périmètre rend 404, pas 403

Un `403` dit « ce dossier existe, et il ne vous regarde pas ». C'est une information, et
elle a de la valeur : un adhérent apprendrait par essais successifs quels NIU le cabinet
suit — c'est-à-dire la liste de ses clients.

Du point de vue de qui n'y a pas accès, un dossier hors périmètre est un dossier qui
n'existe pas. Le `404` dit exactement cela, et il ne ment pas.

Le `403` reste employé quand c'est la **permission** qui manque, sans dossier en jeu : là,
rien n'est révélé qu'on ne sache déjà, la table des rôles étant publiée sur
`/transverse/roles`.

Deux outils, et la distinction entre eux est le sujet :

```python
restreindre(acces, pieces, lambda p: p.entreprise)   # une liste se restreint
exiger_dossier(acces, Permission.LIRE_PIECE, niu)    # une lecture unitaire se refuse
```

### 4 · Un encaissement ne se compte jamais deux fois

Le prestataire de paiement renvoie plusieurs fois la même notification — son mécanisme de
reprise l'y pousse. Quatre garanties concourent, reprises du module éprouvé en production :

```
clé d'idempotence propre  →  le prestataire déduplique de son côté
valider() rejouable       →  un paiement déjà validé se rend inchangé
activee_le                →  refuse d'ouvrir un second compte
réconciliation sortante   →  repêche ce que la notification n'a pas apporté
```

Et la règle inverse, tout aussi importante : **un encaissement qu'on ne sait pas rattacher
n'est jamais rattaché au hasard**. Il est journalisé sous `paiement.non_affecte` et traité à
la main.

### 5 · On ne dépose pas ce qui sera rejeté

Le transport vers la DGI est une demi-journée le jour où ses spécifications existent. Ce
qui a de la valeur, c'est de produire une déclaration **qui ne sera ni rejetée, ni
redressée**. Trois niveaux, et la distinction est le sujet :

```
BLOQUANT     la déclaration serait fausse — on ne dépose pas
RESERVE      elle peut partir, mais quelqu'un doit l'assumer
INFORMATION  ce qu'il faut savoir, sans que la décision change
```

⚠️ **Toute déclaration produite porte aujourd'hui la réserve `REFERENTIEL-NON-VALIDE`**,
et elle est nommée dans la réponse. Laisser déposer sans le dire ferait passer pour
opposable un chiffre qui ne l'est pas.

Et la preuve du dépôt est l'**accusé de réception**, pas notre conviction : `declaree_le`
reçoit la date de l'accusé, celle que l'administration retient pour les délais.

### 6 · Le prédicat d'une règle exprime la conformité

Prédicat **vrai** ⇒ conforme, aucun constat. Prédicat **faux** ⇒ constat émis.
Contre-intuitif, donc rappelé partout dans le code et dans chaque fichier de règle.

## La persistance

Deux modes, choisis par `CGA_PERSISTANCE` :

```
memoire      le jeu de démonstration, perdu au redémarrage — défaut aujourd'hui
postgresql   26 tables, dont 24 cloisonnées par locataire : comptes, habilitations,
             jetons, sessions, journal d'audit, accusés de dépôt, dossiers, pièces,
             demandes, écritures, plan d'imputation, devis, souscriptions, paiements,
             entreprises, salariés, contrats de travail, dossiers de création,
             dossiers commerciaux, proformas, boîte d'envoi, exécutions de saga,
             passages d'ordonnance, suivis de relance, rappels à passer,
             qualifications
```

⚠️ **Aucune autre valeur n'est acceptée.** Le réglage est un type fermé : une valeur
inconnue est refusée au chargement de la configuration, avec la liste des valeurs admises.
Elle l'est parce qu'elle ne l'était pas : une comparaison à `"sql"`, valeur que rien n'a
jamais acceptée, a empêché le garnissage du répertoire des tenants en production pendant
tout un chantier, sans que rien ne le signale.

⚠️ Le défaut reste `memoire` parce que la bascule est une décision d'exploitation, pas un
réglage de développement : elle suppose une base administrée, sauvegardée, et un amorçage
maîtrisé. `CGA_PERSISTANCE=postgresql` suffit à la faire.

**Ce qui reste en code, et pourquoi** : les catalogues — plan SYSCOHADA, journaux,
catalogue d'obligations, offre commerciale. Ce sont des données de configuration,
identiques pour tous les locataires ; leur donner une table ne servirait qu'à devoir les y
remettre à chaque nouvelle base. Le plan d'**imputation**, lui, est propre à chaque
dossier : il a sa table.

**Le magasin de fichiers n'a pas de table**, et n'en aura pas. Des documents scannés en
base feraient grossir les sauvegardes d'un facteur cent pour des données qui ne se
requêtent jamais. La cible est un stockage objet ; ce qui va en base est l'empreinte.

### Le registre des services

`GET /transverse/services` rend les quatorze services, leurs dépendances, et **trois
états distincts** par service. Réservé à l'administration : le graphe des dépendances
d'une plateforme est une carte de ses points de rupture.

```
construction   DECLARE → DOMAINE → CAS_D_USAGE → EN_SERVICE
               constaté sur le disque et sur l'application montée, jamais déclaré
exécution      REPOND · SUSPECT · EN_PANNE · SANS_SONDE
               constaté par une sonde, quand le service en fournit une
dépendance     dependances · entraine · appuis_tombes
               calculé sur le graphe déclaré dans app/registre/
```

⚠️ **Les trois ne se fondent jamais en un seul champ.** Un champ unique mentirait dans
les deux sens : un service complet dont la base est tombée passerait pour incomplet, et
un service à peine commencé mais dont le processus tourne passerait pour opérationnel.
Le second est le plus coûteux, parce qu'il fait croire qu'une fonctionnalité existe.

⚠️ `SANS_SONDE` est distinct de `REPOND`. **Huit services sur quatorze** fournissent
une sonde ; les six autres n'ont aucune configuration propre dont l'absence les
empêcherait de travailler, et le registre le dit plutôt que de leur inventer une sonde
complaisante. Déclarer sain un service qu'on n'interroge pas, c'est affirmer
précisément la chose qu'on ne sait pas.

**Chaque service déclare sa sonde chez lui**, et la composition les rassemble : le
registre n'importe aucun contexte, sans quoi consulter l'état de la plateforme
demanderait de la monter entière — précisément ce qu'on ne peut pas faire quand elle
va mal.

**`entraine` est la question qui compte en incident.** « Le Référentiel ne répond plus »
n'apprend rien ; « le Référentiel ne répond plus, et douze services en dépendent, dont
la Comptabilité et les Obligations » l'apprend. C'est la fermeture inverse du graphe, pas
la liste des voisins directs.

Le graphe vit dans `app/registre/`, et **le test d'architecture le lit de là**. Le même
objet interdit une arête à la relecture et répond à la route : deux vérités recopiées
finissent par diverger, et celle qui dérive est celle que personne ne relit.

### La relance de bout en bout, sans dépendre de personne

Le balayage dépose un `RelanceDue`, le relais le publie, un abonné le poste. **Le
canal se choisit à la remise, jamais au dépôt** : entre les deux, un client peut avoir
révoqué son consentement, et un canal figé ferait partir le message quand même.

```
messagerie   consentement en vigueur, canal actif, ET compte prêt
courriel     canal actif, ET une adresse renseignée
appel        canal actif. Rien d'autre : c'est le plancher
```

⚠️ **L'appel n'envoie rien, il inscrit un rappel** dans `GET /acquisition/rappels`,
avec son motif en clair. C'est ce qui rend le plancher réel : un système dont le
dernier recours serait encore un envoi automatique dépendrait toujours d'une
passerelle, et une panne de celle-ci arrêterait toute relance.

Clore un rappel demande de se nommer, et un rappel déjà clos rend `409` : répondre
`200` ferait qu'un second collaborateur croirait avoir pris le contact.

⚠️ **L'ordre de repli est celui du centre, pas une hiérarchie technique.** Le plan du
référentiel place aujourd'hui l'appel au rang 0 : le centre a choisi d'appeler avant
d'écrire, et le système respecte sa décision. Le changer est un geste de configuration,
sans déploiement.

### HashiCorp Consul : la même déclaration, appliquée à l'exécution

Le graphe des services est vérifié à la relecture par `tests/test_architecture.py`,
qui lit les imports réels. Consul le fait respecter à l'exécution, par mTLS entre
services. Aucun des deux ne remplace l'autre : le test attrape l'import interdit
avant la livraison, le maillage attrape l'appel réseau qu'aucun import ne trahit.

```bash
python -m app.registre.consul \
    --adresse "$ADRESSE" --port 8000 --jeton-de-sonde "$JETON" > consul.json
```

⚠️ **Le sens est unique : le dépôt écrit, Consul applique.** Une intention corrigée
dans l'interface de Consul serait invisible dans le dépôt, absente de la revue,
perdue au prochain déploiement — et le test continuerait d'affirmer une découpe qui
ne serait plus celle appliquée.

⚠️ **Les quatorze services s'enregistrent même dans un seul processus.** Un agent
Consul peut en porter plusieurs, chacun avec son contrôle de santé : Consul montre la
santé service par service alors que tout vit encore ensemble. Le jour où l'un part
vivre ailleurs, seule son adresse change. L'extraction devient un déménagement.

Les trois niveaux du registre correspondent aux trois de Consul, et un seul code
déclenche l'intermédiaire :

```
2xx   → passing    REPOND, et aussi SANS_SONDE
429   → warning    SUSPECT
reste → critical   EN_PANNE
```

Deux traductions peuvent se tromper en produisant une configuration valide, et sont
couvertes : le graphe est déclaré **source → destinations** et Consul l'attend
**destination ← sources**, et le socle est implicite chez nous alors qu'il doit être
développé pour le maillage. Voir `outils/consul/README.md`.

### L'ordonnanceur

Les travaux périodiques — vider la boîte d'envoi, et demain balayer les relances —
tournent de deux façons, au choix de l'installation.

```
CGA_ORDONNANCEUR_EN_PROCESSUS=false   un ordonnanceur extérieur appelle la route
                                      POST /orchestration/ordonnancement  (défaut)
CGA_ORDONNANCEUR_EN_PROCESSUS=true    une boucle tourne dans le processus
CGA_CADENCE_RELAIS_SECONDES=5         le délai entre deux passages du relais
CGA_CADENCE_RELANCE_MINUTES=60        le délai entre deux balayages de relance
```

⚠️ **Les deux ensemble sont sûrs.** Un verrou consultatif PostgreSQL arbitre : deux
instances demandent, une seule obtient, l'autre passe son tour sans lever. Le défaut est
`false` parce que sûr n'est pas la même chose que voulu — une installation pilotée de
l'extérieur verrait sinon une seconde source d'appels dont elle ignore l'existence.

**L'état de chaque travail vit en base**, dans `passage_ordonnance`. Un compte à rebours en
mémoire repartirait à zéro à chaque redéploiement, et un travail quotidien sur une
plateforme déployée chaque matin ne passerait jamais. `GET /orchestration/ordonnancement`
montre chaque travail déclaré, y compris celui qui n'a jamais démarré.

⚠️ **Un travail en retard ne se rattrape pas N fois.** Un processus arrêté trois jours, sur
une cadence horaire, produit **un** passage au redémarrage, pas soixante-douze. Aucun
travail de ce système n'est cumulatif : rejouer un passage manqué ne produirait rien
qu'une seconde relance au même client.

Un travail qui échoue voit son délai de reprise doubler, plafonné à une heure et jamais
plus court que sa cadence. Au delà de vingt échecs consécutifs il est **abandonné** et ne
repart pas seul : un travail qui échoue vingt fois a un défaut que la vingt-et-unième
tentative ne corrigera pas.

**La transaction est la requête.** Un intergiciel ouvre une unité de travail, la valide en
sortie et l'annule sur exception. Dix-huit routes qui valideraient chacune produiraient
dix-huit transactions par requête, et une écriture partielle à la première erreur.

**Le parcours se conduit entièrement par l'API.** Formulaire public, file du
responsable, affectation, qualification, chiffrage, émission de la proforma,
transmission, acceptation par le client **sans compte** — et le dossier avance à
chaque geste.

⚠️ **L'état suit ce que le geste prouve, pas ce qu'il espère.** Une qualification
incomplète ne fait rien avancer : sinon un dossier auquel il manque neuf questions
franchirait l'étape, et le chiffrage porterait sur des faits absents. Une transition
impossible est ignorée plutôt que levée — refuser la requête ferait perdre le travail
du responsable au moment où il corrige une réponse.

⚠️ **L'encaissement est confirmé par un humain habilité**, jamais par un crochet de
prestataire : ce geste ouvre un tenant, et le fournisseur de paiement ne signe pas ses
notifications. Le commercial conclut la vente, **l'administrateur ouvre l'accès** — la
permission requise est celle que la plateforme emploie déjà pour l'acte équivalent.
Rejouable sans dommage : deux confirmations n'ouvrent pas deux tenants.

⚠️ **L'acceptation est publique**, et elle doit l'être : imposer un compte pour signer
un devis ferait perdre la moitié des acceptations. Le lien signé **est**
l'authentification, scellé sur le numéro, la version et l'échéance, à usage unique. Il
n'est rendu qu'à l'émission.

**L'affectation se suffit à elle-même.** `POST /acquisition/dossiers/{ref}/affectation`
accepte un corps vide : le serveur monte les candidatures depuis l'annuaire des
collaborateurs et la charge lue sur les dossiers. Fournir des candidats reste possible,
pour simuler un effectif hypothétique.

⚠️ **Ce que l'annuaire refuse de deviner**, il le laisse vide : la région du prospect
n'est pas collectée par le formulaire, et un compte ne porte pas d'agence. Le critère
de proximité pénalise alors tout le monde également, ce qui l'annule sans le fausser.
Le référentiel l'anticipe explicitement.

**La qualification se cumule.** `POST /acquisition/dossiers/{ref}/qualification`
**reprend** la qualification en cours et lui ajoute les réponses reçues : un
responsable qui répond à cinq questions sur douze, rappelle le client et complète
retrouve son avancement. ⚠️ L'écriture n'a lieu qu'après le lot entier validé — un lot
dont une réponse est fautive ne laisse rien derrière lui.

La version du questionnaire est conservée avec les réponses : une question ajoutée
demain ne rendra pas « incomplètes » les qualifications déjà closes.

**Un tenant payé existe pour de bon.** L'ouverture écrit la ligne en base **et**
inscrit le tenant au répertoire de la passerelle : le sous-domaine répond dans la
seconde, sans attendre un redéploiement. ⚠️ L'ordre compte — la base d'abord : si elle
refuse un slug déjà pris, le répertoire n'a rien appris et la passerelle ne servira pas
un tenant qui n'existe pas.

**Le cloisonnement s'applique tout seul** : un écouteur `do_orm_execute` ajoute le critère
de locataire à toute requête ORM. On ne peut pas l'oublier parce qu'on ne l'écrit jamais.
⚠️ Le SQL textuel y échappe et doit porter son `WHERE` en toutes lettres.

### Les deux rôles PostgreSQL, et pourquoi ils ne sont pas facultatifs

Chaque table cloisonnée porte une politique de sécurité au niveau des lignes. Mais
**PostgreSQL n'applique aucune politique au propriétaire de la table**, ni à un rôle
`BYPASSRLS`, ni à un superutilisateur.

Les migrations créent les tables, donc les possèdent. Une installation qui donne le même
rôle aux migrations et à l'application fait que l'application possède ses tables, et que
les politiques **ne s'appliquent jamais à elle**. Le cloisonnement du multi-cabinet repose
alors sur le seul filtre ORM ci-dessus, que le SQL textuel contourne.

⚠️ **Rien ne le signale.** Le service répond, la recette passe, la suite de tests est verte.
On l'apprend le jour où un adhérent voit la facture d'un autre.

```
cga_migration   crée, possède et modifie le schéma.  Alembic seul l'emploie.
cga_app         lit et écrit les données. Ne possède rien. Soumis aux politiques.
```

À jouer **une fois par base, avant la première migration**, avec un superutilisateur :

```bash
psql "$URL_SUPERUTILISATEUR" \
     -v mot_de_passe_migration="'…'" \
     -v mot_de_passe_app="'…'" \
     -f outils/roles-postgresql.sql
```

**Comment savoir que c'est fait.** L'application le constate au démarrage et le publie :
`GET /sante` rend `"cloisonnement": "APPLIQUE"`. Tant qu'il rend autre chose, le champ
`cloisonnement_explication` nomme la cause et le geste qui la lève. **En production,
l'application refuse de démarrer** sur un contournement constaté : un service qui démarre
sans cloisonnement ne tombe pas en panne, il sert les données de deux cabinets sans
séparation, et une panne franche vaut mieux.

### Une base pour développer, sans toucher au serveur du poste

```bash
eval "$(outils/postgres-local.sh start)"   # instance dédiée, port 55432
alembic upgrade head
pytest                                     # les 29 tests de persistance s'exécutent
outils/postgres-local.sh stop
```

Créer un rôle sur le serveur du système demande `sudo`, donc une décision qui n'appartient
pas au projet : on modifierait la configuration d'une machine partagée pour faire tourner
des tests. Cette instance vit dans un répertoire temporaire et se jette.

⚠️ Elle emploie `--auth=trust`, acceptable parce qu'elle n'écoute que sur `127.0.0.1`, sur
un port non standard, et ne contient que des données de démonstration. Inacceptable
ailleurs. La production reçoit son adresse par `CGA_URL_BASE_DE_DONNEES`.

Les tests de persistance **s'ignorent proprement** si aucune base n'est joignable, avec le
motif et la commande dans le message. Un test vert parce qu'il ne s'est pas exécuté est
pire qu'un test rouge.

### Migrations

`create_all` existe pour les tests et **n'est pas une migration** : il ne modifie aucune
table déjà présente. Toute évolution de schéma passe par Alembic.

⚠️ **Toute nouvelle table s'ajoute à `app/tables.py`**, et nulle part ailleurs.
`autogenerate` ne voit que les modules chargés : une table non recensée n'est pas créée,
et si elle existe déjà en base, il propose de la **supprimer**.

Ce piège s'est refermé trois fois avant qu'un recensement unique ne le ferme.
`tests/test_tables.py` vérifie qu'aucun module n'a été oublié, que chaque table est
cloisonnée, et qu'aucune table déclarée n'est sans migration.

## Le moteur en trois étapes

```
règle YAML
   │  1. résolution   {"param": "SEUIL_…"} → Decimal("500000")
   │                  (la valeur employée est conservée dans le rapport)
   │  2. évaluation   JSONLogic restreint, sans eval, opérateurs sur liste close
   │  3. conséquence  déclarative : la règle décrit, un service en aval applique
   ▼
constat + enjeu chiffré
```

L'enjeu découle de la conséquence : TVA seule rejetée → montant de TVA ; charge seule →
HT ; les deux → TTC ; aucune → non chiffré.

## API

| Route | Objet |
|---|---|
| `GET /sante` | Disponibilité |
| `GET /transverse/roles` | Les neuf rôles du cabinet et ce que chacun permet. Public |
| `POST /transverse/session` | Ouvrir une session. **Tout échec rend le même 401** |
| `GET /transverse/moi` | Rôles, permissions et dossiers accessibles de la session courante |
| `POST /transverse/mot-de-passe/definition` | Définir son mot de passe depuis un lien à usage unique |
| `POST /transverse/mot-de-passe/oubli` | **202 dans tous les cas**, y compris sur une adresse inconnue |
| `POST /transverse/comptes/invitation` | Inviter un collaborateur |
| `POST /transverse/comptes/{id}/suspension` | Couper l'accès, sessions ouvertes comprises |
| `POST /transverse/habilitations/{id}/dossiers/{niu}` | Affecter un dossier à un collaborateur |
| `GET /transverse/audit/verification` | Relire la chaîne de hachage du journal |
| ⚠️ **B, C, E, F** | **Toutes leurs routes exigent désormais une session et vérifient le périmètre.** Hors portefeuille : `404`, jamais `403` |
| `GET /souscription/services?a_la_date=` | L'offre, au barème d'une date. **La date est obligatoire** |
| `POST /souscription/devis` | Établir un devis. Les montants sont **recopiés**, pas référencés |
| `POST /souscription/devis/{ref}/engagement` | Régler par paiement mobile |
| `POST /souscription/notification/tara` | Notification d'encaissement. **Répond toujours 200** |
| `GET /souscription/a-activer` | Les encaissements payés dont l'accès n'a pas été ouvert |
| `POST /transverse/second-facteur` | Enrôler son second facteur. Le secret n'est rendu **qu'une fois** |
| `POST /transverse/session/renforcement` | Élever la session par un code TOTP, pour quinze minutes |
| `GET /obligations/dossiers/{niu}/depot-tva` | Préparer un dépôt : chiffres, **contrôles de recevabilité**, empreinte |
| `POST /obligations/dossiers/{niu}/depot-tva` | Constater le dépôt effectué sur le portail. Exige une session renforcée |
| `POST /souscription/reconciliation` | Repêcher les encaissements dont la notification s'est perdue |
| `GET /souscription/souscriptions/{ref}/echeancier` | Les mensualités, **calculées et non stockées** |
| `POST /souscription/prelevements` | Appeler les mensualités dues. Idempotent |
| `GET /souscription/relances` | Les impayés dont le retard tombe sur un jalon |
| `GET /souscription/services-a-suspendre` | ⚠️ Une **décision**, pas une exécution — suspendre n'est pas séquestrer |
| `GET /referentiel/parametres` | Codes disponibles |
| `GET /referentiel/parametres/{code}?a_la_date=` | Résolution datée. **La date est obligatoire** |
| `GET /referentiel/validation?a_la_date=` | Ce qui reste à valider, et si le référentiel est opposable |
| `GET /conformite/regles` | Catalogue des règles |
| `POST /conformite/controler` | Contrôler une facture transmise |
| `GET /conformite/demonstration` | Références du jeu de démonstration (§ 13.5) |
| `GET /conformite/demonstration/{reference}` | Contrôler une facture de démonstration |

Essai rapide :

```bash
curl http://127.0.0.1:8000/conformite/demonstration/F-2026-0414
# → verdict bloquant, 536 625 FCFA, TVA et charge non déductibles
```

## Ce que la chaîne d'intégration vérifie

`.github/workflows/verification.yml`, à chaque proposition :

```
ruff                  le style, sur app et tests
alembic upgrade head  les migrations tournent réellement
alembic check         le modèle et les migrations disent la même chose
pytest -q -rs         la suite entière, sur un vrai PostgreSQL
aucun test sauté      le contrôle qui donne sa valeur au précédent
```

⚠️ **Le dernier contrôle porte sur la suite entière**, et non sur un fichier nommé. Il
en nommait un — le seul qui réclamait une base à l'époque —, dix-huit le font
aujourd'hui, et l'étape en surveillait un. *Un garde-fou qui nomme ce qu'il surveille
finit par ne plus surveiller que ce qu'il nomme.*

⚠️ **`alembic check` n'est pas décoratif** : c'est lui qui a révélé qu'un `unique=True`
sensible à la casse dans le modèle cohabitait avec un index insensible dans la
migration. Les tests et la production n'avaient pas la même contrainte, et deux tenants
auraient pu répondre au même nom d'hôte.

## La recette de bout en bout

`tests/test_recette_du_parcours.py` joue le parcours entier sur PostgreSQL, par HTTP
là où des routes existent : formulaire public, file du responsable, affectation sans
candidats fournis, qualification en deux passages, chiffrage, encaissement, ouverture
du tenant, sous-domaine joignable. Puis la relance d'une proforma sans réponse,
jusqu'au rappel déposé sur le bureau d'un humain.

⚠️ **Elle ne vérifie aucune règle métier** — c'est le travail des autres fichiers. Elle
vérifie que **les pièces sont branchées entre elles**, et c'est ce qui manquait : les
quatre défauts de câblage les plus graves de ce projet ont été trouvés en tapant un
script à la main, faute d'un tel test.

Elle a été mise à l'épreuve : chaque défaut encore reproductible a été réintroduit pour
vérifier qu'elle vire au rouge. *Une recette qu'on ne met pas à l'épreuve est une
recette dont on ignore ce qu'elle couvre.*

## Tests

| Fichier | Ce qu'il protège |
|---|---|
| `test_jsonlogic.py` | L'évaluateur : portée, comparaisons, refus des opérateurs inconnus |
| `test_referentiel.py` | Lecture datée, bornes `[du, au[`, refus de deviner |
| `test_regles.py` | Un cas passant et un cas échouant **par règle**, plus les verdicts du § 13.5 |
| `test_integrite_referentiel.py` | Fondement légal obligatoire, paramètres référencés existants, couverture de test par règle, aucune valeur légale en dur |
| `test_formats.py` | Formats du § 4 : montants, taux, dates |
| `test_api.py` | Contrats HTTP |
| `test_transverse.py` | Séparation des tâches, habilitation datée, cloisonnement par portefeuille, oracle d'énumération refermé, usage unique des liens, détection d'altération du journal |
| `test_teledeclaration.py` | Second facteur TOTP (vecteur RFC 6238 compris), recevabilité avant dépôt, accusé confronté au dossier préparé, refus du second dépôt |
| `test_abonnements.py` | Arithmétique des périodes (31 janvier, bissextile), états d'échéance, idempotence de l'appel, jalons de relance, arrêt du service |
| `test_souscription.py` | Barème daté, devis figé, idempotence de l'encaissement, rapprochement à trois stratégies, montant incohérent refusé, filet de réconciliation, parcours complet du visiteur à l'espace adhérent |

Deux tests sont des **rappels volontaires** : ils échoueront le jour où le référentiel
sera validé, pour forcer la mise à jour de la documentation associée. Voir
`test_rien_nest_encore_opposable`.

## Ajouter une règle

1. `Docs/referentiel/regles/<CODE>.yaml`
2. Une classe `Test<CODE_avec_underscores>` dans `tests/test_regles.py`, avec au moins un
   cas passant et un cas échouant
3. `pytest` — `test_integrite_referentiel.py` refuse une règle sans tests, sans fondement
   légal, ou référençant un paramètre inexistant

## Avertissement

Aucune valeur légale de ce système n'a été validée sur le Code Général des Impôts. Toutes
portent le statut `A_VALIDER`. `GET /referentiel/validation` en donne l'état exact.
**Aucun chiffre produit n'est opposable** tant qu'un fiscaliste nommé n'a pas confirmé
chaque valeur sur le texte — voir `Docs/architecture/09-questions-ouvertes.md`.
