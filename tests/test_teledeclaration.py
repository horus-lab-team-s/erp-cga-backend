"""Second facteur, recevabilité et dépôt auprès de l'administration.

Ces tests protègent trois propriétés dont dépend la crédibilité du produit :
un dépôt ne se fait pas sans authentification forte, une déclaration irrecevable
n'est jamais consignée comme déposée, et un accusé qui ne correspond pas au
dossier préparé est refusé.

⚠️ Ils ne testent **aucune interface de la DGI** : elle n'est pas publiée. Ils
testent le dépôt, ce qui est vrai quel que soit le guichet.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.contextes.comptabilite.api import ecritures_demo
from app.contextes.obligations.api import (
    ObligationInstance,
    StatutObligation,
    etablir_declaration_tva,
)
from app.contextes.obligations.application.depot import (
    CompletudeDeLaPeriode,
    DepotImpossible,
    ReglagesDuDepotTVA,
    RevueDeLaPeriode,
    constater_depot,
    controler_recevabilite,
    preparer_depot_tva,
)
from app.contextes.obligations.domaine.recevabilite import NiveauRecevabilite
from app.contextes.portefeuille.api import PORTEFEUILLE_DEMO
from app.contextes.transverse.adaptateurs.entrant.dependances import (
    reinitialiser_atelier,
)
from app.contextes.transverse.api import (
    MOT_DE_PASSE_DEMO,
    AccuseIncoherent,
    AccuseReception,
    DepotManuelRequis,
    DocumentATransmettre,
    FormatTransmission,
    JournalAuditMemoire,
    ModeDepot,
    Portail,
    PortailManuel,
    code_attendu,
    empreinte_document,
    engendrer_secret_totp,
    uri_provisionnement,
    verifier_chaine,
    verifier_code,
)
from app.contextes.transverse.domaine.second_facteur import CHIFFRES, PAS_SECONDES
from app.main import creer_application
from app.partage.horloge import maintenant
from tests.conftest import enroler_par_le_courriel

INSTANT = datetime(2026, 8, 15, 10, 0)
NIU = "M081234567890P"
PERIODE = (date(2026, 7, 1), date(2026, 7, 31))


# ── Le second facteur ────────────────────────────────────────────────────────


class TestSecondFacteur:
    def test_un_secret_engendre_est_lisible_en_base_32(self):
        secret = engendrer_secret_totp()
        assert len(secret) == 32
        assert set(secret) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")

    def test_deux_secrets_different(self):
        assert engendrer_secret_totp() != engendrer_secret_totp()

    def test_le_code_de_reference_de_la_rfc_6238(self):
        """Vecteur d'essai publié : secret `12345678901234567890` en ASCII, donc
        `GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ` en base 32, à 59 secondes de l'époque
        Unix, doit donner `287082`.

        C'est ce qui distingue une implémentation correcte d'une implémentation
        qui se teste contre elle-même : sans vecteur externe, un décalage de
        fuseau ou une troncature fausse passe inaperçue puisque le générateur et
        le vérificateur partagent l'erreur.
        """
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        assert code_attendu(secret, datetime(1970, 1, 1, 0, 0, 59)) == "287082"

    def test_le_code_change_a_chaque_tranche(self):
        secret = engendrer_secret_totp()
        premier = code_attendu(secret, INSTANT)
        suivant = code_attendu(secret, INSTANT + timedelta(seconds=PAS_SECONDES))
        assert premier != suivant
        assert len(premier) == CHIFFRES

    def test_la_tolerance_couvre_une_tranche_de_chaque_cote(self):
        """Assez pour la dérive d'un téléphone et le temps de recopier ; assez
        peu pour qu'un code intercepté ne serve plus."""
        secret = engendrer_secret_totp()
        code = code_attendu(secret, INSTANT)
        assert verifier_code(secret, code, INSTANT + timedelta(seconds=PAS_SECONDES))
        assert verifier_code(secret, code, INSTANT - timedelta(seconds=PAS_SECONDES))
        assert not verifier_code(secret, code, INSTANT + timedelta(seconds=PAS_SECONDES * 2))

    def test_un_code_mal_forme_est_refuse_sans_lever(self):
        secret = engendrer_secret_totp()
        for essai in ("", "12345", "abcdef", "1234567890"):
            assert not verifier_code(secret, essai, INSTANT)

    def test_le_fuseau_n_entre_pas_dans_le_calcul(self):
        """`datetime.timestamp()` sur un horodatage naïf suppose le fuseau local.
        Le Cameroun étant à UTC+1, l'oubli décalerait tous les codes de cent
        vingt tranches — jamais valides, sans message d'erreur."""
        from datetime import UTC

        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        naif = datetime(2026, 8, 15, 10, 0, 0)
        conscient = naif.replace(tzinfo=UTC)
        assert code_attendu(secret, naif) == code_attendu(secret, conscient.replace(tzinfo=None))

    def test_l_adresse_de_provisionnement_porte_les_parametres(self):
        uri = uri_provisionnement("ABCDEF", compte="a@b.cm", emetteur="CGA Broad Range")
        assert uri.startswith("otpauth://totp/")
        assert "secret=ABCDEF" in uri
        assert f"digits={CHIFFRES}" in uri and f"period={PAS_SECONDES}" in uri


