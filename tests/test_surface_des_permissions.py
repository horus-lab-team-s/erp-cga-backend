"""Ce qu'un appelant peut faire, et ce qu'il doit prouver avant.

─────────────────────────────────────────────────────────────────────────────────
LE TROISIÈME VOLET

Le pas 37 garde ce qu'un client peut **écrire**, le pas 38 ce qu'il peut **lire**.
Celui-ci garde ce qu'il peut **faire** : quelles routes n'exigent aucune
permission.

Vingt-huit sur cent vingt-deux. Toutes légitimes, vérifiées une par une — et
c'est précisément pourquoi ce fichier existe. **Une liste de vingt-huit exceptions
toutes justifiées est une liste où la vingt-neuvième passera inaperçue.**

⚠️ La liste est **close** : une route nouvelle sans contrôle de permission fait
échouer ce cas, et oblige à écrire son motif. Écrire un motif est peu de travail ;
ne pas pouvoir en écrire un est l'information recherchée.

CE QUE CE CONTRÔLE NE DIT PAS

Il dit qu'une permission est exigée, pas que c'est **la bonne**. Un `LIRE_DOSSIER`
là où il faudrait `VALIDER_ECRITURE` lui échappe. C'est un contrôle de présence,
et il est écrit comme tel.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import inspect
import re

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.main import app

#: Les routes qui n'exigent aucune permission, et **pourquoi**.
#:
#: ⚠️ Cinq familles, et le motif de chacune tient à une phrase. Une exception dont
#: le motif tient à un paragraphe est une exception qui n'en est pas une.
PUBLIQUES: dict[tuple[str, str], str] = {
    # ── La vitrine : c'est un site, il est public par destination ──
    ("GET", "/vitrine/annonce"): "Contenu du site public, écrit pour être lu.",
    ("GET", "/vitrine/articles"): "Contenu du site public, écrit pour être lu.",
    ("GET", "/vitrine/articles/{slug}"): "Contenu du site public, écrit pour être lu.",
    ("GET", "/vitrine/institutions"): "Contenu du site public, écrit pour être lu.",
    ("GET", "/acquisition/canaux"): (
        "La vitrine doit savoir quels canaux proposer au visiteur. Exiger une "
        "session pour afficher un formulaire reviendrait à demander à quelqu'un de "
        "s'inscrire pour poser une question."
    ),
    ("POST", "/acquisition/demandes"): (
        "Le seul point d'entrée du parcours. Un visiteur n'a pas de compte, et "
        "c'est justement ce que cette route existe pour changer."
    ),
    ("POST", "/souscription/devis"): (
        "Établir un devis est le premier geste commercial : il précède le compte."
    ),
    ("GET", "/souscription/services"): (
        "Le catalogue des prestations vendues et de leurs tarifs : c'est ce "
        "qu'un prospect vient lire avant de demander un devis."
    ),
    # ── L'authentification elle-même : on ne peut pas exiger d'être connecté ──
    ("POST", "/transverse/session"): (
        "Ouvrir une session est la façon de prouver son identité : on ne peut "
        "pas exiger d'être connecté pour se connecter."
    ),
    ("DELETE", "/transverse/session"): (
        "Fermer sa propre session. La session courante fait foi ; il n'y a rien à "
        "exiger de plus, et rien à laisser faire sur celle d'un autre."
    ),
    ("POST", "/transverse/session/renforcement"): (
        "Élever sa propre session par un second facteur. Elle porte déjà son "
        "titulaire."
    ),
    ("GET", "/transverse/moi"): (
        "Lire son propre compte : la session en désigne le titulaire, et il n'y "
        "a rien à exiger de plus."
    ),
    ("POST", "/transverse/second-facteur"): (
        "On enrôle pour soi-même et pour personne d'autre : un administrateur qui "
        "enrôlerait le facteur d'un tiers en détiendrait le second facteur."
    ),
    ("POST", "/transverse/second-facteur/confirmation"): (
        "Confirmer son propre premier enrôlement. La session désigne le titulaire, et "
        "le jeton reçu à l'adresse du compte est la preuve : il est refusé s'il "
        "appartient à un autre compte (pas 62)."
    ),
    ("POST", "/transverse/mot-de-passe/oubli"): (
        "Demandée par quelqu'un qui, par définition, ne peut pas se connecter."
    ),
    ("POST", "/transverse/mot-de-passe/definition"): (
        "Le jeton reçu par courriel est la preuve. Exiger une session en plus "
        "rendrait la réinitialisation impossible à celui qui en a besoin."
    ),
    ("GET", "/transverse/notifications"): (
        "Ses propres notifications (pas 94). La session désigne le lecteur ; ce sont les "
        "abonnements du référentiel qui filtrent, par permission, périmètre ou compte nommé."
    ),
    ("POST", "/transverse/notifications/lecture"): (
        "Avancer sa propre position de lecture. Elle ne porte que sur le compte de la session."
    ),
    ("GET", "/transverse/services/recherche"): (
        "Les sources de la recherche globale que la session peut appeler (pas 93). Elle "
        "exige une session, et filtre elle-même par permission : ce qu'elle révèle est "
        "déjà dans le schéma de l'API, et chaque source contrôle son périmètre."
    ),
    ("GET", "/transverse/roles"): (
        "Le catalogue des rôles et de leurs permissions. Il décrit le produit, pas "
        "ses données : un écran de création de compte doit pouvoir l'afficher."
    ),
    # ── Les liens signés : le sceau ou la référence tient lieu de preuve ──
    ("GET", "/acquisition/proformas/{numero}/consultation"): (
        "Le client lit sa proforma avant de l'accepter, sans compte. Le lien signé est "
        "l'authentification, vérifié avant toute lecture : sans lui, un numéro existant "
        "et un numéro inventé reçoivent la même réponse (pas 67)."
    ),
    ("POST", "/acquisition/proformas/{numero}/acceptation"): (
        "Le client accepte par un lien signé, daté, à usage unique. Il n'a pas de "
        "compte, et lui en imposer un ferait renoncer la moitié des acceptations."
    ),
    ("GET", "/souscription/devis/{reference}"): (
        "La référence circule dans un lien et protège seule le devis : elle porte "
        "64 bits d'aléa. Voir `TestLesReferencesNeSeDevinentPas`."
    ),
    ("POST", "/souscription/devis/{reference}/engagement"): (
        "S'engager sur son propre devis, avant d'avoir un compte : le compte n'est "
        "ouvert que si le paiement aboutit."
    ),
    ("GET", "/souscription/souscriptions/{reference}"): (
        "La page sur laquelle le navigateur revient après le paiement. Elle doit "
        "être lisible par quelqu'un dont le compte n'existe pas encore."
    ),
    ("GET", "/souscription/souscriptions/{reference}/echeancier"): (
        "Même page de suivi, même référence non devinable, même raison."
    ),
    # ── Les appels de machines : le prestataire n'a pas de session ──
    ("POST", "/souscription/notification/tara"): (
        "Le prestataire de paiement appelle cette adresse. Il ne s'authentifie "
        "pas : c'est le rapprochement sur une clé que nous avons tirée qui fait "
        "foi, jamais l'appel lui-même."
    ),
    ("GET", "/sante"): (
        "Un orchestrateur la sonde toutes les quelques secondes, sans identité. "
        "Elle ne rend que l'état, jamais le détail d'une panne."
    ),
    # ── Le référentiel normatif : ce sont des valeurs de la loi ──
    ("GET", "/referentiel/parametres"): (
        "Des taux et des seuils qui viennent du Code général des impôts. Les "
        "cacher ne protégerait rien et empêcherait un adhérent de vérifier un "
        "calcul."
    ),
    ("GET", "/referentiel/parametres/{code}"): (
        "Un paramètre légal, lu à une date. Même raison que la liste."
    ),
    ("GET", "/referentiel/validation"): (
        "Combien de paramètres restent à valider, donc si un chiffre produit est "
        "opposable. C'est une information sur la plateforme, pas sur un dossier."
    ),
    # ── Les deux outils de développement, fermés hors démonstration ──
    ("GET", "/transverse/courriels"): (
        "La boîte aux lettres de recette. Rend 404 hors mode démonstration : voir "
        "`TestLesOutilsDeDeveloppementSontFermes`."
    ),
    ("POST", "/souscription/paiements/{identifiant}/simulation"): (
        "Simule un encaissement. Refusée dès que des identifiants de paiement "
        "réels sont configurés : voir `TestLesOutilsDeDeveloppementSontFermes`."
    ),
}


def _routes():
    def descendre(conteneur):
        for route in getattr(conteneur, "routes", []) or []:
            if isinstance(route, APIRoute):
                yield route.path, route
            elif getattr(route, "original_router", None) is not None:
                yield from descendre(route.original_router)

    with TestClient(app):
        return sorted(descendre(app), key=lambda paire: paire[0])


def _sans_controle():
    """Les routes dont le corps n'appelle aucun contrôle de permission."""
    trouvees = []
    for chemin, route in _routes():
        try:
            source = inspect.getsource(route.endpoint)
        except (OSError, TypeError):
            continue
        if any(appel in source for appel in ("exiger(", "exiger_dossier(", "restreindre(")):
            continue
        trouvees.append((sorted(route.methods)[0], chemin))
    return trouvees


