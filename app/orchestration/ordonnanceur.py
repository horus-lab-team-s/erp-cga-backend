"""Ce qui décide qu'un travail périodique est dû, et rien d'autre.

─────────────────────────────────────────────────────────────────────────────────
LE MANQUE QUE CE MODULE COMBLE

La boîte d'envoi se remplit et personne ne la vide. Les proformas sans réponse
attendent une relance que rien ne déclenche. Les sagas interrompues restent
interrompues. Tout le mécanisme existe depuis les pas 7, 8 et 11 ; il lui manquait
**quelqu'un qui appelle**.

POURQUOI CE MODULE NE DORT PAS, N'APPELLE RIEN, ET NE CONNAÎT AUCUNE BASE

Un ordonnanceur écrit comme une boucle qui dort est faux de trois façons, et les
trois se découvrent en exploitation.

Il **perd son échéancier au redémarrage** : le compte à rebours vit en mémoire, un
déploiement le remet à zéro, et un travail quotidien redéployé chaque matin ne
passe jamais.

Il **double sur deux instances** : deux processus qui dorment le même intervalle
se réveillent tous les deux, et relancent deux fois le même client.

Il **ne se teste qu'en attendant**. Vérifier qu'un travail horaire passe bien
demande une heure, ou des ruses sur l'horloge répandues dans tout le code.

D'où la découpe. Ici vit la **question** : quels travaux sont dus à cet instant,
compte tenu de leurs derniers passages ? Elle est pure, elle reçoit l'instant en
argument, et elle se teste en choisissant cet instant. L'exclusion entre instances
est à `app/infrastructure/verrou.py`, l'état des passages en base, et l'appel dans
un adaptateur.

⚠️ **UN TRAVAIL EN RETARD NE SE RATTRAPE PAS N FOIS.** C'est la règle qui distingue
un ordonnanceur d'un compteur. Un processus arrêté trois jours, avec une cadence
horaire, ne doit pas produire soixante-douze passages au redémarrage : il doit en
produire **un**, puis reprendre le rythme. Cette règle est écrite dans `travaux_dus`
et vaut pour tous les travaux, parce qu'aucun de ceux du système n'est cumulatif.
Le relais publie ce qui attend, quel qu'en soit l'âge. Le balayage de relance
regarde ce qui est dû maintenant. Rejouer le passage manqué ne produirait rien
qu'une seconde relance au même client.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "ECHECS_AVANT_ABANDON",
    "Passage",
    "RepriseRefusee",
    "RECUL_MAXIMAL",
    "Travail",
    "delai_avant_reprise",
    "prochain_reveil",
    "travaux_dus",
]

#: Au delà, le recul cesse de croître. Sans plafond, dix échecs d'affilée sur une
#: cadence horaire repousseraient la reprise à plus de quarante jours : le travail
#: serait mort sans que rien ne l'ait déclaré mort.
RECUL_MAXIMAL = timedelta(hours=1)

#: Le nombre d'échecs consécutifs au delà duquel un travail cesse d'être tenté.
#: ⚠️ Il ne se répare pas tout seul : reprendre demande un geste d'exploitation.
#: C'est voulu. Un travail qui échoue vingt fois de suite a un défaut que la
#: vingt-et-unième tentative ne corrigera pas, et continuer masque le problème
#: derrière un journal qui défile.
ECHECS_AVANT_ABANDON = 20


class Travail(BaseModel):
    """Un travail périodique : son nom, son rythme, et ce qu'il fait.

    ⚠️ **La cadence est une donnée, pas une constante de ce module.** Le rythme du
    relais se règle en exploitation : quelques secondes quand la messagerie doit
    être vive, quelques minutes quand la facturation le demande. Ce module dit
    comment on tient un rythme, jamais lequel.
    """

    model_config = ConfigDict(frozen=True)

    nom: str = Field(min_length=1, max_length=64)
    cadence: timedelta
    #: Ce que l'exploitant lit dans la sonde et le journal. Un nom seul
    #: (« relais ») ne dit pas ce qu'un arrêt empêche.
    objet: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _cadence_positive(self) -> Travail:
        # ⚠️ Une cadence nulle rendrait le travail dû à chaque évaluation, donc
        # une boucle chaude qui consomme un cœur sans avancer. Une cadence
        # négative rendrait la comparaison toujours vraie, avec le même effet et
        # sans la moindre trace de son origine.
        if self.cadence <= timedelta(0):
            raise ValueError(
                f"travail {self.nom} : la cadence doit être strictement positive, "
                f"reçu {self.cadence}"
            )
        return self


class RepriseRefusee(ValueError):
    """On ne reprend qu'un travail abandonné (pas 84)."""


