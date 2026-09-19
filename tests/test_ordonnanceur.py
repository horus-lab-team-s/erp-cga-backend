"""L'ordonnanceur : ce qui est dû, à quel rythme, et ce qu'un échec change.

⚠️ **Aucun test de ce fichier n'attend, ne dort, ni ne lit l'horloge.** L'instant est
un argument, et c'est tout l'intérêt de la découpe : vérifier qu'un travail
quotidien passe bien coûte une soustraction, pas vingt-quatre heures.

CE QUI EST VÉRIFIÉ ICI, ET CE QUI NE PEUT PAS L'ÊTRE

La décision : quels travaux sont dus, quel recul après un échec, quand se
réveiller. Et le déroulement d'un tour : ce qu'un exécutant qui lève change, ce
qu'un travail sans exécutant produit.

L'exclusion entre deux instances ne peut pas être vérifiée ici : elle est le fait
de PostgreSQL, et se mesure à `test_verrou.py`, sur une vraie base.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from app.orchestration.ordonnanceur import (
    ECHECS_AVANT_ABANDON,
    RECUL_MAXIMAL,
    Passage,
    Travail,
    delai_avant_reprise,
    prochain_reveil,
    travaux_dus,
)
from app.orchestration.tour import faire_un_tour

MIDI = datetime(2026, 9, 10, 12, 0, 0)

RELAIS = Travail(nom="relais", cadence=timedelta(seconds=5), objet="vide la boîte d'envoi")
RELANCE = Travail(
    nom="relance", cadence=timedelta(hours=1), objet="balaie les proformas sans réponse"
)


def _passage(travail: str, *, fini: datetime | None = MIDI, echecs: int = 0) -> Passage:
    return Passage(travail=travail, termine_le=fini, echecs_consecutifs=echecs)


class TestCeQuiEstDu:
    def test_un_travail_jamais_passe_est_du_tout_de_suite(self):
        """Le premier démarrage doit être franc.

        Une installation neuve qui attendrait une heure avant son premier tour
        laisserait croire à une panne pendant une heure — et c'est justement le
        premier passage qui révèle les erreurs de configuration.
        """
        assert travaux_dus([RELANCE], {}, MIDI) == [RELANCE]

    def test_un_travail_qui_vient_de_passer_n_est_pas_du(self):
        assert travaux_dus([RELAIS], {"relais": _passage("relais")}, MIDI) == []

    def test_a_l_echeance_exacte_le_travail_est_du(self):
        """`>=` et non `>`.

        C'est le cas le plus naturel à écrire dans un test, et le plus fréquent en
        exploitation dès que la cadence divise l'intervalle de réveil. Un `>` ferait
        glisser chaque travail d'un intervalle entier, tous les tours.
        """
        passages = {"relais": _passage("relais")}
        assert travaux_dus([RELAIS], passages, MIDI + timedelta(seconds=5)) == [RELAIS]

    def test_l_ordre_de_declaration_est_l_ordre_d_execution(self):
        """Le relais avant la relance, et ce n'est pas cosmétique.

        ─────────────────────────────────────────────────────────────────────────
        ⚠️ **La justification de ce cas a d'abord été fausse**, et elle plaidait
        pour l'ordre inverse : « le balayage dépose ses envois dans la boîte, les
        déposer juste après un passage du relais les ferait attendre un tour
        entier ».

        La vraie raison est que le relais est le **chemin vital** : c'est lui qui
        ouvre les tenants payés, et un client qui règle attend que son sous-domaine
        réponde. Le faire passer derrière un balayage de centaines de proformas
        ajouterait la durée du parcours au délai d'ouverture.

        Et le coût invoqué n'existait pas : les envois du balayage attendent le
        passage suivant du **relais**, toutes les quelques secondes, et non celui du
        balayage, toutes les heures.

        Trier par nom mettrait « relance » avant « relais ».
        ─────────────────────────────────────────────────────────────────────────
        """
        dus = travaux_dus([RELAIS, RELANCE], {}, MIDI)
        assert [t.nom for t in dus] == ["relais", "relance"]

    def test_un_travail_abandonne_n_est_plus_du_quel_que_soit_le_temps_ecoule(self):
        """Vingt échecs d'affilée ont un défaut que le vingt-et-unième essai ne corrige pas.

        Continuer masquerait le problème derrière un journal qui défile, et
        consommerait la connexion qui sert aux travaux qui, eux, fonctionnent.
        """
        mort = _passage("relais", echecs=ECHECS_AVANT_ABANDON)
        assert travaux_dus([RELAIS], {"relais": mort}, MIDI + timedelta(days=30)) == []

    def test_un_arret_de_trois_jours_ne_produit_pas_soixante_douze_passages(self):
        """La règle qui distingue un ordonnanceur d'un compteur.

        ⚠️ Aucun travail de ce système n'est cumulatif. Le relais publie ce qui
        attend, quel qu'en soit l'âge. Le balayage regarde ce qui est dû maintenant.
        Rejouer un passage manqué ne produirait rien qu'une seconde relance au même
        client, ce qui est précisément ce qu'on cherche à éviter.
        """
        passages = {"relance": _passage("relance")}
        dus = travaux_dus([RELANCE], passages, MIDI + timedelta(days=3))
        assert dus == [RELANCE], "un seul passage, pas soixante-douze"


class TestLeReculApresUnEchec:
    def test_sans_echec_le_delai_est_la_cadence(self):
        assert delai_avant_reprise(RELAIS, _passage("relais")) == RELAIS.cadence

    def test_le_recul_croit_avec_les_echecs(self):
        """Un abonné injoignable martelé toutes les cinq secondes n'en guérit pas."""
        court = delai_avant_reprise(RELAIS, _passage("relais", echecs=4))
        long = delai_avant_reprise(RELAIS, _passage("relais", echecs=8))
        assert long > court

    def test_le_recul_est_plafonne(self):
        """Sans plafond, dix échecs sur une cadence horaire repousseraient la reprise
        à plus de quarante jours : le travail serait mort sans être déclaré mort."""
        assert delai_avant_reprise(RELAIS, _passage("relais", echecs=40)) == RECUL_MAXIMAL

    def test_le_recul_ne_descend_jamais_sous_la_cadence(self):
        """Un travail horaire qui échoue une fois ne doit pas repasser dans dix secondes.

        Le premier palier de recul vaut deux secondes ; l'appliquer tel quel ferait
        d'un unique échec le moyen d'accélérer un travail lent.
        """
        assert delai_avant_reprise(RELANCE, _passage("relance", echecs=1)) == RELANCE.cadence

    def test_un_succes_remet_le_compteur_a_zero_et_non_d_un_cran(self):
        """Décrémenter laisserait un travail qui alterne succès et échec reculer sans fin
        alors qu'il fonctionne une fois sur deux."""
        apres = _passage("relais", echecs=6).reussi(MIDI)
        assert apres.echecs_consecutifs == 0
        assert apres.dernier_echec is None

    def test_un_echec_avance_la_date_de_fin(self):
        """⚠️ Sinon le travail serait éternellement dû, donc retenté sans aucun recul,
        et tout le mécanisme de recul ne s'appliquerait jamais."""
        apres = _passage("relais", fini=None).echoue(MIDI, "base injoignable")
        assert apres.termine_le == MIDI
        assert apres.echecs_consecutifs == 1


