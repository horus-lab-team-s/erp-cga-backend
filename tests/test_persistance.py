"""La persistance PostgreSQL du socle.

─────────────────────────────────────────────────────────────────────────────────
CES TESTS EXIGENT UNE VRAIE BASE, ET C'EST TOUT LEUR INTÉRÊT

Trois des propriétés vérifiées ici **n'existent pas** sur une base en mémoire ni
sur SQLite : le verrou de ligne qui sérialise les ajouts au journal d'audit,
l'unicité du rang sous concurrence, et l'unicité du dépôt d'une déclaration. Les
tester ailleurs reviendrait à tester autre chose.

Si la base n'est pas joignable, ils sont **ignorés avec un message qui dit quoi
faire** — jamais silencieusement passés. Un test vert parce qu'il ne s'est pas
exécuté est pire qu'un test rouge.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.contextes.transverse.adaptateurs.sortant.depots_memoire import (
    CompteIntrouvable,
    CourrielEnDouble,
)
from app.contextes.transverse.adaptateurs.sortant.depots_sql import (
    DepotComptesSql,
    DepotHabilitationsSql,
    DepotJetonsSql,
    DepotSessionsSql,
    JournalAuditSql,
    PortailSql,
)
from app.contextes.transverse.adaptateurs.sortant.portail_manuel import AccuseIncoherent
from app.contextes.transverse.adaptateurs.sortant.tables import TableEntreeAudit
from app.contextes.transverse.api import (
    LOCATAIRE_PAR_DEFAUT,
    AccuseReception,
    Compte,
    DocumentATransmettre,
    EtatCompte,
    FormatTransmission,
    Habilitation,
    Jeton,
    ModeDepot,
    MotifHabilitation,
    MotifRevocation,
    Portail,
    Role,
    Session,
    TypeJeton,
    dossiers_accessibles,
    empreinte_de,
    roles_au,
    verifier_chaine,
)
from tests.conftest import URL_BASE_TEST, enroler_par_le_courriel, exige_postgresql

# ⚠️ Le sondage de disponibilité et les fixtures de session vivent dans
# `conftest.py`, et une seule fois. Deux copies qui divergent feraient sauter
# ces tests en silence — voir l'en-tête du conftest.
INSTANT = datetime(2026, 8, 15, 10, 0)
CABINET = "CGA-BRCG"
AUTRE_CABINET = "CGA-CONCURRENT"
NIU = "M081234567890P"


pytestmark = exige_postgresql


@pytest.fixture
def session(session_sql):
    """Alias local de la fixture partagée — voir `conftest.py`."""
    return session_sql


def _compte(identifiant="C-1", courriel="a@cga-brcg.cm", locataire=CABINET) -> Compte:
    return Compte(
        identifiant=identifiant,
        courriel=courriel,
        nom="BOUBA",
        prenom="Aïcha",
        locataire=locataire,
        empreinte_mot_de_passe="$argon2id$factice",
        etat=EtatCompte.ACTIF,
        cree_le=INSTANT,
    )


# ── Aller-retour des entités ─────────────────────────────────────────────────


class TestComptes:
    def test_un_compte_survit_a_l_aller_retour(self, session):
        depot = DepotComptesSql(session, CABINET)
        depot.enregistrer(_compte())
        session.commit()
        relu = depot.lire("C-1")
        assert relu == _compte()

    def test_l_empreinte_et_le_secret_sont_bien_persistes(self, session):
        """Exclus de la **sérialisation**, jamais du stockage : sans eux, plus
        personne ne se connecte après un redémarrage."""
        depot = DepotComptesSql(session, CABINET)
        depot.enregistrer(_compte().avec_second_facteur("JBSWY3DPEHPK3PXP"))
        session.commit()
        relu = depot.lire("C-1")
        assert relu.empreinte_mot_de_passe == "$argon2id$factice"
        assert relu.secret_totp == "JBSWY3DPEHPK3PXP"
        assert "secret_totp" not in relu.model_dump()

    def test_deux_comptes_ne_partagent_pas_une_adresse(self, session):
        depot = DepotComptesSql(session, CABINET)
        depot.enregistrer(_compte("C-1", "meme@cga-brcg.cm"))
        session.commit()
        with pytest.raises(CourrielEnDouble):
            depot.enregistrer(_compte("C-2", "meme@cga-brcg.cm"))

    def test_deux_cabinets_peuvent_employer_la_meme_personne(self, session):
        """L'unicité de l'adresse est **par locataire** : un comptable peut
        travailler pour deux centres."""
        DepotComptesSql(session, CABINET).enregistrer(
            _compte("C-1", "jean@exemple.cm", CABINET)
        )
        DepotComptesSql(session, AUTRE_CABINET).enregistrer(
            _compte("C-2", "jean@exemple.cm", AUTRE_CABINET)
        )
        session.commit()

    def test_un_compte_d_un_autre_locataire_est_introuvable(self, session):
        DepotComptesSql(session, AUTRE_CABINET).enregistrer(
            _compte("C-9", "x@ailleurs.cm", AUTRE_CABINET)
        )
        session.commit()
        with pytest.raises(CompteIntrouvable):
            DepotComptesSql(session, CABINET).lire("C-9")

    def test_une_ecriture_croisee_est_refusee(self, session):
        """Le filtre de session protège les lectures ; les écritures se
        contrôlent à l'entrée."""
        with pytest.raises(ValueError, match="cloisonnement|locataire"):
            DepotComptesSql(session, CABINET).enregistrer(
                _compte(locataire=AUTRE_CABINET)
            )