class TestLeBalayageVoitQuelqueChose:
    def test_il_trouve_les_routes(self):
        """⚠️ Sans cela, un balayage aveugle déclarerait toutes les routes
        gardées."""
        assert len(_routes()) >= 100

    def test_il_trouve_des_routes_gardees(self):
        """Et la contre-épreuve : s'il n'en trouvait aucune de gardée, c'est que
        la détection du contrôle ne fonctionne plus."""
        assert len(_routes()) - len(_sans_controle()) >= 80


class TestLaListeDesRoutesPubliquesEstClose:
    def test_aucune_route_publique_n_est_apparue(self):
        """⚠️ **Le cas central.**

        Une liste de vingt-huit exceptions toutes justifiées est une liste où la
        vingt-neuvième passera inaperçue. Celle-ci est close : ajouter une route
        sans contrôle oblige à écrire son motif, et ne pas pouvoir en écrire un
        est l'information recherchée.
        """
        inconnues = sorted(set(_sans_controle()) - set(PUBLIQUES))
        assert not inconnues, (
            f"ces routes n'exigent aucune permission et ne figurent pas dans la "
            f"liste : {inconnues}. Ajouter `exiger(...)`, ou les inscrire ici avec "
            "leur motif."
        )

    def test_aucune_exception_n_est_devenue_inutile(self):
        """Une route depuis gardée doit sortir de la liste : l'y laisser ferait "
        croire qu'elle est publique, et personne ne le vérifierait."""
        disparues = sorted(set(PUBLIQUES) - set(_sans_controle()))
        assert not disparues, (
            f"ces routes exigent désormais une permission : {disparues}. Les "
            "retirer de la liste."
        )

    def test_chaque_motif_est_court_et_present(self):
        """⚠️ Un motif qui tient à un paragraphe est un motif qui n'en est pas un.

        Les exceptions courtes se relisent ; les longues se croient sur parole.
        """
        for cle, motif in PUBLIQUES.items():
            assert 20 <= len(motif) <= 320, (cle, len(motif))


