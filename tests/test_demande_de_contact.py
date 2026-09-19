"""La demande de contact : le premier objet du parcours d'acquisition.

Ces tests portent sur trois choses, et sur rien d'autre :

* **ce qu'un dépôt accepte et ce qu'il refuse**, en particulier les deux
  invariants de canal, qui existent pour transformer une panne d'envoi en une
  case à cocher ;
* **le consentement**, qui est daté, versionné et révocable, et dont la
  révocation ne doit rien effacer ;
* **la détection de doublon**, qui rapproche et ne rejette jamais.

Ils ne touchent ni base, ni HTTP, ni horloge : le domaine reçoit son instant.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from app.contextes.souscription.domaine.demande_de_contact import (
    FENETRE_ANTI_DOUBLON,
    ORIGINE_INCONNUE,
    Canal,
    Consentement,
    ConsentementInvalide,
    DemandeDeContact,
    doublon_parmi,
    normaliser_origine,
)

LE_JOUR = datetime(2026, 9, 9, 10, 0)


def _consentement(accorde: bool = True, **surcharges) -> Consentement:
    defauts = {
        "accorde": accorde,
        "recueilli_le": LE_JOUR,
        "version_du_texte": "consentement-whatsapp-v1",
    }
    return Consentement(**{**defauts, **surcharges})


def _demande(**surcharges) -> DemandeDeContact:
    defauts = {
        "identifiant": "dc-0001",
        "deposee_le": LE_JOUR,
        "nom": "Abena Ndzana",
        "telephone": "699112233",
        "service_souhaite": "creation-sarl",
        "canal_prefere": Canal.WHATSAPP,
        "consentement": _consentement(),
    }
    return DemandeDeContact(**{**defauts, **surcharges})


# ── Le dépôt ──────────────────────────────────────────────────────────────────


class TestCeQueLeDepotAccepte:
    def test_le_strict_necessaire_suffit(self):
        """Cinq champs et une case. Exiger davantage coûterait des visiteurs, et
        c'est le seul coût qu'on ne récupère jamais."""
        demande = _demande()
        assert demande.courriel is None
        assert demande.message is None
        assert demande.origine == ORIGINE_INCONNUE

    def test_le_numero_est_normalise_des_l_entree(self):
        """Six écritures du même numéro. Les conserver telles quelles ferait
        échouer le rapprochement de paiement et la détection de doublon, qui
        comparent des chaînes."""
        for ecriture in ("699112233", "+237699112233", "237 699 11 22 33", "00237699112233"):
            assert _demande(telephone=ecriture).telephone == "+237699112233"

    def test_un_numero_inexploitable_est_refuse(self):
        """Franchement, et au dépôt. Un numéro qu'on n'a pas su normaliser est un
        numéro sur lequel personne ne rappellera."""
        with pytest.raises(ValidationError):
            _demande(telephone="12345")

    def test_le_courriel_passe_en_minuscules(self):
        assert _demande(courriel="  Abena@Exemple.CM ").courriel == "abena@exemple.cm"

    def test_un_courriel_vide_vaut_absence(self):
        """Le formulaire envoie une chaîne vide, pas `null`. La conserver ferait
        croire à une adresse et produirait un envoi vers nulle part."""
        assert _demande(courriel="   ").courriel is None

    def test_le_champ_libre_est_borne(self):
        """Un formulaire ouvert sur internet est une porte à robots. Deux mille
        caractères décrivent un besoin ; deux cent mille remplissent une base."""
        _demande(message="a" * 2_000)
        with pytest.raises(ValidationError):
            _demande(message="a" * 2_001)

    def test_le_nom_est_elague(self):
        assert _demande(nom="  Abena Ndzana  ").nom == "Abena Ndzana"

    def test_un_nom_trop_court_est_refuse(self):
        with pytest.raises(ValidationError):
            _demande(nom="A")


class TestLesDeuxInvariantsDeCanal:
    """Les refuser au dépôt, où cela se corrige en cochant une case, plutôt qu'à
    l'envoi, où cela se découvre trois jours plus tard."""

    def test_whatsapp_sans_consentement_est_refuse(self):
        with pytest.raises(ValidationError) as echec:
            _demande(canal_prefere=Canal.WHATSAPP, consentement=_consentement(False))
        assert "consentement" in str(echec.value)

    def test_whatsapp_avec_un_consentement_revoque_est_refuse(self):
        """Le piège : `accorde` vaut toujours `True`. Seul `vaut_maintenant`
        tient compte de la révocation, et c'est lui que l'invariant consulte."""
        revoque = _consentement().revoquer(LE_JOUR, "désabonnement")
        assert revoque.accorde is True
        with pytest.raises(ValidationError):
            _demande(canal_prefere=Canal.WHATSAPP, consentement=revoque)

    def test_le_courriel_prefere_sans_adresse_est_refuse(self):
        with pytest.raises(ValidationError) as echec:
            _demande(canal_prefere=Canal.COURRIEL, consentement=_consentement(False))
        assert "adresse" in str(echec.value)

    def test_le_courriel_prefere_avec_adresse_passe(self):
        demande = _demande(
            canal_prefere=Canal.COURRIEL,
            courriel="abena@exemple.cm",
            consentement=_consentement(False),
        )
        assert demande.canal_prefere is Canal.COURRIEL

    def test_l_appel_ne_demande_ni_consentement_ni_adresse(self):
        """Le numéro suffit, et c'est pour cela qu'il est le canal de repli :
        aucun refus ne peut le rendre inaccessible."""
        demande = _demande(canal_prefere=Canal.APPEL, consentement=_consentement(False))
        assert demande.canal_prefere is Canal.APPEL