class Passage(BaseModel):
    """Ce qu'on sait du dernier tour d'un travail. Vit en base, pas en mémoire.

    ─────────────────────────────────────────────────────────────────────────
    POURQUOI `debute_le` ET `termine_le` PLUTÔT QU'UNE SEULE DATE

    Parce qu'un travail en cours n'est ni fini ni à relancer. Un passage marqué
    au début et jamais terminé signale un processus tué pendant l'exécution :
    l'écart entre les deux dates est la seule façon de distinguer « il tourne
    encore » de « il est mort en route ».

    ⚠️ Ce module ne s'en sert pas encore pour empêcher un chevauchement. C'est le
    verrou d'exclusion qui joue ce rôle, et il le joue mieux : il tombe tout seul
    quand le processus meurt, là où une date en base resterait éternellement à
    dire « en cours ». Les deux dates servent à mesurer et à diagnostiquer.
    ─────────────────────────────────────────────────────────────────────────
    """

    model_config = ConfigDict(frozen=True)

    travail: str = Field(min_length=1, max_length=64)
    debute_le: datetime | None = None
    termine_le: datetime | None = None
    #: Remis à zéro par un succès. C'est lui qui commande le recul.
    echecs_consecutifs: int = Field(default=0, ge=0)
    dernier_echec: str | None = None

    @property
    def abandonne(self) -> bool:
        """Trop d'échecs d'affilée : le travail ne sera plus tenté."""
        return self.echecs_consecutifs >= ECHECS_AVANT_ABANDON

    @property
    def jamais_passe(self) -> bool:
        return self.termine_le is None and self.debute_le is None

    def reussi(self, a_l_instant: datetime) -> Passage:
        """⚠️ Remet le compteur d'échecs **à zéro**, et pas seulement d'un cran.

        Décrémenter laisserait un travail qui alterne succès et échec reculer sans
        fin alors qu'il fonctionne une fois sur deux. Le recul répond à « est-ce
        cassé maintenant », pas à « a-t-il déjà été cassé ».
        """
        return self.model_copy(
            update={
                "termine_le": a_l_instant,
                "echecs_consecutifs": 0,
                "dernier_echec": None,
            }
        )

    def reprendre(self) -> Passage:
        """Le geste d'exploitation qui rend un travail abandonné à l'ordonnanceur (pas 84).

        ─────────────────────────────────────────────────────────────────────────
        ⚠️ IL ÉTAIT ANNONCÉ, ET IL N'EXISTAIT PAS

        `ECHECS_AVANT_ABANDON` et `travaux_dus` disaient : « il faut un geste
        d'exploitation pour le reprendre ». Aucune route, aucune méthode ne le
        faisait. Un travail abandonné (la relance des proformas, la réconciliation des
        paiements) le restait jusqu'à ce que quelqu'un modifie la base à la main.

        CE QUE LA REPRISE FAIT, ET CE QU'ELLE GARDE

        - Le compteur d'échecs revient à zéro : le recul repart de la cadence.
        - `termine_le` est effacé : le travail redevient **dû tout de suite**. On
          reprend un travail après avoir corrigé sa cause, et l'on veut savoir au
          prochain tour si la correction tient, pas dans vingt-quatre heures.
        - `debute_le` est conservé (ou pris sur l'ancienne fin) : le travail a déjà
          tourné, et l'écran ne doit pas afficher « jamais passé ».
        - `dernier_echec` est conservé : c'est la trace de ce qui a été corrigé. Le
          prochain succès l'efface, comme d'habitude.

        ⚠️ Refusée sur un travail qui n'est pas abandonné : remettre à zéro le recul
        d'un travail qui échoue encore le ferait marteler sa cause.
        ─────────────────────────────────────────────────────────────────────────
        """
        if not self.abandonne:
            raise RepriseRefusee(
                f"le travail « {self.travail} » n'est pas abandonné "
                f"({self.echecs_consecutifs} échec(s) d'affilée, abandon à "
                f"{ECHECS_AVANT_ABANDON}) : l'ordonnanceur le tente déjà."
            )
        return self.model_copy(
            update={
                "debute_le": self.debute_le or self.termine_le,
                "termine_le": None,
                "echecs_consecutifs": 0,
            }
        )

    def echoue(self, a_l_instant: datetime, motif: str) -> Passage:
        """Compte l'échec et retient son motif. `termine_le` avance quand même.

        ⚠️ C'est délibéré : sans cela, un travail qui échoue serait éternellement
        dû, donc retenté sans aucun recul, et le recul calculé plus bas ne
        s'appliquerait jamais. La date dit « ce tour est fini », pas « ce tour a
        réussi » : c'est `echecs_consecutifs` qui porte le verdict.
        """
        return self.model_copy(
            update={
                "termine_le": a_l_instant,
                "echecs_consecutifs": self.echecs_consecutifs + 1,
                "dernier_echec": motif[:500],
            }
        )


def delai_avant_reprise(travail: Travail, passage: Passage) -> timedelta:
    """Le délai à respecter avant le prochain tour, recul d'échec compris.

    ─────────────────────────────────────────────────────────────────────────
    Le recul double à chaque échec consécutif, et se plafonne. Un abonné
    injoignable ne doit pas être martelé toutes les cinq secondes : cela ne le
    répare pas, cela remplit le journal, et cela consomme la connexion qui
    servirait à un travail qui, lui, fonctionne.

    ⚠️ **Le recul ne descend jamais sous la cadence.** Un travail dont la cadence
    est déjà d'une heure et qui échoue une fois ne doit pas repasser au bout de
    dix secondes sous prétexte que le premier palier de recul est court.
    ─────────────────────────────────────────────────────────────────────────
    """
    if passage.echecs_consecutifs == 0:
        return travail.cadence
    # 2**n secondes, plafonné. `min` avant `max` : le plafond borne le recul, le
    # plancher garantit qu'on ne repasse jamais plus vite que la cadence voulue.
    recul = min(RECUL_MAXIMAL, timedelta(seconds=2**passage.echecs_consecutifs))
    return max(travail.cadence, recul)