class TestLeProchainReveil:
    def test_il_rend_le_delai_du_travail_le_plus_proche(self):
        """Le plus vif commande, sinon sa cadence devient celle de l'intervalle."""
        passages = {"relais": _passage("relais"), "relance": _passage("relance")}
        assert prochain_reveil([RELAIS, RELANCE], passages, MIDI) == RELAIS.cadence

    def test_un_travail_du_maintenant_rend_le_plancher_et_jamais_zero(self):
        """⚠️ Zéro ferait tourner l'adaptateur sans jamais rendre la main.

        Un ordonnanceur qui monopolise un cœur est une panne, même quand chacun de
        ses passages est correct.
        """
        reveil = prochain_reveil([RELAIS], {}, MIDI)
        assert reveil > timedelta(0)

    def test_tous_abandonnes_rend_la_cadence_la_plus_longue_et_non_l_infini(self):
        """Un processus qui ne se réveille plus jamais ne repartirait pas même réparé."""
        morts = {
            "relais": _passage("relais", echecs=ECHECS_AVANT_ABANDON),
            "relance": _passage("relance", echecs=ECHECS_AVANT_ABANDON),
        }
        assert prochain_reveil([RELAIS, RELANCE], morts, MIDI) == RELANCE.cadence


class TestLaCadenceEstVerifiee:
    @pytest.mark.parametrize("cadence", [timedelta(0), timedelta(seconds=-1)])
    def test_une_cadence_nulle_ou_negative_est_refusee(self, cadence):
        """Elle rendrait le travail dû à chaque évaluation : une boucle chaude qui
        consomme un cœur sans avancer, et sans la moindre trace de son origine."""
        with pytest.raises(ValidationError):
            Travail(nom="fautif", cadence=cadence, objet="rien")


