"""Contexte K · Transverse — identité, habilitations, sessions, audit.

Ces tests portent sur les propriétés dont dépend la sécurité du système, et non
sur la mécanique des accesseurs. Chacun devrait pouvoir être lu à voix haute
devant quelqu'un qui n'écrit pas de code : « un administrateur ne peut pas valider
d'écriture », « le lien d'activation ne sert qu'une fois », « une entrée d'audit
modifiée est détectée au rang exact ».
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.contextes.portefeuille.api import PORTEFEUILLE_DEMO
from app.contextes.transverse.adaptateurs.entrant.dependances import (
    atelier,
    reinitialiser_atelier,
)
from app.contextes.transverse.api import (
    COMPTES_DEMO,
    GENESE,
    HABILITATIONS_DEMO,
    LONGUEUR_MINIMALE,
    MOT_DE_PASSE_DEMO,
    NIU_DEMO,
    PERMISSIONS_PAR_ROLE,
    ROLES_A_PORTEE_OBLIGATOIRE,
    Acces,
    AccesRefuse,
    Compte,
    CourrielDejaPris,
    CourrielEnDouble,
    DepotComptesMemoire,
    DepotHabilitationsMemoire,
    DepotJetonsMemoire,
    DepotSessionsMemoire,
    EntreeAudit,
    EtatCompte,
    Habilitation,
    IdentifiantsRefuses,
    Jeton,
    JetonInvalide,
    JournalAltere,
    JournalAuditMemoire,
    MotDePasseRefuse,
    MotifHabilitation,
    MotifRequis,
    MotifRevocation,
    Permission,
    Role,
    SecondFacteurRequis,
    ServiceEmpreinteArgon2,
    ServiceNotificationMemoire,
    Session,
    SessionInvalide,
    TypeJeton,
    accorder,
    affecter_dossier,
    controler_mot_de_passe,
    definir_mot_de_passe,
    depots_demo,
    dossiers_accessibles,
    emettre_jeton,
    empreinte_de,
    fermer_habilitation,
    fermer_session,
    inviter_collaborateur,
    ouvrir_acces_adherent,
    ouvrir_session,
    permissions_de,
    resoudre_acces,
    retablir_compte,
    revoquer_les_sessions,
    roles_au,
    suspendre_compte,
    verifier_chaine,
    verifier_session,
)
from app.contextes.transverse.domaine.jetons import engendrer_secret
from app.main import creer_application
from app.partage.horloge import horloge_figee

INSTANT = datetime(2026, 8, 15, 9, 30)
AUJOURD_HUI = INSTANT.date()


# ── Outillage ────────────────────────────────────────────────────────────────


@pytest.fixture
def empreintes() -> ServiceEmpreinteArgon2:
    """Paramètres allégés : ces tests dérivent des dizaines d'empreintes, et le
    profil de production coûterait plusieurs secondes. Ce qui est vérifié ici est
    le comportement de l'adaptateur, pas la solidité d'Argon2 — laquelle ne se
    teste pas, elle se paramètre."""
    return ServiceEmpreinteArgon2(time_cost=1, memory_cost=8, parallelism=1)


@pytest.fixture
def depots():
    return (
        DepotComptesMemoire(),
        DepotHabilitationsMemoire(),
        DepotJetonsMemoire(),
        DepotSessionsMemoire(),
        JournalAuditMemoire(),
    )


def _compte(
    identifiant: str = "C-1",
    courriel: str = "test@cga-brcg.cm",
    *,
    empreinte: str | None = "x" * 20,
    etat: EtatCompte = EtatCompte.ACTIF,
) -> Compte:
    return Compte(
        identifiant=identifiant,
        courriel=courriel,
        nom="ESSOMBA",
        prenom="Marie",
        locataire="CGA-BRCG",
        empreinte_mot_de_passe=empreinte,
        etat=etat,
        cree_le=INSTANT,
    )


def _hab(
    role: Role = Role.COMPTABLE,
    portee: set[str] | None = None,
    debut: date = date(2020, 1, 1),
    fin: date | None = None,
    identifiant: str = "H-1",
    compte: str = "C-1",
) -> Habilitation:
    return Habilitation(
        identifiant=identifiant,
        compte=compte,
        role=role,
        portee=None if portee is None else frozenset(portee),
        debut=debut,
        fin=fin,
        motif=MotifHabilitation.RECRUTEMENT,
        accordee_par="C-0",
    )


def _administrateur_distinct() -> Acces:
    """Un administrateur **qui n'est pas** le compte suspendu.

    ⚠️ Pas 69 : `_acces` rend toujours le compte « C-1 », et les cas de suspension
    suspendaient « C-1 » : ils se suspendaient eux-mêmes sans le vouloir. La garde
    contre l'auto-suspension l'a montré.
    """
    return _acces([Role.ADMINISTRATEUR]).model_copy(update={"compte": "C-ADMIN"})


def _acces(
    roles: list[Role],
    dossiers: list[str] | None = None,
    *,
    facteur_fort: bool = False,
) -> Acces:
    return Acces(
        compte="C-1",
        locataire="CGA-BRCG",
        session="S-1",
        nom_complet="Marie ESSOMBA",
        roles=roles,
        permissions=sorted(permissions_de(frozenset(roles)), key=lambda p: p.value),
        dossiers=dossiers,
        facteur_fort=facteur_fort,
        a_la_date=AUJOURD_HUI,
    )


# ── Rôles et permissions ─────────────────────────────────────────────────────


class TestRoles:
    def test_l_administrateur_ne_valide_aucune_ecriture(self):
        """Séparation des tâches : celui qui peut s'octroyer un droit ne doit pas
        être celui qui l'exerce."""
        administrateur = PERMISSIONS_PAR_ROLE[Role.ADMINISTRATEUR]
        assert Permission.VALIDER_ECRITURE not in administrateur
        assert Permission.DEPOSER_DECLARATION not in administrateur
        assert Permission.ECARTER_CONSTAT not in administrateur
        assert Permission.CLOTURER_EXERCICE not in administrateur

    def test_l_administrateur_gere_bien_les_comptes(self):
        assert Permission.GERER_COMPTES in PERMISSIONS_PAR_ROLE[Role.ADMINISTRATEUR]

    def test_seul_le_reviseur_depose(self):
        deposent = [
            role
            for role, permissions in PERMISSIONS_PAR_ROLE.items()
            if Permission.DEPOSER_DECLARATION in permissions
        ]
        assert deposent == [Role.REVISEUR]

    def test_seul_le_fiscaliste_touche_au_referentiel(self):
        modifient = {
            role
            for role, permissions in PERMISSIONS_PAR_ROLE.items()
            if Permission.MODIFIER_PARAMETRE in permissions
            or Permission.MODIFIER_REGLE in permissions
        }
        assert modifient == {Role.FISCALISTE}

    def test_l_inspecteur_ne_peut_rien_ecrire(self):
        """Lecture et avis seulement — c'est ce que dit le dossier de sécurité."""
        ecritures = {
            Permission.SAISIR_ECRITURE,
            Permission.VALIDER_ECRITURE,
            Permission.CONTRE_PASSER,
            Permission.ECARTER_CONSTAT,
            Permission.DEPOSER_DECLARATION,
            Permission.CLOTURER_EXERCICE,
            Permission.IDENTIFIER_PIECE,
            Permission.GERER_COMPTES,
        }
        assert not (PERMISSIONS_PAR_ROLE[Role.INSPECTEUR] & ecritures)

    def test_l_adherent_ne_voit_pas_la_comptabilite(self):
        assert Permission.LIRE_COMPTABILITE not in PERMISSIONS_PAR_ROLE[Role.ADHERENT]
        assert Permission.DEPOSER_PIECE in PERMISSIONS_PAR_ROLE[Role.ADHERENT]

    def test_le_cumul_de_roles_additionne_les_permissions(self):
        """Union, pas intersection : une personne comptable et chargée de
        clientèle cumule les deux métiers."""
        cumul = permissions_de(frozenset({Role.COMPTABLE, Role.CHARGE_CLIENTELE}))
        assert Permission.VALIDER_ECRITURE in cumul
        assert Permission.RELANCER_ADHERENT in cumul

    def test_les_deux_roles_externes_exigent_une_portee(self):
        assert ROLES_A_PORTEE_OBLIGATOIRE == frozenset({Role.ADHERENT, Role.INSPECTEUR})


# ── Le compte ────────────────────────────────────────────────────────────────


