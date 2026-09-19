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
        "par": "Patricia MOUKOURI",
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
            "adresse_publique_site": "https://www.cga-brcg.cm",
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
