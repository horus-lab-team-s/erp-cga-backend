"""La passerelle : du nom d'hôte au locataire établi, ou au refus.

Sixième pas du socle multi-tenant, éprouvé de bout en bout par l'application réelle.

Les cas de résolution pure sont dans `test_tenants_resolution.py`. Ici on vérifie ce que
le branchement produit : qu'un sous-domaine inconnu ne sert rien, qu'un tenant suspendu
obtient de quoi comprendre, et que le locataire établi est bien celui du domaine.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.contextes.tenants.api import NatureTenant, RepertoireEnMemoire
from app.contextes.tenants.application.cycle_de_vie import (
    activer,
    avancer,
    ouvrir,
    resilier,
    suspendre,
)
from app.contextes.tenants.domaine.tenant import ETAPES_ORDONNEES, Tenant
from app.contextes.transverse.adaptateurs.entrant.dependances import (
    IntergicielUniteDeTravail,
    repertoire_des_tenants,
)
from app.partage.locataire import courant

LE_JOUR = date(2026, 9, 9)


def _tenant(slug: str) -> Tenant:
    tenant = ouvrir(f"tnt-{slug}", slug, NatureTenant.ENTREPRISE)
    for etape in ETAPES_ORDONNEES[1:]:
        tenant = avancer(tenant, etape)
    return activer(tenant, LE_JOUR)


@pytest.fixture
def application(monkeypatch):
    """Une application minimale derrière l'intergiciel, avec un répertoire garni.

    Minimale plutôt que l'application complète : on éprouve la passerelle, et monter les
    quatorze contextes rendrait un échec difficile à attribuer.
    """
    repertoire = RepertoireEnMemoire(
        [
            _tenant("station-bonaberi"),
            suspendre(_tenant("boulangerie"), "honoraires échus", LE_JOUR),
            resilier(_tenant("ancien-client"), "départ", LE_JOUR),
            ouvrir("tnt-futur", "futur", NatureTenant.ENTREPRISE),
        ]
    )
    repertoire_des_tenants.cache_clear()
    monkeypatch.setattr(
        "app.contextes.transverse.adaptateurs.entrant.dependances.repertoire_des_tenants",
        lambda: repertoire,
    )

    api = FastAPI()
    api.add_middleware(IntergicielUniteDeTravail)

    @api.get("/qui")
    def qui() -> dict[str, str]:
        return {"locataire": courant()}

    with TestClient(api) as client:
        yield client
    repertoire_des_tenants.cache_clear()


class TestUnTenantActif:
    def test_le_locataire_etabli_est_celui_du_domaine(self, application):
        """La garantie de fond : ce n'est plus une constante, c'est le sous-domaine."""
        reponse = application.get("/qui", headers={"host": "station-bonaberi.cga.cm"})
        assert reponse.status_code == 200
        assert reponse.json()["locataire"] == "station-bonaberi"

    def test_le_port_ne_gene_pas(self, application):
        reponse = application.get("/qui", headers={"host": "station-bonaberi.cga.cm:8000"})
        assert reponse.json()["locataire"] == "station-bonaberi"

    def test_la_casse_ne_gene_pas(self, application):
        reponse = application.get("/qui", headers={"host": "STATION-BONABERI.CGA.CM"})
        assert reponse.json()["locataire"] == "station-bonaberi"


class TestCeQuiEstRefuse:
    def test_un_sous_domaine_inconnu_est_introuvable(self, application):
        assert application.get("/qui", headers={"host": "inconnu.cga.cm"}).status_code == 404

    def test_une_ouverture_en_cours_est_introuvable_elle_aussi(self, application):
        """Distinguer les deux dirait à un inconnu qu'un slug est pris, donc qu'un client
        est en train d'arriver."""
        assert application.get("/qui", headers={"host": "futur.cga.cm"}).status_code == 404

    def test_un_tenant_suspendu_obtient_de_quoi_comprendre(self, application):
        """Un 404 le laisserait croire à une panne. Le message compte autant que le code."""
        reponse = application.get("/qui", headers={"host": "boulangerie.cga.cm"})
        assert reponse.status_code == 402
        detail = reponse.json()["detail"]
        assert "suspendu" in detail.lower()
        assert "intactes" in detail.lower(), "le client doit savoir que ses données restent"

    def test_un_tenant_resilie_a_disparu(self, application):
        assert application.get("/qui", headers={"host": "ancien-client.cga.cm"}).status_code == 410

    def test_un_sous_sous_domaine_ne_sert_pas_le_tenant(self, application):
        """Le certificat générique ne couvre qu'un niveau : `compta.station.cga.cm`
        n'aurait pas de certificat valide. Ne pas le servir est cohérent avec ce que
        l'infrastructure peut réellement présenter."""
        reponse = application.get("/qui", headers={"host": "compta.station-bonaberi.cga.cm"})
        assert reponse.json()["locataire"] != "station-bonaberi"

    def test_aucun_refus_n_est_un_403(self, application):
        """Un 403 apprendrait au demandeur que le tenant existe. Énumérer les
        sous-domaines deviendrait un moyen de découvrir le portefeuille du cabinet."""
        codes = {
            application.get("/qui", headers={"host": hote}).status_code
            for hote in ("inconnu.cga.cm", "futur.cga.cm", "boulangerie.cga.cm",
                         "ancien-client.cga.cm")
        }
        assert 403 not in codes


class TestAffordanceDeDeveloppement:
    """Ce qui se passe quand le nom d'hôte ne désigne aucun tenant."""

    @pytest.mark.parametrize("hote", ["testserver", "localhost:8000", "cga.cm"])
    def test_le_locataire_configure_prend_le_relais(self, application, hote: str):
        """`localhost`, `testserver`, le domaine nu. C'est l'affordance de développement,
        et elle disparaîtra le jour où la vitrine aura son propre service."""
        from app.infrastructure.config import configuration

        reponse = application.get("/qui", headers={"host": hote})
        assert reponse.status_code == 200
        assert reponse.json()["locataire"] == configuration().locataire_par_defaut

    def test_un_domaine_etranger_ne_sert_pas_un_tenant(self, application):
        """`cga.cm.evil.com` contient la racine sans en dépendre. Il ne doit surtout pas
        résoudre en tenant : ce serait servir nos pages sous le nom d'un attaquant."""
        from app.infrastructure.config import configuration

        reponse = application.get("/qui", headers={"host": "cga.cm.evil.com"})
        assert reponse.json()["locataire"] == configuration().locataire_par_defaut


class TestLeRepertoireParDefaut:
    def test_il_est_vide_tant_que_la_table_n_existe_pas(self):
        """Un slug inconnu **est** introuvable, et c'est le bon comportement. Le brancher
        sur la table des tenants sera une substitution d'adaptateur, rien d'autre."""
        repertoire_des_tenants.cache_clear()
        assert repertoire_des_tenants().par_slug("station-bonaberi") is None
        repertoire_des_tenants.cache_clear()

    def test_il_est_insensible_a_la_casse_comme_un_nom_d_hote(self):
        repertoire = RepertoireEnMemoire([_tenant("station")])
        assert repertoire.par_slug("STATION") is not None
        assert repertoire.par_slug("  station  ") is not None