class TestCompte:
    def test_l_adresse_est_normalisee(self):
        assert _compte(courriel="  Jean@CGA-BRCG.CM ").courriel == "jean@cga-brcg.cm"

    def test_une_adresse_sans_arobase_est_refusee(self):
        with pytest.raises(ValueError, match="adresse électronique"):
            _compte(courriel="jean.cga-brcg.cm")

    def test_un_compte_sans_empreinte_n_est_pas_actif(self):
        """L'état le plus fréquent en production : créé, lien parti, mot de passe
        jamais défini. Ni actif, ni inexistant."""
        compte = _compte(empreinte=None, etat=EtatCompte.EN_ATTENTE_ACTIVATION)
        assert not compte.active
        with pytest.raises(Exception, match="jamais activé"):
            compte.exiger_ouverture(INSTANT)

    def test_un_compte_suspendu_ne_s_ouvre_pas(self):
        with pytest.raises(Exception, match="suspendu"):
            _compte().suspendre().exiger_ouverture(INSTANT)

    def test_le_verrou_se_perime_tout_seul(self):
        """Une date, pas un drapeau : rien n'a besoin de venir le lever."""
        verrouille = _compte().model_copy(
            update={"verrouille_jusqu_a": INSTANT + timedelta(minutes=15)}
        )
        assert verrouille.verrouille(INSTANT)
        assert not verrouille.verrouille(INSTANT + timedelta(minutes=16))

    def test_le_verrou_tombe_au_cinquieme_echec_pas_avant(self):
        compte = _compte()
        for _ in range(4):
            compte = compte.apres_echec(INSTANT, timedelta(minutes=15))
        assert compte.verrouille_jusqu_a is None
        compte = compte.apres_echec(INSTANT, timedelta(minutes=15))
        assert compte.verrouille_jusqu_a == INSTANT + timedelta(minutes=15)

    def test_definir_un_mot_de_passe_libere_le_verrou(self):
        """Celui qui a prouvé, par un lien reçu sur sa boîte, qu'il contrôle
        l'adresse, n'a pas à subir le verrou provoqué par un autre."""
        verrouille = _compte().model_copy(
            update={"tentatives_echouees": 5, "verrouille_jusqu_a": INSTANT}
        )
        libere = verrouille.avec_empreinte("neuve", a_l_instant=INSTANT)
        assert libere.verrouille_jusqu_a is None
        assert libere.tentatives_echouees == 0
        assert libere.etat == EtatCompte.ACTIF

    def test_retablir_rend_l_etat_que_le_mot_de_passe_commande(self):
        jamais_active = _compte(empreinte=None, etat=EtatCompte.ACTIF).suspendre()
        assert jamais_active.retablir().etat == EtatCompte.EN_ATTENTE_ACTIVATION
        assert _compte().suspendre().retablir().etat == EtatCompte.ACTIF


# ── L'habilitation datée ─────────────────────────────────────────────────────


class TestHabilitation:
    def test_un_adherent_sans_portee_est_refuse(self):
        """La faute la plus coûteuse que ce contexte puisse laisser passer : un
        adhérent qui lirait la comptabilité de ses concurrents."""
        with pytest.raises(ValueError, match="portée explicite"):
            _hab(role=Role.ADHERENT, portee=None)

    def test_un_comptable_peut_etre_transverse(self):
        assert _hab(role=Role.COMPTABLE, portee=None).transverse

    def test_la_borne_haute_est_exclue(self):
        habilitation = _hab(debut=date(2020, 1, 1), fin=date(2026, 4, 30))
        assert habilitation.couvre(date(2026, 4, 29))
        assert not habilitation.couvre(date(2026, 4, 30))

    def test_qui_etait_habilite_le_12_mars(self):
        """La question à laquelle tout ce module sert à répondre."""
        partie = _hab(fin=date(2026, 4, 30))
        assert roles_au([partie], date(2026, 3, 12)) == frozenset({Role.COMPTABLE})
        assert roles_au([partie], date(2026, 6, 1)) == frozenset()

    def test_fermer_ne_supprime_pas(self):
        fermee = _hab().fermer(date(2026, 4, 30), motif=MotifHabilitation.DEPART, par="C-2")
        assert fermee.fin == date(2026, 4, 30)
        assert fermee.identifiant == "H-1"
        assert "C-2" in (fermee.precision or "")

    def test_on_ne_ferme_pas_deux_fois(self):
        fermee = _hab(fin=date(2026, 1, 1))
        with pytest.raises(ValueError, match="déjà fermée"):
            fermee.fermer(date(2026, 5, 1), motif=MotifHabilitation.DEPART, par="C-2")

    def test_une_fin_anterieure_au_debut_est_refusee(self):
        with pytest.raises(ValueError, match="durée nulle"):
            _hab(debut=date(2026, 5, 1), fin=date(2026, 5, 1))

    def test_le_transverse_l_emporte_sur_toute_liste(self):
        """Une personne à la fois réviseur transverse et adhérente voit tout :
        un réviseur qui perdrait ses droits parce qu'on lui a ouvert un compte
        adhérent croirait à une panne."""
        habilitations = [
            _hab(role=Role.REVISEUR, portee=None, identifiant="H-1"),
            _hab(role=Role.ADHERENT, portee={"M08"}, identifiant="H-2"),
        ]
        assert dossiers_accessibles(habilitations, AUJOURD_HUI) is None

    def test_sans_habilitation_active_aucun_dossier(self):
        assert dossiers_accessibles([_hab(fin=date(2021, 1, 1))], AUJOURD_HUI) == frozenset()

    def test_etendre_une_habilitation_transverse_est_refuse(self):
        with pytest.raises(ValueError, match="déjà transverse"):
            _hab(portee=None).affecter(
                "M08", le=AUJOURD_HUI, identifiant_successeur="H-2", par="C-0"
            )


# ── Les jetons ───────────────────────────────────────────────────────────────


class TestJeton:
    def _emettre(self, type: TypeJeton = TypeJeton.ACTIVATION, secret: str = "s3cr3t") -> Jeton:
        return Jeton.emettre(
            identifiant="J-1",
            compte="C-1",
            type=type,
            secret=secret,
            a_l_instant=INSTANT,
            emis_par="systeme",
        )

    def test_le_secret_n_est_jamais_conserve(self):
        """Une copie de la base ne doit donner accès à aucun compte."""
        jeton = self._emettre(secret="mon-secret")
        serialise = jeton.model_dump_json()
        assert "mon-secret" not in serialise
        assert jeton.empreinte == empreinte_de("mon-secret")

    def test_trois_durees_parce_que_trois_risques(self):
        assert self._emettre(TypeJeton.ACTIVATION).expire_le == INSTANT + timedelta(days=7)
        assert self._emettre(TypeJeton.REINITIALISATION).expire_le == INSTANT + timedelta(
            hours=2
        )
        assert self._emettre(TypeJeton.INVITATION).expire_le == INSTANT + timedelta(days=14)

    def test_un_jeton_ne_sert_qu_une_fois(self):
        consomme = self._emettre().consommer(INSTANT)
        assert consomme.consomme
        with pytest.raises(JetonInvalide, match="déjà consommé"):
            consomme.consommer(INSTANT)

    def test_un_jeton_expire_ne_se_consomme_pas(self):
        with pytest.raises(JetonInvalide, match="expiré"):
            self._emettre(TypeJeton.REINITIALISATION).consommer(INSTANT + timedelta(hours=3))

    def test_deux_emissions_donnent_deux_secrets(self):
        assert engendrer_secret() != engendrer_secret()


# ── Les sessions ─────────────────────────────────────────────────────────────


class TestSession:
    def _session(self) -> Session:
        return Session(
            identifiant="S-1",
            compte="C-1",
            locataire="CGA-BRCG",
            ouverte_le=INSTANT,
            expire_le=INSTANT + timedelta(hours=12),
        )

    def test_une_session_revoquee_ne_vaut_plus_rien(self):
        fermee = self._session().revoquer(INSTANT, MotifRevocation.REVOCATION_ADMINISTRATIVE)
        assert not fermee.active(INSTANT)
        with pytest.raises(SessionInvalide, match="révoquée"):
            fermee.exiger_active(INSTANT)

    def test_revoquer_deux_fois_n_est_pas_une_erreur(self):
        une_fois = self._session().revoquer(INSTANT, MotifRevocation.DECONNEXION)
        assert une_fois.revoquer(INSTANT + timedelta(hours=1), MotifRevocation.EXPIRATION) is (
            une_fois
        )

    def test_une_session_n_est_pas_renforcee_par_defaut(self):
        """On ne réclame pas de code à l'ouverture : un outil ouvert dix fois par
        jour finirait avec le téléphone posé déverrouillé à côté du clavier."""
        assert not self._session().renforcee(INSTANT)

    def test_le_renforcement_se_perime_tout_seul(self):
        renforcee = self._session().renforcer(INSTANT, timedelta(minutes=15))
        assert renforcee.renforcee(INSTANT + timedelta(minutes=14))
        assert not renforcee.renforcee(INSTANT + timedelta(minutes=15))


# ── Le journal d'audit ───────────────────────────────────────────────────────


