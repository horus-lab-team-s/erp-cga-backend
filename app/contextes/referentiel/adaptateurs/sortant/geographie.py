"""Le référentiel géographique du Cameroun, lu une fois (8 novembre 2026).

Les fichiers de `Docs/referentiel/geographie/` (voir son README) : le découpage officiel de
l'INC, et les villes, localités et quartiers d'OpenStreetMap rattachés à leur arrondissement.

⚠️ `localites.yaml` pèse plus d'un mégaoctet : lu au premier appel, puis gardé. Le site ne
reçoit jamais tout : le découpage (une vingtaine de kilo-octets), puis les lieux d'UN
arrondissement à la fois.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

try:  # le chargeur en C, dix fois plus rapide sur le fichier des localités
    from yaml import CSafeLoader as _Chargeur
except ImportError:  # pragma: no cover
    from yaml import SafeLoader as _Chargeur  # type: ignore[assignment]

__all__ = ["Geographie", "cle_de_region", "geographie"]


def cle_de_region(nom: str) -> str:
    """« Extrême-Nord » → `EXTREME_NORD` : la valeur de la question `region_siege`."""
    sans_accent = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode()
    return sans_accent.upper().replace("-", "_").replace(" ", "_")


@dataclass
class Geographie:
    decoupage: list[dict]
    #: code d'arrondissement → {"villes": [...], "localites": [...], "quartiers": [...]}
    lieux: dict[str, dict[str, list[dict]]] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)

    def arrondissement_connu(self, code: str) -> bool:
        return any(
            a["code"] == code
            for r in self.decoupage
            for d in r["departements"]
            for a in d["arrondissements"]
        )


def _lire(chemin: Path) -> dict:
    with chemin.open(encoding="utf-8") as flux:
        return yaml.load(flux, Loader=_Chargeur)  # noqa: S506 — chargeur sûr (Safe)


def charger(dossier: Path) -> Geographie:
    decoupage = _lire(dossier / "decoupage.yaml")
    regions = [{**region, "cle": cle_de_region(region["nom"])} for region in decoupage["regions"]]
    geo = Geographie(decoupage=regions, sources={"decoupage": decoupage["fondement"]["source"]})
    for fichier, cle in (
        ("villes.yaml", "villes"),
        ("localites.yaml", "localites"),
        ("quartiers.yaml", "quartiers"),
    ):
        contenu = _lire(dossier / fichier)
        geo.sources[cle] = contenu["fondement"]["source"]
        for lieu in contenu[cle]:
            rangement = geo.lieux.setdefault(
                lieu["arrondissement"], {"villes": [], "localites": [], "quartiers": []}
            )
            entree = {"nom": lieu["nom"]}
            if lieu.get("ville"):
                entree["ville"] = lieu["ville"]
            rangement[cle].append(entree)
    # Une ville est proposée dans un arrondissement si elle s'y trouve, OU si ses quartiers
    # y sont : le point « Douala » est dans Douala 2e, et Bonapriso (Douala 1er) est à Douala.
    for rangement in geo.lieux.values():
        presentes = {v["nom"] for v in rangement["villes"]}
        for quartier in rangement["quartiers"]:
            ville = quartier.get("ville")
            if ville and ville not in presentes:
                rangement["villes"].append({"nom": ville})
                presentes.add(ville)
        rangement["villes"].sort(key=lambda v: v["nom"])
    return geo


@lru_cache
def geographie() -> Geographie:
    from app.infrastructure.config import configuration

    return charger(Path(configuration().dossier_referentiel) / "geographie")