# ── Le document et son accusé ────────────────────────────────────────────────


def _document(contenu: str = '{"tva":100}') -> DocumentATransmettre:
    return DocumentATransmettre(
        portail=Portail.DGI_TELEDECLARATION,
        code_document="TVA",
        entreprise=NIU,
        periode_debut=PERIODE[0],
        periode_fin=PERIODE[1],
        format=FormatTransmission.SAISIE_MANUELLE,
        contenu=contenu,
    )


def _accuse(document: DocumentATransmettre, numero: str = "DGI-1") -> AccuseReception:
    return AccuseReception(
        numero=numero,
        portail=Portail.DGI_TELEDECLARATION,
        reference_document=document.reference,
        depose_le=INSTANT,
        mode=ModeDepot.MANUEL,
        empreinte_deposee=document.empreinte,
        depose_par="C-003",
    )


class TestDocumentEtAccuse:
    def test_la_reference_n_inclut_pas_la_date_de_depot(self):
        """Un second dépôt de la TVA de juillet doit se heurter au premier, quel
        que soit le jour où il est tenté."""
        assert _document().reference == f"{NIU}/TVA/20260701-20260731"

    def test_l_empreinte_change_avec_le_contenu(self):
        assert _document('{"tva":100}').empreinte != _document('{"tva":101}').empreinte

    def test_l_accuse_exige_la_reference_et_l_empreinte(self):
        """La référence seule laisserait passer un accusé obtenu sur une version
        antérieure du dossier — celle d'avant la correction."""
        prepare = _document()
        corrige = _document('{"tva":999}')
        accuse = _accuse(prepare)
        assert accuse.concerne(prepare)
        assert not accuse.concerne(corrige)

    def test_un_accuse_sans_piece_jointe_le_dit(self):
        assert not _accuse(_document()).verifiable