class TestAudit:
    def _chaine(self, journal: JournalAuditMemoire, combien: int = 3) -> list[EntreeAudit]:
        for i in range(combien):
            journal.ajouter(
                horodatage=INSTANT + timedelta(minutes=i),
                acteur="C-1",
                action="ecriture.validee",
                objet_type="ecriture",
                objet_id=f"2026/AC/{i:06d}",
            )
        return journal.lister()

    def test_la_premiere_entree_porte_la_genese(self):
        journal = JournalAuditMemoire()
        entrees = self._chaine(journal, 1)
        assert entrees[0].rang == 1
        assert entrees[0].empreinte_precedente == GENESE

    def test_une_chaine_intacte_se_verifie(self):
        verifier_chaine(self._chaine(JournalAuditMemoire()))

    def test_un_contenu_modifie_est_detecte_au_bon_rang(self):
        """Le rang exact importe : les entrées qui précèdent restent fiables."""
        entrees = self._chaine(JournalAuditMemoire(), 5)
        falsifiees = list(entrees)
        falsifiees[2] = falsifiees[2].model_copy(update={"acteur": "C-9"})
        with pytest.raises(JournalAltere, match="rang 3"):
            verifier_chaine(falsifiees)

    def test_une_entree_supprimee_est_detectee(self):
        entrees = self._chaine(JournalAuditMemoire(), 5)
        with pytest.raises(JournalAltere, match="rupture de séquence"):
            verifier_chaine([*entrees[:2], *entrees[3:]])

    def test_modifier_une_entree_invalide_toutes_les_suivantes(self):
        """C'est la propriété qui fait l'intérêt du chaînage : on ne peut pas
        retoucher une ligne ancienne sans laisser de trace en aval."""
        entrees = self._chaine(JournalAuditMemoire(), 4)
        recalculee = entrees[1].model_copy(update={"acteur": "C-9"})
        assert recalculee.recalculer() != entrees[2].empreinte_precedente

    def test_le_mot_de_passe_n_entre_jamais_au_journal(self):
        journal = JournalAuditMemoire()
        entree = journal.ajouter(
            horodatage=INSTANT,
            acteur="C-1",
            action="compte.cree",
            objet_type="compte",
            apres={"courriel": "a@b.cm", "empreinte_mot_de_passe": "$argon2id$secret"},
        )
        assert entree.apres is not None
        assert "$argon2id$" not in str(entree.apres)
        assert entree.apres["empreinte_mot_de_passe"] == "«expurgé»"

    def test_l_expurgation_est_recursive(self):
        journal = JournalAuditMemoire()
        entree = journal.ajouter(
            horodatage=INSTANT,
            acteur="C-1",
            action="jeton.emis",
            objet_type="compte",
            apres={"lien": {"secret": "abcdef", "type": "ACTIVATION"}},
        )
        assert entree.apres is not None
        assert entree.apres["lien"]["secret"] == "«expurgé»"

    def test_le_journal_n_offre_ni_modification_ni_suppression(self):
        journal = JournalAuditMemoire()
        assert not hasattr(journal, "modifier")
        assert not hasattr(journal, "supprimer")

    def test_ce_qui_est_hache_est_ce_qui_est_stocke(self):
        """Sans quoi la vérification échouerait sur les entrées expurgées."""
        journal = JournalAuditMemoire()
        journal.ajouter(
            horodatage=INSTANT,
            acteur="C-1",
            action="compte.cree",
            objet_type="compte",
            apres={"secret": "à masquer"},
        )
        verifier_chaine(journal.lister())


# ── L'autorisation ───────────────────────────────────────────────────────────


class TestAutorisation:
    def test_le_droit_sans_le_perimetre_ne_suffit_pas(self):
        """Deux comptables aux mêmes permissions ne voient pas les mêmes dossiers."""
        acces = _acces([Role.COMPTABLE], dossiers=["M08"])
        acces.exiger(Permission.VALIDER_ECRITURE, dossier="M08")
        with pytest.raises(AccesRefuse, match="périmètre"):
            acces.exiger(Permission.VALIDER_ECRITURE, dossier="M07")

    def test_le_refus_de_droit_precede_le_refus_de_perimetre(self):
        """Un refus ne doit jamais révéler qu'un dossier existe à quelqu'un qui
        n'a de toute façon pas la permission."""
        acces = _acces([Role.ADHERENT], dossiers=["M08"])
        with pytest.raises(AccesRefuse, match="ne détient pas"):
            acces.exiger(Permission.VALIDER_ECRITURE, dossier="M07")

    def test_le_transverse_voit_tout(self):
        _acces([Role.REVISEUR], dossiers=None).exiger(
            Permission.VALIDER_ECRITURE, dossier="n-importe-quoi"
        )

    def test_le_depot_de_declaration_est_bloque_faute_de_second_facteur(self):
        with pytest.raises(SecondFacteurRequis):
            _acces([Role.REVISEUR]).exiger(Permission.DEPOSER_DECLARATION)

    def test_le_second_facteur_debloque_le_depot(self):
        _acces([Role.REVISEUR], facteur_fort=True).exiger(Permission.DEPOSER_DECLARATION)

    def test_ecarter_un_constat_exige_un_motif(self):
        acces = _acces([Role.REVISEUR])
        with pytest.raises(MotifRequis):
            acces.exiger(Permission.ECARTER_CONSTAT)
        with pytest.raises(MotifRequis):
            acces.exiger(Permission.ECARTER_CONSTAT, motif="   ")
        acces.exiger(Permission.ECARTER_CONSTAT, motif="facture ressaisie, cf. PJ-2026-0412")

    def test_l_acces_dit_s_il_est_interne(self):
        assert _acces([Role.COMPTABLE]).interne
        assert not _acces([Role.ADHERENT], dossiers=["M08"]).interne

    def test_les_roles_sont_resolus_a_la_date_demandee(self):
        compte = _compte()
        session = Session(
            identifiant="S-1",
            compte="C-1",
            locataire="CGA-BRCG",
            ouverte_le=INSTANT,
            expire_le=INSTANT + timedelta(hours=12),
        )
        habilitations = [_hab(fin=date(2026, 4, 30))]
        assert resoudre_acces(compte, session, habilitations, date(2026, 3, 12)).roles == [
            Role.COMPTABLE
        ]
        assert resoudre_acces(compte, session, habilitations, date(2026, 6, 1)).roles == []


# ── L'authentification ───────────────────────────────────────────────────────


class TestAuthentification:
    def _monter(self, depots, empreintes, mot_de_passe="phrase de passe longue"):
        comptes, habilitations, jetons, sessions, journal = depots
        compte = _compte(empreinte=empreintes.deriver(mot_de_passe))
        comptes.enregistrer(compte)
        return compte

    def test_un_compte_inconnu_et_un_mot_de_passe_faux_donnent_le_meme_message(
        self, depots, empreintes
    ):
        """L'oracle d'énumération est refermé : essayer une liste d'adresses
        n'apprend pas lesquelles appartiennent à des adhérents du cabinet."""
        comptes, _, _, sessions, journal = depots
        self._monter(depots, empreintes)

        messages = []
        for courriel, motdepasse in (
            ("inconnu@ailleurs.cm", "peu importe"),
            ("test@cga-brcg.cm", "mauvais mot de passe"),
        ):
            with pytest.raises(IdentifiantsRefuses) as refus:
                ouvrir_session(
                    courriel,
                    motdepasse,
                    identifiant_session="S-1",
                    comptes=comptes,
                    sessions=sessions,
                    empreintes=empreintes,
                    journal=journal,
                    a_l_instant=INSTANT,
                )
            messages.append(str(refus.value))
        assert messages[0] == messages[1]

    def test_l_echec_est_journalise_avec_sa_cause(self, depots, empreintes):
        """Le message ne dit rien à l'appelant, le journal dit tout au serveur."""
        comptes, _, _, sessions, journal = depots
        self._monter(depots, empreintes)
        with pytest.raises(IdentifiantsRefuses):
            ouvrir_session(
                "test@cga-brcg.cm",
                "faux",
                identifiant_session="S-1",
                comptes=comptes,
                sessions=sessions,
                empreintes=empreintes,
                journal=journal,
                a_l_instant=INSTANT,
            )
        entree = journal.lister()[-1]
        assert entree.action == "session.refusee"
        assert entree.apres is not None
        assert entree.apres["cause"] == "mot de passe incorrect"

    def test_cinq_echecs_verrouillent(self, depots, empreintes):
        comptes, _, _, sessions, journal = depots
        self._monter(depots, empreintes)
        for _ in range(5):
            with pytest.raises(IdentifiantsRefuses):
                ouvrir_session(
                    "test@cga-brcg.cm",
                    "faux",
                    identifiant_session="S-x",
                    comptes=comptes,
                    sessions=sessions,
                    empreintes=empreintes,
                    journal=journal,
                    a_l_instant=INSTANT,
                )
        # Même avec le bon mot de passe, le compte est fermé.
        with pytest.raises(IdentifiantsRefuses):
            ouvrir_session(
                "test@cga-brcg.cm",
                "phrase de passe longue",
                identifiant_session="S-6",
                comptes=comptes,
                sessions=sessions,
                empreintes=empreintes,
                journal=journal,
                a_l_instant=INSTANT,
            )
        assert comptes.lire("C-1").verrouille(INSTANT)

    def test_une_ouverture_reussie_remet_les_compteurs_a_zero(self, depots, empreintes):
        comptes, habilitations, _, sessions, journal = depots
        self._monter(depots, empreintes)
        with pytest.raises(IdentifiantsRefuses):
            ouvrir_session(
                "test@cga-brcg.cm", "faux", identifiant_session="S-0",
                comptes=comptes, sessions=sessions, empreintes=empreintes,
                journal=journal, a_l_instant=INSTANT,
            )
        session, compte = ouvrir_session(
            "test@cga-brcg.cm", "phrase de passe longue", identifiant_session="S-1",
            comptes=comptes, sessions=sessions, empreintes=empreintes,
            journal=journal, a_l_instant=INSTANT,
        )
        assert compte.tentatives_echouees == 0
        assert compte.derniere_connexion == INSTANT
        assert session.active(INSTANT)

    def test_un_compte_suspendu_apres_ouverture_perd_sa_session(self, depots, empreintes):
        """C'est la raison d'être des sessions côté serveur : couper l'accès veut
        dire maintenant, pas ce soir."""
        comptes, habilitations, _, sessions, journal = depots
        self._monter(depots, empreintes)
        ouvrir_session(
            "test@cga-brcg.cm", "phrase de passe longue", identifiant_session="S-1",
            comptes=comptes, sessions=sessions, empreintes=empreintes,
            journal=journal, a_l_instant=INSTANT,
        )
        comptes.enregistrer(comptes.lire("C-1").suspendre())
        with pytest.raises(SessionInvalide, match="compte indisponible"):
            verifier_session(
                "S-1", comptes=comptes, sessions=sessions,
                habilitations=habilitations, a_l_instant=INSTANT,
            )

    def test_revoquer_ferme_toutes_les_sessions(self, depots, empreintes):
        comptes, _, _, sessions, journal = depots
        self._monter(depots, empreintes)
        for numero in range(3):
            ouvrir_session(
                "test@cga-brcg.cm", "phrase de passe longue",
                identifiant_session=f"S-{numero}",
                comptes=comptes, sessions=sessions, empreintes=empreintes,
                journal=journal, a_l_instant=INSTANT,
            )
        assert (
            revoquer_les_sessions(
                "C-1", sessions=sessions, journal=journal, a_l_instant=INSTANT,
                motif=MotifRevocation.SUSPENSION_DU_COMPTE, par="C-2",
            )
            == 3
        )

    def test_se_deconnecter_deux_fois_ne_leve_pas(self, depots, empreintes):
        _, _, _, sessions, journal = depots
        fermer_session("inexistante", sessions=sessions, journal=journal, a_l_instant=INSTANT)


