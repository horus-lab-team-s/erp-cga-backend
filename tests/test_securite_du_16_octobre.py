"""Les correctifs de la revue de sécurité du 16 octobre 2026 qui n'ont pas de foyer ailleurs.

⚠️ CE QUI EST GARDÉ
* le second facteur se suspend après cinq codes refusés en quinze minutes, pour le compte
  et non pour une adresse (une adresse se change) ;
* le CV d'une candidature se télécharge quel que soit son nom (accents, apostrophe,
  guillemets) : l'en-tête n'échoue plus et ne se casse plus.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.contextes.transverse.api import (
    Compte,
    DepotSessionsMemoire,
    EtatCompte,
    JournalAuditMemoire,
    Session,
)
from app.contextes.transverse.application.authentification import (
    CodeInvalide,
    renforcer_session,
)
from app.contextes.transverse.domaine.second_facteur import code_attendu, engendrer_secret_totp
from app.partage.en_tetes import disposition_en_piece_jointe

INSTANT = datetime(2026, 10, 16, 9, 0)


def _compte(secret: str) -> Compte:
    return Compte(
        identifiant="C-1",
        courriel="c@cga-brcg.cm",
        nom="ESSAI",
        prenom="Compte",
        locataire="CGA-BRCG",
        etat=EtatCompte.ACTIF,
        cree_le=INSTANT - timedelta(days=30),
        secret_totp=secret,
    )


def _session() -> Session:
    return Session(
        identifiant="S-1",
        compte="C-1",
        locataire="CGA-BRCG",
        ouverte_le=INSTANT,
        expire_le=INSTANT + timedelta(hours=12),
    )


class TestLeSecondFacteurSeSuspend:
    def test_apres_cinq_codes_refuses_meme_le_bon_est_refuse(self):
        secret = engendrer_secret_totp()
        compte, journal, sessions = _compte(secret), JournalAuditMemoire(), DepotSessionsMemoire()
        for minute in range(5):
            with pytest.raises(CodeInvalide):
                renforcer_session(
                    _session(),
                    compte,
                    "000000" if code_attendu(secret, INSTANT) != "000000" else "111111",
                    sessions=sessions,
                    journal=journal,
                    a_l_instant=INSTANT + timedelta(minutes=minute),
                )
        juste = INSTANT + timedelta(minutes=6)
        with pytest.raises(CodeInvalide, match="suspendus"):
            renforcer_session(
                _session(),
                compte,
                code_attendu(secret, juste),
                sessions=sessions,
                journal=journal,
                a_l_instant=juste,
            )
        # Quinze minutes plus tard, le bon code repasse.
        apres = INSTANT + timedelta(minutes=25)
        renforcee = renforcer_session(
            _session(),
            compte,
            code_attendu(secret, apres),
            sessions=sessions,
            journal=journal,
            a_l_instant=apres,
        )
        assert renforcee.renforcee(apres)


class TestLeNomDuCv:
    @pytest.mark.parametrize("nom", ["CV d’Amélie.pdf", 'CV "piège".pdf', "Ngo Biyong — 2026.pdf"])
    def test_l_en_tete_reste_latin_1_et_garde_le_nom_exact(self, nom):
        en_tete = disposition_en_piece_jointe(nom)
        en_tete.encode("latin-1")  # sans quoi le serveur rend 500
        assert en_tete.startswith('attachment; filename="')
        assert en_tete.count('"') == 2
        assert "filename*=UTF-8''" in en_tete
