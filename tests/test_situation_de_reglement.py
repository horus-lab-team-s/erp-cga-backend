"""Qui est en règle, la règle de dépassement, les grâces, les tarifs datés, le mandat.

─────────────────────────────────────────────────────────────────────────────────
18 OCTOBRE 2026, POINTS 4a, 5 ET 9 DE LA LISTE DU GÉRANT

⚠️ CE QUI EST GARDÉ
* d'office (aucune règle fixée), un dossier en retard ne se traite plus ;
* la règle du cabinet tolère un dépassement, une grâce rouvre, sa levée referme ;
* un geste de traitement sur un dossier bloqué rend 423 ; la lecture et l'encaissement
  restent ouverts, pour qu'il puisse régulariser ;
* une révision de tarif vaut pour l'avenir et ne touche aucune période réglée ;
* le mandat « le cabinet paie pour moi » s'accorde par le super administrateur seul.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.contextes.souscription.domaine.honoraires import (
    CanalDeReglement,
    ContratDHonoraires,
    PeriodiciteDHonoraires,
    ReglementDHonoraires,
    RevisionDHonoraires,
    RevisionRefusee,
    echeancier_d_honoraires,
)
from app.contextes.souscription.domaine.situation_de_reglement import (
    REGLE_D_OFFICE,
    EtatDeReglement,
    GraceDeReglement,
    RegleDeDepassement,
    situation_de_reglement,
)
from app.contextes.transverse.api import NIU_DEMO
from tests.conftest import enroler_par_le_courriel, exige_postgresql, ouvrir_une_session

NIU = "M000000000001A"
LE = datetime(2026, 1, 1, 9)


def _contrat(**autres) -> ContratDHonoraires:
    return ContratDHonoraires(
        niu=NIU,
        denomination="ESSAI SARL",
        montant=Decimal("50000"),
        periodicite=PeriodiciteDHonoraires.MENSUELLE,
        debut=date(2026, 1, 1),
        fixe_par="C-001",
        fixe_le=LE,
        **autres,
    )


def _regle(jours: int) -> RegleDeDepassement:
    return RegleDeDepassement(
        delai_jours=jours, motif="règle d'essai", fixee_par="C-010", fixee_le=LE
    )


def _reglement(periode: date) -> ReglementDHonoraires:
    return ReglementDHonoraires(
        identifiant=f"RH-{periode:%Y%m}",
        niu=NIU,
        periode=periode,
        montant=Decimal("50000"),
        canal=CanalDeReglement.GUICHET,
        reference_externe="reçu",
        constate_le=LE,
        constate_par="C-009",
    )


# Janvier réglé ; février exigible le 1er, en retard à partir du 17.
REGLE_JANVIER = [_reglement(date(2026, 1, 1))]


class TestLaSituation:
    def test_sans_contrat_rien_n_est_du(self):
        s = situation_de_reglement(NIU, None, [], REGLE_D_OFFICE, [], date(2026, 3, 1))
        assert s.etat is EtatDeReglement.SANS_CONTRAT and s.traitable

    def test_d_office_le_retard_bloque(self):
        s = situation_de_reglement(
            NIU, _contrat(), REGLE_JANVIER, REGLE_D_OFFICE, [], date(2026, 2, 17)
        )
        assert s.etat is EtatDeReglement.BLOQUE and not s.traitable
        assert s.en_retard_depuis == date(2026, 2, 17)
        # La veille, février était encore « à régler » : rien d'anormal.
        veille = situation_de_reglement(
            NIU, _contrat(), REGLE_JANVIER, REGLE_D_OFFICE, [], date(2026, 2, 16)
        )
        assert veille.etat is EtatDeReglement.EN_REGLE

    def test_la_regle_tolere_puis_bloque(self):
        regle = _regle(14)
        avant = situation_de_reglement(NIU, _contrat(), REGLE_JANVIER, regle, [], date(2026, 3, 2))
        apres = situation_de_reglement(NIU, _contrat(), REGLE_JANVIER, regle, [], date(2026, 3, 3))
        assert avant.etat is EtatDeReglement.EN_RETARD and avant.traitable
        assert apres.etat is EtatDeReglement.BLOQUE and apres.bloque_le == date(2026, 3, 3)

    def test_une_grace_rouvre_et_sa_levee_referme(self):
        grace = GraceDeReglement(
            identifiant="GR-1",
            niu=NIU,
            jusqu_au=date(2026, 3, 31),
            motif="promesse de règlement écrite",
            accordee_par="C-010",
            accordee_le=LE,
        )
        sous_grace = situation_de_reglement(
            NIU, _contrat(), REGLE_JANVIER, REGLE_D_OFFICE, [grace], date(2026, 3, 10)
        )
        assert sous_grace.etat is EtatDeReglement.SOUS_GRACE and sous_grace.traitable
        levee = GraceDeReglement.model_validate(
            {**grace.model_dump(), "levee_le": LE, "levee_par": "C-010"}
        )
        assert (
            situation_de_reglement(
                NIU, _contrat(), REGLE_JANVIER, REGLE_D_OFFICE, [levee], date(2026, 3, 10)
            ).etat
            is EtatDeReglement.BLOQUE
        )


class TestLeTarifDate:
    def _revision(self, a_partir_du: date) -> RevisionDHonoraires:
        return RevisionDHonoraires(
            montant=Decimal("65000"),
            a_partir_du=a_partir_du,
            motif="hausse convenue",
            fixe_par="C-001",
            fixe_le=LE,
        )

    def test_la_revision_vaut_pour_l_avenir_seulement(self):
        contrat = _contrat().reviser(self._revision(date(2026, 3, 1)), REGLE_JANVIER)
        echeances = echeancier_d_honoraires(contrat, REGLE_JANVIER, date(2026, 3, 5), a_venir=1)
        montants = {e.debut: e.montant for e in echeances}
        assert montants[date(2026, 1, 1)] == Decimal("50000")
        assert montants[date(2026, 2, 1)] == Decimal("50000")
        assert montants[date(2026, 3, 1)] == Decimal("65000")
        assert montants[date(2026, 4, 1)] == Decimal("65000")

    def test_elle_ne_remonte_pas_sur_une_periode_reglee(self):
        fevrier_regle = [*REGLE_JANVIER, _reglement(date(2026, 2, 1))]
        with pytest.raises(RevisionRefusee, match="déjà réglée"):
            _contrat().reviser(self._revision(date(2026, 2, 1)), fevrier_regle)
        # Au 15 janvier, elle ne vaut que pour les périodes qui commencent ensuite : le
        # mois de janvier, réglé, garde son prix.
        revise = _contrat().reviser(self._revision(date(2026, 1, 15)), REGLE_JANVIER)
        assert revise.montant_au(date(2026, 1, 1)) == Decimal("50000")
        with pytest.raises(RevisionRefusee, match="postérieure au début"):
            _contrat().reviser(self._revision(date(2026, 1, 1)), [])


# ── Par les routes ──────────────────────────────────────────────────────────────────

SUPER_ADMIN = "h.kamga@cga-brcg.cm"
COMPTABLE = "l.fotso@cga-brcg.cm"  # porte BÂTIMENT PLUS
DIRECTRICE = "b.mballa@cga-brcg.cm"
BATIMENT = NIU_DEMO["BATIMENT"]
MOTIF = "décision du gérant du 18 octobre 2026"

ECRITURE_HORS_EXERCICE = {
    "journal": "AC",
    "exercice": "2099",
    "date_operation": "2099-01-15",
    "libelle": "essai",
    "lignes": [
        {"compte": "601", "libelle": "Achat", "sens": "DEBIT", "montant": "1000"},
        {"compte": "401", "libelle": "Fournisseur", "sens": "CREDIT", "montant": "1000"},
    ],
}


def _en_tant_que(client, courriel: str) -> None:
    client.cookies.clear()
    ouvrir_une_session(client, courriel)


def _renforcer(client, secret: str | None = None, *, decalage: int = 0) -> str:
    from app.contextes.transverse.api import code_attendu
    from app.partage.horloge import maintenant

    secret = secret or enroler_par_le_courriel(client)
    code = code_attendu(secret, maintenant() + timedelta(seconds=decalage))
    renfort = client.post("/transverse/session/renforcement", json={"code": code})
    assert renfort.status_code == 200, renfort.text
    return secret


@exige_postgresql
class TestParLesRoutes:
    def test_bloque_d_office_traite_sous_grace(self, plateforme):
        client = plateforme
        # La démonstration tolère trente jours : Bâtiment Plus, en retard d'un mois, se traite.
        _en_tant_que(client, COMPTABLE)
        situation = client.get(f"/souscription/honoraires/{BATIMENT}").json()["situation"]
        assert situation["etat"] == "EN_RETARD", situation
        assert (
            client.post(
                f"/comptabilite/dossiers/{BATIMENT}/ecritures", json=ECRITURE_HORS_EXERCICE
            ).status_code
            != 423
        )

        # Le super administrateur revient au « d'office » : plus aucun dépassement.
        _en_tant_que(client, SUPER_ADMIN)
        secret = _renforcer(client)
        regle = client.put("/souscription/reglement/regle", json={"delai_jours": 0, "motif": MOTIF})
        assert regle.status_code == 200, regle.text
        situations = client.get("/souscription/reglement/situations").json()
        assert situations[0]["situation"]["etat"] == "BLOQUE"

        _en_tant_que(client, COMPTABLE)
        refus = client.post(
            f"/comptabilite/dossiers/{BATIMENT}/ecritures", json=ECRITURE_HORS_EXERCICE
        )
        assert refus.status_code == 423 and "non en règle" in refus.text, refus.text
        # Il lit toujours le dossier : lire n'est pas traiter.
        assert client.get(f"/souscription/honoraires/{BATIMENT}").status_code == 200

        # Une grâce rouvre le traitement.
        _en_tant_que(client, SUPER_ADMIN)
        _renforcer(client, secret, decalage=30)
        grace = client.post(
            f"/souscription/honoraires/{BATIMENT}/graces",
            json={"jusqu_au": (date.today() + timedelta(days=14)).isoformat(), "motif": MOTIF},
        )
        assert grace.status_code == 201, grace.text
        assert grace.json()["situation"]["etat"] == "SOUS_GRACE"

        _en_tant_que(client, COMPTABLE)
        assert (
            client.post(
                f"/comptabilite/dossiers/{BATIMENT}/ecritures", json=ECRITURE_HORS_EXERCICE
            ).status_code
            != 423
        )

    def test_seul_le_super_administrateur_accorde_une_grace(self, plateforme):
        client = plateforme
        _en_tant_que(client, DIRECTRICE)
        refus = client.post(
            f"/souscription/honoraires/{BATIMENT}/graces",
            json={"jusqu_au": date.today().isoformat(), "motif": MOTIF},
        )
        assert refus.status_code == 403, refus.text

    def test_la_directrice_revise_le_tarif_pour_l_avenir(self, plateforme):
        client = plateforme
        _en_tant_que(client, DIRECTRICE)
        vue = client.get(f"/souscription/honoraires/{BATIMENT}").json()
        future = next(e for e in vue["echeances"] if e["etat"] == "A_VENIR")
        reglee = next(e for e in vue["echeances"] if e["etat"] == "REGLEE")

        revise = client.post(
            f"/souscription/honoraires/{BATIMENT}/contrat/revisions",
            json={"montant": "60000", "a_partir_du": future["debut"], "motif": MOTIF},
        )

        assert revise.status_code == 201, revise.text
        echeances = {e["debut"]: e for e in revise.json()["echeances"]}
        assert Decimal(echeances[future["debut"]]["montant"]) == Decimal("60000")
        assert Decimal(echeances[reglee["debut"]]["montant"]) == Decimal(reglee["montant"])
        sur_le_passe = client.post(
            f"/souscription/honoraires/{BATIMENT}/contrat/revisions",
            json={"montant": "70000", "a_partir_du": reglee["debut"], "motif": MOTIF},
        )
        assert sur_le_passe.status_code == 409, sur_le_passe.text

    def test_le_mandat_de_paiement_s_accorde_par_le_super_administrateur(self, plateforme):
        client = plateforme
        colombe = NIU_DEMO["COLOMBE"]
        _en_tant_que(client, DIRECTRICE)
        corps = {
            "obligations": ["TVA"],
            "a_compter_du": date.today().isoformat(),
            "signe_le": date.today().isoformat(),
            "signataire": "ESSOMBA Marie-Claire, gérante",
            "motif": MOTIF,
        }
        assert (
            client.post(f"/souscription/fonds-de-tiers/{colombe}/mandat", json=corps).status_code
            == 403
        )

        _en_tant_que(client, SUPER_ADMIN)
        _renforcer(client)
        accorde = client.post(f"/souscription/fonds-de-tiers/{colombe}/mandat", json=corps)
        assert accorde.status_code == 201, accorde.text
        assert accorde.json()["obligations_mandatees"] == ["TVA"]
