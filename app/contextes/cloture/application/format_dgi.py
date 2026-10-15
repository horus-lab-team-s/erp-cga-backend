"""Préparer la DSF au format DGI : les états officiels et ce qui empêche de les tirer.

⚠️ RIEN NE PART D'UN EXERCICE OUVERT, ni d'états incomplets : un compte sans ligne
officielle, ou un bilan qui ne s'équilibre pas, bloque le fichier. Le comptable le lit
avant de télécharger, pas après le rejet du portail.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.contextes.cloture.domaine.etats_dgi import EtatsDgi, etablir_les_etats_dgi

__all__ = ["EtatDuFormatDgi", "etat_du_format_dgi"]


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
