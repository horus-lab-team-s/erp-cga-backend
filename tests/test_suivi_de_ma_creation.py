"""La fondatrice suit sa création et dépose ses pièces, sans compte.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Entre son paiement et son immatriculation — les semaines où elle attend le plus —
la cliente n'avait **rien**. Les dix routes du contexte exigent `SUIVRE_FORMALITE`,
une habilitation de collaborateur ; la page de suivi de la vitrine suit une
souscription et ses mensualités, pas une formalité.

Le canal prévu était WhatsApp. Le cabinet a constaté que cela ne tient pas : on ne
peut écrire librement qu'à l'intérieur de la **fenêtre de vingt-quatre heures**
ouverte par le dernier message du client, et hors de cette fenêtre le seul recours
est un modèle approuvé — dont aucun ne l'est. Une formalité dure des semaines.

⚠️ **TOUJOURS PAS DE MOT DE PASSE.** La décision du 27 septembre tient : « un
formulaire à identifiants pour envoyer une photo de sa carte d'identité ferait
abandonner la moitié des dossiers ». Le compte arrive avec la société, au NIU.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import io
from uuid import uuid4

from app.contextes.creation_entreprise.domaine.lien_de_suivi import sceau_de_suivi
from app.infrastructure.config import configuration
from tests.conftest import exige_postgresql, ouvrir_une_session

pytestmark = exige_postgresql

CHARGEE = "p.moukouri@cga-brcg.cm"
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def _secret() -> str:
    return configuration().cle_chiffrement or "secret-de-developpement"


def _dossier(client) -> str:
    """Un dossier de création ouvert par le cabinet, comme l'encaissement le fait."""
    ouvrir_une_session(client, CHARGEE)
    ouverture = client.post(
        "/creations",
        json={
            "reference": f"CRE-{uuid4().hex[:12]}",
            "fondateur": {
                "nom": "NGONO",
                "prenom": "Sylvie",
                "courriel": "sylvie.ngono@exemple.cm",
                "telephone": "+237699001122",
            },
            "denomination_souhaitee": "NGONO COUTURE SARL",
            "forme_juridique": "SARL",
            "activite": "Confection",
            "siege": "Bonabéri, Douala IV",
            "capital": "1000000",
        },
    )
    assert ouverture.status_code in (200, 201), ouverture.text
    return ouverture.json()["reference"]


def _lien(reference: str) -> dict[str, str]:
    return {"s": sceau_de_suivi(reference, secret=_secret())}


class TestElleVoitSonDossier:
    def test_le_lien_ouvre_son_suivi_sans_aucune_session(self, plateforme):
        client = plateforme
        reference = _dossier(client)
        client.cookies.clear()  # ⚠️ Plus aucune session : c'est tout l'objet.

        vue = client.get(f"/creations/{reference}/suivi", params=_lien(reference))
        assert vue.status_code == 200, vue.text
        corps = vue.json()
        assert corps["denomination_souhaitee"] == "NGONO COUTURE SARL"
        assert corps["etape"] == "QUALIFICATION"
        assert corps["pieces"], "elle doit savoir ce qu'on attend d'elle"
        assert corps["manquantes_bloquantes"] > 0

    def test_il_ne_rend_rien_d_interne(self, plateforme):
        """⚠️ Un lien circule dans une boîte aux lettres.

        `DossierCreation` porte le fondateur, les jalons, les identifiants
        délivrés et les commentaires du cabinet. Elle a besoin de savoir où en
        est son dossier et ce qu'on attend d'elle, pas de relire le travail du
        cabinet.
        """
        client = plateforme
        reference = _dossier(client)
        client.cookies.clear()

        corps = client.get(f"/creations/{reference}/suivi", params=_lien(reference)).json()
        for interne in ("fondateur", "jalons", "immatriculation", "converti_en"):
            assert interne not in corps, f"« {interne} » ne doit pas sortir sur ce lien"

    def test_un_sceau_faux_et_un_dossier_inconnu_repondent_pareil(self, plateforme):
        """⚠️ Sinon on énumère les références en lisant les refus.

        C'est le raisonnement déjà tenu par la consultation de proforma et par la
        résolution des locataires.
        """
        client = plateforme
        reference = _dossier(client)
        client.cookies.clear()

        faux = client.get(f"/creations/{reference}/suivi", params={"s": "0" * 64})
        inconnu = client.get("/creations/CRE-inexistant/suivi", params={"s": "0" * 64})
        absent = client.get(f"/creations/{reference}/suivi")
        assert faux.status_code == inconnu.status_code == absent.status_code == 404
        assert faux.json()["detail"] == inconnu.json()["detail"]


