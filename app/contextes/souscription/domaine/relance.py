"""La relance graduée d'une proforma sans réponse.

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE DOCUMENT DE CONCEPTION DEMANDE

> « Sans réponse à 3, 7 et 14 jours, relance graduée. »
> « Impayée à 30 jours, retour en conversation. »

Trois nombres et un quatrième, et **aucun n'est écrit dans ce fichier**. Le centre
les changera après trois mois d'usage réel, un mardi matin, et ce changement ne
doit demander ni déploiement ni développeur.

⚠️ UNE RELANCE COÛTE DE L'ARGENT, ET PEUT COÛTER LE CANAL

Hors fenêtre de service, une relance part en modèle utilitaire, donc **facturée**.
Une relance automatique mal réglée coûte à chaque déclenchement.

Pire : un numéro qui envoie beaucoup de messages non lus voit sa note baisser
puis ses quotas se réduire. **Relancer trop détruit le canal lui-même**, et le
canal conditionne aussi l'appel, puisque les deux dépendent du même palier de
messagerie.

C'est pourquoi ce module s'arrête après le dernier palier au lieu de boucler, et
pourquoi il n'envoie **qu'une seule relance par passage**.

LES QUATRE RÈGLES, ET CE QUE CHACUNE ÉVITE

1. **Le délai court depuis la transmission, jamais depuis l'émission.** Une
   proforma émise et jamais transmise ne se relance pas : relancer un client qui
   n'a rien reçu le laisse perplexe et fait passer le cabinet pour désorganisé.

2. **Un palier franchi ne se rejoue pas.** Le balayage tourne toutes les heures ;
   sans cette garde, le client recevrait vingt-quatre messages par jour.

3. **Un seul palier par passage, le plus avancé qui soit dû.** Si le balayage a
   été arrêté une semaine, on ne rattrape pas en envoyant trois messages d'un
   coup. Le client n'y verrait pas un système remis en route, il y verrait du
   harcèlement.

4. **Après le dernier palier, on s'arrête et on le dit.** Une proforma épuisée
   sort de la file de relance et entre dans une liste que quelqu'un regarde. Le
   silence d'un client après trois relances est une information ; continuer à
   écrire n'en est pas une.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contextes.souscription.domaine.proforma import EtatProforma, Proforma

__all__ = [
    "IssueDeRelance",
    "PalierDeRelance",
    "PlanDeRelance",
    "RelanceADeclencher",
    "SuiviDeRelance",
    "impayees_a_reprendre",
    "relance_due",
    "relances_dues",
]

#: Les états d'une proforma qui se relance. Émise mais non transmise n'en fait
#: pas partie : voir la règle 1 de l'en-tête.
_RELANCABLES = frozenset({EtatProforma.TRANSMISE})


class IssueDeRelance(StrEnum):
    """Pourquoi une proforma n'a pas produit de relance à ce passage."""

    #: Une relance est due, et elle est rendue.
    DUE = "DUE"
    #: Pas encore transmise, ou pas encore assez ancienne.
    TROP_TOT = "TROP_TOT"
    #: Le client a répondu, ou le document est remplacé, annulé, accepté.
    SANS_OBJET = "SANS_OBJET"
    #: Tous les paliers ont été employés. **Sort de la file.**
    EPUISEE = "EPUISEE"


class PalierDeRelance(BaseModel):
    """Un rang de relance, son délai et son modèle de message.

    Le **ton** est un paramètre du modèle, pas un modèle par ton : trois modèles
    quasi identiques se font approuver trois fois, se corrigent trois fois, et
    divergent au premier oubli.
    """

    model_config = ConfigDict(frozen=True)

    #: 1, 2, 3… Le rang est l'identité du palier : c'est lui qu'on inscrit au
    #: suivi, et non le délai, qui peut changer entre deux relances.
    rang: int = Field(ge=1)
    #: Depuis la **transmission**, jamais depuis l'émission.
    apres: timedelta
    modele: str = Field(min_length=1)
    #: Ce que le modèle recevra comme variable de ton. « courtois », « ferme ».
    ton: str = Field(min_length=1)


