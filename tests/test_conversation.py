"""Le fil de conversation, et la fenêtre de service de vingt-quatre heures.

C'est le morceau du parcours dont les règles ne viennent pas de nous, et où une
erreur ne se voit qu'en production. Les tests visent donc trois choses :

* **la fenêtre**, ce qui l'ouvre et surtout ce qui ne l'ouvre pas ;
* **les refus locaux**, qui doivent tomber avant l'appel à la plateforme et non
  après. Un modèle non approuvé fonctionne dans le bac à sable : si le refus
  n'est pas ici, il n'existe nulle part avant le premier vrai client ;
* **le désordre**, parce que les accusés de la plateforme arrivent dans le
  désordre et que ses rappels sont rejoués.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.contextes.souscription.adaptateurs.sortant.catalogue_modeles import (
    CatalogueDeModeles,
    ModeleInconnu,
)
from app.contextes.souscription.domaine.conversation import (
    FENETRE_DE_SERVICE,
    AppelJournalise,
    CanalMessage,
    CategorieModele,
    Direction,
    EnvoiRefuse,
    Facturation,
    IssueAppel,
    Message,
    ModeleDeMessage,
    MotifRefus,
    StatutModele,
    StatutRemise,
    cout_de,
    ouvrir_un_fil,
)
from app.infrastructure.config import RACINE_DEPOT

T0 = datetime(2026, 9, 9, 10, 0)
CATALOGUE = RACINE_DEPOT / "Docs" / "referentiel" / "messagerie" / "modeles"


def _fil():
    return ouvrir_un_fil("dos-1", "+237699112233")


def _entrant(identifiant: str, a_l_instant: datetime, canal=CanalMessage.WHATSAPP):
    return Message(
        identifiant=identifiant,
        direction=Direction.ENTRANT,
        canal=canal,
        a_l_instant=a_l_instant,
        corps="bonjour",
    )


def _modele(**surcharges) -> ModeleDeMessage:
    defauts = {
        "nom": "cga_essai",
        "libelle": "Essai",
        "categorie": CategorieModele.UTILITAIRE,
        "statut": StatutModele.APPROUVE,
        "approuve_le": T0,
        "corps": "Bonjour {{1}}, au sujet de {{2}}.",
    }
    return ModeleDeMessage(**{**defauts, **surcharges})


# ── La fenêtre ────────────────────────────────────────────────────────────────


class TestFenetreDeService:
    def test_un_fil_neuf_a_la_fenetre_fermee(self):
        """Le cas de tout premier contact : le client n'a rien écrit. C'est
        pourquoi le premier message d'une relation part d'un modèle."""
        fil = _fil()
        assert fil.fenetre_ouverte(T0) is False
        assert fil.a_recu_du_client is False
        assert fil.expire_le() is None

    def test_un_message_entrant_l_ouvre(self):
        fil = _fil().avec(_entrant("m-1", T0))
        assert fil.fenetre_ouverte(T0 + timedelta(hours=1)) is True
        assert fil.expire_le() == T0 + FENETRE_DE_SERVICE

    def test_elle_se_referme_a_vingt_quatre_heures_pile(self):
        """La borne est stricte : à l'instant exact, la plateforme refuse déjà.
        Une comparaison large laisserait passer un envoi qui échouerait chez
        elle, pour une différence d'une microseconde."""
        fil = _fil().avec(_entrant("m-1", T0))
        assert fil.fenetre_ouverte(T0 + FENETRE_DE_SERVICE - timedelta(seconds=1))
        assert not fil.fenetre_ouverte(T0 + FENETRE_DE_SERVICE)

    def test_un_message_sortant_ne_l_ouvre_pas(self):
        """⚠️ La règle la plus contre-intuitive du chantier : **l'envoi d'un
        modèle ne rouvre pas la fenêtre.** C'est pour cela que les modèles sont
        rédigés pour appeler une réponse."""
        fil = _fil()
        envoi = fil.preparer_modele(
            "m-1", _modele(), ("Abena", "création"), T0, consentement_vaut=True
        )
        fil = fil.avec(envoi)
        assert fil.fenetre_ouverte(T0 + timedelta(minutes=1)) is False

    def test_un_courriel_entrant_ne_l_ouvre_pas(self):
        """La plateforme d'envoi ne voit pas les courriels. Compter un courriel
        comme ouvrant la fenêtre ferait tenter un message libre qu'elle
        refuserait."""
        fil = _fil().avec(_entrant("m-1", T0, canal=CanalMessage.COURRIEL))
        assert fil.fenetre_ouverte(T0 + timedelta(hours=1)) is False

    def test_le_dernier_entrant_l_emporte_sur_les_precedents(self):
        """Chaque réponse repart de zéro. Prendre le premier ferait refuser des
        envois parfaitement légitimes après un long échange."""
        fil = (
            _fil()
            .avec(_entrant("m-1", T0))
            .avec(_entrant("m-2", T0 + timedelta(hours=20)))
        )
        assert fil.fenetre_ouverte(T0 + timedelta(hours=30)) is True

    def test_les_entrants_dans_le_desordre_donnent_le_meme_resultat(self):
        """Les rappels arrivent dans le désordre. Prendre le dernier **posé**
        plutôt que le plus récent ferait dépendre la fenêtre de l'ordre des
        requêtes HTTP."""
        tot, tard = _entrant("m-1", T0), _entrant("m-2", T0 + timedelta(hours=20))
        a_l_endroit = _fil().avec(tot).avec(tard)
        a_l_envers = _fil().avec(tard).avec(tot)
        assert a_l_endroit.dernier_entrant == a_l_envers.dernier_entrant


