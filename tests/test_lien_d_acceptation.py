"""Le lien signé protège le numéro, et le client voit ce qu'il accepte.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE (pas 67)

L'acceptation d'une proforma est publique : le lien signé est l'authentification. Son
docstring affirmait que le sceau est vérifié avant la proforma, pour qu'on ne puisse pas
sonder l'existence d'un numéro. Le code lisait la proforma d'abord : un numéro inventé
répondait 404, un numéro existant au sceau faux 422. Les numéros sont séquentiels, et
n'importe qui énumérait les proformas du cabinet.

Et aucune route ne montrait au client ce qu'il acceptait.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from tests.conftest import exige_postgresql, ouvrir_une_session
from tests.test_prix_du_dossier import (
    REPONSES,
    RESPONSABLE,
    _chiffrer,
    _dossier_affecte,
    _qualifier,
)

pytestmark = exige_postgresql

FAUX_SCEAU = "0" * 64


def _proforma_emise(client, telephone):
    ouvrir_une_session(client, RESPONSABLE)
    reference = _dossier_affecte(client, telephone)
    _qualifier(client, reference, REPONSES)
    montant = _chiffrer(client, reference).json()["reference"]
    emise = client.post(f"/acquisition/dossiers/{reference}/proforma", json={"montant": montant})
    assert emise.status_code == 201, emise.text
    return emise.json()


def _lien(proforma):
    return {
        "version": proforma["version"],
        "expire_le": proforma["expire_le"],
        "sceau": proforma["lien_acceptation"],
    }


class TestOnNEnumereRien:
    def test_sans_lien_un_numero_existant_et_un_numero_invente_repondent_pareil(self, plateforme):
        client = plateforme
        proforma = _proforma_emise(client, "699113311")
        visiteur = {"version": 1, "expire_le": proforma["expire_le"], "sceau": FAUX_SCEAU}
        existant = client.post(
            f"/acquisition/proformas/{proforma['numero']}/acceptation",
            json={**visiteur, "identite_declaree": "Quelqu'un"},
        )
        invente = client.post(
            "/acquisition/proformas/PRO-2026-9999/acceptation",
            json={**visiteur, "identite_declaree": "Quelqu'un"},
        )
        assert existant.status_code == invente.status_code == 422, (existant.text, invente.text)
        assert "inconnue" not in invente.text

        lecture_existante = client.get(
            f"/acquisition/proformas/{proforma['numero']}/consultation", params=visiteur
        )
        lecture_inventee = client.get(
            "/acquisition/proformas/PRO-2026-9999/consultation", params=visiteur
        )
        assert lecture_existante.status_code == lecture_inventee.status_code == 422


class TestLeClientVoitCeQuIlAccepte:
    def test_la_consultation_montre_le_prix_et_rien_d_interne(self, plateforme):
        client = plateforme
        proforma = _proforma_emise(client, "699113322")
        lecture = client.get(
            f"/acquisition/proformas/{proforma['numero']}/consultation", params=_lien(proforma)
        )
        assert lecture.status_code == 200, lecture.text
        corps = lecture.json()
        assert corps["montant"] == proforma["montant"]
        assert corps["acceptee"] is False
        for interne in ("plancher", "plafond", "chiffre_par", "valide_par", "faits"):
            assert interne not in corps, f"« {interne} » est interne au cabinet"

    def test_lire_ne_consomme_rien_et_l_acceptation_suit(self, plateforme):
        client = plateforme
        proforma = _proforma_emise(client, "699113333")
        chemin = f"/acquisition/proformas/{proforma['numero']}"
        for _ in range(2):
            assert client.get(f"{chemin}/consultation", params=_lien(proforma)).status_code == 200
        acceptation = client.post(
            f"{chemin}/acceptation", json={**_lien(proforma), "identite_declaree": "Le gérant"}
        )
        assert acceptation.status_code == 200, acceptation.text
        apres = client.get(f"{chemin}/consultation", params=_lien(proforma))
        assert apres.json()["acceptee"] is True

    def test_un_lien_d_une_autre_version_ne_montre_rien(self, plateforme):
        """Le sceau d'une autre version est faux : il est refusé avant toute lecture."""
        client = plateforme
        proforma = _proforma_emise(client, "699113344")
        autre = {**_lien(proforma), "version": proforma["version"] + 1}
        lecture = client.get(
            f"/acquisition/proformas/{proforma['numero']}/consultation", params=autre
        )
        assert lecture.status_code == 422, lecture.text


