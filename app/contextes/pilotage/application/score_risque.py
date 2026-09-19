"""Le calcul du score de risque d'un dossier.

─────────────────────────────────────────────────────────────────────────────────
CE MODULE NE VA CHERCHER AUCUNE DONNÉE

Il reçoit des **observations** déjà relevées — combien d'anomalies bloquantes,
quelles obligations en retard, quelles pièces en souffrance — et les pondère. La
collecte se fait dans l'adaptateur entrant, qui seul sait interroger six contextes.

C'est ce qui rend le calcul testable sans monter une base, et surtout ce qui
permet de vérifier la pondération indépendamment de la collecte. Les deux
échouent différemment : une collecte fausse compte mal, une pondération fausse
classe mal, et confondre les deux dans un même module rendrait le diagnostic
impossible.

LA PONDÉRATION VIENT DU RÉFÉRENTIEL, TOUJOURS

« La pondération des composantes du score appartient à la direction : elle se
règle au référentiel, pas dans le code. » — § 01 du dossier d'architecture. Un
poids absent du référentiel vaut **zéro** et le signale : la composante cesse de
contribuer plutôt que de contribuer d'un montant inventé. C'est le seul défaut
acceptable ici — un score sous-estimé se voit quand le dossier explose, un score
calculé sur un poids fantaisiste ne se voit jamais.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.contextes.pilotage.domaine.entites import (
    ACTION_ATTENDUE,
    CODE_POIDS,
    ChargeCollaborateur,
    Composante,
    MesureComposante,
    ScoreRisque,
)
from app.contextes.referentiel.api import (
    AucuneVersionApplicable,
    ParametreInconnu,
    ServiceParametres,
    StatutValidation,
)

__all__ = ["ObservationsDossier", "evaluer_le_risque", "repartir_la_charge"]

#: Le libellé de chaque composante, au singulier et au pluriel.
#:
#: Deux formes plutôt qu'un « (s) » : un tableau de bord se lit vite, et
#: « 1 anomalie(s) bloquante(s) » fait buter l'œil sur la parenthèse au lieu du
#: chiffre.
_LIBELLES: dict[Composante, tuple[str, str]] = {
    Composante.ANOMALIES_BLOQUANTES: (
        "anomalie bloquante non levée",
        "anomalies bloquantes non levées",
    ),
    Composante.RETARD_DECLARATIF: (
        "obligation échue et non déposée",
        "obligations échues et non déposées",
    ),
    Composante.PIECES_EN_SOUFFRANCE: (
        "pièce reçue et non traitée",
        "pièces reçues et non traitées",
    ),
    Composante.DEMANDES_SANS_REPONSE: (
        "demande restée sans réponse",
        "demandes restées sans réponse",
    ),
}


@dataclass(frozen=True)
class ObservationsDossier:
    """Ce qui a été relevé sur un dossier, avant pondération.

    Les listes portent les **références** des éléments, pas leur compte : c'est
    ce qui permet au score de rester traçable jusqu'à la pièce. Passer des
    entiers ferait gagner trois lignes à l'adaptateur et perdrait la seule chose
    qui rende le score actionnable.
    """

    entreprise: str
    denomination: str
    anomalies_bloquantes: tuple[str, ...] = ()
    obligations_en_retard: tuple[str, ...] = ()
    pieces_en_souffrance: tuple[str, ...] = ()
    demandes_sans_reponse: tuple[str, ...] = ()
    #: Les comptes des collaborateurs qui suivent ce dossier. Sert à la charge,
    #: pas au score : un dossier n'est pas plus risqué parce qu'il est suivi.
    suivi_par: tuple[str, ...] = field(default_factory=tuple)

    def elements_de(self, composante: Composante) -> tuple[str, ...]:
        return {
            Composante.ANOMALIES_BLOQUANTES: self.anomalies_bloquantes,
            Composante.RETARD_DECLARATIF: self.obligations_en_retard,
            Composante.PIECES_EN_SOUFFRANCE: self.pieces_en_souffrance,
            Composante.DEMANDES_SANS_REPONSE: self.demandes_sans_reponse,
        }[composante]


def evaluer_le_risque(
    observations: ObservationsDossier,
    parametres: ServiceParametres,
    a_la_date: date,
) -> ScoreRisque:
    """Pondère les observations et rend le score décomposé.

    Toutes les composantes figurent au résultat, **y compris celles à zéro**. Un
    tableau de bord qui n'afficherait que ce qui pèse laisserait croire que les
    autres n'ont pas été regardées, alors qu'elles l'ont été et qu'elles sont
    saines — ce qui est une information. `mesures_actives` filtre pour l'affichage
    resserré.
    """
    mesures: list[MesureComposante] = []
    for composante in Composante:
        elements = observations.elements_de(composante)
        poids, non_valide = _poids(composante, parametres, a_la_date)
        singulier, pluriel = _LIBELLES[composante]
        mesures.append(
            MesureComposante(
                composante=composante,
                libelle=singulier if len(elements) == 1 else pluriel,
                occurrences=len(elements),
                poids=poids,
                elements=elements,
                action=ACTION_ATTENDUE[composante],
                poids_non_valide=non_valide,
            )
        )

    modere, eleve = _seuils(parametres, a_la_date)
    return ScoreRisque(
        entreprise=observations.entreprise,
        denomination=observations.denomination,
        mesures=tuple(mesures),
        seuil_modere=modere,
        seuil_eleve=eleve,
    )


def _poids(
    composante: Composante, parametres: ServiceParametres, a_la_date: date
) -> tuple[Decimal, bool]:
    """Le poids d'une composante, et s'il est encore à arrêter.

    Un poids absent vaut zéro : la composante cesse de contribuer plutôt que de
    contribuer d'un montant inventé. Elle est alors marquée non arrêtée, et le
    tableau de bord le dit.
    """
    try:
        resolu = parametres.resoudre(CODE_POIDS[composante], a_la_date)
    except (ParametreInconnu, AucuneVersionApplicable):
        return Decimal(0), True
    return resolu.valeur_decimale, resolu.statut is not StatutValidation.VALIDE


def _seuils(parametres: ServiceParametres, a_la_date: date) -> tuple[Decimal, Decimal]:
    """Les deux seuils de classement, avec des valeurs de repli ordonnées.

    ⚠️ Le repli n'est pas un détail : `ScoreRisque` refuse des seuils inversés, et
    des paramètres absents ne doivent pas faire échouer un tableau de bord. Les
    valeurs de repli sont donc choisies dans le bon ordre, et le résultat reste
    lisible même sur un référentiel incomplet.
    """
    modere = _valeur(parametres, "SEUIL_RISQUE_MODERE", a_la_date, Decimal(25))
    eleve = _valeur(parametres, "SEUIL_RISQUE_ELEVE", a_la_date, Decimal(60))
    if modere >= eleve:
        # Le référentiel est incohérent. On ne classe pas de travers : on retombe
        # sur des seuils par défaut ordonnés, ce que l'écran signale par ailleurs
        # via `repose_sur_des_poids_non_arretes`.
        return Decimal(25), Decimal(60)
    return modere, eleve


def _valeur(
    parametres: ServiceParametres, code: str, a_la_date: date, repli: Decimal
) -> Decimal:
    try:
        return parametres.resoudre(code, a_la_date).valeur_decimale
    except (ParametreInconnu, AucuneVersionApplicable):
        return repli


def repartir_la_charge(
    scores: list[ScoreRisque],
    suivi: dict[str, tuple[str, ...]],
    noms: dict[str, str],
) -> list[ChargeCollaborateur]:
    """La charge par collaborateur, à partir des scores des dossiers qu'il suit.

    `suivi` associe chaque NIU aux comptes qui le suivent ; `noms` donne le nom
    complet de chaque compte.

    ⚠️ **Un dossier suivi par deux personnes compte pour les deux**, et son risque
    aussi. Le répartir par moitié donnerait des demi-dossiers, ce qui ne veut rien
    dire : les deux collaborateurs ont bien le dossier entier sur les bras. La
    somme des charges dépasse donc le nombre de dossiers du cabinet, et c'est
    correct.
    """
    par_compte: dict[str, list[ScoreRisque]] = {}
    for score in scores:
        for compte in suivi.get(score.entreprise, ()):
            par_compte.setdefault(compte, []).append(score)

    charges = [
        ChargeCollaborateur(
            compte=compte,
            nom_complet=noms.get(compte, compte),
            dossiers=len(portes),
            risque_porte=sum((s.total for s in portes), Decimal(0)),
            dossiers_a_risque_eleve=sum(1 for s in portes if s.niveau.value == "ELEVE"),
        )
        for compte, portes in par_compte.items()
    ]
    # Du plus chargé au moins chargé : la direction rééquilibre par le haut.
    return sorted(charges, key=lambda c: (c.risque_porte, c.dossiers), reverse=True)
