"""La remise d'une relance : quel canal, choisi quand, et ce qu'il advient sinon.

⚠️ **La règle qui gouverne ce module est que le canal se choisit à la remise, jamais
au dépôt.** Entre les deux il peut s'écouler un tour, une reprise après panne, ou une
journée d'arriéré ; un client peut avoir révoqué son consentement, le centre peut
avoir désactivé un canal. Un canal figé au dépôt ferait partir un message sur un
consentement révoqué.

Et le corollaire du pas 5 bis : **aucune plateforme extérieure n'est bloquante**. La
messagerie n'est employée que si elle est prête, et à défaut le repli descend vers le
courriel puis vers l'appel, qui inscrit une tâche pour un humain.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.contextes.souscription.application.remise_des_relances import (
    CODE_COURRIEL_RELANCE,
    Remise,
    RemiseImpossible,
    remettre_une_relance,
)
from app.contextes.souscription.domaine.canaux import (
    Canal,
    EtatCanal,
    PlanDeContact,
)
from app.contextes.souscription.domaine.demande_de_contact import (
    Consentement,
    DemandeDeContact,
)
from app.contextes.souscription.domaine.dossier_commercial import ouvrir_un_dossier
from app.orchestration.boite_d_envoi import EvenementSortant

T0 = datetime(2026, 9, 10, 9, 0)


def _plan(*actifs: Canal) -> PlanDeContact:
    """Le plan de contact, avec les canaux nommés actifs et les autres coupés."""
    retenus = set(actifs) or {Canal.WHATSAPP, Canal.COURRIEL, Canal.APPEL}
    # L'ordre de repli du centre : messagerie, puis courriel, puis appel. C'est
    # celui du référentiel réel, et le rang est une donnée du plan, pas une
    # hiérarchie technique.
    return PlanDeContact(
        canaux=tuple(
            EtatCanal(
                canal=canal,
                rang=rang,
                actif=canal in retenus,
                motif="" if canal in retenus else "coupé pour l'essai",
            )
            for rang, canal in enumerate((Canal.WHATSAPP, Canal.COURRIEL, Canal.APPEL))
        )
    )


def _dossier(*, courriel: str | None, consent: bool, prefere: Canal = Canal.WHATSAPP):
    demande = DemandeDeContact(
        identifiant="dc-1",
        deposee_le=T0,
        nom="Station Bonabéri",
        telephone="+237699000001",
        courriel=courriel,
        service_souhaite="creation-sarl",
        message=None,
        canal_prefere=prefere,
        consentement=Consentement(accorde=consent, recueilli_le=T0, version_du_texte="v1"),
    )
    return ouvrir_un_dossier("dos-1", demande)


def _avec_consentement_revoque(dossier):
    """Le client s'est retiré après avoir consenti, et sa préférence n'a pas changé.

    Le consentement se révoque sur la demande, non sur le dossier : c'est la demande
    qui porte l'accord, et c'est elle que le repli interroge.
    """
    demande = dossier.derniere_demande
    retiree = demande.model_copy(
        update={
            "consentement": demande.consentement.revoquer(
                T0 + timedelta(days=1), "désabonnement reçu de la plateforme"
            )
        }
    )
    return dossier.model_copy(update={"rattachees": (retiree,)})


def _evenement(rang: int = 2) -> EvenementSortant:
    return EvenementSortant(
        identifiant=f"rel-PRO-2026-0001-{rang}",
        nom="RelanceDue",
        cle="PRO-2026-0001",
        charge={
            "proforma": "PRO-2026-0001",
            "dossier": "dos-1",
            "rang": rang,
            "modele": "cga_relance",
            "ton": "ferme",
            "depuis_jours": 8,
            "derniere": False,
        },
        cree_le=T0,
    )


class _Courriels:
    def __init__(self, accepte: bool = True) -> None:
        self.accepte = accepte
        self.envois: list[tuple[str, str, dict]] = []

    def envoyer(self, code, *, destinataire, contexte):
        self.envois.append((code, destinataire, contexte))
        return self.accepte


class _Taches:
    def __init__(self) -> None:
        self.inscrites: list[tuple[str, str]] = []

    def inscrire(self, dossier, motif, *, a_l_instant):
        self.inscrites.append((dossier, motif))


def _remise(plan, *, courriels=None, taches=None, messagerie_prete=False):
    return Remise(
        plan=plan,
        courriels=courriels or _Courriels(),
        taches=taches or _Taches(),
        horloge=lambda: T0,
        messagerie_prete=messagerie_prete,
    )


class TestLeCanalSeChoisitALaRemise:
    def test_un_consentement_revoque_ecarte_la_messagerie(self):
        """⚠️ Le cas qui justifie tout ce module, et il a d'abord été mal écrit.

        ─────────────────────────────────────────────────────────────────────────
        Sa première version faisait préférer le courriel au client. Le courriel
        l'emportait donc immédiatement, et le consentement ne décidait de rien :
        remplacer `consentement_vaut` par `True` ne changeait rien, et la mutation a
        survécu.

        Le vrai scénario est une **révocation après le dépôt de la demande**. Le
        domaine refuse qu'une demande naisse avec la messagerie préférée sans
        consentement — elle se replie sur l'appel. Mais rien n'empêche un client
        d'avoir consenti, puis de se retirer, pendant qu'une relance attend d'être
        remise.

        C'est exactement le trou qu'un canal figé au dépôt laisserait ouvert : le
        message partirait sur un consentement révoqué.
        ─────────────────────────────────────────────────────────────────────────
        """
        dossier = _dossier(courriel="s@exemple.cm", consent=True, prefere=Canal.WHATSAPP)
        retire = _avec_consentement_revoque(dossier)

        courriels = _Courriels()
        issue = remettre_une_relance(
            _evenement(),
            retire,
            # ⚠️ Messagerie **prête** : seul le consentement peut l'écarter ici.
            _remise(_plan(), courriels=courriels, messagerie_prete=True),
        )
        assert issue.canal is Canal.COURRIEL
        assert issue.replie is True
        assert len(courriels.envois) == 1

    def test_sans_revocation_la_messagerie_preferee_l_emporte(self):
        """La contre-épreuve. Sans elle, le cas précédent passerait sur un système
        qui n'emploierait jamais la messagerie."""
        with pytest.raises(RemiseImpossible, match="aucune passerelle"):
            remettre_une_relance(
                _evenement(),
                _dossier(courriel="s@exemple.cm", consent=True, prefere=Canal.WHATSAPP),
                _remise(_plan(), messagerie_prete=True),
            )

    def test_un_canal_coupe_au_referentiel_est_ecarte(self):
        """Le centre peut couper un canal pendant qu'un arriéré attend d'être remis."""
        taches = _Taches()
        issue = remettre_une_relance(
            _evenement(),
            _dossier(courriel="s@exemple.cm", consent=True),
            _remise(_plan(Canal.APPEL), taches=taches),
        )
        assert issue.canal is Canal.APPEL
        assert issue.replie is True