def test_la_fiche_du_dossier_liste_ses_proformas_sans_leur_lien(plateforme):
    """Pas 68 : la fiche d'un dossier accepté doit savoir quelle proforma faire régler."""
    client = plateforme
    proforma = _proforma_emise(client, "699113355")
    (dossier,) = [
        d for d in client.get("/acquisition/dossiers").json() if d["etat"] == "PROFORMA_EMISE"
    ]
    fiche = client.get(f"/acquisition/dossiers/{dossier['reference']}").json()
    assert [p["numero"] for p in fiche["proformas"]] == [proforma["numero"]]
    assert proforma["lien_acceptation"] not in str(fiche), "le lien ne se réaffiche jamais"


class TestOnPeutRenvoyerLeLien:
    """Le lien se retrouve, il ne se perd pas avec le courriel du client.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ LE DÉFAUT QUE CES CAS FERMENT, RELEVÉ LE 27 SEPTEMBRE.

    Le lien n'était rendu qu'à **l'émission**. La transmission — la route qu'un
    collaborateur emploie quand il envoie le document — ne le rendait pas, et
    rien ne permettait de le retrouver ensuite.

    Un client qui perd son courriel n'avait donc plus de chemin vers sa
    proforma. Le seul recours du cabinet était d'émettre une **v2** d'un
    document que personne n'avait contesté : nouveau numéro, ancien lien
    invalidé, suivi commercial brouillé — pour un courriel égaré.

    Le lien ne contient rien que la proforma ne porte déjà ; il est donc
    recalculé, et les deux routes s'en servent.
    ─────────────────────────────────────────────────────────────────────────────
    """

    def test_la_transmission_rend_le_lien(self, plateforme):
        client = plateforme
        proforma = _proforma_emise(client, "699114455")

        transmise = client.post(
            f"/acquisition/proformas/{proforma['numero']}/transmission", json={}
        )
        assert transmise.status_code == 200, transmise.text
        renvoye = transmise.json()
        assert renvoye["lien_acceptation"], "le lien doit pouvoir être renvoyé"
        assert renvoye["expire_le"], "sans l'expiration, le sceau ne se vérifie pas"

    def test_le_lien_renvoye_est_le_meme_et_il_marche(self, plateforme):
        """⚠️ LE MÊME, et pas un lien neuf : celui que le client a peut-être déjà,
        sur un courriel qu'il retrouvera demain. En émettre un autre invaliderait
        le premier sans que personne le sache."""
        client = plateforme
        proforma = _proforma_emise(client, "699115566")
        transmise = client.post(
            f"/acquisition/proformas/{proforma['numero']}/transmission", json={}
        ).json()

        assert transmise["lien_acceptation"] == proforma["lien_acceptation"]
        assert transmise["expire_le"] == proforma["expire_le"]

        # Et il ouvre réellement la proforma, sans session.
        client.cookies.clear()
        vue = client.get(
            f"/acquisition/proformas/{proforma['numero']}/consultation",
            params=_lien(transmise),
        )
        assert vue.status_code == 200, vue.text
        assert vue.json()["numero"] == proforma["numero"]

    def test_la_transmission_date_l_envoi(self, plateforme):
        """Elle garde sa raison d'être : c'est cette date qui arme la relance."""
        client = plateforme
        proforma = _proforma_emise(client, "699116677")
        transmise = client.post(
            f"/acquisition/proformas/{proforma['numero']}/transmission", json={}
        ).json()

        assert transmise["etat"] == "TRANSMISE"
        assert transmise["transmise_le"], "sans cette date, le balayage ne relance rien"


