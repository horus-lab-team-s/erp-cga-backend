"""La boucle fermée : de l'événement déposé au tenant ouvert.

C'est ce que le document de conception annonçait comme manquant : « les sept
étapes existent, la reprise après incident aussi, la table et le répertoire
aussi. Ce qui manque est l'événement qui les relie, pas les pièces. »

Ces tests parcourent la chaîne entière — boîte d'envoi, relais, saga,
provisionneur, registre — et vérifient les trois propriétés qui la rendent sûre :

* **l'idempotence de bout en bout**, parce que le relais publie au moins une fois ;
* **la reprise à la bonne étape**, parce qu'un rejeu depuis zéro rouvrirait un
  préfixe de stockage ;
* **la distinction entre « la saga est terminée » et « le tenant est
  utilisable »**, parce que trois étapes sont substituées faute d'infrastructure.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pytest

from app.contextes.tenants.adaptateurs.sortant.provisionneur_local import (
    ProvisionneurLocal,
)
from app.contextes.tenants.adaptateurs.sortant.repertoire_memoire import (
    RegistreEnMemoire,
)
from app.contextes.tenants.application.ouverture import NOM_SAGA
from app.contextes.tenants.application.ouverture_sur_paiement import (
    ChargeIncomplete,
    abonner_l_ouverture,
    ouvrir_sur_paiement,
)
from app.contextes.tenants.domaine.substitution import (
    ouverture_reellement_complete,
    substituees,
)
from app.contextes.tenants.domaine.tenant import (
    EtapeOuverture,
    NatureTenant,
    StatutTenant,
    Tenant,
)
from app.infrastructure.depots_orchestration import (
    BoiteDEnvoiMemoire,
    DepotExecutionsMemoire,
)
from app.orchestration.boite_d_envoi import EvenementSortant, deposer
from app.orchestration.relais import Abonnements, publier_un_lot
from app.orchestration.saga import EtatSaga, abandonner

T0 = datetime(2026, 9, 10, 9, 0)
LE_JOUR = date(2026, 9, 10)


def _evenement(cle: str = "dos-1", **charge) -> EvenementSortant:
    defauts = {"tenant": "tnt-station", "slug": "station-bonaberi"}
    return EvenementSortant(
        identifiant=f"ev-{cle}",
        nom="PaiementEncaissé",
        cle=cle,
        charge={**defauts, **charge},
        cree_le=T0,
    )


def _provisionneur(registre: RegistreEnMemoire, **ports: Any) -> ProvisionneurLocal:
    return ProvisionneurLocal(registre, a_la_date=LE_JOUR, **ports)


# ── Le chemin nominal, sans infrastructure ────────────────────────────────────


class TestOuvertureSansInfrastructure:
    def test_la_saga_va_au_bout_et_le_tenant_est_actif(self):
        registre, executions = RegistreEnMemoire(), DepotExecutionsMemoire()
        resultat = ouvrir_sur_paiement(
            _evenement(),
            provisionneur=_provisionneur(registre),
            executions=executions,
            a_l_instant=T0,
        )

        assert resultat.terminee is True
        tenant = registre.par_slug("station-bonaberi")
        assert tenant.statut is StatutTenant.ACTIF
        assert tenant.etape_atteinte is EtapeOuverture.PRET

    def test_mais_le_tenant_n_est_pas_utilisable_pour_autant(self):
        """⚠️ **La distinction qui compte.** Trois étapes ont été substituées
        faute d'infrastructure : le sous-domaine répond, et il n'y a ni schéma,
        ni stockage, ni compte administrateur. Annoncer au client un espace
        ouvert serait un mensonge que sa première connexion découvrirait."""
        registre, executions = RegistreEnMemoire(), DepotExecutionsMemoire()
        resultat = ouvrir_sur_paiement(
            _evenement(),
            provisionneur=_provisionneur(registre),
            executions=executions,
            a_l_instant=T0,
        )

        assert resultat.terminee is True
        assert resultat.tenant_utilisable is False
        assert set(resultat.etapes_substituees) == {
            EtapeOuverture.SCHEMA_CREE.value,
            EtapeOuverture.STOCKAGE_OUVERT.value,
            EtapeOuverture.ADMINISTRATEUR_CREE.value,
        }

    def test_avec_les_trois_ports_le_tenant_devient_utilisable(self):
        """Le jour où l'infrastructure existe, rien ne change dans la saga : on
        lui passe trois fonctions de plus."""
        registre, executions = RegistreEnMemoire(), DepotExecutionsMemoire()
        vus: list[str] = []

        def port(nom: str):
            def _agir(contexte):
                vus.append(nom)
                return {**contexte, nom: True}
            return _agir

        resultat = ouvrir_sur_paiement(
            _evenement(),
            provisionneur=_provisionneur(
                registre,
                schema=port("schema"),
                stockage=port("stockage"),
                comptes=port("comptes"),
            ),
            executions=executions,
            a_l_instant=T0,
        )

        assert resultat.tenant_utilisable is True
        assert resultat.etapes_substituees == ()
        assert vus == ["schema", "stockage", "comptes"]


# ── L'idempotence, de bout en bout ────────────────────────────────────────────


class TestIdempotence:
    def test_le_meme_evenement_deux_fois_n_ouvre_qu_un_tenant(self):
        """Le relais publie **au moins une fois**. Sans cette garde, chaque
        arrivée ouvrirait un second préfixe de stockage et enverrait un second
        lien d'activation."""
        registre, executions = RegistreEnMemoire(), DepotExecutionsMemoire()
        appels: list[str] = []

        def compteur(nom: str):
            def _agir(contexte):
                appels.append(nom)
                return contexte
            return _agir

        provisionneur = _provisionneur(
            registre, stockage=compteur("stockage"), comptes=compteur("lien")
        )
        for _ in range(3):
            resultat = ouvrir_sur_paiement(
                _evenement(),
                provisionneur=provisionneur,
                executions=executions,
                a_l_instant=T0,
            )

        assert resultat.terminee is True
        assert appels.count("stockage") == 1
        assert appels.count("lien") == 1

    def test_un_rejeu_sur_une_saga_terminee_ne_progresse_pas(self):
        registre, executions = RegistreEnMemoire(), DepotExecutionsMemoire()
        premier = ouvrir_sur_paiement(
            _evenement(), provisionneur=_provisionneur(registre),
            executions=executions, a_l_instant=T0,
        )
        second = ouvrir_sur_paiement(
            _evenement(), provisionneur=_provisionneur(registre),
            executions=executions, a_l_instant=T0,
        )

        assert premier.a_progresse is True
        assert second.a_progresse is False

    def test_la_cle_est_celle_du_dossier_et_non_de_l_evenement(self):
        """Deux événements distincts peuvent porter le même encaissement : un
        rappel d'opérateur et une reprise manuelle. Ils doivent tomber sur la
        **même** exécution, sinon deux sagas ouvrent deux tenants."""
        registre, executions = RegistreEnMemoire(), DepotExecutionsMemoire()
        rappel = _evenement()
        reprise = rappel.model_copy(update={"identifiant": "ev-autre"})

        ouvrir_sur_paiement(rappel, provisionneur=_provisionneur(registre),
                            executions=executions, a_l_instant=T0)
        second = ouvrir_sur_paiement(reprise, provisionneur=_provisionneur(registre),
                                     executions=executions, a_l_instant=T0)

        assert second.a_progresse is False
        assert executions.trouver(NOM_SAGA, "dos-1") is not None


