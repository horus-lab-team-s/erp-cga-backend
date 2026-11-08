"""Le référentiel géographique du Cameroun (8 novembre 2026) : cohérent avec lui-même.

Voir `Docs/referentiel/geographie/README.md`. Ces cas ne vérifient pas OpenStreetMap ; ils
vérifient que ce qui est rangé ici tient debout : codes officiels, rattachements, cas connus.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

GEOGRAPHIE = Path(__file__).resolve().parents[1] / "Docs" / "referentiel" / "geographie"


@lru_cache
def _charger(nom: str) -> dict:
    return yaml.safe_load((GEOGRAPHIE / nom).read_text(encoding="utf-8"))


@lru_cache
def _arrondissements() -> dict[str, tuple[str, str, str]]:
    """code → (arrondissement, département, région)."""
    table = {}
    for region in _charger("decoupage.yaml")["regions"]:
        for departement in region["departements"]:
            for arrondissement in departement["arrondissements"]:
                table[arrondissement["code"]] = (
                    arrondissement["nom"],
                    departement["nom"],
                    region["nom"],
                )
    return table


def test_le_decoupage_officiel_est_complet():
    regions = _charger("decoupage.yaml")["regions"]
    assert len(regions) == 10
    assert sum(len(r["departements"]) for r in regions) == 58
    assert len(_arrondissements()) == 360
    assert {r["nom"] for r in regions} == {
        "Adamaoua", "Centre", "Est", "Extrême-Nord", "Littoral",
        "Nord", "Nord-Ouest", "Ouest", "Sud", "Sud-Ouest",
    }  # fmt: skip


def test_les_codes_s_emboitent():
    """Le code d'un arrondissement commence par celui de son département, et ainsi de suite."""
    for region in _charger("decoupage.yaml")["regions"]:
        for departement in region["departements"]:
            assert departement["code"].startswith(region["code"])
            for arrondissement in departement["arrondissements"]:
                assert arrondissement["code"].startswith(departement["code"])


def test_chaque_lieu_est_rattache_a_un_arrondissement_qui_existe():
    connus = _arrondissements()
    for fichier, cle in (
        ("villes.yaml", "villes"),
        ("localites.yaml", "localites"),
        ("quartiers.yaml", "quartiers"),
    ):
        contenu = _charger(fichier)
        assert contenu["fondement"]["source"], fichier
        inconnus = [x["nom"] for x in contenu[cle] if x["arrondissement"] not in connus]
        assert not inconnus, f"{fichier} : arrondissements inconnus pour {inconnus[:5]}"


def test_les_grandes_villes_sont_a_leur_place():
    connus = _arrondissements()
    villes = _charger("villes.yaml")["villes"]

    def lieu(nom: str) -> set[tuple[str, str]]:
        return {connus[v["arrondissement"]][1:] for v in villes if v["nom"] == nom}

    assert lieu("Douala") == {("Wouri", "Littoral")}
    assert lieu("Yaoundé") == {("Mfoundi", "Centre")}
    assert lieu("Garoua") == {("Bénoué", "Nord")}
    assert lieu("Bafoussam") == {("Mifi", "Ouest")}
    # Le doublon erroné du Nord a été retiré : un seul Bamenda, au Nord-Ouest.
    assert lieu("Bamenda") == {("Mezam", "Nord-Ouest")}
    assert lieu("Buéa") == {("Fako", "Sud-Ouest")}


def test_des_quartiers_connus_sont_dans_leur_ville():
    quartiers = _charger("quartiers.yaml")["quartiers"]
    par_ville: dict[str, set[str]] = {}
    for quartier in quartiers:
        par_ville.setdefault(quartier.get("ville", ""), set()).add(quartier["nom"])
    assert {"Akwa", "Bonanjo", "Bonapriso", "Deido", "Makepe"} <= par_ville["Douala"]
    assert {"Bastos", "Biyem-Assi", "Mvog-Ada", "Nlongkak"} <= par_ville["Yaoundé"]
    # Deido et Makepe sont des quartiers, plus des villes.
    noms_des_villes = {v["nom"] for v in _charger("villes.yaml")["villes"]}
    trouve = {"Deido", "Makepe"} & noms_des_villes
    assert not trouve


def test_un_quartier_n_est_range_qu_une_fois_par_arrondissement():
    vus = set()
    for quartier in _charger("quartiers.yaml")["quartiers"]:
        cle = (quartier["arrondissement"], quartier["nom"].lower())
        assert cle not in vus, cle
        vus.add(cle)