class TestLaVoieWhatsApp:
    """La proposition part aussi par WhatsApp, et la console n'invente rien.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ POURQUOI L'ADRESSE EST COMPOSÉE PAR LE SERVEUR.

    La console pouvait recomposer l'adresse à partir du sceau, de la version et
    de l'expiration — elle a les trois. Elle aurait alors existé en deux
    exemplaires : celui du courriel et celui de l'écran. Au premier paramètre
    renommé, l'un des deux serait devenu faux, et la cliente aurait reçu par
    WhatsApp un lien vers une page d'erreur — sans que rien ne le signale, car
    le courriel, lui, aurait continué de marcher.

    Le serveur la compose une fois, là où le courriel la compose.
    ─────────────────────────────────────────────────────────────────────────────
    """

    def test_l_emission_rend_l_adresse_complete_et_le_telephone(self, plateforme):
        client = plateforme
        proforma = _proforma_emise(client, "699221144")

        assert proforma["lien_client"], "sans adresse, pas d'envoi WhatsApp possible"
        assert proforma["numero"] in proforma["lien_client"]
        assert proforma["telephone"], "sans numéro, on ne sait pas à qui écrire"

    def test_l_adresse_rendue_ouvre_vraiment_la_proforma(self, plateforme):
        """⚠️ Le seul cas qui compte : l'adresse collée dans WhatsApp doit
        ouvrir SA proposition, et non une page d'erreur."""
        from urllib.parse import parse_qs, urlsplit

        client = plateforme
        proforma = _proforma_emise(client, "699221155")
        parties = urlsplit(proforma["lien_client"])
        parametres = parse_qs(parties.query)

        client.cookies.clear()
        vue = client.get(
            f"/acquisition/proformas/{proforma['numero']}/consultation",
            params={
                "version": parametres["v"][0],
                "expire_le": parametres["e"][0],
                "sceau": parametres["s"][0],
            },
        )
        assert vue.status_code == 200, vue.text
        assert vue.json()["numero"] == proforma["numero"]

    def test_la_transmission_rend_la_meme_adresse(self, plateforme):
        """Renvoyer par WhatsApp à quelqu'un qui a perdu son courriel doit
        donner le MÊME lien, pas un neuf."""
        client = plateforme
        proforma = _proforma_emise(client, "699221166")
        transmise = client.post(
            f"/acquisition/proformas/{proforma['numero']}/transmission", json={}
        ).json()

        assert transmise["lien_client"] == proforma["lien_client"]



