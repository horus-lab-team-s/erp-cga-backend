"""Construire le référentiel géographique du Cameroun (8 novembre 2026).

    python3 -I outils/referentiel_geographie.py --sources <dossier> \
        --sortie Docs/referentiel/geographie

─────────────────────────────────────────────────────────────────────────────────
POURQUOI

Réunion du 4 novembre 2026 avec le cabinet : la région ne suffit pas, la ville dit le centre de
formalités et le quartier le centre des impôts de rattachement. Le client choisit donc une
localisation dans une liste (région › département › arrondissement › ville › quartier) au
lieu de la taper.

TROIS NIVEAUX DE CONFIANCE, ÉCRITS DANS LES FICHIERS

* **Découpage administratif** (10 régions, 58 départements, 360 arrondissements) : la source
  officielle, l'Institut national de cartographie (INC), publiée par OCHA sur HDX
  (« cod-ab-cmr », CC BY-IGO). Codes officiels (P-codes) conservés.
* **Villes et localités** : OpenStreetMap (`place=city|town`, puis `village`), ODbL. Chacune
  est rattachée à l'arrondissement qui la contient, par sa position sur les contours de l'INC.
* **Quartiers** : OpenStreetMap (`place=suburb|quarter|neighbourhood`). ⚠️ PARTIELS : il
  n'existe pas de liste publique complète des quartiers du Cameroun. Le cabinet complète.

SOURCES ATTENDUES DANS `--sources` (téléchargées par l'exploitant, voir le README du dossier)

    hdx/cmr_admin1.geojson, hdx/cmr_admin2.geojson, hdx/cmr_admin3.geojson
    osm/villes.json, osm/villages.json, osm/quartiers.json   (sorties Overpass `out center tags`)
    osm/limites_quartiers.json   (facultatif : limites `boundary=administrative`, `admin_level=10`)

Aucune dépendance hors de la bibliothèque standard et de PyYAML : le point dans le polygone
est un lancer de rayon, précédé d'un filtre par rectangle englobant.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import argparse
import json
import math
import unicodedata
from datetime import date
from pathlib import Path

import yaml

SOURCE_INC = (
    "Institut national de cartographie (INC), limites administratives publiées par OCHA sur "
    "HDX, jeu « cod-ab-cmr », licence CC BY-IGO : https://data.humdata.org/dataset/cod-ab-cmr"
)
SOURCE_OSM = "OpenStreetMap, contributeurs OpenStreetMap, licence ODbL : https://www.openstreetmap.org/copyright"

#: Au-delà de cette distance, un quartier n'est rattaché à aucune ville (kilomètres).
DISTANCE_QUARTIER_VILLE_KM = 15.0

#: Les corrections relevées à la main le 8 novembre 2026, par identifiant OpenStreetMap.
#: ⚠️ Chacune est vérifiable : l'objet, et pourquoi il est corrigé.
VILLES_RETIREES = {
    # Un second « Bamenda » posé dans le Nord (près de Pitoa), sans population ni Wikidata ;
    # le vrai Bamenda (Nord-Ouest) est node/247717281.
    "node/3037426226": "doublon erroné de Bamenda",
}
#: Des quartiers de Douala marqués `place=town` : ils passent dans les quartiers.
VILLES_QUI_SONT_DES_QUARTIERS = {"Deido": "Douala", "Makepe": "Douala"}


def _nom(proprietes: dict, prefixe: str) -> str:
    """Le nom français de l'INC (`admN_name1`), sinon le nom de référence."""
    for cle in (f"{prefixe}_name1", f"{prefixe}_ref_name1", f"{prefixe}_name"):
        valeur = proprietes.get(cle)
        if valeur and valeur != "<Null>":
            return str(valeur).strip()
    raise ValueError(f"nom absent : {proprietes.get(prefixe + '_pcode')}")


def _cle_de_tri(texte: str) -> str:
    return unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode().lower()


# ── Géométrie ───────────────────────────────────────────────────────────────────


def _anneaux(geometrie: dict) -> list[list[list[list[float]]]]:
    """Les polygones d'une géométrie : [polygone][anneau][point][lon, lat]."""
    if geometrie["type"] == "Polygon":
        return [geometrie["coordinates"]]
    if geometrie["type"] == "MultiPolygon":
        return geometrie["coordinates"]
    raise ValueError(geometrie["type"])