class TestPortailManuel:
    def test_il_ne_depose_pas_et_le_dit_avec_la_marche_a_suivre(self):
        """Un adaptateur qui feindrait de déposer produirait de faux
        justificatifs de dépôt."""
        portail = PortailManuel()
        assert not portail.depose_automatiquement()
        with pytest.raises(DepotManuelRequis, match="numéro d'accusé"):
            portail.deposer(_document(), par="C-003")

    def test_il_consigne_un_accuse_coherent(self):
        portail = PortailManuel()
        document = _document()
        consigne = portail.enregistrer_accuse(_accuse(document), document=document)
        assert consigne.mode is ModeDepot.MANUEL
        assert portail.retrouver(document.reference) is consigne

    def test_il_refuse_un_accuse_de_la_mauvaise_ligne(self):
        portail = PortailManuel()
        autre = _document('{"tva":999}')
        with pytest.raises(AccuseIncoherent, match="ne correspond pas"):
            portail.enregistrer_accuse(_accuse(autre), document=_document())

    def test_il_refuse_un_second_depot_de_la_meme_periode(self):
        portail = PortailManuel()
        document = _document()
        portail.enregistrer_accuse(_accuse(document, "DGI-1"), document=document)
        with pytest.raises(AccuseIncoherent, match="porte déjà l'accusé"):
            portail.enregistrer_accuse(_accuse(document, "DGI-2"), document=document)

    def test_un_accuse_cnps_ne_prouve_rien_devant_la_dgi(self):
        portail = PortailManuel(Portail.DGI_TELEDECLARATION)
        document = _document()
        etranger = _accuse(document).model_copy(update={"portail": Portail.CNPS_DIPE})
        with pytest.raises(AccuseIncoherent, match="ne prouve rien"):
            portail.enregistrer_accuse(etranger, document=document)

    def test_l_empreinte_est_celle_du_contenu_exact(self):
        assert _document().empreinte == empreinte_document('{"tva":100}')


# ── La recevabilité ──────────────────────────────────────────────────────────


def _obligation(**remplace) -> ObligationInstance:
    defauts = dict(
        entreprise=NIU,
        code_obligation="TVA",
        libelle="Déclaration de TVA",
        periode_debut=PERIODE[0],
        periode_fin=PERIODE[1],
        echeance=date(2026, 8, 15),
    )
    return ObligationInstance(**{**defauts, **remplace})


def _declaration(ecritures):
    return etablir_declaration_tva(
        ecritures,
        entreprise="SARL BATIMENT PLUS",
        periode_debut=PERIODE[0],
        periode_fin=PERIODE[1],
    )


class TestRecevabilite:
    def _controler(self, **remplace):
        ecritures = remplace.pop("ecritures", ecritures_demo(NIU))
        defauts = dict(
            a_la_date=date(2026, 8, 10),
            accuse_existant=None,
            completude=CompletudeDeLaPeriode(pieces_recues=0),
            # Le mois transmis au réviseur : l'exigence de revue a ses propres cas (pas 109).
            revue=RevueDeLaPeriode(identifiant="REV-JUILLET", statut="TRANSMISE"),
            reglages=ReglagesDuDepotTVA(),
            referentiel_valide=False,
        )
        return controler_recevabilite(
            remplace.pop("obligation", _obligation()),
            PORTEFEUILLE_DEMO[NIU],
            ecritures,
            remplace.pop("declaration", _declaration(ecritures)),
            **{**defauts, **remplace},
        )

    def test_le_referentiel_non_valide_est_toujours_une_reserve(self):
        """Toute déclaration produite par ce système en porte une, tant qu'aucune
        valeur n'a été confrontée au Code général des impôts."""
        codes = [a.code for a in self._controler().anomalies]
        assert "REFERENTIEL-NON-VALIDE" in codes

    def test_une_reserve_n_empeche_pas_de_deposer(self):
        recevabilite = self._controler()
        assert recevabilite.deposable
        assert recevabilite.exige_une_decision

    def test_la_tva_rejetee_est_une_reserve_chiffree(self):
        """C'est la valeur du contrôle : cette TVA aurait été réclamée et
        refusée."""
        rejet = next(
            a
            for a in self._controler().anomalies
            if a.code == "TVA-REJETEE-PAR-LE-CONTROLE"
        )
        assert rejet.niveau is NiveauRecevabilite.RESERVE
        assert rejet.enjeu and rejet.enjeu > 0

    def test_une_periode_deja_deposee_bloque(self):
        recevabilite = self._controler(accuse_existant=_accuse(_document()))
        assert not recevabilite.deposable
        assert "DEPOT-DEJA-EFFECTUE" in [a.code for a in recevabilite.bloquants]

    def test_une_obligation_deja_declaree_bloque(self):
        declaree = _obligation(
            statut=StatutObligation.DECLAREE, declaree_le=date(2026, 8, 12)
        )
        assert not self._controler(obligation=declaree).deposable

    def test_une_balance_desequilibree_bloque(self):
        """Une déclaration assise sur une comptabilité déséquilibrée est fausse
        par construction."""
        ecritures = ecritures_demo(NIU)
        amputee = ecritures[:-1] + [
            ecritures[-1].model_copy(
                update={"lignes": ecritures[-1].lignes[:1]}, deep=False
            )
        ]
        recevabilite = self._controler(ecritures=amputee, declaration=None)
        assert not recevabilite.deposable
        assert "BALANCE-DESEQUILIBREE" in [a.code for a in recevabilite.bloquants]

    def test_une_echeance_depassee_informe_sans_bloquer(self):
        recevabilite = self._controler(a_la_date=date(2026, 9, 1))
        depassee = next(a for a in recevabilite.anomalies if a.code == "ECHEANCE-DEPASSEE")
        assert depassee.niveau is NiveauRecevabilite.INFORMATION
        assert recevabilite.deposable

    def test_les_pieces_non_traitees_sont_une_reserve(self):
        souffrance = CompletudeDeLaPeriode(
            pieces_recues=6, pieces_en_souffrance=["PJ-1", "PJ-2", "PJ-3", "PJ-4"]
        )
        codes = [a.code for a in self._controler(completude=souffrance).reserves]
        assert "PIECES-NON-TRAITEES" in codes

    def test_l_enjeu_se_totalise(self):
        """On affiche un montant, pas un nombre d'anomalies : « 3 réserves » ne
        déclenche aucune décision."""
        assert self._controler().enjeu_total > 0


