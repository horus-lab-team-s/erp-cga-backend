"""Contexte J · Pilotage CGA.

Ce que ces tests protègent, dans l'ordre où l'erreur coûte le plus cher :

1. **La traçabilité du score.** Un score qui dit « 72 » sans dire pourquoi
   n'appelle aucune action. L'invariant qui exige autant d'éléments cités que
   d'occurrences comptées est le seul rempart contre une traçabilité partielle
   silencieuse — celle qui fait chercher au mauvais endroit.
2. **L'ordre des seuils.** Un dossier ne peut pas être « modéré » au-delà du
   seuil « élevé ». Laisser passer produirait un tableau de bord dont les
   couleurs mentent, ce qui est pire qu'aucun tableau de bord.
3. **Le poids absent valant zéro.** Un score calculé sur un poids inventé ne se
   voit jamais ; un score sous-estimé se voit quand le dossier explose.
4. **La charge qui n'évalue personne.** Les habilitations à portée ouverte ne
   comptent pas : sinon la direction serait en tête de la charge chaque matin.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contextes.pilotage.api import (
    Composante,
    MesureComposante,
    NiveauRisque,
    ObservationsDossier,
    ScoreRisque,
    SeuilsInverses,
    evaluer_le_risque,
    repartir_la_charge,
)
from app.contextes.referentiel.api import DepotParametresYaml, ServiceParametres
from app.infrastructure.config import configuration

JOUR = date(2026, 8, 17)
AGRO = "M065544332211L"


@pytest.fixture(scope="module")
def parametres() -> ServiceParametres:
    return ServiceParametres.depuis_depot(
        DepotParametresYaml(configuration().dossier_referentiel / "parametres.yaml")
    )


def _observations(**remplacements) -> ObservationsDossier:
    defauts = {
        "entreprise": AGRO,
        "denomination": "AGRO-NKOLO SA",
        "anomalies_bloquantes": (),
        "obligations_en_retard": (),
        "pieces_en_souffrance": (),
        "demandes_sans_reponse": (),
    }
    return ObservationsDossier(**{**defauts, **remplacements})


# ══ La traçabilité — la raison d'être du contexte ═════════════════════════════
class TestTracabilite:
    def test_chaque_composante_cite_les_elements_qui_l_ont_produite(self, parametres):
        """LE TEST QUI PORTE LA PROMESSE DU § 01.

        « Dossiers à risque avec score décomposé et **traçable jusqu'à la
        pièce** ». Un directeur qui voit « 3 pièces en souffrance » sans savoir
        lesquelles rouvre le dossier pour les chercher — et le tableau de bord
        lui a fait perdre du temps au lieu de lui en faire gagner.
        """
        score = evaluer_le_risque(
            _observations(pieces_en_souffrance=("PJ-1", "PJ-2", "PJ-3")),
            parametres,
            JOUR,
        )
        mesure = next(
            m for m in score.mesures if m.composante is Composante.PIECES_EN_SOUFFRANCE
        )
        assert mesure.occurrences == 3
        assert mesure.elements == ("PJ-1", "PJ-2", "PJ-3")

    def test_une_tracabilite_partielle_est_refusee(self):
        """Trois occurrences mais deux éléments cités laisse la troisième
        introuvable, et le directeur conclut que l'outil se trompe."""
        with pytest.raises(ValueError, match="traçabilité partielle"):
            MesureComposante(
                composante=Composante.PIECES_EN_SOUFFRANCE,
                libelle="pièces",
                occurrences=3,
                poids=Decimal(5),
                elements=("PJ-1", "PJ-2"),
            )

    def test_une_composante_sans_element_reste_admise(self):
        """Certaines composantes se comptent sans se nommer ; l'invariant ne doit
        pas interdire d'en ajouter une."""
        mesure = MesureComposante(
            composante=Composante.PIECES_EN_SOUFFRANCE,
            libelle="pièces",
            occurrences=4,
            poids=Decimal(5),
        )
        assert mesure.contribution == Decimal(20)

    def test_chaque_composante_dit_quoi_faire(self, parametres):
        """Un score qui ne dit pas quoi faire oblige le directeur à traduire, et
        il traduira mal."""
        score = evaluer_le_risque(
            _observations(anomalies_bloquantes=("PJ-9",), obligations_en_retard=("TVA-07",)),
            parametres,
            JOUR,
        )
        assert all(m.action for m in score.mesures_actives)


