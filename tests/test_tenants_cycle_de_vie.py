"""Le cycle de vie d'un tenant : les transitions permises, et surtout celles qui ne le sont pas.

Deuxième pas du socle multi-tenant. Purement du domaine : aucune base, aucune horloge, la
date est passée en argument. C'est ce qui permet d'écrire les cas de reprise sans montage,
alors qu'ils sont précisément ceux qu'on ne teste jamais en intégration.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.contextes.tenants.application.cycle_de_vie import (
    REPRISES_MAXIMALES,
    abandonner,
    activer,
    avancer,
    ouvrir,
    reactiver,
    reprendre,
    resilier,
    suspendre,
)
from app.contextes.tenants.domaine.tenant import (
    ETAPES_ORDONNEES,
    EtapeOuverture,
    NatureTenant,
    StatutTenant,
    Tenant,
    TransitionInterdite,
    etape_suivante,
)

LE_JOUR = date(2026, 9, 9)
PLUS_TARD = date(2026, 11, 30)


def _neuf() -> Tenant:
    return ouvrir("tnt_001", "station-bonaberi", NatureTenant.ENTREPRISE)


def _pret() -> Tenant:
    """Un tenant dont les sept étapes sont franchies, prêt à basculer."""
    tenant = _neuf()
    for etape in ETAPES_ORDONNEES[1:]:
        tenant = avancer(tenant, etape)
    return tenant


def _actif() -> Tenant:
    return activer(_pret(), LE_JOUR)


class TestOuverture:
    def test_un_tenant_naît_avec_son_slug_deja_reserve(self):
        """L'ordre importe : le slug est pris en base avant que le tenant existe."""
        tenant = _neuf()
        assert tenant.statut is StatutTenant.EN_OUVERTURE
        assert tenant.etape_atteinte is EtapeOuverture.SLUG_RESERVE

    def test_il_ne_sert_pas_encore_les_requetes(self):
        assert not _neuf().sert_les_requetes

    def test_il_n_existe_pas_encore_publiquement(self):
        """Son sous-domaine rend 404, ce qui est le bon comportement : il répond déjà,
        grâce à l'enregistrement DNS générique, bien avant qu'aucun tenant n'existe."""
        assert not _neuf().existe_publiquement


class TestFranchirLesEtapes:
    def test_on_avance_d_un_cran(self):
        tenant = avancer(_neuf(), EtapeOuverture.LIGNE_CREEE)
        assert tenant.etape_atteinte is EtapeOuverture.LIGNE_CREEE

    def test_rejouer_l_etape_courante_est_sans_effet(self):
        """Le cas du message livré deux fois par le bus. L'appelant a bien fait son
        travail, simplement il l'avait déjà fait : on rend le tenant tel quel."""
        tenant = avancer(_neuf(), EtapeOuverture.LIGNE_CREEE)
        assert avancer(tenant, EtapeOuverture.LIGNE_CREEE) == tenant

    def test_sauter_une_etape_est_refuse(self):
        """Le saut le plus tentant laisse un schéma sans ses migrations, qui accepte les
        écritures et perd les colonnes."""
        with pytest.raises(TransitionInterdite, match="Sauter une étape"):
            avancer(_neuf(), EtapeOuverture.STOCKAGE_OUVERT)

    def test_revenir_en_arriere_est_refuse(self):
        """Revenir puis avancer de nouveau créerait un second schéma pour le même tenant."""
        tenant = avancer(_neuf(), EtapeOuverture.LIGNE_CREEE)
        with pytest.raises(TransitionInterdite, match="retour de"):
            avancer(tenant, EtapeOuverture.SLUG_RESERVE)

    def test_rejouer_la_derniere_etape_reste_sans_effet(self):
        """L'idempotence vaut jusqu'au bout : le dernier message du provisionnement peut
        être rejoué comme les autres."""
        assert avancer(_pret(), EtapeOuverture.PRET) == _pret()

    def test_revenir_en_arriere_depuis_le_bout_est_refuse_comme_un_retour(self):
        """Et non comme une avance impossible : le diagnostic n'est pas le même, et le
        message doit dire lequel des deux s'est produit."""
        with pytest.raises(TransitionInterdite, match="retour de"):
            avancer(_pret(), EtapeOuverture.LIGNE_CREEE)

    def test_on_n_avance_pas_un_tenant_actif(self):
        with pytest.raises(TransitionInterdite, match="EN_OUVERTURE"):
            avancer(_actif(), EtapeOuverture.LIGNE_CREEE)

    def test_les_sept_etapes_se_franchissent_dans_l_ordre(self):
        tenant = _neuf()
        franchies = [tenant.etape_atteinte]
        for etape in ETAPES_ORDONNEES[1:]:
            tenant = avancer(tenant, etape)
            franchies.append(tenant.etape_atteinte)
        assert franchies == list(ETAPES_ORDONNEES)
        assert tenant.ouverture_achevee

    def test_l_etape_suivante_de_la_derniere_est_absente(self):
        assert etape_suivante(EtapeOuverture.PRET) is None


