# Référentiel géographique du Cameroun

Construit le 8 novembre 2026, après la réunion du 4 novembre avec le cabinet : la ville dit
le centre de formalités, le quartier dit le centre des impôts de rattachement. Le client choisit
sa localisation dans des listes (région › département › arrondissement › ville › quartier)
au lieu de la taper.

**Ces fichiers sont générés** par `outils/referentiel_geographie.py` : ne pas les modifier à la
main, mais corriger la source ou le script et régénérer.

| Fichier | Contenu | Source | Confiance |
|---|---|---|---|
| `decoupage.yaml` | 10 régions, 58 départements, 360 arrondissements, avec leurs codes officiels (P-codes, ex. `CM005004001`) | Institut national de cartographie (INC), publié par OCHA sur HDX (`cod-ab-cmr`, version v01 du 26 janvier 2026, CC BY-IGO) | **Officielle, complète** |
| `villes.yaml` | 389 villes et bourgs (`city`, `town`), chacun rattaché à son arrondissement | OpenStreetMap (ODbL) ; rattachement par position sur les contours de l'INC | Bonne |
| `localites.yaml` | 10 443 villages, chacun rattaché à son arrondissement | OpenStreetMap | Bonne, inégale selon les régions |
| `quartiers.yaml` | 505 quartiers (points et limites `admin_level=10`), avec leur arrondissement et la ville la plus proche (Douala 96, Yaoundé 75, Ngaoundéré 56…) | OpenStreetMap | ⚠️ **PARTIELLE** |

⚠️ **Aucune liste publique complète des quartiers du Cameroun n'existe** (vérifié le
8 novembre 2026 : OpenStreetMap et GeoNames, qui n'en a que 39). Le formulaire doit donc
toujours permettre d'écrire un quartier absent de la liste, et le cabinet complète.

## Rattachement

Une ville, une localité ou un quartier est rattaché à l'arrondissement **dont le contour officiel
le contient**. Les rares points juste hors des contours (frontière, côte : 4 au
8 novembre 2026) prennent l'arrondissement le plus proche et portent `rattachement: APPROCHE`.

Un quartier porte `ville` : la ville (`city`) à moins de 15 km, à défaut le bourg (`town`).

## Corrections faites à la main (dans le script, par identifiant OpenStreetMap)

- `node/3037426226` retiré : un second « Bamenda » posé dans le Nord, sans population ni Wikidata.
- « Ville de Kumbo » retiré : doublon de Kumbo.
- Deido et Makepe, marqués `town` dans OpenStreetMap, sont des quartiers de Douala.

## Régénérer

    outils/sources-geographie.sh ~/Desktop/Gathe_finance/cga-depots/sources-geographie
    python3 -I outils/referentiel_geographie.py \
        --sources ~/Desktop/Gathe_finance/cga-depots/sources-geographie --sortie Docs/referentiel/geographie

Le premier script télécharge le découpage de l'INC (HDX) et interroge l'API Overpass
d'OpenStreetMap (villes, villages, quartiers, limites `admin_level=10`), en réessayant quand le
serveur public répond 502 ou 504. ⚠️ Ranger les sources hors de `/tmp`, effacé au redémarrage.

Les doublons d'écriture d'un même arrondissement (« New Bell » et « New-Bell ») sont fusionnés.

## Lu par l'API

`GET /referentiel/geographie/decoupage` (publique) rend les régions, départements et
arrondissements ; `cle` est la valeur de la question `region_siege`.
`GET /referentiel/geographie/arrondissements/{code}/lieux` rend les villes, localités et
quartiers d'un arrondissement. Une ville y est proposée si elle s'y trouve **ou** si ses
quartiers y sont (le point « Douala » est dans Douala 2e ; Bonapriso, à Douala 1er, est à Douala).

## Ce qui reste à faire (lot 1)

- Le rattachement de chaque ville à son **centre de formalités (CFCE)** et de chaque
  arrondissement ou quartier à son **centre des impôts** : à recevoir du cabinet, puis à
  ajouter ici.
- Servir ces listes au formulaire de création du site (région › … › quartier, avec
  « autre quartier » en saisie libre).
