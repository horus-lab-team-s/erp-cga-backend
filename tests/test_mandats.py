"""Le mandat : agir dans le périmètre d'un autre locataire.

Septième et dernier pas du socle multi-tenant. Purement du domaine — aucune base, aucune
horloge, la date est passée en argument — parce que c'est une décision d'autorisation, et
qu'une décision d'autorisation doit pouvoir s'écrire et se relire sans montage.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.contextes.transverse.domaine.mandats import (
    Mandat,
    MotifMandat,
    RefusDeMandat,
    mandat_applicable,
)
from app.contextes.transverse.domaine.roles import Role

CENTRE = "brcg"
PME = "station-bonaberi"
AUTRE_PME = "boulangerie"

DEBUT = date(2026, 1, 1)
PENDANT = date(2026, 6, 15)
APRES = date(2027, 6, 15)


def _mandat(**remplacements) -> Mandat:
    """Un mandat de tenue comptable, que chaque test dégrade sur un seul point."""
    base = {
        "identifiant": "mdt-001",
        "mandant": PME,
        "mandataire": CENTRE,
        "roles": frozenset({Role.COMPTABLE}),
        "debut": DEBUT,
        "motif": MotifMandat.CONSENTEMENT,
        "accorde_par": "Direction de la station-service",
    }
    return Mandat(**{**base, **remplacements})


def _chercher(mandats, **remplacements):
    parametres = {
        "mandataire": CENTRE,
        "mandant": PME,
        "compte": "cpt-comptable",
        "role": Role.COMPTABLE,
        "a_la_date": PENDANT,
    }
    return mandat_applicable(mandats, **{**parametres, **remplacements})


class TestLeCasNominal:
    def test_un_mandat_en_vigueur_autorise(self):
        mandat, refus = _chercher([_mandat()])
        assert mandat is not None
        assert refus is None

    def test_agir_chez_soi_ne_demande_aucun_mandat(self):
        """Le dire ici évite à chaque appelant de se poser la question, et évite surtout
        qu'un oubli rende l'accès impossible à un locataire sur ses propres données."""
        mandat, refus = _chercher([], mandant=CENTRE)
        assert mandat is None
        assert refus is None

    def test_sans_designation_tous_les_comptes_du_mandataire_agissent(self):
        mandat, refus = _chercher([_mandat()], compte="n-importe-lequel")
        assert mandat is not None and refus is None


class TestLesTroisQuestions:
    """Chacune peut refuser, et chacune se diagnostique différemment."""

    def test_aucun_mandat_entre_ces_deux_locataires(self):
        _, refus = _chercher([_mandat(mandant=AUTRE_PME)])
        assert refus is RefusDeMandat.AUCUN

    def test_un_mandat_expire_ne_vaut_plus(self):
        _, refus = _chercher([_mandat(fin=date(2026, 3, 1))])
        assert refus is RefusDeMandat.EXPIRE

    def test_un_mandat_pas_encore_commence_ne_vaut_pas_encore(self):
        _, refus = _chercher([_mandat(debut=date(2027, 1, 1))])
        assert refus is RefusDeMandat.EXPIRE

    def test_un_mandat_revoque_se_distingue_d_un_mandat_expire(self):
        """Deux diagnostics différents : l'un est arrivé à terme, l'autre a été retiré.
        La distinction est ce qu'un litige demande de savoir."""
        _, refus = _chercher(
            [_mandat(revoque_le=date(2026, 3, 1), revoque_par="Direction de la PME")]
        )
        assert refus is RefusDeMandat.REVOQUE

    def test_un_role_non_couvert_est_refuse(self):
        """⚠️ Un mandat ne donne pas de rôle, il en autorise l'exercice ailleurs. Un
        comptable mandaté reste comptable : il ne devient pas réviseur en franchissant la
        frontière."""
        _, refus = _chercher([_mandat()], role=Role.REVISEUR)
        assert refus is RefusDeMandat.ROLE_NON_COUVERT

    def test_un_compte_non_designe_est_refuse(self):
        """Ce qui permet à une PME de mandater un centre en désignant l'équipe qui la
        suit, plutôt que le centre entier."""
        _, refus = _chercher(
            [_mandat(comptes=frozenset({"cpt-alice", "cpt-bob"}))], compte="cpt-intrus"
        )
        assert refus is RefusDeMandat.COMPTE_NON_DESIGNE

    def test_un_compte_designe_passe(self):
        mandat, refus = _chercher(
            [_mandat(comptes=frozenset({"cpt-alice"}))], compte="cpt-alice"
        )
        assert mandat is not None and refus is None


