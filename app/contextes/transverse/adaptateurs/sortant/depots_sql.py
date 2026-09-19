"""Réalisations PostgreSQL des dépôts du contexte K.

─────────────────────────────────────────────────────────────────────────────────
LES DÉPÔTS RENDENT DES ENTITÉS, JAMAIS DES LIGNES

Une méthode qui rendrait un `TableCompte` ferait remonter SQLAlchemy dans la
couche application : un attribut lu après fermeture de session lèverait, une
modification serait écrite sans qu'on l'ait demandé, et le domaine dépendrait de
l'ORM. Les entités Pydantic sont construites explicitement, ligne par ligne.

Le coût est une fonction de traduction par table. Le bénéfice est qu'aucun cas
d'usage ne change quand on change d'ORM — et que les entités restent immuables,
ce dont tout le domaine dépend.

LE CLOISONNEMENT EST POSÉ DEUX FOIS, ET CE N'EST PAS UNE REDONDANCE

**L'écouteur de session** filtre toute requête ORM sans qu'on l'écrive : c'est ce
qui rend l'oubli impossible sur les requêtes à venir. Voir l'en-tête de
`base_de_donnees.py`.

**Chaque requête de ce module porte en outre son filtre en toutes lettres.** Un
test l'a exigé, et il avait raison : construit sur une session ordinaire — un
test, un script de reprise, une tâche de fond —, un dépôt qui ne compterait que
sur l'écouteur rendrait les lignes de tous les locataires. Le premier mécanisme
protège de ce qu'on écrira demain ; le second rend ce module correct **quelle que
soit la session qui le porte**.

⚠️ Un dépôt **ne doit jamais** écrire une entité d'un autre locataire. Les
lectures sont filtrées, les écritures sont contrôlées à l'entrée — comme en
mémoire.

CE QUI CHANGE VRAIMENT PAR RAPPORT À LA MÉMOIRE

Deux garanties, et ce sont celles que les ports réclamaient depuis le début :

**Le journal d'audit est chaîné sous verrou de transaction.** La tête est lue
avec `FOR UPDATE`, ce qui sérialise les ajouts concurrents. Si deux processus s'y
présentent, le second attend le premier et lit la vraie tête. La contrainte
d'unicité sur le rang reste la ceinture.

**Le dépôt d'une déclaration est unique par référence**, garanti par la base.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as SessionSql

from app.contextes.transverse.adaptateurs.sortant.coffre import Coffre, CoffreTransparent
from app.contextes.transverse.adaptateurs.sortant.depots_memoire import (
    CompteIntrouvable,
    CourrielEnDouble,
    HabilitationIntrouvable,
)
from app.contextes.transverse.adaptateurs.sortant.portail_manuel import (
    AccuseIncoherent,
    DepotManuelRequis,
)
from app.contextes.transverse.adaptateurs.sortant.tables import (
    TableAccuseReception,
    TableCompte,
    TableEntreeAudit,
    TableHabilitation,
    TableJeton,
    TableMandat,
    TableSession,
)
from app.contextes.transverse.domaine.audit import EntreeAudit
from app.contextes.transverse.domaine.habilitations import Habilitation
from app.contextes.transverse.domaine.identites import Compte
from app.contextes.transverse.domaine.jetons import Jeton
from app.contextes.transverse.domaine.mandats import Mandat, MotifMandat
from app.contextes.transverse.domaine.roles import Role
from app.contextes.transverse.domaine.sessions import Session
from app.contextes.transverse.domaine.teledeclaration import (
    AccuseReception,
    DocumentATransmettre,
    ModeDepot,
    Portail,
)
from app.partage.locataire import sous_mandat

__all__ = [
    "DepotComptesSql",
    "DepotHabilitationsSql",
    "DepotJetonsSql",
    "DepotSessionsSql",
    "JournalAuditSql",
    "PortailSql",
]


# ── Traductions ──────────────────────────────────────────────────────────────


def _vers_compte(ligne: TableCompte, coffre: Coffre) -> Compte:
    return Compte(
        identifiant=ligne.identifiant,
        courriel=ligne.courriel,
        nom=ligne.nom,
        prenom=ligne.prenom,
        locataire=ligne.locataire,
        telephone=ligne.telephone,
        empreinte_mot_de_passe=ligne.empreinte_mot_de_passe,
        secret_totp=coffre.ouvrir(ligne.secret_totp),
        etat=ligne.etat,
        cree_le=ligne.cree_le,
        tentatives_echouees=ligne.tentatives_echouees,
        verrouille_jusqu_a=ligne.verrouille_jusqu_a,
        derniere_connexion=ligne.derniere_connexion,
    )


def _vers_habilitation(ligne: TableHabilitation) -> Habilitation:
    return Habilitation(
        identifiant=ligne.identifiant,
        compte=ligne.compte,
        role=ligne.role,
        portee=None if ligne.portee is None else frozenset(ligne.portee),
        debut=ligne.debut,
        fin=ligne.fin,
        motif=ligne.motif,
        accordee_par=ligne.accordee_par,
        precision=ligne.precision,
    )


def _vers_jeton(ligne: TableJeton) -> Jeton:
    return Jeton(
        identifiant=ligne.identifiant,
        empreinte=ligne.empreinte,
        compte=ligne.compte,
        type=ligne.type,
        emis_le=ligne.emis_le,
        expire_le=ligne.expire_le,
        consomme_le=ligne.consomme_le,
        emis_par=ligne.emis_par,
    )


def _vers_session(ligne: TableSession) -> Session:
    return Session(
        identifiant=ligne.identifiant,
        compte=ligne.compte,
        locataire=ligne.locataire,
        ouverte_le=ligne.ouverte_le,
        expire_le=ligne.expire_le,
        revoquee_le=ligne.revoquee_le,
        motif_revocation=ligne.motif_revocation,
        adresse_ip=ligne.adresse_ip,
        agent=ligne.agent,
        renforcee_jusqu_a=ligne.renforcee_jusqu_a,
    )


def _vers_entree(ligne: TableEntreeAudit) -> EntreeAudit:
    return EntreeAudit(
        rang=ligne.rang,
        horodatage=ligne.horodatage,
        acteur=ligne.acteur,
        locataire=ligne.locataire,
        action=ligne.action,
        objet_type=ligne.objet_type,
        objet_id=ligne.objet_id,
        avant=ligne.avant,
        apres=ligne.apres,
        motif=ligne.motif,
        adresse_ip=ligne.adresse_ip,
        mandat=ligne.mandat,
        empreinte_precedente=ligne.empreinte_precedente,
        empreinte=ligne.empreinte,
    )


def _vers_accuse(ligne: TableAccuseReception) -> AccuseReception:
    return AccuseReception(
        numero=ligne.numero,
        portail=ligne.portail,
        reference_document=ligne.reference_document,
        depose_le=ligne.depose_le,
        mode=ligne.mode,
        empreinte_deposee=ligne.empreinte_deposee,
        depose_par=ligne.depose_par,
        montant_constate=ligne.montant_constate,
        piece_jointe=ligne.piece_jointe,
        precision=ligne.precision,
    )


# ── Les dépôts ───────────────────────────────────────────────────────────────


class DepotComptesSql:
    """Réalisation de `DepotComptes`.

    ⚠️ Le **seul** dépôt à porter un coffre, et c'est voulu : le secret TOTP est
    la seule donnée du système qui doive être à la fois secrète et relisible.
    Les mots de passe sont hachés — irréversibles, donc mieux protégés ; tout le
    reste est de la donnée métier, dont la confidentialité tient au cloisonnement
    et non au chiffrement. Voir `coffre.py`.
    """

    def __init__(
        self, session: SessionSql, locataire: str, coffre: Coffre | None = None
    ) -> None:
        self._session = session
        self.locataire = locataire
        # Le défaut transparent garde les tests et le mode mémoire simples. La
        # production ne peut pas démarrer sans clé — `Configuration` l'interdit.
        self._coffre = coffre or CoffreTransparent()

    def lire(self, identifiant: str) -> Compte:
        ligne = self._session.get(TableCompte, identifiant)
        # `get` court-circuite le filtre de cloisonnement — il interroge par clé
        # primaire, sans passer par `do_orm_execute`. On vérifie donc à la main :
        # c'est la seule lecture du contexte qui l'exige, et elle est signalée.
        if ligne is None or ligne.locataire != self.locataire:
            raise CompteIntrouvable(
                f"aucun compte {identifiant} chez le locataire {self.locataire}"
            )
        return _vers_compte(ligne, self._coffre)

    def par_courriel(self, courriel: str) -> Compte | None:
        ligne = self._session.scalars(
            select(TableCompte).where(
                TableCompte.locataire == self.locataire,
                TableCompte.courriel == courriel.strip().lower(),
            )
        ).first()
        return None if ligne is None else _vers_compte(ligne, self._coffre)

    def enregistrer(self, compte: Compte) -> None:
        if compte.locataire != self.locataire:
            raise ValueError(
                f"compte {compte.identifiant} du locataire {compte.locataire} enregistré "
                f"dans le dépôt de {self.locataire}. Le filtre de session protège les "
                "lectures ; les écritures se contrôlent à l'entrée."
            )
        ligne = self._session.get(TableCompte, compte.identifiant)
        if ligne is None:
            ligne = TableCompte(identifiant=compte.identifiant)
            self._session.add(ligne)
        ligne.locataire = compte.locataire
        ligne.courriel = compte.courriel
        ligne.nom = compte.nom
        ligne.prenom = compte.prenom
        ligne.telephone = compte.telephone
        ligne.empreinte_mot_de_passe = compte.empreinte_mot_de_passe
        ligne.secret_totp = self._coffre.sceller(compte.secret_totp)
        ligne.etat = compte.etat.value
        ligne.cree_le = compte.cree_le
        ligne.tentatives_echouees = compte.tentatives_echouees
        ligne.verrouille_jusqu_a = compte.verrouille_jusqu_a
        ligne.derniere_connexion = compte.derniere_connexion
        try:
            self._session.flush()
        except IntegrityError as conflit:
            self._session.rollback()
            raise CourrielEnDouble(
                f"l'adresse {compte.courriel} est déjà celle d'un autre compte"
            ) from conflit

    def lister(self) -> list[Compte]:
        lignes = self._session.scalars(
            select(TableCompte)
            .where(TableCompte.locataire == self.locataire)
            .order_by(TableCompte.nom, TableCompte.prenom)
        ).all()
        return [_vers_compte(ligne, self._coffre) for ligne in lignes]


class DepotHabilitationsSql:
    """Réalisation de `DepotHabilitations`. Aucune suppression — voir `tables.py`."""

    def __init__(self, session: SessionSql, locataire: str) -> None:
        self._session = session
        self.locataire = locataire

    def lire(self, identifiant: str) -> Habilitation:
        ligne = self._session.get(TableHabilitation, identifiant)
        if ligne is None or ligne.locataire != self.locataire:
            raise HabilitationIntrouvable(f"aucune habilitation {identifiant}")
        return _vers_habilitation(ligne)

    def pour_compte(self, compte: str) -> list[Habilitation]:
        """**Toutes**, fermées comprises : c'est l'historique qui répond à « qui
        était habilité le 12 mars »."""
        lignes = self._session.scalars(
            select(TableHabilitation)
            .where(
                TableHabilitation.locataire == self.locataire,
                TableHabilitation.compte == compte,
            )
            .order_by(TableHabilitation.debut, TableHabilitation.role)
        ).all()
        return [_vers_habilitation(ligne) for ligne in lignes]

    def pour_dossier(self, niu: str) -> list[Habilitation]:
        """Inclut les transverses — portée nulle — qui couvrent ce dossier aussi.

        Le filtre sur la portée se fait en Python : elle est stockée en JSON, et
        une requête sur un tableau JSON coûterait un index GIN et une syntaxe
        que personne ne relit, pour un écran consulté quelques fois par jour.
        """
        lignes = self._session.scalars(
            select(TableHabilitation)
            .where(TableHabilitation.locataire == self.locataire)
            .order_by(TableHabilitation.compte, TableHabilitation.debut)
        ).all()
        return [
            _vers_habilitation(ligne)
            for ligne in lignes
            if ligne.portee is None or niu in ligne.portee
        ]

    def enregistrer(self, habilitation: Habilitation) -> None:
        ligne = self._session.get(TableHabilitation, habilitation.identifiant)
        if ligne is None:
            ligne = TableHabilitation(identifiant=habilitation.identifiant)
            self._session.add(ligne)
        ligne.locataire = self.locataire
        ligne.compte = habilitation.compte
        ligne.role = habilitation.role.value
        ligne.portee = None if habilitation.portee is None else sorted(habilitation.portee)
        ligne.debut = habilitation.debut
        ligne.fin = habilitation.fin
        ligne.motif = habilitation.motif.value
        ligne.accordee_par = habilitation.accordee_par
        ligne.precision = habilitation.precision
        self._session.flush()

    def lister(self) -> list[Habilitation]:
        lignes = self._session.scalars(
            select(TableHabilitation)
            .where(TableHabilitation.locataire == self.locataire)
            .order_by(TableHabilitation.compte, TableHabilitation.debut)
        ).all()
        return [_vers_habilitation(ligne) for ligne in lignes]


class DepotJetonsSql:
    """Réalisation de `DepotJetons`. Indexé par empreinte — le secret n'est pas stocké."""

    def __init__(self, session: SessionSql, locataire: str) -> None:
        self._session = session
        self.locataire = locataire

    def par_empreinte(self, empreinte: str) -> Jeton | None:
        ligne = self._session.scalars(
            select(TableJeton).where(
                TableJeton.locataire == self.locataire,
                TableJeton.empreinte == empreinte,
            )
        ).first()
        return None if ligne is None else _vers_jeton(ligne)

    def pour_compte(self, compte: str) -> list[Jeton]:
        lignes = self._session.scalars(
            select(TableJeton)
            .where(
                TableJeton.locataire == self.locataire,
                TableJeton.compte == compte,
            )
            .order_by(TableJeton.emis_le.desc())
        ).all()
        return [_vers_jeton(ligne) for ligne in lignes]

    def enregistrer(self, jeton: Jeton) -> None:
        ligne = self._session.get(TableJeton, jeton.identifiant)
        if ligne is None:
            ligne = TableJeton(identifiant=jeton.identifiant)
            self._session.add(ligne)
        ligne.locataire = self.locataire
        ligne.empreinte = jeton.empreinte
        ligne.compte = jeton.compte
        ligne.type = jeton.type.value
        ligne.emis_le = jeton.emis_le
        ligne.expire_le = jeton.expire_le
        ligne.consomme_le = jeton.consomme_le
        ligne.emis_par = jeton.emis_par
        self._session.flush()


