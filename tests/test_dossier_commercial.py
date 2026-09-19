"""Le dossier commercial : le graphe, les faits, et la veille.

Trois familles de tests, et elles ne se recouvrent pas :

* **le graphe** — ce qui est permis depuis chaque état, et surtout ce qui ne
  l'est pas. Vérifié exhaustivement plutôt que par échantillon : une arête
  ajoutée par distraction ne doit pas passer inaperçue ;
* **les faits** — les dates qui ne se recalculent pas. `premier_echange_le`
  mesure la réactivité du cabinet, `affecte_le` sa prise en charge ; les
  laisser glisser rendrait les deux indicateurs faux sans rien casser ;
* **la veille** — dont les délais viennent d'ailleurs, et dont ces tests
  vérifient qu'ils viennent bien d'ailleurs.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.contextes.souscription.domaine.demande_de_contact import (
    Canal,
    Consentement,
    DemandeDeContact,
)
from app.contextes.souscription.domaine.dossier_commercial import (
    ETATS_NOMINAUX,
    REAFFECTATIONS_MAXIMALES,
    TRANSITIONS,
    DossierCommercial,
    EtatDossier,
    TransitionDossierRefusee,
    ouvrir_un_dossier,
)

LE_JOUR = datetime(2026, 9, 9, 10, 0)


def _demande(deposee_le: datetime = LE_JOUR) -> DemandeDeContact:
    return DemandeDeContact(
        identifiant="dc-0001",
        deposee_le=deposee_le,
        nom="Abena Ndzana",
        telephone="699112233",
        service_souhaite="creation-sarl",
        canal_prefere=Canal.WHATSAPP,
        consentement=Consentement(
            accorde=True,
            recueilli_le=deposee_le,
            version_du_texte="consentement-whatsapp-v1",
        ),
    )


def _dossier() -> DossierCommercial:
    return ouvrir_un_dossier("dos-0001", _demande())


def _a(etat: EtatDossier) -> DossierCommercial:
    """Un dossier amené à l'état voulu en suivant le chemin nominal.

    Chaque étape avance d'une heure, pour que les dates restent distinctes : deux
    transitions au même instant rendraient indécidable un test qui compare
    `depuis_le` à ce qui précède.
    """
    dossier = _dossier()
    for rang, cible in enumerate(ETATS_NOMINAUX[1:], start=1):
        if dossier.etat is etat:
            return dossier
        instant = LE_JOUR + timedelta(hours=rang)
        if cible is EtatDossier.AFFECTEE:
            dossier = dossier.affecter("resp-1", instant, motif="proximité")
        elif cible is EtatDossier.EN_CONVERSATION:
            dossier = dossier.premier_contact(instant)
        elif cible is EtatDossier.QUALIFIEE:
            dossier = dossier.qualifier(instant)
        elif cible is EtatDossier.CHIFFREE:
            dossier = dossier.chiffrer(instant)
        elif cible is EtatDossier.PROFORMA_EMISE:
            dossier = dossier.emettre_la_proforma(instant)
        elif cible is EtatDossier.ACCEPTEE:
            dossier = dossier.accepter(instant)
        else:
            dossier = dossier.encaisser(instant)
    assert dossier.etat is etat, f"chemin nominal introuvable vers {etat}"
    return dossier


# ── L'ouverture ───────────────────────────────────────────────────────────────


class TestOuverture:
    def test_un_dossier_naît_depose_et_sans_responsable(self):
        dossier = _dossier()
        assert dossier.etat is EtatDossier.DEPOSEE
        assert dossier.responsable is None
        assert dossier.ouvert is True

    def test_il_est_date_de_la_demande_et_non_de_l_instant_present(self):
        """Un incident de traitement remettrait sinon toutes les horloges à zéro,
        et l'alerte qui devait signaler le retard le masquerait."""
        veille = _demande(deposee_le=datetime(2026, 9, 8, 18, 0))
        assert ouvrir_un_dossier("dos-1", veille).depuis_le == veille.deposee_le

    def test_il_porte_la_demande_sans_la_modifier(self):
        dossier = _a(EtatDossier.PAYEE)
        assert dossier.demande == _demande()


# ── Le graphe ─────────────────────────────────────────────────────────────────


