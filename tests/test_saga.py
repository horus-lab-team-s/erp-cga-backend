"""Le socle d'orchestration : saga et boîte d'envoi.

Ces tests portent sur le **mécanisme**, sans aucun métier. Ils éprouvent ce qui
fait la différence entre une saga et une boucle `for` :

* **la reprise**, qui repart après la dernière étape franchie et ne rejoue rien ;
* **la compensation**, à rebours et seulement sur ce qui a été franchi ;
* **les deux états qui ne se résolvent pas tout seuls**, et qui doivent se voir ;
* **la déclaration obligatoire** de ce qu'on fait d'un effet externe.

La saga concrète du tenant est éprouvée à la fin, avec un provisionneur d'essai
qui compte ses appels : c'est ainsi qu'on vérifie qu'une reprise n'ouvre pas un
second préfixe de stockage.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import pytest

from app.contextes.tenants.application.ouverture import (
    NOM_SAGA,
    saga_d_ouverture,
)
from app.contextes.tenants.domaine.tenant import ETAPES_ORDONNEES, EtapeOuverture
from app.orchestration.boite_d_envoi import (
    TENTATIVES_AVANT_QUARANTAINE,
    EvenementSortant,
)
from app.orchestration.saga import (
    DefinitionInvalide,
    Etape,
    EtatSaga,
    Saga,
    abandonner,
    avancer,
    demarrer,
)

T0 = datetime(2026, 9, 9, 10, 0)


class Journal:
    """Un provisionneur d'essai qui retient ce qu'on lui a demandé, et combien de
    fois. C'est le compteur qui permet de prouver l'idempotence."""

    def __init__(self, echoue_sur: str | None = None, echoue_a_compenser: str = "") -> None:
        self.appels: list[str] = []
        self.echoue_sur = echoue_sur
        self.echoue_a_compenser = echoue_a_compenser
        self.tolerance = 0

    def action(self, nom: str):
        def _agir(contexte: Mapping[str, Any]) -> Mapping[str, Any]:
            self.appels.append(nom)
            if nom == self.echoue_sur:
                if self.tolerance <= 0:
                    raise RuntimeError(f"service indisponible pour {nom}")
                self.tolerance -= 1
            return {**contexte, nom: True}

        return _agir

    def compensation(self, nom: str):
        def _defaire(contexte: Mapping[str, Any]) -> Mapping[str, Any]:
            self.appels.append(f"~{nom}")
            if nom == self.echoue_a_compenser:
                raise RuntimeError(f"impossible de défaire {nom}")
            return {k: v for k, v in contexte.items() if k != nom}

        return _defaire

    def compte(self, nom: str) -> int:
        return self.appels.count(nom)


def _saga(journal: Journal, *noms: str, sans_compensation: tuple[str, ...] = ()) -> Saga:
    return Saga(
        nom="essai",
        etapes=tuple(
            Etape(
                nom=nom,
                action=journal.action(nom),
                compensation=(
                    None if nom in sans_compensation else journal.compensation(nom)
                ),
                irreversible="courriel parti" if nom in sans_compensation else "",
            )
            for nom in noms
        ),
    )


# ── La déclaration ────────────────────────────────────────────────────────────


class TestDeclaration:
    def test_un_effet_externe_sans_compensation_ni_raison_est_refuse(self):
        """⚠️ **Le défaut classique des sagas** : on écrit six compensations, on
        oublie la septième, et l'on découvre à l'abandon qu'un préfixe de
        stockage reste facturé pour un client qui n'existe plus. L'oubli ne se
        voit jamais au moment où on le commet, puisque le chemin nominal
        fonctionne parfaitement."""
        with pytest.raises(DefinitionInvalide, match="sans compensation ni raison"):
            Etape(nom="stockage", action=lambda c: c)

    def test_une_raison_d_irreversibilite_suffit(self):
        """Se déclarer irréversible est une réponse acceptable. Se taire non."""
        etape = Etape(
            nom="courriel", action=lambda c: c, irreversible="un envoi ne se rappelle pas"
        )
        assert etape.compensation is None

    def test_compensable_et_irreversible_a_la_fois_est_refuse(self):
        """L'une des deux affirmations est fausse, et on ne sait pas laquelle."""
        with pytest.raises(DefinitionInvalide, match="affirmations est fausse"):
            Etape(
                nom="x",
                action=lambda c: c,
                compensation=lambda c: c,
                irreversible="pourtant",
            )

    def test_une_etape_en_base_n_a_pas_besoin_de_compensation(self):
        """Le `ROLLBACK` la défait. Exiger une compensation ferait écrire du code
        qui ne sert jamais, et qu'on finirait par ne plus relire."""
        assert Etape(nom="x", action=lambda c: c, externe=False).compensation is None

    def test_une_saga_sans_etape_est_refusee(self):
        with pytest.raises(DefinitionInvalide, match="sans étape"):
            Saga(nom="vide", etapes=())

    def test_deux_etapes_du_meme_nom_sont_refusees(self):
        """L'avancement est repéré par le nom ; deux homonymes rendraient la
        reprise indécidable."""
        journal = Journal()
        with pytest.raises(DefinitionInvalide, match="du même nom"):
            _saga(journal, "a", "a")