# ══ La pondération ════════════════════════════════════════════════════════════
class TestPonderation:
    def test_les_poids_viennent_du_referentiel(self, parametres):
        """« La pondération appartient à la direction : elle se règle au
        référentiel, pas dans le code. » — § 01."""
        score = evaluer_le_risque(
            _observations(anomalies_bloquantes=("PJ-1", "PJ-2")), parametres, JOUR
        )
        mesure = next(
            m for m in score.mesures if m.composante is Composante.ANOMALIES_BLOQUANTES
        )
        assert mesure.poids == Decimal(25)
        assert mesure.contribution == Decimal(50)

    def test_un_poids_absent_vaut_zero_et_le_signale(self):
        """Un score sous-estimé se voit quand le dossier explose ; un score
        calculé sur un poids fantaisiste ne se voit jamais."""
        score = evaluer_le_risque(
            _observations(anomalies_bloquantes=("PJ-1",)), ServiceParametres([]), JOUR
        )
        assert score.total == Decimal(0)
        assert score.repose_sur_des_poids_non_arretes

    def test_les_poids_non_arretes_sont_signales(self, parametres_non_arretes):
        """Un score calculé sur des poids que personne n'a choisis n'est pas un
        indicateur, c'est une opinion du développeur."""
        score = evaluer_le_risque(
            _observations(pieces_en_souffrance=("PJ-1",)), parametres_non_arretes, JOUR
        )
        assert score.repose_sur_des_poids_non_arretes

    def test_les_poids_arretes_par_la_direction_ne_sont_plus_signales(self, parametres):
        """Les six réglages du score portent `nature: POLITIQUE_CABINET` et ont été
        arrêtés par la direction le 18 août 2026.

        Ils n'ont jamais relevé d'un texte : aucun article du CGI ne dit ce que
        vaut un retard déclaratif dans une note interne. Ils attendaient donc une
        signature que personne n'avait qualité pour donner, et c'est cette
        confusion entre « non validé » et « non validable par un fiscaliste » que
        la nature du paramètre a levée."""
        score = evaluer_le_risque(
            _observations(pieces_en_souffrance=("PJ-1",)), parametres, JOUR
        )
        assert not score.repose_sur_des_poids_non_arretes

    def test_l_anomalie_bloquante_pese_plus_que_la_piece_en_souffrance(self, parametres):
        """Une anomalie bloquante interdit la comptabilisation : le dossier ne
        peut pas avancer, et l'échéance avance quand même."""
        bloquante = evaluer_le_risque(
            _observations(anomalies_bloquantes=("A",)), parametres, JOUR
        )
        souffrance = evaluer_le_risque(
            _observations(pieces_en_souffrance=("P",)), parametres, JOUR
        )
        assert bloquante.total > souffrance.total