class TestHabilitations:
    def _poser(self, session, **remplace) -> Habilitation:
        DepotComptesSql(session, CABINET).enregistrer(_compte())
        defauts = dict(
            identifiant="H-1",
            compte="C-1",
            role=Role.COMPTABLE,
            portee=frozenset({NIU}),
            debut=date(2021, 9, 1),
            motif=MotifHabilitation.RECRUTEMENT,
            accordee_par="C-0",
        )
        habilitation = Habilitation(**{**defauts, **remplace})
        DepotHabilitationsSql(session, CABINET).enregistrer(habilitation)
        session.commit()
        return habilitation

    def test_la_portee_survit_a_l_aller_retour(self, session):
        pose = self._poser(session)
        assert DepotHabilitationsSql(session, CABINET).lire("H-1") == pose

    def test_une_portee_nulle_ne_devient_pas_une_liste_vide(self, session):
        """`null` vaut « tout le portefeuille », `[]` vaut « aucun dossier ».
        Les confondre ferait voir tout le cabinet à quelqu'un qui ne devrait rien
        voir."""
        self._poser(session, role=Role.REVISEUR, portee=None)
        relue = DepotHabilitationsSql(session, CABINET).lire("H-1")
        assert relue.portee is None
        assert relue.transverse
        assert dossiers_accessibles([relue], date(2026, 8, 15)) is None

    def test_une_habilitation_fermee_demeure(self, session):
        """C'est elle qui répond à « qui était habilité le 12 mars »."""
        self._poser(session, fin=date(2026, 4, 30), motif=MotifHabilitation.DEPART)
        siennes = DepotHabilitationsSql(session, CABINET).pour_compte("C-1")
        assert len(siennes) == 1
        assert roles_au(siennes, date(2026, 3, 12)) == frozenset({Role.COMPTABLE})
        assert roles_au(siennes, date(2026, 6, 1)) == frozenset()

    def test_pour_dossier_inclut_les_transverses(self, session):
        self._poser(session, role=Role.REVISEUR, portee=None)
        assert len(DepotHabilitationsSql(session, CABINET).pour_dossier(NIU)) == 1