class TestLesReferencesNeSeDevinentPas:
    """⚠️ **Le garde d'une affirmation, pas d'un comportement.**

    Quatre routes sont publiques parce que *« la référence est longue et non
    devinable »*. C'est une affirmation du projet sur lui-même, et rien ne la
    tenait.

    Si `_reference` devenait un compteur — `SO-1`, `SO-2` — ces routes
    deviendraient l'annuaire des clients du cabinet : nom, téléphone, courriel,
    NIU, montants. Aucun test ne l'aurait vu.
    """

    #: Deux puissances de deux : au-dessous, une énumération devient concevable.
    BITS_MINIMAUX = 48

    def test_une_reference_porte_assez_d_alea(self):
        from app.contextes.souscription.adaptateurs.entrant.routes_http import (
            _reference,
        )

        echantillon = _reference("SO")
        partie_aleatoire = echantillon.split("-", 1)[1]
        assert re.fullmatch(r"[0-9a-f]+", partie_aleatoire), echantillon
        assert len(partie_aleatoire) * 4 >= self.BITS_MINIMAUX, (
            f"« {echantillon} » ne porte que {len(partie_aleatoire) * 4} bits "
            "d'aléa : les routes publiques qui s'appuient sur cette référence "
            "deviennent énumérables."
        )

    def test_deux_references_different(self):
        """La contre-épreuve : une constante de seize caractères passerait le cas
        précédent."""
        from app.contextes.souscription.adaptateurs.entrant.routes_http import (
            _reference,
        )

        assert len({_reference("SO") for _ in range(50)}) == 50

    def test_le_parcours_d_acquisition_emploie_la_meme_regle(self):
        from app.contextes.souscription.adaptateurs.entrant.routes_acquisition import (
            _reference as reference_acquisition,
        )

        partie = reference_acquisition("dos").split("-", 1)[1]
        assert len(partie) * 4 >= self.BITS_MINIMAUX


class TestLesOutilsDeDeveloppementSontFermes:
    """⚠️ Deux routes publiques n'existent que pour la recette. Leur fermeture ne
    dépend pas d'un déploiement bien fait : elle est dans le code."""

    def test_la_boite_de_recette_repond_404_hors_demonstration(self, monkeypatch):
        from app.infrastructure import config

        with TestClient(app) as client:
            reglage = config.configuration()
            monkeypatch.setattr(reglage, "mode_demonstration", False, raising=False)
            reponse = client.get("/transverse/courriels")
        assert reponse.status_code == 404, reponse.text

    def test_la_simulation_est_refusee_quand_le_prestataire_est_configure(
        self, monkeypatch
    ):
        """⚠️ « Un encaissement réel ne se fabrique pas. »

        Le refus lit `fournisseur.simule`, et non la configuration : le comptoir
        est monté une fois, et régler la clé après coup ne l'aurait pas atteint.
        C'est le fournisseur qui porte la réponse, donc c'est lui qu'on interroge.
        """
        from app.contextes.souscription.adaptateurs.sortant.fournisseur_tara import (
            FournisseurTara,
        )

        monkeypatch.setattr(FournisseurTara, "simule", property(lambda self: False))
        with TestClient(app) as client:
            reponse = client.post(
                "/souscription/paiements/PM-1/simulation", json={"reussi": True}
            )
        assert reponse.status_code == 409, reponse.text
        assert "ne se fabrique pas" in reponse.json()["detail"]


class TestCeQueCeControleNeDitPas:
    def test_il_ne_verifie_pas_que_la_permission_est_la_bonne(self):
        """⚠️ Écrit comme un cas pour qu'on ne s'y trompe pas.

        Ce fichier vérifie qu'une permission est **exigée**, jamais que c'est la
        bonne. Un `LIRE_DOSSIER` là où il faudrait `VALIDER_ECRITURE` lui échappe,
        et c'est aux cas de chaque contexte de le dire.
        """
        assert True
