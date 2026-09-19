"""Le balayage de relance : ce qui part, une fois, et jamais dans le désordre.

⚠️ **La propriété qui compte n'est pas « la relance part », c'est « elle ne part pas
deux fois, ni à l'envers ».** Un système de relance qui envoie trop ne se contente pas
d'agacer : sur WhatsApp, un numéro qui envoie beaucoup de messages non lus voit sa
note baisser puis ses quotas se réduire. **Relancer trop détruit le canal lui-même**,
et le canal conditionne aussi l'appel.

Ces cas gardent donc trois choses : un palier n'est employé qu'une fois, un rappel
moins urgent ne suit jamais un rappel plus urgent, et une acceptée impayée ne reçoit
aucun message.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from app.contextes.souscription.api import TarifArrete, emettre
from app.contextes.souscription.application.balayage_de_relance import (
    NOM_IMPAYEE,
    NOM_RELANCE,
    balayer_les_relances,
)
from app.contextes.souscription.domaine.relance import (
    PalierDeRelance,
    PlanDeRelance,
    SuiviDeRelance,
)
from app.infrastructure.depots_orchestration import BoiteDEnvoiMemoire

T0 = datetime(2026, 9, 1, 9, 0)


def _plan(*jours: int, impaye: int = 30) -> PlanDeRelance:
    return PlanDeRelance(
        paliers=tuple(
            PalierDeRelance(
                rang=rang, apres=timedelta(days=j), modele="cga_relance", ton=f"ton-{rang}"
            )
            for rang, j in enumerate(jours or (3, 7, 14), start=1)
        ),
        impaye_apres=timedelta(days=impaye),
    )


def _proforma(numero="PRO-2026-0001", dossier="dos-1"):
    tarif = TarifArrete(
        montant=Decimal("250000"), plancher=Decimal("200000"),
        reference=Decimal("250000"), plafond=Decimal("375000"),
        version_bareme="2026.1", chiffre_par="awono", valide_par="direction",
        arrete_le=T0,
    )
    return emettre(
        numero=numero, dossier=dossier, service="creation-sarl", tarif=tarif,
        contenu=b"%PDF", modele="creation-sarl", version_modele="2026.1", a_l_instant=T0,
    ).transmise(T0)


class _Suivis:
    """Un dépôt de suivis en mémoire, réduit à ce que le port exige."""

    def __init__(self, *suivis: SuiviDeRelance) -> None:
        self._par_proforma = {s.proforma: s for s in suivis}

    def tous(self):
        return dict(self._par_proforma)

    def enregistrer(self, suivi: SuiviDeRelance) -> None:
        self._par_proforma[suivi.proforma] = suivi


def _balayer(proformas, plan, suivis, *, a_l_instant, boite=None):
    return (
        balayer_les_relances(
            proformas, plan, suivis=suivis, boite=boite or BoiteDEnvoiMemoire(),
            a_l_instant=a_l_instant, identifiant=lambda s: f"rel-{s}",
        ),
        boite,
    )


class TestCeQuiPart:
    def test_une_proforma_trop_recente_ne_declenche_rien(self):
        suivis = _Suivis()
        rapport, _ = _balayer(
            [_proforma()], _plan(), suivis, a_l_instant=T0 + timedelta(days=1)
        )
        assert rapport.relances == ()
        assert rapport.examinees == 1, "le compteur distingue « rien à faire » de « rien lu »"

    def test_le_palier_du_est_depose_dans_la_boite(self):
        boite = BoiteDEnvoiMemoire()
        suivis = _Suivis()
        rapport, _ = _balayer(
            [_proforma()], _plan(), suivis, a_l_instant=T0 + timedelta(days=4), boite=boite
        )
        assert rapport.relances == ("PRO-2026-0001",)
        attente = boite.a_publier()
        assert [e.nom for e in attente] == [NOM_RELANCE]
        assert attente[0].charge["rang"] == 1

    def test_la_cle_est_le_numero_seul(self):
        """⚠️ La boîte tient l'ordre **par clé**. Le numéro seul sérialise donc les
        paliers d'une même proforma : le rang 3 ne peut pas partir avant le rang 2.

        La conséquence est assumée : un rang 1 en échec bloque le rang 2 de la même
        proforma. Si le premier message n'est jamais parti, envoyer le second ferait
        recevoir au client une relance de deuxième niveau pour un message qu'il n'a
        jamais eu.
        """
        boite = BoiteDEnvoiMemoire()
        _balayer([_proforma()], _plan(), _Suivis(), a_l_instant=T0 + timedelta(days=4), boite=boite)
        assert boite.a_publier()[0].cle == "PRO-2026-0001"

    def test_le_dernier_palier_est_signale(self):
        """Après lui, le système se tait. Le responsable doit le savoir : c'est le
        moment où un dossier cesse d'être suivi par la machine."""
        rapport, _ = _balayer(
            [_proforma()], _plan(), _Suivis(), a_l_instant=T0 + timedelta(days=20)
        )
        assert rapport.dernieres == ("PRO-2026-0001",)

    def test_la_charge_ne_porte_ni_montant_ni_identite_du_client(self):
        """⚠️ Un événement qui transporte des données dont personne n'a besoin finit
        par en transporter qu'on ne voulait pas voir circuler : les journaux, les
        files et les sauvegardes le recopient tous."""
        boite = BoiteDEnvoiMemoire()
        _balayer([_proforma()], _plan(), _Suivis(), a_l_instant=T0 + timedelta(days=4), boite=boite)
        charge = boite.a_publier()[0].charge
        assert set(charge) == {
            "proforma", "dossier", "rang", "modele", "ton", "depuis_jours", "derniere"
        }


