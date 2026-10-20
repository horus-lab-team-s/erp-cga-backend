"""La matrice des rôles, réglable par le cabinet (18 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE GÉRANT A DEMANDÉ

« Un RBAC dynamique, qui ressorte la matrice des rôles et cache les vues dont un rôle
n'a pas le droit » ; le super administrateur règle, et des rôles se créent.

⚠️ CE QUI EST GARDÉ
* chaque droit a un libellé : une case sans nom se cocherait au hasard ;
* les bornes : droits réservés au super administrateur, qu'il ne perd pas ; rôle
  externe borné aux droits externes ; gestes du client interdits aux collaborateurs ;
* `permissions_de` lit la matrice du cabinet, rôles créés compris ;
* l'administrateur ne s'accorde pas le rôle de super administrateur ;
* par les routes : régler exige le second facteur et un motif, la session suivante
  du collaborateur porte le nouveau droit, le journal garde l'avant et l'après.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date

import pytest

from app.contextes.transverse.domaine.roles import (
    LIBELLES_PERMISSION,
    PERMISSIONS_PAR_ROLE,
    FicheDeRole,
    Permission,
    Role,
    installer_la_source_des_roles,
    matrice_en_vigueur,
    permissions_de,
)
from tests.conftest import enroler_par_le_courriel, exige_postgresql, ouvrir_une_session

SUPER_ADMIN = "h.kamga@cga-brcg.cm"
ADMINISTRATEUR = "s.onana@cga-brcg.cm"
CAISSIERE = "n.essama@cga-brcg.cm"


def _fiche(code: str, permissions: set[Permission], **autres) -> FicheDeRole:
    livre = code in Role.__members__
    return FicheDeRole(
        code=code,
        libelle=autres.pop("libelle", "un rôle"),
        permissions=frozenset(permissions),
        systeme=livre,
        externe=livre and Role(code) in {Role.ADHERENT, Role.INSPECTEUR, Role.CLIENT_CREATION},
        **autres,
    )


class TestLesBornes:
    def test_chaque_droit_a_son_libelle(self):
        assert set(LIBELLES_PERMISSION) == set(Permission)

    def test_le_super_administrateur_garde_ce_qui_regle_la_matrice(self):
        with pytest.raises(ValueError, match="garde GERER_ROLES"):
            _fiche("SUPER_ADMIN", {Permission.GERER_COMPTES, Permission.ACCORDER_FAVEURS})

    def test_aucun_autre_role_ne_recoit_les_droits_reserves(self):
        with pytest.raises(ValueError, match="n'appartiennent qu'au super administrateur"):
            _fiche("ADMINISTRATEUR", {Permission.GERER_COMPTES, Permission.GERER_ROLES})
        with pytest.raises(ValueError, match="n'appartiennent qu'au super administrateur"):
            _fiche("RESSOURCES_HUMAINES", {Permission.ACCORDER_FAVEURS})

    def test_un_adherent_ne_valide_pas_d_ecriture(self):
        with pytest.raises(ValueError, match="un rôle externe ne reçoit pas VALIDER_ECRITURE"):
            _fiche("ADHERENT", {Permission.LIRE_DOSSIER, Permission.VALIDER_ECRITURE})

    def test_un_collaborateur_ne_regle_pas_les_honoraires_de_tous(self):
        with pytest.raises(ValueError, match="REGLER_SES_HONORAIRES"):
            _fiche("COMPTABLE", {Permission.LIRE_DOSSIER, Permission.REGLER_SES_HONORAIRES})

    def test_un_role_du_cabinet_est_interne_et_ne_prend_pas_un_code_livre(self):
        with pytest.raises(ValueError, match="rôle de collaborateur"):
            FicheDeRole(
                code="PARTENAIRE",
                libelle="partenaire",
                permissions=frozenset(),
                systeme=False,
                externe=True,
            )
        with pytest.raises(ValueError, match="code d'un rôle livré"):
            FicheDeRole(
                code="COMPTABLE",
                libelle="comptable bis",
                permissions=frozenset(),
                systeme=False,
                externe=False,
            )


class TestLaMatriceEnVigueur:
    def test_permissions_de_lit_ce_que_le_cabinet_a_regle(self):
        rh = _fiche(
            "RESSOURCES_HUMAINES", {Permission.EDITER_VITRINE}, libelle="ressources humaines"
        )
        caisse = _fiche(
            "CAISSE", set(PERMISSIONS_PAR_ROLE[Role.CAISSE]) | {Permission.LIRE_DOSSIER}
        )
        from app.contextes.transverse.domaine import roles

        precedente = roles._source_des_roles
        installer_la_source_des_roles(lambda: [rh, caisse])
        try:
            assert permissions_de(["RESSOURCES_HUMAINES"]) == {Permission.EDITER_VITRINE}
            assert Permission.LIRE_DOSSIER in permissions_de([Role.CAISSE])
            assert matrice_en_vigueur()["COMPTABLE"].systeme  # le reste, livré
            installer_la_source_des_roles(lambda: [])
            assert permissions_de([Role.CAISSE]) == PERMISSIONS_PAR_ROLE[Role.CAISSE]
            # Un rôle que la matrice ne connaît plus ne donne rien.
            assert permissions_de(["RESSOURCES_HUMAINES"]) == frozenset()
        finally:
            installer_la_source_des_roles(precedente)


def _renforcer(client, secret: str | None = None, *, decalage: int = 0) -> str:
    """Renforce la session ; sans `secret`, enrôle d'abord. Rend le secret, pour une
    seconde session du même compte (décalée d'un pas : un code ne se rejoue pas)."""
    from datetime import timedelta

    from app.contextes.transverse.api import code_attendu
    from app.partage.horloge import maintenant

    secret = secret or enroler_par_le_courriel(client)
    code = code_attendu(secret, maintenant() + timedelta(seconds=decalage))
    renfort = client.post("/transverse/session/renforcement", json={"code": code})
    assert renfort.status_code == 200, renfort.text
    return secret


def _en_tant_que(client, courriel: str) -> None:
    client.cookies.clear()
    ouvrir_une_session(client, courriel)


MOTIF = "note de service du 18 octobre 2026"
#: Les droits livrés de la caisse : encaisser, et voir les trois familles de versements.
CAISSE_LIVREE = sorted(p.value for p in PERMISSIONS_PAR_ROLE[Role.CAISSE])


@exige_postgresql
class TestParLesRoutes:
    def test_regler_exige_le_second_facteur(self, plateforme):
        client = plateforme
        _en_tant_que(client, SUPER_ADMIN)

        refus = client.put(
            "/transverse/matrice-des-roles/CAISSE",
            json={"permissions": [*CAISSE_LIVREE, "LIRE_DOSSIER"], "motif": MOTIF},
        )

        assert refus.status_code == 403 and "SecondFacteurRequis" in refus.text, refus.text

    def test_la_caissiere_recoit_le_droit_regle_a_sa_session_suivante(self, plateforme):
        client = plateforme
        _en_tant_que(client, CAISSIERE)
        assert "LIRE_DOSSIER" not in client.get("/transverse/moi").json()["permissions"]

        _en_tant_que(client, SUPER_ADMIN)
        secret = _renforcer(client)
        regle = client.put(
            "/transverse/matrice-des-roles/CAISSE",
            json={"permissions": [*CAISSE_LIVREE, "LIRE_DOSSIER"], "motif": MOTIF},
        )
        assert regle.status_code == 200, regle.text
        assert regle.json()["regle"] is True

        _en_tant_que(client, CAISSIERE)
        assert "LIRE_DOSSIER" in client.get("/transverse/moi").json()["permissions"]

        # Revenir à la matrice livrée efface le réglage.
        _en_tant_que(client, SUPER_ADMIN)
        _renforcer(client, secret, decalage=30)
        retour = client.put(
            "/transverse/matrice-des-roles/CAISSE",
            json={"permissions": CAISSE_LIVREE, "motif": MOTIF},
        )
        assert retour.json()["regle"] is False
        matrice = client.get("/transverse/matrice-des-roles").json()
        caisse = next(r for r in matrice["roles"] if r["code"] == "CAISSE")
        assert caisse["permissions"] == CAISSE_LIVREE and matrice["reglable"] is True

    def test_un_role_cree_s_accorde_puis_ne_se_retire_pas_tant_qu_il_est_tenu(self, plateforme):
        client = plateforme
        _en_tant_que(client, SUPER_ADMIN)
        _renforcer(client)
        cree = client.post(
            "/transverse/matrice-des-roles",
            json={
                "code": "ressources_humaines",
                "libelle": "ressources humaines",
                "permissions": ["EDITER_VITRINE"],
                "motif": MOTIF,
            },
        )
        assert cree.status_code == 201, cree.text
        assert cree.json()["code"] == "RESSOURCES_HUMAINES"

        invite = client.post(
            "/transverse/comptes/invitation",
            json={
                "courriel": "rh.essai@cga-brcg.cm",
                "nom": "ESSAI",
                "prenom": "Rh",
                "role": "RESSOURCES_HUMAINES",
                "depuis": date.today().isoformat(),
            },
        )
        assert invite.status_code == 201, invite.text

        refus = client.post(
            "/transverse/matrice-des-roles/RESSOURCES_HUMAINES/retrait", json={"motif": MOTIF}
        )
        assert refus.status_code == 422 and "encore tenu" in refus.text, refus.text

        journal = client.get("/transverse/audit", params={"objet_type": "role"})
        assert journal.status_code == 200, journal.text
        creation = next(e for e in journal.json() if e["action"] == "roles.creation")
        assert creation["motif"] == MOTIF and creation["apres"]["permissions"] == ["EDITER_VITRINE"]

    def test_l_administrateur_ne_s_accorde_pas_le_super_administrateur(self, plateforme):
        client = plateforme
        _en_tant_que(client, ADMINISTRATEUR)

        refus = client.post(
            "/transverse/comptes/invitation",
            json={
                "courriel": "intrus@cga-brcg.cm",
                "nom": "INTRUS",
                "prenom": "Un",
                "role": "SUPER_ADMIN",
                "depuis": date.today().isoformat(),
            },
        )

        assert refus.status_code == 403, refus.text
        # Il lit la matrice, il ne la règle pas.
        lue = client.get("/transverse/matrice-des-roles")
        assert lue.status_code == 200 and lue.json()["reglable"] is False
        regle = client.put(
            "/transverse/matrice-des-roles/CAISSE",
            json={"permissions": ["ENCAISSER"], "motif": MOTIF},
        )
        assert regle.status_code == 403