# ── Le message libre ──────────────────────────────────────────────────────────


class TestMessageLibre:
    def test_il_passe_dans_la_fenetre_et_ne_coute_rien(self):
        """Répondre vite est gratuit. Le même échange repris trois jours plus
        tard se paie en modèles."""
        fil = _fil().avec(_entrant("m-1", T0))
        envoi = fil.preparer_message_libre(
            "m-2",
            "je vous rappelle",
            T0 + timedelta(hours=1),
            canal=CanalMessage.WHATSAPP,
            consentement_vaut=True,
        )
        assert envoi.libre is True
        assert envoi.facturation is Facturation.GRATUIT

    def test_il_est_refuse_hors_fenetre(self):
        """Le refus est **ici**, avant l'appel. La plateforme refuserait aussi,
        mais après un aller-retour, dans son vocabulaire, et le responsable
        verrait un échec technique là où il y a une règle à lui expliquer."""
        fil = _fil().avec(_entrant("m-1", T0))
        with pytest.raises(EnvoiRefuse) as echec:
            fil.preparer_message_libre(
                "m-2",
                "bonjour",
                T0 + timedelta(hours=30),
                canal=CanalMessage.WHATSAPP,
                consentement_vaut=True,
            )
        assert echec.value.motif is MotifRefus.HORS_FENETRE
        assert "modèle approuvé" in str(echec.value)

    def test_il_est_refuse_sur_un_fil_ou_le_client_n_a_jamais_ecrit(self):
        """Le piège nommé par le document : croire qu'on peut écrire librement à
        un numéro qui ne nous a jamais écrit."""
        with pytest.raises(EnvoiRefuse) as echec:
            _fil().preparer_message_libre(
                "m-1", "bonjour", T0, canal=CanalMessage.WHATSAPP, consentement_vaut=True
            )
        assert echec.value.motif is MotifRefus.HORS_FENETRE

    def test_il_est_refuse_sans_consentement(self):
        fil = _fil().avec(_entrant("m-1", T0))
        with pytest.raises(EnvoiRefuse) as echec:
            fil.preparer_message_libre(
                "m-2",
                "bonjour",
                T0 + timedelta(hours=1),
                canal=CanalMessage.WHATSAPP,
                consentement_vaut=False,
            )
        assert echec.value.motif is MotifRefus.SANS_CONSENTEMENT
        assert "L'appel et le courriel restent ouverts" in str(echec.value)

    def test_le_courriel_ne_connait_ni_fenetre_ni_consentement_de_messagerie(self):
        """Deux canaux, deux régimes. Appliquer la fenêtre au courriel
        interdirait d'écrire à un prospect qui n'a jamais répondu, ce qui est
        exactement ce que le courriel sert à faire."""
        envoi = _fil().preparer_message_libre(
            "m-1",
            "bonjour",
            T0,
            canal=CanalMessage.COURRIEL,
            consentement_vaut=False,
        )
        assert envoi.canal is CanalMessage.COURRIEL
        assert envoi.facturation is Facturation.GRATUIT


