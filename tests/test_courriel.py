"""Ce que la plateforme envoie vraiment par courriel.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CES TESTS DÉFENDENT

Un courriel transactionnel est le **seul** moment où le système parle à quelqu'un
qui n'est pas connecté. S'il part cassé, il n'y a pas d'écran pour rattraper : un
adhérent qui a payé et dont le lien d'activation affiche `{lien}` n'a aucun
recours, et le cabinet ne l'apprend qu'au rappel téléphonique.

Trois familles de défauts sont couvertes ici :

* **le message ne doit pas partir incomplet** — mieux vaut un échec journalisé
  qu'un message inutilisable ;
* **le message ne doit jamais faire échouer le geste métier** — un relais en
  panne ne doit pas annuler un encaissement ;
* **le contexte ne doit pas s'injecter dans le balisage** — une dénomination
  sociale est une donnée, pas du HTML.

⚠️ Ce que ces tests ne prouvent **pas** : que le message arrive. La délivrabilité
tient à SPF, DKIM et DMARC sur le domaine du cabinet, et aucun test ne la
remplace.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import base64
import logging
import re
from datetime import datetime
from email.message import EmailMessage

import pytest

from app.contextes.transverse.adaptateurs.sortant.gabarits_courriel import (
    CATALOGUE,
    cles_du_gabarit,
    cles_manquantes,
    gabarit,
    rendre,
)
from app.contextes.transverse.adaptateurs.sortant.notifications_smtp import (
    ServiceNotificationSmtp,
    _masquer,
)

# ── Les contextes réellement fournis par les appelants ──────────────────────
#
# ⚠️ Recopiés des sites d'appel, et c'est le cœur de ce fichier. Un gabarit qui
# réclamerait `{societe}` alors que la route passe `{denomination}` compilerait,
# se déploierait, et n'échouerait qu'au premier envoi réel. Ici, il échoue à la
# seconde où quelqu'un renomme une clé.
#
# Origine de chaque entrée :
#   compte.activation        souscription/adaptateurs/sortant/ouverture_acces_transverse.py
#   compte.invitation        transverse/adaptateurs/entrant/routes_http.py  (inviter)
#   compte.reinitialisation  transverse/adaptateurs/entrant/routes_http.py  (mot de passe oublié)
#   relance.proforma         souscription/application/remise_des_relances.py
#   compte.second_facteur_enrole, compte.second_facteur_reinitialise
#                            transverse/adaptateurs/entrant/routes_http.py  (pas 60)
#   compte.second_facteur_confirmation
#                            transverse/adaptateurs/entrant/routes_http.py  (pas 62)
#   compte.suspendu          transverse/adaptateurs/entrant/routes_http.py  (pas 69)
#   compte.mot_de_passe_change  — au catalogue, pas encore appelé

CONTEXTES_DES_APPELANTS: dict[str, dict[str, str]] = {
    "compte.activation": {
        "prenom": "Paul",
        "denomination": "SARL BATIMENT PLUS",
        "service": "Adhésion annuelle",
        "lien": "https://www.cga-brcg.cm/activation?jeton=abc",
        "expire_le": "23 août 2026 à 10h00",
        "souscription": "SOUS-2026-0007",
    },
    "creation.dossier_ouvert": {
        # Recopié de `abonne_de_souscription._annoncer_l_ouverture`.
        "prenom": "Sylvie NGONO",
        "denomination": "NGONO COUTURE SARL",
        "lien": "https://www.cga-brcg.cm/fr/mon-dossier/CRE-dos-0412?s=abc",
    },
    "creation.dossier_ouvert_avec_compte": {
        # Recopié de `abonne_de_souscription._annoncer_l_ouverture`.
        "prenom": "Sylvie NGONO",
        "denomination": "NGONO COUTURE SARL",
        "lien": "https://www.cga-brcg.cm/fr/mon-dossier/CRE-dos-0412?s=abc",
        "lien_compte": "https://app.cga-brcg.cm/activation?jeton=abc&parcours=creation",
    },
    "compte.adhesion_ajoutee": {
        # Recopié de `abonne_acces_adherent._etendre`.
        "prenom": "Sylvie NGONO",
        "denomination": "NGONO COUTURE SARL",
        "niu": "M091122334455X",
        "lien": "https://app.cga-brcg.cm/connexion",
    },
    # 15 octobre 2026 : les avis à l'adhérent. `prenom` est posé par `prevenir_les_adherents`.
    "adherent.quittance_verifiee": {
        # Recopié de `routes_espace_adherent._prevenir_du_verdict`.
        "prenom": "Jean-Pierre",
        "obligation": "Cotisations sociales CNPS",
        "periode": "juillet 2026",
        "denomination": "SARL BATIMENT PLUS",
        "lien": "https://app.cga-brcg.cm/fr/mon-espace/echeances",
    },
    "adherent.quittance_refusee": {
        # Recopié de `routes_espace_adherent._prevenir_du_verdict`.
        "prenom": "Jean-Pierre",
        "obligation": "Cotisations sociales CNPS",
        "periode": "juillet 2026",
        "denomination": "SARL BATIMENT PLUS",
        "motif": "Quittance de juin, pas de juillet.",
        "lien": "https://app.cga-brcg.cm/fr/mon-espace/echeances",
    },
    "adherent.fonds_recus": {
        # Recopié de `abonne_fonds_de_tiers._prevenir_des_fonds`.
        "prenom": "Jean-Pierre",
        "obligation": "Cotisations sociales CNPS",
        "periode": "juin 2026",
        "montant": "184 500",
        "denomination": "SARL BATIMENT PLUS",
        "lien": "https://app.cga-brcg.cm/fr/mon-espace/echeances",
    },
    "mission.ouverte": {
        # Recopié de `routes_missions._prevenir_le_client`.
        "client": "Briqueterie du Wouri",
        "objet_de_la_demande": "Analyse du bail commercial",
        "reference": "DC-2026-0042",
        "lien": "https://www.cga-brcg.cm/fr/ma-mission/M-0001?s=abc",
    },
    "mission.livree": {
        "client": "Briqueterie du Wouri",
        "objet_de_la_demande": "Analyse du bail commercial",
        "reference": "DC-2026-0042",
        "lien": "https://www.cga-brcg.cm/fr/ma-mission/M-0001?s=abc",
    },
    # Site d'appel : `vitrine/.../routes_recrutement._postuler` (19 octobre 2026).
    "recrutement.accuse": {"nom": "Ngo Biyong Claire", "poste": "Comptable confirmé"},
    "recrutement.reponse": {
        # Recopié de `routes_recrutement.repondre` : le texte relu, en paragraphes.
        "objet": "Entretien pour le poste de Comptable confirmé",
        "poste": "Comptable confirmé",
        "p1": "Bonjour Claire,",
        "p2": "Nous avons le plaisir de vous inviter à un entretien.",
        **{f"p{rang}": "" for rang in range(3, 11)},
    },
    "adherent.honoraires_regles": {
        # Recopié de `abonne_avis_honoraires.prevenir_du_reglement`.
        "prenom": "Jean-Pierre",
        "periode": "octobre 2026",
        "montant": "45 000",
        "reference": "Reçu de caisse 2026-0777",
        "denomination": "SARL BATIMENT PLUS",
        "lien": "https://app.cga-brcg.cm/fr/mon-espace/honoraires",
    },
    "proforma.acceptation": {
        # Recopié de `routes_acquisition._accuser_l_acceptation`.
        "prenom": "Sylvie NGONO",
        "numero": "PRO-2026-0042",
        "montant": "250\u202f000",
        "acceptee_le": "29/09/2026 à 16h03",
        "identite": "La gérante",
    },
    "demande.accuse_de_depot": {
        # Recopié de `abonne_d_accuse_de_depot.accuser_le_depot`.
        "prenom": "Sylvie NGONO",
        # ⚠️ **LA VRAIE FORME, ET NON UNE PLUS JOLIE.**
        #
        # Ce contexte portait « dos-2026-0412 », qui se lit bien et n'existe pas :
        # la route forge `f"dos-{uuid4().hex[:16]}"` (`routes_acquisition.py`).
        # La garde des identifiants techniques ne se déclenchait donc pas, et le
        # défaut réel — un objet de courriel affichant
        # « Votre demande dos-cf776b28a0db44b4 nous est bien parvenue » — passait
        # sous elle sans bruit.
        #
        # Un contexte de test plus présentable que la production ne vérifie que
        # les cas qu'on aurait de toute façon écrits juste.
        "reference": "dos-cf776b28a0db44b4",
        "service": "Création d'entreprise",
        "telephone": "+237699001122",
    },
    "compte.activation_creation": {
        # Recopié de `abonne_acces_adherent._envoyer_le_lien`.
        "prenom": "Sylvie",
        "denomination": "SYLVIE COUTURE SARL",
        "niu": "M091122334455X",
        "lien": "https://www.cga-brcg.cm/activation?jeton=abc",
        "expire_le": "05/10/2026 à 12h29",
    },
    "compte.invitation": {
        "prenom": "Awa",
        "role": "comptable",
        "lien": "/activation?jeton=xyz",
        "expire_le": "30 août 2026 à 09h15",
    },
    "compte.reinitialisation": {
        "prenom": "Awa",
        "lien": "/reinitialisation?jeton=xyz",
    },
    "compte.mot_de_passe_change": {"prenom": "Awa"},
    "compte.suspendu": {"prenom": "Awa"},
    "compte.retabli": {"prenom": "Awa"},
    # Recopié de `_prevenir_les_adherents`, collecte (pas 74).
    "piece.rectificative_demandee": {
        "prenom": "Jean-Pierre",
        "reference": "F-2026-0414",
        "motif": (
            "le NIU du fournisseur est absent ou radié : la TVA n'est pas déductible en l'état"
        ),
    },
    # Pas 111 : `pilotage/adaptateurs/entrant/routes_relance.py`, `_envoyer_par_courriel`.
    # Pas 116 : `transverse/adaptateurs/entrant/routes_acces_adherent.py`, `renvoyer_un_lien`.
    "compte.acces_renvoye": {
        "prenom": "Émile",
        "denomination": "ETS TCHOUMBA & FILS",
        "geste": "définir votre mot de passe et ouvrir votre espace",
        "validite": "7 jours",
        "lien": "/activation?jeton=xyz",
    },
    # Pas 115 : `obligations/adaptateurs/entrant/travail_des_rappels.py`, `envoyer_les_rappels`.
    "echeance.rappel": {
        "prenom": "Jean-Pierre",
        "titre": "Cotisations sociales CNPS",
        "periode": "août 2026",
        "echeance": "15/09/2026",
        "jours": "7",
    },
    "piece.relance": {
        "prenom": "Jean-Paul",
        "mois": "juillet 2026",
        "introduction": "Pour terminer votre mois de juillet 2026, il nous manque encore :",
        "liste": "• le relevé bancaire BQ de juillet 2026 • la facture QUINCAILLERIE DU WOURI",
        "conclusion": (
            "Sans ces pièces avant le 12/08/2026, votre déclaration sera établie sans elles."
        ),
        "signature": "Léonard FOTSO, CGA Broad Range",
    },
    "compte.second_facteur_confirmation": {
        "prenom": "Awa",
        "lien": "/second-facteur/confirmation?jeton=xyz",
    },
    "compte.second_facteur_enrole": {"prenom": "Awa"},
    "compte.second_facteur_reinitialise": {"prenom": "Awa"},
    # ⚠️ Recopié de `_par_courriel` : le contexte porte cinq clés, et le gabarit
    # n'en emploie que deux. Les trois autres sont là pour le jour où le message
    # variera selon le ton et le palier — les fournir dès maintenant évite un
    # second passage sur l'appelant quand ce jour viendra, et le contrôle
    # d'avant-envoi ne se plaint que des clés **manquantes**.
    "relance.proforma": {
        "proforma": "PRO-2026-0001",
        "rang": "2",
        "ton": "ferme",
        "depuis_jours": "8",
        "derniere": "False",
    },
    # Recopié de `_envoyer_la_proforma_par_courriel` (routes_acquisition.py).
    # Recopié de la même fonction, branche « avec document » (14 octobre 2026).
    "proforma.envoi.detaillee": {
        "nom": "Station Bonabéri",
        "proforma": "PRO-2026-0001",
        "montant": "505\u202f000",
        "lien": "https://www.cga-brcgroup.com/fr/proforma/PRO-2026-0001?v=1&e=x&s=y",
        "expire_le": "22/10/2026",
        "detail": [
            ("I. Procedure au cfce", "177\u202f000 FCFA"),
            ("Total général", "505\u202f000 FCFA"),
        ],
        "pieces_jointes": [("Proforma-PRO-2026-0001.pdf", b"%PDF-1.4", "application/pdf")],
    },
    "proforma.envoi": {
        "nom": "Station Bonabéri",
        "proforma": "PRO-2026-0001",
        "montant": "350\u202f000",
        "lien": "https://www.cga-brcgroup.com/fr/proforma/PRO-2026-0001?v=1&e=x&s=y",
        "expire_le": "22/10/2026",
    },
}


class TransportEspion:
    """Retient le message au lieu de le remettre."""

    def __init__(self) -> None:
        self.remis: list[EmailMessage] = []

    def remettre(self, message: EmailMessage) -> None:
        self.remis.append(message)


class TransportEnPanne:
    """Le relais injoignable."""

    def remettre(self, message: EmailMessage) -> None:
        raise OSError("relais injoignable")


def _service(transport: object, **reglages: str) -> ServiceNotificationSmtp:
    return ServiceNotificationSmtp(
        transport=transport,  # type: ignore[arg-type]
        expediteur=reglages.get("expediteur", "CGA <ne-pas-repondre@cga-brcg.cm>"),
        repondre_a=reglages.get("repondre_a", "contact@cga-brcg.cm"),
        adresse_site=reglages.get("adresse_site", "https://www.cga-brcg.cm"),
    )


def _corps(message: EmailMessage) -> tuple[str, str]:
    """Le corps texte et le corps HTML du message multipart."""
    texte = message.get_body(preferencelist=("plain",))
    html = message.get_body(preferencelist=("html",))
    assert texte is not None and html is not None
    return texte.get_content(), html.get_content()


# ── Le catalogue ────────────────────────────────────────────────────────────


class TestCatalogue:
    def test_chaque_gabarit_se_rend_avec_le_contexte_de_son_appelant(self):
        """Le test qui empêche un renommage de clé de passer en production."""
        for code, gabarit_ in CATALOGUE.items():
            assert code in CONTEXTES_DES_APPELANTS, (
                f"Le gabarit {code!r} n'a pas de contexte de référence dans ce "
                "fichier. Ajoutez-le en recopiant son site d'appel."
            )
            manquantes = cles_manquantes(gabarit_, CONTEXTES_DES_APPELANTS[code])
            assert not manquantes, (
                f"Le gabarit {code!r} réclame {sorted(manquantes)}, "
                "que son appelant ne fournit pas."
            )

    def test_le_catalogue_couvre_les_codes_documentes(self):
        """Les codes annoncés par l'adaptateur en mémoire doivent tous exister.

        Les deux listes se sont écrites à des moments différents ; celle qui
        n'est pas exécutée est celle qui ment.
        """
        from app.contextes.transverse.adaptateurs.sortant.notifications_memoire import (
            CODES_MESSAGES,
        )

        assert frozenset(CODES_MESSAGES) == frozenset(CATALOGUE)

    def test_un_code_inconnu_ne_rend_pas_de_gabarit(self):
        assert gabarit("compte.inexistant") is None

    def test_chaque_gabarit_porte_l_agrement(self):
        """L'agrément distingue un CGA d'un cabinet ordinaire.

        Un destinataire qui doute de l'expéditeur le cherche, et un courriel
        d'activation est exactement le message qu'un hameçonnage imite.
        """
        for code, gabarit_ in CATALOGUE.items():
            rendu = rendre(gabarit_, CONTEXTES_DES_APPELANTS[code])
            assert "00000048" in rendu.html
            assert "00000048" in rendu.texte

    def test_les_gabarits_a_lien_annoncent_tous_une_expiration(self):
        """Un lien sans délai affiché laisse croire qu'il vaut indéfiniment.

        L'adhérent le range « pour plus tard », et le retrouve mort.
        """
        for code, gabarit_ in CATALOGUE.items():
            if gabarit_.action is None:
                continue
            rendu = rendre(gabarit_, CONTEXTES_DES_APPELANTS[code])
            texte = rendu.texte.lower()
            assert "expire" in texte or "valable" in texte, code


class TestRendu:
    def test_le_contexte_est_echappe_dans_le_html_et_brut_dans_le_texte(self):
        """Une dénomination sociale est une donnée, jamais du balisage."""
        rendu = rendre(
            CATALOGUE["compte.activation"],
            CONTEXTES_DES_APPELANTS["compte.activation"]
            | {"denomination": 'BÂTIMENT <b>PLUS</b> & "Cie"'},
        )
        assert "&lt;b&gt;PLUS&lt;/b&gt;" in rendu.html
        assert "<b>PLUS</b>" not in rendu.html
        assert "&amp;" in rendu.html
        # Le corps texte n'est pas du balisage : la valeur y reste lisible.
        assert 'BÂTIMENT <b>PLUS</b> & "Cie"' in rendu.texte

    def test_le_corps_texte_ne_contient_pas_de_balises_du_gabarit(self):
        """Le HTML est désactivé dans beaucoup de messageries d'entreprise."""
        for code, gabarit_ in CATALOGUE.items():
            rendu = rendre(gabarit_, CONTEXTES_DES_APPELANTS[code])
            assert "<strong>" not in rendu.texte, code
            assert "</strong>" not in rendu.texte, code

    def test_le_lien_figure_en_clair_dans_le_corps_texte(self):
        """Un bouton HTML ne se clique pas dans un client en texte seul.

        Sans l'adresse écrite en toutes lettres, ce destinataire-là n'a aucun
        moyen d'activer son compte.
        """
        rendu = rendre(
            CATALOGUE["compte.activation"], CONTEXTES_DES_APPELANTS["compte.activation"]
        )
        assert "https://www.cga-brcg.cm/activation?jeton=abc" in rendu.texte

    def test_les_cles_du_gabarit_couvrent_objet_action_et_avertissement(self):
        cles = cles_du_gabarit(CATALOGUE["compte.activation"])
        assert {"denomination", "lien", "expire_le", "prenom", "service"} <= cles


