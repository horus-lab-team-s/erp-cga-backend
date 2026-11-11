"""Préparer la DSF au format DGI : les états officiels et ce qui empêche de les tirer.

⚠️ RIEN NE PART D'UN EXERCICE OUVERT, ni d'états incomplets : un compte sans ligne
officielle, ou un bilan qui ne s'équilibre pas, bloque le fichier. Le comptable le lit
avant de télécharger, pas après le rejet du portail.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.contextes.cloture.domaine.etats_dgi import EtatsDgi, etablir_les_etats_dgi
from app.contextes.cloture.domaine.etats_smt import EtatsSmt, etablir_les_etats_smt

__all__ = ["EtatDuFormatDgi", "EtatDuFormatSmt", "etat_du_format_dgi", "etat_du_format_smt"]


@dataclass(frozen=True)
class EtatDuFormatDgi:
    etats: EtatsDgi
    precedents: EtatsDgi | None
    obstacles: tuple[str, ...]

    @property
    def pret(self) -> bool:
        return not self.obstacles


def etat_du_format_dgi(
    soldes: dict[str, Decimal],
    *,
    soldes_precedents: dict[str, Decimal] | None,
    exercice_clos: bool,
    systeme_normal: bool = True,
) -> EtatDuFormatDgi:
    etats = etablir_les_etats_dgi(soldes)
    precedents = etablir_les_etats_dgi(soldes_precedents) if soldes_precedents else None
    obstacles = []
    if not systeme_normal:
        obstacles.append(
            "le dossier relève de l'IGS : sa DSF se dépose au Système Minimal de Trésorerie "
            "(décret n° 2019/262, art. 6), pas au modèle Normal."
        )
    if not exercice_clos:
        obstacles.append("l'exercice n'est pas clos : la DSF se tire de comptes arrêtés.")
    if not any(soldes.values()):
        # 11 novembre 2026 : un exercice sans écriture validée donnait un fichier « prêt »
        # rempli de zéros, que le portail aurait accepté (constaté au contrôle des exports).
        obstacles.append(
            "aucune écriture validée sur l'exercice : le fichier sortirait vide. Reprenez ou "
            "saisissez les écritures de l'exercice avant de tirer la DSF."
        )
    if etats.comptes_non_couverts:
        obstacles.append(
            "comptes sans ligne dans le modèle officiel : "
            f"{', '.join(etats.comptes_non_couverts)}. Ventilez-les avant de tirer le fichier."
        )
    if not etats.equilibre:
        obstacles.append(
            f"le bilan ne s'équilibre pas (actif {etats.actif['BZ'].net}, "
            f"passif {etats.passif['DZ']})."
        )
    return EtatDuFormatDgi(etats=etats, precedents=precedents, obstacles=tuple(obstacles))


@dataclass(frozen=True)
class EtatDuFormatSmt:
    """11 novembre 2026 : la DSF des adhérents à l'IGS, au modèle officiel SMT."""

    etats: EtatsSmt
    precedents: EtatsSmt | None
    obstacles: tuple[str, ...]

    @property
    def pret(self) -> bool:
        return not self.obstacles


def etat_du_format_smt(
    soldes: dict[str, Decimal],
    *,
    ouverture: dict[str, Decimal],
    precedent: tuple[dict[str, Decimal], dict[str, Decimal]] | None,
    exercice_clos: bool,
) -> EtatDuFormatSmt:
    """`precedent` : (soldes de clôture, soldes d'ouverture) de l'exercice N-1, s'il existe."""
    etats = etablir_les_etats_smt(soldes, ouverture=ouverture)
    precedents = (
        etablir_les_etats_smt(precedent[0], ouverture=precedent[1]) if precedent else None
    )
    obstacles = []
    if not exercice_clos:
        obstacles.append("l'exercice n'est pas clos : la DSF se tire de comptes arrêtés.")
    if not any(soldes.values()):
        # 11 novembre 2026 : un exercice sans écriture validée donnait un fichier « prêt »
        # rempli de zéros, que le portail aurait accepté (constaté au contrôle des exports).
        obstacles.append(
            "aucune écriture validée sur l'exercice : le fichier sortirait vide. Reprenez ou "
            "saisissez les écritures de l'exercice avant de tirer la DSF."
        )
    if etats.comptes_non_couverts:
        obstacles.append(
            "comptes sans ligne dans le modèle SMT : "
            f"{', '.join(etats.comptes_non_couverts)}. Ventilez-les avant de tirer le fichier."
        )
    if not etats.equilibre:
        obstacles.append(
            f"le bilan SMT ne s'équilibre pas (actif {etats.total_actif}, "
            f"passif {etats.total_passif})."
        )
    if not etats.concorde:
        obstacles.append(
            f"le résultat du modèle SMT ({etats.resultat_g}) ne retombe pas sur le résultat "
            f"comptable ({etats.resultat_comptable}) : une écriture de stock ne passe pas par "
            "603 ou 73, ou un compte est mal classé. Rien ne sort tant que l'écart reste."
        )
    return EtatDuFormatSmt(etats=etats, precedents=precedents, obstacles=tuple(obstacles))
