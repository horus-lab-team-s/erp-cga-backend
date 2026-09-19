"""Un magasin mémoire par sorte, dans tout le contexte Souscription.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER A ÉTÉ ÉCRIT, ET CE QU'IL REMPLACE

`travail_de_relance` portait, à côté de ses fabriques, cette phrase :

    « `test_travail_de_relance.py` garde cette unicité en comptant les fabriques. »

**Ce fichier n'existait pas.** Le garde annoncé n'avait jamais été écrit, et le
défaut qu'il devait empêcher s'était produit ailleurs : `routes_acquisition` et
`abonne_de_relance` déclaraient chacun leur `DepotDossiersMemoire`. Mesuré, un
dossier déposé par le formulaire public était introuvable pour l'abonné, et
`poster_une_relance` levait `DossierIntrouvable` sur **chaque** relance, jusqu'à la
quarantaine, sans qu'aucune erreur de programmation n'apparaisse nulle part.

⚠️ **Un commentaire qui nomme un garde-fou vaut moins qu'un garde-fou**, parce
qu'il rassure autant et ne surveille rien. C'est la deuxième fois du chantier
qu'une affirmation du projet sur lui-même se révèle fausse en la vérifiant.

CE QUE CE GARDE LIT, ET POURQUOI PAS UNE LISTE

Il lit **le code**, par son arbre syntaxique, et non une liste tenue à la main.
Trois garde-fous du projet contrôlaient des listes hand-maintenues, et une liste
qu'il faut penser à mettre à jour ne surveille que ce qu'on a pensé à y mettre.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

CONTEXTE = Path(__file__).resolve().parents[1] / "app" / "contextes" / "souscription"


def _fabriques() -> dict[str, list[str]]:
    """Les fonctions mémoïsées qui construisent un dépôt mémoire, par sorte.

    ⚠️ La détection porte sur **ce que la fonction construit**, et non sur son
    nom. Une fabrique nommée `_magasin()` ou `_le_depot()` compterait autant :
    c'est l'appel `DepotXMemoire()` qui fait le magasin, pas l'intitulé.
    """
    par_sorte: dict[str, list[str]] = defaultdict(list)
    for fichier in sorted(CONTEXTE.rglob("*.py")):
        arbre = ast.parse(fichier.read_text(encoding="utf-8"))
        for noeud in ast.walk(arbre):
            if not isinstance(noeud, ast.FunctionDef):
                continue
            memoise = any(
                (isinstance(d, ast.Name) and d.id == "lru_cache")
                or (isinstance(d, ast.Attribute) and d.attr == "lru_cache")
                or (
                    isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Name)
                    and d.func.id == "lru_cache"
                )
                for d in noeud.decorator_list
            )
            if not memoise:
                continue
            for interne in ast.walk(noeud):
                if (
                    isinstance(interne, ast.Call)
                    and isinstance(interne.func, ast.Name)
                    and interne.func.id.startswith("Depot")
                    and interne.func.id.endswith("Memoire")
                ):
                    chemin = fichier.relative_to(CONTEXTE).as_posix()
                    par_sorte[interne.func.id].append(f"{chemin}::{noeud.name}")
    return dict(par_sorte)


class TestUnSeulMagasinParSorte:
    def test_le_balayage_trouve_bien_des_fabriques(self):
        """⚠️ **La contre-épreuve, et elle est indispensable.**

        Un balayage qui ne trouverait rien — chemin faux, arbre mal parcouru,
        décorateur écrit autrement — passerait le cas suivant en déclarant
        triomphalement qu'aucune sorte n'est dupliquée.

        Un cas ne peut mesurer un garde que s'il existe une situation où le garde
        change quelque chose.
        """
        trouvees = _fabriques()
        assert len(trouvees) >= 3, trouvees
        assert "DepotDossiersMemoire" in trouvees

    def test_aucune_sorte_n_est_construite_a_deux_endroits(self):
        """⚠️ En mémoire, le magasin **est** la persistance.

        Deux instances sont deux bases sans lien : l'une reçoit les écritures,
        l'autre sert les lectures, et la panne ne se signale que par une
        quarantaine que quelqu'un doit penser à regarder.
        """
        doublons = {
            sorte: endroits
            for sorte, endroits in _fabriques().items()
            if len(endroits) > 1
        }
        assert not doublons, (
            "ces magasins mémoire sont construits à plusieurs endroits, donc "
            f"plusieurs fois : {doublons}. En mémoire, deux instances sont deux "
            "bases sans lien. Voir app/contextes/souscription/adaptateurs/"
            "sortant/magasins_memoire.py."
        )

    def test_les_dossiers_ont_leur_fabrique_au_sortant(self):
        """Elle vit avec les dépôts, et non chez l'un de ses trois appelants.

        ⚠️ Chez l'un d'eux, les deux autres devraient l'importer, et c'est
        exactement le raisonnement qui fait qu'on en déclare une seconde « pour
        ne pas dépendre des routes ».
        """
        (endroit,) = _fabriques()["DepotDossiersMemoire"]
        assert endroit.startswith("adaptateurs/sortant/magasins_memoire.py"), endroit


class TestLesTroisAppelantsPartagentLeMagasin:
    def test_routes_abonne_et_veille_lisent_le_meme(self):
        """⚠️ La preuve par l'objet, en plus de la preuve par le code.

        Le balayage syntaxique dit qu'il n'y a qu'une fabrique ; il ne dit pas
        que les trois appelants s'en servent. Un quatrième pourrait construire un
        `DepotDossiersMemoire()` sans `lru_cache` et échapper au comptage.
        """
        from app.contextes.souscription.adaptateurs.entrant import (
            abonne_de_relance,
            routes_acquisition,
            travail_de_veille,
        )

        magasin = travail_de_veille.dossiers_memoire()
        assert routes_acquisition._dossiers_memoire() is magasin
        assert abonne_de_relance._dossiers_memoire() is magasin

    def test_ce_qui_est_ecrit_par_la_route_est_lu_par_l_abonne(self):
        """Le défaut tel qu'il se manifestait, retourné en garde.

        Avant la correction, cette lecture levait `DossierIntrouvable`, et le
        relais comptait un échec de remise pour un dossier parfaitement présent
        en base.
        """
        from datetime import datetime

        from app.contextes.souscription.adaptateurs.entrant import (
            abonne_de_relance,
            routes_acquisition,
        )
        from app.contextes.souscription.adaptateurs.sortant.magasins_memoire import (
            vider_les_dossiers_memoire,
        )
        from app.contextes.souscription.domaine.demande_de_contact import (
            Canal,
            Consentement,
            DemandeDeContact,
        )
        from app.contextes.souscription.domaine.dossier_commercial import (
            ouvrir_un_dossier,
        )

        vider_les_dossiers_memoire()
        try:
            jour = datetime(2026, 9, 10, 9, 0)
            demande = DemandeDeContact(
                identifiant="D-PARTAGE",
                deposee_le=jour,
                nom="Abena Ndzana",
                telephone="699112233",
                service_souhaite="creation-sarl",
                canal_prefere=Canal.APPEL,
                consentement=Consentement(
                    accorde=False,
                    recueilli_le=jour,
                    version_du_texte="consentement-whatsapp-v1",
                ),
            )
            routes_acquisition._dossiers_memoire().enregistrer(
                ouvrir_un_dossier("DOS-PARTAGE", demande)
            )
            relu = abonne_de_relance._depots()[0].lire("DOS-PARTAGE")
            assert relu.reference == "DOS-PARTAGE"
        finally:
            vider_les_dossiers_memoire()