# ── Les modèles ───────────────────────────────────────────────────────────────


class TestModele:
    def test_le_nombre_de_parametres_est_le_plus_grand_numero_cite(self):
        """Et non le nombre de numéros distincts. Un corps qui cite {{1}} et
        {{3}} en attend trois, dont un inutilisé : la plateforme les compte
        ainsi."""
        assert _modele(corps="a {{1}} b {{3}}").parametres_attendus == 3
        assert _modele(corps="sans emplacement").parametres_attendus == 0

    def test_il_rend_son_corps_rempli(self):
        rendu = _modele().rendre(("Abena", "la création d'une SARL"))
        assert rendu == "Bonjour Abena, au sujet de la création d'une SARL."

    def test_un_compte_de_parametres_faux_est_refuse(self):
        """La plateforme le refuserait aussi, trois secondes plus tard, dans son
        vocabulaire, sur un envoi déjà compté par le quota."""
        with pytest.raises(EnvoiRefuse) as echec:
            _modele().rendre(("Abena",))
        assert echec.value.motif is MotifRefus.PARAMETRES_INCOMPLETS
        assert "2 paramètre(s) attendu(s), 1 fourni(s)" in str(echec.value)

    def test_un_modele_approuve_sans_date_est_refuse(self):
        """La date est ce qui permet de savoir si le texte envoyé est bien celui
        qui a été soumis, ou s'il a été retouché depuis."""
        with pytest.raises(ValueError, match="sans date d'approbation"):
            _modele(statut=StatutModele.APPROUVE, approuve_le=None)


class TestEnvoiDeModele:
    def test_il_passe_hors_fenetre(self):
        """C'est sa raison d'être : c'est le seul moyen d'écrire quand la fenêtre
        est fermée."""
        envoi = _fil().preparer_modele(
            "m-1", _modele(), ("Abena", "création"), T0, consentement_vaut=True
        )
        assert envoi.modele == "cga_essai"
        assert envoi.facturation is Facturation.FACTURE

    def test_il_est_gratuit_dans_la_fenetre(self):
        fil = _fil().avec(_entrant("m-1", T0))
        envoi = fil.preparer_modele(
            "m-2",
            _modele(),
            ("Abena", "création"),
            T0 + timedelta(hours=1),
            consentement_vaut=True,
        )
        assert envoi.facturation is Facturation.GRATUIT

    @pytest.mark.parametrize(
        "statut",
        [StatutModele.EN_ATTENTE, StatutModele.REFUSE, StatutModele.DESACTIVE],
    )
    def test_un_modele_non_approuve_est_refuse_localement(self, statut):
        """⚠️ **Le piège le plus coûteux du chantier.** Un modèle non approuvé
        fonctionne dans le bac à sable et échoue en production. Si le refus n'est
        pas ici, il n'existe nulle part avant le premier vrai client."""
        modele = _modele(statut=statut, approuve_le=None)
        with pytest.raises(EnvoiRefuse) as echec:
            _fil().preparer_modele(
                "m-1", modele, ("Abena", "création"), T0, consentement_vaut=True
            )
        assert echec.value.motif is MotifRefus.MODELE_NON_APPROUVE
        assert "bac à sable" in str(echec.value)

    def test_le_marketing_est_refuse(self):
        """Le centre ne démarche pas par ce canal. Le refus est explicite pour
        qu'il soit une décision lisible plutôt qu'une absence de fichier."""
        modele = _modele(categorie=CategorieModele.MARKETING)
        with pytest.raises(EnvoiRefuse) as echec:
            _fil().preparer_modele(
                "m-1", modele, ("Abena", "création"), T0, consentement_vaut=True
            )
        assert echec.value.motif is MotifRefus.CATEGORIE_HORS_PERIMETRE

    def test_il_est_refuse_sans_consentement(self):
        with pytest.raises(EnvoiRefuse) as echec:
            _fil().preparer_modele(
                "m-1", _modele(), ("Abena", "x"), T0, consentement_vaut=False
            )
        assert echec.value.motif is MotifRefus.SANS_CONSENTEMENT

    def test_les_parametres_sont_verifies_avant_l_envoi(self):
        with pytest.raises(EnvoiRefuse) as echec:
            _fil().preparer_modele(
                "m-1", _modele(), ("Abena",), T0, consentement_vaut=True
            )
        assert echec.value.motif is MotifRefus.PARAMETRES_INCOMPLETS

    def test_le_corps_rendu_est_conserve_dans_le_fil(self):
        """La plateforme rend son propre texte ; conserver le nôtre permet
        d'afficher le fil sans aller-retour, et de relire six mois plus tard ce
        qui avait été envoyé."""
        envoi = _fil().preparer_modele(
            "m-1", _modele(), ("Abena", "création"), T0, consentement_vaut=True
        )
        assert envoi.corps == "Bonjour Abena, au sujet de création."