class TestLeGraphe:
    """Vérifié exhaustivement : une arête ajoutée par distraction se voit ici."""

    def test_chaque_etat_declare_ses_passages(self):
        assert set(TRANSITIONS) == set(EtatDossier)

    def test_aucune_cible_n_est_inconnue(self):
        for depart, cibles in TRANSITIONS.items():
            for cible in cibles:
                assert cible in EtatDossier, f"{depart} pointe vers {cible}"

    @pytest.mark.parametrize("terminal", [EtatDossier.PAYEE, EtatDossier.SANS_SUITE])
    def test_les_deux_etats_terminaux_ne_repartent_nulle_part(self, terminal):
        assert TRANSITIONS[terminal] == frozenset()

    def test_on_ne_classe_pas_sans_suite_ce_qui_a_ete_accepte(self):
        """La seule différence notable entre ACCEPTÉE et ce qui la précède : un
        client engagé, le cabinet lui redemande, il ne l'oublie pas."""
        assert EtatDossier.SANS_SUITE not in TRANSITIONS[EtatDossier.ACCEPTEE]
        with pytest.raises(TransitionDossierRefusee):
            _a(EtatDossier.ACCEPTEE).classer_sans_suite(LE_JOUR, motif="injoignable")

    def test_tous_les_etats_nominaux_sont_atteignables_par_le_chemin(self):
        for etat in ETATS_NOMINAUX:
            assert _a(etat).etat is etat

    @pytest.mark.parametrize(
        "etat",
        [e for e in EtatDossier if e is not EtatDossier.PAYEE],
    )
    def test_seul_le_paiement_mene_a_paye(self, etat):
        if etat is EtatDossier.ACCEPTEE:
            return
        assert EtatDossier.PAYEE not in TRANSITIONS[etat]

    def test_un_passage_refuse_dit_ce_qui_etait_permis(self):
        """Un message qui se contente de refuser oblige à lire le graphe pour
        comprendre. Celui-ci le montre."""
        with pytest.raises(TransitionDossierRefusee) as echec:
            _dossier().qualifier(LE_JOUR)
        message = str(echec.value)
        assert "DEPOSEE" in message
        assert "AFFECTEE" in message

    def test_on_ne_saute_pas_l_affectation(self):
        with pytest.raises(TransitionDossierRefusee):
            _dossier().premier_contact(LE_JOUR)

    def test_on_ne_chiffre_pas_avant_d_avoir_qualifie(self):
        with pytest.raises(TransitionDossierRefusee):
            _a(EtatDossier.EN_CONVERSATION).chiffrer(LE_JOUR)

    def test_rien_ne_repart_d_un_dossier_paye(self):
        paye = _a(EtatDossier.PAYEE)
        with pytest.raises(TransitionDossierRefusee):
            paye.retour_en_conversation(LE_JOUR + timedelta(days=1))

    def test_un_dossier_classe_ne_se_rouvre_pas(self):
        """Un client qui revient dépose une nouvelle demande. Rouvrir
        demanderait de décider chez quel responsable, avec quelle ancienneté et
        sous quelle veille, et aucune des trois réponses n'est évidente."""
        classe = _dossier().classer_sans_suite(LE_JOUR, motif="hors périmètre")
        with pytest.raises(TransitionDossierRefusee):
            classe.affecter("resp-2", LE_JOUR, motif="reprise")


class TestRetourEnConversation:
    @pytest.mark.parametrize(
        "depuis",
        [EtatDossier.QUALIFIEE, EtatDossier.CHIFFREE, EtatDossier.PROFORMA_EMISE],
    )
    def test_on_redescend_depuis_les_etats_de_travail(self, depuis):
        dossier = _a(depuis).retour_en_conversation(LE_JOUR + timedelta(days=1))
        assert dossier.etat is EtatDossier.EN_CONVERSATION

    def test_une_proforma_acceptee_impayee_redescend(self):
        """Trente jours, et le dossier redevient une conversation plutôt qu'une
        ligne morte."""
        dossier = _a(EtatDossier.ACCEPTEE).retour_en_conversation(
            LE_JOUR + timedelta(days=30)
        )
        assert dossier.etat is EtatDossier.EN_CONVERSATION

    def test_les_faits_ne_sont_pas_retouches(self):
        """Le dossier redescend, le cabinet ne redevient pas réactif pour
        autant."""
        avant = _a(EtatDossier.CHIFFREE)
        apres = avant.retour_en_conversation(LE_JOUR + timedelta(days=1))
        assert apres.premier_echange_le == avant.premier_echange_le
        assert apres.affecte_le == avant.affecte_le
        assert apres.reaffectations == avant.reaffectations