# ── L'activation ─────────────────────────────────────────────────────────────


class TestActivation:
    def test_la_politique_impose_une_longueur_pas_une_composition(self):
        assert LONGUEUR_MINIMALE == 12
        # Une phrase sans majuscule, sans chiffre, sans symbole : acceptée.
        controler_mot_de_passe("chemise bleue mardi tarif", _compte())
        with pytest.raises(MotDePasseRefuse, match="caractères au minimum"):
            controler_mot_de_passe("Court2026!", _compte())

    def test_le_mot_de_passe_ne_contient_pas_l_identite_de_son_porteur(self):
        with pytest.raises(MotDePasseRefuse, match="ESSOMBA"):
            controler_mot_de_passe("essomba-le-grand-2026", _compte())

    def test_les_accents_ne_permettent_pas_de_contourner(self):
        compte = _compte().model_copy(update={"prenom": "Aïcha"})
        with pytest.raises(MotDePasseRefuse):
            controler_mot_de_passe("aicha bonjour tout le monde", compte)

    def test_le_lien_mene_a_un_compte_actif(self, depots, empreintes):
        comptes, _, jetons, sessions, journal = depots
        compte = _compte(empreinte=None, etat=EtatCompte.EN_ATTENTE_ACTIVATION)
        comptes.enregistrer(compte)
        _, secret = emettre_jeton(
            compte, TypeJeton.ACTIVATION, identifiant_jeton="J-1",
            jetons=jetons, journal=journal, a_l_instant=INSTANT, emis_par="systeme",
        )
        active = definir_mot_de_passe(
            secret, "chemise bleue mardi tarif",
            comptes=comptes, jetons=jetons, sessions=sessions,
            empreintes=empreintes, journal=journal, a_l_instant=INSTANT,
        )
        assert active.etat == EtatCompte.ACTIF
        assert active.active

    def test_un_mot_de_passe_refuse_ne_detruit_pas_le_lien(self, depots, empreintes):
        """Ce détail décide de la moitié des appels au support."""
        comptes, _, jetons, sessions, journal = depots
        compte = _compte(empreinte=None, etat=EtatCompte.EN_ATTENTE_ACTIVATION)
        comptes.enregistrer(compte)
        _, secret = emettre_jeton(
            compte, TypeJeton.ACTIVATION, identifiant_jeton="J-1",
            jetons=jetons, journal=journal, a_l_instant=INSTANT, emis_par="systeme",
        )
        with pytest.raises(MotDePasseRefuse):
            definir_mot_de_passe(
                secret, "trop court", comptes=comptes, jetons=jetons, sessions=sessions,
                empreintes=empreintes, journal=journal, a_l_instant=INSTANT,
            )
        # Le lien vaut toujours.
        definir_mot_de_passe(
            secret, "chemise bleue mardi tarif", comptes=comptes, jetons=jetons,
            sessions=sessions, empreintes=empreintes, journal=journal, a_l_instant=INSTANT,
        )

    def test_le_lien_ne_sert_pas_deux_fois(self, depots, empreintes):
        comptes, _, jetons, sessions, journal = depots
        compte = _compte(empreinte=None, etat=EtatCompte.EN_ATTENTE_ACTIVATION)
        comptes.enregistrer(compte)
        _, secret = emettre_jeton(
            compte, TypeJeton.ACTIVATION, identifiant_jeton="J-1",
            jetons=jetons, journal=journal, a_l_instant=INSTANT, emis_par="systeme",
        )
        arguments = dict(
            comptes=comptes, jetons=jetons, sessions=sessions,
            empreintes=empreintes, journal=journal, a_l_instant=INSTANT,
        )
        definir_mot_de_passe(secret, "chemise bleue mardi tarif", **arguments)
        with pytest.raises(JetonInvalide):
            definir_mot_de_passe(secret, "autre phrase de passe ici", **arguments)

    def test_changer_de_mot_de_passe_ferme_les_sessions(self, depots, empreintes):
        comptes, _, jetons, sessions, journal = depots
        compte = _compte(empreinte=empreintes.deriver("phrase de passe longue"))
        comptes.enregistrer(compte)
        ouvrir_session(
            "test@cga-brcg.cm", "phrase de passe longue", identifiant_session="S-1",
            comptes=comptes, sessions=sessions, empreintes=empreintes,
            journal=journal, a_l_instant=INSTANT,
        )
        _, secret = emettre_jeton(
            compte, TypeJeton.REINITIALISATION, identifiant_jeton="J-1",
            jetons=jetons, journal=journal, a_l_instant=INSTANT, emis_par="systeme",
        )
        definir_mot_de_passe(
            secret, "chemise bleue mardi tarif", comptes=comptes, jetons=jetons,
            sessions=sessions, empreintes=empreintes, journal=journal, a_l_instant=INSTANT,
        )
        assert sessions.lire("S-1").revoquee
        assert sessions.lire("S-1").motif_revocation == MotifRevocation.CHANGEMENT_MOT_DE_PASSE


# ── L'empreinte ──────────────────────────────────────────────────────────────


class TestEmpreinte:
    def test_deux_fois_le_meme_mot_de_passe_donne_deux_empreintes(self, empreintes):
        """Le sel : sans lui, deux personnes ayant choisi le même mot de passe se
        reconnaîtraient dans un vidage de base."""
        assert empreintes.deriver("phrase") != empreintes.deriver("phrase")

    def test_une_empreinte_illisible_rend_faux_et_ne_leve_pas(self, empreintes):
        """Une erreur 500 sur une empreinte corrompue distinguerait les comptes
        dont les données sont abîmées."""
        assert empreintes.verifier("phrase", "pas une empreinte argon2") is False

    def test_le_mot_de_passe_correct_est_reconnu(self, empreintes):
        assert empreintes.verifier("phrase", empreintes.deriver("phrase"))


# ── L'administration ─────────────────────────────────────────────────────────


