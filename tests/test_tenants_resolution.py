"""Du nom d'hôte au slug, et du statut au verdict.

Sixième pas du socle multi-tenant. Ces deux décisions sont prises des milliers de fois par
jour sur le chemin critique de chaque requête, et **une résolution fautive ne se voit
pas** : elle sert simplement le mauvais client.

D'où le nombre de cas limites. Ils s'écrivent sans base, sans réseau et sans horloge, ce
qui est précisément l'intérêt d'avoir gardé cette décision dans le domaine.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.contextes.tenants.application.cycle_de_vie import (
    abandonner,
    activer,
    avancer,
    ouvrir,
    resilier,
    suspendre,
)
from app.contextes.tenants.domaine.resolution import (
    Verdict,
    slug_depuis_hote,
    verdict_pour,
)
from app.contextes.tenants.domaine.tenant import (
    ETAPES_ORDONNEES,
    NatureTenant,
    Tenant,
)

RACINE = "cga.cm"
LE_JOUR = date(2026, 9, 9)


class TestSlugDepuisHote:
    @pytest.mark.parametrize(
        ("hote", "attendu"),
        [
            ("station-bonaberi.cga.cm", "station-bonaberi"),
            ("brcg.cga.cm", "brcg"),
            ("a1b.cga.cm", "a1b"),
        ],
    )
    def test_un_sous_domaine_porte_son_slug(self, hote: str, attendu: str):
        assert slug_depuis_hote(hote, RACINE) == attendu

    def test_le_port_ne_fait_pas_partie_du_nom(self):
        """L'en-tête `Host` le porte dès qu'il n'est pas celui par défaut, ce qui est le
        cas de tout environnement de développement."""
        assert slug_depuis_hote("station.cga.cm:8000", RACINE) == "station"

    def test_la_casse_ne_compte_pas(self):
        """Un nom d'hôte est insensible à la casse, et un navigateur peut l'envoyer tel
        que l'utilisateur l'a tapé."""
        assert slug_depuis_hote("STATION.CGA.CM", RACINE) == "station"

    def test_le_point_final_de_la_forme_qualifiee_est_retire(self):
        """`station.cga.cm.` est la forme pleinement qualifiée. Rare, parfaitement
        valide, et elle passerait pour un domaine étranger si on ne la traitait pas."""
        assert slug_depuis_hote("station.cga.cm.", RACINE) == "station"

    def test_la_racine_est_aussi_normalisee(self):
        assert slug_depuis_hote("station.cga.cm", "CGA.CM.") == "station"

    @pytest.mark.parametrize(
        "hote",
        ["cga.cm", "cga.cm:8000", "cga.cm."],
    )
    def test_le_domaine_nu_ne_porte_aucun_slug(self, hote: str):
        assert slug_depuis_hote(hote, RACINE) is None

    def test_un_sous_sous_domaine_est_refuse(self):
        """Le certificat générique ne couvre qu'un niveau. `compta.station.cga.cm`
        n'aurait pas de certificat valide, et le navigateur avertirait avant même
        d'atteindre l'application."""
        assert slug_depuis_hote("compta.station.cga.cm", RACINE) is None

    @pytest.mark.parametrize(
        "hote",
        ["autre-domaine.com", "station.autre.cm", "localhost", "127.0.0.1", "cga.cm.evil.com"],
    )
    def test_ce_qui_n_est_pas_sous_la_racine_ne_porte_rien(self, hote: str):
        """Le dernier cas mérite l'attention : `cga.cm.evil.com` **contient** la racine
        sans en dépendre. Une comparaison par `in` au lieu d'un suffixe l'accepterait, et
        un attaquant servirait ses propres pages sous notre nom."""
        assert slug_depuis_hote(hote, RACINE) is None

    def test_une_adresse_ipv6_ne_porte_rien(self):
        assert slug_depuis_hote("[::1]:8000", RACINE) is None

    @pytest.mark.parametrize("hote", [None, "", "   "])
    def test_un_hote_absent_ne_leve_pas(self, hote: str | None):
        """Un nom d'hôte inattendu est courant — une sonde, un scanner, un client mal
        configuré. Lever ferait de chacun une erreur à diagnostiquer."""
        assert slug_depuis_hote(hote, RACINE) is None

    def test_une_racine_absente_ne_leve_pas(self):
        assert slug_depuis_hote("station.cga.cm", "") is None


class TestVerdict:
    """Ce que mérite une requête, selon l'état du tenant visé."""

    @staticmethod
    def _tenant(nature: NatureTenant = NatureTenant.ENTREPRISE) -> Tenant:
        tenant = ouvrir("tnt_1", "station", nature)
        for etape in ETAPES_ORDONNEES[1:]:
            tenant = avancer(tenant, etape)
        return tenant

    def test_un_tenant_actif_est_servi(self):
        assert verdict_pour(activer(self._tenant(), LE_JOUR)) is Verdict.SERVIR

    def test_un_tenant_suspendu_demande_a_regulariser(self):
        """Le client doit comprendre pourquoi il n'entre plus, et savoir quoi faire. Un
        404 le laisserait croire à une panne."""
        suspendu = suspendre(activer(self._tenant(), LE_JOUR), "honoraires échus", LE_JOUR)
        assert verdict_pour(suspendu) is Verdict.REGULARISER

    def test_un_tenant_resilie_a_disparu(self):
        """On ne lui apprend rien qu'il ignore : il sait déjà qu'il est parti."""
        resilie = resilier(activer(self._tenant(), LE_JOUR), "départ", LE_JOUR)
        assert verdict_pour(resilie) is Verdict.DISPARU

    def test_un_tenant_inconnu_est_introuvable(self):
        assert verdict_pour(None) is Verdict.INTROUVABLE

    def test_une_ouverture_en_cours_est_introuvable_elle_aussi(self):
        """Délibéré. Distinguer les deux dirait à un inconnu qu'un slug est pris, donc
        qu'un client est en train d'arriver."""
        assert verdict_pour(ouvrir("tnt_2", "futur", NatureTenant.ENTREPRISE)) is (
            Verdict.INTROUVABLE
        )

    def test_une_ouverture_echouee_est_introuvable(self):
        echoue = abandonner(self._tenant(), "stockage injoignable")
        assert verdict_pour(echoue) is Verdict.INTROUVABLE

    def test_un_inconnu_et_une_ouverture_en_cours_sont_indiscernables(self):
        """La propriété énoncée comme un test : c'est elle qui empêche d'énumérer les
        sous-domaines pour découvrir le portefeuille du cabinet."""
        inconnu = verdict_pour(None)
        en_cours = verdict_pour(ouvrir("tnt_3", "futur", NatureTenant.ENTREPRISE))
        assert inconnu is en_cours

    def test_aucun_statut_ne_reste_sans_verdict(self):
        """Le jour où un statut s'ajoute, ce test rappelle qu'il lui faut un verdict.
        Sans lui, le nouveau statut tomberait dans le cas par défaut, et un tenant
        deviendrait introuvable sans que personne l'ait décidé."""
        from app.contextes.tenants.domaine.tenant import StatutTenant

        couverts = {
            StatutTenant.ACTIF: Verdict.SERVIR,
            StatutTenant.SUSPENDU: Verdict.REGULARISER,
            StatutTenant.RESILIE: Verdict.DISPARU,
            StatutTenant.EN_OUVERTURE: Verdict.INTROUVABLE,
            StatutTenant.ECHEC: Verdict.INTROUVABLE,
        }
        assert set(couverts) == set(StatutTenant), (
            "un statut de tenant n'a pas de verdict déclaré ici"
        )


class TestLesVerdictsSontDesCodes:
    def test_ils_valent_leur_code_de_statut(self):
        """Les traduire ailleurs ajouterait une table à tenir à jour, et l'oubli se
        verrait en production."""
        assert (Verdict.SERVIR, Verdict.REGULARISER, Verdict.DISPARU, Verdict.INTROUVABLE) == (
            200,
            402,
            410,
            404,
        )

    def test_aucun_verdict_n_est_un_403(self):
        """Un 403 apprendrait au demandeur que le tenant existe. Énumérer les
        sous-domaines deviendrait un moyen de découvrir le portefeuille du cabinet."""
        assert 403 not in {int(verdict) for verdict in Verdict}