class TestChaqueActionEstIdempotentePriseSeule:
    """⚠️ **Le cas pour lequel toute cette machinerie existe.**

    Le moteur enregistre l'avancement **après** l'effet. Une panne entre les deux
    fait rejouer l'étape, et le rejeu porte alors sur un tenant qui a déjà subi
    l'effet.

    La saga seule ne protège pas de cela : elle ne sait pas que l'étape a été
    faite, puisque l'enregistrement a été perdu. C'est chaque action qui doit
    constater que son travail est fait et rendre le même résultat.

    Les tests de la saga ne peuvent pas le montrer, parce qu'elle saute les
    étapes qu'elle a enregistrées. Il faut donc rejouer les actions
    directement, ce que fait cette classe.
    """

    def _ouvert(self):
        registre = RegistreEnMemoire()
        provisionneur = _provisionneur(registre)
        ouvrir_sur_paiement(
            _evenement(), provisionneur=provisionneur,
            executions=DepotExecutionsMemoire(), a_l_instant=T0,
        )
        return registre, provisionneur, dict(_evenement().charge)

    @pytest.mark.parametrize(
        "action",
        [
            "reserver_le_slug",
            "creer_la_ligne",
            "creer_le_schema",
            "amorcer_le_metier",
            "ouvrir_le_stockage",
            "creer_l_administrateur",
            "basculer_pret",
        ],
    )
    def test_rejouer_une_action_sur_un_tenant_deja_ouvert_ne_casse_rien(self, action):
        registre, provisionneur, contexte = self._ouvert()
        avant = registre.par_slug("station-bonaberi")

        getattr(provisionneur, action)(contexte)

        apres = registre.par_slug("station-bonaberi")
        assert apres.statut is StatutTenant.ACTIF, f"{action} a dégradé le tenant"
        assert apres.etape_atteinte is EtapeOuverture.PRET, f"{action} a reculé l'étape"
        assert apres.ouvert_le == avant.ouvert_le, f"{action} a repoussé la date d'ouverture"

    def test_rejouer_la_bascule_ne_repousse_pas_la_date_d_ouverture(self):
        """La date d'ouverture est celle où le sous-domaine a commencé à
        répondre. La repousser à chaque rejeu ferait mentir tout indicateur de
        délai, et la facturation d'abonnement avec."""
        registre, provisionneur, contexte = self._ouvert()
        avant = registre.par_slug("station-bonaberi").ouvert_le

        provisionneur.basculer_pret(contexte)
        provisionneur.basculer_pret(contexte)

        assert registre.par_slug("station-bonaberi").ouvert_le == avant