class TestRetrouverLeLienSansRienChanger:
    """Le lien d'une proforma se relit, et relire ne note rien.

    ─────────────────────────────────────────────────────────────────────────────
    CE QUI MANQUAIT, ET CE QUE LE CLIENT PERDAIT

    Le lien n'était rendu qu'à **l'émission**. La console le gardait dans l'état
    d'un composant, et le perdait au premier rechargement de page : le bouton
    « Envoyer sur WhatsApp » disparaissait alors pour toujours.

    Vérifié le 29 septembre sur la pile, sur sept dossiers réels en
    `PROFORMA_EMISE` : **aucun** n'affichait le bouton. Le responsable qui
    revenait le lendemain sur un dossier n'avait plus d'autre voie que le
    courriel — pour une cliente qui avait peut-être expressément autorisé la
    messagerie et pas donné d'adresse.

    ⚠️ **AUCUN EFFET DE BORD, ET C'EST TOUT L'OBJET.** `POST /transmission` rend
    déjà le lien, et la console aurait pu s'en servir. Mais elle **date l'envoi**,
    et c'est cette date qui arme la relance : la rappeler pour relire un lien
    remettrait le compteur à zéro à chaque consultation, et le client ne serait
    jamais relancé.
    ─────────────────────────────────────────────────────────────────────────────
    """

    def test_le_lien_relu_est_le_meme_et_il_ouvre_la_proforma(self, plateforme):
        client = plateforme
        proforma = _proforma_emise(client, "699334455")

        relu = client.get(f"/acquisition/proformas/{proforma['numero']}/lien")
        assert relu.status_code == 200, relu.text
        corps = relu.json()
        assert corps["lien_client"] == proforma["lien_client"], (
            "deux adresses pour une proforma : l'une des deux mènera à une erreur"
        )

        # ⚠️ Et elle ouvre vraiment : une adresse qui ne mène nulle part serait
        # pire que pas d'adresse du tout, car personne ne le saurait.
        consultation = client.get(
            f"/acquisition/proformas/{proforma['numero']}/consultation",
            params=_lien(proforma),
        )
        assert consultation.status_code == 200, consultation.text

    def test_relire_ne_date_aucune_transmission(self, plateforme):
        """La garde qui donne son sens à cette route."""
        client = plateforme
        proforma = _proforma_emise(client, "699334466")

        for _ in range(3):
            assert (
                client.get(f"/acquisition/proformas/{proforma['numero']}/lien").status_code
                == 200
            )

        # L'état n'a pas bougé : toujours émise, jamais transmise.
        relu = client.get(f"/acquisition/proformas/{proforma['numero']}/lien").json()
        assert relu["etat"] == "EMISE", (
            "relire a marqué la proforma transmise : la relance ne partira jamais"
        )

    def test_elle_porte_de_quoi_composer_le_message_whatsapp(self, plateforme):
        client = plateforme
        proforma = _proforma_emise(client, "699334477")

        corps = client.get(f"/acquisition/proformas/{proforma['numero']}/lien").json()
        assert corps["telephone"], "sans numéro, on ne sait pas à qui écrire"
        assert corps["nom"], "le message s'ouvre sur « Bonjour {nom} »"
        assert corps["montant"], "le message annonce le montant"
        # ⚠️ Le consentement est rendu tel quel, jamais déduit : c'est lui qui
        # décide si la console propose le bouton ou explique son absence.
        assert corps["whatsapp_autorise"] is False, (
            "le dossier de ce cas n'a pas coché la messagerie ; le rendre vrai "
            "ferait proposer WhatsApp à quelqu'un qui l'a refusé"
        )

    def test_un_numero_inconnu_rend_404(self, plateforme):
        client = plateforme
        _proforma_emise(client, "699334488")
        assert client.get("/acquisition/proformas/PRO-2026-9999/lien").status_code == 404

    def test_lire_un_prospect_ne_suffit_pas_a_obtenir_le_lien(self, plateforme):
        """⚠️ Le lien est un PORTEUR : qui l'a, peut accepter à la place du client.

        La fiche du dossier ne le rend pas, et son en-tête le dit depuis le pas 78.
        Cette route exige la même habilitation que l'émission, pas celle, bien plus
        large, qui permet de consulter un prospect.
        """
        client = plateforme
        proforma = _proforma_emise(client, "699334499")

        # Un comptable tient des dossiers ; il ne qualifie pas de prospects.
        ouvrir_une_session(client, "l.fotso@cga-brcg.cm")
        refus = client.get(f"/acquisition/proformas/{proforma['numero']}/lien")
        assert refus.status_code == 403, refus.text