# ── Le chemin nominal ─────────────────────────────────────────────────────────


class TestCheminNominal:
    def test_toutes_les_etapes_sont_franchies_dans_l_ordre(self):
        journal = Journal()
        saga = _saga(journal, "a", "b", "c")
        fin = avancer(saga, demarrer(saga, "cle-1", {}, T0), T0)

        assert fin.etat is EtatSaga.TERMINEE
        assert fin.franchies == ("a", "b", "c")
        assert journal.appels == ["a", "b", "c"]

    def test_le_contexte_traverse_les_etapes(self):
        journal = Journal()
        saga = _saga(journal, "a", "b")
        fin = avancer(saga, demarrer(saga, "cle-1", {"slug": "station"}, T0), T0)
        assert fin.contexte == {"slug": "station", "a": True, "b": True}

    def test_rejouer_une_saga_terminee_ne_refait_rien(self):
        """Les tâches d'arrière-plan se rejouent, c'est leur mécanisme de
        reprise. Sans cette garde, chaque rejeu relancerait la dernière étape."""
        journal = Journal()
        saga = _saga(journal, "a", "b")
        fin = avancer(saga, demarrer(saga, "cle-1", {}, T0), T0)
        rejoue = avancer(saga, fin, T0)

        assert rejoue is fin
        assert journal.appels == ["a", "b"]


# ── La reprise ────────────────────────────────────────────────────────────────