class TestAdministration:
    def test_inviter_cree_le_compte_l_habilitation_et_le_lien(self, depots):
        comptes, habilitations, jetons, _, journal = depots
        compte, jeton, secret = inviter_collaborateur(
            par=_acces([Role.ADMINISTRATEUR]),
            identifiant_compte="C-9", identifiant_habilitation="H-9",
            identifiant_jeton="J-9", courriel="n.mbarga@cga-brcg.cm",
            nom="MBARGA", prenom="Nadine", role=Role.COMPTABLE,
            portee=frozenset({"M08"}), depuis=AUJOURD_HUI,
            comptes=comptes, habilitations=habilitations, jetons=jetons,
            journal=journal, a_l_instant=INSTANT,
        )
        assert compte.etat == EtatCompte.EN_ATTENTE_ACTIVATION
        assert jeton.expire_le == INSTANT + timedelta(days=14)
        assert jetons.par_empreinte(empreinte_de(secret)) is not None
        assert habilitations.lire("H-9").role == Role.COMPTABLE

    def test_un_comptable_ne_peut_pas_inviter(self, depots):
        comptes, habilitations, jetons, _, journal = depots
        with pytest.raises(AccesRefuse):
            inviter_collaborateur(
                par=_acces([Role.COMPTABLE], dossiers=["M08"]),
                identifiant_compte="C-9", identifiant_habilitation="H-9",
                identifiant_jeton="J-9", courriel="x@cga-brcg.cm",
                nom="X", prenom="Y", role=Role.COMPTABLE, portee=None,
                depuis=AUJOURD_HUI, comptes=comptes, habilitations=habilitations,
                jetons=jetons, journal=journal, a_l_instant=INSTANT,
            )

    def test_une_adresse_ne_sert_qu_a_un_compte(self, depots):
        comptes, habilitations, jetons, _, journal = depots
        comptes.enregistrer(_compte(courriel="doublon@cga-brcg.cm"))
        with pytest.raises(CourrielDejaPris):
            inviter_collaborateur(
                par=_acces([Role.ADMINISTRATEUR]),
                identifiant_compte="C-9", identifiant_habilitation="H-9",
                identifiant_jeton="J-9", courriel="doublon@cga-brcg.cm",
                nom="X", prenom="Y", role=Role.COMPTABLE, portee=frozenset({"M08"}),
                depuis=AUJOURD_HUI, comptes=comptes, habilitations=habilitations,
                jetons=jetons, journal=journal, a_l_instant=INSTANT,
            )

    def test_un_administrateur_borne_ne_peut_pas_accorder_le_transverse(self, depots):
        """Sans cette borne, GERER_COMPTES serait un chemin d'élévation universel."""
        _, habilitations, _, _, journal = depots
        with pytest.raises(AccesRefuse, match="transverse"):
            accorder(
                par=_acces([Role.ADMINISTRATEUR], dossiers=["M08"]),
                identifiant="H-9", compte="C-2", role=Role.REVISEUR, portee=None,
                depuis=AUJOURD_HUI, motif=MotifHabilitation.RECRUTEMENT,
                habilitations=habilitations, journal=journal, a_l_instant=INSTANT,
            )

    def test_un_administrateur_ne_donne_pas_ce_qu_il_n_a_pas(self, depots):
        _, habilitations, _, _, journal = depots
        with pytest.raises(AccesRefuse, match="M07"):
            accorder(
                par=_acces([Role.ADMINISTRATEUR], dossiers=["M08"]),
                identifiant="H-9", compte="C-2", role=Role.COMPTABLE,
                portee=frozenset({"M07"}), depuis=AUJOURD_HUI,
                motif=MotifHabilitation.RECRUTEMENT,
                habilitations=habilitations, journal=journal, a_l_instant=INSTANT,
            )

    def test_l_ouverture_adherent_impose_le_role_et_la_portee(self, depots):
        """Aucun degré de liberté : c'est ce qui rend sûr un acte déclenché par
        un webhook de paiement."""
        comptes, habilitations, jetons, _, journal = depots
        compte, jeton, _ = ouvrir_acces_adherent(
            identifiant_compte="A-9", identifiant_habilitation="H-9",
            identifiant_jeton="J-9", courriel="dirigeant@entreprise.cm",
            nom="NKOA", prenom="Jean-Pierre", niu="M081234567890P",
            locataire="CGA-BRCG", depuis=AUJOURD_HUI,
            comptes=comptes, habilitations=habilitations, jetons=jetons,
            journal=journal, a_l_instant=INSTANT,
            reference_souscription="SOUS-2026-0042",
        )
        habilitation = habilitations.lire("H-9")
        assert habilitation.role == Role.ADHERENT
        assert habilitation.portee == frozenset({"M081234567890P"})
        assert habilitation.accordee_par == "systeme"
        assert jeton.expire_le == INSTANT + timedelta(days=7)
        assert compte.etat == EtatCompte.EN_ATTENTE_ACTIVATION

    def test_affecter_un_dossier_etend_le_portefeuille(self, depots):
        _, habilitations, _, _, journal = depots
        habilitations.enregistrer(_hab(portee={"M08"}))
        etendue = affecter_dossier(
            par=_acces([Role.DIRECTION]), identifiant_habilitation="H-1",
            identifiant_successeur="H-2", niu="M07",
            habilitations=habilitations, journal=journal, a_l_instant=INSTANT,
        )
        assert etendue.portee == frozenset({"M08", "M07"})
        # ⚠️ Pas 70 : H-1 avait couru depuis 2020, elle est relayée et non réécrite.
        assert etendue.identifiant == "H-2"
        assert habilitations.lire("H-1").portee == frozenset({"M08"})

    def test_suspendre_coupe_les_sessions_ouvertes(self, depots, empreintes):
        comptes, _, _, sessions, journal = depots
        comptes.enregistrer(_compte(empreinte=empreintes.deriver("phrase de passe longue")))
        ouvrir_session(
            "test@cga-brcg.cm", "phrase de passe longue", identifiant_session="S-1",
            comptes=comptes, sessions=sessions, empreintes=empreintes,
            journal=journal, a_l_instant=INSTANT,
        )
        suspendre_compte(
            par=_administrateur_distinct(), identifiant="C-1",
            motif="fin de contrat", comptes=comptes, sessions=sessions,
            journal=journal, a_l_instant=INSTANT,
        )
        assert sessions.lire("S-1").revoquee
        assert comptes.lire("C-1").etat == EtatCompte.SUSPENDU

    def test_la_suspension_exige_un_motif_journalise(self, depots):
        comptes, _, _, sessions, journal = depots
        comptes.enregistrer(_compte())
        suspendre_compte(
            par=_administrateur_distinct(), identifiant="C-1",
            motif="départ du 30 avril", comptes=comptes, sessions=sessions,
            journal=journal, a_l_instant=INSTANT,
        )
        assert journal.lister()[-1].motif == "départ du 30 avril"

    def test_retablir_un_compte(self, depots):
        comptes, _, _, _, journal = depots
        comptes.enregistrer(_compte().suspendre())
        assert (
            retablir_compte(
                par=_administrateur_distinct(), identifiant="C-1",
                motif="erreur de saisie", comptes=comptes, journal=journal,
                a_l_instant=INSTANT,
            ).etat
            == EtatCompte.ACTIF
        )

    def test_fermer_une_habilitation_la_conserve(self, depots):
        _, habilitations, _, _, journal = depots
        habilitations.enregistrer(_hab())
        fermer_habilitation(
            par=_administrateur_distinct(), identifiant="H-1", le=date(2026, 4, 30),
            motif=MotifHabilitation.DEPART, habilitations=habilitations,
            journal=journal, a_l_instant=INSTANT,
        )
        assert habilitations.lire("H-1").fin == date(2026, 4, 30)

    def test_toute_administration_laisse_une_trace(self, depots):
        comptes, habilitations, jetons, _, journal = depots
        inviter_collaborateur(
            par=_acces([Role.ADMINISTRATEUR]),
            identifiant_compte="C-9", identifiant_habilitation="H-9",
            identifiant_jeton="J-9", courriel="n.mbarga@cga-brcg.cm",
            nom="MBARGA", prenom="Nadine", role=Role.COMPTABLE,
            portee=frozenset({"M08"}), depuis=AUJOURD_HUI,
            comptes=comptes, habilitations=habilitations, jetons=jetons,
            journal=journal, a_l_instant=INSTANT,
        )
        actions = [e.action for e in journal.lister()]
        assert actions == ["compte.cree", "habilitation.accordee", "jeton.emis"]
        verifier_chaine(journal.lister())


# ── Les dépôts ───────────────────────────────────────────────────────────────


class TestNotifications:
    def test_l_envoi_est_retenu_et_non_emis(self):
        """Rien ne part pendant une exécution de tests, et rien ne se perd."""
        notifications = ServiceNotificationMemoire()
        assert notifications.envoyer(
            "compte.activation", destinataire="a@b.cm", contexte={"secret": "abc"}
        )
        retenu = notifications.derniers("compte.activation")[0]
        assert retenu.destinataire == "a@b.cm"
        assert retenu.contexte["secret"] == "abc"

    def test_le_port_a_la_forme_de_send_template(self):
        """Dessiné sur la signature du module `mail+paiement/mail/` : le
        branchement sera une substitution d'adaptateur, pas une réécriture."""
        from app.contextes.transverse.api import CODES_MESSAGES

        assert "compte.activation" in CODES_MESSAGES
        assert "compte.reinitialisation" in CODES_MESSAGES


class TestDepots:
    def test_deux_comptes_ne_partagent_pas_une_adresse(self):
        comptes = DepotComptesMemoire()
        comptes.enregistrer(_compte("C-1", "meme@cga-brcg.cm"))
        with pytest.raises(CourrielEnDouble):
            comptes.enregistrer(_compte("C-2", "meme@cga-brcg.cm"))

    def test_reenregistrer_le_meme_compte_est_permis(self):
        comptes = DepotComptesMemoire()
        comptes.enregistrer(_compte("C-1", "meme@cga-brcg.cm"))
        comptes.enregistrer(_compte("C-1", "meme@cga-brcg.cm").suspendre())
        assert comptes.lire("C-1").etat == EtatCompte.SUSPENDU

    def test_un_compte_d_un_autre_locataire_est_refuse(self):
        comptes = DepotComptesMemoire(locataire="AUTRE-CGA")
        with pytest.raises(ValueError, match="cloisonnement"):
            comptes.enregistrer(_compte())

    def test_pour_compte_rend_aussi_les_habilitations_fermees(self):
        """Filtrer les actives priverait de l'historique, seul capable de dire
        qui était habilité le 12 mars."""
        habilitations = DepotHabilitationsMemoire()
        habilitations.enregistrer(_hab(fin=date(2021, 1, 1)))
        assert len(habilitations.pour_compte("C-1")) == 1

    def test_pour_dossier_inclut_les_transverses(self):
        habilitations = DepotHabilitationsMemoire()
        habilitations.enregistrer(_hab(role=Role.REVISEUR, portee=None, identifiant="H-1"))
        habilitations.enregistrer(
            _hab(portee={"M08"}, identifiant="H-2", compte="C-2")
        )
        assert {h.identifiant for h in habilitations.pour_dossier("M08")} == {"H-1", "H-2"}

    def test_le_journal_est_indexable_par_objet(self):
        journal = JournalAuditMemoire()
        journal.ajouter(
            horodatage=INSTANT, acteur="C-1", action="a", objet_type="compte", objet_id="C-9"
        )
        journal.ajouter(
            horodatage=INSTANT, acteur="C-1", action="b", objet_type="session", objet_id="S-1"
        )
        assert len(journal.lister(objet_type="compte")) == 1
        assert len(journal.lister(acteur="C-1")) == 2


# ── Les données de démonstration ─────────────────────────────────────────────