# ── Les faits ─────────────────────────────────────────────────────────────────


class TestAffectation:
    def test_elle_pose_le_responsable_et_son_motif(self):
        """Le motif est conservé parce que la règle d'affectation vit au
        référentiel et changera : sans lui, on ne saura pas selon quelle version
        un dossier a été routé."""
        dossier = _dossier().affecter("resp-1", LE_JOUR, motif="agence-douala")
        assert dossier.responsable == "resp-1"
        assert dossier.motif_affectation == "agence-douala"
        assert dossier.affecte_le == LE_JOUR

    def test_la_reaffectation_ne_remet_pas_la_prise_en_charge_a_zero(self):
        """C'est précisément le retard qu'on cherche à mesurer."""
        affecte = _dossier().affecter("resp-1", LE_JOUR, motif="proximité")
        plus_tard = LE_JOUR + timedelta(hours=24)
        repris = affecte.reaffecter("resp-2", plus_tard, motif="sans contact")
        assert repris.affecte_le == LE_JOUR
        assert repris.depuis_le == plus_tard
        assert repris.responsable == "resp-2"
        assert repris.reaffectations == 1

    def test_on_ne_reaffecte_pas_au_meme(self):
        """Cela consommerait un tour de la limite sans que personne de nouveau
        ne soit prévenu."""
        affecte = _dossier().affecter("resp-1", LE_JOUR, motif="proximité")
        with pytest.raises(TransitionDossierRefusee):
            affecte.reaffecter("resp-1", LE_JOUR, motif="sans contact")

    def test_la_reaffectation_a_une_limite(self):
        """Une boucle qui se rejoue indéfiniment ne se voit pas : elle se
        découvre au moment où l'on cherche pourquoi personne n'a rappelé."""
        dossier = _dossier().affecter("resp-0", LE_JOUR, motif="proximité")
        for tour in range(REAFFECTATIONS_MAXIMALES):
            dossier = dossier.reaffecter(
                f"resp-{tour + 1}", LE_JOUR, motif="sans contact"
            )
        assert dossier.reaffectations == REAFFECTATIONS_MAXIMALES
        with pytest.raises(TransitionDossierRefusee) as echec:
            dossier.reaffecter("resp-final", LE_JOUR, motif="sans contact")
        assert "arbitrage humain" in str(echec.value)


class TestPremierContact:
    def test_il_pose_la_date_de_reactivite(self):
        affecte = _dossier().affecter("resp-1", LE_JOUR, motif="proximité")
        instant = LE_JOUR + timedelta(hours=3)
        assert affecte.premier_contact(instant).premier_echange_le == instant

    def test_le_deuxieme_message_n_est_pas_un_deuxieme_premier_contact(self):
        """Sans cette garde, l'indicateur mesurerait le dernier échange, ce qui
        est exactement l'inverse de ce qu'on lui demande."""
        en_cours = _a(EtatDossier.EN_CONVERSATION)
        rejoue = en_cours.premier_contact(LE_JOUR + timedelta(days=2))
        assert rejoue.premier_echange_le == en_cours.premier_echange_le
        assert rejoue is en_cours

    def test_il_survit_a_un_retour_en_conversation(self):
        dossier = _a(EtatDossier.QUALIFIEE)
        repris = dossier.retour_en_conversation(LE_JOUR + timedelta(days=5))
        encore = repris.premier_contact(LE_JOUR + timedelta(days=5))
        assert encore.premier_echange_le == dossier.premier_echange_le


class TestEncaissement:
    def test_il_est_terminal_et_date(self):
        instant = LE_JOUR + timedelta(days=2)
        paye = _a(EtatDossier.ACCEPTEE).encaisser(instant)
        assert paye.etat is EtatDossier.PAYEE
        assert paye.payee_le == instant
        assert paye.ouvert is False

    def test_il_est_rejouable_sans_repousser_la_date(self):
        """`payee_le` déclenche l'ouverture du tenant : la repousser ferait
        repartir un provisionnement déjà fait."""
        paye = _a(EtatDossier.PAYEE)
        rejoue = paye.encaisser(LE_JOUR + timedelta(days=9))
        assert rejoue is paye