class TestJetonsEtSessions:
    def test_le_jeton_se_retrouve_par_son_empreinte(self, session):
        DepotComptesSql(session, CABINET).enregistrer(_compte())
        jeton = Jeton.emettre(
            identifiant="J-1",
            compte="C-1",
            type=TypeJeton.ACTIVATION,
            secret="mon-secret",
            a_l_instant=INSTANT,
            emis_par="systeme",
        )
        depot = DepotJetonsSql(session, CABINET)
        depot.enregistrer(jeton)
        session.commit()
        assert depot.par_empreinte(empreinte_de("mon-secret")) == jeton
        assert depot.par_empreinte(empreinte_de("autre")) is None

    def test_le_renforcement_de_session_survit(self, session):
        DepotComptesSql(session, CABINET).enregistrer(_compte())
        ouverte = Session(
            identifiant="S-1",
            compte="C-1",
            locataire=CABINET,
            ouverte_le=INSTANT,
            expire_le=INSTANT + timedelta(hours=12),
        ).renforcer(INSTANT, timedelta(minutes=15))
        depot = DepotSessionsSql(session, CABINET)
        depot.enregistrer(ouverte)
        session.commit()
        relue = depot.lire("S-1")
        assert relue is not None
        assert relue.renforcee(INSTANT + timedelta(minutes=10))
        assert not relue.renforcee(INSTANT + timedelta(minutes=20))

    def test_une_session_revoquee_le_reste(self, session):
        DepotComptesSql(session, CABINET).enregistrer(_compte())
        depot = DepotSessionsSql(session, CABINET)
        depot.enregistrer(
            Session(
                identifiant="S-1",
                compte="C-1",
                locataire=CABINET,
                ouverte_le=INSTANT,
                expire_le=INSTANT + timedelta(hours=12),
            ).revoquer(INSTANT, MotifRevocation.SUSPENSION_DU_COMPTE)
        )
        session.commit()
        relue = depot.lire("S-1")
        assert relue is not None and relue.revoquee
        assert relue.motif_revocation is MotifRevocation.SUSPENSION_DU_COMPTE


# ── Le journal d'audit ───────────────────────────────────────────────────────