# ══ Le classement ═════════════════════════════════════════════════════════════
class TestClassement:
    def test_les_trois_niveaux(self, parametres):
        faible = evaluer_le_risque(_observations(), parametres, JOUR)
        modere = evaluer_le_risque(
            _observations(pieces_en_souffrance=tuple(f"P{i}" for i in range(6))),
            parametres,
            JOUR,
        )
        eleve = evaluer_le_risque(
            _observations(anomalies_bloquantes=("A1", "A2", "A3")), parametres, JOUR
        )
        assert faible.niveau is NiveauRisque.FAIBLE
        assert modere.niveau is NiveauRisque.MODERE
        assert eleve.niveau is NiveauRisque.ELEVE

    def test_des_seuils_inverses_sont_refuses(self):
        """Un tableau de bord dont les couleurs mentent est plus dangereux
        qu'aucun tableau de bord.

        ⚠️ On attend `ValidationError` et non `SeuilsInverses` : Pydantic
        enveloppe toute `ValueError` levée dans un `model_validator`. Le type du
        domaine reste utile — il nomme la faute dans le message et sert au
        `raise` — mais il n'atteint pas l'appelant tel quel. C'est la convention
        déjà tenue par `test_comptabilite.py` et `test_collecte.py`, et le
        `match` vérifie que c'est bien cette règle qui a refusé, pas une autre.
        """
        with pytest.raises(ValidationError, match="seuil modéré"):
            ScoreRisque(
                entreprise=AGRO,
                denomination="X",
                seuil_modere=Decimal(80),
                seuil_eleve=Decimal(30),
            )
        assert issubclass(SeuilsInverses, ValueError)

    def test_un_referentiel_incoherent_retombe_sur_des_seuils_ordonnes(self):
        """Des paramètres absents ne doivent pas faire échouer un tableau de bord.

        Les valeurs de repli sont choisies dans le bon ordre, et le résultat
        reste lisible même sur un référentiel incomplet.
        """
        score = evaluer_le_risque(_observations(), ServiceParametres([]), JOUR)
        assert score.seuil_modere < score.seuil_eleve

    def test_les_seuils_sont_conserves_avec_le_score(self, parametres):
        """Sans eux, le niveau ne se rejoue pas : un score de 40 est « modéré » ou
        « élevé » selon le réglage, et le réglage change."""
        score = evaluer_le_risque(_observations(), parametres, JOUR)
        assert score.seuil_modere == Decimal(25)
        assert score.seuil_eleve == Decimal(60)


# ══ Le score lui-même ═════════════════════════════════════════════════════════
class TestScore:
    def test_le_score_n_est_pas_borne_a_cent(self, parametres):
        """Un dossier cumulant douze anomalies doit se distinguer de celui qui en
        compte trois. Le ramener à cent ferait perdre à la direction précisément
        l'information qui la ferait agir en premier.
        """
        score = evaluer_le_risque(
            _observations(anomalies_bloquantes=tuple(f"A{i}" for i in range(12))),
            parametres,
            JOUR,
        )
        assert score.total == Decimal(300)

    def test_toutes_les_composantes_figurent_meme_a_zero(self, parametres):
        """Un tableau qui n'afficherait que ce qui pèse laisserait croire que le
        reste n'a pas été regardé — alors qu'il l'a été, et qu'il est sain."""
        score = evaluer_le_risque(_observations(), parametres, JOUR)
        assert len(score.mesures) == len(Composante)
        assert score.mesures_actives == ()

    def test_les_composantes_actives_sont_triees_par_poids(self, parametres):
        """L'ordre est celui de l'action : la direction traite ce qui pèse le
        plus. Trier par nom produirait un ordre sans rapport avec la décision."""
        score = evaluer_le_risque(
            _observations(
                anomalies_bloquantes=("A",),
                pieces_en_souffrance=("P1", "P2"),
                obligations_en_retard=("O",),
            ),
            parametres,
            JOUR,
        )
        contributions = [m.contribution for m in score.mesures_actives]
        assert contributions == sorted(contributions, reverse=True)

    def test_un_dossier_sain_a_un_score_nul(self, parametres):
        score = evaluer_le_risque(_observations(), parametres, JOUR)
        assert score.total == Decimal(0)
        assert score.niveau is NiveauRisque.FAIBLE

    def test_le_libelle_s_accorde_en_nombre(self, parametres):
        """« 1 anomalie(s) bloquante(s) » fait buter l'œil sur la parenthèse au
        lieu du chiffre."""
        une = evaluer_le_risque(
            _observations(anomalies_bloquantes=("A",)), parametres, JOUR
        )
        deux = evaluer_le_risque(
            _observations(anomalies_bloquantes=("A", "B")), parametres, JOUR
        )
        assert une.mesures_actives[0].libelle.endswith("levée")
        assert deux.mesures_actives[0].libelle.endswith("levées")