# ── L'envoi ─────────────────────────────────────────────────────────────────


class TestEnvoi:
    def test_un_message_complet_part(self):
        transport = TransportEspion()
        assert (
            _service(transport).envoyer(
                "compte.invitation",
                destinataire="awa@exemple.cm",
                contexte=CONTEXTES_DES_APPELANTS["compte.invitation"],
            )
            is True
        )
        (message,) = transport.remis
        assert message["To"] == "awa@exemple.cm"
        assert message["Reply-To"] == "contact@cga-brcg.cm"
        assert "accès" in message["Subject"]
        texte, html = _corps(message)
        assert "Awa" in texte
        assert "comptable" in html

    def test_un_lien_relatif_devient_absolu(self):
        """Les appelants ne connaissent pas l'adresse publique du site.

        Elle diffère entre développement, recette et production ; la leur faire
        porter garantirait qu'un jour l'une d'elles enverra un lien vers
        `localhost`.
        """
        transport = TransportEspion()
        _service(transport).envoyer(
            "compte.invitation",
            destinataire="awa@exemple.cm",
            contexte=CONTEXTES_DES_APPELANTS["compte.invitation"],
        )
        texte, _ = _corps(transport.remis[0])
        assert "https://www.cga-brcg.cm/activation?jeton=xyz" in texte

    def test_un_lien_deja_absolu_passe_intact(self):
        """Le contexte M construit le sien : il connaît la page d'activation."""
        transport = TransportEspion()
        _service(transport, adresse_site="https://autre.example").envoyer(
            "compte.activation",
            destinataire="paul@exemple.cm",
            contexte=CONTEXTES_DES_APPELANTS["compte.activation"],
        )
        texte, _ = _corps(transport.remis[0])
        assert "https://www.cga-brcg.cm/activation?jeton=abc" in texte
        assert "https://autre.example/activation" not in texte

    def test_une_cle_manquante_annule_l_envoi(self):
        """Mieux vaut ne rien envoyer qu'un message affichant « {lien} ».

        Le destinataire croirait avoir été servi, et n'aurait aucun recours.
        """
        transport = TransportEspion()
        incomplet = dict(CONTEXTES_DES_APPELANTS["compte.invitation"])
        del incomplet["lien"]
        assert (
            _service(transport).envoyer(
                "compte.invitation", destinataire="awa@exemple.cm", contexte=incomplet
            )
            is False
        )
        assert transport.remis == []

    def test_un_code_inconnu_n_envoie_rien_et_ne_leve_pas(self):
        transport = TransportEspion()
        assert (
            _service(transport).envoyer(
                "compte.inexistant", destinataire="awa@exemple.cm", contexte={}
            )
            is False
        )
        assert transport.remis == []

    def test_un_relais_en_panne_ne_leve_jamais(self):
        """Le pire n'est pas « le message n'est pas parti ».

        C'est « le paiement a été encaissé et le compte n'existe pas ». Une
        exception qui remonterait ici annulerait la transaction métier.
        """
        assert (
            _service(TransportEnPanne()).envoyer(
                "compte.invitation",
                destinataire="awa@exemple.cm",
                contexte=CONTEXTES_DES_APPELANTS["compte.invitation"],
            )
            is False
        )

    def test_le_journal_ne_contient_jamais_le_lien(self, caplog):
        """Le lien **est** le secret. Une trace qui le porte vaut un vol de compte."""
        caplog.set_level(logging.DEBUG, logger="cga.courriel")
        _service(TransportEnPanne()).envoyer(
            "compte.invitation",
            destinataire="awa@exemple.cm",
            contexte=CONTEXTES_DES_APPELANTS["compte.invitation"],
        )
        traces = caplog.text
        assert traces  # l'échec est bien journalisé
        assert "jeton=xyz" not in traces
        assert "awa@exemple.cm" not in traces

    def test_l_adresse_est_masquee_dans_les_traces(self):
        assert _masquer("awa.diallo@cga-brcg.cm") == "a***@cga-brcg.cm"
        assert _masquer("pas-une-adresse") == "adresse-invalide"

    def test_le_corps_texte_precede_le_html(self):
        """Les filtres anti-pourriel lisent la première variante.

        Un message dont la seule partie lisible est du HTML se classe mal, et un
        courriel classé en indésirable équivaut à un courriel non envoyé.
        """
        transport = TransportEspion()
        _service(transport).envoyer(
            "compte.suspendu",
            destinataire="awa@exemple.cm",
            contexte=CONTEXTES_DES_APPELANTS["compte.suspendu"],
        )
        types = [part.get_content_type() for part in transport.remis[0].walk()]
        assert types.index("text/plain") < types.index("text/html")


