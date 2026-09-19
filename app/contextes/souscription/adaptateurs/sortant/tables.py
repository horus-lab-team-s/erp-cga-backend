"""Les tables du contexte M.

Le devis, la souscription et le paiement sont conservés comme documents, avec les
colonnes que le parcours interroge : la référence pour le lien reçu par courriel,
l'état pour la surveillance, la clé d'idempotence et l'échéance pour le
rapprochement.

⚠️ Deux contraintes d'unicité portent des garanties commerciales, et ce sont les
mêmes que celles du domaine — désormais tenues par la base :

* `uq_paiement_cle_idempotence` — deux appels portant la même clé ne produisent
  qu'une opération. C'est la première ligne de défense contre le double-débit.
* `uq_paiement_echeance` — une mensualité n'est prélevée qu'une fois, même si
  l'ordonnanceur rejoue sa tâche.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.base_de_donnees import Base, Cloisonne, Document

__all__ = [
    "TableDevis",
    "TableProforma",
    "TableQualification",
    "TableRappelAPasser",
    "TableSuiviDeRelance",
    "TableDossierCommercial",
    "TablePaiement",
    "TableSouscription",
]


class TableDevis(Cloisonne, Document, Base):
    """Une proposition datée."""

    __tablename__ = "devis"
    __table_args__ = (Index("ix_devis_courriel", "locataire", "courriel"),)

    reference: Mapped[str] = mapped_column(String(64), primary_key=True)
    courriel: Mapped[str] = mapped_column(String(320), nullable=False)
    etabli_le: Mapped[date] = mapped_column(Date, nullable=False)
    valide_jusqu_au: Mapped[date] = mapped_column(Date, nullable=False)
    etat: Mapped[str] = mapped_column(String(20), nullable=False)


class TableSouscription(Cloisonne, Document, Base):
    """Un engagement pris."""

    __tablename__ = "souscription"
    __table_args__ = (
        # `a_activer` — payées et non activées — est **la** requête de
        # surveillance : un client qui a payé et n'a rien reçu doit apparaître
        # sans qu'on ait à le chercher.
        Index("ix_souscription_etat", "locataire", "etat"),
        Index("ix_souscription_niu", "locataire", "niu"),
    )

    reference: Mapped[str] = mapped_column(String(64), primary_key=True)
    devis: Mapped[str] = mapped_column(String(64), nullable=False)
    service: Mapped[str] = mapped_column(String(40), nullable=False)
    etat: Mapped[str] = mapped_column(String(30), nullable=False)
    montant: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    engagee_le: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    payee_le: Mapped[datetime | None] = mapped_column(DateTime)
    niu: Mapped[str | None] = mapped_column(String(20))
    compte: Mapped[str | None] = mapped_column(String(64))
    prend_effet_le: Mapped[date | None] = mapped_column(Date)


class TablePaiement(Cloisonne, Document, Base):
    """Un encaissement — voir l'en-tête pour les deux garanties."""

    __tablename__ = "paiement"
    __table_args__ = (
        UniqueConstraint(
            "locataire", "cle_idempotence", name="uq_paiement_locataire_cle"
        ),
        UniqueConstraint(
            "locataire", "echeance", name="uq_paiement_locataire_echeance"
        ),
        Index("ix_paiement_reference_reglee", "locataire", "reference_reglee"),
        Index("ix_paiement_statut", "locataire", "statut", "initie_le"),
        # Le rapprochement à trois stratégies interroge sur ces deux colonnes.
        Index("ix_paiement_reference_externe", "locataire", "reference_externe"),
        Index("ix_paiement_telephone", "locataire", "telephone", "initie_le"),
    )

    identifiant: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: Ce que le paiement règle : une souscription, ou le numéro d'une proforma.
    #: ⚠️ La colonne s'appelait `souscription`. Renommée le jour où un paiement a
    #: pu régler autre chose : un nom qui ment coûte plus cher qu'une migration.
    reference_reglee: Mapped[str] = mapped_column(String(64), nullable=False)
    cle_idempotence: Mapped[str] = mapped_column(String(64), nullable=False)
    #: `NULL` pour le paiement de mise en route, engagé avant que l'échéancier
    #: n'existe. PostgreSQL n'applique pas l'unicité aux valeurs nulles, ce qui
    #: est exactement ce qu'il faut ici.
    echeance: Mapped[str | None] = mapped_column(String(80))
    statut: Mapped[str] = mapped_column(String(20), nullable=False)
    montant: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    telephone: Mapped[str] = mapped_column(String(20), nullable=False)
    initie_le: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    reference_externe: Mapped[str | None] = mapped_column(String(120))


