"""Ce qu'une réponse rend, et ce qui ne doit jamais en sortir.

─────────────────────────────────────────────────────────────────────────────────
LE MIROIR DU PAS 37

Le pas 37 garde ce qu'un client peut **écrire**. Celui-ci garde ce qu'il peut
**lire**.

⚠️ **Le défaut a existé**, et le domaine en porte la mémoire : *« la route
publique de définition du mot de passe rendait le compte, empreinte comprise, à un
appelant non authentifié. Une empreinte Argon2 livrée est de la matière à casser
hors ligne, tranquillement, sans limite de tentatives et sans que rien ne
l'enregistre. »*

La correction retenue est **portée par le champ**, non par la route, et le motif
mérite d'être répété : *« une exclusion à écrire route par route est une exclusion
qu'on oubliera à la prochaine. »*

Ce fichier garde cette exclusion là où elle vit.

UN MODÈLE DE REQUÊTE N'EST PAS UN MODÈLE DE RÉPONSE

C'est la distinction qui rend ce contrôle utilisable. Un corps de requête porte
légitimement un mot de passe : c'est ainsi qu'on se connecte. Une réponse, jamais
— sauf le secret d'enrôlement, rendu une seule fois, et nommé ici avec son motif.

⚠️ **Le contrôle porte sur les modèles, pas sur un appel.** Une sonde qui
interrogerait les routes du jeu de démonstration ne verrait rien : les comptes de
démonstration n'ont pas tous d'empreinte, et un champ vide ne fuit pas. Il
fuirait en production, sur des comptes réels. *Une fuite qui dépend des données
ne se trouve pas en essayant.*
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel

import app
from app.main import app as application

#: Les champs sensibles qui **existent** dans le projet, et dont la valeur ne doit
#: jamais franchir la frontière HTTP en sortie.
#:
#: ⚠️ Reconnus par leur nom. Un secret baptisé autrement échappera au contrôle ;
#: ce n'est pas une raison de ne pas attraper celui qui s'appelle « secret ».
#:
#: ⚠️ **`sceau` y figure alors qu'aucune réponse ne le porte**, et c'est le point :
#: le lien d'acceptation est signé, daté, à usage unique, et il tient lieu de
#: preuve d'identité pour un client qui n'a pas de compte. Le rendre par une route
#: permettrait à n'importe quel collaborateur d'accepter une proforma à la place
#: du client. Le contrôle garde une propriété vraie aujourd'hui.
CHAMPS_SENSIBLES = frozenset(
    {
        "empreinte_mot_de_passe",
        "secret_totp",
        "mot_de_passe",
        "secret",
        "cle_chiffrement",
        "smtp_mot_de_passe",
        "tara_cle_api",
        "sceau",
    }
)

#: Des noms qui ne désignent **rien** aujourd'hui, et qui ne doivent rien désigner.
#:
#: ─────────────────────────────────────────────────────────────────────────────
#: ⚠️ POURQUOI SURVEILLER DES NOMS QUI N'EXISTENT PAS
#:
#: Ce sont les noms qu'on emploie sans réfléchir, souvent en recopiant une
#: bibliothèque anglophone ou une charge utile de prestataire. Le jour où un
#: modèle porte un champ `password`, deux choses doivent arriver : le contrôle de
#: sortie doit le couvrir, et quelqu'un doit **en parler**.
#:
#: Le cas ci-dessous échoue alors, et force cette conversation : le nom doit
#: rejoindre `CHAMPS_SENSIBLES`, ce qui est un geste conscient, et non se glisser
#: dans une liste où personne ne le relira.
#:
#: ⚠️ C'est l'inverse du contrôle précédent, et les deux sont nécessaires : l'un
#: garde ce qui existe, l'autre garde ce qui n'existe pas encore.
#: ─────────────────────────────────────────────────────────────────────────────
NOMS_PROSCRITS = frozenset(
    {"password", "motdepasse", "cle_api", "graine", "empreinte_jeton", "totp"}
)

#: L'union, pour le balayage des réponses.
JAMAIS_EN_SORTIE = CHAMPS_SENSIBLES | NOMS_PROSCRITS

#: Les réponses admises à porter un champ sensible, et **pourquoi**.
EXCEPTIONS = {
    ("Enrolement", "secret"): (
        "Le secret TOTP, rendu une seule fois à l'enrôlement, pour être présenté "
        "en code-barres puis oublié. Il n'apparaît dans aucune lecture ultérieure "
        "du compte : l'entité l'exclut de sa sérialisation. Qui perd son téléphone "
        "réenrôle ; on ne lui rappelle pas son secret."
    ),
}


def _modeles_du_projet() -> set[type[BaseModel]]:
    """Tous les modèles Pydantic définis sous `app/`."""
    trouves: set[type[BaseModel]] = set()
    for info in pkgutil.walk_packages(app.__path__, "app."):
        try:
            module = importlib.import_module(info.name)
        except Exception:  # noqa: BLE001 — un module optionnel ne doit pas arrêter le balayage
            continue
        for _, objet in inspect.getmembers(module, inspect.isclass):
            if issubclass(objet, BaseModel) and objet is not BaseModel:
                trouves.add(objet)
    return trouves


def _routes():
    def descendre(conteneur):
        for route in getattr(conteneur, "routes", []) or []:
            if isinstance(route, APIRoute):
                yield route.path, route
            elif getattr(route, "original_router", None) is not None:
                yield from descendre(route.original_router)

    with TestClient(application):
        return sorted(descendre(application), key=lambda paire: paire[0])


def _champs_recursifs(modele, vus=None, profondeur=0):
    """Les champs du modèle et de ses sous-modèles, avec leur porteur."""
    vus = vus if vus is not None else set()
    if modele is None or profondeur > 3 or modele in vus:
        return []
    vus.add(modele)
    champs = getattr(modele, "model_fields", None)
    if not champs:
        return []
    trouves = [(modele, nom, info) for nom, info in champs.items()]
    for info in champs.values():
        annotation = getattr(info, "annotation", None)
        for candidat in (annotation, *(getattr(annotation, "__args__", ()) or ())):
            trouves += _champs_recursifs(candidat, vus, profondeur + 1)
    return trouves


class TestLeBalayageVoitQuelqueChose:
    """⚠️ La contre-épreuve : deux « aucun ne doit » passeraient si le balayage
    ne trouvait rien."""

    def test_il_trouve_les_modeles(self):
        assert len(_modeles_du_projet()) >= 200

    def test_il_trouve_les_routes_avec_une_reponse(self):
        avec_reponse = [r for _, r in _routes() if r.response_model is not None]
        assert len(avec_reponse) >= 60


class TestAucunSecretNeSortParUneReponse:
    def test_aucune_reponse_ne_porte_un_champ_sensible_serialisable(self):
        """⚠️ **Le défaut historique, retourné en garde.**

        Une empreinte Argon2 livrée se casse hors ligne, sans limite de
        tentatives et sans que rien ne l'enregistre.
        """
        fautives = []
        for chemin, route in _routes():
            modele = route.response_model
            if modele is None:
                continue
            candidats = (modele, *(getattr(modele, "__args__", ()) or ()))
            for candidat in candidats:
                for porteur, nom, info in _champs_recursifs(candidat):
                    if nom not in JAMAIS_EN_SORTIE:
                        continue
                    if getattr(info, "exclude", False):
                        continue
                    if (porteur.__name__, nom) in EXCEPTIONS:
                        continue
                    fautives.append((chemin, porteur.__name__, nom))
        assert not fautives, (
            f"ces réponses peuvent sérialiser un champ sensible : {sorted(set(fautives))}. "
            "Poser `exclude=True` sur le champ — pas sur la route : une exclusion "
            "à écrire route par route est une exclusion qu'on oubliera."
        )

    def test_l_exclusion_est_portee_par_le_champ(self):
        """⚠️ Là où le projet a choisi de la porter, et le motif tient en une
        phrase : une route de plus est une occasion d'oubli de plus."""
        from app.contextes.transverse.domaine.identites import Compte

        info = Compte.model_fields["empreinte_mot_de_passe"]
        assert info.exclude is True

    def test_la_configuration_ne_sort_par_aucune_route(self):
        """Elle porte la clé de chiffrement et l'adresse de la base, mot de passe
        compris. Aucune réponse ne doit la rendre, même partiellement typée."""
        from app.infrastructure.config import Configuration

        for chemin, route in _routes():
            modele = route.response_model
            if modele is None:
                continue
            candidats = (modele, *(getattr(modele, "__args__", ()) or ()))
            for candidat in candidats:
                porteurs = {p for p, _, _ in _champs_recursifs(candidat)}
                assert Configuration not in porteurs, chemin