# ── La facturation ────────────────────────────────────────────────────────────


class TestFacturation:
    """Le tableau du document, éprouvé case par case. Il reproduit la
    tarification de la plateforme, pas une décision du centre."""

    @pytest.mark.parametrize(
        ("categorie", "ouverte", "attendu"),
        [
            (None, True, Facturation.GRATUIT),
            (None, False, Facturation.IMPOSSIBLE),
            (CategorieModele.UTILITAIRE, True, Facturation.GRATUIT),
            (CategorieModele.UTILITAIRE, False, Facturation.FACTURE),
            (CategorieModele.AUTHENTIFICATION, True, Facturation.FACTURE),
            (CategorieModele.AUTHENTIFICATION, False, Facturation.FACTURE),
            (CategorieModele.MARKETING, True, Facturation.FACTURE),
            (CategorieModele.MARKETING, False, Facturation.FACTURE),
        ],
    )
    def test_le_tableau(self, categorie, ouverte, attendu):
        assert cout_de(categorie, ouverte) is attendu

    def test_le_fil_compte_ce_qu_il_a_coute(self):
        """Le compteur doit être visible dès le premier jour, et pas découvert
        sur la facture. Une relance automatique mal réglée coûte à chaque
        déclenchement."""
        fil = _fil()
        for rang in range(3):
            envoi = fil.preparer_modele(
                f"m-{rang}",
                _modele(),
                ("Abena", "création"),
                T0 + timedelta(days=rang),
                consentement_vaut=True,
            )
            fil = fil.avec(envoi)
        assert len(fil.envois_factures()) == 3

    def test_les_entrants_ne_comptent_pas(self):
        fil = _fil().avec(_entrant("m-1", T0))
        assert fil.envois_factures() == ()


# ── Le désordre et les rejeux ─────────────────────────────────────────────────


class TestStatutDeRemise:
    def test_il_progresse(self):
        message = _entrant("m-1", T0).avec_statut(StatutRemise.ENVOYE)
        assert message.avec_statut(StatutRemise.REMIS).statut is StatutRemise.REMIS

    def test_il_ne_recule_pas(self):
        """⚠️ Les accusés arrivent dans le désordre : « lu » peut précéder
        « remis », ce sont deux requêtes indépendantes. Assigner ferait reculer
        le message, et le tableau de bord annoncerait non lus des messages qui
        l'étaient."""
        lu = _entrant("m-1", T0).avec_statut(StatutRemise.LU)
        assert lu.avec_statut(StatutRemise.REMIS) is lu
        assert lu.statut is StatutRemise.LU

    def test_un_accuse_rejoue_laisse_l_objet_inchange(self):
        remis = _entrant("m-1", T0).avec_statut(StatutRemise.REMIS)
        assert remis.avec_statut(StatutRemise.REMIS) is remis

    def test_l_echec_l_emporte_sur_tout(self):
        """Un échec rapporté après un accusé de remise est une contradiction de
        la plateforme. Dans le doute il faut croire l'échec : un message annoncé
        remis qui ne l'était pas se découvre par un client qui n'a rien reçu."""
        lu = _entrant("m-1", T0).avec_statut(StatutRemise.LU)
        assert lu.avec_statut(StatutRemise.ECHEC).statut is StatutRemise.ECHEC

    def test_le_fil_fait_progresser_le_bon_message(self):
        fil = _fil().avec(_entrant("m-1", T0)).avec(_entrant("m-2", T0))
        apres = fil.avec_statut("m-2", StatutRemise.LU)
        assert apres.messages[0].statut is StatutRemise.EN_ATTENTE
        assert apres.messages[1].statut is StatutRemise.LU

    def test_un_accuse_pour_un_message_inconnu_ne_leve_pas(self):
        """Lever ferait échouer le rappel, que la plateforme rejouerait,
        indéfiniment."""
        fil = _fil().avec(_entrant("m-1", T0))
        assert fil.avec_statut("m-inconnu", StatutRemise.LU).messages[0].statut is (
            StatutRemise.EN_ATTENTE
        )


