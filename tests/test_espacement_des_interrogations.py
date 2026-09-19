"""Combien de fois on interroge le prestataire, et à quel rythme.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

La réconciliation était une **route qu'un humain devait cliquer**. Elle passait
rarement, et interroger chaque paiement en attente à chaque passage ne coûtait
rien.

Branchée à l'ordonnanceur, elle passe toutes les cinq minutes. Une opération que
l'abonné a abandonnée reste en attente vingt-quatre heures : sans espacement,
**288 interrogations pour un seul paiement que personne ne validera jamais**.

Le compteur `verifications` existait déjà, avec ce commentaire : « sert à repérer
les opérations sur lesquelles on s'acharne ». Il sert désormais à ne plus
s'acharner.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from app.contextes.souscription.domaine.paiements import (
    ESPACEMENT_MAXIMAL,
    NaturePaiement,
    Paiement,
)

T0 = datetime(2026, 9, 11, 9, 0)
CINQ_MINUTES = timedelta(minutes=5)


def _paiement(**surcharges) -> Paiement:
    defauts = {
        "identifiant": "pay-1",
        "nature": NaturePaiement.PROFORMA,
        "reference_reglee": "PRO-2026-0001",
        "cle_idempotence": "cle-idempotence-0001",
        "montant": Decimal("250000"),
        "telephone": "699112233",
        "initie_le": T0,
    }
    return Paiement(**{**defauts, **surcharges})


class TestLePremierExamen:
    def test_rien_avant_le_delai_minimal(self):
        """⚠️ Le filet sert les cas où la notification **n'est pas venue**, pas
        ceux où elle n'est **pas encore** venue.

        Interroger une opération engagée il y a dix secondes ne rend rien d'utile :
        l'abonné n'a pas encore saisi son code.
        """
        paiement = _paiement()
        assert paiement.a_interroger(T0 + timedelta(minutes=4), CINQ_MINUTES) is False

    def test_au_delai_minimal_le_premier_appel_part(self):
        paiement = _paiement()
        assert paiement.a_interroger(T0 + CINQ_MINUTES, CINQ_MINUTES) is True

    def test_jamais_interroge_veut_dire_interrogeable(self):
        """⚠️ `derniere_verification` à `None` n'est pas « interrogé il y a très
        longtemps » : c'est « jamais ». La confusion ferait attendre l'espacement
        avant le tout premier appel, donc dix minutes au lieu de cinq."""
        assert _paiement().derniere_verification is None


class TestLEspacementQuiDouble:
    def test_il_double_a_chaque_tentative(self):
        for tentatives, attendu in ((0, 5), (1, 10), (2, 20), (3, 40)):
            paiement = _paiement(verifications=tentatives)
            assert paiement.espacement(CINQ_MINUTES) == timedelta(minutes=attendu)

    def test_il_plafonne(self):
        """⚠️ Sans plafond, la vingtième tentative attendrait vingt-huit jours,
        sur une opération qui expire à vingt-quatre heures : le filet cesserait
        d'exister avant d'avoir servi."""
        assert _paiement(verifications=20).espacement(CINQ_MINUTES) == ESPACEMENT_MAXIMAL

    def test_une_interrogation_trop_proche_est_refusee(self):
        interroge = _paiement(verifications=1, derniere_verification=T0)
        assert interroge.a_interroger(T0 + timedelta(minutes=9), CINQ_MINUTES) is False
        assert interroge.a_interroger(T0 + timedelta(minutes=10), CINQ_MINUTES) is True


class TestCeQueCelaCoute:
    """⚠️ Le cas qui justifie tout le fichier : il compte les appels réels."""

    def _appels(self, *, cadence: timedelta, duree: timedelta, espace: bool) -> int:
        paiement = _paiement()
        instant, appels = T0, 0
        fin = T0 + duree
        while instant <= fin:
            interrogeable = (
                paiement.a_interroger(instant, CINQ_MINUTES)
                if espace
                else instant - paiement.initie_le >= CINQ_MINUTES
            )
            if interrogeable:
                paiement = paiement.apres_verification(instant)
                appels += 1
            instant += cadence
        return appels

    def test_sans_espacement_le_prestataire_est_appele_288_fois(self):
        """L'état d'avant, mesuré. Ce cas est le rappel de ce qu'on répare : il ne
        doit pas se mettre à passer autrement."""
        appels = self._appels(
            cadence=timedelta(minutes=5), duree=timedelta(hours=24), espace=False
        )
        assert appels == 288

    def test_avec_espacement_il_est_appele_vingt_six_fois(self):
        appels = self._appels(
            cadence=timedelta(minutes=5), duree=timedelta(hours=24), espace=True
        )
        assert appels == 26, appels

    def test_le_premier_appel_part_aussi_vite_qu_avant(self):
        """⚠️ La contre-épreuve : l'espacement ne doit pas retarder le rattrapage.

        Un espacement qui ferait attendre le premier appel échangerait 288 appels
        inutiles contre un client qui attend son espace plus longtemps, ce qui
        serait un mauvais marché.
        """
        paiement = _paiement()
        assert paiement.a_interroger(T0 + CINQ_MINUTES, CINQ_MINUTES) is True


class TestLaTraceDeLInterrogation:
    def test_elle_compte_et_elle_date(self):
        apres = _paiement().apres_verification(T0 + CINQ_MINUTES)
        assert apres.verifications == 1
        assert apres.derniere_verification == T0 + CINQ_MINUTES

    def test_elle_ne_touche_a_rien_d_autre(self):
        """⚠️ Interroger n'est pas décider : le statut, le montant et la date
        d'initiation ne bougent pas. Un compteur qui modifierait l'opération
        ferait mentir la péremption, qui se calcule sur `initie_le`."""
        avant = _paiement()
        apres = avant.apres_verification(T0 + CINQ_MINUTES)
        assert apres.statut is avant.statut
        assert apres.initie_le == avant.initie_le
        assert apres.montant == avant.montant