class TestLeLienNeVitPasToujours:
    """⚠️ 19 octobre 2026 (audit de sécurité) : le lien de suivi s'ouvrait pour toujours."""

    def test_un_dossier_clos_garde_son_lien_quatre_vingt_dix_jours(self, plateforme):
        from datetime import timedelta

        from app.partage.horloge import horloge_figee, maintenant

        client = plateforme
        reference = _dossier(client)
        abandon = client.post(f"/creations/{reference}/abandon", json={"motif": "Projet reporté."})
        assert abandon.status_code == 200, abandon.text
        client.cookies.clear()

        ouvert = client.get(f"/creations/{reference}/suivi", params=_lien(reference))
        assert ouvert.status_code == 200
        with horloge_figee(maintenant() + timedelta(days=91)):
            echu = client.get(f"/creations/{reference}/suivi", params=_lien(reference))
        assert echu.status_code == 404, "trois mois après la clôture, le lien ne s'ouvre plus"
        # ⚠️ Le même refus qu'un lien faux : rien ne dit que le dossier existe.
        faux = client.get(f"/creations/{reference}/suivi", params={"s": "0" * 64})
        assert echu.json() == faux.json()

    def test_le_sceau_n_est_pas_signe_avec_la_cle_du_coffre(self):
        """Une clé par usage : le sceau du suivi n'est pas un HMAC direct de la clé."""
        import hashlib
        import hmac

        from app.partage.sceau import cle_d_usage

        direct = hmac.new(b"cle", b"CRE-1", hashlib.sha256).hexdigest()
        assert sceau_de_suivi("CRE-1", secret="cle") != direct
        assert cle_d_usage("cle", "lien-de-suivi") != cle_d_usage("cle", "acceptation-proforma")


