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
| `quartiers.yaml` | 507 quartiers (points et limites `admin_level=10`), avec leur arrondissement et la ville la plus proche (Douala 96, Yaoundé 75, Ngaoundéré 56…) | OpenStreetMap | ⚠️ **PARTIELLE** |

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

Télécharger les sources dans un dossier vide (`<src>`), puis lancer le script :

    # Découpage officiel : décompresser cmr_admin_boundaries.geojson.zip dans <src>/hdx/
    https://data.humdata.org/dataset/cod-ab-cmr

    # Lieux d'OpenStreetMap (API Overpass, une requête par fichier, sortie `out center tags`)
    #   <src>/osm/villes.json             nwr(area.c)[place~"^(city|town)$"];
    #   <src>/osm/villages.json           nwr(area.c)[place~"^(village)$"];
    #   <src>/osm/quartiers.json          nwr(area.c)[place~"^(suburb|quarter|neighbourhood)$"];
    #   <src>/osm/limites_quartiers.json  way(area.c)[boundary=administrative][admin_level~"^(9|10)$"][name];
    #                                     rel(area.c)[boundary=administrative][admin_level~"^(9|10)$"][name];
    # avec, en tête de chaque requête :
    #   [out:json][timeout:360];area["ISO3166-1"="CM"][admin_level=2]->.c;

    python3 -I outils/referentiel_geographie.py --sources <src> --sortie Docs/referentiel/geographie

## Ce qui reste à faire (lot 1)

- Le rattachement de chaque ville à son **centre de formalités (CFCE)** et de chaque
  arrondissement ou quartier à son **centre des impôts** : à recevoir du cabinet, puis à
  ajouter ici.
- Servir ces listes au formulaire de création du site (région › … › quartier, avec
  « autre quartier » en saisie libre).
