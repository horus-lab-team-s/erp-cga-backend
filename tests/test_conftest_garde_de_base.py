"""Le garde qui dit qu'une suite verte n'a rien vérifié.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Le 30 septembre, la base de test locale a disparu **trois fois** — le répertoire
de données lui-même, qui vit dans un répertoire temporaire et « se jette », comme
l'outil l'annonce. À chaque fois, la suite a rendu :

    3542 passed, 387 skipped     → exit 0

Trois cent quatre-vingt-sept cas — la persistance, le cloisonnement, les parcours
de bout en bout — n'avaient rien vérifié. Lu vite, « 3542 passed » ressemble à un
succès, et j'ai relancé sans m'interroger les deux premières fois.

La chaîne d'intégration porte ce garde depuis longtemps, et le dit bien : « dans
les trois cas la suite reste verte en n'ayant rien vérifié ». Il manquait en local,
là où l'on regarde la dernière ligne cinquante fois par jour.

⚠️ **IL NE FAIT PAS ÉCHOUER LA SUITE.** Sauter est légitime quand on travaille sans
base : l'exiger obligerait à en monter une pour vérifier une fonction pure. Il rend
l'omission impossible à ne pas voir, et rien de plus.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import tests.conftest as conftest


class _RapporteurFeint:
    """Ce que pytest passe au résumé, réduit à ce que le garde emploie."""

    def __init__(self, sautes: int) -> None:
        self.stats = {"skipped": [object()] * sautes}
        self.separateurs: list[str] = []
        self.lignes: list[str] = []

    def write_sep(self, _caractere, titre, **_options):
        self.separateurs.append(titre)

    def write_line(self, ligne, **_options):
        self.lignes.append(ligne)


def _resumer(sautes: int, motif: str | None, monkeypatch) -> _RapporteurFeint:
    monkeypatch.setattr(conftest, "MOTIF_INDISPONIBLE", motif)
    rapporteur = _RapporteurFeint(sautes)
    conftest.pytest_terminal_summary(rapporteur, 0, None)
    return rapporteur


def test_base_absente_il_le_dit_fort(monkeypatch):
    rapporteur = _resumer(387, "connection refused", monkeypatch)

    assert rapporteur.separateurs == ["BASE ABSENTE : LA SUITE N'A PAS TOUT VÉRIFIÉ"]
    ecrit = "\n".join(rapporteur.lignes)
    # ⚠️ LE NOMBRE, et non un avertissement vague : « des tests ont été sautés »
    # ne dit pas si c'est trois ou quatre cents.
    assert "387 cas sautés" in ecrit
    # Le motif, parce qu'un port fermé et un mot de passe faux ne se corrigent pas
    # du même geste.
    assert "connection refused" in ecrit
    # Et le geste à faire, pour qu'on n'ait pas à chercher l'outil.
    assert "postgres-local.sh start" in ecrit


def test_base_presente_il_se_tait(monkeypatch):
    """⚠️ La contre-épreuve, et elle compte autant.

    Un garde qui parle à chaque exécution devient du bruit, et le bruit
    s'apprend à ignorer — ce qui le rendrait pire qu'absent.
    """
    rapporteur = _resumer(0, None, monkeypatch)

    assert rapporteur.separateurs == []
    assert rapporteur.lignes == []
