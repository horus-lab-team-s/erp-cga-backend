"""Les relances automatiques des honoraires (9 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
Réunion du 4 novembre 2026 : « leur faire comprendre, à l'avance, que leur compte sera
bloqué ». Rien ne le disait à l'adhérent tant qu'il n'ouvrait pas son espace.

⚠️ CE QUI EST GARDÉ
* quatre paliers, chacun tiré des règles existantes : exigible, en retard, préavis
  (AVERTI), suspendu ;
* une fois par palier et par échéance, le journal servant de mémoire ;
* pas d'inondation : un passage n'envoie qu'UN message par dossier, le plus grave, et
  tient les autres paliers nouveaux pour couverts ;
* un compte adhérent suspendu ne reçoit rien ; l'adhérent est prévenu dans sa cloche.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app.contextes.souscription.domaine.relances_d_honoraires import (
    PalierDeRelance,
    relance_a_envoyer,
    relances_atteintes,
)
from app.contextes.souscription.domaine.situation_de_reglement import (
    GraceDeReglement,
    RegleDeDepassement,
)
from app.contextes.transverse.api import NIU_DEMO
from tests.conftest import exige_postgresql, ouvrir_une_session
from tests.test_situation_de_reglement import NIU, REGLE_JANVIER, _contrat, _reglement

LE = datetime(2026, 1, 1, 9)


def _regle(**autres) -> RegleDeDepassement:
    return RegleDeDepassement(
        delai_jours=0, motif="règle d'essai", fixee_par="C-010", fixee_le=LE, **autres
    )


def _atteintes(jour: date, reglements=REGLE_JANVIER, graces=(), regle=None):
    return relances_atteintes(_contrat(), reglements, regle or _regle(), list(graces), jour)


def _paliers(jour: date, **autres) -> set[tuple[str, date]]:
    return {(r.palier.value, r.periode) for r in _atteintes(jour, **autres)}


FEVRIER = date(2026, 2, 1)


class TestLesPaliers:
    # Janvier réglé ; février exigible le 1er février, en retard le 17, la suspension
    # au 1er mai (trois mois), le préavis à partir du 16 avril (quinze jours).

    def test_rien_avant_l_exigibilite_ni_pour_une_echeance_reglee(self):
        assert _atteintes(date(2026, 1, 20)) == []
        tout_regle = [*REGLE_JANVIER, _reglement(FEVRIER)]
        assert _atteintes(date(2026, 2, 10), reglements=tout_regle) == []

    def test_exigible_puis_en_retard(self):
        assert _paliers(date(2026, 2, 1)) == {("echeance_a_regler", FEVRIER)}
        assert _paliers(date(2026, 2, 16)) == {("echeance_a_regler", FEVRIER)}
        (retard,) = [r for r in _atteintes(date(2026, 2, 17)) if r.palier.value == "en_retard"]
        assert retard.atteint_le == date(2026, 2, 17)
        assert retard.limite == date(2026, 2, 16)

    def test_le_preavis_puis_la_suspension_portent_la_date(self):
        avant = _paliers(date(2026, 4, 15))
        assert not {p for p, _ in avant} & {"preavis_suspension", "acces_suspendu"}
        (preavis,) = [
            r for r in _atteintes(date(2026, 4, 16)) if r.palier.value == "preavis_suspension"
        ]
        assert preavis.periode == FEVRIER and preavis.suspension_le == date(2026, 5, 1)
        assert preavis.atteint_le == date(2026, 4, 16)
        # Février, mars et avril dus, à 50 000 chacun.
        assert preavis.du == Decimal("150000")
        suspendu = [
            r for r in _atteintes(date(2026, 5, 1)) if r.palier.value == "acces_suspendu"
        ]
        assert [r.suspension_le for r in suspendu] == [date(2026, 5, 1)]
        assert ("preavis_suspension", FEVRIER) not in _paliers(date(2026, 5, 1))

    def test_les_delais_sont_ceux_de_la_regle_du_cabinet(self):
        regle = _regle(acces_client_mois=2, preavis_acces_jours=5)
        paliers = {p for p, _ in _paliers(date(2026, 3, 27), regle=regle)}
        assert "preavis_suspension" in paliers
        assert "acces_suspendu" in {p for p, _ in _paliers(date(2026, 4, 1), regle=regle)}

    def test_une_grace_tait_le_preavis_et_la_suspension_pas_le_retard(self):
        grace = GraceDeReglement(
            identifiant="GR-1",
            niu=NIU,
            jusqu_au=date(2026, 5, 31),
            motif="accord du gérant, paiement promis",
            accordee_par="C-010",
            accordee_le=LE,
        )
        paliers = {p for p, _ in _paliers(date(2026, 5, 10), graces=[grace])}
        assert paliers == {"echeance_a_regler", "en_retard"}


class TestPasDInondation:
    def test_un_retard_ancien_ne_donne_qu_un_message_le_plus_grave(self):
        """Cinq mois d'impayés au premier passage : un message, celui de la suspension."""
        atteintes = _atteintes(date(2026, 6, 20))
        assert len(atteintes) > 5
        choisie, couvertes = relance_a_envoyer(atteintes, deja=set())
        assert choisie is not None and choisie.palier is PalierDeRelance.ACCES_SUSPENDU
        assert {r.cle for r in couvertes} | {choisie.cle} == {r.cle for r in atteintes}

    def test_une_fois_par_palier_et_par_echeance(self):
        premier, couvertes = relance_a_envoyer(_atteintes(date(2026, 2, 1)), deja=set())
        assert premier.palier is PalierDeRelance.ECHEANCE_A_REGLER and not couvertes
        deja = {premier.cle}
        assert relance_a_envoyer(_atteintes(date(2026, 2, 10)), deja) == (None, [])
        # Le retard est un palier nouveau ; il part, une fois.
        retard, _ = relance_a_envoyer(_atteintes(date(2026, 2, 17)), deja)
        assert retard.palier is PalierDeRelance.EN_RETARD
        deja.add(retard.cle)
        assert relance_a_envoyer(_atteintes(date(2026, 2, 20)), deja) == (None, [])

    def test_a_gravite_egale_la_plus_recente(self):
        """Mars et février en retard, rien de dit encore : le retard de mars, le palier courant."""
        regle = _regle(acces_client_mois=6)
        choisie, _ = relance_a_envoyer(_atteintes(date(2026, 3, 20), regle=regle), set())
        assert choisie.palier is PalierDeRelance.EN_RETARD
        assert choisie.periode == date(2026, 3, 1)