class TestRejeuDesRappels:
    def test_le_meme_message_deux_fois_ne_compte_qu_une(self):
        """Les rappels sont rejoués : c'est le mécanisme de reprise de la
        plateforme, pas une anomalie. Un entrant écrit deux fois apparaîtrait
        deux fois et, pire, prolongerait la fenêtre à chaque rejeu."""
        message = _entrant("m-1", T0)
        fil = _fil().avec(message)
        assert fil.avec(message) is fil
        assert len(fil.messages) == 1

    def test_le_meme_appel_deux_fois_ne_compte_qu_une(self):
        appel = AppelJournalise(
            identifiant="a-1", a_l_instant=T0, duree_secondes=180,
            issue=IssueAppel.REPONDU,
        )
        fil = _fil().avec_appel(appel)
        assert fil.avec_appel(appel) is fil


# ── L'appel ───────────────────────────────────────────────────────────────────


class TestAppel:
    def test_il_porte_sa_duree_et_son_issue(self):
        appel = AppelJournalise(
            identifiant="a-1", a_l_instant=T0, duree_secondes=180,
            issue=IssueAppel.REPONDU,
        )
        assert appel.duree_secondes == 180

    def test_il_ne_porte_pas_de_contenu(self):
        """Ce que le responsable retient d'un appel se saisit dans la
        qualification, en données typées. Un champ de notes libres ici produirait
        un fil inexploitable et une qualification vide."""
        champs = set(AppelJournalise.model_fields)
        assert champs == {"identifiant", "a_l_instant", "duree_secondes", "issue"}

    @pytest.mark.parametrize(
        "issue",
        [IssueAppel.SANS_REPONSE, IssueAppel.OCCUPE, IssueAppel.INJOIGNABLE],
    )
    def test_une_duree_sur_un_appel_non_repondu_est_refusee(self, issue):
        """Compter les sonneries comme du temps d'échange fausserait
        l'indicateur qui sert à mesurer la charge réelle."""
        with pytest.raises(ValueError, match="durée non nulle"):
            AppelJournalise(
                identifiant="a-1", a_l_instant=T0, duree_secondes=30, issue=issue
            )

    def test_un_appel_non_repondu_sans_duree_passe(self):
        appel = AppelJournalise(
            identifiant="a-1", a_l_instant=T0, issue=IssueAppel.SANS_REPONSE
        )
        assert appel.duree_secondes == 0


class TestDernierEchange:
    def test_il_compte_les_deux_sens_et_les_appels(self):
        """C'est ce que la veille des sept jours regarde. Prendre le seul dernier
        entrant ferait relancer un client à qui l'on vient d'écrire."""
        fil = _fil().avec(_entrant("m-1", T0))
        assert fil.dernier_echange == T0

        fil = fil.avec_appel(
            AppelJournalise(
                identifiant="a-1",
                a_l_instant=T0 + timedelta(days=2),
                issue=IssueAppel.SANS_REPONSE,
            )
        )
        assert fil.dernier_echange == T0 + timedelta(days=2)

    def test_un_fil_vide_n_a_pas_d_echange(self):
        assert _fil().dernier_echange is None


# ── Le catalogue réel ─────────────────────────────────────────────────────────


