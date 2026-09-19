"""Le dossier commercial : huit états entre une intention et un client.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI UN OBJET DE PLUS, ALORS QUE LA DEMANDE EXISTE DÉJÀ

Parce qu'ils n'ont pas la même durée de vie ni la même nature.

La `DemandeDeContact` est **figée** : c'est ce que le visiteur a écrit, un
vendredi soir, et cela ne changera plus jamais. Le dossier commercial, lui, est
ce que le cabinet en fait, et il change à chaque échange.

Les fondre en un seul objet obligerait à réécrire la demande à mesure que le
dossier avance, et ferait disparaître ce que le client avait réellement dit.
C'est souvent la seule chose qui explique un malentendu trois semaines plus tard.

Un dossier porte donc sa demande, sans la modifier jamais.

LES HUIT ÉTATS, ET LE NEUVIÈME

    DÉPOSÉE ──> AFFECTÉE ──> EN_CONVERSATION ──> QUALIFIÉE ──> CHIFFRÉE
                    ↑↺             ↑  ↑              │            │
                    │              │  └──────────────┘            │
                    │              │                              ↓
                    │              └──────────────────────  PROFORMA_ÉMISE
                    │                     ↑                       │
                 (réaffectation)          │                       ↓
                                          └───────────────── ACCEPTÉE ──> PAYÉE
                                          (impayée à 30 jours)

    Depuis presque partout : ──> SANS_SUITE

Les huit premiers sont le chemin nominal. **SANS_SUITE** n'en fait pas partie et
c'est voulu : quitter le parcours est une seule chose, quel que soit l'endroit où
on le quitte, et lui donner un état par étape multiplierait les cas sans rien
apprendre à personne.

TROIS PROPRIÉTÉS DU GRAPHE MÉRITENT D'ÊTRE LUES

**On ne classe pas sans suite ce qui a été accepté.** Une proforma acceptée qui
reste impayée retourne en conversation, elle ne disparaît pas. Le client s'est
engagé ; le cabinet lui redemande, il ne l'oublie pas. C'est la seule différence
notable entre `ACCEPTÉE` et les états qui la précèdent.

**PAYÉE est terminal.** Rien n'en repart, parce que la suite n'appartient plus au
commerce : l'ouverture du tenant s'enchaîne, et elle relève du contexte N.

**SANS_SUITE est terminal aussi.** Un client qui revient ne rouvre pas un dossier
classé, il dépose une nouvelle demande. Rouvrir demanderait de décider chez quel
responsable, avec quelle ancienneté, sous quelle veille, et aucune de ces trois
réponses n'est évidente. Déposer de nouveau les rend toutes les trois triviales,
et la détection de doublon fait le lien quand il y en a un.

⚠️ LES DÉLAIS DE VEILLE NE SONT PAS DANS CE FICHIER, ET C'EST LE POINT

Chaque état a son délai : deux heures pour être affecté, vingt-quatre heures pour
être contacté, sept jours sans échange avant relance. Aucun de ces nombres
n'apparaît ici.

« Est-ce que cela varie sans le code ? » Oui, et vite. Le centre changera ces
délais après trois mois d'usage réel, un mardi matin, et ce changement ne doit
demander ni déploiement ni développeur. Ils sont donc des paramètres datés du
référentiel, passés à `en_souffrance` en argument.

Ce qui reste ici est la **forme** de la règle, qui elle ne varie pas : un dossier
est en souffrance quand il est resté trop longtemps dans son état. Ce qui vaut
« trop » est ailleurs.

C'est le principe de configuration appliqué au plus banal des besoins, et c'est
précisément là qu'on l'oublie le plus souvent.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.contextes.souscription.domaine.demande_de_contact import DemandeDeContact

__all__ = [
    "DossierCommercial",
    "EtatDossier",
    "ETATS_NOMINAUX",
    "REAFFECTATIONS_MAXIMALES",
    "TRANSITIONS",
    "TransitionDossierRefusee",
    "ouvrir_un_dossier",
]


class TransitionDossierRefusee(ValueError):
    """Le passage demandé n'existe pas depuis l'état courant."""


class EtatDossier(StrEnum):
    DEPOSEE = "DEPOSEE"
    AFFECTEE = "AFFECTEE"
    EN_CONVERSATION = "EN_CONVERSATION"
    QUALIFIEE = "QUALIFIEE"
    CHIFFREE = "CHIFFREE"
    PROFORMA_EMISE = "PROFORMA_EMISE"
    ACCEPTEE = "ACCEPTEE"
    PAYEE = "PAYEE"
    #: Hors du chemin nominal. Voir l'en-tête.
    SANS_SUITE = "SANS_SUITE"


#: Le chemin nominal, dans l'ordre. Sert à mesurer l'avancement et à ordonner un
#: tableau de bord, jamais à décider d'une transition : c'est `TRANSITIONS` qui
#: décide, et lui seul.
ETATS_NOMINAUX: tuple[EtatDossier, ...] = (
    EtatDossier.DEPOSEE,
    EtatDossier.AFFECTEE,
    EtatDossier.EN_CONVERSATION,
    EtatDossier.QUALIFIEE,
    EtatDossier.CHIFFREE,
    EtatDossier.PROFORMA_EMISE,
    EtatDossier.ACCEPTEE,
    EtatDossier.PAYEE,
)

#: Le graphe, déclaré en un seul endroit et lisible d'un coup d'oeil.
#:
#: Il est en code et non au référentiel, contrairement aux délais, parce qu'il ne
#: varie pas sans code : chaque arête suppose quelque chose qui l'emprunte. Une
#: transition vers `PROFORMA_EMISE` suppose un générateur de proforma ; ajouter
#: l'arête sans lui ne produirait qu'un état où le dossier se bloque.
TRANSITIONS: Mapping[EtatDossier, frozenset[EtatDossier]] = {
    EtatDossier.DEPOSEE: frozenset({EtatDossier.AFFECTEE, EtatDossier.SANS_SUITE}),
    # Vers lui-même : la réaffectation, automatique après vingt-quatre heures sans
    # contact, ou décidée par un responsable de pôle.
    EtatDossier.AFFECTEE: frozenset(
        {EtatDossier.AFFECTEE, EtatDossier.EN_CONVERSATION, EtatDossier.SANS_SUITE}
    ),
    EtatDossier.EN_CONVERSATION: frozenset(
        {EtatDossier.QUALIFIEE, EtatDossier.SANS_SUITE}
    ),
    EtatDossier.QUALIFIEE: frozenset(
        {EtatDossier.CHIFFREE, EtatDossier.EN_CONVERSATION, EtatDossier.SANS_SUITE}
    ),
    EtatDossier.CHIFFREE: frozenset(
        {
            EtatDossier.PROFORMA_EMISE,
            EtatDossier.EN_CONVERSATION,
            EtatDossier.SANS_SUITE,
        }
    ),
    EtatDossier.PROFORMA_EMISE: frozenset(
        {EtatDossier.ACCEPTEE, EtatDossier.EN_CONVERSATION, EtatDossier.SANS_SUITE}
    ),
    # Pas de SANS_SUITE : voir l'en-tête. Un engagement impayé se relance.
    EtatDossier.ACCEPTEE: frozenset({EtatDossier.PAYEE, EtatDossier.EN_CONVERSATION}),
    EtatDossier.PAYEE: frozenset(),
    EtatDossier.SANS_SUITE: frozenset(),
}

#: Au-delà, la réaffectation automatique tourne en rond et il faut un humain.
#: Le même garde-fou que les reprises d'ouverture du contexte N, pour la même
#: raison : une boucle qui se rejoue indéfiniment ne se voit pas, elle se
#: découvre au moment où l'on cherche pourquoi personne n'a rappelé.
REAFFECTATIONS_MAXIMALES = 3


class DossierCommercial(BaseModel):
    """Ce que le cabinet fait d'une demande, de son dépôt à son paiement."""

    model_config = ConfigDict(frozen=True)

    reference: str = Field(min_length=1)

    #: Portée, jamais modifiée. Voir l'en-tête.
    demande: DemandeDeContact

    #: Les dépôts suivants du même numéro dans la fenêtre anti-doublon.
    #:
    #: Un visiteur qui soumet deux fois n'a pas besoin de deux responsables, mais
    #: son second message n'est pas du bruit pour autant : c'est souvent la
    #: précision qu'il revenait ajouter. Jeter la seconde demande ferait
    #: disparaître ce champ libre, et le responsable ne saurait jamais qu'elle
    #: est arrivée.
    #:
    #: Un tuple, et non une liste : le modèle est figé, et une liste par défaut
    #: resterait modifiable sur place malgré le gel.
    rattachees: tuple[DemandeDeContact, ...] = ()

    etat: EtatDossier = EtatDossier.DEPOSEE

    #: Quand le dossier est entré dans l'état où il se trouve. C'est sur cette
    #: date que porte la veille, et non sur la date de dépôt : un dossier qui
    #: avance ne doit pas déclencher l'alerte de l'étape qu'il a quittée.
    depuis_le: datetime

    #: `None` tant que personne n'a été désigné, et c'est justement ce que la
    #: première alerte cherche.
    responsable: str | None = None

    #: Pourquoi ce responsable. Conservé pour audit, parce que la règle
    #: d'affectation vit au référentiel et changera : sans le motif, on ne saura
    #: pas selon quelle version un dossier a été routé.
    motif_affectation: str | None = None

    reaffectations: int = Field(default=0, ge=0)

    #: Ceux qui ont tenu ce dossier et l'ont laissé partir sans rien enregistrer.
    #:
    #: ─────────────────────────────────────────────────────────────────────────
    #: ⚠️ **SANS CETTE MÉMOIRE, LA REPRISE TOURNE ENTRE DEUX PERSONNES.**
    #:
    #: `reaffecter` n'écartait que le titulaire du moment. La grille choisit le
    #: moins chargé, et celui qui vient de rendre le dossier redevient le moins
    #: chargé : mesuré sur cinq collaborateurs équivalents, les trois reprises
    #: allaient à **deux** d'entre eux, alpha puis beta puis alpha puis beta, et
    #: les trois autres n'étaient jamais sollicités.
    #:
    #: Le refus du domaine le disait déjà, sans l'empêcher : « au-delà, la règle
    #: d'affectation tourne en rond ». Elle tournait en rond bien avant.
    #:
    #: ⚠️ **Non promu en colonne.** Rien ne le requête : il est lu sur un dossier
    #: déjà chargé, au moment de choisir. Une colonne serait une migration et un
    #: index à décider pour une lecture qui ne coûte rien.
    #: ─────────────────────────────────────────────────────────────────────────
    responsables_passes: tuple[str, ...] = ()

    affecte_le: datetime | None = None
    #: Le premier échange réellement enregistré, quel que soit le canal. C'est
    #: l'indicateur de réactivité du cabinet, et il ne se recalcule pas.
    premier_echange_le: datetime | None = None
    payee_le: datetime | None = None

    clos_le: datetime | None = None
    motif_cloture: str | None = None

    #: L'adresse du futur espace du client — le sous-domaine qui répondra.
    #:
    #: ─────────────────────────────────────────────────────────────────────────
    #: ⚠️ **RETENU AVANT LE PAIEMENT, ET C'EST CE QUI PERMET DE L'AUTOMATISER.**
    #:
    #: Le slug est *choisi par un humain, jamais dérivé du nom* : c'est une
    #: adresse, elle se communique, et on n'en change pas. Tant que
    #: l'encaissement était saisi à la main, le choix pouvait se faire au moment
    #: de la saisie.
    #:
    #: Dès lors que l'opérateur notifie le règlement tout seul, plus personne
    #: n'est là pour choisir : il faut donc que le slug soit **déjà retenu** quand
    #: la notification arrive. Il l'est au moment où le règlement est demandé au
    #: client, par le collaborateur qui prépare l'espace.
    #:
    #: `None` veut dire « aucun règlement n'a encore été demandé ». Un
    #: encaissement notifié sur un dossier sans slug ne peut pas ouvrir de tenant,
    #: et le dit plutôt que d'en inventer un.
    #:
    #: ⚠️ Non promu en colonne : rien ne le requête, il est lu sur un dossier déjà
    #: chargé. L'unicité du slug est tenue par la table `tenant`, qui est le seul
    #: endroit où elle a un sens.
    #: ─────────────────────────────────────────────────────────────────────────
    slug_retenu: str | None = None

    #: Quand la veille a signalé que ce dossier dormait **dans l'état où il
    #: est**. `None` veut dire « pas encore signalé », jamais « pas en retard ».
    #:
    #: ⚠️ **Remis à `None` par toute transition**, dans `_passer_a`. Un dossier
    #: qui bouge recommence une attente : garder le marqueur ferait qu'un dossier
    #: signalé en `AFFECTÉE`, passé en conversation, puis rendormi pour trois
    #: semaines, ne serait jamais signalé une seconde fois.
    #:
    #: ⚠️ **Il n'est pas promu en colonne.** Rien ne le requête : la veille
    #: charge les ouverts d'un état, qu'elle chargeait déjà, et lit le marqueur
    #: en mémoire. Une colonne de plus serait une migration, un index à décider
    #: et une occasion de divergence, pour une lecture qui ne coûte rien.
    signale_le: datetime | None = None

    # ── Ce que le dossier sait dire de lui-même ─────────────────────────────

    @property
    def ouvert(self) -> bool:
        return self.etat not in (EtatDossier.PAYEE, EtatDossier.SANS_SUITE)

    @property
    def toutes_les_demandes(self) -> tuple[DemandeDeContact, ...]:
        """La demande d'origine, puis les rattachées, dans l'ordre d'arrivée.

        C'est cette suite que la détection de doublon interroge : comparer le
        nouveau dépôt à la seule demande d'origine ferait, au troisième envoi
        d'un visiteur insistant, sortir de la fenêtre un fil pourtant vivant.
        """
        return (self.demande, *self.rattachees)

    @property
    def derniere_demande(self) -> DemandeDeContact:
        """Ce que le client a écrit en dernier. Ce qu'affiche la console."""
        return self.toutes_les_demandes[-1]

    def rattacher(self, demande: DemandeDeContact) -> DossierCommercial:
        """Un second dépôt rejoint ce dossier.

        L'état ne change pas, et `depuis_le` non plus : un client qui renvoie
        son formulaire ne fait pas repartir le délai dont dispose le cabinet
        pour le rappeler. L'inverse permettrait d'échapper indéfiniment à
        l'alerte en soumettant une fois par heure.

        Refusé sur un dossier fermé : un dépôt qui suit un paiement ou un
        classement est une nouvelle intention, pas une répétition de
        l'ancienne, et il mérite son propre dossier.
        """
        if not self.ouvert:
            raise TransitionDossierRefusee(
                f"dossier {self.reference} à l'état {self.etat} : on n'y rattache "
                "plus rien. Un dépôt arrivé après un paiement ou un classement "
                "est une nouvelle intention, et il ouvre son propre dossier."
            )
        if any(
            connue.identifiant == demande.identifiant
            for connue in self.toutes_les_demandes
        ):
            return self
        return self.model_copy(update={"rattachees": (*self.rattachees, demande)})

    @property
    def avancement(self) -> int:
        """Rang dans le chemin nominal, de 0 à 7. `-1` hors du chemin.

        Un entier plutôt qu'un pourcentage : les étapes ne pèsent pas le même
        poids, et annoncer « 62 % » sur un dossier qui n'a pas encore été chiffré
        serait une invention.
        """
        if self.etat not in ETATS_NOMINAUX:
            return -1
        return ETATS_NOMINAUX.index(self.etat)

    def en_souffrance(
        self, a_l_instant: datetime, delais: Mapping[EtatDossier, timedelta]
    ) -> bool:
        """Le dossier est-il resté trop longtemps où il est ?

        Les délais viennent du référentiel, voir l'en-tête. Un état absent de la
        table n'est pas sous veille : `QUALIFIÉE` ne l'est pas, parce que le
        chiffrage est immédiat et qu'une alerte sur un état qu'on traverse en
        deux secondes serait du bruit.

        Un dossier fermé n'est jamais en souffrance. Sans cette garde, tous les
        dossiers payés de l'année remonteraient dans l'alerte le jour où l'on
        ajoute un délai sur `PAYÉE` par erreur.
        """
        if not self.ouvert:
            return False
        delai = delais.get(self.etat)
        if delai is None:
            return False
        return a_l_instant - self.depuis_le > delai

    def a_signaler(
        self, a_l_instant: datetime, delais: Mapping[EtatDossier, timedelta]
    ) -> bool:
        """En souffrance **et** pas encore signalé dans cet état.

        ─────────────────────────────────────────────────────────────────────
        LA DIFFÉRENCE AVEC `en_souffrance` EST TOUTE LA VEILLE

        `en_souffrance` répond à « ce dossier dort-il ? » : c'est ce qu'une
        console affiche, et la réponse reste vraie tant que personne n'agit.

        `a_signaler` répond à « faut-il le dire ? », et la réponse devient
        fausse dès qu'on l'a dit. Confondre les deux ferait déposer la même
        alerte à chaque passage de l'ordonnanceur : sur un dossier oublié six
        mois, plus de quatre mille alertes pour un seul fait.

        ⚠️ Le marqueur ne s'efface pas au bout d'un moment. Un dossier signalé
        puis laissé tel quel ne resignale jamais, et c'est voulu : la relance de
        l'humain qui n'a pas traité son alerte est un problème d'encadrement,
        pas de messagerie. Le redire chaque heure apprendrait seulement à ne
        plus lire les alertes.
        ─────────────────────────────────────────────────────────────────────
        """
        return self.signale_le is None and self.en_souffrance(a_l_instant, delais)

    def signaler(self, a_l_instant: datetime) -> DossierCommercial:
        """Marque que l'alerte est partie. **L'état ne change pas.**

        Signaler n'est pas agir : le dossier reste exactement où il était, avec
        la même ancienneté. Faire avancer un dossier parce qu'on a prévenu
        quelqu'un ferait mentir `depuis_le`, donc la veille elle-même.

        Rejouable : un second signalement garde la date du premier. C'est elle
        qui dit depuis quand quelqu'un est censé savoir.
        """
        if self.signale_le is not None:
            return self
        return self.model_copy(update={"signale_le": a_l_instant})

    # ── Le passage d'un état à l'autre ──────────────────────────────────────

    def _passer_a(
        self, cible: EtatDossier, a_l_instant: datetime, **champs: object
    ) -> DossierCommercial:
        """Le seul chemin par lequel l'état change.

        Toutes les transitions passent ici, et le graphe est consulté une fois.
        Répartir la vérification dans chaque méthode donnerait neuf endroits où
        l'oublier, et l'oubli ne se verrait qu'en production, sur le dossier
        d'un client qui aurait sauté une étape.
        """
        if cible not in TRANSITIONS[self.etat]:
            raise TransitionDossierRefusee(
                f"dossier {self.reference} à l'état {self.etat} : le passage vers "
                f"{cible} n'existe pas. Les passages permis depuis cet état sont "
                f"{sorted(TRANSITIONS[self.etat]) or 'aucun, il est terminal'}."
            )
        # ⚠️ `signale_le` est effacé ici, et **ici seulement**. C'est le seul
        # chemin par lequel l'état change, donc le seul endroit où « l'attente
        # recommence » est vrai. L'effacer dans chaque méthode donnerait neuf
        # endroits où l'oublier, exactement comme pour la vérification du graphe.
        return self.model_copy(
            update={
                "etat": cible,
                "depuis_le": a_l_instant,
                "signale_le": None,
                **champs,
            }
        )

    def affecter(
        self, responsable: str, a_l_instant: datetime, *, motif: str
    ) -> DossierCommercial:
        """Un responsable est désigné. Depuis `DÉPOSÉE` seulement.

        La réaffectation est un autre geste, avec son propre compteur et sa
        propre limite : voir `reaffecter`.
        """
        return self._passer_a(
            EtatDossier.AFFECTEE,
            a_l_instant,
            responsable=responsable,
            motif_affectation=motif,
            affecte_le=a_l_instant,
        )

    def reaffecter(
        self, responsable: str, a_l_instant: datetime, *, motif: str
    ) -> DossierCommercial:
        """Le premier n'a pas rappelé dans le délai. On passe la main.

        `affecte_le` **ne bouge pas** : c'est la date à laquelle le cabinet a
        pris le dossier en charge, et la remettre à zéro à chaque passage de
        main effacerait précisément le retard qu'on cherche à mesurer.
        """
        if self.reaffectations >= REAFFECTATIONS_MAXIMALES:
            raise TransitionDossierRefusee(
                f"dossier {self.reference} déjà réaffecté {self.reaffectations} fois. "
                "Au-delà, la règle d'affectation tourne en rond et le dossier a "
                "besoin d'un arbitrage humain, pas d'un tour de plus."
            )
        if responsable == self.responsable:
            raise TransitionDossierRefusee(
                f"dossier {self.reference} déjà affecté à {responsable}. Le "
                "réaffecter au même consommerait un tour de la limite sans que "
                "personne de nouveau ne soit prévenu."
            )
        if responsable in self.responsables_passes:
            raise TransitionDossierRefusee(
                f"dossier {self.reference} : {responsable} l'a déjà tenu sans "
                "l'avancer. Le lui rendre consommerait un tour de la limite pour "
                "revenir à quelqu'un qui a déjà laissé passer son délai."
            )
        return self._passer_a(
            EtatDossier.AFFECTEE,
            a_l_instant,
            responsable=responsable,
            motif_affectation=motif,
            reaffectations=self.reaffectations + 1,
            # ⚠️ Le titulaire sortant entre ici, et **jamais l'entrant** : la
            # liste dit « a déjà eu sa chance », pas « a touché au dossier ».
            # Y mettre l'entrant l'interdirait dès la reprise suivante alors
            # qu'il n'a encore rien eu le temps de faire.
            responsables_passes=self._avec_le_sortant(),
        )

    def _avec_le_sortant(self) -> tuple[str, ...]:
        """Le titulaire actuel rejoint ceux qui ont déjà eu la main.

        Rejouable : un nom n'entre qu'une fois. Sans cette garde, un même
        responsable compterait deux fois dans une liste qui ne sert qu'à
        l'exclure, ce qui ne changerait rien au comportement et rendrait
        illisible la question « combien de personnes ont vu ce dossier ? ».
        """
        if self.responsable is None or self.responsable in self.responsables_passes:
            return self.responsables_passes
        return (*self.responsables_passes, self.responsable)

    def premier_contact(self, a_l_instant: datetime) -> DossierCommercial:
        """Un premier échange est enregistré, quel que soit le canal.

        Rejouable : le deuxième message n'est pas un deuxième premier contact.
        Sans cette garde, `premier_echange_le` se décalerait à chaque message et
        l'indicateur de réactivité mesurerait le dernier échange, ce qui est
        exactement l'inverse de ce qu'on lui demande.
        """
        if self.premier_echange_le is not None:
            return self
        return self._passer_a(
            EtatDossier.EN_CONVERSATION,
            a_l_instant,
            premier_echange_le=a_l_instant,
        )

    def qualifier(self, a_l_instant: datetime) -> DossierCommercial:
        """Le responsable juge la qualification complète.

        Le domaine ne vérifie pas qu'elle l'est : la complétude se mesure sur la
        `Qualification`, qui est un autre objet et connaît son questionnaire.
        """
        return self._passer_a(EtatDossier.QUALIFIEE, a_l_instant)

    def chiffrer(self, a_l_instant: datetime) -> DossierCommercial:
        """Le moteur a rendu une proposition."""
        return self._passer_a(EtatDossier.CHIFFREE, a_l_instant)

    def emettre_la_proforma(self, a_l_instant: datetime) -> DossierCommercial:
        """Le tarif est validé, la proforma existe."""
        return self._passer_a(EtatDossier.PROFORMA_EMISE, a_l_instant)

    def accepter(self, a_l_instant: datetime) -> DossierCommercial:
        """Le client accepte. À partir d'ici, le montant ne bouge plus."""
        return self._passer_a(EtatDossier.ACCEPTEE, a_l_instant)

    def encaisser(self, a_l_instant: datetime) -> DossierCommercial:
        """Le paiement est confirmé. Terminal côté commerce.

        Rejouable : le prestataire renvoie ses notifications, et une seconde ne
        doit ni échouer bruyamment ni repousser `payee_le`, qui déclenche
        l'ouverture du tenant.
        """
        if self.etat is EtatDossier.PAYEE:
            return self
        return self._passer_a(EtatDossier.PAYEE, a_l_instant, payee_le=a_l_instant)

    def retour_en_conversation(self, a_l_instant: datetime) -> DossierCommercial:
        """On redescend pour reprendre l'échange.

        Trois cas réels : la qualification s'avère incomplète, le tarif ne
        convient pas, ou la proforma acceptée reste impayée à trente jours. Dans
        les trois, le dossier est vivant et le client joignable.

        Aucun motif n'est demandé ici, et c'est délibéré : la raison est
        l'événement qui a provoqué le retour, et elle est journalisée par
        celui qui le provoque. La recopier dans le dossier en donnerait deux
        versions, dont une seule serait tenue à jour.

        `premier_echange_le` n'est pas retouché, ni `reaffectations` : ce sont
        des faits, pas des états. Le dossier redescend, le cabinet ne redevient
        pas réactif pour autant.
        """
        return self._passer_a(EtatDossier.EN_CONVERSATION, a_l_instant)

    def retenir_le_slug(self, slug: str) -> DossierCommercial:
        """Fixe l'adresse du futur espace. **Une fois pour toutes.**

        ⚠️ Un slug déjà retenu ne se remplace pas en silence. Le changer entre la
        demande de règlement et la notification ouvrirait le tenant à une adresse
        différente de celle annoncée au client, et l'ancienne aurait déjà pu être
        communiquée.

        Rejouable avec la **même** valeur : une demande de règlement relancée
        après un appel manqué ne doit pas échouer pour cela.
        """
        if self.slug_retenu == slug:
            return self
        if self.slug_retenu is not None:
            raise TransitionDossierRefusee(
                f"dossier {self.reference} : l'espace « {self.slug_retenu} » est "
                f"déjà retenu, « {slug} » ne peut pas le remplacer. Une adresse "
                "annoncée au client ne se change pas en cours de règlement."
            )
        return self.model_copy(update={"slug_retenu": slug})

    def classer_sans_suite(
        self, a_l_instant: datetime, *, motif: str
    ) -> DossierCommercial:
        """Le dossier quitte le parcours. Il n'est pas supprimé.

        Rejouable, avec le motif d'origine : un classement rejoué par un lot ne
        doit pas réécrire la raison pour laquelle un humain avait classé.
        """
        if self.etat is EtatDossier.SANS_SUITE:
            return self
        return self._passer_a(
            EtatDossier.SANS_SUITE,
            a_l_instant,
            clos_le=a_l_instant,
            motif_cloture=motif,
        )


def ouvrir_un_dossier(
    reference: str, demande: DemandeDeContact
) -> DossierCommercial:
    """Le dossier naît à l'état `DÉPOSÉE`, daté de la demande elle-même.

    Et non de l'instant présent. La veille des deux heures compte à partir du
    moment où le client a cliqué, pas du moment où le lot a traité sa ligne :
    sinon un incident de traitement remettrait toutes les horloges à zéro, et
    l'alerte qui devait signaler le retard le masquerait.
    """
    return DossierCommercial(
        reference=reference, demande=demande, depuis_le=demande.deposee_le
    )
