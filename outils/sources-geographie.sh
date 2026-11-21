#!/usr/bin/env bash
# Télécharger les sources du référentiel géographique (8 novembre 2026).
#
#     outils/sources-geographie.sh <dossier>          puis
#     python3 -I outils/referentiel_geographie.py --sources <dossier> --sortie Docs/referentiel/geographie
#
# <dossier> : un dossier DURABLE hors du dépôt (pas /tmp, effacé au redémarrage). Voir
# Docs/referentiel/geographie/README.md pour ce que chaque source apporte.
set -euo pipefail
DOSSIER="${1:?usage : $0 <dossier>}"
mkdir -p "$DOSSIER/hdx" "$DOSSIER/osm"
CLIENT="cga-referentiel/1.0"   # Overpass refuse les requêtes sans client nommé (406)

echo "== Découpage officiel (INC, HDX cod-ab-cmr)"
curl -fsSL -m 300 -A "$CLIENT" -o "$DOSSIER/hdx/limites.zip" \
  "https://data.humdata.org/dataset/b13f08ef-92ee-4446-9b0a-e219f5c25415/resource/2248ac6d-9675-47de-90d3-1e7ce6e601b1/download/cmr_admin_boundaries.geojson.zip"
unzip -o -q -j "$DOSSIER/hdx/limites.zip" 'cmr_admin1.geojson' 'cmr_admin2.geojson' 'cmr_admin3.geojson' -d "$DOSSIER/hdx"
rm -f "$DOSSIER/hdx/limites.zip"

ENTETE='[out:json][timeout:360];area["ISO3166-1"="CM"][admin_level=2]->.c;'
overpass() {  # <fichier> <requête> : réessaie, le serveur public répond parfois 502/504
  for essai in 1 2 3 4; do
    if curl -fsS -m 420 -A "$CLIENT" -o "$DOSSIER/osm/$1" --data-urlencode "data=$ENTETE$2" \
        https://overpass-api.de/api/interpreter && python3 -I -c "import json,sys;json.load(open(sys.argv[1]))" "$DOSSIER/osm/$1"; then
      echo "   $1 : $(python3 -I -c "import json,sys;print(len(json.load(open(sys.argv[1]))['elements']))" "$DOSSIER/osm/$1") éléments"
      return 0
    fi
    echo "   $1 : essai $essai en échec, nouvel essai dans $((essai * 20)) s" >&2
    sleep $((essai * 20))
  done
  return 1
}
echo "== OpenStreetMap (Overpass)"
overpass villes.json 'nwr(area.c)[place~"^(city|town)$"];out center tags;'
sleep 10
overpass villages.json 'nwr(area.c)[place~"^(village)$"];out center tags;'
sleep 10
overpass quartiers.json 'nwr(area.c)[place~"^(suburb|quarter|neighbourhood)$"];out center tags;'
sleep 10
overpass limites_quartiers.json 'way(area.c)[boundary=administrative][admin_level~"^(9|10)$"][name];out center tags;rel(area.c)[boundary=administrative][admin_level~"^(9|10)$"][name];out center tags;'
echo "== Sources prêtes dans $DOSSIER"