def travaux_dus(
    travaux: Sequence[Travail],
    passages: dict[str, Passage],
    a_l_instant: datetime,
) -> list[Travail]:
    """Les travaux à faire tourner maintenant, dans l'ordre où ils sont déclarés.

    ─────────────────────────────────────────────────────────────────────────
    L'ORDRE DE DÉCLARATION EST L'ORDRE D'EXÉCUTION, ET CELA COMPTE

    Le relais passe avant le balayage de relance, et la raison écrite ici a d'abord
    été **fausse**. Elle disait : « le balayage dépose ses envois dans la boîte, et
    les déposer juste après un passage du relais les ferait attendre un tour
    entier ». Cet argument plaide pour l'ordre inverse, celui qui n'est pas retenu.

    La vraie raison est que **le relais est le chemin vital**. C'est lui qui ouvre
    les tenants payés : un client qui règle attend que son sous-domaine réponde. Le
    faire passer derrière un balayage qui parcourt des centaines de proformas
    ajouterait la durée de ce parcours au délai d'ouverture, sur chaque tour où les
    deux coïncident.

    Et le prétendu coût de cet ordre n'existe pas. Les envois déposés par le
    balayage attendent le passage suivant du **relais**, qui tourne toutes les
    quelques secondes, et non le passage suivant du balayage, qui tourne toutes les
    heures. Le retard est donc de quelques secondes, pas d'un tour de relance.

    Trier par nom ou par ancienneté casserait cette dépendance sans que rien ne le
    signale : « relance » passerait avant « relais ».

    ⚠️ **UN TRAVAIL JAMAIS PASSÉ EST DÛ IMMÉDIATEMENT.** C'est ce qui rend le
    premier démarrage franc : une installation neuve ne reste pas une heure sans
    rien faire avant son premier tour. Le premier passage est aussi celui qui
    révèle les erreurs de configuration, et on le veut tout de suite.

    ⚠️ **UN TRAVAIL ABANDONNÉ N'EST PLUS DÛ**, quel que soit le temps écoulé. Voir
    `ECHECS_AVANT_ABANDON` : il faut un geste d'exploitation pour le reprendre.
    ─────────────────────────────────────────────────────────────────────────
    """
    dus = []
    for travail in travaux:
        passage = passages.get(travail.nom, Passage(travail=travail.nom))
        if passage.abandonne:
            continue
        if passage.termine_le is None:
            dus.append(travail)
            continue
        # ⚠️ `>=` et non `>`. Un test qui pose l'instant exactement à l'échéance
        # est le cas le plus naturel à écrire, et le plus fréquent en pratique
        # quand la cadence divise l'intervalle de réveil.
        if a_l_instant - passage.termine_le >= delai_avant_reprise(travail, passage):
            dus.append(travail)
    return dus


def prochain_reveil(
    travaux: Sequence[Travail],
    passages: dict[str, Passage],
    a_l_instant: datetime,
    *,
    plancher: timedelta = timedelta(seconds=1),
) -> timedelta:
    """Dans combien de temps quelque chose sera dû. Sert à dormir juste ce qu'il faut.

    ─────────────────────────────────────────────────────────────────────────
    Sans cette fonction, l'adaptateur choisirait un intervalle fixe, et se
    tromperait dans les deux sens : trop court, il interroge la base pour rien
    des milliers de fois par heure ; trop long, il rate la cadence du travail le
    plus vif, qui devient la cadence de l'intervalle.

    ⚠️ Le **plancher** existe pour une raison précise : sans lui, un travail dû à
    l'instant même rendrait zéro, et l'adaptateur tournerait sans jamais rendre la
    main. Un ordonnanceur qui monopolise un cœur est une panne, même quand chaque
    passage est correct.

    Quand tous les travaux sont abandonnés, il n'y a plus rien à attendre : la
    fonction rend alors la cadence la plus longue déclarée, pour continuer à se
    réveiller de temps en temps sans rien faire, plutôt que de rendre l'infini et
    laisser un processus qui ne se réveillera plus jamais même après réparation.
    ─────────────────────────────────────────────────────────────────────────
    """
    if not travaux:
        return plancher
    attentes = []
    for travail in travaux:
        passage = passages.get(travail.nom, Passage(travail=travail.nom))
        if passage.abandonne:
            continue
        if passage.termine_le is None:
            return plancher
        reste = delai_avant_reprise(travail, passage) - (a_l_instant - passage.termine_le)
        attentes.append(max(plancher, reste))
    if not attentes:
        return max(travail.cadence for travail in travaux)
    return min(attentes)