class TestLeTour:
    def test_un_travail_qui_leve_n_arrete_pas_les_suivants(self):
        """La propriété qui compte.

        Un balayage cassé par un modèle de message absent n'a aucune raison
        d'arrêter la publication des événements, dont dépend l'ouverture des
        tenants déjà payés.
        """

        def casse() -> str:
            raise RuntimeError("modèle absent")

        rapport = faire_un_tour(
            [RELANCE, RELAIS], {}, {"relance": casse, "relais": lambda: "3 publiés"}, MIDI
        )
        assert [r.travail for r in rapport.resultats] == ["relance", "relais"]
        assert [r.reussi for r in rapport.resultats] == [False, True]

    def test_le_motif_d_echec_porte_le_type_et_le_message(self):
        """« connection refused » seul ne dit pas quelle couche a refusé."""

        def casse() -> str:
            raise TimeoutError("passerelle muette")

        rapport = faire_un_tour([RELAIS], {}, {"relais": casse}, MIDI)
        assert "TimeoutError" in rapport.echecs[0].compte_rendu
        assert "passerelle muette" in rapport.echecs[0].compte_rendu

    def test_un_travail_sans_executant_est_signale_et_non_ignore(self):
        """C'est la forme que prend une faute de frappe dans un nom de travail.

        Sauter en silence donnerait un travail déclaré, visible dans la
        configuration, présent dans la sonde, et qui ne tourne jamais.
        """
        rapport = faire_un_tour([RELAIS], {}, {}, MIDI)
        assert rapport.sans_executant == ("relais",)
        assert rapport.resultats == ()

    def test_un_travail_sans_executant_n_est_pas_compte_comme_un_echec(self):
        """⚠️ Un échec allonge le recul et conduit à l'abandon.

        Ici il n'y a rien à réessayer : c'est un défaut de branchement, qui se
        corrige dans le code et non par une nouvelle tentative.
        """
        rapport = faire_un_tour([RELAIS], {}, {}, MIDI)
        assert rapport.echecs == ()
        assert rapport.fins == ()  # rien d'écrit : le compteur d'échecs ne bouge pas

    def test_le_debut_et_la_fin_sont_portes_par_une_seule_ecriture(self):
        """La correction d'une erreur de conception, figée pour qu'elle ne revienne pas.

        ─────────────────────────────────────────────────────────────────────────
        Le tour rendait d'abord **deux** états par travail : un début à valider
        avant l'exécution, une fin après. L'intention était de détecter un
        processus tué en cours de travail.

        ⚠️ Cela ne pouvait pas fonctionner. L'exclusion repose sur un verrou
        consultatif de **transaction**, qui tombe à la validation : valider la
        marque de début aurait rendu le verrou au milieu du tour, et une seconde
        instance serait entrée.

        Et le raisonnement était faux plus haut encore : avec un verrou de
        transaction, un processus tué ne laisse rien. Sa transaction est annulée,
        son verrou tombe avec sa connexion. Il n'y a aucun état « commencé sans
        fini » à détecter.

        `debute_le` reste, et mesure la durée du tour. Une seule écriture.
        ─────────────────────────────────────────────────────────────────────────
        """
        rapport = faire_un_tour([RELAIS], {}, {"relais": lambda: "ok"}, MIDI)
        assert not hasattr(rapport, "debuts"), (
            "deux écritures par travail obligeraient à valider entre les deux, "
            "donc à rendre le verrou au milieu du tour"
        )
        assert [p.debute_le for p in rapport.fins] == [MIDI]
        assert [p.termine_le for p in rapport.fins] == [MIDI]

    def test_un_tour_sans_rien_a_faire_le_dit(self):
        passages = {"relais": _passage("relais")}
        rapport = faire_un_tour([RELAIS], passages, {"relais": lambda: "ok"}, MIDI)
        assert rapport.rien_a_faire

    def test_l_instant_est_le_meme_pour_tout_le_tour(self):
        """Un tour qui relirait l'horloge entre deux travaux verrait un travail dû au
        début et plus dû à la fin, selon la durée du précédent."""
        rapport = faire_un_tour(
            [RELAIS, RELANCE],
            {},
            {"relais": lambda: "ok", "relance": lambda: "ok"},
            MIDI,
        )
        assert {p.termine_le for p in rapport.fins} == {MIDI}