# ── Le choix de l'adaptateur ────────────────────────────────────────────────


class TestChoixDuService:
    def test_sans_relais_configure_rien_ne_part(self):
        """Le défaut de développement : les messages sont retenus, pas envoyés.

        C'est ce qui empêche une suite de tests d'écrire à de vraies adresses.
        """
        from app.contextes.transverse.adaptateurs.entrant.dependances import (
            service_de_notification,
        )
        from app.contextes.transverse.adaptateurs.sortant.notifications_memoire import (
            ServiceNotificationMemoire,
        )

        service_de_notification.cache_clear()
        assert isinstance(service_de_notification(), ServiceNotificationMemoire)


class TestConfigurationDeProduction:
    """Les réglages qu'un déploiement de production ne peut pas se permettre.

    Chacun de ces défauts est invisible à l'exécution : l'application démarre,
    répond, et perd les données ou n'envoie rien. Le seul moment où ils coûtent
    peu est le démarrage.
    """

    def _reglages(self, **surcharges: object) -> dict[str, object]:
        base = {
            "environnement": "production",
            "persistance": "postgresql",
            "smtp_hote": "smtp.exemple.cm",
            # ⚠️ L'adresse de l'API en https : c'est elle qui décide si le témoin
            # de session circule chiffré (`Configuration.temoin_securise`).
            "adresse_publique": "https://api.cga-brcg.cm",
            "adresse_publique_site": "https://www.cga-brcg.cm",
            "adresse_publique_vitrine": "https://www.cga-brcgroup.com",
            "origines_cors": ["https://www.cga-brcg.cm"],
            "cle_chiffrement": base64.b64encode(bytes(32)).decode("ascii"),
        }
        return base | surcharges

    def test_une_production_correctement_reglee_demarre(self):
        from app.infrastructure.config import Configuration

        assert Configuration(**self._reglages()).en_production is True

    @pytest.mark.parametrize(
        ("surcharge", "attendu"),
        [
            ({"persistance": "memoire"}, "disparaissent au redémarrage"),
            ({"smtp_hote": ""}, "aucun compte ne pourrait être activé"),
            (
                {"smtp_chiffrement": False},
                "expose l'authentification SMTP en clair",
            ),
            (
                {"adresse_publique_site": "http://www.cga-brcg.cm"},
                "doit être en https",
            ),
            ({"origines_cors": ["http://localhost:3000"]}, "origine en clair"),
            (
                {"cle_chiffrement": ""},
                "livrerait tous les seconds facteurs",
            ),
        ],
    )
    def test_un_reglage_dangereux_refuse_le_demarrage(
        self, surcharge: dict, attendu: str
    ):
        from app.infrastructure.config import Configuration

        with pytest.raises(ValueError, match="Configuration de production refusée"):
            Configuration(**self._reglages(**surcharge))
        # Le message doit dire **pourquoi**, pas seulement que c'est refusé :
        # celui qui déploie à 3 h du matin n'ira pas lire le code.
        try:
            Configuration(**self._reglages(**surcharge))
        except ValueError as refus:
            assert attendu in str(refus)

    def test_le_developpement_n_est_soumis_a_aucune_de_ces_exigences(self):
        """Sinon personne ne pourrait lancer l'application sur son poste."""
        from app.infrastructure.config import Configuration

        reglages = Configuration(environnement="developpement", persistance="memoire")
        assert reglages.en_production is False