class TestClassementSansSuite:
    def test_il_conserve_le_motif_et_la_date(self):
        classe = _dossier().classer_sans_suite(LE_JOUR, motif="non pertinente")
        assert classe.etat is EtatDossier.SANS_SUITE
        assert classe.motif_cloture == "non pertinente"
        assert classe.clos_le == LE_JOUR
        assert classe.ouvert is False

    def test_le_rejeu_ne_reecrit_pas_la_raison_d_un_humain(self):
        classe = _dossier().classer_sans_suite(LE_JOUR, motif="non pertinente")
        rejoue = classe.classer_sans_suite(LE_JOUR + timedelta(days=1), motif="lot")
        assert rejoue is classe
        assert rejoue.motif_cloture == "non pertinente"

    @pytest.mark.parametrize(
        "depuis",
        [
            EtatDossier.DEPOSEE,
            EtatDossier.AFFECTEE,
            EtatDossier.EN_CONVERSATION,
            EtatDossier.QUALIFIEE,
            EtatDossier.CHIFFREE,
            EtatDossier.PROFORMA_EMISE,
        ],
    )
    def test_il_est_possible_partout_avant_l_acceptation(self, depuis):
        assert _a(depuis).classer_sans_suite(LE_JOUR, motif="m").ouvert is False


class TestRattachement:
    """La garde du domaine, éprouvée ici et non depuis le cas d'usage.

    Le cas d'usage écarte déjà les dossiers fermés avant d'appeler `rattacher`.
    Ses tests ne peuvent donc pas dire si la garde du domaine existe : ils
    passaient à l'identique en la retirant, ce qui s'est vu en la retirant. Un
    garde-fou se teste à l'endroit où il est posé, jamais depuis l'étage qui le
    rend inutile.
    """

    def test_une_seconde_demande_rejoint_le_dossier(self):
        seconde = _demande(LE_JOUR + timedelta(hours=2)).model_copy(
            update={"identifiant": "dc-0002"}
        )
        dossier = _dossier().rattacher(seconde)
        assert len(dossier.toutes_les_demandes) == 2
        assert dossier.derniere_demande is seconde

    def test_l_etat_et_la_veille_ne_bougent_pas(self):
        """Un client qui renvoie son formulaire ne fait pas repartir le délai
        dont dispose le cabinet pour le rappeler."""
        avant = _a(EtatDossier.AFFECTEE)
        apres = avant.rattacher(
            _demande(LE_JOUR + timedelta(hours=6)).model_copy(
                update={"identifiant": "dc-0002"}
            )
        )
        assert apres.etat is avant.etat
        assert apres.depuis_le == avant.depuis_le

    def test_la_meme_demande_deux_fois_ne_compte_qu_une(self):
        """Le rejeu d'un lot ne doit pas dupliquer un dépôt déjà rangé."""
        seconde = _demande(LE_JOUR + timedelta(hours=2)).model_copy(
            update={"identifiant": "dc-0002"}
        )
        une_fois = _dossier().rattacher(seconde)
        deux_fois = une_fois.rattacher(seconde)
        assert deux_fois is une_fois

    @pytest.mark.parametrize("ferme", [EtatDossier.PAYEE, EtatDossier.SANS_SUITE])
    def test_un_dossier_ferme_n_accueille_rien(self, ferme):
        dossier = (
            _a(EtatDossier.PAYEE)
            if ferme is EtatDossier.PAYEE
            else _dossier().classer_sans_suite(LE_JOUR, motif="m")
        )
        with pytest.raises(TransitionDossierRefusee) as echec:
            dossier.rattacher(
                _demande(LE_JOUR + timedelta(hours=1)).model_copy(
                    update={"identifiant": "dc-0002"}
                )
            )
        assert "nouvelle intention" in str(echec.value)

    def test_la_demande_d_origine_reste_la_premiere(self):
        """`toutes_les_demandes` est un ordre d'arrivée, pas un ensemble.
        L'inverser ferait afficher le premier message comme le dernier."""
        dossier = _dossier()
        for rang in (2, 3):
            dossier = dossier.rattacher(
                _demande(LE_JOUR + timedelta(hours=rang)).model_copy(
                    update={"identifiant": f"dc-000{rang}"}
                )
            )
        assert [d.identifiant for d in dossier.toutes_les_demandes] == [
            "dc-0001",
            "dc-0002",
            "dc-0003",
        ]


# ── L'avancement ──────────────────────────────────────────────────────────────