class TestLaDemandeEstFigee:
    def test_on_ne_reecrit_pas_ce_qui_a_ete_depose(self):
        """Ce que le responsable apprend ensuite se range dans la qualification.
        Corriger la demande ferait disparaître ce que le client avait écrit."""
        with pytest.raises(ValidationError):
            _demande().nom = "Autre"


# ── Le consentement ───────────────────────────────────────────────────────────


class TestConsentement:
    def test_accorde_et_non_revoque_vaut_maintenant(self):
        assert _consentement().vaut_maintenant is True

    def test_refuse_ne_vaut_pas(self):
        assert _consentement(False).vaut_maintenant is False

    def test_la_revocation_ne_l_efface_pas(self):
        """La preuve qu'il avait été donné pendant la période où l'on a écrit est
        exactement ce qu'on demanderait au cabinet de produire."""
        revoque = _consentement().revoquer(LE_JOUR, "désabonnement")
        assert revoque.accorde is True
        assert revoque.recueilli_le == LE_JOUR
        assert revoque.version_du_texte == "consentement-whatsapp-v1"
        assert revoque.vaut_maintenant is False

    def test_la_revocation_est_datee_et_motivee(self):
        plus_tard = LE_JOUR + timedelta(days=3)
        revoque = _consentement().revoquer(plus_tard, "STOP reçu")
        assert revoque.revoque_le == plus_tard
        assert revoque.motif_revocation == "STOP reçu"

    def test_revoquer_deux_fois_ne_repousse_pas_la_date(self):
        """Le prestataire renvoie ses notifications de désabonnement. La seconde
        ne doit pas écraser la première, qui est celle qui fait foi."""
        premiere = _consentement().revoquer(LE_JOUR, "STOP")
        seconde = premiere.revoquer(LE_JOUR + timedelta(days=1), "STOP encore")
        assert seconde is premiere

    def test_revoquer_ce_qui_n_a_jamais_ete_accorde_est_refuse(self):
        """Enregistrer une révocation laisserait croire qu'un accord avait
        existé, ce qui est le contraire de la vérité."""
        with pytest.raises(ConsentementInvalide):
            _consentement(False).revoquer(LE_JOUR, "STOP")

    def test_la_version_du_texte_est_obligatoire(self):
        """Un consentement sans rédaction identifiée ne prouve rien : on ne sait
        pas à quoi la personne a dit oui."""
        with pytest.raises(ValidationError):
            _consentement(version_du_texte="")


class TestLaDemandeSuitLaRevocation:
    def test_le_canal_retombe_sur_l_appel(self):
        """Laisser `WHATSAPP` violerait l'invariant du modèle, et l'objet
        deviendrait impossible à relire depuis la base."""
        demande = _demande().sans_consentement(LE_JOUR, "STOP")
        assert demande.canal_prefere is Canal.APPEL
        assert demande.joignable_sur_whatsapp is False

    def test_un_canal_qui_ne_depend_pas_du_consentement_ne_bouge_pas(self):
        origine = _demande(
            canal_prefere=Canal.COURRIEL,
            courriel="abena@exemple.cm",
        )
        apres = origine.sans_consentement(LE_JOUR, "STOP")
        assert apres.canal_prefere is Canal.COURRIEL

    def test_la_demande_reste_traitable(self):
        """Une révocation ne rend pas la demande intraitable : le numéro est
        toujours là, et c'est lui le canal de repli."""
        demande = _demande().sans_consentement(LE_JOUR, "STOP")
        assert demande.telephone == "+237699112233"
        assert demande.identifiant == "dc-0001"


# ── L'origine ─────────────────────────────────────────────────────────────────


class TestOrigine:
    @pytest.mark.parametrize(
        ("brut", "attendu"),
        [
            ("Facebook Ads / Mars", "facebook-ads-mars"),
            ("facebook-ads-mars", "facebook-ads-mars"),
            ("  PARRAINAGE  ", "parrainage"),
            ("salon__PME__2026", "salon-pme-2026"),
        ],
    )
    def test_deux_ecritures_de_la_meme_campagne_se_rejoignent(self, brut, attendu):
        """Les compter séparément rendrait le rapport des origines inutilisable,
        et c'est la seule chose qu'on demande à ce champ."""
        assert normaliser_origine(brut) == attendu

    @pytest.mark.parametrize("vide", [None, "", "   ", "///"])
    def test_l_absence_vaut_arrivee_directe(self, vide):
        assert normaliser_origine(vide) == ORIGINE_INCONNUE

    def test_une_origine_inconnue_n_est_pas_rejetee(self):
        """Perdre une demande parce que la campagne n'a pas été déclarée au
        référentiel serait absurde : c'est le commercial qui a oublié, pas le
        visiteur."""
        demande = _demande(origine="campagne-jamais-declaree")
        assert demande.origine == "campagne-jamais-declaree"

    def test_elle_est_bornee(self):
        assert len(normaliser_origine("x" * 200)) == 60