# ══ La charge par collaborateur ═══════════════════════════════════════════════
class TestCharge:
    def _score(self, niu: str, anomalies: int, parametres) -> ScoreRisque:
        return evaluer_le_risque(
            _observations(
                entreprise=niu,
                denomination=niu,
                anomalies_bloquantes=tuple(f"A{i}" for i in range(anomalies)),
            ),
            parametres,
            JOUR,
        )

    def test_la_charge_distingue_dix_dossiers_calmes_de_trois_en_feu(self, parametres):
        """C'est tout l'objet du cumul de risque : compter les dossiers seuls
        ferait passer pour surchargé celui qui n'a que du travail facile."""
        scores = [
            self._score("A", 0, parametres),
            self._score("B", 0, parametres),
            self._score("C", 4, parametres),
        ]
        charges = repartir_la_charge(
            scores,
            {"A": ("calme",), "B": ("calme",), "C": ("charge",)},
            {"calme": "Paul CALME", "charge": "Marie CHARGE"},
        )
        assert charges[0].compte == "charge", "le plus risqué se présente en premier"
        assert charges[0].dossiers == 1
        assert charges[0].dossiers_a_risque_eleve == 1
        assert charges[1].dossiers == 2
        assert charges[1].risque_porte == Decimal(0)

    def test_un_dossier_suivi_a_deux_compte_pour_les_deux(self, parametres):
        """Le répartir par moitié donnerait des demi-dossiers, ce qui ne veut rien
        dire : les deux ont bien le dossier entier sur les bras."""
        charges = repartir_la_charge(
            [self._score("A", 2, parametres)],
            {"A": ("un", "deux")},
            {"un": "Un", "deux": "Deux"},
        )
        assert len(charges) == 2
        assert all(c.dossiers == 1 for c in charges)
        assert all(c.risque_porte == Decimal(50) for c in charges)

    def test_le_risque_moyen_ne_divise_jamais_par_zero(self):
        from app.contextes.pilotage.api import ChargeCollaborateur

        vide = ChargeCollaborateur(compte="x", nom_complet="X", dossiers=0)
        assert vide.risque_moyen == Decimal(0)

    def test_un_collaborateur_sans_dossier_n_apparait_pas(self, parametres):
        """La charge liste ceux qui portent, pas l'annuaire du cabinet."""
        charges = repartir_la_charge(
            [self._score("A", 1, parametres)],
            {"A": ("porteur",)},
            {"porteur": "Porteur", "oisif": "Oisif"},
        )
        assert [c.compte for c in charges] == ["porteur"]