class TableDossierCommercial(Cloisonne, Document, Base):
    """Un dossier du parcours d'acquisition.

    ⚠️ **Elle est cloisonnée comme les autres**, et la demande qui la remplit
    arrive pourtant d'un visiteur anonyme, sans tenant. Il n'y a pas de
    contradiction : un nom d'hôte qui ne désigne aucun tenant retombe sur le
    locataire du centre, et la demande s'y range. C'est le cabinet qui la reçoit,
    pas un client.

    QUATRE COLONNES PROMUES, UNE PAR REQUÊTE RÉELLE

    * `telephone` — la détection de doublon, à chaque dépôt. Une seule colonne
      suffit pour un dossier qui porte plusieurs demandes, parce que le
      rattachement n'a lieu que sur numéro identique : toutes les demandes d'un
      dossier portent le même. Interroger le document seul obligerait à le lire
      en entier, sur chaque dépôt ;
    * `etat` et `depuis_le` — le balayage de veille, état par état, avec un
      délai propre à chacun ;
    * `responsable` — « mes dossiers », l'écran que le commercial ouvre le
      matin.

    `deposee_le` est promue aussi, mais pour l'ordre, pas pour un filtre : un
    tableau de bord qui change d'ordre d'un rafraîchissement à l'autre passe
    pour cassé même quand il ne l'est pas.
    """

    __tablename__ = "dossier_commercial"
    __table_args__ = (
        # Le doublon : « ce numéro a-t-il déjà écrit depuis hier ? »
        Index("ix_dossier_telephone", "locataire", "telephone", "deposee_le"),
        # La veille : « qui est resté trop longtemps dans cet état ? »
        Index("ix_dossier_veille", "locataire", "etat", "depuis_le"),
        # La console : « mes dossiers en cours ».
        Index("ix_dossier_responsable", "locataire", "responsable", "etat"),
    )

    reference: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: Forme canonique `+237XXXXXXXXX`, celle de la demande. Voir l'en-tête.
    telephone: Mapped[str] = mapped_column(String(20), nullable=False)
    etat: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Quand le dossier est entré dans son état courant. La veille porte
    #: là-dessus, jamais sur `deposee_le`.
    depuis_le: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    deposee_le: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    responsable: Mapped[str | None] = mapped_column(String(64))