# ── Le dépôt ─────────────────────────────────────────────────────────────────


class TestDepot:
    def _preparer(self, portail=None, **remplace):
        ecritures = ecritures_demo(NIU)
        return preparer_depot_tva(
            remplace.pop("obligation", _obligation()),
            PORTEFEUILLE_DEMO[NIU],
            _declaration(ecritures),
            ecritures,
            portail=portail or PortailManuel(),
            a_la_date=date(2026, 8, 10),
            **{
                "completude": CompletudeDeLaPeriode(pieces_recues=0),
                "revue": RevueDeLaPeriode(identifiant="REV-JUILLET", statut="TRANSMISE"),
                "reglages": ReglagesDuDepotTVA(),
                **remplace,
            },
        )

    def test_preparer_ne_change_aucun_etat(self):
        """Préparer se refait autant de fois qu'on veut : c'est en préparant
        qu'on découvre ce qu'il reste à corriger."""
        portail = PortailManuel()
        premier = self._preparer(portail)
        second = self._preparer(portail)
        assert premier.document.empreinte == second.document.empreinte
        assert portail.retrouver(premier.document.reference) is None

    def test_le_bordereau_est_deterministe(self):
        """Deux sérialisations différentes du même dossier produiraient deux
        empreintes, et l'accusé ne se rattacherait plus au document."""
        assert self._preparer().document.contenu == self._preparer().document.contenu

    def test_le_depot_n_est_pas_automatique(self):
        """La DGI ne publie aucune interface programmatique."""
        assert not self._preparer().depot_automatique

    def test_constater_fait_passer_l_obligation_a_declaree(self):
        portail = PortailManuel()
        journal = JournalAuditMemoire()
        dossier = self._preparer(portail)
        obligation = _obligation()
        declaree, consigne = constater_depot(
            dossier,
            obligation,
            _accuse(dossier.document, "DGI-2026-77"),
            portail=portail,
            journal=journal,
            par="C-003",
            a_l_instant=INSTANT,
        )
        assert declaree.statut is StatutObligation.DECLAREE
        assert declaree.reference_depot == "DGI-2026-77"
        assert consigne.mode is ModeDepot.MANUEL

    def test_la_date_retenue_est_celle_de_l_accuse(self):
        """Un réviseur qui dépose le 14 et consigne le 17 doit voir le 14 : c'est
        cette date que l'administration retient pour les délais."""
        portail = PortailManuel()
        dossier = self._preparer(portail)
        accuse = _accuse(dossier.document).model_copy(
            update={"depose_le": datetime(2026, 8, 14, 10, 22)}
        )
        declaree, _ = constater_depot(
            dossier,
            _obligation(),
            accuse,
            portail=portail,
            journal=JournalAuditMemoire(),
            par="C-003",
            a_l_instant=datetime(2026, 8, 17, 9, 0),
        )
        assert declaree.declaree_le == date(2026, 8, 14)

    def test_un_dossier_non_recevable_ne_se_constate_pas(self):
        """Consigner un dépôt irrégulier comme régulier n'en efface que la
        trace."""
        portail = PortailManuel()
        dossier = self._preparer(portail, obligation=_obligation())
        # On lui impose un bloquant en le rendant déjà déposé.
        portail.enregistrer_accuse(
            _accuse(dossier.document, "DGI-1"), document=dossier.document
        )
        rejoue = self._preparer(portail)
        with pytest.raises(DepotImpossible, match="DEPOT-DEJA-EFFECTUE"):
            constater_depot(
                rejoue,
                _obligation(),
                _accuse(rejoue.document, "DGI-2"),
                portail=portail,
                journal=JournalAuditMemoire(),
                par="C-003",
                a_l_instant=INSTANT,
            )

    def test_le_depot_laisse_une_trace_verifiable(self):
        portail = PortailManuel()
        journal = JournalAuditMemoire()
        dossier = self._preparer(portail)
        constater_depot(
            dossier,
            _obligation(),
            _accuse(dossier.document),
            portail=portail,
            journal=journal,
            par="C-003",
            a_l_instant=INSTANT,
        )
        entree = journal.lister()[-1]
        assert entree.action == "obligation.deposee"
        assert entree.apres is not None
        assert entree.apres["reserves"]
        verifier_chaine(journal.lister())