class TestCatalogueDuReferentiel:
    @pytest.fixture(scope="class")
    def catalogue(self):
        return CatalogueDeModeles.depuis(CATALOGUE)

    def test_les_sept_modeles_du_document_sont_la(self, catalogue):
        """Sept modèles à faire approuver, nommés par le document de conception.
        En oublier un se découvrirait au moment de s'en servir, hors fenêtre,
        devant un client qui attend."""
        assert {m.nom for m in catalogue.tous()} == {
            "cga_prise_de_contact",
            "cga_envoi_proforma",
            "cga_relance_proforma",
            "cga_rappel_echeance",
            "cga_relance_honoraires",
            "cga_lien_activation",
            "cga_piece_manquante",
        }

    def test_aucun_n_est_encore_approuve(self, catalogue):
        """État honnête : le compte de la plateforme n'est pas ouvert. Les
        déclarer approuvés ferait passer les tests et échouer la production, ce
        qui est exactement le défaut que ce chantier cherche à rendre
        impossible."""
        assert catalogue.envoyables() == []

    def test_aucun_n_est_du_marketing(self, catalogue):
        """Le centre ne démarche pas par ce canal."""
        assert not [
            m for m in catalogue.tous() if m.categorie is CategorieModele.MARKETING
        ]

    def test_le_lien_d_activation_est_de_categorie_authentification(self, catalogue):
        """Tarif et règles distincts : facturé même fenêtre ouverte. Le classer
        utilitaire ferait refuser l'envoi par la plateforme."""
        modele = catalogue.prendre("cga_lien_activation")
        assert modele.categorie is CategorieModele.AUTHENTIFICATION

    def test_chacun_declare_son_usage(self, catalogue):
        """Sept modèles se ressemblent vite. Sans usage écrit, on emploiera le
        mauvais, et l'erreur se lira chez le client."""
        for modele in catalogue.tous():
            assert modele.usage.strip(), modele.nom

    def test_chacun_porte_au_moins_un_emplacement(self, catalogue):
        """Un modèle sans emplacement est un message identique pour tout le
        monde. La plateforme les approuve moins volontiers, et le client le
        lit comme une circulaire."""
        for modele in catalogue.tous():
            assert modele.parametres_attendus >= 1, modele.nom

    def test_la_prise_de_contact_se_rend(self, catalogue):
        """Le premier message de toute relation. Un compte de paramètres faux ici
        bloquerait l'entrée du parcours pour tout le monde."""
        modele = catalogue.prendre("cga_prise_de_contact")
        rendu = modele.rendre(("Abena", "Marc Awono", "la création d'une SARL"))
        assert "Abena" in rendu
        assert "{{" not in rendu

    def test_un_modele_inconnu_leve(self, catalogue):
        """Rendre `None` ferait qu'un envoi disparaîtrait silencieusement, et la
        relance qu'on croyait partie ne serait jamais partie."""
        with pytest.raises(ModeleInconnu, match="Modèles connus"):
            catalogue.prendre("cga_jamais_ecrit")

    def test_le_catalogue_est_trie(self, catalogue):
        assert [m.nom for m in catalogue.tous()] == sorted(
            m.nom for m in catalogue.tous()
        )


class TestLaFenetreN_estPasConfigurable:
    def test_elle_vaut_vingt_quatre_heures_dans_le_code(self):
        assert FENETRE_DE_SERVICE == timedelta(hours=24)

    def test_aucun_fichier_du_referentiel_ne_la_porte(self):
        """⚠️ Le garde-fou qui protège une distinction, pas une valeur.

        La configuration est pour ce que **le centre décide**. La fenêtre lui est
        imposée. La mettre au référentiel ferait croire le contraire : quelqu'un
        la porterait à soixante-douze heures pour se laisser du temps, le système
        accepterait, et les envois échoueraient chez la plateforme sans que rien
        ici ne l'explique.

        Ce test tombe le jour où quelqu'un l'y met, et son message dit pourquoi.
        """
        referentiel = RACINE_DEPOT / "Docs" / "referentiel"
        suspects = []
        for fichier in sorted(referentiel.rglob("*.yaml")):
            texte = fichier.read_text(encoding="utf-8")
            if "fenetre_de_service" in texte or "fenetre_service" in texte:
                suspects.append(str(fichier.relative_to(referentiel)))
        assert not suspects, (
            f"la fenêtre de service apparaît au référentiel : {suspects}. Elle est "
            "imposée par la plateforme d'envoi, pas décidée par le centre. La "
            "rendre configurable invite à la changer, et les envois échoueraient "
            "sans qu'aucun message local ne l'explique."
        )


# ── Le fil et le dossier ──────────────────────────────────────────────────────


