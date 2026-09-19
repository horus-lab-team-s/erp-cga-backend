"""Les ports du contexte K.

Des `Protocol`, pas des classes de base : un adaptateur n'hérite de rien, il se
contente de présenter les bonnes signatures. C'est la convention des onze autres
contextes, et elle évite qu'une hiérarchie d'implémentations ne se forme.

─────────────────────────────────────────────────────────────────────────────────
CE QUI EST ABSENT DE CES INTERFACES, ET POURQUOI

**Aucun paramètre `locataire`.** Décision reprise du contexte B : un filtre qu'il
faut penser à passer est un filtre qu'on oubliera, et l'oubli ne se voit pas — la
requête rend simplement des lignes en trop. Le cloisonnement appartient donc à
l'instance du dépôt, et en base à un filtre appliqué à la session.

**Aucune méthode `supprimer`, nulle part.** Ni sur les comptes — leur identifiant
figure dans les écritures qu'ils ont validées —, ni sur les habilitations — leur
historique est ce qui prouve qui pouvait quoi —, ni sur le journal d'audit, dont
c'est la garantie même. Ce qui cesse se ferme ou se suspend ; rien ne s'efface.

**Aucune méthode `retirer_role`.** Voir `habilitations.py` : on ferme une
habilitation à une date, on ne la retire pas.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from app.contextes.transverse.domaine.audit import EntreeAudit
from app.contextes.transverse.domaine.habilitations import Habilitation
from app.contextes.transverse.domaine.identites import Compte
from app.contextes.transverse.domaine.jetons import Jeton
from app.contextes.transverse.domaine.mandats import Mandat
from app.contextes.transverse.domaine.sessions import Session
from app.contextes.transverse.domaine.teledeclaration import (
    AccuseReception,
    DocumentATransmettre,
)

__all__ = [
    "DepotComptes",
    "DepotHabilitations",
    "DepotJetons",
    "DepotLectures",
    "DepotSessions",
    "JournalAudit",
    "PortailDeclaratif",
    "ServiceEmpreinte",
    "ServiceNotification",
]


class DepotComptes(Protocol):
    """Les identités du locataire."""

    def lire(self, identifiant: str) -> Compte:
        """Lève si le compte n'existe pas."""
        ...

    def par_courriel(self, courriel: str) -> Compte | None:
        """`None` plutôt qu'une exception : à l'ouverture de session, l'absence
        est un cas courant qu'il ne faut surtout pas distinguer d'un mot de passe
        faux. L'appelant reçoit `None` et rend la même réponse dans les deux cas."""
        ...

    def enregistrer(self, compte: Compte) -> None:
        """Insère ou remplace. Doit refuser deux comptes de même courriel : c'est
        l'identifiant de connexion, et deux porteurs rendraient l'un des deux
        inaccessible sans message d'erreur."""
        ...

    def lister(self) -> list[Compte]: ...


class DepotHabilitations(Protocol):
    def pour_compte(self, compte: str) -> list[Habilitation]:
        """**Toutes** les habilitations, fermées comprises.

        Rendre seulement les actives serait une erreur de conception : la question
        « qui était habilité le 12 mars » ne se répond qu'avec l'historique
        complet, et un dépôt qui filtre prive l'appelant d'une information qu'il
        ne peut plus retrouver."""
        ...

    def pour_dossier(self, niu: str) -> list[Habilitation]:
        """Qui a accès à ce dossier — l'écran d'affectation le demande."""
        ...

    def lire(self, identifiant: str) -> Habilitation: ...

    def enregistrer(self, habilitation: Habilitation) -> None: ...


class DepotJetons(Protocol):
    def par_empreinte(self, empreinte: str) -> Jeton | None:
        """La recherche se fait sur l'empreinte, jamais sur le secret : celui-ci
        n'est pas stocké. L'appelant reçoit le secret du lien, en calcule
        l'empreinte, et interroge avec elle."""
        ...

    def pour_compte(self, compte: str) -> list[Jeton]: ...

    def enregistrer(self, jeton: Jeton) -> None: ...


class DepotSessions(Protocol):
    def lire(self, identifiant: str) -> Session | None: ...

    def pour_compte(self, compte: str) -> list[Session]:
        """Sert à révoquer d'un coup toutes les sessions d'un compte suspendu ou
        dont le mot de passe vient de changer."""
        ...

    def enregistrer(self, session: Session) -> None: ...