class TestDonneesDemo:
    def test_les_niu_correspondent_au_portefeuille_du_contexte_b(self):
        """K ne peut pas importer B — il appartient au socle. Les NIU y sont donc
        recopiés, et c'est ce test, qui a le droit d'importer les deux, qui
        garantit qu'ils ne dérivent pas."""
        assert set(NIU_DEMO.values()) == set(PORTEFEUILLE_DEMO)

    def test_les_habilitations_ne_visent_que_des_comptes_existants(self):
        identifiants = {c.identifiant for c in COMPTES_DEMO}
        assert all(h.compte in identifiants for h in HABILITATIONS_DEMO)

    def test_les_deux_comptables_ont_des_portefeuilles_disjoints(self):
        """Mêmes permissions, dossiers différents : le cloisonnement se démontre
        en deux lectures."""
        _, habilitations, _, _, _ = depots_demo()
        premier = dossiers_accessibles(habilitations.pour_compte("C-004"), AUJOURD_HUI)
        second = dossiers_accessibles(habilitations.pour_compte("C-005"), AUJOURD_HUI)
        assert premier and second
        assert not (premier & second)
        assert permissions_de(frozenset({Role.COMPTABLE})) == permissions_de(
            frozenset({Role.COMPTABLE})
        )

    def test_la_clinique_n_est_affectee_a_aucun_comptable(self):
        """Anomalie délibérée : c'est ce que l'écran d'administration doit
        remonter, et il faut qu'il ait de quoi le faire."""
        _, habilitations, _, _, _ = depots_demo()
        comptables = [
            h
            for h in habilitations.pour_dossier(NIU_DEMO["CLINIQUE"])
            if h.role == Role.COMPTABLE and h.couvre(AUJOURD_HUI)
        ]
        assert comptables == []

    def test_le_comptable_parti_avait_bien_ce_dossier_avant_son_depart(self):
        _, habilitations, _, _, _ = depots_demo()
        siennes = habilitations.pour_compte("C-008")
        assert roles_au(siennes, date(2026, 3, 1)) == frozenset({Role.COMPTABLE})
        assert roles_au(siennes, date(2026, 6, 1)) == frozenset()

    def test_un_adherent_de_demonstration_n_a_jamais_defini_son_mot_de_passe(self):
        comptes, _, _, _, _ = depots_demo()
        en_attente = [
            c for c in comptes.lister() if c.etat == EtatCompte.EN_ATTENTE_ACTIVATION
        ]
        assert [c.identifiant for c in en_attente] == ["A-003"]

    def test_chaque_adherent_ne_voit_que_son_dossier(self):
        _, habilitations, _, _, _ = depots_demo()
        for compte in ("A-001", "A-002", "A-003"):
            dossiers = dossiers_accessibles(habilitations.pour_compte(compte), AUJOURD_HUI)
            assert dossiers is not None and len(dossiers) == 1

    def test_le_journal_de_demonstration_est_vierge(self):
        """Fabriquer un historique d'audit produirait une chaîne qui n'atteste
        de rien."""
        *_, journal = depots_demo()
        assert len(journal) == 0

    def test_le_mot_de_passe_de_demonstration_respecte_la_politique(self):
        controler_mot_de_passe(MOT_DE_PASSE_DEMO, COMPTES_DEMO[0])