class TestAucunCodeDemandeN_estAbsentDuCatalogue:
    """Le garde-fou qui manquait, et le défaut qu'il aurait évité.

    ─────────────────────────────────────────────────────────────────────────────
    Ce fichier vérifiait déjà trois correspondances : le catalogue contre le
    catalogue de démonstration, le catalogue contre les contextes de référence, et
    chaque gabarit contre le contexte que ses appelants fournissent.

    ⚠️ **Aucune ne vérifiait que les codes réellement demandés par l'application
    existent.** L'abonné de relance a été écrit en réclamant `relance_proforma`, qui
    n'était nulle part : `gabarit()` rendait `None`, `envoyer()` rendait `False`, la
    remise levait, et **aucune relance ne serait jamais partie par courriel**.

    Rien ne l'aurait signalé avant le premier envoi réel. Les trois contrôles
    existants portaient tous sur des listes tenues à la main : elles restent
    cohérentes entre elles quand on oublie d'y ajouter une entrée.

    CE CAS LIT LE CODE, ET NON UNE LISTE

    C'est ce qui le rend fiable : il ne peut pas rester d'accord avec un oubli.
    ─────────────────────────────────────────────────────────────────────────────
    """

    def _codes_demandes(self) -> dict[str, str]:
        """Les codes que l'application réclame, avec le fichier qui les réclame.

        Deux formes sont reconnues, parce que les deux existent dans ce dépôt : une
        chaîne passée directement à `envoyer(...)`, et une constante de module
        nommée `CODE_COURRIEL...`. Une troisième forme apparaîtrait ici comme un
        code non trouvé, ce qui est le bon sens de l'échec : ce cas doit alerter
        quand il cesse de tout voir, pas se taire.
        """
        import ast
        from pathlib import Path

        racine = Path(__file__).resolve().parents[1] / "app"
        trouves: dict[str, str] = {}
        for fichier in sorted(racine.rglob("*.py")):
            if "gabarits_courriel" in fichier.name or "notifications_" in fichier.name:
                continue  # le catalogue lui-même, et le service qui le lit
            arbre = ast.parse(fichier.read_text(encoding="utf-8"))
            for noeud in ast.walk(arbre):
                if (
                    isinstance(noeud, ast.Call)
                    and isinstance(noeud.func, ast.Attribute)
                    and noeud.func.attr == "envoyer"
                    and noeud.args
                    and isinstance(noeud.args[0], ast.Constant)
                    and isinstance(noeud.args[0].value, str)
                ):
                    trouves[noeud.args[0].value] = fichier.name
                if isinstance(noeud, ast.Assign):
                    for cible in noeud.targets:
                        if (
                            isinstance(cible, ast.Name)
                            and cible.id.startswith("CODE_COURRIEL")
                            and isinstance(noeud.value, ast.Constant)
                            and isinstance(noeud.value.value, str)
                        ):
                            trouves[noeud.value.value] = fichier.name
        return trouves

    def test_le_scan_trouve_bien_des_codes(self):
        """⚠️ La contre-épreuve, et elle compte plus que le cas suivant.

        Un scan qui ne trouverait rien passerait le contrôle en ne vérifiant rien.
        C'est exactement la forme de test vert qui ne prouve rien, et elle
        surviendrait au premier refactoring qui changerait la façon d'appeler.
        """
        demandes = self._codes_demandes()
        assert len(demandes) >= 2, (
            "le scan ne reconnaît plus les appels : il passerait au vert sans rien "
            f"vérifier. Trouvé : {demandes}"
        )

    def test_chaque_code_demande_existe_au_catalogue(self):
        absents = {
            code: fichier
            for code, fichier in self._codes_demandes().items()
            if code not in CATALOGUE
        }
        assert not absents, (
            "ces codes de courriel sont demandés par l'application et n'existent "
            f"pas au catalogue : {absents}. L'envoi rendrait `False` sans rien "
            "poster, et le geste métier qui l'a déclenché échouerait au premier "
            "envoi réel."
        )