class TestLeMotifLePlusAvance:
    """Le diagnostic doit aider à corriger, pas seulement à constater."""

    def test_un_mandat_qui_ne_couvre_pas_le_role_le_dit(self):
        """Répondre « aucun mandat » ferait créer un second mandat, là qu'il fallait
        élargir le premier."""
        _, refus = _chercher([_mandat()], role=Role.FISCALISTE)
        assert refus is RefusDeMandat.ROLE_NON_COUVERT

    def test_entre_un_expire_et_un_role_non_couvert_on_retient_le_second(self):
        mandats = [_mandat(identifiant="a", fin=date(2026, 3, 1)), _mandat(identifiant="b")]
        _, refus = _chercher(mandats, role=Role.REVISEUR)
        assert refus is RefusDeMandat.ROLE_NON_COUVERT

    def test_un_mandat_valide_l_emporte_sur_un_autre_qui_refuse(self):
        """L'ordre de la liste ne doit pas décider de l'autorisation."""
        mandats = [_mandat(identifiant="a", fin=date(2026, 3, 1)), _mandat(identifiant="b")]
        mandat, refus = _chercher(mandats)
        assert mandat is not None and mandat.identifiant == "b"
        assert refus is None


class TestLaVigueur:
    """Intervalle [début, fin[, borne haute exclue."""

    def test_le_premier_jour_compte(self):
        assert _mandat().en_vigueur(DEBUT)

    def test_le_jour_de_fin_ne_compte_pas(self):
        """Sans cette convention, le dernier jour appartiendrait à deux périodes."""
        fin = date(2026, 3, 1)
        assert not _mandat(fin=fin).en_vigueur(fin)
        assert _mandat(fin=fin).en_vigueur(date(2026, 2, 28))

    def test_le_jour_de_revocation_ne_compte_pas_non_plus(self):
        retrait = date(2026, 3, 1)
        mandat = _mandat(revoque_le=retrait, revoque_par="Direction")
        assert not mandat.en_vigueur(retrait)
        assert mandat.en_vigueur(date(2026, 2, 28))

    def test_sans_fin_il_court_toujours(self):
        assert _mandat().en_vigueur(APRES)


class TestCeQueLeModeleRefuse:
    def test_un_mandat_sur_soi_meme(self):
        with pytest.raises(ValidationError, match="sur lui-même"):
            _mandat(mandant=CENTRE, mandataire=CENTRE)

    def test_un_mandat_sans_role(self):
        """Un mandat qui n'autorise aucun rôle n'autorise rien, et le laisser exister
        ferait croire à un accès qui n'existe pas."""
        with pytest.raises(ValidationError, match="sans rôle"):
            _mandat(roles=frozenset())

    def test_une_designation_vide(self):
        """`None` dit « tous les comptes ». Un ensemble vide n'autorise personne, et la
        confusion entre les deux se paie par un accès ouvert à tort ou fermé à tort."""
        with pytest.raises(ValidationError, match="désignation vide"):
            _mandat(comptes=frozenset())

    def test_une_borne_de_validite_incoherente(self):
        with pytest.raises(ValidationError, match="incohérente"):
            _mandat(fin=DEBUT)

    def test_une_revocation_anterieure_au_debut(self):
        with pytest.raises(ValidationError, match="avant d'avoir commencé"):
            _mandat(revoque_le=date(2025, 1, 1), revoque_par="Direction")

    def test_une_revocation_sans_auteur(self):
        """Une révocation porte sa date **et** son auteur. L'une sans l'autre laisse un
        retrait que personne n'assume."""
        with pytest.raises(ValidationError, match="date"):
            _mandat(revoque_le=date(2026, 3, 1))

    def test_un_auteur_de_revocation_sans_date(self):
        with pytest.raises(ValidationError, match="date"):
            _mandat(revoque_par="Direction")

    def test_un_mandat_est_immuable(self):
        with pytest.raises(ValidationError):
            _mandat().revoque_le = date(2026, 3, 1)


class TestLaRevocationSeDistingueDeLaFin:
    def test_les_deux_champs_coexistent(self):
        """Écraser `fin` ferait perdre l'information qu'un mandat a été retiré plutôt
        qu'arrivé à échéance."""
        mandat = _mandat(
            fin=date(2027, 1, 1), revoque_le=date(2026, 3, 1), revoque_par="Direction"
        )
        assert mandat.fin == date(2027, 1, 1)
        assert mandat.revoque_le == date(2026, 3, 1)

    def test_la_revocation_prime_sur_la_fin(self):
        mandat = _mandat(
            fin=date(2027, 1, 1), revoque_le=date(2026, 3, 1), revoque_par="Direction"
        )
        assert not mandat.en_vigueur(PENDANT)


class TestLesMotifs:
    @pytest.mark.parametrize("motif", list(MotifMandat))
    def test_chaque_motif_produit_un_mandat_valable(self, motif: MotifMandat):
        assert _mandat(motif=motif).motif is motif

    def test_l_assistance_remplace_un_role_transversal(self):
        """Le dépannage se trace et il finit, au lieu d'ouvrir un accès permanent à tous
        les locataires."""
        mandat = _mandat(motif=MotifMandat.ASSISTANCE, fin=date(2026, 6, 20))
        assert mandat.en_vigueur(PENDANT)
        assert not mandat.en_vigueur(date(2026, 6, 20))