class TestActivation:
    def test_le_sous_domaine_se_met_a_repondre(self):
        tenant = _actif()
        assert tenant.statut is StatutTenant.ACTIF
        assert tenant.sert_les_requetes
        assert tenant.existe_publiquement
        assert tenant.ouvert_le == LE_JOUR

    def test_activer_avant_la_fin_des_etapes_est_refuse(self):
        """Activer maintenant donnerait un tenant joignable dont le schéma, le stockage ou
        le compte administrateur manquent."""
        partiel = avancer(_neuf(), EtapeOuverture.LIGNE_CREEE)
        with pytest.raises(TransitionInterdite, match="s'arrête à"):
            activer(partiel, LE_JOUR)

    def test_activer_deux_fois_est_refuse(self):
        with pytest.raises(TransitionInterdite):
            activer(_actif(), LE_JOUR)


class TestEchecEtReprise:
    def test_une_ouverture_echoue_avec_son_motif(self):
        echoue = abandonner(_pret(), "stockage objet injoignable")
        assert echoue.statut is StatutTenant.ECHEC
        assert echoue.motif == "stockage objet injoignable"

    def test_un_echec_ne_detruit_rien(self):
        """Un schéma orphelin coûte quelques mégaoctets ; un schéma détruit à tort coûte
        une comptabilité."""
        avant = avancer(avancer(_neuf(), EtapeOuverture.LIGNE_CREEE), EtapeOuverture.SCHEMA_CREE)
        echoue = abandonner(avant, "incident")
        assert echoue.etape_atteinte is EtapeOuverture.SCHEMA_CREE

    def test_la_reprise_repart_de_l_etape_atteinte(self):
        """La distinction qui justifie tout le champ. Recommencer à zéro créerait un second
        schéma pour le même tenant."""
        bloque = abandonner(
            avancer(avancer(_neuf(), EtapeOuverture.LIGNE_CREEE), EtapeOuverture.SCHEMA_CREE),
            "incident",
        )
        repris = reprendre(bloque)
        assert repris.statut is StatutTenant.EN_OUVERTURE
        assert repris.etape_atteinte is EtapeOuverture.SCHEMA_CREE, "et non SLUG_RESERVE"

    def test_la_reprise_se_compte(self):
        tenant = abandonner(_neuf(), "incident")
        assert reprendre(tenant).reprises == 1

    def test_les_reprises_s_arretent_apres_le_seuil(self):
        """Une reprise qui boucle indéfiniment masque un défaut au lieu de le signaler."""
        tenant = _neuf().model_copy(
            update={
                "statut": StatutTenant.ECHEC,
                "motif": "incident",
                "reprises": REPRISES_MAXIMALES,
            }
        )
        with pytest.raises(TransitionInterdite, match="reprises déjà tentées"):
            reprendre(tenant)

    def test_on_ne_reprend_pas_un_tenant_actif(self):
        with pytest.raises(TransitionInterdite):
            reprendre(_actif())

    def test_on_n_abandonne_pas_un_tenant_actif(self):
        with pytest.raises(TransitionInterdite):
            abandonner(_actif(), "incident")


