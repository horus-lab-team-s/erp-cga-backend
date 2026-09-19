"""La clôture mensuelle d'un dossier, et le verrou du mois transmis (pas 107).

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE

1. **Les réglages** : un point par défaut bloquant, sauf les pièces attendues ; un fichier
   partiel qui garde les autres points ; les brouillons toujours bloquants ; un mois renvoyé
   jamais verrouillé.
2. **Chaque point**, calculé sur les faits : pièces du mois, caisse en fin de journée, demandes
   ouvertes, écarts en attente du second regard ; leur ordre, et le réglage qui les rend
   informatifs ou les retire.
3. **Le mois en chiffres**.
4. **Le verrou**, déduit des revues, et appliqué aux quatre gestes qui écrivent : saisir,
   corriger, valider, contre-passer. La contre-passation datée du mois ouvert reste le moyen
   de corriger un mois verrouillé.
5. **La transmission** refusée tant qu'un point bloque, ou tant que le mois n'est pas fini.
6. **Les routes** : l'écran de clôture sur le jeu de démonstration, le verrou posé par la
   transmission, levé par le renvoi, reposé par la retransmission ; et sur PostgreSQL.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contextes.comptabilite.adaptateurs.sortant.cloture_mensuelle_yaml import (
    charger_les_reglages_de_la_cloture_mensuelle,
)
from app.contextes.comptabilite.adaptateurs.sortant.depots_revues import (
    DepotRevuesMemoire,
    vider_les_revues,
)
from app.contextes.comptabilite.api import (
    COMPTES_SYSCOHADA,
    JOURNAUX_CABINET,
    BrouillonEcriture,
    DepotEcrituresMemoire,
    EtatEcriture,
    LigneEcriture,
    MoisVerrouille,
    Sens,
    contrepasser_une_ecriture,
    enregistrer_une_ecriture,
    valider_une_ecriture,
    vider_les_ecritures_en_memoire,
)
from app.contextes.comptabilite.application.revue import transmettre_un_mois
from app.contextes.comptabilite.application.tenue_du_journal import (
    CorrectionDeBrouillon,
    corriger_un_brouillon,
    verifier_l_environnement,
)
from app.contextes.comptabilite.domaine.cloture_mensuelle import (
    CodePoint,
    DemandeOuverte,
    EcartEnSuspens,
    PieceDuMois,
    PointDeCloture,
    ReglagesDeLaClotureMensuelle,
    chiffrer_le_mois,
    periode_verrouillee_au,
    periodes_verrouillees,
    points_de_cloture,
)
from app.contextes.comptabilite.domaine.entites import EcritureComptable
from app.contextes.comptabilite.domaine.revue import (
    PassageDeRelais,
    ReglagesDeLEchantillon,
    RevueDeDossier,
    RevueRefusee,
    StatutRevue,
)
from app.contextes.portefeuille.contrats import Exercice
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.main import creer_application
from app.partage.locataire import etabli
from tests.conftest import exige_postgresql, ouvrir_une_session

BATIMENT = "M081234567890P"
COMPTABLE = "l.fotso@cga-brcg.cm"
COMPTABLE_AUTRE = "c.ndongo@cga-brcg.cm"
REVISEUR = "a.bouba@cga-brcg.cm"
OUVERT = Exercice(libelle="2026", ouverture=date(2026, 1, 1), cloture=date(2026, 12, 31))
JUILLET = (date(2026, 7, 1), date(2026, 7, 31))
INSTANT = datetime(2026, 8, 5, 9, 0)
REFERENTIEL = Path(__file__).resolve().parents[2] / "Docs" / "referentiel"


@pytest.fixture(autouse=True)
def neuf():
    from app.contextes.collecte.api import vider_les_magasins_de_la_collecte
    from app.contextes.conformite.api import vider_les_ecarts

    def vider():
        vider_les_revues()
        vider_les_ecritures_en_memoire()
        vider_les_ecarts()
        vider_les_magasins_de_la_collecte()

    reinitialiser_atelier()
    vider()
    yield
    vider()


def _lignes(montant="1000", debit="601", credit="401"):
    return [
        LigneEcriture(compte=debit, libelle="x", sens=Sens.DEBIT, montant=Decimal(montant)),
        LigneEcriture(compte=credit, libelle="x", sens=Sens.CREDIT, montant=Decimal(montant)),
    ]


def _ecriture(numero, jour, *, montant="1000", debit="601", credit="401", etat="VALIDEE"):
    valeurs = dict(
        journal="AC",
        exercice="2026",
        numero=numero,
        date_operation=jour,
        libelle=f"écriture {numero}",
        piece_justificative=f"PJ-{numero}",
        lignes=_lignes(montant, debit, credit),
        etat=EtatEcriture(etat),
    )
    if etat == "VALIDEE":
        valeurs.update(validee_par="C-004", validee_le=INSTANT)
    return EcritureComptable(**valeurs)


def _piece(identifiant, etat, jour, reference=None, recue=None):
    return PieceDuMois(
        identifiant=identifiant,
        reference=reference,
        etat=etat,
        date=jour,
        recue_le=recue or jour,
    )


def _points(**faits):
    du, au = faits.pop("periode", JUILLET)
    return points_de_cloture(
        du=du,
        au=au,
        points_de_la_revue=faits.pop("revue", []),
        ecritures_de_l_exercice=faits.pop("ecritures", []),
        pieces=faits.pop("pieces", []),
        demandes=faits.pop("demandes", []),
        ecarts_en_suspens=faits.pop("ecarts", []),
        reglages=faits.pop("reglages", ReglagesDeLaClotureMensuelle()),
    )


def _point(points, code) -> PointDeCloture:
    return next(p for p in points if p.code is code)


# ── 1. Les réglages ───────────────────────────────────────────────────────────


class TestLesReglages:
    def test_par_defaut_tout_bloque_sauf_les_pieces_attendues(self):
        reglages = ReglagesDeLaClotureMensuelle()
        assert {c for c, r in reglages.points.items() if not r.bloquant} == {
            CodePoint.PIECES_ATTENDUES
        }
        assert all(r.actif for r in reglages.points.values())
        assert reglages.statuts_qui_verrouillent == {StatutRevue.TRANSMISE, StatutRevue.VALIDEE}

    def test_le_fichier_du_referentiel_dit_la_meme_chose_que_les_defauts(self):
        lus = charger_les_reglages_de_la_cloture_mensuelle(REFERENTIEL)
        assert lus.source == "cloture_mensuelle/reglages.yaml"
        assert lus.model_dump(exclude={"source"}) == ReglagesDeLaClotureMensuelle().model_dump(
            exclude={"source"}
        )

    def test_sans_fichier_les_defauts(self, tmp_path):
        assert charger_les_reglages_de_la_cloture_mensuelle(tmp_path).source == "valeurs par défaut"

    def test_un_fichier_partiel_garde_les_autres_points(self):
        reglages = ReglagesDeLaClotureMensuelle(points={"TRESORERIE": {"bloquant": False}})
        assert not reglages.points[CodePoint.TRESORERIE].bloquant
        assert reglages.points[CodePoint.RAPPROCHEMENT].bloquant
        assert not reglages.points[CodePoint.PIECES_ATTENDUES].bloquant
        assert len(reglages.points) == len(CodePoint)

    @pytest.mark.parametrize(
        "mauvais",
        [
            {"points": {"BROUILLONS": {"bloquant": False}}},
            {"points": {"BROUILLONS": {"actif": False}}},
            {"points": {"INVENTE": {"bloquant": True}}},
            {"points": {"TRESORERIE": {"bloquant": True, "coche": True}}},
            {"statuts_qui_verrouillent": ["TRANSMISE", "RENVOYEE"]},
            {"cle_inconnue": 1},
        ],
    )
    def test_un_reglage_qui_mentirait_est_refuse(self, mauvais):
        with pytest.raises(ValidationError):
            ReglagesDeLaClotureMensuelle.model_validate(mauvais)


# ── 2. Les points ─────────────────────────────────────────────────────────────


class TestLesPoints:
    def test_l_ordre_et_le_reglage_qui_retire_ou_rend_informatif(self):
        points = _points()
        assert [p.code for p in points] == [
            CodePoint.PIECES_A_COMPTABILISER,
            CodePoint.TRESORERIE,
            CodePoint.PIECES_ATTENDUES,
            CodePoint.ECARTS_EN_SUSPENS,
        ]
        reglages = ReglagesDeLaClotureMensuelle(
            points={"TRESORERIE": {"actif": False}, "PIECES_A_COMPTABILISER": {"bloquant": False}}
        )
        pieces = [_piece("PJ-1", "LUE", date(2026, 7, 3))]
        points = _points(pieces=pieces, reglages=reglages)
        assert CodePoint.TRESORERIE not in {p.code for p in points}
        pieces_point = _point(points, CodePoint.PIECES_A_COMPTABILISER)
        assert not pieces_point.traite and not pieces_point.bloquant and not pieces_point.bloque

    def test_les_pieces_du_mois_a_comptabiliser(self):
        pieces = [
            _piece("PJ-1", "COMPTABILISEE", date(2026, 7, 3), "F-1"),
            _piece("PJ-2", "LUE", date(2026, 7, 31), "F-2"),
            _piece("PJ-3", "RECUE", date(2026, 7, 1)),
            # Classée sans écriture (un doublon, un relevé) : traitée.
            _piece("PJ-4", "ARCHIVEE", date(2026, 7, 10)),
            # Datée de juin, reçue en juillet : c'est la clôture de juin qui la réclame.
            _piece("PJ-5", "LUE", date(2026, 6, 30), recue=date(2026, 7, 2)),
            _piece("PJ-6", "RAPPROCHEE", date(2026, 8, 1)),
        ]
        point = _point(_points(pieces=pieces), CodePoint.PIECES_A_COMPTABILISER)
        assert not point.traite and point.bloque
        assert point.references == ("PJ-2", "PJ-3")
        assert point.titre == "2 pièces du mois à comptabiliser ou classer"
        assert "4 pièces du mois · 2 traitées" in point.detail
        assert "F-2 (lue)" in point.detail and "PJ-3 (recue)" in point.detail
        tout_fait = _point(_points(pieces=pieces[:1]), CodePoint.PIECES_A_COMPTABILISER)
        assert tout_fait.traite and tout_fait.titre.startswith("Toutes")

    def test_la_caisse_se_juge_en_fin_de_journee_et_en_cumul(self):
        # 1 000 entrés en juin ; le 3 juillet, 1 500 sortis et 800 entrés : 300 le soir.
        ecritures = [
            _ecriture(1, date(2026, 6, 10), montant="1000", debit="571", credit="101"),
            _ecriture(2, date(2026, 7, 3), montant="1500", debit="601", credit="571"),
            _ecriture(3, date(2026, 7, 3), montant="800", debit="571", credit="701"),
        ]
        point = _point(_points(ecritures=ecritures), CodePoint.TRESORERIE)
        assert point.traite, point.detail
        # Le 20, 400 sortis : -100 le soir.
        negatif = [
            *ecritures,
            _ecriture(4, date(2026, 7, 20), montant="400", debit="601", credit="571"),
        ]
        point = _point(_points(ecritures=negatif), CodePoint.TRESORERIE)
        assert not point.traite and point.references == ("571",)
        assert "571 au 20/07 (-100)" in point.detail
        # Un brouillon ne compte pas, ni une écriture d'après la fin du mois.
        ignores = [
            *ecritures,
            _ecriture(
                5, date(2026, 7, 20), montant="400", debit="601", credit="571", etat="BROUILLON"
            ),
            _ecriture(6, date(2026, 8, 2), montant="9000", debit="601", credit="571"),
        ]
        assert _point(_points(ecritures=ignores), CodePoint.TRESORERIE).traite
        sans = _point(_points(ecritures=[_ecriture(7, date(2026, 7, 2))]), CodePoint.TRESORERIE)
        assert sans.traite and sans.detail == "aucun compte de caisse mouvementé"

    def test_les_pieces_attendues_informent_sans_bloquer(self):
        demandes = [
            DemandeOuverte(
                identifiant="DEM-1",
                motif="Relevé",
                demandee_le=date(2026, 7, 2),
                derniere_relance=date(2026, 8, 8),
            ),
            DemandeOuverte(identifiant="DEM-2", motif="Facture", demandee_le=date(2026, 7, 20)),
            # Demandée après la fin du mois : elle ne concerne pas sa clôture.
            DemandeOuverte(identifiant="DEM-3", motif="Contrat", demandee_le=date(2026, 8, 3)),
        ]
        point = _point(_points(demandes=demandes), CodePoint.PIECES_ATTENDUES)
        assert not point.traite and not point.bloque
        assert point.references == ("DEM-1", "DEM-2")
        assert point.titre == "2 pièces attendues non reçues"
        assert point.detail == "dernière relance le 08/08/2026 · le mois sera transmis sans elles"

    def test_les_ecarts_en_attente_sur_une_piece_du_mois(self):
        pieces = [
            _piece("PJ-1", "COMPTABILISEE", date(2026, 7, 3), "F-1"),
            _piece("PJ-2", "COMPTABILISEE", date(2026, 8, 3), "F-2"),
        ]
        ecarts = [
            EcartEnSuspens(
                identifiant="ECT-1", reference_document="F-1", code_regle="R1", severite="MAJEUR"
            ),
            EcartEnSuspens(
                identifiant="ECT-2", reference_document="F-2", code_regle="R1", severite="MAJEUR"
            ),
        ]
        point = _point(_points(pieces=pieces, ecarts=ecarts), CodePoint.ECARTS_EN_SUSPENS)
        assert not point.traite and point.bloque
        assert point.references == ("ECT-1",)
        assert "F-1 (R1, majeur)" in point.detail


class TestLesChiffresDuMois:
    def test_achats_nets_tva_rejetee_et_pieces_recues(self):
        ecritures = [
            _ecriture(1, date(2026, 7, 3), montant="1000", debit="604"),
            # Un avoir : le compte d'achat est crédité, les achats nets baissent.
            _ecriture(2, date(2026, 7, 9), montant="200", debit="401", credit="6019"),
            _ecriture(3, date(2026, 7, 9), montant="5000", debit="622"),
            _ecriture(4, date(2026, 7, 9), montant="7000", debit="604", etat="BROUILLON"),
            _ecriture(5, date(2026, 8, 1), montant="7000", debit="604"),
        ]
        pieces = [
            _piece("PJ-1", "LUE", date(2026, 6, 28), recue=date(2026, 7, 1)),
            _piece("PJ-2", "LUE", date(2026, 7, 28), recue=date(2026, 8, 1)),
        ]
        chiffres = chiffrer_le_mois(
            du=JUILLET[0],
            au=JUILLET[1],
            ecritures_de_l_exercice=ecritures,
            pieces=pieces,
            tva_rejetee_par_ecriture=lambda e: Decimal(10) if e.numero == 3 else Decimal(0),
            reglages=ReglagesDeLaClotureMensuelle(),
        )
        assert chiffres.ecritures_validees == 3
        assert chiffres.achats_du_mois == Decimal(800)
        assert chiffres.tva_rejetee == Decimal(10)
        assert chiffres.pieces_recues == 1


# ── 3. Le verrou ──────────────────────────────────────────────────────────────


def _revue(statut, du=JUILLET[0], au=JUILLET[1], identifiant="REV-JUILLET"):
    historique = [
        PassageDeRelais(
            statut=StatutRevue.TRANSMISE, par="Léonard FOTSO", compte="C-004", le=INSTANT
        )
    ]
    valeurs = {}
    if statut is not StatutRevue.TRANSMISE:
        historique.append(
            PassageDeRelais(
                statut=statut, par="Aïcha BOUBA", compte="C-006", le=datetime(2026, 8, 7, 10)
            )
        )
    if statut is StatutRevue.VALIDEE:
        valeurs.update(validee_par="Aïcha BOUBA")
    return RevueDeDossier(
        identifiant=identifiant,
        dossier=BATIMENT,
        exercice="2026",
        du=du,
        au=au,
        statut=statut,
        transmise_par_compte="C-004",
        transmise_par="Léonard FOTSO",
        echantillon=[],
        ecritures_du_mois=1,
        historique=historique,
        **valeurs,
    )


class TestLesPeriodesVerrouillees:
    def test_transmis_et_valide_verrouillent_renvoye_non(self):
        revues = [
            _revue(StatutRevue.TRANSMISE),
            _revue(StatutRevue.RENVOYEE, date(2026, 5, 1), date(2026, 5, 31), "REV-MAI"),
            _revue(StatutRevue.VALIDEE, date(2026, 6, 1), date(2026, 6, 30), "REV-JUIN"),
        ]
        periodes = periodes_verrouillees(revues, ReglagesDeLaClotureMensuelle())
        assert [p.revue for p in periodes] == ["REV-JUIN", "REV-JUILLET"]
        juin = periodes[0]
        assert juin.par == "Aïcha BOUBA" and juin.depuis == datetime(2026, 8, 7, 10)
        assert periode_verrouillee_au(date(2026, 7, 31), periodes).revue == "REV-JUILLET"
        assert periode_verrouillee_au(date(2026, 6, 1), periodes).revue == "REV-JUIN"
        assert periode_verrouillee_au(date(2026, 5, 15), periodes) is None
        assert periode_verrouillee_au(date(2026, 8, 1), periodes) is None

    def test_un_cabinet_peut_rouvrir_les_mois_valides(self):
        reglages = ReglagesDeLaClotureMensuelle(statuts_qui_verrouillent=["TRANSMISE"])
        revues = [
            _revue(StatutRevue.VALIDEE),
            _revue(StatutRevue.TRANSMISE, date(2026, 8, 1), date(2026, 8, 31), "REV-AOUT"),
        ]
        assert [p.revue for p in periodes_verrouillees(revues, reglages)] == ["REV-AOUT"]


@pytest.fixture
def registre():
    return DepotEcrituresMemoire(BATIMENT)


def _saisir(registre, jour, verrous=()):
    return enregistrer_une_ecriture(
        BrouillonEcriture(
            journal="AC",
            exercice="2026",
            date_operation=jour,
            libelle="Achat",
            piece_justificative="PJ-X",
            lignes=_lignes(),
        ),
        journaux=JOURNAUX_CABINET,
        plan=COMPTES_SYSCOHADA,
        exercice=OUVERT,
        periodes_verrouillees=verrous,
        depot=registre,
        par="C-004",
    )


def _valider(registre, cle, verrous=()):
    return valider_une_ecriture(
        cle, exercice=OUVERT, periodes_verrouillees=verrous, depot=registre, par="C-004", le=INSTANT
    )


class TestLeVerrouAuxQuatreGestes:
    def _verrous(self, statut=StatutRevue.TRANSMISE):
        return periodes_verrouillees([_revue(statut)], ReglagesDeLaClotureMensuelle())

    def test_saisir_dans_un_mois_verrouille_est_refuse_et_dit_quoi_faire(self, registre):
        with pytest.raises(MoisVerrouille) as refus:
            _saisir(registre, date(2026, 7, 31), self._verrous())
        message = str(refus.value)
        assert (
            "du 01/07/2026 au 31/07/2026, transmis au réviseur (Léonard FOTSO, le 05/08/2026)"
            in message
        )
        assert "demander au réviseur de renvoyer le mois" in message
        with pytest.raises(MoisVerrouille) as refus:
            _saisir(registre, date(2026, 7, 1), self._verrous(StatutRevue.VALIDEE))
        assert "validé par le réviseur" in str(refus.value)
        assert "renvoyer" not in str(refus.value)
        # Les jours qui bordent le mois restent ouverts.
        assert _saisir(registre, date(2026, 8, 1), self._verrous()).numero == 1
        assert _saisir(registre, date(2026, 6, 30), self._verrous()).numero == 2
        # Mois renvoyé : le comptable corrige.
        assert _saisir(registre, date(2026, 7, 15), self._verrous(StatutRevue.RENVOYEE)).numero == 3

    def test_la_reprise_rencontre_le_meme_controle(self):
        brouillon = BrouillonEcriture(
            journal="AC",
            exercice="2026",
            date_operation=date(2026, 7, 5),
            libelle="Reprise",
            lignes=_lignes(),
        )
        with pytest.raises(MoisVerrouille):
            verifier_l_environnement(
                brouillon,
                journaux=JOURNAUX_CABINET,
                plan=COMPTES_SYSCOHADA,
                exercice=OUVERT,
                periodes_verrouillees=self._verrous(),
            )

    def test_corriger_ni_vers_ni_depuis_un_mois_verrouille(self, registre):
        aout = _saisir(registre, date(2026, 8, 3))
        juillet = _saisir(registre, date(2026, 7, 20))

        def correction(jour):
            return CorrectionDeBrouillon(
                date_operation=jour,
                libelle="Corrigée",
                piece_justificative="PJ-X",
                lignes=_lignes(),
            )

        def corriger(cle, jour):
            return corriger_un_brouillon(
                cle,
                correction(jour),
                journaux=JOURNAUX_CABINET,
                plan=COMPTES_SYSCOHADA,
                exercice=OUVERT,
                periodes_verrouillees=self._verrous(),
                depot=registre,
                par="C-004",
            )

        with pytest.raises(MoisVerrouille):
            corriger(aout.cle, date(2026, 7, 30))
        # Sortir de juillet un brouillon de juillet le changerait autant qu'y en ajouter un.
        with pytest.raises(MoisVerrouille):
            corriger(juillet.cle, date(2026, 8, 30))
        assert corriger(aout.cle, date(2026, 8, 30)).libelle == "Corrigée"

    def test_valider_dans_un_mois_verrouille_est_refuse(self, registre):
        brouillon = _saisir(registre, date(2026, 7, 20))
        with pytest.raises(MoisVerrouille):
            _valider(registre, brouillon.cle, self._verrous())
        assert _valider(registre, brouillon.cle).etat is EtatEcriture.VALIDEE

    def test_la_contre_passation_datee_du_mois_ouvert_corrige_le_mois_verrouille(self, registre):
        origine = _valider(registre, _saisir(registre, date(2026, 7, 20)).cle)

        def contrepasser(jour):
            return contrepasser_une_ecriture(
                origine.cle,
                motif="Compte d'immobilisation, pas de charge.",
                jour=jour,
                exercice=OUVERT,
                periodes_verrouillees=self._verrous(StatutRevue.VALIDEE),
                depot=registre,
                par="C-004",
            )

        with pytest.raises(MoisVerrouille) as refus:
            contrepasser(date(2026, 7, 31))
        assert "Dater la contre-passation après le 31/07/2026" in str(refus.value)
        inverse = contrepasser(date(2026, 8, 1))
        assert inverse.date_operation == date(2026, 8, 1)
        assert inverse.ecriture_contrepassee == origine.cle


# ── 4. La transmission ───────────────────────────────────────────────────────


def _transmettre(points, au=JUILLET[1], le=INSTANT):
    return transmettre_un_mois(
        dossier=BATIMENT,
        exercice="2026",
        du=JUILLET[0],
        au=au,
        ecritures_de_l_exercice=[_ecriture(1, date(2026, 7, 3))],
        reglages=ReglagesDeLEchantillon(),
        par="Léonard FOTSO",
        compte="C-004",
        le=le,
        message=None,
        depot=DepotRevuesMemoire(),
        points_de_cloture=points,
    )


class TestLaTransmission:
    @pytest.fixture(autouse=True)
    def _cabinet(self):
        with etabli("CGA-BRCG"):
            yield

    def test_un_point_bloquant_refuse_et_se_nomme_un_point_informatif_non(self):
        pieces = [_piece("PJ-2", "LUE", date(2026, 7, 31), "F-2")]
        demandes = [
            DemandeOuverte(identifiant="DEM-1", motif="Relevé", demandee_le=date(2026, 7, 2))
        ]
        points = _points(pieces=pieces, demandes=demandes)
        with pytest.raises(RevueRefusee) as refus:
            _transmettre(points)
        assert str(refus.value).startswith(
            "1 point bloquant à traiter avant de transmettre : 1 pièce du mois à comptabiliser"
        )
        assert "attendue" not in str(refus.value)
        assert _transmettre(_points(demandes=demandes)).statut is StatutRevue.TRANSMISE

    def test_un_mois_pas_fini_ne_se_transmet_pas(self):
        with pytest.raises(RevueRefusee, match="n'est pas finie"):
            _transmettre([], le=datetime(2026, 7, 31, 18))
        assert _transmettre([], le=datetime(2026, 8, 1, 8)).statut is StatutRevue.TRANSMISE


# ── 5. Les routes ─────────────────────────────────────────────────────────────


def _client(courriel: str) -> TestClient:
    client = TestClient(creer_application())
    reponse = client.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert reponse.status_code == 200, reponse.text
    return client


@pytest.fixture
def cloture_sans_point_bloquant(monkeypatch):
    """Juillet, au jeu de démonstration, a des pièces à comptabiliser : il ne se transmet pas
    (voir `TestLEcranDeCloture`). Pour éprouver le verrou, les points restent calculés mais
    informatifs, comme un cabinet le réglerait au référentiel."""
    from app.contextes.comptabilite.adaptateurs.entrant import routes_cloture_mensuelle

    informatifs = ReglagesDeLaClotureMensuelle(
        points={code.value: {"bloquant": code is CodePoint.BROUILLONS} for code in CodePoint}
    )
    monkeypatch.setattr(routes_cloture_mensuelle, "_reglages", lambda: informatifs)


def _saisie(jour: str) -> dict:
    return {
        "journal": "AC",
        "exercice": "2026",
        "date_operation": jour,
        "libelle": "Achat de fournitures",
        "piece_justificative": "PJ-VERROU",
        "lignes": [
            {"compte": "601", "libelle": "Achat", "sens": "DEBIT", "montant": "25000"},
            {"compte": "401", "libelle": "Fournisseur", "sens": "CREDIT", "montant": "25000"},
        ],
    }


ECRAN = f"/comptabilite/dossiers/{BATIMENT}/clotures/2026-07"
REVUES = f"/comptabilite/dossiers/{BATIMENT}/revues"
REVUE = f"{REVUES}/REV-20260701-20260731"
ECRITURES = f"/comptabilite/dossiers/{BATIMENT}/ecritures"


class TestLEcranDeCloture:
    def test_juillet_de_la_demonstration_ne_se_transmet_pas_encore(self):
        comptable = _client(COMPTABLE)
        reponse = comptable.get(ECRAN)
        assert reponse.status_code == 200, reponse.text
        vue = reponse.json()
        assert vue["denomination"] and vue["du"] == "2026-07-01" and vue["au"] == "2026-07-31"
        assert [p["code"] for p in vue["points"]] == [
            "PIECES_A_COMPTABILISER",
            "BROUILLONS",
            "RAPPROCHEMENT",
            "TRESORERIE",
            "EQUILIBRE",
            "NUMEROTATION",
            "PIECES_ATTENDUES",
            "ECARTS_EN_SUSPENS",
        ]
        pieces = vue["points"][0]
        assert pieces["traite"] is False and pieces["bloquant"] is True
        assert pieces["titre"] == "4 pièces du mois à comptabiliser ou classer"
        assert vue["traites"] == sum(p["traite"] for p in vue["points"])
        assert (
            vue["bloquants_restants"]
            == sum(p["bloquant"] and not p["traite"] for p in vue["points"])
            >= 1
        )
        assert vue["mois_termine"] is True and vue["transmissible"] is False
        assert vue["revue"] is None and vue["verrou"] is None
        assert vue["reviseurs"] == ["Aïcha BOUBA"]
        assert vue["chiffres"]["pieces_recues"] == 7
        assert vue["chiffres"]["ecritures_validees"] == 3
        assert vue["source_des_reglages"] == "cloture_mensuelle/reglages.yaml"

        refus = comptable.post(REVUES, json={"du": "2026-07-01", "au": "2026-07-31"})
        assert refus.status_code == 422
        assert "point bloquant à traiter avant de transmettre" in refus.json()["detail"]
        assert "4 pièces du mois à comptabiliser ou classer" in refus.json()["detail"]

    def test_une_piece_compte_au_mois_de_son_document_pas_de_sa_reception(self):
        comptable = _client(COMPTABLE)
        lue = comptable.post(
            "/collecte/pieces/PJ-2026-0028/lecture",
            json={
                "type": "FACTURE_ACHAT",
                "reference_document": "F-2026-0499",
                "date_document": "2026-07-30",
                "montant_ttc": "118000",
                "emetteur": "QUINCAILLERIE DU WOURI",
            },
        )
        assert lue.status_code == 200, lue.text
        # Reçue le 5 août, datée du 30 juillet : c'est la clôture de juillet qui la réclame.
        juillet = comptable.get(ECRAN).json()["points"][0]["references"]
        aout = comptable.get(f"/comptabilite/dossiers/{BATIMENT}/clotures/2026-08").json()
        assert "PJ-2026-0028" in juillet
        assert "PJ-2026-0028" not in aout["points"][0]["references"]
        assert aout["chiffres"]["pieces_recues"] == 1

    def test_un_ecart_en_attente_bloque_puis_ne_bloque_plus_une_fois_tranche(self):
        reviseur = _client(REVISEUR)
        propose = reviseur.post(
            "/conformite/pieces/F-2026-0412/ecarts",
            json={
                "code_regle": "FAC-ACH-007",
                "motif": "Règlement par virement, attesté par le relevé bancaire déposé.",
            },
        )
        assert propose.status_code == 200, propose.text
        ecart = propose.json()["ecart"]["identifiant"]

        def point():
            vue = _client(COMPTABLE).get(ECRAN).json()
            return next(p for p in vue["points"] if p["code"] == "ECARTS_EN_SUSPENS")

        en_attente = point()
        assert en_attente["traite"] is False and en_attente["references"] == [ecart]
        confirme = _client("r.ebolo@cga-brcg.cm").post(
            f"/conformite/pieces/F-2026-0412/ecarts/{ecart}/second-regard",
            json={"decision": "CONFIRMER", "motif": "Relevé bancaire relu : virement du 30/06."},
        )
        assert confirme.status_code == 200, confirme.text
        # Effectif : tranché, il ne suspend plus rien.
        assert point()["traite"] is True

    def test_perimetre_format_et_mois_en_cours(self):
        assert _client(COMPTABLE_AUTRE).get(ECRAN).status_code == 404
        comptable = _client(COMPTABLE)
        assert (
            comptable.get(f"/comptabilite/dossiers/{BATIMENT}/clotures/2026-13").status_code == 422
        )
        en_cours = comptable.get(
            f"/comptabilite/dossiers/{BATIMENT}/clotures/{date.today():%Y-%m}"
        ).json()
        assert en_cours["mois_termine"] is False and en_cours["transmissible"] is False

    @pytest.mark.usefixtures("cloture_sans_point_bloquant")
    def test_transmettre_verrouille_renvoyer_rouvre_retransmettre_reverrouille(self):
        comptable, reviseur = _client(COMPTABLE), _client(REVISEUR)
        assert comptable.post(ECRITURES, json=_saisie("2026-07-28")).status_code == 201
        # Le brouillon bloque toujours, quels que soient les réglages.
        refus = comptable.post(REVUES, json={"du": "2026-07-01", "au": "2026-07-31"})
        assert refus.status_code == 422 and "brouillon" in refus.json()["detail"]
        vider_les_ecritures_en_memoire()

        assert (
            comptable.post(REVUES, json={"du": "2026-07-01", "au": "2026-07-31"}).status_code == 201
        )
        vue = comptable.get(ECRAN).json()
        assert vue["verrou"]["statut"] == "TRANSMISE" and vue["verrou"]["par"] == "Léonard FOTSO"
        assert vue["revue"]["identifiant"] == "REV-20260701-20260731"
        assert vue["transmissible"] is False

        refus = comptable.post(ECRITURES, json=_saisie("2026-07-28"))
        assert refus.status_code == 409
        assert "elle est verrouillée" in refus.json()["detail"]
        assert comptable.post(ECRITURES, json=_saisie("2026-08-03")).status_code == 201

        remarque = {
            "nature": "COMPTE",
            "reference": "401",
            "texte": "Ce fournisseur est-il le bon ?",
        }
        assert reviseur.post(f"{REVUE}/remarques", json=remarque).status_code == 200
        assert reviseur.post(f"{REVUE}/renvoi", json={}).status_code == 200
        assert comptable.get(ECRAN).json()["verrou"] is None
        corrigee = comptable.post(ECRITURES, json=_saisie("2026-07-29"))
        assert corrigee.status_code == 201, corrigee.text
        numero = corrigee.json()["numero"]
        # Retransmettre avec un brouillon est refusé ; validé, le mois se retransmet et se
        # reverrouille.
        assert (
            comptable.post(
                f"{REVUE}/remarques/1/reponse", json={"reponse": "Oui, vérifié."}
            ).status_code
            == 200
        )
        assert comptable.post(f"{REVUE}/retransmission", json={}).status_code == 422
        validation = comptable.post(f"{ECRITURES}/2026/AC/{numero}/validation")
        assert validation.status_code == 200, validation.text
        assert comptable.post(f"{REVUE}/retransmission", json={}).status_code == 200
        assert comptable.get(ECRAN).json()["verrou"]["statut"] == "TRANSMISE"
        assert comptable.post(ECRITURES, json=_saisie("2026-07-30")).status_code == 409

        assert reviseur.post(f"{REVUE}/remarques/1/cloture").status_code == 200
        assert reviseur.post(f"{REVUE}/validation").status_code == 200
        verrou = comptable.get(ECRAN).json()["verrou"]
        assert verrou["statut"] == "VALIDEE" and verrou["par"] == "Aïcha BOUBA"
        # Validé : la contre-passation datée de juillet est refusée, datée d'août elle passe.
        contre = f"{ECRITURES}/2026/AC/{numero}/contre-passation"
        motif = {"motif": "Fournisseur erroné, constaté en août."}
        refus = comptable.post(contre, json={**motif, "date_operation": "2026-07-31"})
        assert refus.status_code == 409 and "après le 31/07/2026" in refus.json()["detail"]
        assert (
            comptable.post(contre, json={**motif, "date_operation": "2026-08-04"}).status_code
            == 201
        )


# ── 6. Le traitement des pièces, que la clôture a mis au jour ────────────────


def _piece_recue(identifiant, reference, type_="FACTURE_ACHAT", etat="LUE"):
    from app.contextes.collecte.api import CanalDepot, EtatPiece, PieceJustificative, TypePiece

    return PieceJustificative(
        identifiant=identifiant,
        entreprise=BATIMENT,
        canal=CanalDepot.DEPOT_CABINET,
        depose_le=date(2026, 7, 3),
        recue_le=datetime(2026, 7, 3, 9),
        type=TypePiece(type_),
        etat=EtatPiece(etat),
        reference_document=reference,
        date_document=date(2026, 7, 2),
        montant_ttc=Decimal(1000),
        **({"reference_ecriture": "2026/AC/000001"} if etat == "COMPTABILISEE" else {}),
    )


class TestLeTraitementDesPieces:
    @pytest.fixture
    def pieces(self):
        from app.contextes.collecte.api import DepotPiecesMemoire

        with etabli("CGA-BRCG"):
            yield DepotPiecesMemoire()

    def test_la_piece_citee_par_son_identifiant_ou_sa_reference_est_comptabilisee(self, pieces):
        from app.contextes.collecte.api import comptabiliser_la_piece

        pieces.enregistrer(_piece_recue("PJ-1", "F-1"))
        pieces.enregistrer(_piece_recue("PJ-2", "F-2"))
        une = comptabiliser_la_piece(
            pieces, entreprise=BATIMENT, piece_justificative="PJ-1", cle_ecriture="2026/AC/000007"
        )
        assert une.etat.value == "COMPTABILISEE" and une.reference_ecriture == "2026/AC/000007"
        autre = comptabiliser_la_piece(
            pieces, entreprise=BATIMENT, piece_justificative="F-2", cle_ecriture="2026/AC/000008"
        )
        assert autre.identifiant == "PJ-2"
        assert pieces.par_identifiant("PJ-2").etat.value == "COMPTABILISEE"
        # Déjà traitée : plus candidate. Un autre dossier : jamais.
        assert (
            comptabiliser_la_piece(
                pieces, entreprise=BATIMENT, piece_justificative="PJ-1", cle_ecriture="X"
            )
            is None
        )
        assert (
            comptabiliser_la_piece(
                pieces, entreprise="M071122334455J", piece_justificative="F-2", cle_ecriture="X"
            )
            is None
        )

    def test_un_doublon_non_classe_ou_un_releve_ne_se_rattachent_pas(self, pieces):
        from app.contextes.collecte.api import comptabiliser_la_piece

        pieces.enregistrer(_piece_recue("PJ-1", "F-1"))
        pieces.enregistrer(_piece_recue("PJ-9", "F-1"))
        pieces.enregistrer(_piece_recue("PJ-3", "REL-07", type_="RELEVE_BANCAIRE"))
        for citee in ("F-1", "REL-07", None, "INCONNUE"):
            assert (
                comptabiliser_la_piece(
                    pieces, entreprise=BATIMENT, piece_justificative=citee, cle_ecriture="X"
                )
                is None
            )
        assert {p.etat.value for p in pieces.du_dossier(BATIMENT)} == {"LUE"}

    def test_classer_exige_un_motif_et_une_piece_en_attente(self, pieces):
        from app.contextes.collecte.api import TransitionRefusee, classer_une_piece

        piece = _piece_recue("PJ-9", "F-1")
        pieces.enregistrer(piece)
        with pytest.raises(TransitionRefusee, match="10 caractères"):
            classer_une_piece(piece, motif=" doublon  ", pieces=pieces)
        classee = classer_une_piece(piece, motif="Doublon de PJ-2026-0001", pieces=pieces)
        assert classee.etat.value == "ARCHIVEE"
        assert pieces.par_identifiant("PJ-9").motif_archivage == "Doublon de PJ-2026-0001"
        with pytest.raises(TransitionRefusee, match="déjà classée"):
            classer_une_piece(classee, motif="Doublon de PJ-2026-0001", pieces=pieces)


COLOMBE = "M071122334455J"


class TestUnMoisQuiSeClotVraiment:
    """Sans aucun réglage assoupli : le mois de juillet de LA COLOMBE, bloqué par deux pièces
    lues, se clôt quand l'une est classée et l'autre comptabilisée par la validation de son
    écriture. C'est le chemin que la production emprunte."""

    def test_classer_valider_puis_transmettre(self):
        comptable = _client(COMPTABLE)
        ecran = f"/comptabilite/dossiers/{COLOMBE}/clotures/2026-07"
        avant = comptable.get(ecran).json()
        pieces = avant["points"][0]
        assert pieces["references"] == ["PJ-2026-0003", "PJ-2026-0023"]
        assert avant["bloquants_restants"] == 1

        assert _client(COMPTABLE_AUTRE).post(
            "/collecte/pieces/PJ-2026-0003/classement", json={"motif": "Doublon constaté"}
        ).status_code in {403, 404}
        court = comptable.post("/collecte/pieces/PJ-2026-0003/classement", json={"motif": "non"})
        assert court.status_code == 409 and "10 caractères" in court.json()["detail"]
        classee = comptable.post(
            "/collecte/pieces/PJ-2026-0003/classement",
            json={"motif": "Facture refaite par le fournisseur : PJ-2026-0023 la remplace."},
        )
        assert classee.status_code == 200, classee.text
        assert classee.json()["etat"] == "ARCHIVEE"
        refaite = comptable.post(
            "/collecte/pieces/PJ-2026-0003/classement", json={"motif": "Une seconde fois ?"}
        )
        assert refaite.status_code == 409

        saisie = {**_saisie("2026-07-24"), "piece_justificative": "F-2026-0434"}
        brouillon = comptable.post(f"/comptabilite/dossiers/{COLOMBE}/ecritures", json=saisie)
        assert brouillon.status_code == 201, brouillon.text
        numero = brouillon.json()["numero"]
        # Saisie seule : la pièce attend toujours, un brouillon peut encore changer de pièce.
        assert comptable.get(ecran).json()["bloquants_restants"] == 2
        validee = comptable.post(
            f"/comptabilite/dossiers/{COLOMBE}/ecritures/2026/AC/{numero}/validation"
        )
        assert validee.status_code == 200, validee.text

        apres = comptable.get(ecran).json()
        assert apres["points"][0]["traite"] is True
        assert apres["bloquants_restants"] == 0 and apres["transmissible"] is True
        journal = [
            e["action"]
            for e in _client(REVISEUR)
            .get("/transverse/audit?objet_type=piece_justificative")
            .json()
        ]
        assert {"collecte.piece_classee", "collecte.piece_comptabilisee"} <= set(journal)

        transmise = comptable.post(
            f"/comptabilite/dossiers/{COLOMBE}/revues",
            json={"du": "2026-07-01", "au": "2026-07-31"},
        )
        assert transmise.status_code == 201, transmise.text
        assert comptable.get(ecran).json()["verrou"]["statut"] == "TRANSMISE"

        # Renvoyé, le mois se rouvre ; un écart proposé entre-temps sur une de ses pièces, et
        # qui attend le second regard, bloque la retransmission aux mêmes conditions.
        reviseur = _client(REVISEUR)
        revue = f"/comptabilite/dossiers/{COLOMBE}/revues/REV-20260701-20260731"
        remarque = {
            "nature": "COMPTE",
            "reference": "401",
            "texte": "Le fournisseur est-il juste ?",
        }
        assert reviseur.post(f"{revue}/remarques", json=remarque).status_code == 200
        assert reviseur.post(f"{revue}/renvoi", json={}).status_code == 200
        propose = reviseur.post(
            "/conformite/pieces/F-2026-0434/ecarts",
            json={"code_regle": "FAC-ACH-007", "motif": "Virement attesté par le relevé du mois."},
        )
        assert propose.status_code == 200 and propose.json()["ecart"]["statut"] == "EN_ATTENTE"
        reponse = {"reponse": "Oui, vérifié sur la facture."}
        assert comptable.post(f"{revue}/remarques/1/reponse", json=reponse).status_code == 200
        refus = comptable.post(f"{revue}/retransmission", json={})
        assert refus.status_code == 422
        assert "écart de constat en attente du second regard" in refus.json()["detail"]
        ecart = propose.json()["ecart"]["identifiant"]
        assert (
            _client("r.ebolo@cga-brcg.cm")
            .post(
                f"/conformite/pieces/F-2026-0434/ecarts/{ecart}/second-regard",
                json={"decision": "CONFIRMER", "motif": "Relevé relu : virement du 20/07."},
            )
            .status_code
            == 200
        )
        assert comptable.post(f"{revue}/retransmission", json={}).status_code == 200


class TestSurPostgresql:
    pytestmark = exige_postgresql

    @pytest.mark.usefixtures("cloture_sans_point_bloquant")
    def test_le_verrou_se_relit_d_une_requete_a_l_autre(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)
        transmise = client.post(REVUES, json={"du": "2026-07-01", "au": "2026-07-31"})
        assert transmise.status_code == 201, transmise.text
        refus = client.post(ECRITURES, json=_saisie("2026-07-15"))
        assert refus.status_code == 409 and "verrouillée" in refus.json()["detail"]
        vue = client.get(ECRAN).json()
        assert vue["verrou"]["revue"] == "REV-20260701-20260731"
        assert client.post(ECRITURES, json=_saisie("2026-08-15")).status_code == 201
