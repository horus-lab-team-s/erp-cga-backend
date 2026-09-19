# Serveur CGA Broad Range

Le serveur du produit : quatorze contextes bornés, un socle multi-locataire clos, et le
référentiel légal daté qu'il applique.

Conception et réalisation : **TCHAMBA TCHAKOUNTE Edwin**, ingénieur informaticien.

## Les cinq dépôts

| Dépôt | Ce qu'il porte |
| --- | --- |
| **`erp-cga-backend`** | Ce dépôt : le serveur, le référentiel légal, le contenu éditorial de la vitrine |
| `erp-cga-console` | La console du cabinet et l'espace de l'adhérent |
| `erp-cga-vitrine` | Le site public |
| `erp-cga-mobile` | L'application de terrain, hors ligne |
| `erp-cga-plateforme` | La conception, la recette, le déploiement, le site de documentation |

## Démarrer

```bash
pip install -e .                       # ou : uv sync
outils/postgres-local.sh start         # PostgreSQL local, port 55432
alembic upgrade head
python -m uvicorn app.main:app --reload
```

Toute la pile d'un coup, base amorcée, console et vitrine comprises :

```bash
outils/pile-de-demonstration.sh neuve       # base recréée, jeu de démonstration versé
outils/pile-de-demonstration.sh demarrer    # monte ce qui manque, ne détruit rien
outils/pile-de-demonstration.sh etat
```

API sur 8010, console sur 3011, vitrine sur 3012. La vitrine est facultative : absente,
l'étape est sautée et dite.

## La suite de tests

```bash
CGA_URL_BASE_DE_DONNEES_TEST="postgresql+psycopg://cga@127.0.0.1:55432/cga_test" \
  python -m pytest -q
```

3 761 cas. Une partie exige PostgreSQL et s'ignore proprement sans lui : le cloisonnement
par sécurité des lignes ne se vérifie pas sur SQLite, et un test qui passerait sans base
donnerait une fausse assurance sur ce qui protège les données d'un adhérent des autres.

⚠️ **Donner l'URL sous une forme qui porte un mot de passe.** Les tests qui vérifient le
cloisonnement se reconnectent sous des rôles restreints en reposant l'utilisateur sur
cette URL. Si le compte de départ est superutilisateur, il contourne toutes les politiques
et quarante cas tombent d'un coup, en accusant le cloisonnement d'être cassé alors que
c'est la connexion qui l'était.

## ⚠️ Ce que la scission a rendu fragile

### Le référentiel légal est ici, et c'est un choix

`Docs/referentiel/` est le seul dossier de documentation resté dans ce dépôt, parce que
ce n'en est pas : le serveur le lit à chaque requête pour décider d'un taux, d'un seuil,
d'un motif de refus. Un serveur qui ne peut ni démarrer ni être testé sans cloner un
second dépôt est un serveur qu'on finit par tester ailleurs, c'est-à-dire nulle part. Les
textes qui **expliquent** ces valeurs, eux, sont restés dans `erp-cga-plateforme`.

⚠️ **Une erreur de niveau dans le calcul de la racine ne se voit pas au démarrage.** Le
serveur trouve un dossier vide, retombe sur les valeurs de repli du domaine, et démarre.
Cela se voit sur un taux d'imposition, pas dans un journal. Voir le commentaire de
`RACINE_DEPOT`, dans `app/infrastructure/config.py`.

### Trois outils lisent trois dépôts

L'invariant du projet — *tout ce que le serveur permet est à l'écran* — se mesure sur le
serveur et les **deux** interfaces à la fois. `app/infrastructure/voisinage.py` porte la
règle de recherche, une seule pour tout le dépôt.

```bash
CGA_RACINE_CONSOLE=../erp-cga-console:../erp-cga-vitrine \
CGA_RACINE_PLATEFORME=../erp-cga-plateforme \
python -m outils.avancement_des_ecrans      # 228 gestes branchés sur 228
python -m outils.couverture_des_ecrans      # 226 routes appelées sur 231
python -m outils.contrat_des_ecrans         # 278 appels lisibles sur 279
```

⚠️ **Un dépôt manquant fait échouer ces outils, jamais afficher « cent pour cent ».**
N'en lire qu'un a fait tomber la couverture de 226 à 222 et fait refuser la grille pour
cinq pages « absentes » qui existaient : elles étaient dans la vitrine.

Les tests, eux, s'ignorent au lieu d'échouer quand un dépôt voisin manque, et disent
lequel : faire échouer rendrait ce dépôt intestable seul, et une suite rouge par
construction finit par ne plus être lue.

## Ce qu'il faut lire avant de toucher au code

| Fichier | Ce qu'il tient |
| --- | --- |
| `app/registre/services.py` | Les arêtes autorisées entre contextes. Un import qui les viole fait échouer un test |
| `app/partage/locataire.py` | Le locataire courant, posé une fois à l'entrée |
| `app/contextes/transverse/domaine/mandats.py` | Le mandat : agir chez un autre locataire, et le dire |
| `app/infrastructure/securite_lignes.py` | Le cloisonnement par PostgreSQL, sous l'ORM |
| `app/orchestration/saga.py` | Les sagas et la boîte d'envoi, livraison au moins une fois |

Le dossier de conception complet est dans `erp-cga-plateforme`, sous `Docs/architecture/`.
