"""Le relais de publication : vider la boîte sans casser l'ordre.

Ces tests portent sur ce qui distingue un relais d'une boucle `for` :

* **l'ordre par clé tient même quand ça casse.** C'est la partie subtile, et
  celle dont l'absence ne se voit qu'au moment d'un incident ;
* **la panne d'un abonné n'arrête pas les autres**, ni le passage ;
* **un événement sans abonné est publié**, pas mis en échec.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.infrastructure.depots_orchestration import BoiteDEnvoiMemoire
from app.orchestration.boite_d_envoi import (
    TENTATIVES_AVANT_QUARANTAINE,
    EvenementSortant,
    deposer,
)
from app.orchestration.relais import (
    Abonnements,
    journal_du_passage,
    publier_un_lot,
)

T0 = datetime(2026, 9, 10, 9, 0)


def _boite(*evenements: tuple[str, str, str]) -> BoiteDEnvoiMemoire:
    """Une boîte garnie de `(identifiant, nom, clé)`, espacés d'une minute."""
    boite = BoiteDEnvoiMemoire()
    for rang, (identifiant, nom, cle) in enumerate(evenements):
        deposer(boite, identifiant, nom, cle, {}, T0 + timedelta(minutes=rang))
    return boite


class Espion:
    """Un abonné qui retient ce qu'il a reçu, et qui peut échouer à volonté."""

    def __init__(self, echoue_sur: set[str] | None = None) -> None:
        self.recus: list[str] = []
        self.echoue_sur = echoue_sur or set()

    def __call__(self, evenement: EvenementSortant) -> None:
        self.recus.append(evenement.identifiant)
        if evenement.identifiant in self.echoue_sur:
            raise RuntimeError("consommateur injoignable")


# ── Le chemin nominal ─────────────────────────────────────────────────────────


class TestPassageOrdinaire:
    def test_tout_part_et_rien_ne_reste(self):
        boite = _boite(("ev-1", "PaiementEncaissé", "dos-1"))
        espion = Espion()
        abonnements = Abonnements()
        abonnements.abonner("PaiementEncaissé", espion)

        rapport = publier_un_lot(boite, abonnements, T0)

        assert rapport.publies == 1
        assert rapport.echecs == 0
        assert espion.recus == ["ev-1"]
        assert boite.a_publier() == []

    def test_l_ordre_de_remise_est_celui_de_l_emission(self):
        """Du plus ancien au plus récent. C'est ce qui garantit qu'un
        `TenantOuvert` ne précède pas le `PaiementEncaissé` qui l'a causé."""
        boite = _boite(
            ("ev-1", "PaiementEncaissé", "dos-1"),
            ("ev-2", "TenantOuvert", "dos-1"),
        )
        espion = Espion()
        abonnements = Abonnements()
        for nom in ("PaiementEncaissé", "TenantOuvert"):
            abonnements.abonner(nom, espion)

        publier_un_lot(boite, abonnements, T0)
        assert espion.recus == ["ev-1", "ev-2"]

    def test_plusieurs_abonnes_recoivent_le_meme_evenement(self):
        boite = _boite(("ev-1", "PaiementEncaissé", "dos-1"))
        premier, second = Espion(), Espion()
        abonnements = Abonnements()
        abonnements.abonner("PaiementEncaissé", premier)
        abonnements.abonner("PaiementEncaissé", second)

        publier_un_lot(boite, abonnements, T0)
        assert premier.recus == second.recus == ["ev-1"]

    def test_un_evenement_sans_abonne_est_publie(self):
        """⚠️ Publié, pas mis en échec. Tous les événements n'intéressent
        personne dès le premier jour, et les mettre en échec ferait partir en
        quarantaine des faits parfaitement traités."""
        boite = _boite(("ev-1", "PersonneNEcoute", "dos-1"))
        rapport = publier_un_lot(boite, Abonnements(), T0)

        assert rapport.publies == 1
        assert rapport.sans_abonne == 1
        assert rapport.echecs == 0

    def test_le_lot_est_borne(self):
        """Un arriéré de cent mille chargé d'un coup ferait un passage qui ne
        finit pas. On traite, on valide, on repasse."""
        boite = _boite(*[(f"ev-{i}", "Fait", f"cle-{i}") for i in range(10)])
        rapport = publier_un_lot(boite, Abonnements(), T0, limite=3)

        assert rapport.publies == 3
        assert len(boite.a_publier()) == 7

    def test_rejouer_le_passage_ne_republie_rien(self):
        boite = _boite(("ev-1", "Fait", "dos-1"))
        espion = Espion()
        abonnements = Abonnements()
        abonnements.abonner("Fait", espion)

        publier_un_lot(boite, abonnements, T0)
        rapport = publier_un_lot(boite, abonnements, T0 + timedelta(minutes=1))

        assert rapport.publies == 0
        assert espion.recus == ["ev-1"]