# ── L'API ────────────────────────────────────────────────────────────────────


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Chaque test repart d'un atelier neuf : en mémoire, l'atelier **est** la
    persistance, et un test qui suspend un compte le suspendrait pour les
    suivants.

    ⚠️ L'horloge est **figée à `INSTANT`** pour la durée du test. Les routes
    lisent `maintenant()` ; sans cela, elles dateraient leurs écritures du jour
    réel tandis que les assertions raisonnent sur `AUJOURD_HUI`. Cet écart n'a
    aucun effet le jour où le fichier est écrit, et fait échouer la suite le
    lendemain à minuit — c'est arrivé.
    """
    reinitialiser_atelier()
    with horloge_figee(INSTANT):
        yield TestClient(creer_application())


def _connecter(client: TestClient, courriel: str) -> None:
    reponse = client.post(
        "/transverse/session",
        json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO},
    )
    assert reponse.status_code == 200, reponse.text


class TestApi:
    def test_les_roles_sont_publics(self, client: TestClient):
        reponse = client.get("/transverse/roles")
        assert reponse.status_code == 200
        fiches = {f["role"]: f for f in reponse.json()}
        assert "VALIDER_ECRITURE" not in fiches["ADMINISTRATEUR"]["permissions"]
        assert fiches["ADHERENT"]["portee_obligatoire"] is True

    def test_sans_session_tout_est_401(self, client: TestClient):
        assert client.get("/transverse/moi").status_code == 401
        assert client.get("/transverse/audit").status_code == 401

    def test_une_connexion_valide_pose_le_temoin(self, client: TestClient):
        reponse = client.post(
            "/transverse/session",
            json={"courriel": "s.onana@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        assert reponse.status_code == 200
        assert "cga_session" in reponse.cookies
        corps = reponse.json()
        assert corps["roles"] == ["ADMINISTRATEUR"]
        assert corps["interne"] is True
        assert corps["dossiers"] is None
        assert corps["transverse"] is True

    def test_le_mot_de_passe_faux_rend_401_sans_rien_dire(self, client: TestClient):
        connu = client.post(
            "/transverse/session",
            json={"courriel": "s.onana@cga-brcg.cm", "mot_de_passe": "faux mot de passe"},
        )
        inconnu = client.post(
            "/transverse/session",
            json={"courriel": "personne@nulle-part.cm", "mot_de_passe": "faux mot de passe"},
        )
        assert connu.status_code == inconnu.status_code == 401
        assert connu.json()["detail"] == inconnu.json()["detail"]

    def test_un_compte_jamais_active_ne_se_connecte_pas(self, client: TestClient):
        reponse = client.post(
            "/transverse/session",
            json={"courriel": "e.tchoumba@tchoumbaetfils.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        assert reponse.status_code == 401

    def test_un_adherent_ne_voit_que_son_dossier(self, client: TestClient):
        _connecter(client, "jp.nkoa@batimentplus.cm")
        corps = client.get("/transverse/moi").json()
        assert corps["roles"] == ["ADHERENT"]
        assert corps["interne"] is False
        assert corps["dossiers"] == [NIU_DEMO["BATIMENT"]]
        assert "LIRE_COMPTABILITE" not in corps["permissions"]

    def test_un_comptable_ne_lit_pas_le_dossier_d_un_collegue(self, client: TestClient):
        _connecter(client, "l.fotso@cga-brcg.cm")
        sien = client.get(
            f"/transverse/dossiers/{NIU_DEMO['BATIMENT']}/acces",
            params={"a_la_date": "2026-08-15"},
        )
        autre = client.get(
            f"/transverse/dossiers/{NIU_DEMO['TCHOUMBA']}/acces",
            params={"a_la_date": "2026-08-15"},
        )
        assert sien.status_code == 200
        assert autre.status_code == 403

    def test_un_comptable_ne_liste_pas_les_comptes(self, client: TestClient):
        _connecter(client, "l.fotso@cga-brcg.cm")
        reponse = client.get("/transverse/comptes", params={"a_la_date": "2026-08-15"})
        assert reponse.status_code == 403
        assert "GERER_COMPTES" in reponse.json()["detail"]

    def test_l_administrateur_liste_les_comptes_avec_leurs_roles(self, client: TestClient):
        _connecter(client, "s.onana@cga-brcg.cm")
        lignes = client.get(
            "/transverse/comptes", params={"a_la_date": "2026-08-15"}
        ).json()
        parti = next(x for x in lignes if x["compte"]["identifiant"] == "C-008")
        assert parti["roles"] == []
        assert parti["compte"]["etat"] == "SUSPENDU"

    def test_le_parti_avait_des_roles_avant_son_depart(self, client: TestClient):
        _connecter(client, "s.onana@cga-brcg.cm")
        lignes = client.get(
            "/transverse/comptes", params={"a_la_date": "2026-03-01"}
        ).json()
        parti = next(x for x in lignes if x["compte"]["identifiant"] == "C-008")
        assert parti["roles"] == ["COMPTABLE"]

    def test_inviter_puis_activer_puis_se_connecter(self, client: TestClient):
        """Le parcours complet d'une prise de poste, en quatre requêtes."""
        _connecter(client, "s.onana@cga-brcg.cm")
        invitation = client.post(
            "/transverse/comptes/invitation",
            json={
                "courriel": "n.mbarga@cga-brcg.cm",
                "nom": "MBARGA",
                "prenom": "Nadine",
                "role": "COMPTABLE",
                "portee": [NIU_DEMO["CLINIQUE"]],
                "depuis": "2026-01-05",
            },
        )
        assert invitation.status_code == 201, invitation.text
        # ⚠️ Pas 69 : le lien n'est plus rendu à l'administrateur, il part au collaborateur.
        assert "lien_provisoire" not in invitation.json()
        assert secret_de_l_invitation() not in invitation.text
        secret = secret_de_l_invitation()
        assert invitation.json()["compte"]["etat"] == "EN_ATTENTE_ACTIVATION"

        # Trop court : le lien survit.
        refus = client.post(
            "/transverse/mot-de-passe/definition",
            json={"secret": secret, "mot_de_passe": "Court1!"},
        )
        assert refus.status_code == 400
        assert "caractères au minimum" in refus.json()["detail"]

        assert (
            client.post(
                "/transverse/mot-de-passe/definition",
                json={"secret": secret, "mot_de_passe": "chemise bleue mardi tarif"},
            ).status_code
            == 200
        )
        # Le lien ne sert pas deux fois.
        assert (
            client.post(
                "/transverse/mot-de-passe/definition",
                json={"secret": secret, "mot_de_passe": "autre phrase de passe ici"},
            ).status_code
            == 410
        )

        client.delete("/transverse/session")
        connexion = client.post(
            "/transverse/session",
            json={
                "courriel": "n.mbarga@cga-brcg.cm",
                "mot_de_passe": "chemise bleue mardi tarif",
            },
        )
        assert connexion.status_code == 200
        assert connexion.json()["dossiers"] == [NIU_DEMO["CLINIQUE"]]

    def test_l_invitation_prepare_un_courriel(self, client: TestClient):
        _connecter(client, "s.onana@cga-brcg.cm")
        client.post(
            "/transverse/comptes/invitation",
            json={
                "courriel": "n.mbarga@cga-brcg.cm",
                "nom": "MBARGA",
                "prenom": "Nadine",
                "role": "COMPTABLE",
                "portee": [NIU_DEMO["CLINIQUE"]],
                "depuis": "2026-01-05",
            },
        )
        message = atelier().notifications.derniers("compte.invitation")[0]
        assert message.destinataire == "n.mbarga@cga-brcg.cm"
        assert message.contexte["prenom"] == "Nadine"

    def test_l_adresse_deja_prise_rend_409(self, client: TestClient):
        _connecter(client, "s.onana@cga-brcg.cm")
        reponse = client.post(
            "/transverse/comptes/invitation",
            json={
                "courriel": "l.fotso@cga-brcg.cm",
                "nom": "X",
                "prenom": "Y",
                "role": "COMPTABLE",
                "portee": [NIU_DEMO["CLINIQUE"]],
                "depuis": "2026-09-01",
            },
        )
        assert reponse.status_code == 409

    def test_inviter_un_adherent_sans_portee_rend_422(self, client: TestClient):
        _connecter(client, "s.onana@cga-brcg.cm")
        reponse = client.post(
            "/transverse/comptes/invitation",
            json={
                "courriel": "nouveau@entreprise.cm",
                "nom": "X",
                "prenom": "Y",
                "role": "ADHERENT",
                "portee": None,
                "depuis": "2026-09-01",
            },
        )
        assert reponse.status_code == 422
        assert "portée explicite" in reponse.json()["detail"]

    def test_l_oubli_rend_202_meme_sur_une_adresse_inconnue(self, client: TestClient):
        connue = client.post(
            "/transverse/mot-de-passe/oubli", json={"courriel": "l.fotso@cga-brcg.cm"}
        )
        inconnue = client.post(
            "/transverse/mot-de-passe/oubli", json={"courriel": "personne@nulle-part.cm"}
        )
        assert connue.status_code == inconnue.status_code == 202
        assert connue.json() == inconnue.json()

    def test_suspendre_coupe_la_session_en_cours(self, client: TestClient):
        """La démonstration de ce qu'un jeton autoportant ne permettrait pas."""
        autre = TestClient(client.app)
        autre.post(
            "/transverse/session",
            json={"courriel": "l.fotso@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        assert autre.get("/transverse/moi").status_code == 200

        _connecter(client, "s.onana@cga-brcg.cm")
        assert (
            client.post(
                "/transverse/comptes/C-004/suspension",
                json={"motif": "départ constaté ce matin"},
            ).status_code
            == 200
        )
        assert autre.get("/transverse/moi").status_code == 401

    def test_la_suspension_previent_le_titulaire_et_ne_se_retourne_pas_contre_soi(
        self, client: TestClient
    ):
        """⚠️ Pas 69 : le gabarit « compte suspendu » n'était jamais envoyé, et un
        administrateur pouvait suspendre son propre compte, laissant le cabinet sans
        personne pour gérer les comptes."""
        from app.contextes.transverse.adaptateurs.entrant.dependances import (
            service_de_notification,
        )

        _connecter(client, "s.onana@cga-brcg.cm")  # C-002, administrateur
        soi = client.post("/transverse/comptes/C-002/suspension", json={"motif": "essai"})
        assert soi.status_code == 409, soi.text
        assert client.get("/transverse/moi").status_code == 200, "la session tient"

        service_de_notification().vider()
        assert client.post(
            "/transverse/comptes/C-004/suspension", json={"motif": "départ constaté ce matin"}
        ).status_code == 200
        (message,) = service_de_notification().derniers("compte.suspendu")
        assert message.destinataire == "l.fotso@cga-brcg.cm"
        assert "départ" not in str(message.contexte), "le motif reste au journal"

    def test_affecter_le_dossier_orphelin(self, client: TestClient):
        _connecter(client, "b.mballa@cga-brcg.cm")  # direction
        reponse = client.post(
            f"/transverse/habilitations/H-004/dossiers/{NIU_DEMO['CLINIQUE']}"
        )
        assert reponse.status_code == 200
        assert NIU_DEMO["CLINIQUE"] in reponse.json()["portee"]

    def test_l_administrateur_affecte_aussi_les_dossiers(self, client: TestClient):
        """La direction décide de la répartition, l'administration l'exécute :
        les deux détiennent AFFECTER_DOSSIER. Ce qui est refusé au comptable et au
        réviseur, ce n'est pas la même chose que ce qui distingue ces deux-là."""
        _connecter(client, "s.onana@cga-brcg.cm")
        assert (
            client.post(
                f"/transverse/habilitations/H-004/dossiers/{NIU_DEMO['CLINIQUE']}"
            ).status_code
            == 200
        )

    def test_ni_le_comptable_ni_le_reviseur_n_affectent_un_dossier(
        self, client: TestClient
    ):
        """Un comptable qui pourrait s'ajouter un dossier n'aurait plus de
        périmètre du tout."""
        _connecter(client, "l.fotso@cga-brcg.cm")
        assert (
            client.post(
                f"/transverse/habilitations/H-004/dossiers/{NIU_DEMO['CLINIQUE']}"
            ).status_code
            == 403
        )
        client.delete("/transverse/session")
        _connecter(client, "a.bouba@cga-brcg.cm")  # réviseur
        assert (
            client.post(
                f"/transverse/habilitations/H-004/dossiers/{NIU_DEMO['CLINIQUE']}"
            ).status_code
            == 403
        )

    def test_une_habilitation_a_venir_ne_donne_rien_aujourd_hui(self, client: TestClient):
        """Un contrat signé pour septembre n'ouvre pas les dossiers en août."""
        _connecter(client, "s.onana@cga-brcg.cm")
        client.post(
            "/transverse/comptes/invitation",
            json={
                "courriel": "futur@cga-brcg.cm",
                "nom": "ABEGA",
                "prenom": "Paul",
                "role": "COMPTABLE",
                "portee": [NIU_DEMO["CLINIQUE"]],
                "depuis": "2099-01-01",
            },
        )
        secret = secret_de_l_invitation()
        client.post(
            "/transverse/mot-de-passe/definition",
            json={"secret": secret, "mot_de_passe": "chemise bleue mardi tarif"},
        )
        client.delete("/transverse/session")
        connexion = client.post(
            "/transverse/session",
            json={"courriel": "futur@cga-brcg.cm", "mot_de_passe": "chemise bleue mardi tarif"},
        )
        assert connexion.status_code == 200
        assert connexion.json()["roles"] == []
        assert connexion.json()["dossiers"] == []

    def test_le_journal_enregistre_les_connexions_et_se_verifie(self, client: TestClient):
        _connecter(client, "s.onana@cga-brcg.cm")
        client.post(
            "/transverse/session",
            json={"courriel": "s.onana@cga-brcg.cm", "mot_de_passe": "faux"},
        )
        entrees = client.get("/transverse/audit").json()
        actions = [e["action"] for e in entrees]
        assert "session.ouverte" in actions
        assert "session.refusee" in actions
        verification = client.get("/transverse/audit/verification").json()
        assert verification["intacte"] is True
        assert verification["entrees"] == len(entrees)

    def test_le_comptable_ne_lit_pas_le_journal(self, client: TestClient):
        _connecter(client, "l.fotso@cga-brcg.cm")
        assert client.get("/transverse/audit").status_code == 403

    def test_la_deconnexion_ferme_reellement(self, client: TestClient):
        _connecter(client, "l.fotso@cga-brcg.cm")
        assert client.delete("/transverse/session").status_code == 204
        assert client.get("/transverse/moi").status_code == 401

    def test_le_temoin_n_est_pas_lisible_par_un_script(self, client: TestClient):
        reponse = client.post(
            "/transverse/session",
            json={"courriel": "s.onana@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        entete = reponse.headers["set-cookie"]
        assert "HttpOnly" in entete
        assert "SameSite=lax" in entete

    def test_le_jeton_en_entete_vaut_le_temoin(self, client: TestClient):
        """Pour l'outillage en ligne de commande, et pour rien d'autre."""
        identifiant = client.post(
            "/transverse/session",
            json={"courriel": "s.onana@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
        ).cookies["cga_session"]
        nu = TestClient(client.app)
        assert (
            nu.get(
                "/transverse/moi", headers={"Authorization": f"Bearer {identifiant}"}
            ).status_code
            == 200
        )


# ── Le cloisonnement, appliqué aux routes métier ─────────────────────────────


class TestCloisonnementDesRoutesMetier:
    """Ce que K garantit ne vaut que si B, C, E et F l'appliquent.

    Ces tests sont les plus importants du contexte : sans eux, la table des
    permissions serait une déclaration d'intention. Ils passent par les routes
    réelles, avec de vraies sessions, sur le jeu de démonstration.
    """

    def test_aucune_route_metier_n_est_ouverte(self, client: TestClient):
        """Avant le branchement, ces quatre contextes répondaient à tout le
        monde. Le test le protège désormais."""
        fermees = [
            "/portefeuille/entreprises?a_la_date=2026-08-15",
            f"/portefeuille/entreprises/{NIU_DEMO['BATIMENT']}",
            "/portefeuille/anomalies?a_la_date=2026-08-15",
            "/collecte/pieces?a_la_date=2026-08-15",
            "/collecte/doublons",
            "/collecte/demandes",
            f"/comptabilite/dossiers/{NIU_DEMO['BATIMENT']}/ecritures?exercice=2026",
            "/comptabilite/plan-comptable",
            "/obligations/catalogue?a_la_date=2026-08-15",
        ]
        for chemin in fermees:
            assert client.get(chemin).status_code == 401, chemin

    def _client(self, courriel: str) -> TestClient:
        client = TestClient(creer_application())
        reponse = client.post(
            "/transverse/session",
            json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        assert reponse.status_code == 200, reponse.text
        return client

    def test_un_comptable_ne_voit_que_son_portefeuille(self, client: TestClient):
        """Trois dossiers pour l'un, deux pour l'autre, six au cabinet. Mêmes
        permissions, listes différentes."""
        fotso = self._client("l.fotso@cga-brcg.cm")
        ndongo = self._client("c.ndongo@cga-brcg.cm")
        bouba = self._client("a.bouba@cga-brcg.cm")  # réviseur, transverse

        def niu_vus(c: TestClient) -> set[str]:
            lignes = c.get(
                "/portefeuille/entreprises", params={"a_la_date": "2026-08-15"}
            ).json()
            return {ligne["niu"] for ligne in lignes}

        assert len(niu_vus(fotso)) == 3
        assert len(niu_vus(ndongo)) == 2
        assert not (niu_vus(fotso) & niu_vus(ndongo))
        assert len(niu_vus(bouba)) == 6

    def test_le_dossier_d_un_collegue_rend_404_et_non_403(self, client: TestClient):
        """Un 403 dirait « ce dossier existe, et il ne vous regarde pas ». C'est
        une information, et elle a de la valeur pour un concurrent."""
        fotso = self._client("l.fotso@cga-brcg.cm")
        assert (
            fotso.get(f"/portefeuille/entreprises/{NIU_DEMO['BATIMENT']}").status_code
            == 200
        )
        refus = fotso.get(f"/portefeuille/entreprises/{NIU_DEMO['TCHOUMBA']}")
        assert refus.status_code == 404
        assert "portefeuille" in refus.json()["detail"]

    def test_le_filtre_explicite_est_controle_avant_d_etre_applique(
        self, client: TestClient
    ):
        """Sans ce contrôle, demander le NIU d'un dossier hors portefeuille
        rendrait une liste vide, et l'absence se distinguerait mal du refus."""
        fotso = self._client("l.fotso@cga-brcg.cm")
        reponse = fotso.get(
            "/collecte/pieces",
            params={"a_la_date": "2026-08-15", "entreprise": NIU_DEMO["TCHOUMBA"]},
        )
        assert reponse.status_code == 404

    def test_la_boite_de_reception_est_restreinte(self, client: TestClient):
        """Une liste se restreint, elle ne se refuse pas : un écran vide vaudrait
        mieux qu'une erreur, mais une liste juste vaut mieux que les deux."""
        fotso = self._client("l.fotso@cga-brcg.cm")
        bouba = self._client("a.bouba@cga-brcg.cm")

        def dossiers(c: TestClient) -> set[str]:
            lignes = c.get(
                "/collecte/pieces", params={"a_la_date": "2026-08-15"}
            ).json()
            return {ligne["entreprise"] for ligne in lignes}

        vus = dossiers(fotso)
        assert vus and vus < dossiers(bouba)

    def test_un_adherent_ne_voit_que_son_dossier(self, client: TestClient):
        nkoa = self._client("jp.nkoa@batimentplus.cm")
        lignes = nkoa.get(
            "/portefeuille/entreprises", params={"a_la_date": "2026-08-15"}
        ).json()
        assert [ligne["niu"] for ligne in lignes] == [NIU_DEMO["BATIMENT"]]

    def test_un_adherent_n_accede_pas_a_la_comptabilite(self, client: TestClient):
        """Un solde intermédiaire lu comme définitif conduit à des décisions de
        trésorerie fondées sur un brouillon."""
        nkoa = self._client("jp.nkoa@batimentplus.cm")
        refus = nkoa.get(
            f"/comptabilite/dossiers/{NIU_DEMO['BATIMENT']}/balance",
            params={"exercice": "2026"},
        )
        assert refus.status_code == 403
        assert "LIRE_COMPTABILITE" in refus.json()["detail"]

    def test_un_adherent_ne_lit_pas_les_arbitrages_de_doublons(
        self, client: TestClient
    ):
        nkoa = self._client("jp.nkoa@batimentplus.cm")
        assert nkoa.get("/collecte/doublons").status_code == 403

    def test_seul_le_charge_de_clientele_releve_les_relances(self, client: TestClient):
        moukouri = self._client("p.moukouri@cga-brcg.cm")
        bouba = self._client("a.bouba@cga-brcg.cm")
        parametres = {"a_la_date": "2026-08-15"}
        assert moukouri.get("/collecte/relances", params=parametres).status_code == 200
        assert bouba.get("/collecte/relances", params=parametres).status_code == 403

    def test_un_adherent_depose_bien_ses_propres_pieces(self, client: TestClient):
        """Le canal `DEPOT_CABINET` existe, donc un collaborateur saisit aussi —
        mais l'adhérent reste le premier déposant."""
        from app.contextes.transverse.api import PERMISSIONS_PAR_ROLE

        assert Permission.DEPOSER_PIECE in PERMISSIONS_PAR_ROLE[Role.ADHERENT]
        assert Permission.DEPOSER_PIECE in PERMISSIONS_PAR_ROLE[Role.COMPTABLE]

    def test_le_comptable_parti_n_accede_plus_a_rien(self, client: TestClient):
        """Compte suspendu **et** habilitation fermée : les deux protègent, et
        c'est la première qui refuse en premier."""
        reponse = client.post(
            "/transverse/session",
            json={"courriel": "a.tchinda@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        assert reponse.status_code == 401


# ── L'unité de travail ───────────────────────────────────────────────────────


class TestUniteDeTravail:
    """La couture entre mémoire et PostgreSQL.

    Elle est vérifiée **en mémoire**, parce que c'est le mode en vigueur. Ce
    qu'on prouve ici n'est pas le comportement SQL — `test_persistance.py` s'en
    charge contre une vraie base — mais que le point de bascule existe et qu'il
    refuse ce qu'il doit refuser.
    """

    def test_en_memoire_l_atelier_est_celui_du_processus(self):
        """Deux instances seraient deux bases sans lien."""
        from app.contextes.transverse.api import unite_de_travail

        with unite_de_travail() as premier, unite_de_travail() as second:
            assert premier is second is atelier()

    def test_la_remise_a_zero_repart_d_un_atelier_vierge(self):
        """En mémoire, l'atelier *est* la persistance : sans cela, un test qui
        suspend un compte le suspend pour les suivants."""
        avant = atelier()
        reinitialiser_atelier()
        assert atelier() is not avant

    def test_hors_requete_le_mode_postgresql_leve(self, monkeypatch):
        """Un atelier fabriqué à la volée ouvrirait une transaction que personne
        ne fermerait, et ses verrous sur le journal d'audit bloqueraient toutes
        les écritures suivantes. Mieux vaut une erreur immédiate qu'une
        application qui se fige au bout d'une heure."""
        from app.infrastructure.config import Configuration, configuration

        configuration.cache_clear()
        monkeypatch.setenv("CGA_PERSISTANCE", "postgresql")
        try:
            assert Configuration().persistance == "postgresql"
            with pytest.raises(RuntimeError, match="unité de travail"):
                atelier()
        finally:
            monkeypatch.delenv("CGA_PERSISTANCE", raising=False)
            configuration.cache_clear()
            reinitialiser_atelier()

    def test_l_intergiciel_est_monte(self):
        """Sans lui, aucune requête n'ouvrirait de transaction en mode SQL."""
        from app.contextes.transverse.api import IntergicielUniteDeTravail

        monte = [
            intergiciel
            for intergiciel in creer_application().user_middleware
            if intergiciel.cls is IntergicielUniteDeTravail
        ]
        assert len(monte) == 1

    def test_l_atelier_porte_le_portail_declaratif(self):
        """Le registre des accusés fait partie du socle, au même titre que les
        comptes : c'est ce qui permettra de le migrer d'un bloc."""
        assert atelier().portail.depose_automatiquement() is False


def secret_de_l_invitation() -> str:
    """Le secret d'activation, lu dans le dernier courriel d'invitation retenu (pas 69).

    Comme le collaborateur invité le reçoit : la réponse de l'API ne le porte plus.
    """
    from app.contextes.transverse.adaptateurs.entrant.dependances import service_de_notification

    (dernier, *_) = service_de_notification().derniers("compte.invitation")
    return dernier.contexte["lien"].split("jeton=", 1)[1]