class TestUnPalierNePartQuUneFois:
    def test_deux_balayages_au_meme_instant_ne_deposent_qu_une_relance(self):
        """Le balayage est rejoué à chaque tour d'ordonnanceur. Sans cette garde,
        un client serait relancé toutes les heures."""
        boite = BoiteDEnvoiMemoire()
        suivis = _Suivis()
        instant = T0 + timedelta(days=4)
        _balayer([_proforma()], _plan(), suivis, a_l_instant=instant, boite=boite)
        rapport, _ = _balayer([_proforma()], _plan(), suivis, a_l_instant=instant, boite=boite)
        assert rapport.relances == ()
        assert len(boite.a_publier()) == 1

    def test_un_rappel_moins_urgent_ne_suit_jamais_un_rappel_plus_urgent(self):
        """⚠️ **Le défaut que l'épreuve de bout en bout a révélé.**

        ─────────────────────────────────────────────────────────────────────────
        Une proforma transmise depuis huit jours, sur un plan à 3, 7 et 14 jours :
        les rangs 1 et 2 sont dus. Le domaine n'en envoie qu'un, le plus avancé, et
        son en-tête affirmait depuis le pas 11 que **les précédents sont marqués
        employés avec lui**.

        Rien ne le faisait. Le balayage n'inscrivait que le rang envoyé, et le
        passage suivant trouvait le rang 1 encore libre et l'envoyait : le client
        recevait « dernier rappel » puis « petit rappel amical ».

        Aucune garde ne l'attrapait, parce que chaque envoi était individuellement
        correct.
        ─────────────────────────────────────────────────────────────────────────
        """
        boite = BoiteDEnvoiMemoire()
        suivis = _Suivis()
        instant = T0 + timedelta(days=8)

        premier, _ = _balayer([_proforma()], _plan(), suivis, a_l_instant=instant, boite=boite)
        second, _ = _balayer([_proforma()], _plan(), suivis, a_l_instant=instant, boite=boite)

        assert boite.a_publier()[0].charge["rang"] == 2
        assert second.relances == (), "un rang inférieur est reparti après un rang supérieur"
        assert suivis.tous()["PRO-2026-0001"].rangs_envoyes == (1, 2)

    def test_les_rangs_deja_inscrits_ne_sont_pas_effaces(self):
        """⚠️ **Le suivi existant doit être repris, pas reconstruit.**

        ─────────────────────────────────────────────────────────────────────────
        `rangs_couverts` ne contient que les paliers **dus**, c'est-à-dire ceux qui
        n'ont pas déjà été employés : le domaine les exclut lui-même. Repartir d'un
        suivi vide effacerait donc les rangs antérieurs sans qu'aucun compteur ne
        bouge.

        La conséquence se voit un passage plus tard : le rang 1, effacé, redevient
        libre et repart après le rang 2. C'est le même défaut que le cas précédent,
        par un autre chemin, et il a survécu à la première rédaction de ces cas.
        ─────────────────────────────────────────────────────────────────────────
        """
        suivis = _Suivis(SuiviDeRelance(proforma="PRO-2026-0001").avec(1, T0))
        instant = T0 + timedelta(days=8)

        _balayer([_proforma()], _plan(), suivis, a_l_instant=instant)
        assert suivis.tous()["PRO-2026-0001"].rangs_envoyes == (1, 2)

        # La contre-épreuve : sans le rang 1 conservé, il repartirait ici.
        second, _ = _balayer([_proforma()], _plan(), suivis, a_l_instant=instant)
        assert second.relances == ()

    def test_deux_paliers_successifs_sont_deux_evenements_distincts(self):
        """⚠️ **L'identifiant porte le rang, et il le doit.**

        L'identifiant est l'identité de l'événement dans la boîte. Deux relances de
        la même proforma à deux paliers sont deux faits différents ; leur donner le
        même identifiant ferait que **la seconde remplacerait la première**, en
        silence, et le client ne recevrait jamais le premier rappel.

        La clé, elle, reste le numéro seul : c'est ce qui les sérialise.
        """
        boite = BoiteDEnvoiMemoire()
        suivis = _Suivis()
        _balayer([_proforma()], _plan(), suivis, a_l_instant=T0 + timedelta(days=4), boite=boite)
        _balayer([_proforma()], _plan(), suivis, a_l_instant=T0 + timedelta(days=8), boite=boite)

        attente = boite.a_publier()
        assert [e.charge["rang"] for e in attente] == [1, 2]
        assert len({e.identifiant for e in attente}) == 2
        assert {e.cle for e in attente} == {"PRO-2026-0001"}