# ── La reprise ────────────────────────────────────────────────────────────────


class TestReprise:
    def test_elle_repart_a_l_etape_qui_a_echoue(self):
        """**Le test qui justifie toute la machinerie.** Sans reprise à la bonne
        étape, chaque rejeu rouvrirait un préfixe de stockage."""
        registre, executions = RegistreEnMemoire(), DepotExecutionsMemoire()
        appels: list[str] = []
        casse = {"oui": True}

        def stockage(contexte):
            appels.append("stockage")
            return contexte

        def comptes(contexte):
            appels.append("comptes")
            if casse["oui"]:
                raise RuntimeError("service de courriel indisponible")
            return contexte

        provisionneur = _provisionneur(registre, stockage=stockage, comptes=comptes)
        bloquee = ouvrir_sur_paiement(
            _evenement(), provisionneur=provisionneur,
            executions=executions, a_l_instant=T0,
        )
        assert bloquee.terminee is False
        assert bloquee.execution.franchies[-1] == EtapeOuverture.STOCKAGE_OUVERT

        casse["oui"] = False
        fin = ouvrir_sur_paiement(
            _evenement(), provisionneur=provisionneur,
            executions=executions, a_l_instant=T0,
        )

        assert fin.terminee is True
        assert appels.count("stockage") == 1
        assert appels.count("comptes") == 2

    def test_un_echec_ne_leve_pas_et_se_consigne(self):
        """Lever ferait échouer la publication, l'événement serait rejoué, la
        saga reprise : deux mécanismes de reprise superposés produisent des
        tentatives multipliées, pas une meilleure fiabilité."""
        registre, executions = RegistreEnMemoire(), DepotExecutionsMemoire()

        def casse(contexte):
            raise RuntimeError("indisponible")

        resultat = ouvrir_sur_paiement(
            _evenement(), provisionneur=_provisionneur(registre, schema=casse),
            executions=executions, a_l_instant=T0,
        )
        assert resultat.terminee is False
        assert "indisponible" in resultat.execution.dernier_echec

    def test_le_slug_pris_par_un_autre_est_refuse_franchement(self):
        """Un slug n'est jamais réattribué : le rejeu ne le libérera pas, et il
        faut en proposer un autre au client plutôt qu'attendre."""
        registre = RegistreEnMemoire()
        registre.enregistrer(
            Tenant(identifiant="tnt-autre", slug="station-bonaberi",
                   nature=NatureTenant.ENTREPRISE)
        )
        resultat = ouvrir_sur_paiement(
            _evenement(), provisionneur=_provisionneur(registre),
            executions=DepotExecutionsMemoire(), a_l_instant=T0,
        )
        assert resultat.terminee is False
        assert "appartient déjà" in resultat.execution.dernier_echec


