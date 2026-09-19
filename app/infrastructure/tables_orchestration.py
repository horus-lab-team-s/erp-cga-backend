"""Les tables du socle d'orchestration.

Elles ne sont pas dans un contexte borné, et c'est délibéré : la boîte d'envoi et
les exécutions de saga ne sont le métier de personne. Elles vivent avec
l'infrastructure, au même titre que le journal d'audit.

⚠️ **Les deux premières sont cloisonnées.** Un événement appartient au locataire
qui l'a produit, une saga au locataire qui la fait tourner. La saga d'ouverture
d'un tenant appartient au **centre**, pas au tenant qu'elle crée : le tenant
n'existe pas encore quand elle démarre.

⚠️ **La troisième ne l'est pas**, délibérément. Le passage de l'ordonnanceur
n'appartient à personne : le relais vide la boîte de tous les locataires, et lui
donner un propriétaire obligerait à en choisir un au hasard. Voir
`TablePassageOrdonnance`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.base_de_donnees import Base, Cloisonne, Document

__all__ = [
    "TableEvenementSortant",
    "TableExecutionSaga",
    "TablePassageOrdonnance",
]


class TableEvenementSortant(Cloisonne, Document, Base):
    """La boîte d'envoi.

    ⚠️ **L'index `ix_boite_a_publier` est celui qui fait vivre le système.** Le
    passage de publication l'interroge en boucle, toutes les quelques secondes,
    et il doit rendre en temps constant même quand la table a un an d'historique.

    Il porte `publie_le` et `en_quarantaine`, dans cet ordre, parce que c'est la
    requête : « ce qui n'est pas publié et pas mis de côté, du plus ancien au plus
    récent ».

    L'ORDRE D'ÉMISSION EST UNE GARANTIE, PAS UN CONFORT

    Deux événements de la même clé doivent être traités dans l'ordre. Un
    `TenantOuvert` traité avant le `PaiementEncaissé` qui l'a causé produirait un
    tenant sans justification. `cree_le` est donc promue et indexée.
    """

    __tablename__ = "boite_d_envoi"
    __table_args__ = (
        Index("ix_boite_a_publier", "locataire", "publie_le", "en_quarantaine", "cree_le"),
        Index("ix_boite_cle", "locataire", "cle", "cree_le"),
    )

    identifiant: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: Promu : les journaux d'exploitation filtrent là-dessus.
    nom: Mapped[str] = mapped_column(String(80), nullable=False)
    cle: Mapped[str] = mapped_column(String(120), nullable=False)
    cree_le: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    #: `NULL` tant qu'il attend. C'est cette colonne que la requête de publication
    #: interroge, et c'est pourquoi elle n'est pas un booléen : la date sert aussi
    #: à mesurer le délai de bout en bout.
    publie_le: Mapped[datetime | None] = mapped_column(DateTime)
    tentatives: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    en_quarantaine: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dernier_echec: Mapped[str | None] = mapped_column(Text)


class TableExecutionSaga(Cloisonne, Document, Base):
    """L'avancement d'une saga.

    ⚠️ **`uq_saga_cle` porte toute la sûreté du mécanisme.** Deux exécutions
    concurrentes de la même saga sur la même clé produiraient l'effet deux fois :
    deux préfixes de stockage, deux liens d'activation, deux comptes
    administrateur. C'est la base qui arbitre, jamais une lecture suivie d'une
    écriture — entre les deux, l'autre a eu le temps d'écrire.

    L'INDEX DE REPRISE RÉPOND À DEUX QUESTIONS D'EXPLOITATION

    « Qu'est-ce qui est en cours et depuis quand ? » pour la relance automatique,
    et « qu'est-ce qui attend un humain ? » pour l'alerte du matin. Les deux se
    lisent sur `etat` et `demarree_le`.
    """

    __tablename__ = "execution_saga"
    __table_args__ = (
        UniqueConstraint("locataire", "saga", "cle", name="uq_saga_cle"),
        Index("ix_saga_reprise", "locataire", "etat", "demarree_le"),
    )

    identifiant: Mapped[str] = mapped_column(String(64), primary_key=True)
    saga: Mapped[str] = mapped_column(String(60), nullable=False)
    cle: Mapped[str] = mapped_column(String(120), nullable=False)
    etat: Mapped[str] = mapped_column(String(30), nullable=False)
    tentatives: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    demarree_le: Mapped[datetime | None] = mapped_column(DateTime)
    terminee_le: Mapped[datetime | None] = mapped_column(DateTime)
    dernier_echec: Mapped[str | None] = mapped_column(Text)


class TablePassageOrdonnance(Base):
    """La mémoire de l'ordonnanceur : quand chaque travail est passé pour la dernière fois.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **ELLE N'EST PAS CLOISONNÉE, ET C'EST LA SEULE DE CE MODULE.**

    Un travail périodique n'appartient à aucun cabinet. Le relais vide la boîte de
    tout le monde, le balayage de relance parcourt tous les locataires : donner un
    `locataire` à ces lignes obligerait à choisir lequel, et la réponse serait
    fausse quel que soit le choix.

    La conséquence est à nommer parce qu'elle surprend : cette table n'a pas de
    politique de sécurité au niveau des lignes, et n'en veut pas. Le diagnostic de
    `app/infrastructure/roles.py` ne la réclame donc pas non plus, puisqu'il ne
    cherche que les tables portant une colonne `locataire`. Une table sans cloison
    n'est pas une table oubliée : c'est une table dont le contenu n'est
    l'affaire de personne en particulier.

    POURQUOI L'ÉTAT VIT ICI PLUTÔT QU'EN MÉMOIRE

    Un compte à rebours en mémoire est remis à zéro par chaque redéploiement. Un
    travail quotidien, sur une plateforme déployée chaque matin, ne passerait
    jamais — et rien ne le signalerait, puisque le processus a bien démarré.

    ⚠️ **Une seule ligne par travail**, jamais un historique. La question posée à
    chaque tour est « quand est-ce passé la dernière fois », et un historique
    obligerait à trier une table qui grossit indéfiniment pour répondre à une
    question qui tient dans une ligne. Ce qu'il faut conserver du passé va au
    journal d'exploitation, pas ici.
    ─────────────────────────────────────────────────────────────────────────────
    """

    __tablename__ = "passage_ordonnance"

    #: Le nom du travail est la clé. Deux lignes pour « relais » n'auraient aucun
    #: sens, et la contrainte l'empêche plutôt que de l'espérer.
    travail: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: Marqué au début du tour, jamais effacé. L'écart avec `termine_le` distingue
    #: « il tourne encore » de « il est mort en route ».
    debute_le: Mapped[datetime | None] = mapped_column(DateTime)
    #: Marqué à la fin, que le tour ait réussi ou échoué. C'est
    #: `echecs_consecutifs` qui porte le verdict, pas cette date.
    termine_le: Mapped[datetime | None] = mapped_column(DateTime)
    #: Remis à zéro par un succès. Commande le recul, et l'abandon au delà du
    #: seuil déclaré dans `app/orchestration/ordonnanceur.py`.
    echecs_consecutifs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dernier_echec: Mapped[str | None] = mapped_column(Text)