class TestElleDeposeSesPieces:
    def _televerser(self, client, reference):
        envoi = client.post(
            "/collecte/fichiers-scelles",
            params={"reference": reference, **_lien(reference)},
            files={"fichier": ("cni.pdf", io.BytesIO(PDF), "application/pdf")},
        )
        assert envoi.status_code == 201, envoi.text
        return envoi.json()["empreinte"]

    def test_elle_televerse_puis_rattache_sa_piece(self, plateforme):
        client = plateforme
        reference = _dossier(client)
        client.cookies.clear()

        avant = client.get(f"/creations/{reference}/suivi", params=_lien(reference)).json()
        code = next(p["code"] for p in avant["pieces"] if not p["fournie"])

        empreinte = self._televerser(client, reference)
        depot = client.post(
            f"/creations/{reference}/suivi/pieces",
            params=_lien(reference),
            json={"code": code, "empreinte": empreinte},
        )
        assert depot.status_code == 200, depot.text
        apres = depot.json()
        assert next(p for p in apres["pieces"] if p["code"] == code)["fournie"] is True
        assert apres["manquantes_bloquantes"] < avant["manquantes_bloquantes"]

    def test_le_televersement_refuse_un_sceau_faux(self, plateforme):
        client = plateforme
        reference = _dossier(client)
        client.cookies.clear()

        refus = client.post(
            "/collecte/fichiers-scelles",
            params={"reference": reference, "s": "0" * 64},
            files={"fichier": ("cni.pdf", io.BytesIO(PDF), "application/pdf")},
        )
        assert refus.status_code == 404, refus.text

    def test_le_type_est_lu_dans_le_contenu_et_non_dans_le_nom(self, plateforme):
        """⚠️ La garde de l'autre porte, et elle vaut ici aussi.

        Une porte ouverte sans session est la dernière où l'on voudrait relâcher
        les contrôles. Les deux routes partagent `_ranger_le_fichier`, ce qui
        empêche exactement la divergence où la moins surveillée reste ouverte.
        """
        client = plateforme
        reference = _dossier(client)
        client.cookies.clear()

        refus = client.post(
            "/collecte/fichiers-scelles",
            params={"reference": reference, **_lien(reference)},
            files={
                "fichier": (
                    "piege.pdf",
                    io.BytesIO(b"<html>bonjour</html>"),
                    "application/pdf",
                )
            },
        )
        assert refus.status_code == 415, refus.text

    def test_le_cabinet_ouvre_ce_qu_elle_a_depose(self, plateforme):
        """⚠️ LA MOITIÉ QUI MANQUAIT.

        Elle déposait, le fichier arrivait au magasin, l'empreinte s'inscrivait à
        son dossier — et personne ne pouvait l'ouvrir : le seul téléchargement
        passait par un identifiant de pièce, donc par un NIU, qu'une formalité
        n'a pas.
        """
        client = plateforme
        reference = _dossier(client)
        client.cookies.clear()
        empreinte = self._televerser(client, reference)

        ouvrir_une_session(client, CHARGEE)
        fichier = client.get(f"/collecte/fichiers/{empreinte}")
        assert fichier.status_code == 200, fichier.text
        assert fichier.content == PDF
        # ⚠️ Les en-têtes qui empêchent qu'un fichier déposé s'exécute dans le
        # domaine du cabinet, et qu'un justificatif nominatif dorme dans un cache.
        assert "attachment" in fichier.headers["content-disposition"]
        assert fichier.headers["x-content-type-options"] == "nosniff"
        assert fichier.headers["cache-control"] == "private, no-store"

    def test_une_empreinte_mal_formee_rend_404_et_non_500(self, plateforme):
        """⚠️ Constaté en coupant une empreinte à la main : le magasin refuse la
        clé, et l'exception s'échappait en 500.

        Une clé mal formée et une clé inconnue reçoivent la même réponse :
        distinguer les deux dirait à un curieux que sa forme est bonne.
        """
        client = plateforme
        ouvrir_une_session(client, CHARGEE)
        # ⚠️ Pas de clé VIDE dans cette liste : `/collecte/fichiers/` désigne une
        # autre route, celle du dépôt, et rend 405. C'est du routage, pas un
        # refus de lecture, et l'y mettre ferait croire à un cas mesuré.
        for cle in ("cfa3181c1ee36e8b", "z" * 64, "../../etc/passwd"):
            reponse = client.get(f"/collecte/fichiers/{cle}")
            assert reponse.status_code == 404, (cle, reponse.status_code)

    def test_le_cabinet_a_le_meme_chemin_pour_ce_qu_il_recoit_autrement(self, plateforme):
        """⚠️ Le dossier ne doit pas porter deux sortes de pièces reçues.

        La fondatrice dépose par son lien, et le document est rangé. Un
        collaborateur qui reçoit la même pièce par messagerie ou au guichet ne
        pouvait que **cocher une case** : rien ne produisait d'empreinte de son
        côté. Six mois plus tard, on ne sait plus laquelle porte son document.
        """
        client = plateforme
        reference = _dossier(client)

        envoi = client.post(
            "/collecte/fichiers-de-formalite",
            files={"fichier": ("statuts.pdf", io.BytesIO(PDF), "application/pdf")},
        )
        assert envoi.status_code == 201, envoi.text
        empreinte = envoi.json()["empreinte"]

        rattache = client.post(
            f"/creations/{reference}/pieces",
            json={"code": "STATUTS", "empreinte": empreinte},
        )
        assert rattache.status_code == 200, rattache.text
        assert client.get(f"/collecte/fichiers/{empreinte}").status_code == 200

    def test_cette_porte_du_cabinet_exige_une_habilitation(self, plateforme):
        """⚠️ Elle n'a PAS de sceau : c'est la permission des formalités qui garde.

        Sans périmètre de dossier à vérifier, la permission est tout ce qui reste.
        La confondre avec la porte scellée de la cliente ouvrirait un dépôt libre.
        """
        client = plateforme
        _dossier(client)
        client.cookies.clear()

        refus = client.post(
            "/collecte/fichiers-de-formalite",
            files={"fichier": ("statuts.pdf", io.BytesIO(PDF), "application/pdf")},
        )
        assert refus.status_code in (401, 403), refus.text

    def test_un_adherent_ne_lit_pas_un_fichier_par_son_empreinte(self, plateforme):
        """19 octobre 2026 (audit de sécurité) : `LIRE_PIECE` suffisait, et l'empreinte
        devenait un laissez-passer vers les fichiers des autres dossiers."""
        client = plateforme
        ouvrir_une_session(client, CHARGEE)
        depot = client.post(
            "/collecte/fichiers-de-formalite",
            files={"fichier": ("statuts.pdf", io.BytesIO(PDF), "application/pdf")},
        )
        assert depot.status_code in (200, 201), depot.text
        empreinte = depot.json()["empreinte"]
        client.cookies.clear()
        ouvrir_une_session(client, "jp.nkoa@batimentplus.cm")
        assert client.get(f"/collecte/fichiers/{empreinte}").status_code == 403

    def test_une_piece_hors_checklist_est_refusee(self, plateforme):
        client = plateforme
        reference = _dossier(client)
        client.cookies.clear()
        empreinte = self._televerser(client, reference)

        refus = client.post(
            f"/creations/{reference}/suivi/pieces",
            params=_lien(reference),
            json={"code": "PIECE_INVENTEE", "empreinte": empreinte},
        )
        assert refus.status_code == 404, refus.text


