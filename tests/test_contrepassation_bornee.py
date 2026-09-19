"""La contre-passation et la validation passent le contrôle d'exercice.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE (pas 71)

La contre-passation se disait datée « du jour où l'on s'aperçoit de l'erreur, jamais
de l'écriture d'origine ». Aucune ligne ne le tenait : la date venait de la requête,
et le brouillon inverse naissait sans aucun contrôle d'exercice. Sur PostgreSQL,
avant correction :

    2026 clos, contre-passer une écriture de 2026   → brouillon dans 2026, daté 2027
    valider ce brouillon                            → la balance de 2026 close change
    contre-passer au 01/01/2020                     → accepté

La liasse remise à l'administration devenait fausse après coup, par un geste que
l'écran s'apprêtait à proposer.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.contextes.comptabilite.api import (
    ContrepassationAntidatee,
    DateHorsExercice,
    ExerciceClos,
    contrepasser_une_ecriture,
    valider_une_ecriture,
)
from app.contextes.portefeuille.contrats import Exercice
from app.partage.horloge import horloge_figee
from tests.conftest import exige_postgresql, ouvrir_une_session
from tests.test_cloture_exercice import DOSSIER, REVISEUR, _balance, _clore
from tests.test_comptabilite_saisie import JOUR, OUVERT, _enregistrer, depot  # noqa: F401

CLOS_2026 = OUVERT.model_copy(update={"clos": True})
MOTIF = "Facture enregistrée en double, constatée au rapprochement."


def _validee(depot):  # noqa: F811
    ecriture = _enregistrer(depot)
    return valider_une_ecriture(
        ecriture.cle,
        exercice=OUVERT,
        periodes_verrouillees=(),
        depot=depot,
        par="C-003",
        le=datetime(2026, 8, 17),
    )


def _contrepasser(depot, origine, *, jour, exercice=OUVERT):  # noqa: F811
    return contrepasser_une_ecriture(
        origine.cle,
        motif=MOTIF,
        jour=jour,
        exercice=exercice,
        periodes_verrouillees=(),
        depot=depot,
        par="C-003",
    )


class TestLaContrePassation:
    def test_dans_un_exercice_clos_elle_est_refusee_et_n_ecrit_rien(self, depot):  # noqa: F811
        origine = _validee(depot)
        with pytest.raises(ExerciceClos, match="ne se contre-passe plus"):
            _contrepasser(depot, origine, jour=date(2026, 12, 20), exercice=CLOS_2026)
        assert len(depot.lister("2026")) == 1, "un brouillon est né malgré le refus"

    def test_antidatee_avant_l_origine_elle_est_refusee(self, depot):  # noqa: F811
        origine = _validee(depot)
        with pytest.raises(ContrepassationAntidatee, match="avant l'écriture"):
            _contrepasser(depot, origine, jour=date(2026, 3, 1))

    def test_le_jour_meme_de_l_origine_reste_admis(self, depot):  # noqa: F811
        """L'erreur vue à la relecture se constate le jour même : ce n'est pas antidater."""
        origine = _validee(depot)
        assert _contrepasser(depot, origine, jour=JOUR).date_operation == JOUR

    def test_hors_des_bornes_elle_est_refusee_avec_la_date_a_retenir(self, depot):  # noqa: F811
        """Le 15/01/2027, 2026 encore ouvert : la phrase dit la date possible."""
        origine = _validee(depot)
        with pytest.raises(DateHorsExercice, match="au plus tard du 31/12/2026"):
            _contrepasser(depot, origine, jour=date(2027, 1, 15))

    def test_un_exercice_inconnu_refuse(self, depot):  # noqa: F811
        origine = _validee(depot)
        with pytest.raises(ExerciceClos, match="inconnu"):
            _contrepasser(depot, origine, jour=JOUR, exercice=None)

    def test_l_exercice_d_une_autre_ecriture_est_un_refus_bruyant(self, depot):  # noqa: F811
        origine = _validee(depot)
        autre = Exercice(libelle="2027", ouverture=date(2027, 1, 1), cloture=date(2027, 12, 31))
        with pytest.raises(ValueError, match="incohérent"):
            _contrepasser(depot, origine, jour=JOUR, exercice=autre)


class TestLaValidation:
    def test_un_brouillon_ne_se_valide_plus_une_fois_l_exercice_clos(self, depot):  # noqa: F811
        """⚠️ La seconde moitié du défaut : même né avant la clôture, par un chemin
        qu'on n'aurait pas vu, un brouillon ne s'engage pas dans un exercice clos."""
        brouillon = _enregistrer(depot)
        with pytest.raises(ExerciceClos):
            valider_une_ecriture(
                brouillon.cle,
                exercice=CLOS_2026,
                periodes_verrouillees=(),
                depot=depot,
                par="C-003",
                le=datetime(2027, 1, 20),
            )
        assert depot.lire(brouillon.cle).validee_par is None


