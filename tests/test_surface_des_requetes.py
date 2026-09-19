"""Ce qu'une route accepte d'un client, et ce qu'elle ne doit jamais accepter.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Le pas 36 a trouvé une route qui acceptait l'entité du domaine comme corps de
requête. Un adhérent — le **client**, pas le cabinet — pouvait donc déposer une
pièce en la déclarant **déjà comptabilisée**, avec une empreinte fabriquée,
antidatée de six ans, et sans auteur.

Le défaut n'était pas une étourderie locale : c'est une **classe** de défaut, et
elle revient chaque fois qu'on gagne trois lignes en réutilisant l'entité.

Ce fichier balaie donc les trente-cinq corps de requête du système, à deux
niveaux :

* aucun ne doit être une **entité du domaine**, sauf exception nommée ;
* aucun ne doit porter un **champ que le système possède** — un état, un auteur,
  un horodatage d'arrivée.

⚠️ **Les deux contrôles sont nécessaires.** Le premier attrape la réutilisation
d'un agrégat ; le second attrape un modèle de requête écrit exprès mais qui
expose un champ de trop. Le contexte comptable énonçait déjà la règle : *« Il ne
porte ni numéro, ni état, ni valideur. Ce qu'un client ne peut pas envoyer n'a pas
besoin d'être contrôlé. »*
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.main import app

#: Les entités du domaine admises comme corps de requête, et **pourquoi**.
#:
#: ⚠️ Une exception sans motif est une exception qu'on ajoutera sans y penser. Le
#: motif est donc la valeur, et il est relu à chaque ajout.
EXCEPTIONS = {
    "FactureAControler": (
        "La description d'une facture soumise au moteur de conformité. Elle n'est "
        "pas un agrégat persisté : la route calcule un verdict et le rend, sans "
        "rien écrire. Il n'y a donc aucun champ dont le système soit propriétaire, "
        "et c'est précisément cette description que l'appelant doit fournir."
    ),
}

#: Les champs dont le système est propriétaire, quel que soit le contexte.
#:
#: ⚠️ Reconnus par leur **nom**, et c'est assumé : un contrôle par le nom attrape
#: le cas ordinaire — celui où l'on recopie un champ de l'entité sans y penser —
#: et c'est celui-là qui se produit. Un champ du système baptisé autrement lui
#: échappera ; ce n'est pas une raison de ne rien contrôler.
CHAMPS_DU_SYSTEME = frozenset(
    {
        # L'état d'un agrégat ne se déclare pas : il découle d'un geste.
        "etat",
        "statut",
        # Les horodatages d'arrivée : « seule celle-ci fait foi ».
        "cree_le",
        "recue_le",
        "publie_le",
        "clos_le",
        "affecte_le",
        "payee_le",
        "signale_le",
        "confirme_le",
        "solde_le",
        "derniere_verification",
        "validee_le",
        # Les auteurs : un acte est personnel, jamais anonyme ni usurpé.
        "depose_par",
        "saisie_par",
        "validee_par",
        "emis_par",
        # Les compteurs et les traces que seul le système incrémente.
        "reaffectations",
        "responsables_passes",
        "verifications",
        "tentatives",
        # Les secrets, qui n'entrent jamais par une requête ordinaire.
        "empreinte_mot_de_passe",
    }
)


def _routes():
    """Toutes les routes montées, sous-routeurs compris.

    ⚠️ `app.routes` ne rend que les routes de premier niveau : les contextes sont
    montés en sous-routeurs, et un balayage naïf voit **une** route au lieu de cent
    vingt-deux. Un contrôle qui ne regarde rien passe toujours.
    """

    def descendre(conteneur):
        for route in getattr(conteneur, "routes", []) or []:
            if isinstance(route, APIRoute):
                yield route.path, route
            elif getattr(route, "original_router", None) is not None:
                yield from descendre(route.original_router)

    with TestClient(app):
        return sorted(descendre(app), key=lambda paire: paire[0])


def _corps_de_requete():
    """Chaque route qui accepte un corps, et le modèle de ce corps."""
    for chemin, route in _routes():
        if not {"POST", "PUT", "PATCH"} & set(route.methods):
            continue
        if route.body_field is None:
            continue
        modele = getattr(route.body_field.field_info, "annotation", None)
        if modele is None:
            continue
        yield sorted(route.methods)[0], chemin, modele


def _auteurs_declares(modele, chemin, vus):
    """Tout champ `par` ou `*_par`, à tout niveau d'imbrication.

    Voir `test_aucun_auteur_ne_se_declare`.
    """
    from pydantic import BaseModel

    if not (isinstance(modele, type) and issubclass(modele, BaseModel)) or modele in vus:
        return
    vus.add(modele)
    for nom, info in modele.model_fields.items():
        if nom == "par" or nom.endswith("_par"):
            yield f"{chemin}.{nom}"
        annotation = info.annotation
        for sous in (*getattr(annotation, "__args__", ()), annotation):
            yield from _auteurs_declares(sous, f"{chemin}.{nom}", vus)


class TestLeBalayageVoitQuelqueChose:
    """⚠️ **La contre-épreuve, et elle est indispensable ici.**

    Les deux contrôles de ce fichier sont des « aucun ne doit ». Un balayage qui
    ne trouverait rien — chemin faux, sous-routeurs non parcourus — les ferait
    passer tous les deux en déclarant triomphalement que tout va bien.

    C'est exactement ce qui est arrivé en écrivant ce fichier : le premier
    balayage voyait **une** route au lieu de cent vingt-deux.
    """

    def test_il_trouve_les_routes(self):
        assert len(_routes()) >= 100

    def test_il_trouve_les_corps_de_requete(self):
        assert len(list(_corps_de_requete())) >= 30


class TestAucuneEntiteDuDomaineEnCorpsDeRequete:
    def test_le_balayage_reconnait_un_auteur_imbrique(self):
        """⚠️ La contre-épreuve du contrôle des auteurs (pas 63) : un détecteur cassé ne
        trouverait aucun auteur, et le contrôle passerait en silence."""
        from pydantic import BaseModel

        class Ligne(BaseModel):
            valide_par: str

        class Corps(BaseModel):
            par: str
            lignes: list[Ligne]
            facultative: Ligne | None = None

        assert sorted(_auteurs_declares(Corps, "Corps", set())) == [
            "Corps.lignes.valide_par",
            "Corps.par",
        ]

    def test_le_balayage_reconnait_une_entite_du_domaine(self):
        """Le contrôle repose sur le module de définition. Si cette convention
        changeait, le contrôle cesserait de voir sans cesser de passer."""
        from app.contextes.collecte.domaine.pieces import PieceJustificative

        assert ".domaine." in PieceJustificative.__module__

    def test_aucune_route_n_accepte_un_agregat(self):
        """⚠️ **Le défaut du pas 36, retourné en garde.**

        Réutiliser l'entité gagne trois lignes et donne au client la plume du
        système : il déclare l'état, l'auteur, l'horodatage d'arrivée. Le domaine
        a beau écrire la règle, c'est la route qui décide.
        """
        fautives = [
            (methode, chemin, modele.__name__)
            for methode, chemin, modele in _corps_de_requete()
            if ".domaine." in getattr(modele, "__module__", "")
            and modele.__name__ not in EXCEPTIONS
        ]
        assert not fautives, (
            f"ces routes acceptent une entité du domaine comme corps : {fautives}. "
            "Écrire un modèle de requête qui ne porte que ce qu'un client peut "
            "légitimement déclarer, et laisser le système poser le reste."
        )

    def test_chaque_exception_porte_son_motif(self):
        """Une exception sans motif est une exception qu'on ajoutera sans y
        penser."""
        assert all(len(motif) > 80 for motif in EXCEPTIONS.values())

    def test_les_exceptions_sont_toutes_employees(self):
        """⚠️ Une exception devenue inutile doit disparaître.

        Laissée en place, elle rouvrirait la porte le jour où quelqu'un réemploie
        ce nom, sans qu'aucune discussion n'ait lieu.
        """
        employes = {
            modele.__name__
            for _, _, modele in _corps_de_requete()
            if ".domaine." in getattr(modele, "__module__", "")
        }
        assert set(EXCEPTIONS) <= employes, sorted(set(EXCEPTIONS) - employes)


class TestAucunChampDuSystemeDansUnCorpsDeRequete:
    def test_aucune_route_n_expose_un_champ_du_systeme(self):
        """⚠️ Le second niveau : un modèle de requête écrit exprès, mais qui
        expose un champ de trop.

        La complétude et le score de risque lisent l'état ; les relances et la
        veille lisent les horodatages ; l'audit lit l'auteur. Chacun de ces champs
        laissé au client défait une garantie que le reste du système construit.
        """
        fautives = []
        for methode, chemin, modele in _corps_de_requete():
            exposes = sorted(set(getattr(modele, "model_fields", {})) & CHAMPS_DU_SYSTEME)
            if exposes:
                fautives.append((methode, chemin, modele.__name__, exposes))
        assert not fautives, (
            f"ces corps de requête portent des champs du système : {fautives}. "
            "Le système les pose ; un client qui les envoie doit être refusé, pas "
            "cru."
        )

    def test_aucun_auteur_ne_se_declare(self):
        """⚠️ **Un auteur se lit sur la session, il ne s'écrit jamais dans le corps.** (pas 63)

        ─────────────────────────────────────────────────────────────────────────
        La liste des noms surveillés contenait `depose_par`, `saisie_par`,
        `validee_par`, `emis_par`, et laissait passer deux auteurs :

        * `par`, à la clôture d'un rappel : qui a appelé le client s'écrivait dans
          la requête, et n'importe qui pouvait y mettre le nom d'un collègue ;
        * `valide_par`, à l'émission d'une proforma : qui engage le cabinet sur un
          prix s'écrivait aussi. Le contrôle interne lit `chiffre_par != valide_par`
          pour dire si la séparation des tâches est respectée ; écrire « direction »
          dans le corps simulait un contrôle que personne n'avait fait.

        Ce cas ne compare plus des noms à une liste : **tout champ `par` ou `*_par`
        est un auteur**, à tout niveau d'imbrication. Le contrôle par liste attrapait
        le nom recopié ; celui-ci attrape la forme.
        ─────────────────────────────────────────────────────────────────────────
        """
        fautives = [
            (methode, route, champ)
            for methode, route, modele in _corps_de_requete()
            for champ in _auteurs_declares(modele, modele.__name__, set())
        ]
        assert not fautives, (
            f"ces corps de requête déclarent un auteur : {fautives}. L'auteur d'un acte "
            "est le compte de la session (`acces.compte`) ; un client qui l'écrit doit "
            "être refusé, pas cru."
        )

    @pytest.mark.parametrize("champ", sorted(CHAMPS_DU_SYSTEME))
    def test_chaque_champ_surveille_existe_quelque_part(self, champ):
        """⚠️ Une liste de noms surveillés qui vieillit surveille des fantômes.

        Ce cas vérifie que chaque nom désigne un champ réel d'une entité du
        projet. Un champ renommé fait tomber ce cas, ce qui est le seul moment où
        quelqu'un relira la liste.
        """
        import ast

        from app.infrastructure.config import RACINE_DEPOT

        racine = RACINE_DEPOT / "Backend_erp_cga" / "app"
        for fichier in racine.rglob("*.py"):
            arbre = ast.parse(fichier.read_text(encoding="utf-8"))
            for noeud in ast.walk(arbre):
                cible = getattr(noeud, "target", None)
                if isinstance(noeud, ast.AnnAssign) and isinstance(cible, ast.Name):
                    if cible.id == champ:
                        return
        pytest.fail(
            f"le champ « {champ} » n'existe nulle part : la liste surveille un "
            "fantôme, et un champ renommé n'est plus contrôlé."
        )