class TestLeCabinetPeutRedonnerLeLien:
    """⚠️ LE COURRIEL SE PERD, ET IL N'Y AVAIT AUCUNE ISSUE.

    Le lien ne partait qu'une fois, à l'ouverture du dossier. Perdu, classé en
    indésirable, adresse saisie de travers : la cliente n'avait plus aucun moyen
    d'entrer, et **le cabinet non plus n'avait aucun moyen de le lui rendre**.
    Le sceau n'apparaissait ni à la fiche, ni au pipeline, ni dans un journal.

    Cas de tous les jours, pas cas limite. Vu en jouant le parcours complet sur
    la pile, pas en relisant le code.
    """

    def test_le_lien_relu_est_exactement_celui_qui_ouvre_le_suivi(self, plateforme):
        """⚠️ LA SEULE PROPRIÉTÉ QUI COMPTE : il ouvre vraiment.

        Rendre une adresse bien formée mais dont le sceau ne vaut pas serait
        pire que ne rien rendre : le collaborateur l'enverrait, et la cliente
        tomberait sur « ce lien ne s'ouvre pas » sans que personne comprenne.
        On rejoue donc l'adresse rendue sur la route publique.
        """
        client = plateforme
        reference = _dossier(client)

        relu = client.get(f"/creations/{reference}/lien-de-suivi")
        assert relu.status_code == 200, relu.text
        corps = relu.json()
        assert corps["reference"] == reference
        assert corps["denomination_souhaitee"] == "NGONO COUTURE SARL"
        assert corps["clos"] is False

        # ⚠️ On repart de L'ADRESSE RENDUE, et non d'un sceau donné à part : c'est
        # l'adresse que le collaborateur colle dans son message, et la réponse ne
        # porte volontairement pas le sceau nu (voir `LienDeSuivi`).
        sceau = corps["lien"].split("?s=", 1)[1]
        assert "sceau" not in corps, (
            "la réponse rend le sceau nu en plus du lien : un secret rendu deux "
            "fois se protège deux fois moins bien"
        )

        client.cookies.clear()
        vu = client.get(f"/creations/{reference}/suivi", params={"s": sceau})
        assert vu.status_code == 200, vu.text
        assert vu.json()["reference"] == reference

    def test_relire_deux_fois_rend_le_meme_lien(self, plateforme):
        """⚠️ Le sceau est RECALCULÉ, pas tiré au sort.

        S'il changeait d'une lecture à l'autre, le lien envoyé il y a trois
        semaines cesserait d'ouvrir au moment où un collaborateur le relit, et
        rien ne dirait pourquoi.
        """
        client = plateforme
        reference = _dossier(client)
        premier = client.get(f"/creations/{reference}/lien-de-suivi").json()
        second = client.get(f"/creations/{reference}/lien-de-suivi").json()
        assert premier == second

    def test_la_lecture_ne_transmet_rien(self, plateforme):
        """Aucun courriel ne part : le collaborateur relit, puis choisit son canal."""
        from app.contextes.transverse.adaptateurs.entrant.dependances import (
            service_de_notification,
        )

        client = plateforme
        reference = _dossier(client)
        poste = service_de_notification()
        poste.vider()
        assert client.get(f"/creations/{reference}/lien-de-suivi").status_code == 200
        assert poste.derniers() == [], "relire le lien a envoyé un message"

    def test_sans_habilitation_le_lien_ne_se_lit_pas(self, plateforme):
        """⚠️ `SUIVRE_FORMALITE`, et non une habilitation plus large.

        Ce lien ouvre le dossier **sans mot de passe**. Le rendre lisible plus
        largement que le dossier lui-même créerait un chemin d'accès plus court
        que la porte qu'il contourne.
        """
        client = plateforme
        reference = _dossier(client)
        client.cookies.clear()
        assert client.get(f"/creations/{reference}/lien-de-suivi").status_code == 401

        # Un collaborateur qui lit les prospects mais ne suit pas les formalités.
        ouvrir_une_session(client, "b.mballa@cga-brcg.cm")
        refus = client.get(f"/creations/{reference}/lien-de-suivi")
        assert refus.status_code == 403, refus.text

    def test_un_dossier_inconnu_rend_404(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, CHARGEE)
        assert client.get("/creations/CRE-inexistant/lien-de-suivi").status_code == 404