class DepotMandats(Protocol):
    """Les mandats **reçus** par ce locataire, c'est-à-dire ceux qu'il a accordés.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **CLOISONNÉ SUR LE MANDANT, ET NON SUR LE MANDATAIRE.**

    Un mandat met en jeu les données du mandant : c'est lui qui l'accorde, lui qui le
    retire, et c'est dans son périmètre que la vérification a lieu. Le ranger chez le
    mandataire obligerait à sortir du périmètre servi pour savoir si l'on a le droit d'y
    entrer, ce qui est exactement l'ordre inverse de celui qu'on veut.

    La contrepartie est connue : un centre qui voudrait la liste des mandats qu'il détient
    devrait interroger chaque mandant. Ce besoin n'existe pas encore, et l'inventer ici
    aurait coûté une seconde table à tenir d'accord avec la première.
    ─────────────────────────────────────────────────────────────────────────────
    """

    def lire(self, identifiant: str) -> Mandat | None: ...

    def tous(self) -> list[Mandat]:
        """Tous les mandats accordés par ce locataire, révoqués et expirés compris.

        ⚠️ **Sans filtre de validité.** La décision « ce mandat s'applique-t-il » appartient
        au domaine, qui la prend à une date donnée et sait dire *pourquoi* elle est non.
        Filtrer ici rendrait ce diagnostic impossible, et le dépôt déciderait à la place du
        domaine ce qu'est un mandat vivant.
        """
        ...

    def enregistrer(self, mandat: Mandat) -> None: ...


class JournalAudit(Protocol):
    """Append-only. Ni `modifier`, ni `supprimer` — voir `audit.py`."""

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
        """Chaîne une entrée à la suite de la précédente et la rend.

        ⚠️ **Garantie exigée : lecture de la tête et écriture doivent être
        atomiques.** Deux ajouts concurrents qui liraient la même tête
        produiraient deux entrées de même rang chaînées au même prédécesseur, et
        `verifier_chaine` signalerait dès lors une altération qui n'en est pas
        une. Une réalisation en mémoire ne peut pas le garantir ; une table
        PostgreSQL le fera par un verrou sur la tête ou une contrainte d'unicité
        sur le rang."""
        ...

    def tete(self) -> EntreeAudit | None:
        """La dernière entrée, ou `None` si le journal est vierge."""
        ...

    def lister(
        self,
        *,
        acteur: str | None = None,
        objet_type: str | None = None,
        objet_id: str | None = None,
        depuis: datetime | None = None,
    ) -> list[EntreeAudit]:
        """Dans l'ordre des rangs. Un journal rendu à l'envers ou trié par date
        ne se vérifie pas : c'est la séquence qui porte la garantie."""
        ...


class ServiceEmpreinte(Protocol):
    """La dérivation du mot de passe, tenue hors du domaine.

    Les paramètres d'Argon2 — coût mémoire, nombre de passes, parallélisme — se
    règlent en fonction du matériel du serveur. C'est une préoccupation
    d'infrastructure, et le domaine n'a pas à changer le jour où l'on double la
    mémoire allouée.
    """

    def deriver(self, mot_de_passe: str) -> str: ...

    def verifier(self, mot_de_passe: str, empreinte: str) -> bool:
        """Ne lève jamais sur une empreinte mal formée : rend `False`. Une
        exception distinguerait « empreinte corrompue » de « mot de passe faux »,
        et cette différence se lit dans les temps de réponse."""
        ...

    def a_rederiver(self, empreinte: str) -> bool:
        """L'empreinte a été produite avec des paramètres devenus insuffisants.

        Le mot de passe n'étant connu qu'à l'instant de la connexion, c'est le
        seul moment où l'on peut redériver. Sans ce signal, un durcissement des
        paramètres ne profiterait qu'aux comptes créés après."""
        ...


