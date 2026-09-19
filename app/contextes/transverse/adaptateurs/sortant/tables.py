"""Les tables du contexte K.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI LE SOCLE PASSE EN BASE LE PREMIER

Sans identité persistante, rien d'autre ne sert : un redémarrage renvoie tout le
monde à la page de connexion avec des comptes qui n'existent plus. Et sans
journal d'audit persistant, la chaîne de hachage recommence à la genèse à chaque
démarrage — c'est-à-dire qu'elle n'atteste de rien.

Le **registre des accusés de réception** est le plus critique de tous. Une
comptabilité se refait ; une preuve de dépôt, non. Perdre un accusé, c'est perdre
la seule pièce qui prouve qu'une déclaration a été remise dans les délais.

CE QUE LES TABLES AJOUTENT AUX ENTITÉS

Deux garanties que la mémoire ne pouvait pas tenir, et que les ports exigeaient :

**L'unicité du rang d'audit.** `uq_journal_audit_rang` par locataire. Deux
processus qui liraient la même tête produiraient deux entrées de même rang ; la
contrainte en fait échouer une, qui recommence. C'est ce que l'en-tête du port
`JournalAudit` réclamait et que le verrou en mémoire ne pouvait garantir qu'à
l'intérieur d'un processus.

**L'unicité du dépôt.** `uq_accuse_reception_reference_document` : un second
dépôt de la TVA de juillet se heurte à la base, pas à la mémoire de celui qui
saisit.

CE QUI N'EST PAS UNE CLÉ ÉTRANGÈRE, ET POURQUOI

`journal_audit.acteur` ne référence pas `compte.identifiant`. L'acteur vaut
parfois `systeme` ou `anonyme` — un webhook, une tentative de connexion sur une
adresse inconnue —, et surtout : **le journal doit survivre à tout**. Une clé
étrangère qui empêcherait de supprimer un compte serait un bon garde-fou ; une
qui empêcherait d'écrire au journal parce que l'acteur n'est pas encore en base
serait une catastrophe. Le journal n'a de dépendance vers rien.

LA COLONNE `locataire` VIENT DU MIXIN, ET N'EST JAMAIS REDÉCLARÉE

`Cloisonne` la porte. Une table cloisonnée ne peut donc pas l'oublier : le seul
oubli possible est celui du mixin lui-même, qui se lit sur la ligne de
déclaration de la classe. Les index composites, eux, restent ici — ils
commencent tous par `locataire`, et un index simple ferait double emploi.

LES ÉNUMÉRATIONS SONT DES CHAÎNES, PAS DES TYPES POSTGRESQL

Un type `ENUM` natif se modifie par `ALTER TYPE`, qui ne se joue pas dans une
transaction sur toutes les versions et qui rend les migrations pénibles. Ajouter
un rôle ou un état ne doit pas être une opération de schéma. La contrainte reste
portée par Pydantic à la lecture — et c'est là qu'elle doit être, puisque c'est
le domaine qui décide de ce qui est un rôle.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.base_de_donnees import Base, Cloisonne

__all__ = [
    "TableAccuseReception",
    "TableCompte",
    "TableEntreeAudit",
    "TableHabilitation",
    "TableJeton",
    "TableLectureDesNotifications",
    "TableMandat",
    "TableSession",
]

#: Longueur des identifiants techniques — `C-abc123…`, `S-…`, `H-…`.
_ID = 64
#: SHA-256 hexadécimal.
_EMPREINTE = 64


class TableCompte(Cloisonne, Base):
    """Les identités."""

    __tablename__ = "compte"
    __table_args__ = (
        # L'adresse est l'identifiant de connexion : deux porteurs rendraient
        # l'un des deux inaccessible. Unique **par locataire** — deux cabinets
        # peuvent employer la même personne.
        UniqueConstraint("locataire", "courriel", name="uq_compte_locataire_courriel"),
        Index("ix_compte_locataire_etat", "locataire", "etat"),
    )

    identifiant: Mapped[str] = mapped_column(String(_ID), primary_key=True)

    courriel: Mapped[str] = mapped_column(String(320), nullable=False)
    nom: Mapped[str] = mapped_column(String(120), nullable=False)
    prenom: Mapped[str] = mapped_column(String(120), nullable=False)
    telephone: Mapped[str | None] = mapped_column(String(20))

    #: ⚠️ Ces deux colonnes ne sortent jamais dans une réponse : l'entité les
    #: exclut de sa sérialisation. Elles doivent en outre être **chiffrées au
    #: repos** — voir `second_facteur.py` pour le secret, qui ne peut pas être
    #: réduit à une empreinte puisqu'il doit être relu.
    empreinte_mot_de_passe: Mapped[str | None] = mapped_column(String(255))
    #: ⚠️ 255 et non 32, la taille d'un secret TOTP en base32 : la colonne
    #: porte un **scellé** — préfixe de version, nonce et sceau d'authenticité
    #: compris, soit 83 caractères aujourd'hui. La marge couvre un changement
    #: d'algorithme sans migration. Voir `coffre.py`.
    secret_totp: Mapped[str | None] = mapped_column(String(255))

    etat: Mapped[str] = mapped_column(String(32), nullable=False)
    cree_le: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    tentatives_echouees: Mapped[int] = mapped_column(nullable=False, default=0)
    verrouille_jusqu_a: Mapped[datetime | None] = mapped_column(DateTime)
    derniere_connexion: Mapped[datetime | None] = mapped_column(DateTime)


class TableHabilitation(Cloisonne, Base):
    """Les rôles datés.

    ⚠️ **Aucune suppression.** Une habilitation se ferme — `fin` reçoit une date.
    La ligne demeure, parce que c'est elle qui répondra à « qui était habilité le
    12 mars ». Il n'existe donc pas de `DELETE` sur cette table dans le code, et
    il ne doit pas en apparaître.
    """

    __tablename__ = "habilitation"
    __table_args__ = (
        Index("ix_habilitation_compte", "locataire", "compte"),
        # La portée est une table de liaison plutôt qu'un tableau : on interroge
        # « qui a accès à ce dossier », et une colonne `text[]` obligerait à un
        # index GIN et à une syntaxe que personne ne relit.
    )

    identifiant: Mapped[str] = mapped_column(String(_ID), primary_key=True)

    compte: Mapped[str] = mapped_column(
        String(_ID), ForeignKey("compte.identifiant"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(40), nullable=False)

    debut: Mapped[date] = mapped_column(Date, nullable=False)
    fin: Mapped[date | None] = mapped_column(Date)
    motif: Mapped[str] = mapped_column(String(40), nullable=False)
    accordee_par: Mapped[str] = mapped_column(String(_ID), nullable=False)
    precision: Mapped[str | None] = mapped_column(Text)

    #: `NULL` = transverse, tous les dossiers. Une liste vide et `NULL` ne se
    #: confondent pas : la première est « aucun dossier », la seconde « tous ».
    #: Stockée en JSON plutôt qu'en table de liaison — une portée se lit toujours
    #: entière, jamais par élément, et la jointure ne servirait qu'à la requête
    #: « qui accède à ce dossier », qui reste rare et qui se fait très bien par
    #: un filtre en Python sur les habilitations actives.
    portee: Mapped[list[str] | None] = mapped_column(JSON)


class TableJeton(Cloisonne, Base):
    """Les liens à usage unique.

    Indexée sur l'empreinte : c'est la seule chose qu'on cherche, puisque le
    secret n'est pas stocké. Une recherche par compte sert au diagnostic.
    """

    __tablename__ = "jeton"
    __table_args__ = (
        UniqueConstraint("empreinte", name="uq_jeton_empreinte"),
        Index("ix_jeton_compte", "locataire", "compte"),
    )

    identifiant: Mapped[str] = mapped_column(String(_ID), primary_key=True)

    empreinte: Mapped[str] = mapped_column(String(_EMPREINTE), nullable=False)
    compte: Mapped[str] = mapped_column(
        String(_ID), ForeignKey("compte.identifiant"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    emis_le: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expire_le: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    consomme_le: Mapped[datetime | None] = mapped_column(DateTime)
    emis_par: Mapped[str] = mapped_column(String(_ID), nullable=False)


class TableSession(Cloisonne, Base):
    """Les accès ouverts.

    C'est la table la plus lue du système — une fois par requête. L'index sur le
    compte sert la révocation en masse, qui est le geste rare mais critique.
    """

    __tablename__ = "session_ouverte"
    __table_args__ = (Index("ix_session_compte", "locataire", "compte"),)

    identifiant: Mapped[str] = mapped_column(String(_ID), primary_key=True)

    compte: Mapped[str] = mapped_column(
        String(_ID), ForeignKey("compte.identifiant"), nullable=False
    )
    ouverte_le: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expire_le: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoquee_le: Mapped[datetime | None] = mapped_column(DateTime)
    motif_revocation: Mapped[str | None] = mapped_column(String(40))
    adresse_ip: Mapped[str | None] = mapped_column(String(64))
    agent: Mapped[str | None] = mapped_column(String(255))
    renforcee_jusqu_a: Mapped[datetime | None] = mapped_column(DateTime)


class TableMandat(Cloisonne, Base):
    """Les mandats accordés par ce locataire à un autre.

    ⚠️ **`locataire` est le mandant.** C'est lui qui accorde, lui qui retire, et c'est dans
    son périmètre que la vérification a lieu : ranger le mandat chez le mandataire
    obligerait à sortir du périmètre servi pour savoir si l'on a le droit d'y entrer.

    ⚠️ **Les rôles et les comptes désignés sont des listes JSON**, et non des tables liées.
    Ce sont des valeurs du mandat, sans existence propre : une table de liaison
    demanderait deux écritures là où le domaine n'en voit qu'une, et laisserait la
    possibilité d'un mandat sans rôle, que le modèle refuse.
    """

    __tablename__ = "mandat"
    __table_args__ = (
        Index("ix_mandat_mandataire", "locataire", "mandataire"),
    )

    identifiant: Mapped[str] = mapped_column(String(_ID), primary_key=True)

    #: Le locataire dont les comptes vont agir. Le mandant, lui, est la colonne
    #: `locataire` héritée du cloisonnement.
    mandataire: Mapped[str] = mapped_column(String(_ID), nullable=False)

    roles: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    #: `None` = tous les comptes du mandataire.
    comptes: Mapped[list[str] | None] = mapped_column(JSON)

    debut: Mapped[date] = mapped_column(Date, nullable=False)
    fin: Mapped[date | None] = mapped_column(Date)
    motif: Mapped[str] = mapped_column(String(32), nullable=False)
    accorde_par: Mapped[str] = mapped_column(String(200), nullable=False)

    #: ⚠️ Distincte de `fin` : un mandat retiré avant terme n'est pas un mandat arrivé à
    #: échéance, et c'est exactement ce qu'un litige demande de savoir.
    revoque_le: Mapped[date | None] = mapped_column(Date)
    revoque_par: Mapped[str | None] = mapped_column(String(200))

    precision: Mapped[str | None] = mapped_column(String(500))


class TableEntreeAudit(Cloisonne, Base):
    """Le journal chaîné.

    ─────────────────────────────────────────────────────────────────────────
    L'UNICITÉ DU RANG EST LA GARANTIE QUE LA MÉMOIRE NE POUVAIT PAS TENIR

    Deux processus qui liraient la même tête produiraient deux entrées de rang
    identique chaînées au même prédécesseur, et `verifier_chaine` signalerait
    une altération qui n'en serait pas une. La contrainte d'unicité en fait
    échouer une : le dépôt relit la tête et recommence.

    C'est écrit dans l'en-tête du port `JournalAudit` depuis le premier jour,
    avec la mention « une réalisation en mémoire ne peut pas le garantir ».
    C'est ici que ce n'est plus vrai.

    AUCUNE CLÉ ÉTRANGÈRE, AUCUNE SUPPRESSION

    L'acteur vaut parfois `systeme` ou `anonyme`. Et le journal doit survivre à
    tout : une contrainte qui empêcherait d'y écrire serait pire que l'absence
    de contrainte. Il n'existe ni `UPDATE` ni `DELETE` sur cette table dans le
    code — c'est sa garantie même.
    ─────────────────────────────────────────────────────────────────────────
    """

    __tablename__ = "journal_audit"
    __table_args__ = (
        UniqueConstraint("locataire", "rang", name="uq_journal_audit_locataire_rang"),
        Index("ix_journal_audit_objet", "locataire", "objet_type", "objet_id"),
        Index("ix_journal_audit_acteur", "locataire", "acteur"),
    )

    #: Clé technique. Le rang porte l'ordre ; l'identifiant ne sert qu'à
    #: référencer une ligne sans ambiguïté.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    rang: Mapped[int] = mapped_column(BigInteger, nullable=False)
    horodatage: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    acteur: Mapped[str] = mapped_column(String(_ID), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    objet_type: Mapped[str] = mapped_column(String(64), nullable=False)
    objet_id: Mapped[str | None] = mapped_column(String(255))

    avant: Mapped[dict | None] = mapped_column(JSON)
    apres: Mapped[dict | None] = mapped_column(JSON)
    motif: Mapped[str | None] = mapped_column(Text)
    adresse_ip: Mapped[str | None] = mapped_column(String(64))

    #: ⚠️ Facultatif, et il le restera : la très grande majorité des actions sont exercées
    #: par un locataire chez lui. Une colonne obligatoire obligerait à inventer une valeur
    #: pour ce cas, et « aucun mandat » n'est pas une valeur, c'est une absence.
    mandat: Mapped[str | None] = mapped_column(String(_ID))

    empreinte_precedente: Mapped[str] = mapped_column(String(_EMPREINTE), nullable=False)
    empreinte: Mapped[str] = mapped_column(String(_EMPREINTE), nullable=False)


class TableAccuseReception(Cloisonne, Base):
    """Les preuves de dépôt.

    **La table la plus critique du système.** Une comptabilité se refait ; un
    accusé de réception, non. C'est elle qui porte la date opposable d'un dépôt,
    celle que l'administration retiendra pour calculer une pénalité de retard.

    L'unicité de `reference_document` — dossier, obligation, période — est ce qui
    empêche un second dépôt de la même déclaration. Elle est portée par la base
    plutôt que par la mémoire de celui qui saisit.
    """

    __tablename__ = "accuse_reception"
    __table_args__ = (
        UniqueConstraint(
            "locataire",
            "reference_document",
            name="uq_accuse_reception_locataire_reference",
        ),
        Index("ix_accuse_reception_portail", "locataire", "portail", "depose_le"),
    )

    numero: Mapped[str] = mapped_column(String(120), primary_key=True)

    portail: Mapped[str] = mapped_column(String(40), nullable=False)
    reference_document: Mapped[str] = mapped_column(String(255), nullable=False)
    depose_le: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)

    #: L'empreinte de ce qui a été déposé. C'est elle qui permettra, trois ans
    #: plus tard, de prouver que les chiffres remis sont bien ceux du système.
    empreinte_deposee: Mapped[str] = mapped_column(String(_EMPREINTE), nullable=False)
    depose_par: Mapped[str] = mapped_column(String(_ID), nullable=False)
    montant_constate: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    piece_jointe: Mapped[str | None] = mapped_column(String(255))
    precision: Mapped[str | None] = mapped_column(Text)

    #: Le contenu exact déposé, conservé tel quel. Sans lui, l'empreinte ne
    #: prouve rien : on saurait que quelque chose a été déposé, pas quoi.
    contenu_depose: Mapped[str | None] = mapped_column(Text)

    verifie: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class TableLectureDesNotifications(Cloisonne, Base):
    """Jusqu'où chaque compte a lu ses notifications (pas 94).

    ─────────────────────────────────────────────────────────────────────────
    UNE LIGNE PAR COMPTE, ET RIEN D'AUTRE

    Les notifications ne sont pas stockées : elles sont lues au journal d'audit à
    travers les abonnements du référentiel (voir `domaine/notifications.py`). Seule la
    position de lecture est une donnée propre au compte, et c'est un **rang** du
    journal, unique et croissant, jamais une heure.

    ⚠️ La clé porte le locataire : un identifiant de compte est propre au cabinet qui
    l'a créé, et le rang aussi (chaque cabinet a sa chaîne).
    ─────────────────────────────────────────────────────────────────────────
    """

    __tablename__ = "lecture_des_notifications"

    locataire: Mapped[str] = mapped_column(String(_ID), primary_key=True)
    compte: Mapped[str] = mapped_column(String(_ID), primary_key=True)
    lu_jusqu_au_rang: Mapped[int] = mapped_column(BigInteger, nullable=False)