class TestReprise:
    def test_un_echec_arrete_a_l_etape_et_ne_compense_rien(self):
        """⚠️ La règle la plus importante du moteur. Compenser sur un échec
        technique détruirait un tenant à moitié créé pour une coupure de trois
        secondes."""
        journal = Journal(echoue_sur="b")
        saga = _saga(journal, "a", "b", "c")
        apres = avancer(saga, demarrer(saga, "cle-1", {}, T0), T0)

        assert apres.etat is EtatSaga.EN_COURS
        assert apres.franchies == ("a",)
        assert apres.compensees == ()
        assert "b : service indisponible" in apres.dernier_echec
        assert journal.appels == ["a", "b"]

    def test_la_reprise_repart_a_l_etape_qui_a_echoue(self):
        """Et ne rejoue **pas** ce qui était franchi. C'est ce qui distingue une
        saga d'une boucle : la seconde recommencerait tout."""
        journal = Journal(echoue_sur="b")
        saga = _saga(journal, "a", "b", "c")
        bloquee = avancer(saga, demarrer(saga, "cle-1", {}, T0), T0)

        journal.echoue_sur = None
        fin = avancer(saga, bloquee, T0)

        assert fin.etat is EtatSaga.TERMINEE
        assert journal.compte("a") == 1
        assert journal.compte("b") == 2
        assert journal.compte("c") == 1

    def test_les_tentatives_se_comptent_et_se_remettent_a_zero(self):
        """À zéro dès qu'une étape passe : le compteur mesure l'acharnement sur
        une étape, pas l'âge de l'exécution."""
        journal = Journal(echoue_sur="b")
        saga = _saga(journal, "a", "b", "c")
        courante = demarrer(saga, "cle-1", {}, T0)
        for attendu in (1, 2, 3):
            courante = avancer(saga, courante, T0)
            assert courante.tentatives == attendu

        journal.echoue_sur = None
        assert avancer(saga, courante, T0).tentatives == 0

    def test_au_dela_de_la_borne_l_execution_appelle_un_humain(self):
        """Une boucle sans borne ne se voit pas : elle se découvre sur la facture
        du prestataire ou dans un journal saturé."""
        journal = Journal(echoue_sur="b")
        saga = _saga(journal, "a", "b")
        courante = demarrer(saga, "cle-1", {}, T0)
        for _ in range(3):
            courante = avancer(saga, courante, T0, tentatives_maximales=3)

        assert courante.etat is EtatSaga.ECHOUEE
        assert courante.demande_un_humain is True

    def test_une_execution_echouee_se_reprend(self):
        """Si un humain la relance, c'est qu'il a levé la cause. La laisser
        bloquée obligerait à la recréer, donc à perdre ce qu'elle avait déjà
        franchi."""
        journal = Journal(echoue_sur="b")
        saga = _saga(journal, "a", "b", "c")
        courante = demarrer(saga, "cle-1", {}, T0)
        for _ in range(3):
            courante = avancer(saga, courante, T0, tentatives_maximales=3)
        assert courante.etat is EtatSaga.ECHOUEE

        journal.echoue_sur = None
        fin = avancer(saga, courante, T0)
        assert fin.etat is EtatSaga.TERMINEE
        assert journal.compte("a") == 1

    def test_la_reprise_apres_echec_rend_un_budget_de_tentatives_neuf(self):
        """⚠️ La subtilité que le compteur seul ne montre pas.

        Un humain qui relance une exécution `ECHOUEE` a levé la cause. Reprendre
        sans remettre le compteur à zéro lui accorderait **une seule** tentative
        avant de rebasculer en échec : la cause serait levée, le service
        répondrait, et l'exécution retomberait quand même au premier hoquet
        suivant.

        Le test le prouve en laissant l'étape échouer encore : avec le budget
        remis à neuf, elle a droit à ses trois essais ; sans, elle épuise le
        sien dès le premier.
        """
        journal = Journal(echoue_sur="b")
        saga = _saga(journal, "a", "b")
        courante = demarrer(saga, "cle-1", {}, T0)
        for _ in range(3):
            courante = avancer(saga, courante, T0, tentatives_maximales=3)
        assert courante.etat is EtatSaga.ECHOUEE

        # Un humain relance. L'étape échoue encore une fois.
        reprise = avancer(saga, courante, T0, tentatives_maximales=3)
        assert reprise.etat is EtatSaga.EN_COURS, (
            "une reprise demandée par un humain doit repartir avec un budget "
            "entier, pas avec le reliquat de l'échec précédent"
        )
        assert reprise.tentatives == 1

    def test_une_etape_inseree_au_milieu_se_franchit_a_la_reprise(self):
        """L'avancement est repéré par les **noms**, jamais par un index.

        Le cas est réel : une définition gagne une étape pendant que des
        exécutions sont en cours. Avec un index, la reprise repartirait au
        mauvais endroit et rejouerait une étape déjà franchie ou en sauterait
        une. Avec les noms, elle franchit la nouvelle et laisse les autres
        tranquilles.
        """
        journal = Journal(echoue_sur="d")
        ancienne = _saga(journal, "a", "c", "d")
        courante = avancer(ancienne, demarrer(ancienne, "cle-1", {}, T0), T0)
        assert courante.franchies == ("a", "c")

        journal.echoue_sur = None
        enrichie = _saga(journal, "a", "b", "c", "d")
        reprise = avancer(enrichie, courante, T0)

        assert reprise.etat is EtatSaga.TERMINEE
        assert reprise.franchies == ("a", "c", "b", "d")
        assert journal.compte("a") == 1, "une étape franchie a été rejouée"
        assert journal.compte("c") == 1, "une étape franchie a été rejouée"
        assert journal.compte("b") == 1, "l'étape insérée a été sautée"

    def test_une_etape_disparue_de_la_definition_ne_se_compense_pas_en_silence(self):
        """On ne devine pas comment défaire ce qu'on ne connaît plus, et l'on
        refuse de le taire : l'effet reste pendant, il doit se voir."""
        journal = Journal()
        ancienne = _saga(journal, "a", "obsolete")
        fin = avancer(ancienne, demarrer(ancienne, "cle-1", {}, T0), T0)

        nouvelle = _saga(journal, "a")
        annulee = abandonner(nouvelle, fin, T0, motif="abandon")

        assert annulee.etat is EtatSaga.COMPENSATION_INCOMPLETE
        assert annulee.compensations_en_echec == (
            "obsolete : étape absente de la définition courante",
        )
        assert annulee.compensees == ("a",)