class TestAucunePlateformeExterieureNEstBloquante:
    def test_sans_messagerie_prete_le_repli_descend_au_courriel(self):
        """⚠️ La règle du pas 5 bis, éprouvée pour de bon.

        Le client a consenti et préfère la messagerie. Le compte n'est pas ouvert :
        le parcours doit fonctionner quand même.
        """
        courriels = _Courriels()
        issue = remettre_une_relance(
            _evenement(),
            _dossier(courriel="s@exemple.cm", consent=True),
            _remise(_plan(), courriels=courriels, messagerie_prete=False),
        )
        assert issue.canal is Canal.COURRIEL
        assert issue.replie is True
        assert issue.ecartes, "le motif de l'écart doit être conservé"

    def test_sans_messagerie_ni_courriel_le_repli_descend_a_l_appel(self):
        """Le plancher. Un prospect sans adresse et sans messagerie reste joignable."""
        taches = _Taches()
        issue = remettre_une_relance(
            _evenement(),
            _dossier(courriel=None, consent=True),
            _remise(_plan(), taches=taches, messagerie_prete=False),
        )
        assert issue.canal is Canal.APPEL
        assert issue.confiee_a_un_humain is True

    def test_l_appel_inscrit_une_tache_et_n_envoie_rien(self):
        """⚠️ C'est ce qui rend le plancher réel.

        Un système dont le dernier recours serait encore un envoi automatique
        n'aurait aucun plancher : il dépendrait toujours d'une passerelle, et une
        panne de celle-ci arrêterait toute relance.
        """
        courriels, taches = _Courriels(), _Taches()
        remettre_une_relance(
            _evenement(rang=3),
            _dossier(courriel=None, consent=True),
            _remise(_plan(), courriels=courriels, taches=taches),
        )
        assert courriels.envois == []
        assert len(taches.inscrites) == 1
        dossier, motif = taches.inscrites[0]
        assert dossier == "dos-1"
        assert "PRO-2026-0001" in motif and "rang 3" in motif

    def test_la_messagerie_retenue_sans_passerelle_leve(self):
        """⚠️ Une incohérence de câblage, pas un état normal.

        `canal_a_employer` ne rend ce canal que si la messagerie est déclarée prête.
        Arriver là sans passerelle branchée doit lever plutôt que de faire
        silencieusement rien, ce qui laisserait croire que le client a été relancé.
        """
        with pytest.raises(RemiseImpossible, match="aucune passerelle"):
            remettre_une_relance(
                _evenement(),
                _dossier(courriel=None, consent=True),
                _remise(_plan(), messagerie_prete=True),
            )


class TestLeCourriel:
    def test_le_code_du_gabarit_est_celui_du_service_de_notification(self):
        """Le domaine ne connaît ni SMTP, ni HTML, ni gabarit : il demande qu'un
        message identifié par un code parte vers une adresse."""
        courriels = _Courriels()
        remettre_une_relance(
            _evenement(),
            _dossier(courriel="s@exemple.cm", consent=True),
            _remise(_plan(), courriels=courriels),
        )
        code, destinataire, _ = courriels.envois[0]
        assert code == CODE_COURRIEL_RELANCE
        assert destinataire == "s@exemple.cm"

    def test_le_contexte_ne_porte_ni_montant_ni_identite(self):
        """⚠️ Le gabarit les lira où elles vivent. Un contexte qui transporte des
        données dont personne n'a besoin finit par en transporter qu'on ne voulait
        pas voir circuler."""
        courriels = _Courriels()
        remettre_une_relance(
            _evenement(),
            _dossier(courriel="s@exemple.cm", consent=True),
            _remise(_plan(), courriels=courriels),
        )
        _, _, contexte = courriels.envois[0]
        assert set(contexte) == {"proforma", "rang", "ton", "depuis_jours", "derniere"}

    def test_un_refus_d_envoi_leve(self):
        """⚠️ Le relais retentera, et retiendra les paliers suivants de la même
        proforma en attendant.

        Un rang 2 posté alors que le rang 1 n'est jamais parti ferait recevoir au
        client une relance de deuxième niveau pour un message qu'il n'a jamais eu.
        """
        with pytest.raises(RemiseImpossible, match="refusé l'envoi"):
            remettre_une_relance(
                _evenement(),
                _dossier(courriel="s@exemple.cm", consent=True),
                _remise(_plan(), courriels=_Courriels(accepte=False)),
            )