class TestAvancement:
    def test_il_suit_le_rang_dans_le_chemin_nominal(self):
        assert _dossier().avancement == 0
        assert _a(EtatDossier.CHIFFREE).avancement == 4
        assert _a(EtatDossier.PAYEE).avancement == 7

    def test_hors_du_chemin_il_vaut_moins_un(self):
        """Un entier plutôt qu'un pourcentage : annoncer « 62 % » sur un dossier
        non chiffré serait une invention."""
        assert _dossier().classer_sans_suite(LE_JOUR, motif="m").avancement == -1


# ── La veille ─────────────────────────────────────────────────────────────────


class TestVeille:
    """Les délais viennent du référentiel. Ces tests vérifient qu'ils en
    viennent, c'est-à-dire qu'aucun nombre n'est écrit dans le domaine."""

    def test_aucun_delai_n_est_ecrit_dans_le_module(self):
        """Le garde-fou le plus utile du lot : il tombe le jour où quelqu'un
        remet « deux heures » en dur pour aller plus vite."""
        from pathlib import Path

        import app.contextes.souscription.domaine.dossier_commercial as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        code = [
            ligne
            for ligne in source.splitlines()
            if "timedelta(" in ligne and not ligne.lstrip().startswith("#")
        ]
        assert not code, f"un délai est écrit dans le domaine : {code}"

    def test_un_dossier_reste_trop_longtemps_dans_son_etat(self):
        dossier = _dossier()
        delais = {EtatDossier.DEPOSEE: timedelta(hours=2)}
        assert dossier.en_souffrance(LE_JOUR + timedelta(hours=1), delais) is False
        assert dossier.en_souffrance(LE_JOUR + timedelta(hours=3), delais) is True

    def test_le_delai_court_depuis_l_entree_dans_l_etat(self):
        """Et non depuis le dépôt : un dossier qui avance ne doit pas déclencher
        l'alerte de l'étape qu'il a quittée.

        ⚠️ L'instant choisi n'est pas quelconque. Il tombe **entre** les deux
        échéances possibles : vingt-quatre heures après le dépôt, mais seulement
        vingt-trois après l'affectation. Une vérification prise plus loin dans le
        temps donnerait le même verdict des deux côtés et ne prouverait rien.

        La première version de ce test mesurait à vingt heures puis vingt-six, et
        laissait passer un domaine qui comptait depuis `demande.deposee_le`. Le
        défaut ne s'est vu qu'en cassant volontairement le code pour voir si le
        test le remarquait. Il ne le remarquait pas.
        """
        dossier = _dossier().affecter(
            "resp-1", LE_JOUR + timedelta(hours=1), motif="proximité"
        )
        delais = {EtatDossier.AFFECTEE: timedelta(hours=24)}
        entre_les_deux = LE_JOUR + timedelta(hours=24, minutes=30)
        assert dossier.depuis_le == LE_JOUR + timedelta(hours=1)
        assert entre_les_deux - dossier.demande.deposee_le > delais[EtatDossier.AFFECTEE]
        assert dossier.en_souffrance(entre_les_deux, delais) is False
        assert (
            dossier.en_souffrance(LE_JOUR + timedelta(hours=26), delais) is True
        )

    def test_un_etat_absent_de_la_table_n_est_pas_sous_veille(self):
        """QUALIFIÉE ne l'est pas : le chiffrage est immédiat, et une alerte sur
        un état traversé en deux secondes serait du bruit."""
        assert _a(EtatDossier.QUALIFIEE).en_souffrance(
            LE_JOUR + timedelta(days=365), {}
        ) is False

    @pytest.mark.parametrize(
        "ferme",
        [EtatDossier.PAYEE, EtatDossier.SANS_SUITE],
    )
    def test_un_dossier_ferme_n_est_jamais_en_souffrance(self, ferme):
        """Sans cette garde, tous les dossiers payés de l'année remonteraient le
        jour où l'on ajoute un délai sur PAYÉE par erreur."""
        dossier = (
            _a(EtatDossier.PAYEE)
            if ferme is EtatDossier.PAYEE
            else _dossier().classer_sans_suite(LE_JOUR, motif="m")
        )
        delais = {ferme: timedelta(seconds=1)}
        assert dossier.en_souffrance(LE_JOUR + timedelta(days=400), delais) is False

    def test_changer_le_delai_ne_demande_pas_de_toucher_au_code(self):
        """La démonstration littérale du principe : deux tables, deux verdicts,
        le même dossier et la même ligne de code."""
        dossier = _dossier()
        instant = LE_JOUR + timedelta(hours=5)
        assert dossier.en_souffrance(instant, {EtatDossier.DEPOSEE: timedelta(hours=2)})
        assert not dossier.en_souffrance(
            instant, {EtatDossier.DEPOSEE: timedelta(hours=8)}
        )