# ── Par le travail, en base ─────────────────────────────────────────────────────

BATIMENT = NIU_DEMO["BATIMENT"]
ADHERENT = "jp.nkoa@batimentplus.cm"
DEBUT = date(2027, 1, 1)


def _relancer(jour: date) -> str:
    from app.contextes.souscription.adaptateurs.entrant.travail_des_relances_d_honoraires import (
        relancer_les_honoraires,
    )
    from app.contextes.transverse.api import unite_de_travail
    from app.partage.locataire import etabli

    with etabli("CGA-BRCG"), unite_de_travail("CGA-BRCG"):
        return relancer_les_honoraires(a_la_date=jour)


def _preparer_batiment(**regle) -> None:
    """Un contrat neuf au 1er janvier 2027 (les règlements de la démonstration portent sur
    des périodes antérieures, que cet échéancier ne connaît pas), et la règle d'office."""
    from app.contextes.souscription.adaptateurs.entrant.routes_http import comptoir
    from app.contextes.transverse.api import unite_de_travail
    from app.partage.locataire import etabli

    with etabli("CGA-BRCG"), unite_de_travail("CGA-BRCG"):
        boutique = comptoir()
        actuel = boutique.contrats_honoraires.lire(BATIMENT)
        boutique.contrats_honoraires.enregistrer(
            actuel.model_copy(update={"debut": DEBUT, "revisions": ()})
        )
        boutique.regle_de_depassement.enregistrer(_regle(**regle))