class PlanDeRelance(BaseModel):
    """Les paliers du centre, dans l'ordre, et le délai d'impayé.

    Vit au référentiel. Le jour où le centre décide de relancer à deux, cinq et
    dix jours, c'est un fichier qui change.
    """

    model_config = ConfigDict(frozen=True)

    paliers: tuple[PalierDeRelance, ...]
    #: Au delà, une proforma acceptée mais non payée retourne en conversation.
    #: Ce n'est pas une relance de plus : c'est un dossier qu'un humain reprend.
    impaye_apres: timedelta

    @model_validator(mode="after")
    def _les_paliers_progressent(self) -> PlanDeRelance:
        rangs = [p.rang for p in self.paliers]
        # ⚠️ Contiguïté, pas seulement ordre croissant. La première version
        # comparait à `sorted(set(rangs))`, ce qui laissait passer `[1, 3]` :
        # les rangs étaient bien triés et distincts, et il manquait le 2.
        if rangs != list(range(1, len(rangs) + 1)):
            raise ValueError(
                f"paliers de rangs {rangs} : ils doivent aller de 1 en 1, sans "
                "trou ni doublon. Le rang est inscrit au suivi de chaque "
                "proforma ; un trou rendrait indécidable le palier suivant."
            )
        delais = [p.apres for p in self.paliers]
        if delais != sorted(delais) or len(set(delais)) != len(delais):
            raise ValueError(
                f"délais de relance {delais} : ils doivent croître strictement. "
                "Deux paliers au même délai partiraient le même jour, et un "
                "délai qui décroît ferait relancer à l'envers."
            )
        return self

    @property
    def dernier_rang(self) -> int:
        return self.paliers[-1].rang if self.paliers else 0

    def palier(self, rang: int) -> PalierDeRelance | None:
        return next((p for p in self.paliers if p.rang == rang), None)


class SuiviDeRelance(BaseModel):
    """Ce qui a déjà été envoyé pour une proforma.

    ⚠️ Porté à part, et **pas sur la proforma** : celle-ci est figée et vaut
    contrat. Y inscrire un compteur de relances ferait changer un document
    contractuel pour une raison qui n'a rien de contractuel.
    """

    model_config = ConfigDict(frozen=True)

    proforma: str = Field(min_length=1)
    #: Les rangs déjà employés. Un rang n'y figure qu'une fois.
    rangs_envoyes: tuple[int, ...] = ()
    derniere_le: datetime | None = None

    @property
    def dernier_rang(self) -> int:
        return max(self.rangs_envoyes, default=0)

    def avec(self, rang: int, a_l_instant: datetime) -> SuiviDeRelance:
        """Inscrit un palier employé. **Rejouable** : un rang n'y entre qu'une fois.

        Sans cette garde, un balayage rejoué doublerait le compteur et ferait
        croire que le client a été relancé deux fois au même rang.
        """
        return self.avec_les((rang,), a_l_instant)

    def avec_les(
        self, rangs: Iterable[int], a_l_instant: datetime
    ) -> SuiviDeRelance:
        """Inscrit plusieurs paliers d'un coup, et c'est le cas normal.

        ─────────────────────────────────────────────────────────────────────────
        ⚠️ **CE N'EST PAS UNE COMMODITÉ, C'EST LA CORRECTION D'UN DÉFAUT.**

        Quand plusieurs paliers sont dus, `relance_due` n'en envoie qu'un — le plus
        avancé — et **les précédents doivent être marqués employés avec lui**. Cet
        en-tête l'affirmait depuis le pas 11, et rien ne le faisait : l'appelant
        n'inscrivait que le rang envoyé.

        La conséquence se voit à l'usage. Une proforma transmise depuis huit jours,
        sur un plan à 3, 7 et 14 jours : le premier passage envoie le rang 2, et le
        passage suivant trouve le rang 1 encore libre et **l'envoie**. Le client
        reçoit un rappel moins urgent après un rappel plus urgent.

        Aucune garde ne l'attrapait, parce que chaque envoi était individuellement
        correct.
        ─────────────────────────────────────────────────────────────────────────
        """
        nouveaux = tuple(r for r in dict.fromkeys(rangs) if r not in self.rangs_envoyes)
        if not nouveaux:
            return self
        return self.model_copy(
            update={
                # Triés : le suivi se relit, et un ordre d'insertion refléterait le
                # hasard des passages plutôt que la progression des paliers.
                "rangs_envoyes": tuple(sorted((*self.rangs_envoyes, *nouveaux))),
                "derniere_le": a_l_instant,
            }
        )