# ── La compensation ───────────────────────────────────────────────────────────


class TestCompensation:
    def test_elle_defait_a_rebours(self):
        """Défaire dans l'ordre de création laisserait des dépendances :
        supprimer un tenant avant son compte administrateur laisse le compte
        orphelin."""
        journal = Journal()
        saga = _saga(journal, "a", "b", "c")
        fin = avancer(saga, demarrer(saga, "cle-1", {}, T0), T0)

        annulee = abandonner(saga, fin, T0, motif="client rétracté")
        assert annulee.etat is EtatSaga.COMPENSEE
        assert annulee.compensees == ("c", "b", "a")
        assert journal.appels[-3:] == ["~c", "~b", "~a"]

    def test_elle_ne_touche_que_ce_qui_a_ete_franchi(self):
        """⚠️ Le second défaut classique : compenser un stockage jamais ouvert
        supprime, dans le meilleur des cas, un préfixe qui n'existe pas — dans le
        pire, celui d'un autre."""
        journal = Journal(echoue_sur="c")
        saga = _saga(journal, "a", "b", "c", "d")
        bloquee = avancer(saga, demarrer(saga, "cle-1", {}, T0), T0)

        annulee = abandonner(saga, bloquee, T0, motif="abandon")
        assert annulee.compensees == ("b", "a")
        assert "~c" not in journal.appels
        assert "~d" not in journal.appels

    def test_une_compensation_qui_echoue_n_arrete_pas_les_autres(self):
        """Défaire ce qu'on peut vaut mieux que s'arrêter au premier obstacle."""
        journal = Journal(echoue_a_compenser="b")
        saga = _saga(journal, "a", "b", "c")
        fin = avancer(saga, demarrer(saga, "cle-1", {}, T0), T0)

        annulee = abandonner(saga, fin, T0, motif="abandon")
        assert annulee.compensees == ("c", "a")
        assert annulee.compensations_en_echec == ("b : impossible de défaire b",)
        assert annulee.etat is EtatSaga.COMPENSATION_INCOMPLETE

    def test_une_compensation_incomplete_appelle_un_humain(self):
        """C'est l'état le plus important du lot : **il reste des effets externes
        pendants**, il ne se résout pas tout seul, et il doit produire une alerte
        nominative."""
        journal = Journal(echoue_a_compenser="b")
        saga = _saga(journal, "a", "b")
        fin = avancer(saga, demarrer(saga, "cle-1", {}, T0), T0)
        annulee = abandonner(saga, fin, T0, motif="abandon")

        assert annulee.demande_un_humain is True
        assert annulee.achevee is True

    def test_une_etape_irreversible_est_nommee_et_non_passee_sous_silence(self):
        """Un courriel envoyé ne se rappelle pas, et l'exploitation doit le
        savoir plutôt que de le supposer."""
        journal = Journal()
        saga = _saga(journal, "a", "courriel", sans_compensation=("courriel",))
        fin = avancer(saga, demarrer(saga, "cle-1", {}, T0), T0)

        annulee = abandonner(saga, fin, T0, motif="abandon")
        assert annulee.compensations_en_echec == (
            "courriel : irréversible — courriel parti",
        )
        assert annulee.compensees == ("a",)

    def test_le_motif_atteint_les_compensations(self):
        """⚠️ Une compensation a besoin de savoir **pourquoi** elle défait.

        Un tenant résilié porte un motif que le client lira ; « annulé » ne lui
        apprendrait rien, et l'exploitation ne saurait pas distinguer une
        rétractation d'un paiement contesté. Le motif entrait dans l'exécution
        et pas dans le contexte : les compensations retombaient sur leur valeur
        par défaut, et le défaut ne s'est vu qu'en écrivant la première
        compensation qui s'en sert.
        """
        from app.orchestration.saga import CLE_MOTIF

        vus: list[str] = []

        def defaire(contexte):
            vus.append(str(contexte.get(CLE_MOTIF)))
            return contexte

        saga = Saga(
            nom="essai",
            etapes=(Etape(nom="a", action=lambda c: c, compensation=defaire),),
        )
        fin = avancer(saga, demarrer(saga, "cle-1", {}, T0), T0)
        abandonner(saga, fin, T0, motif="paiement contesté")

        assert vus == ["paiement contesté"]

    def test_abandonner_deux_fois_ne_recompense_pas(self):
        """Les tâches se rejouent. Une seconde compensation supprimerait un
        préfixe déjà supprimé, ou pire, celui d'un autre."""
        journal = Journal()
        saga = _saga(journal, "a", "b")
        fin = avancer(saga, demarrer(saga, "cle-1", {}, T0), T0)
        une_fois = abandonner(saga, fin, T0, motif="abandon")
        deux_fois = abandonner(saga, une_fois, T0, motif="abandon")

        assert deux_fois is une_fois
        assert journal.compte("~b") == 1

    def test_avancer_sur_une_execution_compensee_ne_fait_rien(self):
        """Sinon une tâche de reprise ressusciterait un tenant abandonné."""
        journal = Journal()
        saga = _saga(journal, "a", "b")
        fin = avancer(saga, demarrer(saga, "cle-1", {}, T0), T0)
        annulee = abandonner(saga, fin, T0, motif="abandon")

        assert avancer(saga, annulee, T0) is annulee