# ══ La route, et pourquoi elle a sa propre classe ═════════════════════════════
#
# ⚠️ CE QUI A ÉTÉ APPRIS ICI, AU PRIX D'UN HTTP 500 EN RECETTE
#
# Les classes ci-dessus éprouvent le calcul en lui **donnant** des observations.
# Elles ne touchent jamais `_observer`, qui est le seul endroit où le pilotage
# lit les cinq autres contextes. Un champ mal nommé y est donc passé au travers
# de vingt et un tests verts : `instance.type_obligation` au lieu de
# `code_obligation`, découvert à l'exécution seulement.
#
# La leçon n'est pas « il fallait mieux relire ». C'est que la collecte est du
# **câblage**, et que le câblage ne se vérifie qu'en le parcourant. D'où ces
# tests, qui appellent la route et rien d'autre.
class TestRoute:
    @pytest.fixture(scope="class")
    def application(self):
        from app.contextes.transverse.adaptateurs.entrant.dependances import (
            reinitialiser_atelier,
        )
        from app.main import creer_application

        reinitialiser_atelier()
        return creer_application()

    def _connecte(self, application, courriel: str):
        from fastapi.testclient import TestClient

        from app.contextes.transverse.api import MOT_DE_PASSE_DEMO

        client = TestClient(application)
        reponse = client.post(
            "/transverse/session",
            json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        assert reponse.status_code == 200, reponse.text
        return client

    @pytest.fixture(scope="class")
    def direction(self, application):
        return self._connecte(application, "b.mballa@cga-brcg.cm")

    def test_la_collecte_traverse_les_cinq_contextes(self, direction):
        """Le test qui manquait. Il ne juge aucun chiffre : il exige que la route
        aille au bout, ce qu'aucun test d'unité ne faisait."""
        reponse = direction.get("/pilotage/tableau-de-bord?a_la_date=2026-08-17")
        assert reponse.status_code == 200, reponse.text
        corps = reponse.json()
        assert corps["dossiers"] > 0
        assert len(corps["risques"]) == corps["dossiers"]

    def test_les_dossiers_sont_rendus_du_plus_risque_au_moins_risque(self, direction):
        """Un tri alphabétique ferait un annuaire, joli et inutile un lundi matin."""
        corps = direction.get("/pilotage/tableau-de-bord?a_la_date=2026-08-17").json()
        totaux = [float(ligne["total"]) for ligne in corps["risques"]]
        assert totaux == sorted(totaux, reverse=True)

    def test_chaque_composante_active_cite_ses_elements(self, direction):
        """La promesse du § 01, éprouvée sur les données réelles et non sur des
        observations fabriquées : c'est là que les références se perdent."""
        corps = direction.get("/pilotage/tableau-de-bord?a_la_date=2026-08-17").json()
        mesures = [m for ligne in corps["risques"] for m in ligne["mesures"]]
        assert mesures, "aucune composante active : le jeu de démonstration ne prouve rien"
        for mesure in mesures:
            assert mesure["action"]
            if mesure["elements"]:
                assert len(mesure["elements"]) == mesure["occurrences"]

    def test_une_cnps_en_retard_pese_sur_le_risque_de_l_employeur(self, direction):
        """⚠️ **Le pilotage ne comptait jamais une cotisation sociale en retard.** (pas 56)

        Son échéancier était calculé sans salariés, comme tous les autres. Le 17 août,
        la CNPS de juillet d'un employeur est échue depuis deux jours : elle doit
        figurer parmi les éléments du retard déclaratif, et pas chez un dossier sans
        personnel.
        """
        corps = direction.get("/pilotage/tableau-de-bord?a_la_date=2026-08-17").json()
        elements = {
            ligne["entreprise"]: [
                e for m in ligne["mesures"] for e in m["elements"] if e.startswith("CNPS")
            ]
            for ligne in corps["risques"]
        }
        assert "CNPS 07/2026" in elements["M065544332211L"], elements
        assert elements["P019876543210K"] == [], elements

    def test_les_demandes_echues_pesent_sur_le_risque(self, direction):
        """⚠️ **Cette composante valait toujours zéro** (pas 104).

        La lecture appelait une méthode que le dépôt des demandes n'a pas, l'erreur était
        avalée par l'agrégateur, et le score de chaque dossier ignorait les pièces demandées
        et jamais reçues. Le 17 août, DP-2026-002 et DP-2026-009 (attendues le 12) sont
        échues chez SARL BATIMENT PLUS ; DP-2026-010, attendue le 20, ne l'est pas encore.
        """
        corps = direction.get("/pilotage/tableau-de-bord?a_la_date=2026-08-17").json()
        elements = {
            ligne["entreprise"]: [
                e
                for m in ligne["mesures"]
                if m["composante"] == "DEMANDES_SANS_REPONSE"
                for e in m["elements"]
            ]
            for ligne in corps["risques"]
        }
        assert sorted(elements["M081234567890P"]) == ["DP-2026-002", "DP-2026-009"]
        # Sans date butoir mais assez ancienne pour être escaladée : elle compte aussi.
        assert "DP-2026-011" in elements["M065544332211L"]

    def test_la_charge_ne_compte_que_les_collaborateurs_du_cabinet(self, direction):
        """⚠️ **Adhérents et inspecteur figuraient parmi les collaborateurs** (pas 105).

        Leur habilitation a une portée explicite (leur dossier, leur mission) ; la règle « portée
        explicite = porte le dossier » les comptait donc dans la charge du cabinet.
        """
        corps = direction.get("/pilotage/tableau-de-bord?a_la_date=2026-08-17").json()
        noms = {c["nom_complet"] for c in corps["charges"]}
        assert noms == {"Patricia MOUKOURI", "Léonard FOTSO", "Christelle NDONGO"}

    def test_le_pilotage_est_ferme_meme_au_reviseur(self, application):
        """Voir tout le portefeuille n'est pas piloter le cabinet : le score porte
        un jugement d'affectation, que seule la direction a mandat de lire."""
        for courriel in (
            "a.bouba@cga-brcg.cm",
            "l.fotso@cga-brcg.cm",
            "jp.nkoa@batimentplus.cm",
        ):
            client = self._connecte(application, courriel)
            reponse = client.get("/pilotage/tableau-de-bord")
            assert reponse.status_code == 403, f"{courriel} : {reponse.status_code}"