class TestChargeIncomplete:
    @pytest.mark.parametrize("manquant", ["tenant", "slug"])
    def test_elle_est_refusee_a_l_entree(self, manquant):
        """Découverte à la quatrième étape, elle laisserait un tenant à moitié
        fait."""
        charge = {"tenant": "tnt-1", "slug": "station"}
        charge[manquant] = ""
        with pytest.raises(ChargeIncomplete, match=manquant):
            ouvrir_sur_paiement(
                _evenement(**charge), provisionneur=_provisionneur(RegistreEnMemoire()),
                executions=DepotExecutionsMemoire(), a_l_instant=T0,
            )


# ── L'abandon ─────────────────────────────────────────────────────────────────


class TestAbandon:
    def test_il_defait_a_rebours_sans_liberer_le_slug(self):
        """Libérer le slug enverrait les anciens liens et les courriels archivés
        d'un client chez un concurrent.

        ⚠️ Le tenant finit **résilié**, pas en échec. Le domaine a refusé
        d'« abandonner » un tenant actif, et il avait raison : un tenant qui a
        été actif a existé, son sous-domaine a répondu, son lien est parti. Le
        déclarer en échec d'ouverture réécrirait cette histoire.
        """
        from app.contextes.tenants.application.ouverture import saga_d_ouverture

        registre, executions = RegistreEnMemoire(), DepotExecutionsMemoire()
        provisionneur = _provisionneur(registre)
        resultat = ouvrir_sur_paiement(
            _evenement(), provisionneur=provisionneur,
            executions=executions, a_l_instant=T0,
        )

        annulee = abandonner(
            saga_d_ouverture(provisionneur), resultat.execution, T0,
            motif="paiement contesté",
        )
        assert annulee.etat is EtatSaga.COMPENSEE
        assert annulee.compensations_en_echec == ()
        tenant = registre.par_slug("station-bonaberi")
        assert tenant is not None, "le slug a été libéré"
        assert tenant.statut is StatutTenant.RESILIE
        assert tenant.motif == "paiement contesté"

    def test_un_abandon_avant_l_activation_met_le_tenant_en_echec(self):
        """L'autre moitié de la distinction : une ouverture qui n'a jamais
        abouti échoue, elle ne se résilie pas."""
        from app.contextes.tenants.application.ouverture import saga_d_ouverture

        registre, executions = RegistreEnMemoire(), DepotExecutionsMemoire()

        def casse(contexte):
            raise RuntimeError("indisponible")

        provisionneur = _provisionneur(registre, stockage=casse)
        bloquee = ouvrir_sur_paiement(
            _evenement(), provisionneur=provisionneur,
            executions=executions, a_l_instant=T0,
        )
        annulee = abandonner(
            saga_d_ouverture(provisionneur), bloquee.execution, T0,
            motif="client rétracté",
        )
        tenant = registre.par_slug("station-bonaberi")
        assert annulee.etat is EtatSaga.COMPENSEE
        assert tenant.statut is StatutTenant.ECHEC
        assert tenant.motif == "client rétracté"


# ── La chaîne entière ─────────────────────────────────────────────────────────