class TestLaSalutationNEstJamaisVide:
    """« Bonjour  », et le message qui suit réclamait de l'argent.

    ─────────────────────────────────────────────────────────────────────────────
    CE QUI S'EST PASSÉ, ET CE QUE LA SUITE N'A PAS VU

    `Compte.prenom` a été relâché à `""` le 28 septembre, pour laisser entrer une
    fondatrice dont le formulaire public ne porte qu'UN champ de nom. La
    correction était juste, et la suite entière est restée verte.

    Le soir même, un rappel d'échéance réel est parti de la pile de démonstration
    vers une adhérente. Il s'ouvrait sur :

        Bonjour

    Rien, et une espace en trop. Douze messages composaient leur salutation sur
    `compte.prenom` brut ; deux seulement se rabattaient sur le nom, et c'étaient
    les deux écrits le même jour que la correction.

    ⚠️ **Une suite verte ne prouve que ce qu'elle mesure.** Celle-ci ne mesurait
    pas la salutation. Ces deux cas la mesurent : l'un sur le modèle, l'autre sur
    le code source, parce que le treizième appelant n'est pas encore écrit.
    ─────────────────────────────────────────────────────────────────────────────
    """

    def test_un_compte_sans_prenom_garde_une_appellation(self):
        from app.contextes.transverse.api import Compte

        compte = Compte(
            identifiant="A-essai",
            courriel="sylvie@exemple.cm",
            nom="Sylvie NGONO",
            locataire="cga-brcg",
            cree_le=datetime(2026, 9, 28, 12, 0),
        )
        assert compte.prenom == ""
        assert compte.appellation == "Sylvie NGONO"
        # Et rien qui commence ou finisse par une espace, nulle part.
        assert compte.nom_affiche == "Sylvie NGONO"

    def test_un_compte_avec_prenom_est_appele_par_son_prenom(self):
        from app.contextes.transverse.api import Compte

        compte = Compte(
            identifiant="A-essai",
            courriel="awa@exemple.cm",
            nom="MBIDA",
            prenom="Awa",
            locataire="cga-brcg",
            cree_le=datetime(2026, 9, 28, 12, 0),
        )
        assert compte.appellation == "Awa"
        assert compte.nom_affiche == "Awa MBIDA"

    def test_aucun_appelant_ne_compose_la_salutation_a_la_main(self):
        """⚠️ LA GARDE QUI TIENT DANS LE TEMPS.

        Le cas précédent prouve que la propriété est bonne ; il ne prouve pas
        qu'on s'en sert. Celui-ci lit le code source : un contexte de courriel
        qui remplit `prenom` autrement que par `appellation` est refusé, y
        compris `compte.prenom or compte.nom` — qui rend pourtant le bon
        résultat, mais qu'il faut penser à écrire.
        """
        import pathlib
        import re

        racine = pathlib.Path(__file__).resolve().parent.parent / "app"

        # ⚠️ La SEULE exemption, et elle est nommée : ce module écrit le champ
        # `prenom` d'un salarié dans SA colonne de base. Ce n'est pas une
        # salutation, et y appliquer un repli sur le nom rangerait un faux.
        EXEMPTES = {"contextes/social/adaptateurs/sortant/depots.py"}

        # ⚠️ Une CHAÎNE d'attributs est acceptée, tant qu'elle finit par
        # `appellation` : `dossier.fondateur.appellation` est aussi juste que
        # `compte.appellation`. La première version n'admettait qu'un point, et
        # refusait un appelant correct — une garde qui refuse le bon geste finit
        # par se faire contourner.
        autorises = re.compile(r'"prenom":\s*([\w.]+\.appellation|demande\.nom)\b')
        fautifs = []
        for fichier in sorted(racine.rglob("*.py")):
            chemin = str(fichier.relative_to(racine))
            if chemin in EXEMPTES:
                continue
            for numero, ligne in enumerate(fichier.read_text().splitlines(), 1):
                if '"prenom":' not in ligne or autorises.search(ligne):
                    continue
                fautifs.append(f"{chemin}:{numero} — {ligne.strip()}")

        assert not fautifs, (
            "Ces appelants composent la salutation à la main. Un compte sans "
            "prénom leur fera écrire « Bonjour  ». Employez "
            "`compte.appellation`.\n  " + "\n  ".join(fautifs)
        )


