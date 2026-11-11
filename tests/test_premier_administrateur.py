"""Le premier compte d'une base de production, sans le jeu de démonstration.

⚠️ CONSTATÉ À L'AUDIT AVANT LA MISE EN PRODUCTION (20 octobre 2026) : seul l'amorçage de
la démonstration écrivait des comptes, avec leur mot de passe publié, et rien ne
l'empêchait de tourner sur une base de production neuve.
"""

from __future__ import annotations

import pytest

from app.amorcage import AmorcageRefuse, amorcer
from app.premier_administrateur import BaseDejaHabitee, creer_le_premier_administrateur
from tests.conftest import exige_postgresql


def test_l_amorcage_refuse_un_environnement_expose(monkeypatch):
    from app.infrastructure import config

    reglee = config.configuration().model_copy(update={"environnement": "production"})
    monkeypatch.setattr(config, "configuration", lambda: reglee)
    with pytest.raises(AmorcageRefuse, match="premier_administrateur"):
        amorcer("tnt-essai-production")


@exige_postgresql
class TestLePremierAdministrateur:
    def test_une_base_deja_habitee_est_refusee(self, plateforme):
        with pytest.raises(BaseDejaHabitee):
            creer_le_premier_administrateur(
                courriel="nouveau@cga-brcg.cm", nom="MBALLA", prenom="Bernadette"
            )

    def test_une_base_vide_recoit_son_administrateur_sans_mot_de_passe(self, plateforme):
        from app.contextes.transverse.adaptateurs.entrant.dependances import unite_de_travail
        from app.contextes.transverse.domaine.roles import Role
        from app.partage.locataire import etabli

        locataire = "tnt-premiere-ouverture"
        lien = creer_le_premier_administrateur(
            courriel="Titulaire@Cabinet.cm", nom="NKOULOU", prenom="Serge", locataire=locataire
        )

        assert "/activation?jeton=" in lien
        with etabli(locataire), unite_de_travail(locataire) as boutique:
            (compte,) = boutique.comptes.lister()
            roles = {h.role for h in boutique.habilitations.pour_compte(compte.identifiant)}
        assert compte.courriel == "titulaire@cabinet.cm"
        assert compte.etat.value == "EN_ATTENTE_ACTIVATION"
        assert roles == {Role.ADMINISTRATEUR, Role.SUPER_ADMIN}
        with pytest.raises(BaseDejaHabitee):
            creer_le_premier_administrateur(
                courriel="second@cabinet.cm", nom="X", prenom="Y", locataire=locataire
            )