# ── Le doublon ────────────────────────────────────────────────────────────────


class TestDoublon:
    def test_le_meme_numero_dans_la_fenetre_est_un_doublon(self):
        premiere = _demande(identifiant="dc-1", deposee_le=LE_JOUR)
        seconde = _demande(
            identifiant="dc-2", deposee_le=LE_JOUR + timedelta(hours=2)
        )
        assert doublon_parmi(seconde, [premiere]) is premiere

    def test_hors_fenetre_ce_n_en_est_pas_un(self):
        """Un visiteur qui revient une semaine plus tard revient vraiment. Le
        rattacher à un fil refermé le ferait disparaître du tableau de bord."""
        premiere = _demande(identifiant="dc-1", deposee_le=LE_JOUR)
        seconde = _demande(
            identifiant="dc-2", deposee_le=LE_JOUR + timedelta(hours=25)
        )
        assert doublon_parmi(seconde, [premiere]) is None

    def test_la_fenetre_est_un_argument_et_non_une_constante(self):
        """Le centre voudra l'allonger après trois mois d'usage réel, et ce
        changement ne doit demander ni déploiement ni développeur."""
        premiere = _demande(identifiant="dc-1", deposee_le=LE_JOUR)
        seconde = _demande(
            identifiant="dc-2", deposee_le=LE_JOUR + timedelta(hours=25)
        )
        assert doublon_parmi(seconde, [premiere], fenetre=timedelta(days=7)) is premiere

    def test_un_autre_numero_n_en_est_pas_un(self):
        premiere = _demande(identifiant="dc-1", telephone="699112233")
        seconde = _demande(
            identifiant="dc-2",
            telephone="677889900",
            deposee_le=LE_JOUR + timedelta(hours=1),
        )
        assert doublon_parmi(seconde, [premiere]) is None

    def test_les_ecritures_differentes_du_meme_numero_se_rejoignent(self):
        """C'est tout l'intérêt de normaliser à l'entrée : `+237699112233` et
        `699 11 22 33` sont la même personne, et le sont pour la base aussi."""
        premiere = _demande(identifiant="dc-1", telephone="+237699112233")
        seconde = _demande(
            identifiant="dc-2",
            telephone="699 11 22 33",
            deposee_le=LE_JOUR + timedelta(minutes=5),
        )
        assert doublon_parmi(seconde, [premiere]) is premiere

    def test_un_service_different_reste_un_doublon(self):
        """Un visiteur qui hésite entre deux prestations n'a pas besoin de deux
        responsables : il en a besoin d'un, à qui l'on dit qu'il hésite."""
        premiere = _demande(identifiant="dc-1", service_souhaite="creation-sarl")
        seconde = _demande(
            identifiant="dc-2",
            service_souhaite="tenue-comptable",
            deposee_le=LE_JOUR + timedelta(hours=1),
        )
        assert doublon_parmi(seconde, [premiere]) is premiere

    def test_une_demande_ne_se_double_pas_elle_meme(self):
        """Le rapprochement se fait sur une liste relue depuis la base, qui
        contient déjà la demande enregistrée. Sans cette garde, chaque demande
        se rattacherait à elle-même."""
        seule = _demande(identifiant="dc-1")
        assert doublon_parmi(seule, [seule]) is None

    def test_la_plus_recente_l_emporte(self):
        """C'est le fil vivant, celui que le responsable a sous les yeux."""
        ancienne = _demande(identifiant="dc-1", deposee_le=LE_JOUR)
        recente = _demande(
            identifiant="dc-2", deposee_le=LE_JOUR + timedelta(hours=3)
        )
        nouvelle = _demande(
            identifiant="dc-3", deposee_le=LE_JOUR + timedelta(hours=5)
        )
        assert doublon_parmi(nouvelle, [ancienne, recente]) is recente

    def test_une_demande_anterieure_a_la_fenetre_ne_compte_pas_a_l_envers(self):
        """L'écart est signé. Sans la borne basse, une demande déposée **après**
        celle qu'on examine passerait pour un doublon, ce qui arrive dès qu'on
        rejoue un lot dans le désordre."""
        posterieure = _demande(
            identifiant="dc-2", deposee_le=LE_JOUR + timedelta(hours=2)
        )
        examinee = _demande(identifiant="dc-1", deposee_le=LE_JOUR)
        assert doublon_parmi(examinee, [posterieure]) is None

    def test_sans_historique_il_n_y_a_pas_de_doublon(self):
        assert doublon_parmi(_demande(), []) is None

    def test_la_valeur_par_defaut_est_de_vingt_quatre_heures(self):
        assert FENETRE_ANTI_DOUBLON == timedelta(hours=24)