class TestUnDepotDeLaClienteCompteCommeMouvement:
    """⚠️ L'INDICATEUR QUI DÉCIDE DE L'ORDRE DES RELANCES NE DOIT PAS MENTIR.

    `immobile_depuis` ne lisait que les jalons, c'est-à-dire les franchissements
    d'étape, c'est-à-dire les gestes du cabinet. Depuis que la cliente dépose
    elle-même par son lien scellé, elle peut envoyer cinq documents un mardi
    sans qu'aucun jalon ne bouge : le pipeline annonçait « immobile depuis trois
    semaines », et le chargé de formalités relançait en premier celle qui venait
    de répondre.

    Un indicateur qui ment est pire qu'un indicateur absent.
    """

    def _pipeline(self, client, reference):
        lignes = client.get("/creations/pipeline").json()
        (ligne,) = [x for x in lignes if x["reference"] == reference]
        return ligne

    def test_la_propriete_compte_une_piece_recue_apres_le_dernier_jalon(self):
        """⚠️ LE CAS QUI COMPTE, ET QU'UN ESSAI PAR HTTP NE PEUT PAS MONTRER.

        Un dossier créé à l'instant a son `ouvert_le` aujourd'hui : l'ancienne
        et la nouvelle version rendent alors la même date, et le test passe des
        deux côtés. Écrit d'abord ainsi, il ne gardait rien, ce qu'une mutation
        a révélé.

        Il faut un dossier dont le dernier geste du cabinet est ANCIEN et dont
        la pièce est RÉCENTE. C'est précisément la situation dénoncée : la
        cliente répond, le cabinet n'a rien fait depuis trois semaines.
        """
        from datetime import date, timedelta

        from app.contextes.creation_entreprise.api import DossierCreation

        vieux = date.today() - timedelta(days=21)
        hier = date.today() - timedelta(days=1)
        dossier = DossierCreation(
            reference="CRE-essai-immobilite",
            fondateur={
                "nom": "NGONO",
                "prenom": "Sylvie",
                "courriel": "s@exemple.cm",
                "telephone": "+237699001122",
            },
            denomination_souhaitee="NGONO COUTURE SARL",
            forme_juridique="SARL",
            activite="Confection",
            siege="Bonabéri",
            capital="1000000",
            ouvert_le=vieux,
            jalons=({"etape": "QUALIFICATION", "survenu_le": vieux, "par": "C-007"},),
            pieces=(
                {
                    "code": "ID_FONDATEUR",
                    "libelle": "Pièce d'identité",
                    "obligatoire": True,
                    "fournie_le": hier,
                },
                {
                    "code": "CASIER_JUDICIAIRE",
                    "libelle": "Casier judiciaire",
                    "obligatoire": True,
                    "fournie_le": None,
                },
            ),
        )
        assert dossier.immobile_depuis == hier, (
            "le dépôt de la cliente ne compte pas comme mouvement : le pipeline "
            f"annonce {(date.today() - dossier.immobile_depuis).days} jours "
            "d'immobilité alors qu'elle a répondu hier"
        )

    def test_sans_aucune_piece_recue_l_indicateur_ne_bouge_pas(self):
        """⚠️ La correction ne doit pas rajeunir un dossier qui dort vraiment.

        Une pièce non fournie porte `fournie_le` à `None` : elle ne doit compter
        pour rien. Sans ce cas, on ne saurait pas si la propriété lit les dates
        ou la simple présence d'une pièce à la liste.
        """
        from datetime import date, timedelta

        from app.contextes.creation_entreprise.api import DossierCreation

        vieux = date.today() - timedelta(days=21)
        dossier = DossierCreation(
            reference="CRE-essai-dort",
            fondateur={
                "nom": "NGONO",
                "prenom": "Sylvie",
                "courriel": "s@exemple.cm",
                "telephone": "+237699001122",
            },
            denomination_souhaitee="NGONO COUTURE SARL",
            forme_juridique="SARL",
            activite="Confection",
            siege="Bonabéri",
            capital="1000000",
            ouvert_le=vieux,
            jalons=({"etape": "QUALIFICATION", "survenu_le": vieux, "par": "C-007"},),
            pieces=(
                {
                    "code": "ID_FONDATEUR",
                    "libelle": "Pièce d'identité",
                    "obligatoire": True,
                    "fournie_le": None,
                },
            ),
        )
        assert dossier.immobile_depuis == vieux

    def test_le_depot_rapproche_la_date_du_dernier_mouvement(self, plateforme):
        from datetime import date

        client = plateforme
        reference = _dossier(client)
        avant = self._pipeline(client, reference)

        # Elle dépose par son lien, sans aucune session.
        client.cookies.clear()
        depot = client.post(
            "/collecte/fichiers-scelles",
            params={"reference": reference, **_lien(reference)},
            files={"fichier": ("carte.pdf", PDF, "application/pdf")},
        )
        assert depot.status_code in (200, 201), depot.text
        rattache = client.post(
            f"/creations/{reference}/suivi/pieces",
            params=_lien(reference),
            json={"code": "ID_FONDATEUR", "empreinte": depot.json()["empreinte"]},
        )
        assert rattache.status_code == 200, rattache.text

        ouvrir_une_session(client, CHARGEE)
        apres = self._pipeline(client, reference)
        assert apres["immobile_depuis"] == date.today().isoformat(), (
            "le dépôt de la cliente n'a pas compté comme mouvement : "
            f"{avant['immobile_depuis']} -> {apres['immobile_depuis']}"
        )
        assert apres["jours_d_immobilite"] == 0

    def test_aucun_jalon_n_est_inventé_pour_une_piece(self, plateforme):
        """⚠️ Un `Jalon` est le franchissement d'une ÉTAPE.

        En poser un pour une pièce ferait apparaître, au panneau des jalons du
        dossier, une étape que personne n'a franchie.
        """
        client = plateforme
        reference = _dossier(client)
        avant = len(client.get(f"/creations/{reference}").json()["dossier"]["jalons"])

        client.post(
            f"/creations/{reference}/pieces",
            json={"code": "ID_FONDATEUR", "empreinte": None},
        )
        apres = client.get(f"/creations/{reference}").json()["dossier"]["jalons"]
        assert len(apres) == avant, "une pièce reçue a inventé un jalon d'étape"