class TestLesAccepteesImpayees:
    def _acceptee(self):
        proforma = _proforma()
        return proforma.model_copy(update={"etat": type(proforma.etat).ACCEPTEE})

    def test_une_acceptee_impayee_ne_recoit_aucun_message(self):
        """⚠️ Le client s'est engagé et n'a pas réglé : ce n'est plus un problème de
        relance automatique, c'est un dossier qu'un humain reprend.

        Continuer à lui envoyer des modèles ne produit rien qu'une facture de
        messagerie.
        """
        boite = BoiteDEnvoiMemoire()
        rapport, _ = _balayer(
            [self._acceptee()], _plan(impaye=30), _Suivis(),
            a_l_instant=T0 + timedelta(days=40), boite=boite,
        )
        assert rapport.impayees == ("PRO-2026-0001",)
        assert rapport.relances == ()
        assert [e.nom for e in boite.a_publier()] == [NOM_IMPAYEE]

    def test_l_evenement_d_impayee_est_classe_sur_le_dossier(self):
        """C'est un humain qui reprend, et il reprend un **dossier**, pas un document.
        La clé porte donc la référence du dossier."""
        boite = BoiteDEnvoiMemoire()
        _balayer(
            [self._acceptee()], _plan(impaye=30), _Suivis(),
            a_l_instant=T0 + timedelta(days=40), boite=boite,
        )
        assert boite.a_publier()[0].cle == "dos-1"

    def test_une_acceptee_dans_les_delais_ne_remonte_pas(self):
        rapport, _ = _balayer(
            [self._acceptee()], _plan(impaye=30), _Suivis(),
            a_l_instant=T0 + timedelta(days=10),
        )
        assert rapport.impayees == ()