def _recus(code: str) -> list:
    from app.contextes.transverse.api import service_de_notification

    return [m for m in service_de_notification().derniers(code) if m.destinataire == ADHERENT]


@exige_postgresql
class TestLeTravail:
    def test_chaque_palier_une_fois_et_un_seul_message_par_passage(self, plateforme):
        _preparer_batiment()
        _relancer(date(2027, 1, 2))
        (exigible,) = _recus("honoraires.echeance_a_regler")
        assert exigible.contexte["periode"] == "janvier 2027"
        assert exigible.contexte["montant"] == "45 000"
        assert exigible.contexte["limite"] == "16 janvier 2027"
        assert exigible.contexte["lien"].endswith("/fr/mon-espace/honoraires")

        # Rejoué : rien de plus.
        _relancer(date(2027, 1, 2))
        _relancer(date(2027, 1, 10))
        assert len(_recus("honoraires.echeance_a_regler")) == 1

        _relancer(date(2027, 1, 17))
        assert len(_recus("honoraires.en_retard")) == 1

        # Le 17 mars, février et mars sont devenus exigibles puis en retard, et le préavis
        # court : UN message, le préavis, et les autres paliers sont couverts.
        _relancer(date(2027, 3, 17))
        (preavis,) = _recus("honoraires.preavis_suspension")
        assert preavis.contexte["suspension_le"] == "1 avril 2027"
        assert preavis.contexte["jours"] == "15"
        assert preavis.contexte["du"] == "135 000"
        assert len(_recus("honoraires.echeance_a_regler")) == 1
        assert len(_recus("honoraires.en_retard")) == 1
        _relancer(date(2027, 3, 18))
        assert len(_recus("honoraires.preavis_suspension")) == 1

        _relancer(date(2027, 4, 1))
        (suspendu,) = _recus("honoraires.acces_suspendu")
        assert suspendu.contexte["mois"] == "3"
        _relancer(date(2027, 4, 2))
        assert len(_recus("honoraires.acces_suspendu")) == 1

    def test_le_premier_passage_n_inonde_pas(self, plateforme):
        """Un dossier en retard de plusieurs mois, jamais relancé : un seul message."""
        _preparer_batiment()
        _relancer(date(2027, 6, 20))
        recus = [
            m
            for code in (
                "honoraires.echeance_a_regler",
                "honoraires.en_retard",
                "honoraires.preavis_suspension",
                "honoraires.acces_suspendu",
            )
            for m in _recus(code)
        ]
        assert [m.code for m in recus] == ["honoraires.acces_suspendu"]
        # Et le passage suivant n'envoie pas, après coup, ce qu'il a couvert.
        _relancer(date(2027, 6, 21))
        assert len(_recus("honoraires.en_retard")) == 0

    def test_un_compte_suspendu_ne_recoit_rien(self, plateforme):
        from app.contextes.transverse.api import EtatCompte, atelier, unite_de_travail
        from app.partage.locataire import etabli

        _preparer_batiment()
        with etabli("CGA-BRCG"), unite_de_travail("CGA-BRCG"):
            comptes = atelier().comptes
            compte = comptes.par_courriel(ADHERENT)
            comptes.enregistrer(compte.model_copy(update={"etat": EtatCompte.SUSPENDU}))
        _relancer(date(2027, 1, 2))
        assert _recus("honoraires.echeance_a_regler") == []

    def test_l_adherent_la_lit_dans_sa_cloche(self, plateforme):
        client = plateforme
        _preparer_batiment()
        _relancer(date(2027, 1, 17))
        client.cookies.clear()
        ouvrir_une_session(client, ADHERENT)
        cloche = client.get("/transverse/notifications").json()["notifications"]
        assert any(
            n["action"] == "honoraires.relance_adressee"
            and n["titre"] == "Honoraires de janvier 2027 en retard"
            for n in cloche
        ), cloche