class TestAucunIdentifiantTechnique:
    """⚠️ NI À L'OBJET, NI DANS LE CORPS.

    L'accusé de dépôt portait « Votre demande dos-cf776b28a0db44b4 nous est bien
    parvenue ». Vingt caractères hexadécimaux qu'aucune cliente ne lit, ne
    retient ni ne dicte au téléphone, à l'endroit précis où le message doit se
    reconnaître d'un coup d'œil. Vu en lisant la boîte d'une recette de bout en
    bout : aucun test ne regardait ce que l'objet devenait une fois rendu.

    ⚠️ **ON MESURE LE RENDU, PAS LE GABARIT.** `{reference}` dans le gabarit ne
    dit pas ce qu'il vaudra : c'est la substitution qui révèle l'identifiant.

    Ce qui reste permis : un numéro fait pour être dicté, comme `PRO-2026-0049`.
    Ce qui ne l'est pas : un préfixe technique suivi d'hexadécimal.
    """

    #: Un préfixe alphabétique court, éventuellement redoublé (`CRE-dos-`), suivi
    #: d'au moins huit caractères hexadécimaux. C'est la LONGUEUR qui distingue un
    #: identifiant interne d'un mot ordinaire porteur d'un tiret.
    TECHNIQUE = re.compile(r"\b[a-zA-Z]{3,6}-(?:[a-z]{3}-)?[0-9a-f]{8,}\b")

    def test_aucun_objet_rendu_n_en_porte(self):
        fautifs = [
            (code, rendre(g, CONTEXTES_DES_APPELANTS[code]).objet)
            for code, g in CATALOGUE.items()
            if self.TECHNIQUE.search(rendre(g, CONTEXTES_DES_APPELANTS[code]).objet)
        ]
        assert not fautifs, "identifiant technique à l'objet :\n  " + "\n  ".join(
            f"{code} : {objet}" for code, objet in fautifs
        )

    def test_aucun_corps_rendu_n_en_porte_non_plus(self):
        """⚠️ **L'OBJET N'ÉTAIT QU'UNE MOITIÉ DU GESTE.**

        La première version de cette garde ne regardait que l'objet. Le corps de
        l'accusé de dépôt gardait « Elle porte la référence dos-cf776b28a0db44b4
        ; conservez-la, elle nous permet de retrouver votre dossier
        immédiatement. »

        C'est une promesse que le format ne tient pas : seize caractères
        hexadécimaux ne se dictent pas au téléphone, ne se notent pas sans faute
        et ne se relisent pas. Et c'était inutile, la phrase suivante disant que
        le cabinet rappelle la cliente sur son numéro.

        ⚠️ Le HTML est contrôlé aussi : l'échappement peut couper une chaîne
        autrement, et ce qui passe en texte ne passe pas forcément en HTML.
        """
        fautifs = []
        for code, g in CATALOGUE.items():
            rendu = rendre(g, CONTEXTES_DES_APPELANTS[code])
            for ou, corps in (("texte", rendu.texte), ("html", rendu.html)):
                trouves = set(self.TECHNIQUE.findall(corps))
                fautifs += [f"{code} ({ou}) : {x}" for x in sorted(trouves)]
        assert not fautifs, "identifiant technique dans un corps :\n  " + "\n  ".join(
            fautifs
        )

    def test_la_garde_reconnait_bien_ce_qu_elle_cherche(self):
        """⚠️ Une garde qui ne se déclenche jamais ne garde rien.

        On lui montre la chaîne exacte qui a motivé ce fichier, et deux objets du
        parcours qui doivent rester permis.
        """
        assert self.TECHNIQUE.search("Votre demande dos-cf776b28a0db44b4 nous est parvenue")
        assert self.TECHNIQUE.search("Dossier CRE-dos-cf776b28a0db44b4 ouvert")
        assert not self.TECHNIQUE.search("Votre proposition PRO-2026-0049 : 505 000 FCFA")
        assert not self.TECHNIQUE.search("Votre dossier NGONO COUTURE SARL est ouvert")


