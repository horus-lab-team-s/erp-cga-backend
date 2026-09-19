"""Le fil de conversation, et la contrainte qui gouverne tout le reste.

─────────────────────────────────────────────────────────────────────────────────
LA RÈGLE À CONNAÎTRE AVANT TOUTE CHOSE

**On ne peut écrire librement à un client que dans les vingt-quatre heures qui
suivent son dernier message.** Hors de cette fenêtre, seul un modèle approuvé à
l'avance par la plateforme d'envoi passe.

    ─── le client écrit ────────────────────────────────────────>
        T0                              T0 + 24 h
        │                                   │
        │◄──── message libre : permis ─────►│◄─ message libre : REFUSÉ ──
        │◄──── modèle : permis, gratuit ───►│◄─ modèle : permis, FACTURÉ ─
        │                                   │
        └── un nouvel entrant repart de zéro et rouvre tout ────────────

⚠️ **L'envoi d'un modèle ne rouvre pas la fenêtre.** Elle ne se rouvre que
lorsque le client répond. C'est pour cette raison que les modèles sont rédigés
pour appeler une réponse : un modèle qui ne fait pas écrire le client laisse la
fenêtre fermée et le suivant sera facturé aussi.

POURQUOI VINGT-QUATRE HEURES N'EST PAS UN PARAMÈTRE DU RÉFÉRENTIEL

C'est la question de départage du projet, et elle demande ici un second temps.

« Est-ce que cela varie sans le code ? » Oui : la plateforme pourrait changer sa
règle demain. La réponse s'arrêterait là, et l'on mettrait vingt-quatre heures au
référentiel. **Ce serait une erreur**, et une erreur qui ne se verrait qu'en
production.

Il faut donc une seconde question : **« et qui décide ? »**

Le centre décide de ses délais de veille, de ses honoraires, de sa grille de
routage. Il **ne décide pas** de la fenêtre de service : elle lui est imposée. La
rendre configurable ferait croire le contraire à celui qui lit le fichier. Un
responsable de pôle la porterait un jour à soixante-douze heures pour se laisser
du temps, le système accepterait, et les envois échoueraient chez la plateforme
sans que rien ici ne l'explique.

**La configuration est pour ce que le centre décide. Ce que le monde impose est
une constante, avec sa citation.** Ce qui est bien configuré ici, en revanche,
c'est le **catalogue des modèles** : leur texte, leur catégorie, leur état
d'approbation. Cela, le centre le décide et le révise.

LE PIÈGE LE PLUS COÛTEUX DU CHANTIER

Un modèle non approuvé **fonctionne dans le bac à sable et échoue en
production**. Rien ne distingue les deux à l'écriture du code. C'est pourquoi le
statut d'approbation est vérifié **ici**, avant l'appel, et pourquoi un modèle en
attente est refusé localement plutôt que tenté.

Les modèles doivent être soumis dès le début du développement, pas la veille de
la mise en service : l'approbation prend de quelques heures à quelques jours.

CE QUE CHAQUE ENVOI COÛTE

    Ce qu'on envoie              Fenêtre ouverte   Fenêtre fermée
    Message libre                gratuit           REFUSÉ
    Modèle utilitaire            gratuit           facturé
    Modèle d'authentification    facturé           facturé
    Modèle marketing             facturé           facturé (hors périmètre)

Depuis le 1er juillet 2025, la facturation est **par message** et non plus par
conversation de vingt-quatre heures. Deux conséquences de conception :

* **répondre vite est gratuit.** Un responsable qui traite le fil pendant que la
  fenêtre est ouverte ne coûte rien ; le même échange repris trois jours plus
  tard se paie en modèles ;
* **une relance automatique mal réglée coûte à chaque déclenchement.** Le
  compteur par catégorie doit être visible dès le premier jour, et pas découvert
  sur la facture.

LE FIL ET LA QUALIFICATION SONT DEUX CHOSES

Un appel est journalisé comme un événement du fil, avec sa durée et son issue.
**Son contenu ne l'est pas.** Ce que le responsable retient d'un appel, il le
saisit dans la qualification, qui est un autre objet et se remplit avec des
données typées.

Confondre les deux produirait un fil plein de notes libres, inexploitable par
quoi que ce soit d'automatique, et une qualification vide.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from enum import IntEnum, StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "AppelJournalise",
    "CanalMessage",
    "CategorieModele",
    "Direction",
    "EnvoiRefuse",
    "FENETRE_DE_SERVICE",
    "Facturation",
    "Fil",
    "IssueAppel",
    "Message",
    "ModeleDeMessage",
    "MotifRefus",
    "StatutModele",
    "StatutRemise",
    "cout_de",
    "ouvrir_un_fil",
]

#: Vingt-quatre heures. **Imposé par la plateforme d'envoi, pas décidé par le
#: centre.** Voir l'en-tête : c'est précisément pour cela que ce n'est pas un
#: paramètre du référentiel.
#:
#: Source : WhatsApp Business Platform, fenêtre de service client.
FENETRE_DE_SERVICE = timedelta(hours=24)

#: Les emplacements d'un modèle, au format de la plateforme : `{{1}}`, `{{2}}`…
_PARAMETRE = re.compile(r"\{\{(\d+)\}\}")


class MotifRefus(StrEnum):
    """Pourquoi un envoi n'a pas été préparé.

    Nommés un par un plutôt que fondus dans un message : l'appelant en affiche
    certains au responsable (« la fenêtre est fermée, employez un modèle ») et en
    remonte d'autres à l'exploitation (« ce modèle n'est pas approuvé »). Un
    message unique obligerait à le lire pour décider quoi en faire.
    """

    HORS_FENETRE = "HORS_FENETRE"
    SANS_CONSENTEMENT = "SANS_CONSENTEMENT"
    MODELE_NON_APPROUVE = "MODELE_NON_APPROUVE"
    PARAMETRES_INCOMPLETS = "PARAMETRES_INCOMPLETS"
    CATEGORIE_HORS_PERIMETRE = "CATEGORIE_HORS_PERIMETRE"


class EnvoiRefuse(ValueError):
    """Un envoi impossible, refusé **avant** l'appel à la plateforme.

    Porte son motif en attribut plutôt qu'en texte seul : le traiter par
    correspondance de chaîne serait fragile, et l'un des motifs se corrige par le
    responsable quand l'autre demande une intervention d'exploitation.
    """

    def __init__(self, motif: MotifRefus, message: str) -> None:
        super().__init__(message)
        self.motif = motif


class Direction(StrEnum):
    ENTRANT = "ENTRANT"
    SORTANT = "SORTANT"


class CanalMessage(StrEnum):
    WHATSAPP = "WHATSAPP"
    COURRIEL = "COURRIEL"


class CategorieModele(StrEnum):
    """Les catégories de la plateforme. Elles décident du tarif et des règles."""

    #: Notification liée à une transaction ou à un suivi. La grande majorité de
    #: ce que le centre envoie.
    UTILITAIRE = "UTILITAIRE"
    #: Codes et liens d'activation. Tarif et règles distincts, facturés même
    #: fenêtre ouverte.
    AUTHENTIFICATION = "AUTHENTIFICATION"
    #: Prospection. **Hors périmètre** : le centre ne démarche pas par ce canal.
    #: La catégorie existe pour que le refus soit explicite plutôt qu'implicite.
    MARKETING = "MARKETING"


class StatutModele(StrEnum):
    """L'état d'un modèle chez la plateforme.

    ⚠️ Seul `APPROUVE` permet l'envoi. Les trois autres sont refusés **ici**, et
    non tentés : un modèle non approuvé fonctionne dans le bac à sable et échoue
    en production, ce qui en fait le piège le plus coûteux du chantier.
    """

    EN_ATTENTE = "EN_ATTENTE"
    APPROUVE = "APPROUVE"
    REFUSE = "REFUSE"
    #: Retiré après approbation, par la plateforme ou par le centre.
    DESACTIVE = "DESACTIVE"


class Facturation(StrEnum):
    GRATUIT = "GRATUIT"
    FACTURE = "FACTURE"
    #: L'envoi n'est pas possible du tout.
    IMPOSSIBLE = "IMPOSSIBLE"


class StatutRemise(IntEnum):
    """L'avancement d'un message sortant, **ordonné**.

    Un `IntEnum` et non un `StrEnum`, et c'est le point : les accusés de la
    plateforme arrivent **dans le désordre**. Le rappel « lu » peut précéder le
    rappel « remis », parce que ce sont deux requêtes HTTP indépendantes.

    Assigner le statut reçu ferait donc reculer un message de « lu » à « remis »,
    et le tableau de bord annoncerait des messages non lus qui l'étaient. Le
    statut ne se pose pas, il **progresse** : voir `Message.avec_statut`.

    `ECHEC` est au sommet à dessein. Un échec définitif rapporté après un accusé
    de remise est une contradiction de la plateforme, et dans le doute il faut
    croire l'échec : un message annoncé remis qui ne l'était pas se découvre par
    un client qui n'a rien reçu.
    """

    EN_ATTENTE = 0
    ENVOYE = 1
    REMIS = 2
    LU = 3
    ECHEC = 4


class IssueAppel(StrEnum):
    REPONDU = "REPONDU"
    SANS_REPONSE = "SANS_REPONSE"
    OCCUPE = "OCCUPE"
    #: Numéro injoignable ou hors service. Distinct de « sans réponse » : celui-ci
    #: se relance, celui-là demande un autre numéro.
    INJOIGNABLE = "INJOIGNABLE"


def cout_de(categorie: CategorieModele | None, fenetre_ouverte: bool) -> Facturation:
    """Le tableau de l'en-tête, encodé une fois.

    `categorie` à `None` désigne un message libre, qui n'a pas de catégorie : il
    est gratuit dans la fenêtre et impossible en dehors.

    ⚠️ Ce tableau reproduit la tarification de la plateforme, **pas une décision
    du centre**. Il est donc du code, comme la fenêtre, et pour la même raison.
    """
    if categorie is None:
        return Facturation.GRATUIT if fenetre_ouverte else Facturation.IMPOSSIBLE
    if categorie is CategorieModele.UTILITAIRE:
        return Facturation.GRATUIT if fenetre_ouverte else Facturation.FACTURE
    return Facturation.FACTURE


class ModeleDeMessage(BaseModel):
    """Un texte soumis à l'avance à la plateforme et approuvé par elle.

    Le catalogue vit au référentiel : le texte, la catégorie et l'état
    d'approbation sont révisés par le centre, sans livraison.
    """

    model_config = ConfigDict(frozen=True)

    #: Le nom du modèle **chez la plateforme**. C'est lui qui part dans l'appel,
    #: pas le corps : la plateforme rend le texte qu'elle a approuvé.
    nom: str = Field(min_length=1)
    libelle: str = Field(min_length=1)
    langue: str = Field(default="fr", min_length=2)
    categorie: CategorieModele
    statut: StatutModele = StatutModele.EN_ATTENTE

    #: Le corps tel que soumis, avec ses emplacements `{{1}}`, `{{2}}`…
    #:
    #: Conservé bien que la plateforme rende le sien : c'est ce qui permet
    #: d'afficher le fil au responsable sans un aller-retour, et de relire six
    #: mois plus tard ce qui avait été envoyé.
    corps: str = Field(min_length=1)

    approuve_le: datetime | None = None
    #: À quoi il sert dans le parcours. Documentaire, et utile : sept modèles se
    #: ressemblent vite.
    usage: str = ""

    @model_validator(mode="after")
    def _un_modele_approuve_porte_sa_date(self) -> ModeleDeMessage:
        if self.statut is StatutModele.APPROUVE and self.approuve_le is None:
            raise ValueError(
                f"modèle {self.nom} déclaré APPROUVE sans date d'approbation. "
                "La date est ce qui permet de savoir si le texte envoyé est bien "
                "celui qui a été soumis, ou s'il a été retouché depuis."
            )
        return self

    @property
    def parametres_attendus(self) -> int:
        """Le plus grand numéro d'emplacement cité.

        Le **plus grand**, et non le nombre d'emplacements distincts : un corps
        qui cite `{{1}}` et `{{3}}` en attend trois, dont un inutilisé. La
        plateforme les compte ainsi, et compter autrement ferait échouer l'envoi
        pour une raison qu'aucun message local n'expliquerait.
        """
        numeros = [int(n) for n in _PARAMETRE.findall(self.corps)]
        return max(numeros, default=0)

    @property
    def envoyable(self) -> bool:
        return self.statut is StatutModele.APPROUVE

    def rendre(self, parametres: tuple[str, ...]) -> str:
        """Le corps, emplacements remplis. Pour l'affichage du fil, pas pour l'envoi.

        Refuse un nombre de paramètres qui ne correspond pas. La plateforme le
        refuserait aussi, mais trois secondes plus tard, dans un message de son
        vocabulaire, sur un envoi déjà compté par le quota.
        """
        attendus = self.parametres_attendus
        if len(parametres) != attendus:
            raise EnvoiRefuse(
                MotifRefus.PARAMETRES_INCOMPLETS,
                f"modèle {self.nom} : {attendus} paramètre(s) attendu(s), "
                f"{len(parametres)} fourni(s). La plateforme refuserait l'envoi "
                "en comptant le quota.",
            )
        rendu = self.corps
        for rang, valeur in enumerate(parametres, start=1):
            rendu = rendu.replace(f"{{{{{rang}}}}}", valeur)
        return rendu


class Message(BaseModel):
    """Un message du fil, entrant ou sortant. Figé une fois posé.

    Le seul changement admis est l'avancement de son statut de remise, et il ne
    va que dans un sens. Tout le reste est un fait : ce qui a été écrit, quand, et
    par quel canal.
    """

    model_config = ConfigDict(frozen=True)

    identifiant: str = Field(min_length=1)
    direction: Direction
    canal: CanalMessage
    a_l_instant: datetime
    corps: str = ""

    #: Le nom du modèle employé, ou `None` pour un message libre.
    modele: str | None = None
    categorie: CategorieModele | None = None
    facturation: Facturation = Facturation.GRATUIT

    statut: StatutRemise = StatutRemise.EN_ATTENTE
    #: L'identifiant du message chez la plateforme. C'est la clé d'idempotence des
    #: rappels entrants : le même rappel rejoué ne doit rien écrire deux fois.
    identifiant_externe: str | None = None

    @property
    def libre(self) -> bool:
        return self.modele is None

    def avec_statut(self, statut: StatutRemise) -> Message:
        """Fait progresser le statut. **Ne le fait jamais reculer.**

        Les accusés de la plateforme arrivent dans le désordre : « lu » peut
        précéder « remis », ce sont deux requêtes indépendantes. Assigner ferait
        reculer le message, et le tableau de bord annoncerait non lus des
        messages qui l'étaient.

        Rejouable par construction : un accusé déjà reçu laisse l'objet
        inchangé, ce dont les rappels rejoués ont besoin.
        """
        if statut <= self.statut:
            return self
        return self.model_copy(update={"statut": statut})


class AppelJournalise(BaseModel):
    """Un appel : qu'il a eu lieu, combien de temps, et comment il s'est terminé.

    ⚠️ **Son contenu n'est pas ici, et n'y sera jamais.** Ce que le responsable
    retient d'un appel se saisit dans la qualification, en données typées. Un
    champ de notes libres ici produirait un fil inexploitable par quoi que ce soit
    d'automatique, et une qualification vide.
    """

    model_config = ConfigDict(frozen=True)

    identifiant: str = Field(min_length=1)
    a_l_instant: datetime
    duree_secondes: int = Field(default=0, ge=0)
    issue: IssueAppel

    @model_validator(mode="after")
    def _une_duree_suppose_une_reponse(self) -> AppelJournalise:
        if self.issue is not IssueAppel.REPONDU and self.duree_secondes:
            raise ValueError(
                f"appel {self.identifiant} : une durée non nulle sur un appel "
                f"{self.issue}. Compter les sonneries comme du temps d'échange "
                "fausserait l'indicateur qui sert à mesurer la charge réelle."
            )
        return self


class Fil(BaseModel):
    """L'échange avec un prospect, tous canaux confondus.

    Un fil par dossier. Les canaux cohabitent dedans, parce que c'est ainsi qu'un
    responsable lit une relation : il se souvient d'avoir appelé mardi et écrit
    jeudi, pas d'avoir eu deux conversations parallèles.

    ⚠️ **La fenêtre de service, elle, ne concerne que la messagerie
    instantanée.** Un courriel n'ouvre rien et ne ferme rien.
    """

    model_config = ConfigDict(frozen=True)

    dossier: str = Field(min_length=1)
    #: Forme canonique. C'est sur ce numéro que porte la fenêtre.
    telephone: str = Field(min_length=9)

    messages: tuple[Message, ...] = ()
    appels: tuple[AppelJournalise, ...] = ()

    # ── Ce que le fil sait dire de lui-même ─────────────────────────────────

    @property
    def dernier_entrant(self) -> datetime | None:
        """Le dernier message **du client sur la messagerie instantanée**.

        Deux restrictions, et chacune compte. Un message sortant n'ouvre rien :
        l'envoi d'un modèle ne rouvre pas la fenêtre, seule une réponse le fait.
        Un courriel n'ouvre rien non plus : la plateforme d'envoi ne le voit pas.
        """
        entrants = [
            m.a_l_instant
            for m in self.messages
            if m.direction is Direction.ENTRANT and m.canal is CanalMessage.WHATSAPP
        ]
        return max(entrants, default=None)

    def fenetre_ouverte(self, a_l_instant: datetime) -> bool:
        """Sommes-nous dans les vingt-quatre heures qui suivent son dernier mot ?

        Un fil dont le client n'a jamais écrit a la fenêtre **fermée**, et c'est
        le cas de tout premier contact : c'est pourquoi le premier message d'une
        relation part nécessairement d'un modèle.
        """
        dernier = self.dernier_entrant
        if dernier is None:
            return False
        return a_l_instant - dernier < FENETRE_DE_SERVICE

    def expire_le(self) -> datetime | None:
        """Quand la fenêtre se referme, pour l'afficher au responsable.

        Un compte à rebours visible change le comportement : on répond avant que
        cela devienne payant. Un système qui refuse sans avoir prévenu se fait
        contourner.
        """
        dernier = self.dernier_entrant
        return None if dernier is None else dernier + FENETRE_DE_SERVICE

    @property
    def a_recu_du_client(self) -> bool:
        return self.dernier_entrant is not None

    # ── Ce qu'on peut envoyer, et à quel prix ───────────────────────────────

    def preparer_message_libre(
        self,
        identifiant: str,
        corps: str,
        a_l_instant: datetime,
        *,
        canal: CanalMessage,
        consentement_vaut: bool = True,
    ) -> Message:
        """Un message écrit librement. Sur la messagerie, refusé hors fenêtre.

        ⚠️ **Le canal est obligatoire et n'a pas de valeur par défaut.** Il en
        avait une — la messagerie — et c'était le couplage à retirer : un défaut
        qui désigne le canal le plus contraint fait écrire du code qui ne
        fonctionne que si une plateforme tierce est prête. Le nommer à chaque
        appel oblige à savoir lequel on emprunte.

        Le refus, quand il tombe, est **ici**, avant l'appel. La plateforme
        refuserait aussi, mais après un aller-retour réseau, dans son
        vocabulaire, et le responsable verrait un échec technique là où il y a
        une règle simple à lui expliquer.
        """
        if canal is CanalMessage.WHATSAPP:
            self._exiger_le_consentement(consentement_vaut)
            if not self.fenetre_ouverte(a_l_instant):
                raise EnvoiRefuse(
                    MotifRefus.HORS_FENETRE,
                    f"fil {self.dossier} : la fenêtre de service est fermée. Un "
                    "message libre ne passe pas ; employez un modèle approuvé, "
                    "rédigé pour appeler une réponse.",
                )
        return Message(
            identifiant=identifiant,
            direction=Direction.SORTANT,
            canal=canal,
            a_l_instant=a_l_instant,
            corps=corps,
            facturation=cout_de(None, self.fenetre_ouverte(a_l_instant))
            if canal is CanalMessage.WHATSAPP
            else Facturation.GRATUIT,
        )

    def preparer_modele(
        self,
        identifiant: str,
        modele: ModeleDeMessage,
        parametres: tuple[str, ...],
        a_l_instant: datetime,
        *,
        consentement_vaut: bool,
    ) -> Message:
        """Un modèle approuvé. Passe dans la fenêtre comme en dehors.

        Trois refus possibles, et chacun se corrige par quelqu'un de différent :
        le consentement par le client, l'approbation par l'exploitation, les
        paramètres par l'appelant. C'est pourquoi les motifs sont distincts.
        """
        self._exiger_le_consentement(consentement_vaut)
        if not modele.envoyable:
            raise EnvoiRefuse(
                MotifRefus.MODELE_NON_APPROUVE,
                f"modèle {modele.nom} à l'état {modele.statut} : la plateforme le "
                "refuserait. ⚠️ Il fonctionne pourtant dans le bac à sable, ce qui "
                "rend ce défaut invisible jusqu'à la mise en service.",
            )
        if modele.categorie is CategorieModele.MARKETING:
            raise EnvoiRefuse(
                MotifRefus.CATEGORIE_HORS_PERIMETRE,
                f"modèle {modele.nom} de catégorie MARKETING. Le centre ne "
                "démarche pas par ce canal ; le refus est ici pour qu'il soit une "
                "décision lisible plutôt qu'une absence de fichier.",
            )
        # Lève `PARAMETRES_INCOMPLETS` si le compte ne correspond pas.
        corps = modele.rendre(parametres)

        return Message(
            identifiant=identifiant,
            direction=Direction.SORTANT,
            canal=CanalMessage.WHATSAPP,
            a_l_instant=a_l_instant,
            corps=corps,
            modele=modele.nom,
            categorie=modele.categorie,
            facturation=cout_de(modele.categorie, self.fenetre_ouverte(a_l_instant)),
        )

    def _exiger_le_consentement(self, consentement_vaut: bool) -> None:
        if not consentement_vaut:
            raise EnvoiRefuse(
                MotifRefus.SANS_CONSENTEMENT,
                f"fil {self.dossier} : le consentement à être contacté sur cette "
                "messagerie n'est pas en vigueur. Il a été refusé au dépôt, ou "
                "révoqué depuis. L'appel et le courriel restent ouverts.",
            )

    # ── Ce qui s'ajoute au fil ──────────────────────────────────────────────

    def avec(self, message: Message) -> Fil:
        """Ajoute un message. **Rejouable sur l'identifiant.**

        Les rappels de la plateforme sont rejoués : c'est son mécanisme de
        reprise, pas une anomalie. Un entrant écrit deux fois apparaîtrait deux
        fois dans le fil et, pire, prolongerait la fenêtre à chaque rejeu.
        """
        if any(m.identifiant == message.identifiant for m in self.messages):
            return self
        return self.model_copy(update={"messages": (*self.messages, message)})

    def avec_appel(self, appel: AppelJournalise) -> Fil:
        """Journalise un appel. Rejouable de la même façon."""
        if any(a.identifiant == appel.identifiant for a in self.appels):
            return self
        return self.model_copy(update={"appels": (*self.appels, appel)})

    def avec_statut(self, identifiant: str, statut: StatutRemise) -> Fil:
        """Fait progresser le statut d'un message sortant.

        Un identifiant inconnu laisse le fil inchangé plutôt que de lever : un
        accusé peut arriver pour un message d'un autre fil, ou pour un message
        qu'une reprise a effacé. Lever ferait échouer le rappel, que la
        plateforme rejouerait, indéfiniment.
        """
        return self.model_copy(
            update={
                "messages": tuple(
                    m.avec_statut(statut) if m.identifiant == identifiant else m
                    for m in self.messages
                )
            }
        )

    # ── Ce que le fil rend au reste du système ──────────────────────────────

    @property
    def dernier_echange(self) -> datetime | None:
        """Le plus récent des messages et des appels, quel que soit le sens.

        C'est ce que la veille des sept jours regarde. Prendre le seul dernier
        entrant ferait relancer un client à qui l'on vient d'écrire.
        """
        instants = [m.a_l_instant for m in self.messages]
        instants += [a.a_l_instant for a in self.appels]
        return max(instants, default=None)

    def envois_factures(self) -> tuple[Message, ...]:
        """Ce que le fil a coûté. Le compteur doit être visible dès le premier
        jour, et pas découvert sur la facture."""
        return tuple(
            m
            for m in self.messages
            if m.direction is Direction.SORTANT and m.facturation is Facturation.FACTURE
        )


def ouvrir_un_fil(dossier: str, telephone: str) -> Fil:
    """Un fil vide, fenêtre fermée.

    Fermée, et c'est le cas normal : le client n'a encore rien écrit. Le premier
    message d'une relation part donc nécessairement d'un modèle, et c'est la
    contrainte qui explique pourquoi sept modèles doivent être approuvés avant la
    mise en service.
    """
    return Fil(dossier=dossier, telephone=telephone)