class TableProforma(Cloisonne, Document, Base):
    """Le document commercial chiffré, qui vaut contrat une fois accepté.

    ⚠️ **C'est la table dont la durée de conservation est la plus longue du
    contexte.** Ce qui a été envoyé à un client doit ressortir à l'identique dix
    ans plus tard, y compris devant un tribunal. Rien ici ne se met à jour en
    place : une modification produit une nouvelle ligne, et l'ancienne passe à
    l'état REMPLACEE.

    SIX COLONNES PROMUES, ET CHACUNE RÉPOND À UNE QUESTION QU'ON POSE VRAIMENT

    * `dossier` — « les proformas de ce dossier », l'écran du responsable ;
    * `etat` et `transmise_le` — le calendrier de relance à 3, 7 et 14 jours,
      balayé par l'ordonnanceur ;
    * `montant` — le pilotage, qui agrège sans lire les documents ;
    * `remplace` — remonter le fil des versions, ce qui permet de montrer au
      client ce qu'il avait reçu avant ;
    * `emise_le` — l'ordre, et le contrôle de continuité de la série.

    ⚠️ **LA CLÉ PRIMAIRE EST COMPOSITE, ET C'EST UNE CORRECTION**

    `(locataire, numero)`, et non le numéro seul. Un numéro de proforma est
    **séquentiel par cabinet** : `PRO-2026-0001` existe chez chacun d'eux, et
    c'est normal. Une clé primaire sur le seul numéro ferait échouer la première
    émission du second cabinet, avec un message parlant de doublon là où il n'y a
    aucun doublon.

    Les autres tables du contexte s'en tirent avec une clé simple parce que leurs
    identifiants sont opaques et tirés au sort, donc uniques par construction. Le
    numéro d'une proforma est l'inverse : il est lisible, prévisible, et il doit
    l'être — un client le cite au téléphone.

    Le défaut a été trouvé en éprouvant la contrainte d'unicité sur une vraie
    base : le rejet venait de `pk_proforma`, pas de la contrainte prévue pour
    cela.
    """

    __tablename__ = "proforma"
    __table_args__ = (
        # Le calendrier de relance : « ce qui est transmis et sans réponse ».
        Index("ix_proforma_relance", "locataire", "etat", "transmise_le"),
        Index("ix_proforma_dossier", "locataire", "dossier", "emise_le"),
    )

    #: ⚠️ Redéclarée en clé primaire. Le mixin `Cloisonne` la pose comme colonne
    #: ordinaire ; ici elle fait partie de l'identité, parce qu'un numéro n'a de
    #: sens qu'au sein d'un cabinet.
    locataire: Mapped[str] = mapped_column(String(64), primary_key=True)
    numero: Mapped[str] = mapped_column(String(32), primary_key=True)
    dossier: Mapped[str] = mapped_column(String(64), nullable=False)
    etat: Mapped[str] = mapped_column(String(20), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: Le numéro de la version précédente. `NULL` pour une première version.
    remplace: Mapped[str | None] = mapped_column(String(32))
    montant: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    emise_le: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    transmise_le: Mapped[datetime | None] = mapped_column(DateTime)


class TableSuiviDeRelance(Cloisonne, Document, Base):
    """Ce qui a déjà été relancé, pour une proforma. À part du document.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **POURQUOI CE N'EST PAS UNE COLONNE DE PLUS SUR LA PROFORMA**

    La proforma est figée et vaut contrat. Ce qui a été envoyé à un client doit
    ressortir à l'identique dix ans plus tard, y compris devant un tribunal. Y
    inscrire un compteur de relances ferait **changer un document contractuel pour
    une raison qui n'a rien de contractuel**, et chaque balayage réécrirait une
    ligne dont la stabilité est précisément la propriété qu'on lui demande.

    Le suivi vit donc à part, il se réécrit autant qu'il faut, et la proforma ne
    bouge pas.

    LA CLÉ EST COMPOSITE, POUR LA MÊME RAISON QUE CELLE DE LA PROFORMA

    `(locataire, proforma)`. Un numéro de proforma est **séquentiel par cabinet** :
    `PRO-2026-0001` existe chez chacun d'eux. Une clé sur le seul numéro ferait que
    le premier cabinet à relancer empêcherait tous les autres d'inscrire leur suivi,
    et le refus citerait une contrainte de clé primaire sans rapport apparent avec
    le cloisonnement.

    UNE SEULE COLONNE PROMUE, ET ELLE SERT AU BALAYAGE

    `derniere_le` répond à « qu'est-ce qui a été relancé récemment », qui est la
    question de l'écran du responsable. Les rangs employés restent dans le document :
    le balayage charge de toute façon l'entité entière pour décider du palier
    suivant, et les promouvoir n'épargnerait aucune lecture.
    ─────────────────────────────────────────────────────────────────────────────
    """

    __tablename__ = "suivi_de_relance"
    __table_args__ = (
        # « Ce qui a été relancé récemment », du plus récent au plus ancien.
        Index("ix_suivi_relance_recent", "locataire", "derniere_le"),
    )

    #: ⚠️ Redéclarée en clé primaire, comme sur la proforma : un numéro n'a de sens
    #: qu'au sein d'un cabinet.
    locataire: Mapped[str] = mapped_column(String(64), primary_key=True)
    proforma: Mapped[str] = mapped_column(String(32), primary_key=True)
    #: `NULL` tant qu'aucune relance n'est partie. La ligne peut exister sans envoi :
    #: elle est écrite dès qu'un balayage s'intéresse à la proforma.
    derniere_le: Mapped[datetime | None] = mapped_column(DateTime)


class TableRappelAPasser(Cloisonne, Document, Base):
    """Le carnet des rappels : ce que la machine confie à un humain.

    ⚠️ **Deux colonnes promues, et l'index qui va avec porte la requête de l'écran.**

    Celle du responsable est « ce qui m'attend, du plus ancien au plus récent », et
    elle se pose sur `fait_le IS NULL`. Sans `fait_le` promue, il faudrait charger
    tous les rappels de l'année pour trouver la poignée qui attend.

    `dossier` est promue pour la question inverse : « qu'a-t-on déjà confié sur ce
    dossier », posée en rouvrant un dossier avant d'appeler.
    """

    __tablename__ = "rappel_a_passer"
    __table_args__ = (
        # « Ce qui m'attend », du plus ancien au plus récent. Le partiel n'est pas
        # exprimé ici : SQLAlchemy le rendrait, mais un index partiel se relit mal
        # dans une migration, et le volume attendu ne le justifie pas.
        Index("ix_rappel_en_attente", "locataire", "fait_le", "cree_le"),
        Index("ix_rappel_dossier", "locataire", "dossier", "cree_le"),
    )

    #: ⚠️ **Redéclarée en clé primaire**, comme sur la proforma et le suivi de
    #: relance. C'est la troisième fois que ce piège se présente, et il a la même
    #: cause : l'identifiant d'un rappel dérive de celui de l'événement, qui dérive
    #: du **numéro de proforma**, lequel est séquentiel par cabinet.
    #:
    #: `rap-rel-PRO-2026-0001-2` existe donc chez chaque cabinet qui relance sa
    #: première proforma au rang 2. Une clé sur le seul identifiant ferait que le
    #: premier à relancer empêcherait tous les autres d'inscrire leur rappel.
    #:
    #: *La règle générale se lit maintenant : toute clé dérivée d'une numérotation
    #: par cabinet doit porter le locataire.*
    locataire: Mapped[str] = mapped_column(String(64), primary_key=True)
    identifiant: Mapped[str] = mapped_column(String(64), primary_key=True)
    dossier: Mapped[str] = mapped_column(String(64), nullable=False)
    cree_le: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    #: `NULL` tant que personne n'a rappelé. C'est la colonne que l'écran interroge,
    #: et c'est pourquoi elle n'est pas un booléen : la date sert aussi à mesurer le
    #: délai entre la décision de la machine et l'appel de l'humain.
    fait_le: Mapped[datetime | None] = mapped_column(DateTime)


class TableQualification(Cloisonne, Document, Base):
    """Ce qu'on a appris d'un prospect, et ce qui manque encore.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **CE QUI MANQUAIT N'ÉTAIT PAS LA TABLE, C'ÉTAIT L'ÉCRITURE.**

    La route de qualification construisait l'objet, validait chaque réponse au type
    de sa question, rendait l'avancement et les faits — puis **le jetait**. Un
    responsable qui répondait à cinq questions sur douze et revenait le lendemain
    recommençait à zéro, sans qu'aucune erreur ne se produise.

    UNE LIGNE PAR DOSSIER, ET NON PAR PASSAGE

    La qualification d'un dossier est un état, pas un journal : elle se complète au
    fil des échanges avec le client. Conserver chaque passage obligerait à trier
    pour reconstituer l'état courant, et la question posée est toujours « où en
    est-on ».

    ⚠️ **`version_questionnaire` est promue**, et c'est ce qui rend une
    qualification relisible. Un questionnaire évolue : une question ajoutée rendrait
    « incomplètes » toutes les qualifications déjà closes si l'on comparait à la
    version du jour. La version employée est donc conservée avec les réponses, et
    l'avancement se mesure contre elle.
    ─────────────────────────────────────────────────────────────────────────────
    """

    __tablename__ = "qualification"
    __table_args__ = (
        # « Les qualifications de ce service », pour mesurer ce qu'un questionnaire
        # laisse en plan avant de le réviser.
        Index("ix_qualification_service", "locataire", "service"),
    )

    #: ⚠️ Redéclarée en clé primaire : une référence de dossier est propre au
    #: cabinet qui l'a émise. Voir la règle établie au pas 17.
    locataire: Mapped[str] = mapped_column(String(64), primary_key=True)
    dossier: Mapped[str] = mapped_column(String(64), primary_key=True)
    service: Mapped[str] = mapped_column(String(64), nullable=False)
    version_questionnaire: Mapped[str] = mapped_column(String(32), nullable=False)