class TestAucunTiretCadratin:
    """Le cabinet n'en veut nulle part dans ce qu'un client lit.

    ─────────────────────────────────────────────────────────────────────────────
    LA RÈGLE EXISTAIT, ELLE NE COUVRAIT QUE LE DOCUMENT DE CONCEPTION

    La chaîne d'intégration refuse depuis longtemps le tiret cadratin dans
    `architecture-multitenant-cga.html`, avec sa raison : « il s'y glisse sans
    qu'on le voie en relecture, il ressemble à un trait d'union un peu long ».

    Le 30 septembre, le cabinet l'a étendue à **tous les messages**. Mesuré :
    trente-sept en sortaient, dont trente-quatre venaient du pied de page et de
    son séparateur, répétés sur les dix-huit gabarits.

    ⚠️ **ON MESURE LE RENDU, PAS LE SOURCE.** Un gabarit peut porter un cadratin
    dans son commentaire sans que le client le voie ; et il peut en recevoir un
    par une valeur interpolée, que la lecture du source ne montrerait pas. Ce
    sont les octets envoyés qui comptent.
    ─────────────────────────────────────────────────────────────────────────────
    """

    def test_aucun_gabarit_n_en_envoie_dans_son_texte(self):
        fautifs = []
        for code, gabarit_ in CATALOGUE.items():
            rendu = rendre(gabarit_, CONTEXTES_DES_APPELANTS[code])
            for ligne in rendu.texte.splitlines():
                if "—" in ligne:
                    fautifs.append(f"{code} · {ligne.strip()}")
        assert not fautifs, "tiret cadratin dans le texte reçu :\n  " + "\n  ".join(fautifs)

    def test_ni_dans_son_html(self):
        fautifs = [
            code
            for code, gabarit_ in CATALOGUE.items()
            if "—" in rendre(gabarit_, CONTEXTES_DES_APPELANTS[code]).html
        ]
        assert not fautifs, f"tiret cadratin dans le HTML de : {fautifs}"

    def test_ni_dans_un_objet(self):
        """⚠️ L'objet est la SEULE ligne que tout le monde lit, y compris ceux qui
        n'ouvrent pas le message."""
        fautifs = [code for code, g in CATALOGUE.items() if "—" in g.objet]
        assert not fautifs, f"tiret cadratin dans l'objet de : {fautifs}"

    def test_ni_dans_les_modeles_whatsapp(self):
        """Le même client, l'autre canal."""
        import pathlib

        import yaml

        racine = pathlib.Path(__file__).resolve().parent.parent
        dossier = racine / "Docs" / "referentiel" / "messagerie" / "modeles"
        fautifs = [
            m["nom"]
            for f in sorted(dossier.glob("*.yaml"))
            if "—" in (m := yaml.safe_load(f.read_text()))["corps"]
        ]
        assert not fautifs, f"tiret cadratin dans le corps de : {fautifs}"

    def test_la_signature_emploie_le_separateur_des_courriels(self):
        """⚠️ `-- ` est posé par la RFC 3676 : les clients de messagerie le
        reconnaissent et replient ce qui suit. Un cadratin ne leur dit rien."""
        rendu = rendre(CATALOGUE["compte.invitation"], CONTEXTES_DES_APPELANTS["compte.invitation"])
        assert "\n-- \n" in rendu.texte
        assert "agrément MINFI/DGI n° 00000048" in rendu.texte