class TestParLaRoute:
    """La sonde du pas 71, rejouée : les trois constats deviennent trois refus."""

    pytestmark = exige_postgresql

    def test_apres_la_cloture_la_contre_passation_est_refusee_et_la_balance_tient(
        self, plateforme
    ):
        client = plateforme
        with horloge_figee(datetime(2027, 1, 15, 9, 0)):
            ouvrir_une_session(client, REVISEUR)
            ecritures = client.get(
                f"/comptabilite/dossiers/{DOSSIER}/ecritures?exercice=2026"
            ).json()
            origine = next(e for e in ecritures if e["etat"] == "VALIDEE")
            cle = f"2026/{origine['journal']}/{origine['numero']}"
            avant = _balance(client, "2026")
            assert _clore(client, appliquer=True)["applique"] is True

            refus = client.post(
                f"/comptabilite/dossiers/{DOSSIER}/ecritures/{cle}/contre-passation",
                json={"motif": MOTIF},
            )
            assert refus.status_code == 409, refus.text
            assert "ne se contre-passe plus" in refus.json()["detail"]
            assert _balance(client, "2026") == avant
            assert len(
                client.get(f"/comptabilite/dossiers/{DOSSIER}/ecritures?exercice=2026").json()
            ) == len(ecritures), "un brouillon est né dans l'exercice clos"

    def test_en_cours_d_exercice_l_antidatee_est_refusee(self, plateforme):
        client = plateforme
        with horloge_figee(datetime(2026, 9, 14, 10, 0)):
            ouvrir_une_session(client, REVISEUR)
            ecritures = client.get(
                f"/comptabilite/dossiers/{DOSSIER}/ecritures?exercice=2026"
            ).json()
            # Une écriture validée postérieure au 1er janvier : sa veille reste dans 2026,
            # et l'antidatation se mesure sans être masquée par la borne de l'exercice.
            origine = next(
                e for e in ecritures
                if e["etat"] == "VALIDEE" and e["date_operation"] > "2026-01-01"
            )
            cle = f"2026/{origine['journal']}/{origine['numero']}"
            refus = client.post(
                f"/comptabilite/dossiers/{DOSSIER}/ecritures/{cle}/contre-passation",
                json={"motif": MOTIF, "date_operation": "2020-01-01"},
            )
            assert refus.status_code == 409, refus.text
            # Hors de l'exercice avant même d'être antidatée : la borne parle la première.
            assert "hors de l'exercice" in refus.json()["detail"]

            veille = date.fromisoformat(origine["date_operation"]) - timedelta(days=1)
            antidatee = client.post(
                f"/comptabilite/dossiers/{DOSSIER}/ecritures/{cle}/contre-passation",
                json={"motif": MOTIF, "date_operation": veille.isoformat()},
            )
            assert antidatee.status_code == 409, antidatee.text
            assert "avant l'écriture" in antidatee.json()["detail"]

            admise = client.post(
                f"/comptabilite/dossiers/{DOSSIER}/ecritures/{cle}/contre-passation",
                json={"motif": MOTIF},
            )
            assert admise.status_code == 201, admise.text
            assert admise.json()["date_operation"] == "2026-09-14"