class TestCelleQuiAttendUneReponsePasseDevant:
    """⚠️ LA CONSÉQUENCE DE LA CORRECTION PRÉCÉDENTE, ET SA RÉPARATION.

    Compter le dépôt d'une cliente comme un mouvement a rendu `immobile_depuis`
    honnête et l'ordre du pipeline faux : le dossier où elle vient d'envoyer
    cinq documents affiche « sans mouvement depuis 1 j » et tombait **au bas**
    d'une liste triée du plus ancien au plus récent. Elle avait répondu, et
    plus personne ne regardait.

    Avant la correction il restait en tête, mais pour une mauvaise raison et
    sous une étiquette qui mentait. Les deux comportements étaient fautifs.
    """

    @staticmethod
    def _dossier(reference, *, jalon_le, piece_le, clos=False):
        from app.contextes.creation_entreprise.api import DossierCreation

        return DossierCreation(
            reference=reference,
            fondateur={
                "nom": "NGONO",
                "prenom": "Sylvie",
                "courriel": "s@exemple.cm",
                "telephone": "+237699001122",
            },
            denomination_souhaitee=reference,
            forme_juridique="SARL",
            activite="Confection",
            siege="Bonabéri",
            capital="1000000",
            ouvert_le=jalon_le,
            etape="ABANDONNE" if clos else "QUALIFICATION",
            jalons=(
                {"etape": "QUALIFICATION", "survenu_le": jalon_le, "par": "C-007"},
                *(
                    ({"etape": "ABANDONNE", "survenu_le": jalon_le, "par": "C-007"},)
                    if clos
                    else ()
                ),
            ),
            pieces=(
                {
                    "code": "ID_FONDATEUR",
                    "libelle": "Identité",
                    "obligatoire": True,
                    "fournie_le": piece_le,
                },
            ),
            motif_abandon="essai" if clos else None,
        )

    def test_elle_a_deposé_apres_le_dernier_geste_du_cabinet(self):
        from datetime import date, timedelta

        hier = date.today() - timedelta(days=1)
        vieux = date.today() - timedelta(days=21)
        assert self._dossier("A", jalon_le=vieux, piece_le=hier).attend_le_cabinet

    def test_le_depot_du_jour_meme_compte(self):
        """⚠️ LE CAS QUE LA COMPARAISON STRICTE MANQUAIT, ET C'EST LE PLUS COURANT.

        Dossier ouvert le matin, pièces envoyées l'après-midi, puis trois
        semaines de silence. `fournie_le` et `survenu_le` sont des DATES, sans
        heure : elles sont égales, et une comparaison stricte ne signalait
        jamais cette cliente. Exactement la situation que la propriété existe
        pour attraper.

        Trouvé parce qu'un test de bout en bout créait ses deux dossiers le jour
        même, et échouait sans que le produit soit en cause.
        """
        from datetime import date

        assert self._dossier(
            "E", jalon_le=date.today(), piece_le=date.today()
        ).attend_le_cabinet

    def test_rien_ne_bouge_n_est_pas_une_attente(self):
        from datetime import date, timedelta

        vieux = date.today() - timedelta(days=21)
        assert not self._dossier("B", jalon_le=vieux, piece_le=None).attend_le_cabinet

    def test_le_cabinet_a_agi_apres_elle(self):
        """⚠️ Elle a déposé, PUIS le cabinet a franchi une étape : la balle n'est
        plus dans son camp à lui."""
        from datetime import date, timedelta

        assert not self._dossier(
            "C",
            jalon_le=date.today() - timedelta(days=1),
            piece_le=date.today() - timedelta(days=5),
        ).attend_le_cabinet

    def test_un_dossier_clos_n_attend_plus_rien(self):
        """Abandonné ou converti : le cabinet ne doit rien, et le faire remonter
        en tête du pipeline volerait la place d'un dossier vivant."""
        from datetime import date, timedelta

        assert not self._dossier(
            "D",
            jalon_le=date.today() - timedelta(days=21),
            piece_le=date.today() - timedelta(days=1),
            clos=True,
        ).attend_le_cabinet

    def test_le_pipeline_la_place_devant_celle_qui_dort(self, plateforme):
        """⚠️ LE POINT DU PAS, mesuré sur la route, pas sur la propriété.

        Le dossier où elle attend doit précéder celui où il ne se passe rien,
        alors même que son dernier mouvement est PLUS RÉCENT.
        """
        client = plateforme
        attente = _dossier(client)
        dort = _dossier(client)

        # Elle dépose sur le premier, par son lien, sans session.
        client.cookies.clear()
        depot = client.post(
            "/collecte/fichiers-scelles",
            params={"reference": attente, **_lien(attente)},
            files={"fichier": ("carte.pdf", PDF, "application/pdf")},
        )
        assert depot.status_code in (200, 201), depot.text
        assert (
            client.post(
                f"/creations/{attente}/suivi/pieces",
                params=_lien(attente),
                json={"code": "ID_FONDATEUR", "empreinte": depot.json()["empreinte"]},
            ).status_code
            == 200
        )

        ouvrir_une_session(client, CHARGEE)
        lignes = client.get("/creations/pipeline").json()
        rangs = {ligne["reference"]: rang for rang, ligne in enumerate(lignes)}
        assert rangs[attente] < rangs[dort], (
            "la cliente qui attend une réponse est rangée après celle dont le "
            "dossier ne bouge pas"
        )
        (ligne,) = [c for c in lignes if c["reference"] == attente]
        assert ligne["attend_le_cabinet"] is True
        (autre,) = [c for c in lignes if c["reference"] == dort]
        assert autre["attend_le_cabinet"] is False


