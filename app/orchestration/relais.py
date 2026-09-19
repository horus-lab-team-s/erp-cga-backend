"""Le relais : vider la boîte d'envoi, sans jamais casser l'ordre.

─────────────────────────────────────────────────────────────────────────────────
CE QU'IL FAIT, ET RIEN D'AUTRE

Il lit un lot borné d'événements en attente, les remet à qui s'y est abonné, et
marque chacun publié ou en échec. Il tourne en boucle, toutes les quelques
secondes.

⚠️ **L'ORDRE PAR CLÉ EST UNE GARANTIE, ET C'EST LA PARTIE SUBTILE**

Deux événements de la même clé doivent être traités dans l'ordre d'émission. Un
`TenantOuvert` traité avant le `PaiementEncaissé` qui l'a causé produirait un
tenant sans justification.

Le lot est donc lu du plus ancien au plus récent, ce qui suffit tant que tout
passe. Mais **quand un événement échoue, les suivants de la même clé doivent
attendre avec lui.** Sinon le rejeu du premier, au tour suivant, arriverait après
le second, et l'ordre serait inversé au pire moment : celui d'un incident.

Les événements des **autres** clés continuent, eux. Un tenant bloqué ne doit pas
retenir les quatre-vingt-dix-neuf autres.

CE QU'IL N'EST PAS

Ce n'est pas un bus. Il n'y a ni réseau, ni sérialisation, ni courtier : dans un
monolithe modulaire, un abonné est une fonction du même processus, et prétendre
le contraire ajouterait une panne possible pour aucun gain.

Le jour où un service partira sur une autre machine, son abonné deviendra un
appel sortant, et **ce module ne changera pas** : il ignore ce que fait un
abonné, il sait seulement qu'il peut échouer.

UN ABONNÉ QUI ÉCHOUE N'EST PAS UN DÉFAUT DU RELAIS

Il compte l'échec, le nomme, et laisse l'événement en attente. Au bout de dix
tentatives, l'événement part en quarantaine plutôt que de saturer la file
indéfiniment. La quarantaine n'est pas une suppression : c'est la trace d'un fait
qui a bien eu lieu et que personne n'a su traiter.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from app.orchestration.boite_d_envoi import (
    TENTATIVES_AVANT_QUARANTAINE,
    BoiteDEnvoi,
    EvenementSortant,
)

__all__ = [
    "Abonnements",
    "Abonne",
    "RapportPublication",
    "journal_du_passage",
    "publier_un_lot",
]

#: Ce qu'un abonné doit savoir faire : recevoir l'événement, et lever s'il n'a pas
#: pu le traiter. Rien de plus. Il ne rend rien, parce qu'un relais qui
#: interpréterait une valeur de retour deviendrait un orchestrateur déguisé.
Abonne = Callable[[EvenementSortant], None]


class Abonnements:
    """Qui écoute quoi.

    ⚠️ **Un événement sans abonné est publié, pas mis en échec.** C'est le cas
    normal : tous les événements n'intéressent pas quelqu'un dès le premier jour,
    et les mettre en échec ferait partir en quarantaine des faits parfaitement
    traités. Le rapport les compte à part, pour que l'absence se voie sans
    alerter.
    """

    def __init__(self) -> None:
        self._par_nom: dict[str, list[Abonne]] = {}

    def abonner(self, nom: str, abonne: Abonne) -> None:
        self._par_nom.setdefault(nom, []).append(abonne)

    def pour(self, nom: str) -> tuple[Abonne, ...]:
        return tuple(self._par_nom.get(nom, ()))

    def noms(self) -> list[str]:
        return sorted(self._par_nom)


@dataclass(frozen=True)
class RapportPublication:
    """Ce qu'un passage a fait. Destiné au journal d'exploitation."""

    publies: int = 0
    #: Publiés faute d'abonné. Comptés à part : c'est un fait à surveiller, pas
    #: une anomalie.
    sans_abonne: int = 0
    echecs: int = 0
    #: Laissés en attente parce qu'un événement antérieur de la même clé a
    #: échoué. Ce n'est pas un échec de plus, c'est l'ordre qui tient.
    retenus: int = 0
    mis_en_quarantaine: int = 0
    motifs: tuple[str, ...] = field(default_factory=tuple)

    @property
    def traites(self) -> int:
        return self.publies + self.echecs

    @property
    def demande_un_regard(self) -> bool:
        """Ce qui doit remonter au journal du matin."""
        return bool(self.mis_en_quarantaine or self.echecs)


def publier_un_lot(
    boite: BoiteDEnvoi,
    abonnements: Abonnements,
    a_l_instant: datetime,
    *,
    limite: int = 100,
    seuil_quarantaine: int = TENTATIVES_AVANT_QUARANTAINE,
) -> RapportPublication:
    """Vide un lot, dans l'ordre, en retenant ce qui doit l'être.

    ─────────────────────────────────────────────────────────────────────────
    POURQUOI UN LOT BORNÉ PLUTÔT QUE TOUT

    Un arriéré de cent mille événements chargé d'un coup ferait un passage qui ne
    finit pas, une transaction qui tient des verrous, et une reprise qui repart
    de zéro à la moindre coupure. Un lot traité puis validé, et l'on repasse :
    c'est ce qui permet d'avancer même très en retard.

    LES ÉCHECS SONT COMPTÉS, JAMAIS PROPAGÉS

    Un abonné qui lève ne fait pas échouer le passage. Le relais existe
    précisément pour que la panne d'un consommateur n'arrête pas les autres.
    ─────────────────────────────────────────────────────────────────────────
    """
    publies = sans_abonne = echecs = retenus = quarantaine = 0
    motifs: list[str] = []
    #: Les clés dont un événement a échoué dans ce lot. Voir l'en-tête.
    bloquees: set[str] = set()

    for evenement in boite.a_publier(limite):
        if evenement.cle in bloquees:
            retenus += 1
            continue

        abonnes = abonnements.pour(evenement.nom)
        echec = _remettre(evenement, abonnes)

        if echec is None:
            boite.enregistrer(evenement.publie(a_l_instant))
            publies += 1
            if not abonnes:
                sans_abonne += 1
            continue

        apres = evenement.echoue(echec, seuil=seuil_quarantaine)
        boite.enregistrer(apres)
        echecs += 1
        bloquees.add(evenement.cle)
        motifs.append(f"{evenement.nom} [{evenement.cle}] : {echec}")
        if apres.en_quarantaine:
            quarantaine += 1

    return RapportPublication(
        publies=publies,
        sans_abonne=sans_abonne,
        echecs=echecs,
        retenus=retenus,
        mis_en_quarantaine=quarantaine,
        motifs=tuple(motifs),
    )


def _remettre(evenement: EvenementSortant, abonnes: Sequence[Abonne]) -> str | None:
    """Remet l'événement à chaque abonné. Rend le motif du premier échec.

    ⚠️ **Le premier échec arrête la remise aux abonnés suivants**, et c'est
    délibéré. L'événement sera rejoué en entier, et les abonnés déjà servis le
    recevront une seconde fois : c'est la contrepartie assumée du « au moins une
    fois », et c'est pourquoi tout abonné doit être idempotent.

    Continuer malgré l'échec ne changerait rien à ce rejeu et rendrait le motif
    ambigu : on ne saurait plus lequel a échoué en premier, ni si les suivants
    ont échoué à cause de lui.
    """
    for abonne in abonnes:
        try:
            abonne(evenement)
        except Exception as echec:  # noqa: BLE001 — on veut le motif, quel qu'il soit
            return f"{getattr(abonne, '__name__', abonne)} : {echec}"
    return None


def journal_du_passage(rapport: RapportPublication) -> Mapping[str, object]:
    """Ce qu'on écrit au journal, sans le contenu des événements.

    On journalise des **identifiants** et des **décisions**, jamais des
    contenus : un journal circule, se copie, et n'a pas le régime de protection
    d'une base métier.
    """
    return {
        "publies": rapport.publies,
        "sans_abonne": rapport.sans_abonne,
        "echecs": rapport.echecs,
        "retenus": rapport.retenus,
        "quarantaine": rapport.mis_en_quarantaine,
        "motifs": list(rapport.motifs),
    }
