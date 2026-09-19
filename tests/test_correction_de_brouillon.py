"""Un brouillon se corrige, à son numéro : la promesse que l'écran faisait déjà.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE (pas 72)

L'écran disait après chaque saisie « reste modifiable tant qu'elle n'est pas
validée », l'entité exposait `modifiable`, la contre-passation refusée conseillait
« un brouillon se corrige ou se supprime », la clôture « à valider ou à supprimer ».
Aucune route ne corrigeait ni ne supprimait.

Sur PostgreSQL, un brouillon saisi sans pièce justificative ne se validait pas, ne se
contre-passait pas, et bloquait la clôture de l'exercice pour toujours.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contextes.comptabilite.api import (
    COMPTES_SYSCOHADA,
    JOURNAUX_CABINET,
    CompteInconnu,
    CorrectionDeBrouillon,
    CorrectionRefusee,
    DateHorsExercice,
    EtatEcriture,
    ExerciceClos,
    LigneEcriture,
    Sens,
    contrepasser_une_ecriture,
    corriger_un_brouillon,
    valider_une_ecriture,
)
from app.partage.horloge import horloge_figee
from tests.conftest import exige_postgresql, ouvrir_une_session
from tests.test_cloture_exercice import COMPTABLE, DOSSIER, REVISEUR, _clore
from tests.test_comptabilite_saisie import (  # noqa: F401
    OUVERT,
    _brouillon,
    _enregistrer,
    depot,
)

CLOS_2026 = OUVERT.model_copy(update={"clos": True})


def _lignes(montant: str, *, compte_debit: str = "601") -> list[LigneEcriture]:
    return [
        LigneEcriture(
            compte=compte_debit, libelle="Achat", sens=Sens.DEBIT, montant=Decimal(montant)
        ),
        LigneEcriture(
            compte="401", libelle="Fournisseur", sens=Sens.CREDIT, montant=Decimal(montant)
        ),
    ]


def _correction(**champs) -> CorrectionDeBrouillon:
    base = dict(
        date_operation=date(2026, 8, 18),
        libelle="Facture ALPHA F-2026-0912, montant rectifié",
        piece_justificative="PJ-2026-0912",
        lignes=_lignes("1200000"),
    )
    base.update(champs)
    return CorrectionDeBrouillon(**base)


def _corriger(depot, cle, correction, *, exercice=OUVERT, par="C-007"):  # noqa: F811
    return corriger_un_brouillon(
        cle, correction, journaux=JOURNAUX_CABINET, plan=COMPTES_SYSCOHADA,
        exercice=exercice, periodes_verrouillees=(), depot=depot, par=par,
    )


class TestLaCorrection:
    def test_le_brouillon_sans_piece_se_corrige_puis_se_valide(self, depot):  # noqa: F811
        """⚠️ L'impasse du pas 72, levée."""
        brouillon = _enregistrer(depot, _brouillon(piece_justificative=None))
        with pytest.raises(ValueError, match="pièce justificative"):
            valider_une_ecriture(
                brouillon.cle,
                exercice=OUVERT,
                periodes_verrouillees=(),
                depot=depot,
                par="C-003",
                le=datetime(2026, 8, 19),
            )
        corrigee = _corriger(depot, brouillon.cle, _correction())
        validee = valider_une_ecriture(
            corrigee.cle,
            exercice=OUVERT,
            periodes_verrouillees=(),
            depot=depot,
            par="C-003",
            le=datetime(2026, 8, 19),
        )
        assert validee.etat is EtatEcriture.VALIDEE

    def test_le_numero_le_journal_et_l_exercice_restent(self, depot):  # noqa: F811
        premier = _enregistrer(depot)
        second = _enregistrer(depot)
        corrigee = _corriger(depot, premier.cle, _correction())
        assert (corrigee.journal, corrigee.exercice, corrigee.numero) == ("AC", "2026", 1)
        assert [e.numero for e in depot.lister("2026")] == [1, second.numero]
        assert depot.lire(premier.cle).montant == Decimal(1200000)
        assert corrigee.saisie_par == "C-007", "l'auteur du contenu est celui qui l'a écrit"

    def test_la_cle_ne_se_corrige_pas(self):
        with pytest.raises(ValidationError):
            CorrectionDeBrouillon(**_correction().model_dump(), numero=7)
        with pytest.raises(ValidationError):
            CorrectionDeBrouillon(**_correction().model_dump(), journal="VE")


