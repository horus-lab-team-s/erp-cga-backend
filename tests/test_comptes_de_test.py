"""Les comptes d'essai par rôle (8 novembre 2026) : voir `app/comptes_de_test.py`."""

from __future__ import annotations

from app.comptes_de_test import (
    ROLES_PAR_DEFAUT,
    courriel_de_test,
    creer_les_comptes_de_test,
    principal,
    retirer_les_comptes_de_test,
)
from app.contextes.transverse.domaine.roles import Role
from tests.conftest import exige_postgresql


def test_le_courriel_d_essai_se_derive_de_la_base():
    assert courriel_de_test("TchambaEdwin@gmail.com", Role.CHARGE_CLIENTELE) == (
        "tchambaedwin+charge-clientele@gmail.com"
    )
    assert courriel_de_test("a+x@b.cm", Role.CAISSE) == "a+caisse@b.cm"


def test_super_admin_n_est_pas_cree_par_defaut():
    assert Role.SUPER_ADMIN not in ROLES_PAR_DEFAUT


def test_une_base_invalide_est_refusee(capsys):
    assert principal(["--base", "pas-une-adresse"]) == 2


@exige_postgresql
class TestLesComptesDEssai:
    def test_un_compte_actif_par_role_puis_suspendu(self, plateforme):
        from app.contextes.transverse.adaptateurs.entrant.dependances import unite_de_travail
        from app.contextes.transverse.adaptateurs.sortant.empreinte_argon2 import (
            ServiceEmpreinteArgon2,
        )
        from app.contextes.transverse.domaine.identites import EtatCompte
        from app.partage.locataire import etabli

        locataire = "tnt-comptes-essai"
        bilan = creer_les_comptes_de_test(
            base="edwin@essai.cm", niu="M081234567890P", locataire=locataire
        )
        crees = {role: (courriel, mdp) for role, courriel, mdp in bilan}
        # Sans --creation, le client en création est sauté.
        assert crees[Role.CLIENT_CREATION][1].startswith("sauté")
        with etabli(locataire), unite_de_travail(locataire) as boutique:
            for role in (Role.COMPTABLE, Role.ADHERENT, Role.DIRECTION):
                courriel, mdp = crees[role]
                compte = boutique.comptes.par_courriel(courriel)
                assert compte is not None and compte.etat is EtatCompte.ACTIF
                assert ServiceEmpreinteArgon2().verifier(mdp, compte.empreinte_mot_de_passe)
                (habilitation,) = boutique.habilitations.pour_compte(compte.identifiant)
                assert habilitation.role is role
            adherent = boutique.comptes.par_courriel(crees[Role.ADHERENT][0])
            (h_adherent,) = boutique.habilitations.pour_compte(adherent.identifiant)
            assert h_adherent.portee == frozenset({"M081234567890P"})

        # Rejoué : rien n'est recréé ni changé.
        rejoue = creer_les_comptes_de_test(base="edwin@essai.cm", locataire=locataire)
        assert all(mdp.startswith(("existe déjà", "sauté")) for _, _, mdp in rejoue)

        suspendus = retirer_les_comptes_de_test(base="edwin@essai.cm", locataire=locataire)
        assert len(suspendus) == len(ROLES_PAR_DEFAUT) - 1  # le client en création n'existe pas
        with etabli(locataire), unite_de_travail(locataire) as boutique:
            compte = boutique.comptes.par_courriel(crees[Role.COMPTABLE][0])
            assert compte is not None and compte.etat is EtatCompte.SUSPENDU