# ── La boîte d'envoi ──────────────────────────────────────────────────────────


class TestBoiteDEnvoi:
    def _evenement(self, **surcharges) -> EvenementSortant:
        defauts = {
            "identifiant": "ev-1",
            "nom": "PaiementEncaissé",
            "cle": "dos-1",
            "cree_le": T0,
        }
        return EvenementSortant(**{**defauts, **surcharges})

    def test_un_evenement_neuf_attend(self):
        assert self._evenement().en_attente is True

    def test_la_publication_est_rejouable_sans_repousser_la_date(self):
        """La date sert à mesurer le délai de bout en bout. La repousser à chaque
        confirmation en ferait une mesure du dernier rejeu."""
        publie = self._evenement().publie(T0)
        assert publie.publie(datetime(2026, 9, 10)) is publie
        assert publie.en_attente is False

    def test_un_echec_se_compte(self):
        apres = self._evenement().echoue("consommateur injoignable")
        assert apres.tentatives == 1
        assert apres.dernier_echec == "consommateur injoignable"
        assert apres.en_attente is True

    def test_au_dela_du_seuil_l_evenement_part_en_quarantaine(self):
        """Un événement qu'aucun consommateur n'accepte sature la file et retarde
        tous les autres, dont ceux qui, eux, seraient traités."""
        evenement = self._evenement()
        for _ in range(TENTATIVES_AVANT_QUARANTAINE):
            evenement = evenement.echoue("refus")
        assert evenement.en_quarantaine is True
        assert evenement.en_attente is False

    def test_la_quarantaine_n_est_pas_une_suppression(self):
        """Supprimer effacerait la trace d'un fait qui a bien eu lieu, et l'on
        chercherait longtemps pourquoi un tenant payé n'a jamais été ouvert."""
        evenement = self._evenement()
        for _ in range(TENTATIVES_AVANT_QUARANTAINE):
            evenement = evenement.echoue("refus")
        assert evenement.nom == "PaiementEncaissé"
        assert evenement.charge == {}
        assert evenement.dernier_echec == "refus"


# ── La saga du tenant ─────────────────────────────────────────────────────────


class ProvisionneurDEssai:
    """Compte ses appels. C'est ainsi qu'on prouve qu'une reprise n'ouvre pas un
    second préfixe de stockage."""

    def __init__(self) -> None:
        self.appels: list[str] = []
        self.echoue_sur: str | None = None

    def __getattr__(self, nom: str):
        def _agir(contexte: Mapping[str, Any]) -> Mapping[str, Any]:
            self.appels.append(nom)
            if nom == self.echoue_sur:
                raise RuntimeError("prestataire injoignable")
            return {**contexte, nom: True}

        return _agir

    def compte(self, nom: str) -> int:
        return self.appels.count(nom)