class TestSuspension:
    def test_les_donnees_restent_intactes(self):
        """Une suspension n'est pas une résiliation anticipée : le client qui régularise
        doit retrouver son espace exactement comme il l'a laissé."""
        suspendu = suspendre(_actif(), "honoraires échus", PLUS_TARD)
        assert suspendu.statut is StatutTenant.SUSPENDU
        assert suspendu.ouvert_le == LE_JOUR, "la date d'ouverture ne bouge pas"

    def test_le_sous_domaine_repond_encore(self):
        """Une page de régularisation, pas un 404 : le client doit comprendre pourquoi il
        n'entre plus, et savoir quoi faire."""
        suspendu = suspendre(_actif(), "honoraires échus", PLUS_TARD)
        assert suspendu.existe_publiquement
        assert not suspendu.sert_les_requetes

    def test_la_reactivation_est_instantanee(self):
        rendu = reactiver(suspendre(_actif(), "honoraires échus", PLUS_TARD))
        assert rendu.statut is StatutTenant.ACTIF
        assert rendu.suspendu_le is None
        assert rendu.ouvert_le == LE_JOUR, "celle du premier jour, pas celle du retour"

    def test_on_ne_suspend_pas_ce_qui_n_est_pas_actif(self):
        with pytest.raises(TransitionInterdite):
            suspendre(_neuf(), "motif", PLUS_TARD)

    def test_on_ne_reactive_pas_ce_qui_n_est_pas_suspendu(self):
        with pytest.raises(TransitionInterdite):
            reactiver(_actif())


class TestResiliation:
    def test_elle_est_terminale(self):
        resilie = resilier(_actif(), "départ du client", PLUS_TARD)
        assert resilie.statut is StatutTenant.RESILIE
        with pytest.raises(TransitionInterdite, match="déjà RESILIE"):
            resilier(resilie, "encore", PLUS_TARD)

    def test_le_slug_reste_porte_par_le_tenant(self):
        """Il reste réservé définitivement : le libérer enverrait les anciens liens d'un
        client chez un concurrent."""
        resilie = resilier(_actif(), "départ", PLUS_TARD)
        assert resilie.slug == "station-bonaberi"

    def test_un_tenant_suspendu_se_resilie(self):
        suspendu = suspendre(_actif(), "impayé", PLUS_TARD)
        assert resilier(suspendu, "impayé définitif", PLUS_TARD).statut is StatutTenant.RESILIE

    def test_une_ouverture_en_cours_s_abandonne_et_ne_se_resilie_pas(self):
        """La distinction compte : un abandon n'a jamais eu de client à prévenir."""
        with pytest.raises(TransitionInterdite, match="s'abandonne"):
            resilier(_neuf(), "motif", PLUS_TARD)

    def test_un_tenant_resilie_ne_sert_plus_rien(self):
        resilie = resilier(_actif(), "départ", PLUS_TARD)
        assert not resilie.sert_les_requetes
        assert not resilie.existe_publiquement


class TestInvariants:
    """Ce que le modèle refuse d'être, quelle que soit la transition qui l'y amènerait."""

    def test_un_tenant_actif_porte_sa_date_d_ouverture(self):
        with pytest.raises(ValidationError, match="date d'ouverture"):
            Tenant(
                identifiant="tnt_x",
                slug="essai",
                nature=NatureTenant.ENTREPRISE,
                statut=StatutTenant.ACTIF,
                etape_atteinte=EtapeOuverture.PRET,
            )

    @pytest.mark.parametrize(
        "statut", [StatutTenant.SUSPENDU, StatutTenant.RESILIE, StatutTenant.ECHEC]
    )
    def test_un_tenant_coupe_porte_un_motif(self, statut: StatutTenant):
        """Un tenant coupé sans motif est un incident qu'on ne saura pas expliquer au
        client qui appelle."""
        with pytest.raises(ValidationError, match="motif"):
            Tenant(
                identifiant="tnt_x",
                slug="essai",
                nature=NatureTenant.ENTREPRISE,
                statut=statut,
                etape_atteinte=EtapeOuverture.PRET,
                ouvert_le=LE_JOUR,
                suspendu_le=PLUS_TARD,
                resilie_le=PLUS_TARD,
            )

    def test_un_tenant_est_immuable(self):
        with pytest.raises(ValidationError):
            _actif().statut = StatutTenant.SUSPENDU

    def test_chaque_transition_rend_un_nouvel_objet(self):
        """C'est ce qui permet de comparer l'avant et l'après, et ce qui empêche un cas
        d'usage d'altérer un tenant qu'un autre tient déjà."""
        avant = _actif()
        apres = suspendre(avant, "motif", PLUS_TARD)
        assert avant.statut is StatutTenant.ACTIF
        assert apres is not avant