class TestLeCourrielSuitLeDocument:
    """⚠️ LE LOGO, LE DÉTAIL ET LA PIÈCE JOINTE (14 octobre 2026).

    Le cabinet veut que ses courriels suivent le style de ses proformas, logo
    compris. Trois choses à garder, et aucune ne se voit dans le texte d'un test
    de gabarit : elles vivent dans la STRUCTURE du message.

    Éprouvé dans les deux sens : sans `_joindre_le_logo`, le premier cas rougit ;
    sans `_joindre_les_pieces`, le deuxième ; en rendant le tableau sans
    échappement, le dernier.
    """

    def _envoyer(self, code: str = "proforma.envoi.detaillee", **surcharges):
        transport = TransportEspion()
        parti = _service(transport).envoyer(
            code,
            destinataire="cliente@exemple.cm",
            contexte={**CONTEXTES_DES_APPELANTS[code], **surcharges},
        )
        assert parti is True
        (message,) = transport.remis
        return message

    def test_le_logo_voyage_dans_le_corps_et_non_en_piece_jointe(self):
        message = self._envoyer("compte.activation")
        html = message.get_body(preferencelist=("html",))
        assert "cid:logo-cga" in html.get_content()

        images = [p for p in message.walk() if p.get_content_type() == "image/jpeg"]
        assert len(images) == 1
        assert images[0]["Content-ID"] == "<logo-cga>"
        # ⚠️ Rangé AVEC le corps : listé comme pièce jointe, la cliente verrait un
        # fichier image sous chaque message du cabinet.
        assert list(message.iter_attachments()) == []

    def test_la_proforma_part_avec_son_document(self):
        message = self._envoyer()

        (jointe,) = list(message.iter_attachments())
        assert jointe.get_filename() == "Proforma-PRO-2026-0001.pdf"
        assert jointe.get_content_type() == "application/pdf"
        assert jointe.get_content() == b"%PDF-1.4"

    def test_le_detail_est_dans_les_deux_corps_et_le_total_en_dernier(self):
        message = self._envoyer()
        texte = message.get_body(preferencelist=("plain",)).get_content()
        html = message.get_body(preferencelist=("html",)).get_content()

        for corps in (texte, html):
            assert "I. Procedure au cfce" in corps
            assert corps.index("I. Procedure au cfce") < corps.index("Total général")
        assert "505 000 FCFA" in html

    def test_une_proforma_sans_document_n_annonce_ni_detail_ni_piece(self):
        message = self._envoyer("proforma.envoi")

        assert list(message.iter_attachments()) == []
        html = message.get_body(preferencelist=("html",)).get_content()
        assert "joint à ce message" not in html

    def test_une_piece_mal_formee_n_empeche_pas_le_message_de_partir(self):
        message = self._envoyer(pieces_jointes=[("sans-contenu.pdf",), "n'importe quoi"])
        assert list(message.iter_attachments()) == []

    def test_une_designation_ne_peut_pas_injecter_de_balise(self):
        """Les intitulés viennent du référentiel, et les lignes peuvent être
        retouchées par le responsable : ce qui s'y écrit n'est pas du HTML."""
        message = self._envoyer(
            detail=[("<script>alert(1)</script>", "1 FCFA"), ("Total général", "1 FCFA")]
        )
        html = message.get_body(preferencelist=("html",)).get_content()
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