class TestSagaDuTenant:
    def test_elle_suit_l_ordre_du_domaine(self):
        """⚠️ Les deux listes vivent dans deux fichiers. En réordonner une sans
        l'autre produirait un provisionnement qui franchit les étapes dans un
        ordre et les enregistre dans un autre, et le défaut ne se verrait qu'à la
        première reprise après incident."""
        saga = saga_d_ouverture(ProvisionneurDEssai())
        assert saga.noms == tuple(e.value for e in ETAPES_ORDONNEES)
        assert saga.nom == NOM_SAGA

    def test_un_ordre_divergent_est_refuse_a_la_construction(self):
        """⚠️ Ce contrôle ne peut pas échouer tant que la saga est écrite dans le
        bon ordre — c'est précisément pourquoi il faut l'éprouver à part.

        Le test qui se contentait de construire la saga réelle passait aussi bien
        avec le contrôle que sans lui : il ne disait rien de son existence. Un
        garde-fou qu'aucun test ne fait tomber est un garde-fou dont personne ne
        sait s'il fonctionne.

        Le cas réel qu'il attrape : quelqu'un réordonne le tuple écrit à la main
        dans `saga_d_ouverture` sans toucher à `ETAPES_ORDONNEES`. Le
        provisionnement franchirait alors les étapes dans un ordre et les
        enregistrerait dans un autre, et le défaut ne se verrait qu'à la première
        reprise après incident.
        """
        from app.contextes.tenants.application.ouverture import _verifier_l_ordre

        correcte = saga_d_ouverture(ProvisionneurDEssai())
        _verifier_l_ordre(correcte)  # ne lève pas

        inversee = Saga(nom=NOM_SAGA, etapes=tuple(reversed(correcte.etapes)))
        with pytest.raises(ValueError, match="alors que le domaine déclare"):
            _verifier_l_ordre(inversee)

    def test_chaque_etape_externe_declare_ce_qu_on_en_fait(self):
        """C'est le contrôle que la construction impose. On le répète ici pour
        que le nom du test soit lisible dans un rapport."""
        for etape in saga_d_ouverture(ProvisionneurDEssai()).etapes:
            if etape.externe:
                assert etape.compensation is not None or etape.irreversible

    def test_le_provisionnement_complet_franchit_les_sept_etapes(self):
        provisionneur = ProvisionneurDEssai()
        saga = saga_d_ouverture(provisionneur)
        fin = avancer(saga, demarrer(saga, "station", {"slug": "station"}, T0), T0)

        assert fin.etat is EtatSaga.TERMINEE
        assert len(fin.franchies) == 7
        assert fin.franchies[-1] == EtapeOuverture.PRET

    def test_une_reprise_n_ouvre_pas_un_second_stockage(self):
        """**Le test qui justifie tout le mécanisme.** Sans reprise à la bonne
        étape, chaque rejeu rouvrirait un préfixe de stockage et enverrait un
        second lien d'activation."""
        provisionneur = ProvisionneurDEssai()
        provisionneur.echoue_sur = "creer_l_administrateur"
        saga = saga_d_ouverture(provisionneur)

        bloquee = avancer(saga, demarrer(saga, "station", {}, T0), T0)
        assert bloquee.etat is EtatSaga.EN_COURS
        assert bloquee.franchies[-1] == EtapeOuverture.STOCKAGE_OUVERT

        provisionneur.echoue_sur = None
        fin = avancer(saga, bloquee, T0)

        assert fin.etat is EtatSaga.TERMINEE
        assert provisionneur.compte("ouvrir_le_stockage") == 1
        assert provisionneur.compte("creer_l_administrateur") == 2

    def test_l_abandon_defait_a_rebours_et_libere_le_stockage(self):
        """C'est la compensation dont l'oubli coûte de l'argent tous les mois,
        sans que personne ne le voie."""
        provisionneur = ProvisionneurDEssai()
        saga = saga_d_ouverture(provisionneur)
        fin = avancer(saga, demarrer(saga, "station", {}, T0), T0)

        annulee = abandonner(saga, fin, T0, motif="paiement contesté")
        assert annulee.etat is EtatSaga.COMPENSEE
        assert provisionneur.compte("fermer_le_stockage") == 1
        assert provisionneur.compte("revoquer_le_jeton") == 1
        assert annulee.compensees[0] == EtapeOuverture.PRET
        assert annulee.compensees[-1] == EtapeOuverture.SLUG_RESERVE

    def test_un_abandon_partiel_ne_defait_que_ce_qui_existe(self):
        provisionneur = ProvisionneurDEssai()
        provisionneur.echoue_sur = "ouvrir_le_stockage"
        saga = saga_d_ouverture(provisionneur)
        bloquee = avancer(saga, demarrer(saga, "station", {}, T0), T0)

        annulee = abandonner(saga, bloquee, T0, motif="abandon")
        assert annulee.etat is EtatSaga.COMPENSEE
        assert provisionneur.compte("fermer_le_stockage") == 0
        assert provisionneur.compte("revoquer_le_jeton") == 0
        assert provisionneur.compte("supprimer_le_schema") == 1