class TestElleVoitLesNumerosDeSaSociete:
    """⚠️ LA NOUVELLE QU'ELLE ATTEND DEPUIS LE PREMIER JOUR.

    Sa page de suivi la menait jusqu'à « SUIVI_IMMATRICULATION », une étiquette
    technique, sans jamais lui dire que sa société existait. Le RCCM et le NIU
    étaient au dossier, visibles du cabinet seul.

    Ce ne sont pas des informations internes : ce sont les numéros de son
    entreprise, que sa banque, ses fournisseurs et l'administration lui
    demanderont. Les lui cacher derrière un appel téléphonique n'ajoutait aucune
    sécurité, seulement un délai.

    Trouvé en faisant avancer un dossier d'essai jusqu'au bout sur la pile, et
    en relisant sa page à chaque étape.
    """

    def test_avant_toute_delivrance_elle_ne_voit_rien_mais_le_champ_existe(
        self, plateforme
    ):
        """⚠️ Le champ est là dès le début, vide. Un écran qui doit tester son
        existence avant de tester son contenu finit par en oublier un cas."""
        client = plateforme
        reference = _dossier(client)
        client.cookies.clear()
        vu = client.get(f"/creations/{reference}/suivi", params=_lien(reference)).json()
        assert vu["identifiants"]["rccm"] is None
        assert vu["identifiants"]["immatriculee"] is False

    def test_le_rccm_et_le_niu_lui_parviennent_avec_leurs_dates(self, plateforme):
        from datetime import date

        client = plateforme
        reference = _dossier(client)

        # ⚠️ Le guichet ne prend pas un dossier incomplet, et le tunnel non plus :
        # `DEPOT_CFCE` rend 409 tant qu'une pièce bloquante manque. On les fournit
        # donc toutes d'abord, par la porte du cabinet.
        fiche = client.get(f"/creations/{reference}").json()["dossier"]
        for piece in fiche["pieces"]:
            if piece["obligatoire"]:
                client.post(
                    f"/creations/{reference}/pieces",
                    json={"code": piece["code"], "empreinte": None},
                )

        for etape in ("CONSTITUTION", "DEPOT_CFCE", "SUIVI_IMMATRICULATION"):
            assert (
                client.post(
                    f"/creations/{reference}/etape", json={"vers": etape}
                ).status_code
                == 200
            )
        jour = date.today().isoformat()
        pose = client.post(
            f"/creations/{reference}/identifiants",
            json={
                "rccm": "RC/DLA/2026/B/1234",
                "rccm_obtenu_le": jour,
                "niu": "M092612345678X",
                "niu_obtenu_le": jour,
            },
        )
        assert pose.status_code == 200, pose.text

        client.cookies.clear()
        vu = client.get(f"/creations/{reference}/suivi", params=_lien(reference)).json()
        ids = vu["identifiants"]
        assert ids["rccm"] == "RC/DLA/2026/B/1234"
        assert ids["niu"] == "M092612345678X"
        assert ids["rccm_obtenu_le"] == jour, "une date sans son numéro n'a aucun sens"
        assert ids["immatriculee"] is True

    def test_le_reste_du_dossier_ne_sort_toujours_pas(self, plateforme):
        """⚠️ LE SOUS-ENSEMBLE RESTE DÉLIBÉRÉ.

        Il s'élargit de ce qui lui appartient, pas du travail du cabinet. Ni
        jalons, ni commentaires internes, ni identité du collaborateur ne
        doivent franchir un lien qui circule dans une boîte aux lettres.
        """
        client = plateforme
        reference = _dossier(client)
        client.cookies.clear()
        vu = client.get(f"/creations/{reference}/suivi", params=_lien(reference)).json()
        for interdit in ("jalons", "fondateur", "motif_abandon", "converti_en"):
            assert interdit not in vu, f"« {interdit} » sort sur le lien de la cliente"