class TestLeMarqueurDeSignalement:
    """`signaler`, `a_signaler`, et l'effacement par une transition.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **CES CAS MESURENT LE CONTRAT DE LA MÉTHODE, PAS SON EMPLOI DU JOUR**

    L'idempotence de `signaler` n'est atteinte par aucun appelant : la veille
    vérifie `signale_le is None` avant d'appeler. Une mutation qui supprime la
    garde survit donc à tous les tests de la veille, et c'est normal.

    Elle est gardée ici plutôt que retirée, et la raison est nommée : `signale_le`
    répond à « depuis quand quelqu'un est censé savoir ». C'est cette date que la
    console affiche pour distinguer une alerte fraîche d'une alerte que personne
    n'a traitée depuis trois semaines. Un second appel qui l'écraserait ferait
    paraître neuve une alerte ancienne, ce qui est exactement l'inverse de ce
    qu'elle sert à montrer.

    `classer_sans_suite` porte la même garde pour la même raison, et elle, un
    appelant l'atteint : la route peut être appelée deux fois.
    ─────────────────────────────────────────────────────────────────────────────
    """

    DELAIS = {EtatDossier.DEPOSEE: timedelta(hours=8)}

    def test_un_dossier_neuf_n_est_pas_signale(self):
        assert _dossier().signale_le is None

    def test_a_signaler_exige_les_deux_conditions(self):
        """En souffrance **et** pas encore signalé.

        ⚠️ Confondre `a_signaler` et `en_souffrance` ferait déposer la même alerte
        à chaque passage : sur un dossier oublié six mois, plus de dix-sept mille
        alertes pour un seul fait.
        """
        tard = LE_JOUR + timedelta(hours=9)
        frais = _dossier()

        assert frais.en_souffrance(tard, self.DELAIS) is True
        assert frais.a_signaler(tard, self.DELAIS) is True

        dit = frais.signaler(tard)
        # Il dort toujours : c'est un fait, pas une opinion.
        assert dit.en_souffrance(tard, self.DELAIS) is True
        # Mais il n'y a plus rien à dire.
        assert dit.a_signaler(tard, self.DELAIS) is False

    def test_signaler_conserve_la_date_du_premier_signalement(self):
        """⚠️ Le cas qui garde une mutation qu'aucun appelant n'atteint.

        Voir l'en-tête de la classe : c'est cette date qui dit depuis quand
        quelqu'un est censé savoir, et l'écraser ferait paraître neuve une alerte
        vieille de trois semaines.
        """
        premier = LE_JOUR + timedelta(hours=9)
        dit = _dossier().signaler(premier)
        redit = dit.signaler(premier + timedelta(days=21))

        assert redit.signale_le == premier

    def test_signaler_ne_change_ni_l_etat_ni_l_anciennete(self):
        """Signaler n'est pas agir.

        ⚠️ Faire avancer un dossier parce qu'on a prévenu quelqu'un remettrait
        `depuis_le` à l'instant du signalement : le dossier cesserait d'être en
        retard **du fait qu'on a dit qu'il l'était**, et la veille s'éteindrait
        elle-même.
        """
        tard = LE_JOUR + timedelta(hours=9)
        avant = _dossier()
        apres = avant.signaler(tard)

        assert apres.etat is avant.etat
        assert apres.depuis_le == avant.depuis_le

    def test_toute_transition_efface_le_marqueur(self):
        """Le marqueur suit l'attente, pas le dossier.

        ⚠️ Il est effacé dans `_passer_a`, et **là seulement** : c'est le seul
        chemin par lequel l'état change, donc le seul endroit où « l'attente
        recommence » est vrai. L'effacer dans chaque méthode donnerait neuf
        endroits où l'oublier.
        """
        tard = LE_JOUR + timedelta(hours=9)
        signale = _dossier().signaler(tard)
        assert signale.signale_le is not None

        assert signale.affecter("mballa", tard, motif="tour de rôle").signale_le is None
        assert signale.classer_sans_suite(tard, motif="prix").signale_le is None