class TestChaineComplete:
    """⚠️ **Le test qui ferme la boucle annoncée par le document de
    conception.** Il part d'un événement déposé dans la boîte et finit sur un
    tenant dont le sous-domaine répond, en passant par le relais et la saga.

    Si un jour il échoue, c'est que le lien entre l'encaissement et l'ouverture
    s'est rompu, ce qui est précisément l'incident que personne ne voit : le
    client a payé, et rien ne se passe.
    """

    def _monter(self):
        boite = BoiteDEnvoiMemoire()
        registre, executions = RegistreEnMemoire(), DepotExecutionsMemoire()
        abonnements = Abonnements()
        abonner_l_ouverture(
            abonnements,
            provisionneur=_provisionneur(registre),
            executions=executions,
            horloge=lambda: T0,
        )
        return boite, registre, executions, abonnements

    def test_du_paiement_encaisse_au_sous_domaine_qui_repond(self):
        boite, registre, executions, abonnements = self._monter()
        deposer(
            boite, "ev-1", "PaiementEncaissé", "dos-1",
            {"tenant": "tnt-station", "slug": "station-bonaberi"}, T0,
        )

        rapport = publier_un_lot(boite, abonnements, T0)

        assert rapport.publies == 1
        assert rapport.echecs == 0
        assert registre.par_slug("station-bonaberi").statut is StatutTenant.ACTIF
        assert executions.trouver(NOM_SAGA, "dos-1").etat is EtatSaga.TERMINEE

    def test_le_meme_rappel_rejoue_n_ouvre_rien_de_plus(self):
        """L'incident qui coûte le plus cher : un paiement encaissé deux fois
        qui ouvrirait deux tenants."""
        boite, registre, executions, abonnements = self._monter()
        deposer(boite, "ev-1", "PaiementEncaissé", "dos-1",
                {"tenant": "tnt-station", "slug": "station-bonaberi"}, T0)
        publier_un_lot(boite, abonnements, T0)

        # Le prestataire renvoie sa notification : un second événement, même clé.
        deposer(boite, "ev-2", "PaiementEncaissé", "dos-1",
                {"tenant": "tnt-station", "slug": "station-bonaberi"}, T0)
        rapport = publier_un_lot(boite, abonnements, T0)

        assert rapport.publies == 1
        assert rapport.echecs == 0
        assert registre.par_slug("station-bonaberi").statut is StatutTenant.ACTIF
        assert executions.trouver(NOM_SAGA, "dos-1").franchies == tuple(
            e.value for e in EtapeOuverture
        )

    def test_une_charge_incomplete_met_l_evenement_en_echec_et_non_le_passage(self):
        """Rejouer n'ajoutera pas les champs manquants. Il faut que cela se voie,
        sans arrêter les autres événements du lot."""
        boite, registre, _, abonnements = self._monter()
        deposer(boite, "ev-casse", "PaiementEncaissé", "dos-casse", {"tenant": ""}, T0)
        deposer(boite, "ev-bon", "PaiementEncaissé", "dos-bon",
                {"tenant": "tnt-b", "slug": "boulangerie"}, T0)

        rapport = publier_un_lot(boite, abonnements, T0)

        assert rapport.echecs == 1
        assert rapport.publies == 1
        assert registre.par_slug("boulangerie") is not None
        assert registre.par_slug("dos-casse") is None

    def test_le_tenant_ouvert_n_est_pas_annonce_utilisable(self):
        """La chaîne fonctionne, et le tenant n'est pas prêt pour autant : trois
        étapes sont substituées. Le dire est la seule façon de ne pas promettre
        au client un espace qui ne s'ouvrira pas."""
        boite, _, executions, abonnements = self._monter()
        deposer(boite, "ev-1", "PaiementEncaissé", "dos-1",
                {"tenant": "tnt-station", "slug": "station-bonaberi"}, T0)
        publier_un_lot(boite, abonnements, T0)

        execution = executions.trouver(NOM_SAGA, "dos-1")
        assert execution.etat is EtatSaga.TERMINEE
        assert ouverture_reellement_complete(execution.contexte) is False
        assert len(substituees(execution.contexte)) == 3