def _dans_l_anneau(lon: float, lat: float, anneau: list[list[float]]) -> bool:
    dedans = False
    j = len(anneau) - 1
    for i in range(len(anneau)):
        xi, yi = anneau[i][0], anneau[i][1]
        xj, yj = anneau[j][0], anneau[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            dedans = not dedans
        j = i
    return dedans


class Zone:
    def __init__(self, code: str, geometrie: dict) -> None:
        self.code = code
        self.polygones = _anneaux(geometrie)
        points = [p for poly in self.polygones for p in poly[0]]
        self.ouest = min(p[0] for p in points)
        self.est = max(p[0] for p in points)
        self.sud = min(p[1] for p in points)
        self.nord = max(p[1] for p in points)
        self.centre = ((self.ouest + self.est) / 2, (self.sud + self.nord) / 2)

    def contient(self, lon: float, lat: float) -> bool:
        if not (self.ouest <= lon <= self.est and self.sud <= lat <= self.nord):
            return False
        for exterieur, *trous in self.polygones:
            if _dans_l_anneau(lon, lat, exterieur) and not any(
                _dans_l_anneau(lon, lat, t) for t in trous
            ):
                return True
        return False


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distance en kilomètres entre deux points (lon, lat), formule de haversine."""
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def _arrondissement_de(lon: float, lat: float, zones: list[Zone]) -> tuple[str, bool]:
    """Le code de l'arrondissement qui contient le point ; à défaut le plus proche (approché)."""
    for zone in zones:
        if zone.contient(lon, lat):
            return zone.code, False
    plus_proche = min(zones, key=lambda z: _km((lon, lat), z.centre))
    return plus_proche.code, True


# ── Lieux d'OpenStreetMap ─────────────────────────────────────────────────────


def _lieux(chemin: Path) -> list[dict]:
    lieux = []
    for element in json.loads(chemin.read_text(encoding="utf-8"))["elements"]:
        etiquettes = element.get("tags", {})
        nom = (etiquettes.get("name:fr") or etiquettes.get("name") or "").strip()
        if not nom:
            continue
        if "lat" in element:
            lat, lon = element["lat"], element["lon"]
        elif "center" in element:
            lat, lon = element["center"]["lat"], element["center"]["lon"]
        else:
            continue
        lieux.append(
            {
                "nom": nom,
                "type": etiquettes.get("place"),
                "lon": round(float(lon), 5),
                "lat": round(float(lat), 5),
                "osm": f"{element['type']}/{element['id']}",
            }
        )
    return lieux


def construire(sources: Path, sortie: Path) -> dict[str, int]:
    hdx = sources / "hdx"
    regions = json.loads((hdx / "cmr_admin1.geojson").read_text(encoding="utf-8"))["features"]
    departements = json.loads((hdx / "cmr_admin2.geojson").read_text(encoding="utf-8"))["features"]
    arrondissements = json.loads((hdx / "cmr_admin3.geojson").read_text(encoding="utf-8"))[
        "features"
    ]
    version = sorted({str(f["properties"].get("version")) for f in arrondissements})

    # ── Le découpage ──────────────────────────────────────────────────────────
    par_region: dict[str, dict] = {}
    for f in sorted(regions, key=lambda f: _cle_de_tri(_nom(f["properties"], "adm1"))):
        p = f["properties"]
        par_region[p["adm1_pcode"]] = {
            "code": p["adm1_pcode"],
            "nom": _nom(p, "adm1"),
            "nom_en": p.get("adm1_name") or None,
            "departements": [],
        }
    par_departement: dict[str, dict] = {}
    for f in sorted(departements, key=lambda f: _cle_de_tri(_nom(f["properties"], "adm2"))):
        p = f["properties"]
        departement = {"code": p["adm2_pcode"], "nom": _nom(p, "adm2"), "arrondissements": []}
        par_departement[p["adm2_pcode"]] = departement
        par_region[p["adm1_pcode"]]["departements"].append(departement)
    nom_arrondissement: dict[str, tuple[str, str, str]] = {}
    for f in sorted(arrondissements, key=lambda f: _cle_de_tri(_nom(f["properties"], "adm3"))):
        p = f["properties"]
        par_departement[p["adm2_pcode"]]["arrondissements"].append(
            {"code": p["adm3_pcode"], "nom": _nom(p, "adm3")}
        )
        nom_arrondissement[p["adm3_pcode"]] = (
            _nom(p, "adm3"),
            par_departement[p["adm2_pcode"]]["nom"],
            par_region[p["adm1_pcode"]]["nom"],
        )
    zones = [Zone(f["properties"]["adm3_pcode"], f["geometry"]) for f in arrondissements]

    entete = {
        "applicable_du": date.today().isoformat(),
        "nature": "LOI",
    }
    decoupage = {
        **entete,
        "libelle": "Découpage administratif du Cameroun : régions, départements, arrondissements",
        "fondement": {
            "texte": "Le découpage officiel en 10 régions, 58 départements et 360 arrondissements.",
            "source": f"{SOURCE_INC} (version {', '.join(version)})",
        },
        "statut": "A_VALIDER",
        "regions": list(par_region.values()),
    }

    # ── Villes et localités ───────────────────────────────────────────────────
    def rattacher(lieux: list[dict]) -> list[dict]:
        rattaches = []
        for lieu in lieux:
            code, approche = _arrondissement_de(lieu["lon"], lieu["lat"], zones)
            entree = {
                "nom": lieu["nom"],
                "type": lieu["type"],
                "arrondissement": code,
                "lat": lieu["lat"],
                "lon": lieu["lon"],
                "osm": lieu["osm"],
            }
            if approche:
                # Juste hors des contours (frontière, côte) : l'arrondissement le plus proche.
                entree["rattachement"] = "APPROCHE"
            rattaches.append(entree)
        return sorted(rattaches, key=lambda e: (e["arrondissement"], _cle_de_tri(e["nom"])))

    brutes = [
        v
        for v in _lieux(sources / "osm" / "villes.json")
        if v["osm"] not in VILLES_RETIREES and v["nom"] != "Ville de Kumbo"  # doublon de Kumbo
    ]
    devenus_quartiers = [v for v in brutes if v["nom"] in VILLES_QUI_SONT_DES_QUARTIERS]
    villes = rattacher([v for v in brutes if v["nom"] not in VILLES_QUI_SONT_DES_QUARTIERS])
    villages = rattacher(_lieux(sources / "osm" / "villages.json"))
    lieux_quartiers = _lieux(sources / "osm" / "quartiers.json") + devenus_quartiers
    limites = sources / "osm" / "limites_quartiers.json"
    if limites.exists():
        for lieu in _lieux(limites):
            lieu["type"] = "limite_quartier"
            lieux_quartiers.append(lieu)
    # Un même quartier peut être à la fois un point et une limite : gardé une fois
    # par arrondissement.
    quartiers, vus = [], set()
    for quartier in rattacher(lieux_quartiers):
        cle = (quartier["arrondissement"], _cle_de_tri(quartier["nom"]))
        if cle not in vus:
            vus.add(cle)
            quartiers.append(quartier)

    # Chaque quartier est rattaché à la ville (city) la plus proche, sinon au bourg (town) le
    # plus proche, s'il est assez près. Une ville d'abord : Bonapriso est à Douala, pas au
    # bourg voisin qu'OpenStreetMap aurait posé plus près.
    grandes = [v for v in villes if v["type"] == "city"]
    for quartier in quartiers:
        point = (quartier["lon"], quartier["lat"])
        for candidates in (grandes, villes):
            proche = min(candidates, key=lambda v: _km(point, (v["lon"], v["lat"])))
            if _km(point, (proche["lon"], proche["lat"])) <= DISTANCE_QUARTIER_VILLE_KM:
                quartier["ville"] = proche["nom"]
                break

    def fichier(libelle: str, texte: str, lieux: list[dict], cle: str) -> dict:
        return {
            **entete,
            "nature": "POLITIQUE_CABINET",
            "libelle": libelle,
            "fondement": {"texte": texte, "source": f"{SOURCE_OSM} ; rattachement : {SOURCE_INC}"},
            "statut": "A_VALIDER",
            cle: lieux,
        }

    sortie.mkdir(parents=True, exist_ok=True)
    ecrits = {
        "decoupage.yaml": decoupage,
        "villes.yaml": fichier(
            "Villes du Cameroun (city, town)",
            "Les villes d'OpenStreetMap, rattachées chacune à l'arrondissement qui la contient.",
            villes,
            "villes",
        ),
        "localites.yaml": fichier(
            "Villages et localités du Cameroun (village)",
            "Les villages d'OpenStreetMap, rattachés chacun à l'arrondissement qui le contient.",
            villages,
            "localites",
        ),
        "quartiers.yaml": fichier(
            "Quartiers (suburb, quarter, neighbourhood) — liste PARTIELLE",
            "Les quartiers connus d'OpenStreetMap. Aucune liste publique complète n'existe : "
            "le cabinet complète cette liste, et le client peut toujours écrire son quartier.",
            quartiers,
            "quartiers",
        ),
    }
    for nom, contenu in ecrits.items():
        (sortie / nom).write_text(
            "# Généré par outils/referentiel_geographie.py : ne pas modifier à la main.\n"
            + yaml.safe_dump(contenu, allow_unicode=True, sort_keys=False, width=100),
            encoding="utf-8",
        )
    return {
        "regions": len(par_region),
        "departements": len(par_departement),
        "arrondissements": len(nom_arrondissement),
        "villes": len(villes),
        "localites": len(villages),
        "quartiers": len(quartiers),
        "approches": sum(1 for x in villes + villages + quartiers if x.get("rattachement")),
    }


def principal() -> int:
    lecteur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    lecteur.add_argument("--sources", type=Path, required=True)
    lecteur.add_argument("--sortie", type=Path, required=True)
    options = lecteur.parse_args()
    for cle, nombre in construire(options.sources, options.sortie).items():
        print(f"{cle:>16} : {nombre}")
    return 0


if __name__ == "__main__":
    raise SystemExit(principal())