class DepotSessionsSql:
    """Réalisation de `DepotSessions`."""

    def __init__(self, session: SessionSql, locataire: str) -> None:
        self._session = session
        self.locataire = locataire

    def lire(self, identifiant: str) -> Session | None:
        ligne = self._session.get(TableSession, identifiant)
        if ligne is None or ligne.locataire != self.locataire:
            return None
        return _vers_session(ligne)

    def pour_compte(self, compte: str) -> list[Session]:
        lignes = self._session.scalars(
            select(TableSession)
            .where(
                TableSession.locataire == self.locataire,
                TableSession.compte == compte,
            )
            .order_by(TableSession.ouverte_le.desc())
        ).all()
        return [_vers_session(ligne) for ligne in lignes]

    def enregistrer(self, session: Session) -> None:
        ligne = self._session.get(TableSession, session.identifiant)
        if ligne is None:
            ligne = TableSession(identifiant=session.identifiant)
            self._session.add(ligne)
        ligne.locataire = session.locataire
        ligne.compte = session.compte
        ligne.ouverte_le = session.ouverte_le
        ligne.expire_le = session.expire_le
        ligne.revoquee_le = session.revoquee_le
        ligne.motif_revocation = (
            session.motif_revocation.value if session.motif_revocation else None
        )
        ligne.adresse_ip = session.adresse_ip
        ligne.agent = session.agent
        ligne.renforcee_jusqu_a = session.renforcee_jusqu_a
        self._session.flush()