class TestLesRefus:
    def test_une_ecriture_validee_ne_se_corrige_pas(self, depot):  # noqa: F811
        ecriture = _enregistrer(depot)
        valider_une_ecriture(
            ecriture.cle,
            exercice=OUVERT,
            periodes_verrouillees=(),
            depot=depot,
            par="C-003",
            le=datetime(2026, 8, 17),
        )
        with pytest.raises(CorrectionRefusee, match="contre-passer"):
            _corriger(depot, ecriture.cle, _correction())
        assert depot.lire(ecriture.cle).montant == Decimal(1000000)

    def test_une_contre_passation_en_brouillon_ne_se_corrige_pas(self, depot):  # noqa: F811
        ecriture = _enregistrer(depot)
        valider_une_ecriture(
            ecriture.cle,
            exercice=OUVERT,
            periodes_verrouillees=(),
            depot=depot,
            par="C-003",
            le=datetime(2026, 8, 17),
        )
        inverse = contrepasser_une_ecriture(
            ecriture.cle, motif="Doublon", jour=date(2026, 8, 20), exercice=OUVERT,
            periodes_verrouillees=(),
            depot=depot, par="C-003",
        )
        with pytest.raises(CorrectionRefusee, match="contre-passation"):
            _corriger(depot, inverse.cle, _correction())

    def test_les_controles_de_la_saisie_sont_rejoues(self, depot):  # noqa: F811
        brouillon = _enregistrer(depot)
        with pytest.raises(CompteInconnu):
            _corriger(depot, brouillon.cle, _correction(lignes=_lignes("5", compte_debit="60119")))
        with pytest.raises(DateHorsExercice):
            _corriger(depot, brouillon.cle, _correction(date_operation=date(2027, 1, 3)))
        with pytest.raises(ExerciceClos):
            _corriger(depot, brouillon.cle, _correction(), exercice=CLOS_2026)
        assert depot.lire(brouillon.cle).montant == Decimal(1000000), "un refus a écrit"


class TestParLaRoute:
    """La sonde du pas 72 : le brouillon sans pièce ne bloque plus la clôture."""

    pytestmark = exige_postgresql

    def test_le_brouillon_oublie_se_corrige_et_la_cloture_passe(self, plateforme):
        client = plateforme
        with horloge_figee(datetime(2026, 11, 2, 9, 0)):
            ouvrir_une_session(client, COMPTABLE)
            saisie = client.post(f"/comptabilite/dossiers/{DOSSIER}/ecritures", json={
                "journal": "OD", "exercice": "2026", "date_operation": "2026-11-02",
                "libelle": "Saisie sans pièce",
                "lignes": [
                    {"compte": "601", "libelle": "x", "sens": "DEBIT", "montant": "1000"},
                    {"compte": "401", "libelle": "x", "sens": "CREDIT", "montant": "1000"},
                ],
            })
            assert saisie.status_code == 201, saisie.text
            base = f"/comptabilite/dossiers/{DOSSIER}/ecritures/2026/OD/{saisie.json()['numero']}"
            assert client.post(f"{base}/validation", json={}).status_code == 409

            corps = {
                "date_operation": "2026-11-02", "libelle": "Achat de fournitures",
                "piece_justificative": "PJ-2026-1102",
                "lignes": saisie.json()["lignes"],
            }
            refus = client.post(f"{base}/correction", json={**corps, "numero": 99})
            assert refus.status_code == 422, "un numéro glissé dans la requête est passé"
            corrigee = client.post(f"{base}/correction", json=corps)
            assert corrigee.status_code == 200, corrigee.text
            assert corrigee.json()["piece_justificative"] == "PJ-2026-1102"
            assert client.post(f"{base}/validation", json={}).status_code == 200
            assert client.post(f"{base}/correction", json=corps).status_code == 409

        with horloge_figee(datetime(2027, 1, 15, 9, 0)):
            ouvrir_une_session(client, REVISEUR)
            rapport = _clore(client, appliquer=False)
            assert "BROUILLON_SUBSISTANT" not in [o["motif"] for o in rapport["obstacles"]]
            assert rapport["possible"] is True, rapport["obstacles"]

    def test_l_adherent_ne_corrige_pas(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "jp.nkoa@batimentplus.cm")
        # ⚠️ Un corps **valide** : avec des lignes vides, la requête tombait en 422 avant
        # le contrôle d'accès, et le cas ne mesurait rien.
        reponse = client.post(
            f"/comptabilite/dossiers/{DOSSIER}/ecritures/2026/OD/1/correction",
            json={
                "date_operation": "2026-11-02", "libelle": "Réécrite par l'adhérent",
                "piece_justificative": "PJ-X",
                "lignes": [
                    {"compte": "601", "libelle": "x", "sens": "DEBIT", "montant": "1"},
                    {"compte": "401", "libelle": "x", "sens": "CREDIT", "montant": "1"},
                ],
            },
        )
        assert reponse.status_code == 403, reponse.text