class ServiceNotification(Protocol):
    """L'envoi d'un courriel transactionnel.

    Le domaine ne connaît ni Brevo, ni SMTP, ni gabarit HTML : il demande qu'un
    message identifié par un code parte vers une adresse, avec un contexte. Le
    module `mail+paiement/` du dépôt fournit exactement cette forme — un catalogue
    de gabarits en base, un code, un contexte —, ce qui rendra le branchement
    direct.

    ⚠️ Ne doit **jamais** lever : un courriel qui échoue ne doit pas annuler la
    création du compte ni le paiement qui l'a déclenchée. L'échec se journalise et
    se rejoue.
    """

    def envoyer(
        self,
        code: str,
        *,
        destinataire: str,
        contexte: dict[str, Any],
    ) -> bool:
        """Rend `True` si le message est parti. Voir l'avertissement ci-dessus."""
        ...


class PortailDeclaratif(Protocol):
    """Le guichet auquel on dépose une déclaration.

    ─────────────────────────────────────────────────────────────────────────
    `depose_automatiquement()` COMMANDE TOUT LE PARCOURS

    Ce n'est pas un détail d'implémentation que l'appelant pourrait ignorer.
    Les deux modes produisent des parcours **différents** :

    * **Automatique** — on appelle `deposer`, on reçoit un accusé, l'obligation
      passe à déclarée. Un aller-retour.
    * **Manuel** — la plateforme produit un bordereau, un humain va sur le
      portail, saisit, obtient un numéro, et **revient le saisir**. Trois
      étapes, dont deux hors du système.

    Masquer cette différence derrière une méthode `deposer` qui attendrait un
    humain serait un mensonge d'interface. L'appelant demande, et adapte son
    parcours.

    AUCUNE MÉTHODE NE REND `None` POUR DIRE « ÇA A ÉCHOUÉ »

    `deposer` rend un accusé ou lève. Un `None` silencieux laisserait
    l'obligation dans un état indéterminé — ni déposée, ni en échec — et c'est
    exactement l'état dans lequel un dossier se perd.
    ─────────────────────────────────────────────────────────────────────────
    """

    def depose_automatiquement(self) -> bool:
        """Le guichet accepte-t-il un dépôt sans intervention humaine ?

        ⚠️ Faux aujourd'hui pour la DGI : aucune interface programmatique n'est
        publiée. Voir l'en-tête de `teledeclaration.py`.
        """
        ...

    def deposer(self, document: DocumentATransmettre, *, par: str) -> AccuseReception:
        """Dépose et rend l'accusé. Lève `DepotRefuseParLePortail` sur un refus.

        Ne doit **jamais** être appelée quand `depose_automatiquement()` est
        faux : la réalisation manuelle lève, et c'est voulu — l'appeler est un
        défaut de parcours, pas un cas limite.
        """
        ...

    def enregistrer_accuse(
        self, accuse: AccuseReception, *, document: DocumentATransmettre
    ) -> AccuseReception:
        """Consigne un accusé obtenu hors du système.

        C'est le geste du mode manuel : le réviseur revient avec un numéro. La
        réalisation **doit vérifier** que l'accusé porte sur ce document — même
        référence, même empreinte —, sans quoi un numéro recopié dans la
        mauvaise ligne attesterait d'un dépôt qui n'a pas eu lieu.
        """
        ...

    def tous(self) -> list[AccuseReception]:
        """Tous les accusés consignés, visibles du locataire.

        ⚠️ Déclaré au port au pas 58 : les deux réalisations l'avaient, et l'échéancier
        en a besoin pour dire, en une lecture, quelles obligations sont déposées.
        """
        ...

    def retrouver(self, reference_document: str) -> AccuseReception | None:
        """L'accusé déjà enregistré pour ce document, s'il existe.

        C'est ce qui rend le dépôt idempotent : redéposer la TVA de juillet doit
        se heurter au premier dépôt. Ici, `None` est une réponse — l'absence
        d'accusé est un fait, pas un échec.
        """
        ...


#: ⚠️ Pas 104 : ce port vivait dans son adaptateur, `lectures_des_notifications.py`.
#: `test_conformite_des_ports` ne lit que `domaine/ports.py` : ses réalisations en
#: mémoire et SQL n'étaient jamais confrontées.
class DepotLectures(Protocol):
    def lu_jusqu_au_rang(self, compte: str) -> int | None: ...

    def avancer(self, compte: str, rang: int) -> int:
        """Porte la position à `rang` **si elle avance**, et rend la position retenue.

        ⚠️ Jamais en arrière : deux onglets ouverts qui marquent « tout lu » à des
        instants différents ne doivent pas rendre non lu ce que le premier a lu.
        """
        ...
