"""Charge les profils d'échange depuis le référentiel sur disque.

Un adaptateur, et rien d'autre. Il lit des fichiers et construit des profils ; il
ne décide d'aucun format.

⚠️ **Le moteur ne connaît aucun logiciel.** Il sait qu'un profil décrit des
colonnes, un séparateur et un encodage ; il ne sait pas que Sage existe. Brancher
un progiciel de plus, c'est déposer un fichier ici — pas modifier du code.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.comptabilite.domaine.echange import (
    CHAMPS_DISPONIBLES,
    Colonne,
    FormeDuSens,
    ProfilDEchange,
)

__all__ = ["ProfilInconnu", "charger_les_profils", "charger_un_profil"]

#: Les fins de ligne, nommées plutôt qu'échappées. `"\r\n"` dans un fichier YAML
#: se relit mal et s'édite encore plus mal : un espace de trop et le fichier
#: produit devient illisible pour l'importeur, sans que rien ne le signale.
_FINS_DE_LIGNE = {"crlf": "\r\n", "lf": "\n"}


class ProfilInconnu(KeyError):
    """Aucun profil de ce code au référentiel. Le message nomme ceux qui existent."""


def charger_les_profils(dossier: Path) -> dict[str, ProfilDEchange]:
    """Tous les profils du dossier, indexés par code.

    ⚠️ **Le code du fichier fait foi sur le nom du fichier.** Renommer un fichier
    ne doit pas changer le profil qu'un écran désigne : c'est le code qui est cité
    dans une configuration de client, pas le chemin.
    """
    profils: dict[str, ProfilDEchange] = {}
    for chemin in sorted(dossier.glob("*.yaml")):
        profil = _lire(chemin)
        if profil.code in profils:
            raise ValueError(
                f"deux profils portent le code « {profil.code} » : "
                f"{chemin.name} et un autre. Un code désigne un profil et un seul."
            )
        profils[profil.code] = profil
    return profils


def charger_un_profil(dossier: Path, code: str) -> ProfilDEchange:
    profils = charger_les_profils(dossier)
    if code not in profils:
        raise ProfilInconnu(
            f"aucun profil d'échange « {code} ». Les profils du référentiel sont : "
            f"{', '.join(sorted(profils)) or 'aucun'}."
        )
    return profils[code]


def _lire(chemin: Path) -> ProfilDEchange:
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8"))

    fin = str(donnees.get("fin_de_ligne", "crlf")).lower()
    if fin not in _FINS_DE_LIGNE:
        raise ValueError(
            f"{chemin.name} : fin de ligne « {fin} » inconnue. "
            f"Les valeurs admises sont : {', '.join(sorted(_FINS_DE_LIGNE))}."
        )

    colonnes = []
    for entree in donnees["colonnes"]:
        champ = entree["champ"]
        if champ not in CHAMPS_DISPONIBLES:
            # ⚠️ Refusé, jamais ignoré. Une colonne silencieusement vide ferait
            # importer au client un fichier amputé qu'il croirait complet.
            raise ValueError(
                f"{chemin.name} : le champ « {champ} » n'existe pas au pivot. "
                f"Les champs disponibles sont : {', '.join(sorted(CHAMPS_DISPONIBLES))}."
            )
        colonnes.append(Colonne(**entree))

    # ⚠️ L'encodage est éprouvé **au chargement**, pas à l'export. Un encodage
    # inconnu découvert au moment où un client attend son fichier est un incident ;
    # découvert au démarrage, c'est une faute de frappe.
    encodage = donnees.get("encodage", "cp1252")
    try:
        "é".encode(encodage)
    except LookupError as echec:
        raise ValueError(
            f"{chemin.name} : encodage « {encodage} » inconnu de Python."
        ) from echec

    return ProfilDEchange(
        code=donnees["code"],
        libelle=donnees["libelle"],
        remarque=donnees.get("remarque", ""),
        separateur=donnees.get("separateur", ";"),
        encodage=encodage,
        fin_de_ligne=_FINS_DE_LIGNE[fin],
        decimale=donnees.get("decimale", ","),
        format_date=donnees.get("format_date", "%d/%m/%Y"),
        entete=donnees.get("entete", True),
        forme_du_sens=FormeDuSens(
            donnees.get("forme_du_sens", FormeDuSens.COLONNES_SEPAREES.value)
        ),
        marqueur_debit=donnees.get("marqueur_debit", "D"),
        marqueur_credit=donnees.get("marqueur_credit", "C"),
        colonnes=tuple(colonnes),
    )