class TestLesExceptions:
    def test_chaque_exception_porte_son_motif(self):
        assert all(len(motif) > 80 for motif in EXCEPTIONS.values())

    def test_chaque_exception_designe_un_champ_reel(self):
        """⚠️ Une exception qui ne désigne plus rien est une porte laissée
        ouverte pour un nom que quelqu'un réemploiera."""
        modeles = {m.__name__: m for m in _modeles_du_projet()}
        for nom_modele, champ in EXCEPTIONS:
            assert nom_modele in modeles, nom_modele
            assert champ in modeles[nom_modele].model_fields, (nom_modele, champ)

    def test_le_secret_d_enrolement_n_est_rendu_que_la_une_fois(self):
        """Le compte lui-même ne le porte pas : qui perd son téléphone réenrôle."""
        from app.contextes.transverse.domaine.identites import Compte

        assert not (set(Compte.model_fields) & {"secret", "secret_totp"}) or all(
            Compte.model_fields[c].exclude
            for c in set(Compte.model_fields) & {"secret", "secret_totp"}
        )


class TestLaListeSurveilleeNeVieillitPas:
    @pytest.mark.parametrize("champ", sorted(CHAMPS_SENSIBLES))
    def test_chaque_champ_sensible_existe_encore(self, champ):
        """⚠️ Une liste de noms qui vieillit surveille des fantômes, et un champ
        renommé cesse d'être contrôlé sans que rien ne le dise.

        Ce cas a mordu en étant écrit : cinq des dix noms de la première version
        ne désignaient rien. Ils sont passés dans `NOMS_PROSCRITS`, où leur
        absence est la propriété gardée.
        """
        for modele in _modeles_du_projet():
            if champ in modele.model_fields:
                return
        pytest.fail(
            f"le champ « {champ} » n'existe dans aucun modèle : la liste surveille "
            "un fantôme, et un champ renommé n'est plus contrôlé."
        )

    @pytest.mark.parametrize("nom", sorted(NOMS_PROSCRITS))
    def test_aucun_nom_proscrit_n_est_apparu(self, nom):
        """⚠️ Le jour où un modèle porte ce nom, ce cas échoue — et c'est voulu.

        Le nom doit alors rejoindre `CHAMPS_SENSIBLES`, ce qui est un geste
        conscient, précédé d'une question : *ce champ doit-il vraiment exister,
        et sous ce nom-là ?*
        """
        porteurs = [
            modele.__name__
            for modele in _modeles_du_projet()
            if nom in modele.model_fields
        ]
        assert not porteurs, (
            f"le champ « {nom} » est apparu dans {porteurs}. Le déplacer vers "
            "`CHAMPS_SENSIBLES` après avoir vérifié qu'il doit exister, et sous "
            "ce nom."
        )