# ── L'ordre par clé, quand ça casse ───────────────────────────────────────────


class TestOrdreParCle:
    """⚠️ **La partie subtile, et celle dont l'absence ne se voit qu'à
    l'incident.**"""

    def test_un_echec_retient_les_suivants_de_la_meme_cle(self):
        """Sinon le rejeu du premier, au tour suivant, arriverait après le
        second, et l'ordre serait inversé au pire moment."""
        boite = _boite(
            ("ev-1", "PaiementEncaissé", "dos-1"),
            ("ev-2", "TenantOuvert", "dos-1"),
        )
        espion = Espion(echoue_sur={"ev-1"})
        abonnements = Abonnements()
        for nom in ("PaiementEncaissé", "TenantOuvert"):
            abonnements.abonner(nom, espion)

        rapport = publier_un_lot(boite, abonnements, T0)

        assert rapport.echecs == 1
        assert rapport.retenus == 1
        assert espion.recus == ["ev-1"], "le second a été remis malgré l'échec du premier"
        assert len(boite.a_publier()) == 2

    def test_les_autres_cles_continuent(self):
        """Un tenant bloqué ne doit pas retenir les quatre-vingt-dix-neuf
        autres."""
        boite = _boite(
            ("ev-1", "Fait", "bloque"),
            ("ev-2", "Fait", "libre"),
            ("ev-3", "Fait", "bloque"),
            ("ev-4", "Fait", "libre"),
        )
        espion = Espion(echoue_sur={"ev-1"})
        abonnements = Abonnements()
        abonnements.abonner("Fait", espion)

        rapport = publier_un_lot(boite, abonnements, T0)

        assert rapport.publies == 2
        assert rapport.echecs == 1
        assert rapport.retenus == 1
        assert espion.recus == ["ev-1", "ev-2", "ev-4"]

    def test_la_reprise_repart_dans_l_ordre(self):
        """Le tour suivant, la cause levée, l'ordre est celui d'origine."""
        boite = _boite(
            ("ev-1", "Fait", "dos-1"),
            ("ev-2", "Fait", "dos-1"),
        )
        espion = Espion(echoue_sur={"ev-1"})
        abonnements = Abonnements()
        abonnements.abonner("Fait", espion)

        publier_un_lot(boite, abonnements, T0)
        espion.echoue_sur = set()
        rapport = publier_un_lot(boite, abonnements, T0 + timedelta(minutes=5))

        assert rapport.publies == 2
        assert espion.recus == ["ev-1", "ev-1", "ev-2"]


# ── Les échecs ────────────────────────────────────────────────────────────────