class TestJournalAudit:
    def test_la_chaine_se_verifie_apres_aller_retour(self, session):
        """Le test le plus important du module : une entrée relue depuis la base
        doit produire la même empreinte, sinon `verifier_chaine` déclarerait
        altérée toute la base au premier redémarrage."""
        journal = JournalAuditSql(session, CABINET)
        for i in range(5):
            journal.ajouter(
                horodatage=INSTANT + timedelta(minutes=i),
                acteur="C-1",
                action="ecriture.validee",
                objet_type="ecriture",
                objet_id=f"2026/AC/{i:06d}",
                apres={
                    "montant": Decimal("1250.75"),
                    "date": date(2026, 7, 12),
                    "role": Role.COMPTABLE,
                },
            )
        session.commit()
        verifier_chaine(journal.lister())

    def test_le_rang_est_unique_par_locataire(self, session):
        """La garantie que la mémoire ne pouvait pas tenir : deux processus qui
        liraient la même tête ne peuvent pas tous deux écrire."""
        journal = JournalAuditSql(session, CABINET)
        journal.ajouter(
            horodatage=INSTANT, acteur="C-1", action="a", objet_type="compte"
        )
        session.commit()
        session.add(
            TableEntreeAudit(
                locataire=CABINET,
                rang=1,
                horodatage=INSTANT,
                acteur="C-2",
                action="forgee",
                objet_type="compte",
                empreinte_precedente="0" * 64,
                empreinte="f" * 64,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    def test_deux_cabinets_ont_chacun_leur_chaine(self, session):
        """Le rang recommence à 1 par locataire : une chaîne partagée ferait
        dépendre l'intégrité de l'un des écritures de l'autre."""
        JournalAuditSql(session, CABINET).ajouter(
            horodatage=INSTANT, acteur="C-1", action="a", objet_type="compte"
        )
        JournalAuditSql(session, AUTRE_CABINET).ajouter(
            horodatage=INSTANT, acteur="X-1", action="a", objet_type="compte"
        )
        session.commit()
        for locataire in (CABINET, AUTRE_CABINET):
            entrees = JournalAuditSql(session, locataire).lister()
            assert [e.rang for e in entrees] == [1]

    def test_le_journal_n_offre_ni_modification_ni_suppression(self, session):
        journal = JournalAuditSql(session, CABINET)
        assert not hasattr(journal, "modifier")
        assert not hasattr(journal, "supprimer")

    def test_les_valeurs_sensibles_restent_expurgees_en_base(self, session):
        journal = JournalAuditSql(session, CABINET)
        entree = journal.ajouter(
            horodatage=INSTANT,
            acteur="C-1",
            action="compte.cree",
            objet_type="compte",
            apres={"empreinte_mot_de_passe": "$argon2id$vrai", "secret": "abc"},
        )
        session.commit()
        assert entree.apres is not None
        assert "$argon2id$vrai" not in str(entree.apres)
        relu = journal.lister()[-1]
        assert relu.apres is not None
        assert relu.apres["secret"] == "«expurgé»"


# ── Le registre des accusés ──────────────────────────────────────────────────


def _document(contenu: str = '{"tva":100}') -> DocumentATransmettre:
    return DocumentATransmettre(
        portail=Portail.DGI_TELEDECLARATION,
        code_document="TVA",
        entreprise=NIU,
        periode_debut=date(2026, 7, 1),
        periode_fin=date(2026, 7, 31),
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
        montant_constate=Decimal("499500.00"),
    )


class TestRegistreDesAccuses:
    """La table la plus critique : une comptabilité se refait, un accusé non."""

    def test_un_accuse_survit_a_l_aller_retour(self, session):
        portail = PortailSql(session, CABINET)
        document = _document()
        portail.enregistrer_accuse(_accuse(document), document=document)
        session.commit()
        relu = portail.retrouver(document.reference)
        assert relu is not None
        assert relu.numero == "DGI-1"
        assert relu.montant_constate == Decimal("499500.00")
        assert relu.concerne(document)

    def test_le_second_depot_est_refuse_par_la_base(self, session):
        """L'unicité est portée par la base, pas par la mémoire de celui qui
        saisit."""
        portail = PortailSql(session, CABINET)
        document = _document()
        portail.enregistrer_accuse(_accuse(document, "DGI-1"), document=document)
        session.commit()
        with pytest.raises(AccuseIncoherent, match="porte déjà l'accusé"):
            portail.enregistrer_accuse(_accuse(document, "DGI-2"), document=document)

    def test_un_accuse_de_la_mauvaise_ligne_est_refuse(self, session):
        portail = PortailSql(session, CABINET)
        with pytest.raises(AccuseIncoherent, match="ne correspond pas"):
            portail.enregistrer_accuse(
                _accuse(_document('{"tva":999}')), document=_document()
            )

    def test_le_contenu_depose_est_conserve(self, session):
        """Sans lui, l'empreinte prouverait qu'on a déposé quelque chose, pas
        quoi."""
        portail = PortailSql(session, CABINET)
        document = _document()
        portail.enregistrer_accuse(_accuse(document), document=document)
        session.commit()
        garde = session.execute(
            text("select contenu_depose from accuse_reception where numero = 'DGI-1'")
        ).scalar_one()
        assert garde == document.contenu

    def test_deux_cabinets_deposent_la_meme_periode_sans_se_gener(self, session):
        """L'unicité est **par locataire** : deux centres peuvent suivre deux
        entreprises différentes qui portent le même NIU chez eux — et surtout,
        l'un ne doit jamais bloquer l'autre."""
        document = _document()
        PortailSql(session, CABINET).enregistrer_accuse(
            _accuse(document, "DGI-A"), document=document
        )
        PortailSql(session, AUTRE_CABINET).enregistrer_accuse(
            _accuse(document, "DGI-B"), document=document
        )
        session.commit()
        assert PortailSql(session, CABINET).retrouver(document.reference).numero == "DGI-A"

    def test_il_ne_depose_toujours_pas_tout_seul(self, session):
        assert not PortailSql(session, CABINET).depose_automatiquement()


# ── L'application entière ────────────────────────────────────────────────────


class TestApplicationSurPostgresql:
    """Le test qui manquait, et qui aurait trouvé le défaut.

    ─────────────────────────────────────────────────────────────────────────
    LES DÉPÔTS PEUVENT ÊTRE JUSTES ET L'APPLICATION FAUSSE

    Les tests précédents exercent chaque dépôt isolément, et ils passaient tous
    alors qu'une route consignait les accusés de réception dans **son propre
    registre en mémoire** — un `lru_cache` local, invisible de la base. Le refus
    du second dépôt fonctionnait, la suite était verte, et la preuve de dépôt
    disparaissait au redémarrage pendant que tout le reste persistait.

    Ce défaut ne pouvait se voir qu'en faisant tourner l'application réelle sur
    une vraie base, **puis en redémarrant**. C'est ce que fait cette classe.
    ─────────────────────────────────────────────────────────────────────────
    """

    @pytest.fixture
    def application(self, moteur_test, monkeypatch):
        """Bascule la configuration en PostgreSQL, amorce, et rend un client."""
        from app.contextes.transverse.adaptateurs.entrant.dependances import (
            reinitialiser_atelier,
        )
        from app.infrastructure import base_de_donnees
        from app.infrastructure.config import configuration

        monkeypatch.setenv("CGA_PERSISTANCE", "postgresql")
        monkeypatch.setenv("CGA_URL_BASE_DE_DONNEES", URL_BASE_TEST)
        configuration.cache_clear()
        base_de_donnees.moteur.cache_clear()
        base_de_donnees._fabrique.cache_clear()
        reinitialiser_atelier()

        # L'amorçage complet plutôt que les seuls comptes : un test qui ne
        # verse qu'une partie du jeu vérifie une application que personne
        # n'utilisera jamais dans cet état.
        from app.amorcage import amorcer

        amorcer(forcer=True)

        from fastapi.testclient import TestClient

        from app.main import creer_application

        yield TestClient(creer_application())

        configuration.cache_clear()
        base_de_donnees.moteur.cache_clear()
        base_de_donnees._fabrique.cache_clear()
        reinitialiser_atelier()

    def _renforcer(self, client) -> None:
        from app.contextes.transverse.api import code_attendu
        from app.partage.horloge import maintenant

        secret = enroler_par_le_courriel(client)
        client.post(
            "/transverse/session/renforcement",
            json={"code": code_attendu(secret, maintenant())},
        )

    @pytest.mark.usefixtures("depot_tva_sans_revue_exigee")
    def test_le_parcours_complet_sur_une_vraie_base(self, application):
        """Connexion, second facteur, dépôt, refus du second — puis relecture
        **hors de l'application**, comme après un redémarrage."""
        from app.contextes.transverse.api import (
            MOT_DE_PASSE_DEMO,
            unite_de_travail,
            verifier_chaine,
        )

        connexion = application.post(
            "/transverse/session",
            json={"courriel": "a.bouba@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        assert connexion.status_code == 200, connexion.text
        assert connexion.json()["roles"] == ["REVISEUR"]

        self._renforcer(application)
        parametres = {
            "periode_debut": "2026-07-01",
            "periode_fin": "2026-07-31",
            "a_la_date": "2026-08-15",
        }
        depot = application.post(
            f"/obligations/dossiers/{NIU}/depot-tva",
            params=parametres,
            json={"numero": "DGI-2026-0007741", "depose_le": "2026-08-14T10:22:00"},
        )
        assert depot.status_code == 200, depot.text
        assert depot.json()["obligation"]["statut"] == "DECLAREE"

        rejoue = application.post(
            f"/obligations/dossiers/{NIU}/depot-tva",
            params=parametres,
            json={"numero": "DGI-2026-0007742", "depose_le": "2026-08-14T10:30:00"},
        )
        assert rejoue.status_code == 409

        # ── Le redémarrage : on relit hors de l'application ─────────────────
        # Le locataire est **nommé** ici. Hors requête, aucun intergiciel ne l'a
        # établi, et `unite_de_travail` refuse de deviner : lire des données sans
        # dire pour qui servirait des lignes arbitraires sans que rien ne le
        # signale. Voir `app/partage/locataire.py`.
        with unite_de_travail(LOCATAIRE_PAR_DEFAUT) as boutique:
            accuse = boutique.portail.retrouver(
                f"{NIU}/TVA/20260701-20260731"
            )
            assert accuse is not None, (
                "l'accusé n'est pas en base : une route le consigne ailleurs. "
                "C'est exactement le défaut que ce test existe pour attraper."
            )
            assert accuse.numero == "DGI-2026-0007741"

            entrees = boutique.journal.lister()
            verifier_chaine(entrees)
            assert [e.action for e in entrees] == [
                "session.ouverte",
                # ⚠️ Le lien d'enrôlement (pas 62) : le premier enrôlement se prouve
                # par la boîte aux lettres, et l'émission du lien est un acte journalisé.
                "jeton.emis",
                "compte.second_facteur_enrole",
                "session.renforcee",
                "obligation.deposee",
            ]

    def test_le_second_facteur_survit_au_redemarrage(self, application):
        from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, unite_de_travail

        application.post(
            "/transverse/session",
            json={"courriel": "a.bouba@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        self._renforcer(application)
        with unite_de_travail(LOCATAIRE_PAR_DEFAUT) as boutique:
            assert boutique.comptes.lire("C-003").second_facteur_actif

    def test_les_cinq_contextes_lisent_la_base(self, application):
        """Le portefeuille, la collecte, la comptabilité, les obligations et la
        souscription — tous branchés sur la même unité de travail.

        Ce qui est vérifié n'est pas que chaque dépôt fonctionne — les classes
        précédentes s'en chargent — mais qu'**aucune route ne s'est laissée un
        registre en mémoire**. C'est le défaut trouvé sur les accusés de
        réception, et il pouvait se reproduire cinq fois.
        """
        from app.contextes.transverse.api import MOT_DE_PASSE_DEMO

        application.post(
            "/transverse/session",
            json={"courriel": "a.bouba@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        jour = {"a_la_date": "2026-08-15"}
        assert len(application.get("/portefeuille/entreprises", params=jour).json()) == 6
        assert len(application.get("/collecte/pieces", params=jour).json()) == 30
        assert application.get("/collecte/demandes").json()

        ecritures = application.get(
            f"/comptabilite/dossiers/{NIU}/ecritures", params={"exercice": "2026"}
        ).json()
        assert ecritures

        sante = application.get(
            f"/comptabilite/dossiers/{NIU}/sante", params={"exercice": "2026"}
        ).json()
        assert sante["equilibree"] is True

        assert application.get(
            f"/comptabilite/dossiers/{NIU}/plan-imputation"
        ).json()["regles"]

        tva = application.get(
            f"/obligations/dossiers/{NIU}/declaration-tva",
            params={"periode_debut": "2026-07-01", "periode_fin": "2026-07-31"},
        ).json()
        assert Decimal(tva["tva_rejetee"]) > 0

    def test_la_souscription_survit_au_redemarrage(self, application):
        """Devis, engagement, encaissement, ouverture d'accès — puis relecture
        hors de l'application."""
        from app.contextes.transverse.api import unite_de_travail

        devis = application.post(
            "/souscription/devis",
            json={
                "prospect": {
                    "nom": "MBIDA",
                    "prenom": "Paul",
                    "courriel": "p.mbida@exemple.cm",
                    "telephone": "677445566",
                    "niu": NIU,
                    "chiffre_affaires_declare": "10000000",
                },
                "lignes": [{"service": "ADHESION"}],
            },
        ).json()
        engagement = application.post(
            f"/souscription/devis/{devis['reference']}/engagement", json={}
        ).json()
        encaissement = application.post(
            f"/souscription/paiements/{engagement['paiement']['identifiant']}/simulation",
            json={"reussi": True},
        ).json()
        # ⚠️ Pas 83 : payer n'ouvre plus l'accès ; le cabinet vérifie l'identité, puis
        # ouvre. La vérification elle-même doit survivre au redémarrage : c'est elle
        # qui dit, au journal, qui a ouvert ce dossier à ce payeur.
        assert encaissement["activee"] is False
        from app.contextes.transverse.api import MOT_DE_PASSE_DEMO

        application.post(
            "/transverse/session",
            json={"courriel": "s.onana@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        activation = application.post(
            f"/souscription/souscriptions/{engagement['souscription']['reference']}/activation",
            json={"verification": "RCCM et CNI du gérant présentés au cabinet ce jour."},
        )
        assert activation.status_code == 200, activation.text
        encaissement = {"compte": activation.json()["compte"]}

        with unite_de_travail(LOCATAIRE_PAR_DEFAUT) as boutique:
            souscription = boutique.session.get(
                __import__(
                    "app.contextes.souscription.adaptateurs.sortant.tables",
                    fromlist=["TableSouscription"],
                ).TableSouscription,
                {"reference": engagement["souscription"]["reference"]},
            )
            assert souscription is not None
            assert souscription.etat == "ACTIVEE"
            assert souscription.compte == encaissement["compte"]
            # Le compte adhérent créé par la souscription est en base lui aussi.
            assert boutique.comptes.lire(encaissement["compte"]).courriel == (
                "p.mbida@exemple.cm"
            )

    def test_les_gestes_d_exploitation_ecrivent_en_base(self, application):
        """Pas 84 : reprendre un travail abandonné et remettre un événement en
        quarantaine, sur les dépôts SQL, puis relecture hors de l'application.

        ⚠️ Le passage repris porte une fin **effacée** : c'est ce qui le rend dû tout de
        suite. Une colonne qui refuserait la valeur nulle ferait échouer la reprise en
        base seulement, là où la mémoire l'accepte.
        """
        from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, unite_de_travail
        from app.infrastructure.depots_orchestration import BoiteDEnvoiSql, DepotPassagesSql
        from app.orchestration.boite_d_envoi import EvenementSortant
        from app.orchestration.ordonnanceur import ECHECS_AVANT_ABANDON, Passage

        instant = datetime(2026, 9, 15, 9, 0)
        with unite_de_travail(LOCATAIRE_PAR_DEFAUT) as boutique:
            DepotPassagesSql(boutique.session).enregistrer(
                Passage(
                    travail="relance",
                    debute_le=instant,
                    termine_le=instant,
                    echecs_consecutifs=ECHECS_AVANT_ABANDON,
                    dernier_echec="base injoignable",
                )
            )
            BoiteDEnvoiSql(boutique.session, LOCATAIRE_PAR_DEFAUT).deposer(
                EvenementSortant(
                    identifiant="EV-PG-84",
                    nom="EvenementDEssai84",
                    cle="ESSAI-84",
                    cree_le=instant,
                    tentatives=10,
                    dernier_echec="abonné : refus",
                    en_quarantaine=True,
                )
            )
        try:
            application.post(
                "/transverse/session",
                json={"courriel": "s.onana@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
            )
            motif = {"motif": "Cause corrigée, essai sur base réelle."}
            reprise = application.post("/orchestration/ordonnancement/relance/reprise", json=motif)
            assert reprise.status_code == 200, reprise.text
            remise = application.post("/orchestration/quarantaine/EV-PG-84/remise", json=motif)
            assert remise.status_code == 200, remise.text

            with unite_de_travail(LOCATAIRE_PAR_DEFAUT) as boutique:
                relance = DepotPassagesSql(boutique.session).tous()["relance"]
                assert relance.echecs_consecutifs == 0
                assert relance.termine_le is None
                assert not relance.jamais_passe
                boite = BoiteDEnvoiSql(boutique.session, LOCATAIRE_PAR_DEFAUT)
                assert "EV-PG-84" not in [e.identifiant for e in boite.en_quarantaine()]
                assert "EV-PG-84" in [e.identifiant for e in boite.a_publier(1000)]
                actions = [e.action for e in boutique.journal.lister()]
                assert "orchestration.travail_repris" in actions
                assert "orchestration.evenement_remis" in actions
        finally:
            # Les tables d'orchestration ne sont pas remises à zéro par l'amorçage :
            # ne rien laisser derrière soi pour les tests suivants.
            with unite_de_travail(LOCATAIRE_PAR_DEFAUT) as boutique:
                boutique.session.execute(
                    text("DELETE FROM boite_d_envoi WHERE identifiant = 'EV-PG-84'")
                )
                boutique.session.execute(
                    text("DELETE FROM passage_ordonnance WHERE travail = 'relance'")
                )

    def test_le_cloisonnement_tient_a_travers_les_routes(self, application):
        """Le filtre de session s'applique aux requêtes réelles, pas seulement
        aux dépôts pris isolément."""
        from app.contextes.transverse.api import MOT_DE_PASSE_DEMO

        application.post(
            "/transverse/session",
            json={"courriel": "l.fotso@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        lignes = application.get(
            "/portefeuille/entreprises", params={"a_la_date": "2026-08-15"}
        ).json()
        assert len(lignes) == 3