class TestLeFilFaitAvancerLeDossier:
    """Le lien entre deux objets qui s'ignorent volontairement.

    Cette ignorance est ce qui permettra au fil de servir un jour la relation
    avec un adhérent déjà client, qui n'a plus de dossier commercial du tout.
    """

    def _affecte(self):
        from app.contextes.souscription.domaine.demande_de_contact import (
            Canal,
            Consentement,
            DemandeDeContact,
        )
        from app.contextes.souscription.domaine.dossier_commercial import (
            ouvrir_un_dossier,
        )

        demande = DemandeDeContact(
            identifiant="dc-1",
            deposee_le=T0,
            nom="Abena Ndzana",
            telephone="699112233",
            service_souhaite="creation-sarl",
            canal_prefere=Canal.WHATSAPP,
            consentement=Consentement(
                accorde=True, recueilli_le=T0, version_du_texte="v1"
            ),
        )
        return ouvrir_un_dossier("dos-1", demande).affecter(
            "resp-1", T0, motif="proximité"
        )

    def test_un_envoi_ouvre_la_conversation(self):
        from app.contextes.souscription.application.conversation import (
            enregistrer_un_message,
        )
        from app.contextes.souscription.domaine.dossier_commercial import EtatDossier

        fil = _fil()
        envoi = fil.preparer_modele(
            "m-1", _modele(), ("Abena", "création"), T0, consentement_vaut=True
        )
        resultat = enregistrer_un_message(self._affecte(), fil, envoi, T0)

        assert resultat.a_ouvert_la_conversation is True
        assert resultat.dossier.etat is EtatDossier.EN_CONVERSATION
        assert resultat.dossier.premier_echange_le == T0
        assert len(resultat.fil.messages) == 1

    def test_un_appel_sans_reponse_ouvre_aussi_la_conversation(self):
        """`premier_echange_le` mesure la **réactivité du cabinet**, pas la
        disponibilité du client. Un responsable qui a appelé trois fois dans
        l'heure a fait son travail, et l'indicateur doit le dire."""
        from app.contextes.souscription.application.conversation import (
            enregistrer_un_appel,
        )
        from app.contextes.souscription.domaine.dossier_commercial import EtatDossier

        appel = AppelJournalise(
            identifiant="a-1", a_l_instant=T0, issue=IssueAppel.SANS_REPONSE
        )
        resultat = enregistrer_un_appel(self._affecte(), _fil(), appel, T0)
        assert resultat.dossier.etat is EtatDossier.EN_CONVERSATION

    def test_un_dossier_deja_en_conversation_ne_rebouge_pas(self):
        """Le deuxième message n'est pas un deuxième premier contact."""
        from app.contextes.souscription.application.conversation import (
            enregistrer_un_message,
        )

        premier = enregistrer_un_message(
            self._affecte(), _fil(), _entrant("m-1", T0), T0
        )
        second = enregistrer_un_message(
            premier.dossier,
            premier.fil,
            _entrant("m-2", T0 + timedelta(hours=2)),
            T0 + timedelta(hours=2),
        )
        assert second.a_ouvert_la_conversation is False
        assert second.dossier.premier_echange_le == T0
        assert len(second.fil.messages) == 2

    def test_un_dossier_non_affecte_enrichit_le_fil_sans_bouger(self):
        """Ce module ne force aucune transition que le domaine refuse, et ne
        rattrape aucune exception pour faire semblant."""
        from app.contextes.souscription.application.conversation import (
            enregistrer_un_message,
        )
        from app.contextes.souscription.domaine.demande_de_contact import (
            Canal,
            Consentement,
            DemandeDeContact,
        )
        from app.contextes.souscription.domaine.dossier_commercial import (
            EtatDossier,
            ouvrir_un_dossier,
        )

        demande = DemandeDeContact(
            identifiant="dc-1",
            deposee_le=T0,
            nom="Abena Ndzana",
            telephone="699112233",
            service_souhaite="creation-sarl",
            canal_prefere=Canal.WHATSAPP,
            consentement=Consentement(
                accorde=True, recueilli_le=T0, version_du_texte="v1"
            ),
        )
        depose = ouvrir_un_dossier("dos-1", demande)
        resultat = enregistrer_un_message(depose, _fil(), _entrant("m-1", T0), T0)

        assert resultat.dossier.etat is EtatDossier.DEPOSEE
        assert resultat.a_ouvert_la_conversation is False
        assert len(resultat.fil.messages) == 1