class RelanceADeclencher(BaseModel):
    """Une relance à envoyer, et de quoi l'envoyer."""

    model_config = ConfigDict(frozen=True)

    proforma: str = Field(min_length=1)
    dossier: str = Field(min_length=1)
    rang: int = Field(ge=1)
    modele: str = Field(min_length=1)
    ton: str = Field(min_length=1)
    #: Depuis combien de temps le document a été transmis, au moment du passage.
    depuis: timedelta
    #: `True` si c'est le dernier palier du plan. L'appelant en informe le
    #: responsable : après celle-ci, le système se tait.
    derniere: bool = False

    #: ⚠️ **Tous les rangs que cet envoi consomme**, celui envoyé compris.
    #:
    #: Quand plusieurs paliers sont dus, un seul message part et les précédents
    #: n'ont plus d'objet. L'appelant doit **tous** les inscrire au suivi, sans quoi
    #: le passage suivant trouve un rang inférieur encore libre et l'envoie : le
    #: client reçoit un rappel moins urgent après un rappel plus urgent.
    #:
    #: Le champ est ici plutôt que recalculé par l'appelant parce que c'est le
    #: domaine qui décide ce qu'un envoi consomme. Un `range(1, rang + 1)` écrit
    #: dans l'adaptateur supposerait des rangs contigus et sans trou : c'est vrai
    #: aujourd'hui, et ce serait une supposition invisible le jour où ça changerait.
    rangs_couverts: tuple[int, ...] = ()


def relance_due(
    proforma: Proforma,
    suivi: SuiviDeRelance,
    plan: PlanDeRelance,
    a_l_instant: datetime,
) -> tuple[IssueDeRelance, RelanceADeclencher | None]:
    """Faut-il relancer cette proforma maintenant, et à quel palier ?

    ─────────────────────────────────────────────────────────────────────────
    LE PALIER RENDU EST **LE PLUS AVANCÉ QUI SOIT DÛ**, ET UN SEUL

    Si le balayage a été arrêté une semaine, trois paliers peuvent être dus. On
    n'en envoie qu'un, le plus avancé, et les précédents sont marqués employés
    avec lui : ils n'ont plus d'objet.

    Envoyer les trois ferait recevoir au client trois messages dans la même
    minute. Il n'y verrait pas un système remis en route, il y verrait du
    harcèlement, et le canal en paierait le prix.
    ─────────────────────────────────────────────────────────────────────────
    """
    if proforma.etat not in _RELANCABLES or proforma.transmise_le is None:
        return IssueDeRelance.SANS_OBJET, None
    if suivi.dernier_rang >= plan.dernier_rang and plan.paliers:
        return IssueDeRelance.EPUISEE, None

    depuis = a_l_instant - proforma.transmise_le
    dus = [
        p
        for p in plan.paliers
        if p.rang not in suivi.rangs_envoyes and depuis >= p.apres
    ]
    if not dus:
        return IssueDeRelance.TROP_TOT, None

    palier = dus[-1]
    return IssueDeRelance.DUE, RelanceADeclencher(
        proforma=proforma.numero,
        dossier=proforma.dossier,
        rang=palier.rang,
        modele=palier.modele,
        ton=palier.ton,
        depuis=depuis,
        derniere=palier.rang == plan.dernier_rang,
        # Voir `rangs_couverts` : tous les dus, pas seulement celui qui part.
        rangs_couverts=tuple(sorted(p.rang for p in dus)),
    )


def relances_dues(
    proformas: Sequence[Proforma],
    suivis: dict[str, SuiviDeRelance],
    plan: PlanDeRelance,
    a_l_instant: datetime,
) -> list[RelanceADeclencher]:
    """Le lot d'un passage, de la plus ancienne transmission à la plus récente.

    L'ordre n'est pas cosmétique : si le lot est borné en aval, c'est le client
    qui attend depuis le plus longtemps qui doit passer d'abord.
    """
    a_faire = []
    for proforma in sorted(
        proformas, key=lambda p: (p.transmise_le or p.emise_le, p.numero)
    ):
        issue, relance = relance_due(
            proforma,
            suivis.get(proforma.numero, SuiviDeRelance(proforma=proforma.numero)),
            plan,
            a_l_instant,
        )
        if issue is IssueDeRelance.DUE and relance is not None:
            a_faire.append(relance)
    return a_faire


def impayees_a_reprendre(
    proformas: Sequence[Proforma], plan: PlanDeRelance, a_l_instant: datetime
) -> list[Proforma]:
    """Les acceptées non payées au delà du délai. **Pas une relance de plus.**

    Le client s'est engagé et n'a pas réglé : ce n'est plus un problème de
    relance automatique, c'est un dossier qu'un humain reprend. Le dossier
    commercial redescend en conversation, et le responsable rappelle.

    Continuer à envoyer des modèles à quelqu'un qui a signé et pas payé ne
    produit rien qu'une facture de messagerie.
    """
    return sorted(
        (
            p
            for p in proformas
            if p.etat is EtatProforma.ACCEPTEE
            and p.transmise_le is not None
            and a_l_instant - p.transmise_le >= plan.impaye_apres
        ),
        key=lambda p: p.transmise_le,
    )