class TestLAcceptationLaisseUneTraceChezLeClient:
    """Elle engage son entreprise, et ne recevait rien.

    ─────────────────────────────────────────────────────────────────────────────
    CE QUE LA PILE A MONTRÉ, LE 29 SEPTEMBRE

    La page d'acceptation le dit en toutes lettres — « mon accord engage
    l'entreprise que je représente » — et le domaine le confirme : une proforma
    acceptée **vaut contrat**.

    En acceptant réellement, sur la pile, la boîte de la cliente est ressortie
    **inchangée** :

        avant  : ['Nous avons bien reçu votre demande — dos-…',
                  'Votre proposition PRO-2026-0039 : 75 000 FCFA']
        après  : les deux mêmes

    Ni le montant accepté, ni la date, ni le nom déclaré. Le cabinet, lui, avait
    tout. C'est le même défaut que l'accusé de dépôt corrigé la veille, un cran
    plus loin dans le parcours — et un cran plus grave, puisqu'il s'agit d'un
    engagement.
    ─────────────────────────────────────────────────────────────────────────────
    """

    def _proforma_pour_une_cliente_joignable(self, client, telephone, adresse):
        """Comme `_proforma_emise`, mais la demande porte une adresse.

        ⚠️ Le dossier d'essai partagé n'en porte PAS : l'adresse est facultative
        au formulaire public. Un cas d'accusé écrit sur ce dossier-là passerait
        au vert en ne vérifiant rien — c'est exactement le piège que le dernier
        cas de cette classe transforme en garde.
        """
        from tests.test_prix_du_dossier import REPONSES, _chiffrer, _qualifier

        ouvrir_une_session(client, RESPONSABLE)
        depot = client.post(
            "/acquisition/demandes",
            json={
                "nom": "Sylvie NGONO",
                "telephone": telephone,
                "courriel": adresse,
                "service_souhaite": "CREATION",
                "canal_prefere": "APPEL",
            },
        )
        assert depot.status_code in (200, 201), depot.text
        (dossier,) = [
            d for d in client.get("/acquisition/dossiers").json() if d["etat"] == "DEPOSEE"
        ]
        reference = dossier["reference"]
        client.post(f"/acquisition/dossiers/{reference}/affectation", json={})
        _qualifier(client, reference, REPONSES)
        montant = _chiffrer(client, reference).json()["reference"]
        emise = client.post(
            f"/acquisition/dossiers/{reference}/proforma", json={"montant": montant}
        )
        assert emise.status_code == 201, emise.text
        return emise.json()

    def _accepter(self, client, proforma, identite="La gérante"):
        return client.post(
            f"/acquisition/proformas/{proforma['numero']}/acceptation",
            json={**_lien(proforma), "identite_declaree": identite},
        )

    def test_elle_recoit_l_accuse_de_son_accord(self, plateforme):
        from app.contextes.transverse.api import service_de_notification

        client = plateforme
        service_de_notification().vider()
        proforma = self._proforma_pour_une_cliente_joignable(
            client, "699445500", "sylvie.ngono@exemple.cm"
        )
        assert self._accepter(client, proforma).status_code == 200

        (accuse,) = service_de_notification().derniers("proforma.acceptation")
        assert accuse.contexte["numero"] == proforma["numero"]
        # ⚠️ Les trois choses qu'elle n'avait pas et que le cabinet avait : ce
        # qu'elle a accepté, quand, et au nom de qui.
        assert accuse.contexte["montant"], "le montant accepté"
        assert accuse.contexte["acceptee_le"], "la date de l'accord"
        assert accuse.contexte["identite"] == "La gérante"
        assert accuse.destinataire == "sylvie.ngono@exemple.cm"

    def test_un_poste_muet_ne_fait_pas_perdre_la_signature(self, plateforme, monkeypatch):
        """⚠️ LA GARDE QUI COMPTE, ET ELLE VA DANS L'AUTRE SENS.

        L'accord EST enregistré, le dossier a suivi. Rendre une erreur parce
        qu'un serveur de messagerie n'a pas répondu ferait **perdre une signature
        contractuelle** — pour un accusé. C'est le raisonnement déjà tenu par la
        route pour la transition du dossier.
        """
        import app.contextes.souscription.adaptateurs.entrant.routes_acquisition as routes

        client = plateforme
        proforma = _proforma_emise(client, "699445511")

        def poste_en_panne():
            raise RuntimeError("relais injoignable")

        monkeypatch.setattr(routes, "service_de_notification", poste_en_panne)
        reponse = self._accepter(client, proforma)
        assert reponse.status_code == 200, reponse.text
        assert reponse.json()["numero"] == proforma["numero"]

    def test_sans_adresse_aucun_accuse_et_aucun_incident(self, plateforme):
        """L'adresse est facultative au formulaire : le rappel se fait au téléphone."""
        from app.contextes.transverse.api import service_de_notification

        client = plateforme
        service_de_notification().vider()
        proforma = _proforma_emise(client, "699445522")
        assert self._accepter(client, proforma).status_code == 200
        assert service_de_notification().derniers("proforma.acceptation") == []