class TestEchecs:
    def test_un_abonne_qui_leve_ne_fait_pas_echouer_le_passage(self):
        """Le relais existe précisément pour que la panne d'un consommateur
        n'arrête pas les autres."""
        boite = _boite(("ev-1", "Fait", "dos-1"))
        abonnements = Abonnements()
        abonnements.abonner("Fait", Espion(echoue_sur={"ev-1"}))

        rapport = publier_un_lot(boite, abonnements, T0)
        assert rapport.echecs == 1
        assert rapport.demande_un_regard is True

    def test_le_motif_nomme_l_abonne_et_la_cle(self):
        """Un motif qui dit seulement « échec » oblige à chercher lequel, sur
        quel événement, pour quel dossier."""
        boite = _boite(("ev-1", "PaiementEncaissé", "dos-42"))
        abonnements = Abonnements()
        abonnements.abonner("PaiementEncaissé", Espion(echoue_sur={"ev-1"}))

        rapport = publier_un_lot(boite, abonnements, T0)
        motif = rapport.motifs[0]
        assert "PaiementEncaissé" in motif
        assert "dos-42" in motif
        assert "injoignable" in motif

    def test_le_premier_echec_arrete_la_remise_aux_suivants(self):
        """L'événement sera rejoué en entier de toute façon. Continuer rendrait
        le motif ambigu sans rien changer au rejeu."""
        boite = _boite(("ev-1", "Fait", "dos-1"))
        casse, apres = Espion(echoue_sur={"ev-1"}), Espion()
        abonnements = Abonnements()
        abonnements.abonner("Fait", casse)
        abonnements.abonner("Fait", apres)

        publier_un_lot(boite, abonnements, T0)
        assert casse.recus == ["ev-1"]
        assert apres.recus == []

    def test_au_dela_du_seuil_l_evenement_part_en_quarantaine(self):
        """Un événement qu'aucun consommateur n'accepte sature la file et
        retarde tous les autres, dont ceux qui, eux, seraient traités."""
        boite = _boite(("ev-1", "Fait", "dos-1"))
        abonnements = Abonnements()
        abonnements.abonner("Fait", Espion(echoue_sur={"ev-1"}))

        for tour in range(TENTATIVES_AVANT_QUARANTAINE):
            rapport = publier_un_lot(boite, abonnements, T0 + timedelta(minutes=tour))

        assert rapport.mis_en_quarantaine == 1
        assert boite.a_publier() == []
        assert len(boite.en_quarantaine()) == 1

    def test_la_quarantaine_conserve_le_fait_et_son_motif(self):
        """Supprimer effacerait la trace d'un fait qui a bien eu lieu, et l'on
        chercherait longtemps pourquoi un tenant payé n'a jamais été ouvert."""
        boite = _boite(("ev-1", "PaiementEncaissé", "dos-1"))
        abonnements = Abonnements()
        abonnements.abonner("PaiementEncaissé", Espion(echoue_sur={"ev-1"}))
        for tour in range(TENTATIVES_AVANT_QUARANTAINE):
            publier_un_lot(boite, abonnements, T0 + timedelta(minutes=tour))

        mis_de_cote = boite.en_quarantaine()[0]
        assert mis_de_cote.nom == "PaiementEncaissé"
        assert mis_de_cote.dernier_echec
        assert mis_de_cote.tentatives == TENTATIVES_AVANT_QUARANTAINE

    def test_un_lot_vide_ne_demande_aucun_regard(self):
        rapport = publier_un_lot(BoiteDEnvoiMemoire(), Abonnements(), T0)
        assert rapport.traites == 0
        assert rapport.demande_un_regard is False


class TestJournal:
    def test_il_ne_porte_aucun_contenu_d_evenement(self):
        """On journalise des identifiants et des décisions, jamais des
        contenus : un journal circule et n'a pas le régime de protection d'une
        base métier."""
        boite = BoiteDEnvoiMemoire()
        deposer(
            boite, "ev-1", "Fait", "dos-1",
            {"telephone": "+237699112233", "montant": 450000}, T0,
        )
        rapport = publier_un_lot(boite, Abonnements(), T0)
        journal = journal_du_passage(rapport)

        rendu = str(journal)
        assert "699112233" not in rendu
        assert "450000" not in rendu
        assert journal["publies"] == 1


class TestAbonnements:
    def test_un_nom_inconnu_rend_aucun_abonne(self):
        assert Abonnements().pour("Jamais") == ()

    def test_les_noms_sortent_tries(self):
        abonnements = Abonnements()
        for nom in ("Zebre", "Alpha"):
            abonnements.abonner(nom, Espion())
        assert abonnements.noms() == ["Alpha", "Zebre"]