class JournalAuditSql:
    """Réalisation de `JournalAudit`. Append-only, chaîné, sérialisé par locataire.

    ─────────────────────────────────────────────────────────────────────────
    ⚠️ `SELECT … FOR UPDATE` NE SUFFISAIT PAS, ET LE COMMENTAIRE D'ORIGINE
    AFFIRMAIT LE CONTRAIRE

    La version précédente lisait la tête avec `with_for_update()` en expliquant
    que « deux transactions concurrentes ne peuvent pas lire le même rang
    maximal ». **C'était faux**, et huit souscriptions simultanées l'ont montré :
    cinq sur huit en `500`, sur `duplicate key … (locataire, rang)=(CGA-BRCG, 40)`.

    Pourquoi : `FOR UPDATE` verrouille **les lignes que la requête a lues**. Il
    ne dit rien des lignes qui n'existent pas encore. Le déroulé est le suivant :

      · T1 lit la ligne de rang 39 et la verrouille ;
      · T2 veut la même ligne et attend ;
      · T1 insère le rang 40 et valide, libérant le verrou ;
      · T2 repart — et `FOR UPDATE` ne lui fait **re-vérifier que la ligne 39**,
        qui existe toujours. Elle ne redécouvre pas la ligne 40.

    T2 calcule donc 39 + 1 = 40, et heurte la contrainte d'unicité. C'est le
    problème classique du fantôme : un verrou de ligne ne protège pas d'une
    insertion.

    LA RÉPONSE : UN VERROU CONSULTATIF, PAR LOCATAIRE

    `pg_advisory_xact_lock` ne porte sur aucune ligne — donc les insertions ne
    lui échappent pas. Il est pris pour la durée de la **transaction** et libéré
    à la validation comme à l'annulation : rien à relâcher à la main, rien à
    oublier dans une branche d'erreur.

    Sérialiser est le bon comportement, et non un pis-aller : **une chaîne de
    hachage est séquentielle par nature.** Chaque entrée porte l'empreinte de la
    précédente ; on ne peut pas en ajouter deux à la fois, quelle que soit
    l'astuce. Le verrou ne fait qu'énoncer cette contrainte au lieu de la
    heurter.

    ⚠️ Le verrou est **par locataire** : deux cabinets n'attendent pas l'un pour
    l'autre. C'est ce qui rend le coût acceptable — un cabinet écrit quelques
    entrées par seconde au plus.

    La contrainte d'unicité sur `(locataire, rang)` reste la ceinture. Elle a
    d'ailleurs prouvé sa valeur : le jour où le verrou était insuffisant, elle a
    fait échouer l'écriture au lieu de laisser la chaîne se corrompre en silence.
    ─────────────────────────────────────────────────────────────────────────
    """

    def __init__(self, session: SessionSql, locataire: str) -> None:
        self._session = session
        self.locataire = locataire

    def _serialiser_les_ajouts(self) -> None:
        """Prend le verrou du locataire jusqu'à la fin de la transaction.

        `hashtext` réduit l'identifiant à un entier, ce qui est exactement ce
        qu'attend `pg_advisory_xact_lock`. Une collision de hachage entre deux
        locataires ferait attendre l'un pour l'autre — jamais écrire à tort.
        """
        self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:cle))"),
            {"cle": f"audit:{self.locataire}"},
        )

    def _tete(self) -> TableEntreeAudit | None:
        """La dernière entrée. À n'appeler qu'après `_serialiser_les_ajouts`."""
        return self._session.scalars(
            select(TableEntreeAudit)
            .where(TableEntreeAudit.locataire == self.locataire)
            .order_by(TableEntreeAudit.rang.desc())
            .limit(1)
        ).first()

    def ajouter(
        self,
        *,
        horodatage: datetime,
        acteur: str,
        action: str,
        objet_type: str,
        objet_id: str | None = None,
        avant: dict[str, Any] | None = None,
        apres: dict[str, Any] | None = None,
        motif: str | None = None,
        adresse_ip: str | None = None,
    ) -> EntreeAudit:
        # ⚠️ Le verrou **avant** la lecture. L'inverse laisserait exactement la
        # fenêtre que ce module vient de refermer.
        self._serialiser_les_ajouts()
        precedente = self._tete()
        entree = EntreeAudit.poser(
            precedente=None if precedente is None else _vers_entree(precedente),
            horodatage=horodatage,
            acteur=acteur,
            locataire=self.locataire,
            action=action,
            objet_type=objet_type,
            objet_id=objet_id,
            avant=avant,
            apres=apres,
            motif=motif,
            adresse_ip=adresse_ip,
            # ⚠️ Lu dans le contexte, jamais reçu en argument : voir la réalisation en
            # mémoire. Un mandat qu'il faudrait penser à passer serait oublié là où il
            # compte le plus.
            mandat=sous_mandat(),
        )
        self._session.add(
            TableEntreeAudit(
                locataire=entree.locataire,
                rang=entree.rang,
                horodatage=entree.horodatage,
                acteur=entree.acteur,
                action=entree.action,
                objet_type=entree.objet_type,
                objet_id=entree.objet_id,
                # Sérialisés par Pydantic pour que dates et décimaux traversent
                # le JSON — un `datetime` brut ferait échouer l'insertion.
                avant=_json(entree.avant),
                apres=_json(entree.apres),
                motif=entree.motif,
                adresse_ip=entree.adresse_ip,
                mandat=entree.mandat,
                empreinte_precedente=entree.empreinte_precedente,
                empreinte=entree.empreinte,
            )
        )
        self._session.flush()
        return entree

    def tete(self) -> EntreeAudit | None:
        ligne = self._session.scalars(
            select(TableEntreeAudit)
            .where(TableEntreeAudit.locataire == self.locataire)
            .order_by(TableEntreeAudit.rang.desc())
            .limit(1)
        ).first()
        return None if ligne is None else _vers_entree(ligne)

    def lister(
        self,
        *,
        acteur: str | None = None,
        objet_type: str | None = None,
        objet_id: str | None = None,
        depuis: datetime | None = None,
    ) -> list[EntreeAudit]:
        requete = (
            select(TableEntreeAudit)
            .where(TableEntreeAudit.locataire == self.locataire)
            .order_by(TableEntreeAudit.rang)
        )
        if acteur is not None:
            requete = requete.where(TableEntreeAudit.acteur == acteur)
        if objet_type is not None:
            requete = requete.where(TableEntreeAudit.objet_type == objet_type)
        if objet_id is not None:
            requete = requete.where(TableEntreeAudit.objet_id == objet_id)
        if depuis is not None:
            requete = requete.where(TableEntreeAudit.horodatage >= depuis)
        return [_vers_entree(ligne) for ligne in self._session.scalars(requete).all()]

    def __len__(self) -> int:
        return int(
            self._session.scalar(
                select(func.count()).select_from(TableEntreeAudit).where(
                    TableEntreeAudit.locataire == self.locataire
                )
            )
            or 0
        )