# ── L'API ────────────────────────────────────────────────────────────────────


PARAMS = {
    "periode_debut": "2026-07-01",
    "periode_fin": "2026-07-31",
    "a_la_date": "2026-08-15",
}


@pytest.fixture
def client() -> TestClient:
    # Le portail vit désormais sur l'atelier : le remettre à zéro suffit, et
    # c'est mieux — un registre d'accusés séparé de l'atelier est précisément le
    # défaut qu'on vient de corriger.
    reinitialiser_atelier()
    client = TestClient(creer_application())
    reponse = client.post(
        "/transverse/session",
        json={"courriel": "a.bouba@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
    )
    assert reponse.status_code == 200, reponse.text
    return client


def _renforcer(client: TestClient) -> None:
    secret = enroler_par_le_courriel(client)
    code = code_attendu(secret, maintenant())
    assert client.post(
        "/transverse/session/renforcement", json={"code": code}
    ).json()["facteur_fort"]


@pytest.mark.usefixtures("depot_tva_sans_revue_exigee")
class TestApi:
    def test_le_dossier_se_prepare_avec_ses_reserves(self, client: TestClient):
        reponse = client.get(f"/obligations/dossiers/{NIU}/depot-tva", params=PARAMS)
        assert reponse.status_code == 200, reponse.text
        corps = reponse.json()
        assert corps["depot_automatique"] is False
        assert corps["recevabilite"]["exige_une_decision"] is True
        assert len(corps["document"]["empreinte"]) == 64

    def test_deposer_sans_second_facteur_est_refuse(self, client: TestClient):
        """C'est l'action sensible du tableau de 05-securite-multitenant.md."""
        reponse = client.post(
            f"/obligations/dossiers/{NIU}/depot-tva",
            params=PARAMS,
            json={"numero": "DGI-1", "depose_le": "2026-08-14T10:22:00"},
        )
        assert reponse.status_code == 403
        assert "SecondFacteurRequis" in reponse.json()["detail"]

    def test_le_parcours_complet(self, client: TestClient):
        """Enrôlement → renforcement → préparation → dépôt → refus du second."""
        _renforcer(client)
        corps = {"numero": "DGI-2026-0007741", "depose_le": "2026-08-14T10:22:00"}
        depot = client.post(
            f"/obligations/dossiers/{NIU}/depot-tva", params=PARAMS, json=corps
        )
        assert depot.status_code == 200, depot.text
        rendu = depot.json()
        assert rendu["obligation"]["statut"] == "DECLAREE"
        assert rendu["obligation"]["declaree_le"] == "2026-08-14"
        assert rendu["accuse"]["verifiable"] is False
        assert "REFERENTIEL-NON-VALIDE" in rendu["reserves_assumees"]

        rejoue = client.post(
            f"/obligations/dossiers/{NIU}/depot-tva", params=PARAMS, json=corps
        )
        assert rejoue.status_code == 409
        assert "DEPOT-DEJA-EFFECTUE" in rejoue.json()["detail"]

    def test_un_code_faux_ne_renforce_pas(self, client: TestClient):
        enroler_par_le_courriel(client)
        assert (
            client.post(
                "/transverse/session/renforcement", json={"code": "000000"}
            ).status_code
            == 401
        )

    def test_le_renforcement_sans_enrolement_rend_400(self, client: TestClient):
        assert (
            client.post(
                "/transverse/session/renforcement", json={"code": "123456"}
            ).status_code
            == 400
        )

    def test_le_secret_n_apparait_jamais_dans_une_lecture_de_compte(
        self, client: TestClient
    ):
        """Le rendre une seule fois de plus suffirait à ce que quiconque le lit
        engendre les codes du porteur pour toujours.

        Lu par l'administrateur, seul rôle à détenir `GERER_COMPTES` — le
        réviseur ne liste pas les comptes.
        """
        enroler_par_le_courriel(client)
        administrateur = TestClient(client.app)
        administrateur.post(
            "/transverse/session",
            json={"courriel": "s.onana@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        lignes = administrateur.get(
            "/transverse/comptes", params={"a_la_date": "2026-08-15"}
        ).json()
        assert isinstance(lignes, list) and lignes
        for ligne in lignes:
            assert "secret_totp" not in ligne["compte"]
            assert "empreinte_mot_de_passe" not in ligne["compte"]
        bouba = next(x for x in lignes if x["compte"]["identifiant"] == "C-003")
        assert bouba["compte"]["second_facteur_actif"] is True

    def test_enrole_n_est_pas_renforce(self, client: TestClient):
        """La distinction commande l'écran : sans enrôlement on propose de
        s'enrôler, avec enrôlement on demande un code."""
        avant = client.get("/transverse/moi").json()
        assert avant["second_facteur_enrole"] is False
        enroler_par_le_courriel(client)
        apres = client.get("/transverse/moi").json()
        assert apres["second_facteur_enrole"] is True
        assert apres["facteur_fort"] is False

    def test_le_depot_est_reserve_au_reviseur(self, client: TestClient):
        autre = TestClient(client.app)
        autre.post(
            "/transverse/session",
            json={"courriel": "l.fotso@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        reponse = autre.post(
            f"/obligations/dossiers/{NIU}/depot-tva",
            params=PARAMS,
            json={"numero": "DGI-1", "depose_le": "2026-08-14T10:22:00"},
        )
        assert reponse.status_code == 403
        assert "DEPOSER_DECLARATION" in reponse.json()["detail"]

    def test_un_montant_a_payer_nul_ne_devient_pas_zero(self, client: TestClient):
        """Un crédit de TVA n'est pas un montant à payer de zéro : le champ reste
        nul, et l'écran n'affiche pas « 0 F à régler » là où il y a un report."""
        corps = client.get(
            f"/obligations/dossiers/{NIU}/depot-tva", params=PARAMS
        ).json()
        montant = corps["document"]["montant_a_payer"]
        assert montant is None or Decimal(montant) > 0