class PortailSql:
    """Réalisation de `PortailDeclaratif` : le registre des accusés, en base.

    Même comportement que `PortailManuel` — il ne dépose pas, il consigne — mais
    l'unicité du dépôt est désormais garantie par la base plutôt que par un
    dictionnaire. C'est le point le plus important de tout ce lot : perdre un
    accusé, c'est perdre la preuve qu'une déclaration a été remise dans les
    délais.
    """

    def __init__(
        self,
        session: SessionSql,
        locataire: str,
        portail: Portail = Portail.DGI_TELEDECLARATION,
    ) -> None:
        self._session = session
        self.locataire = locataire
        self.portail = portail

    def depose_automatiquement(self) -> bool:
        return False

    def deposer(self, document: DocumentATransmettre, *, par: str) -> AccuseReception:
        raise DepotManuelRequis(
            f"Le portail {self.portail} ne reçoit pas de dépôt automatique. "
            f"Déposer la déclaration {document.code_document} de "
            f"{document.entreprise} pour la période du {document.periode_debut} au "
            f"{document.periode_fin} sur le portail, puis revenir saisir le numéro "
            "d'accusé de réception."
        )

    def enregistrer_accuse(
        self, accuse: AccuseReception, *, document: DocumentATransmettre
    ) -> AccuseReception:
        if not accuse.concerne(document):
            raise AccuseIncoherent(
                f"l'accusé {accuse.numero} ne correspond pas au document préparé. "
                f"Attendu : {document.reference}, empreinte {document.empreinte[:12]}… ; "
                f"reçu : {accuse.reference_document}, empreinte "
                f"{accuse.empreinte_deposee[:12]}…."
            )
        # ⚠️ Contrôlé contre le guichet **du document**, et non contre celui du
        # registre (pas 59). Le registre tient les accusés du locataire pour tous
        # ses guichets : la CNPS n'a pas de registre à elle, et un contrôle contre
        # `self.portail` refusait toute cotisation sociale. La garde reste entière :
        # un accusé CNPS ne s'attache toujours pas à une déclaration DGI.
        if accuse.portail is not document.portail:
            raise AccuseIncoherent(
                f"accusé du portail {accuse.portail} pour un document du portail "
                f"{document.portail}. Un accusé CNPS ne prouve rien devant la DGI."
            )
        depose = self.retrouver(document.reference)
        if depose is not None:
            raise AccuseIncoherent(
                f"{document.reference} porte déjà l'accusé {depose.numero} du "
                f"{depose.depose_le}. Un second dépôt produirait une déclaration "
                "rectificative non demandée."
            )

        consigne = accuse.model_copy(update={"mode": ModeDepot.MANUEL})
        self._session.add(
            TableAccuseReception(
                numero=consigne.numero,
                locataire=self.locataire,
                portail=consigne.portail.value,
                reference_document=consigne.reference_document,
                depose_le=consigne.depose_le,
                mode=consigne.mode.value,
                empreinte_deposee=consigne.empreinte_deposee,
                depose_par=consigne.depose_par,
                montant_constate=consigne.montant_constate,
                piece_jointe=consigne.piece_jointe,
                precision=consigne.precision,
                # Le contenu exact : sans lui, l'empreinte prouverait qu'on a
                # déposé quelque chose, pas quoi.
                contenu_depose=document.contenu,
                verifie=consigne.piece_jointe is not None,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as conflit:
            self._session.rollback()
            raise AccuseIncoherent(
                f"{document.reference} a déjà été déposé — contrainte d'unicité de la "
                "base. Deux dépôts concurrents de la même période ont été tentés."
            ) from conflit
        return consigne

    def retrouver(self, reference_document: str) -> AccuseReception | None:
        ligne = self._session.scalars(
            select(TableAccuseReception).where(
                TableAccuseReception.locataire == self.locataire,
                TableAccuseReception.reference_document == reference_document,
            )
        ).first()
        return None if ligne is None else _vers_accuse(ligne)

    def tous(self) -> list[AccuseReception]:
        lignes = self._session.scalars(
            select(TableAccuseReception)
            .where(TableAccuseReception.locataire == self.locataire)
            .order_by(TableAccuseReception.depose_le.desc())
        ).all()
        return [_vers_accuse(ligne) for ligne in lignes]


def _json(donnees: dict[str, Any] | None) -> dict[str, Any] | None:
    """Rend un dictionnaire sérialisable en JSON.

    Les entrées d'audit portent des dates, des décimaux et des énumérations. Les
    remettre tels quels à une colonne `JSON` fait échouer l'insertion sur un type
    non sérialisable — et l'échec survient au `flush`, loin de l'appelant.

    La conversion est **stable pour l'empreinte**, et c'est ce qui compte :
    `corps_canonique` sérialise déjà avec `default=str`, si bien qu'une date
    convertie ici produit exactement la même chaîne qu'une date convertie là.
    Relire une entrée depuis la base et recalculer son empreinte redonne donc la
    même valeur — sans quoi `verifier_chaine` déclarerait altérée toute entrée
    ayant fait l'aller-retour. Un test le vérifie.
    """
    if donnees is None:
        return None
    return json.loads(json.dumps(donnees, default=str, ensure_ascii=False))


def _vers_mandat(ligne: TableMandat) -> Mandat:
    return Mandat(
        identifiant=ligne.identifiant,
        mandant=ligne.locataire,
        mandataire=ligne.mandataire,
        roles=frozenset(Role(r) for r in ligne.roles),
        comptes=None if ligne.comptes is None else frozenset(ligne.comptes),
        debut=ligne.debut,
        fin=ligne.fin,
        motif=MotifMandat(ligne.motif),
        accorde_par=ligne.accorde_par,
        revoque_le=ligne.revoque_le,
        revoque_par=ligne.revoque_par,
        precision=ligne.precision,
    )


class DepotMandatsSql:
    """Réalisation de `DepotMandats`. Cloisonné sur le **mandant**.

    ⚠️ `enregistrer` refuse un mandat dont le mandant n'est pas le locataire du dépôt. Le
    filtre de session protège les lectures ; une écriture, elle, se contrôle à l'entrée,
    sans quoi un locataire pourrait s'accorder un mandat chez un autre.
    """

    def __init__(self, session: SessionSql, locataire: str) -> None:
        self._session = session
        self.locataire = locataire

    def lire(self, identifiant: str) -> Mandat | None:
        ligne = self._session.get(TableMandat, identifiant)
        if ligne is None or ligne.locataire != self.locataire:
            return None
        return _vers_mandat(ligne)

    def tous(self) -> list[Mandat]:
        lignes = self._session.scalars(
            select(TableMandat)
            .where(TableMandat.locataire == self.locataire)
            .order_by(TableMandat.debut.desc(), TableMandat.identifiant)
        ).all()
        return [_vers_mandat(ligne) for ligne in lignes]

    def enregistrer(self, mandat: Mandat) -> None:
        if mandat.mandant != self.locataire:
            raise ValueError(
                f"mandat {mandat.identifiant} du mandant {mandat.mandant} enregistré dans "
                f"le dépôt de {self.locataire}. Un locataire n'accorde de mandat que sur "
                "ses propres données."
            )
        ligne = self._session.get(TableMandat, mandat.identifiant)
        if ligne is None:
            ligne = TableMandat(identifiant=mandat.identifiant)
            self._session.add(ligne)
        ligne.locataire = mandat.mandant
        ligne.mandataire = mandat.mandataire
        ligne.roles = sorted(r.value for r in mandat.roles)
        ligne.comptes = None if mandat.comptes is None else sorted(mandat.comptes)
        ligne.debut = mandat.debut
        ligne.fin = mandat.fin
        ligne.motif = mandat.motif.value
        ligne.accorde_par = mandat.accorde_par
        ligne.revoque_le = mandat.revoque_le
        ligne.revoque_par = mandat.revoque_par
        ligne.precision = mandat.precision
        self._session.flush()
