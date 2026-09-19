"""La clôture mensuelle d'un dossier : vérifier, puis verrouiller en passant la main (pas 107).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE COMPTABLE FAIT EN FIN DE MOIS

Maquette « Parcours comptable », vue G (UC10, UC12) : « vérifier, verrouiller la période,
passer la main au réviseur ». Avant ce pas, le comptable transmettait un mois (pas 102) sans
qu'aucune vérification ne précède le geste, sauf l'absence de brouillon ; et une fois le mois
transmis, **rien n'empêchait d'y écrire encore**. Le réviseur relisait un mois qui pouvait
changer sous ses yeux.

Ce module apporte deux choses, et deux seulement.

1. **Les points de contrôle du mois.** Chacun est calculé sur les faits (écritures, pièces,
   rapprochements, écarts, demandes), jamais coché à la main : un point « traité » parce
   qu'on l'a déclaré tel ne prouve rien. Chaque point est **bloquant** ou **informatif**,
   selon le référentiel (`Docs/referentiel/cloture_mensuelle/reglages.yaml`).

2. **Le verrou de la période.** Un mois est verrouillé tant qu'une revue le couvre dans un
   statut qui verrouille (par défaut : transmis, validé). Le verrou n'est pas une donnée à
   part : il **se déduit** des revues. Il ne peut donc ni survivre à la revue, ni lui manquer.

⚠️ QUI DÉVERROUILLE ? LA QUESTION DE LA MAQUETTE, TRANCHÉE PAR LA CONFIGURATION

La maquette laisse ouvert : « le verrouillage est-il réversible par le réviseur seul, ou
faut-il l'accord de la direction ? Le parcours suppose le réviseur. » Ici, c'est le réviseur,
par le **renvoi** : un mois renvoyé avec des remarques n'est plus verrouillé, parce que le
comptable doit pouvoir corriger ce qu'on lui demande de corriger. Un mois **validé** reste
verrouillé ; une erreur découverte ensuite se corrige par une contre-passation datée du mois
ouvert, visible au grand livre. Un cabinet qui voudrait un autre partage modifie la liste
`statuts_qui_verrouillent`, sans toucher au code.

⚠️ CE QUE LE VERROU N'EST PAS

Il n'est pas la clôture de l'exercice (contexte clôture), qui ferme tout et pour toujours.
Il ne supprime rien, ne recalcule rien : il refuse d'écrire dans une période.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contextes.comptabilite.domaine.entites import EcritureComptable, EtatEcriture
from app.contextes.comptabilite.domaine.revue import RevueDeDossier, StatutRevue

__all__ = [
    "ChiffresDuMois",
    "CodePoint",
    "DemandeOuverte",
    "EcartEnSuspens",
    "PeriodeVerrouillee",
    "PieceDuMois",
    "PointDeCloture",
    "ReglageDuPoint",
    "ReglagesDeLaClotureMensuelle",
    "chiffrer_le_mois",
    "periode_verrouillee_au",
    "periodes_verrouillees",
    "points_de_cloture",
]


class CodePoint(StrEnum):
    """Les points de contrôle que la plateforme sait calculer.

    Le référentiel choisit lesquels s'appliquent et lesquels bloquent ; il ne peut pas en
    inventer un : un point sans calcul serait une case à cocher, et c'est ce qu'on refuse.
    """

    #: Hérités de la revue (pas 102) : même calcul, pour que le comptable voie avant de
    #: transmettre exactement ce que le réviseur verra après.
    BROUILLONS = "BROUILLONS"
    NUMEROTATION = "NUMEROTATION"
    EQUILIBRE = "EQUILIBRE"
    RAPPROCHEMENT = "RAPPROCHEMENT"
    #: Propres à la clôture (pas 107).
    PIECES_A_COMPTABILISER = "PIECES_A_COMPTABILISER"
    TRESORERIE = "TRESORERIE"
    ECARTS_EN_SUSPENS = "ECARTS_EN_SUSPENS"
    PIECES_ATTENDUES = "PIECES_ATTENDUES"


#: L'ordre d'affichage : d'abord ce que le comptable produit (pièces, banque, caisse,
#: balance), puis ce qui dépend des autres (l'adhérent, le second regard).
ORDRE_DES_POINTS: tuple[CodePoint, ...] = (
    CodePoint.PIECES_A_COMPTABILISER,
    CodePoint.BROUILLONS,
    CodePoint.RAPPROCHEMENT,
    CodePoint.TRESORERIE,
    CodePoint.EQUILIBRE,
    CodePoint.NUMEROTATION,
    CodePoint.PIECES_ATTENDUES,
    CodePoint.ECARTS_EN_SUSPENS,
)


class ReglageDuPoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Un point inactif n'est ni calculé ni affiché.
    actif: bool = True
    #: Un point bloquant non traité interdit la transmission ; un point informatif s'affiche.
    bloquant: bool = True


def _reglages_par_defaut() -> dict[CodePoint, ReglageDuPoint]:
    reglages = {code: ReglageDuPoint() for code in CodePoint}
    # « 2 pièces attendues non reçues · la déclaration sera établie sans elles » : la maquette
    # le montre en avertissement. Le cabinet n'a pas la main sur ce que l'adhérent envoie.
    reglages[CodePoint.PIECES_ATTENDUES] = ReglageDuPoint(bloquant=False)
    return reglages


class ReglagesDeLaClotureMensuelle(BaseModel):
    """`Docs/referentiel/cloture_mensuelle/reglages.yaml`. Valeurs de départ, à arrêter par le
    cabinet."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    points: dict[CodePoint, ReglageDuPoint] = Field(default_factory=_reglages_par_defaut)
    #: Les statuts de revue qui verrouillent la période. Voir l'en-tête du module.
    statuts_qui_verrouillent: frozenset[StatutRevue] = frozenset(
        {StatutRevue.TRANSMISE, StatutRevue.VALIDEE}
    )
    #: Les racines des comptes de caisse, dont le solde ne peut pas être négatif (SYSCOHADA :
    #: 57, caisse). Une caisse ne se vide pas en dessous de zéro ; un solde négatif dit une
    #: sortie saisie avant l'entrée, ou une entrée oubliée.
    racines_de_caisse: tuple[str, ...] = ("57",)
    #: Les racines des comptes d'achats, pour « le mois en chiffres » (SYSCOHADA : 60).
    racines_d_achats: tuple[str, ...] = ("60",)
    source: str = "valeurs par défaut"

    @model_validator(mode="before")
    @classmethod
    def _completer_les_points(cls, donnees):
        # Un fichier qui ne règle que deux points garde les autres à leur défaut : oublier un
        # point dans le YAML ne doit pas le faire disparaître en silence.
        if isinstance(donnees, dict) and isinstance(donnees.get("points"), dict):
            complets = {code.value: ReglageDuPoint() for code in CodePoint}
            complets[CodePoint.PIECES_ATTENDUES.value] = ReglageDuPoint(bloquant=False)
            complets.update(donnees["points"])
            donnees = {**donnees, "points": complets}
        return donnees

    @model_validator(mode="after")
    def _les_brouillons_bloquent_toujours(self) -> ReglagesDeLaClotureMensuelle:
        # ⚠️ La transmission refuse un brouillon quoi qu'on écrive ici (pas 102) : un brouillon
        # peut encore changer, et le réviser serait relire un texte qui n'est pas arrêté. Un
        # réglage qui dirait le contraire mentirait à l'écran ; on le refuse au chargement.
        reglage = self.points[CodePoint.BROUILLONS]
        if not (reglage.actif and reglage.bloquant):
            raise ValueError(
                "le point BROUILLONS est toujours actif et bloquant : la transmission refuse un "
                "mois qui en contient, quel que soit ce réglage."
            )
        if not self.statuts_qui_verrouillent <= {StatutRevue.TRANSMISE, StatutRevue.VALIDEE}:
            raise ValueError(
                "statuts_qui_verrouillent n'admet que TRANSMISE et VALIDEE : un mois RENVOYE doit "
                "rester corrigeable, puisqu'on demande au comptable de le corriger."
            )
        return self


# ── Ce que la route relève chez les voisins ──────────────────────────────────
#
# Le domaine ne connaît ni la collecte ni la conformité : la route traduit leurs objets en
# ces formes minimales. C'est aussi ce qui rend chaque point testable sans monter un contexte.


class PieceDuMois(BaseModel):
    model_config = ConfigDict(frozen=True)

    identifiant: str
    reference: str | None
    #: L'état de la pièce, tel que la collecte le nomme (RECUE, LUE, RAPPROCHEE, COMPTABILISEE,
    #: ARCHIVEE).
    etat: str
    #: La date du document si elle est lue, sinon la date de réception.
    date: date
    recue_le: date


class DemandeOuverte(BaseModel):
    model_config = ConfigDict(frozen=True)

    identifiant: str
    motif: str
    demandee_le: date
    derniere_relance: date | None = None


class EcartEnSuspens(BaseModel):
    """Un écart de constat proposé, qui attend le second regard (pas 92)."""

    model_config = ConfigDict(frozen=True)

    identifiant: str
    reference_document: str
    code_regle: str
    severite: str


class PointDeCloture(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: CodePoint
    titre: str
    detail: str
    traite: bool
    bloquant: bool
    #: Les objets en cause (écritures, pièces, demandes, écarts) : un point non traité qui ne
    #: dirait pas lesquels obligerait à les chercher.
    references: tuple[str, ...] = ()

    @property
    def bloque(self) -> bool:
        return self.bloquant and not self.traite


def _jj_mm(jour: date) -> str:
    return f"{jour:%d/%m}"


def _points_de_la_piece(pieces: list[PieceDuMois], du: date, au: date) -> PointDeCloture:
    # ⚠️ Une pièce ARCHIVEE (classée sans écriture, pas 107) est traitée : elle compte parmi
    # les pièces du mois, pas parmi celles qui restent.
    du_mois = [p for p in pieces if du <= p.date <= au]
    restantes = sorted(
        (p for p in du_mois if p.etat not in {"COMPTABILISEE", "ARCHIVEE"}),
        key=lambda p: p.identifiant,
    )
    comptabilisees = len(du_mois) - len(restantes)
    return PointDeCloture(
        code=CodePoint.PIECES_A_COMPTABILISER,
        titre=(
            "Toutes les pièces du mois sont traitées"
            if not restantes
            else f"{len(restantes)} pièce{'s' if len(restantes) > 1 else ''} du mois "
            "à comptabiliser ou classer"
        ),
        detail=(
            f"{len(du_mois)} pièce{'s' if len(du_mois) > 1 else ''} du mois · "
            f"{comptabilisees} traitée{'s' if comptabilisees > 1 else ''}"
            if du_mois
            else "aucune pièce datée du mois"
        )
        + (
            " · à traiter : "
            + ", ".join(f"{p.reference or p.identifiant} ({p.etat.lower()})" for p in restantes[:5])
            + ("…" if len(restantes) > 5 else "")
            if restantes
            else ""
        ),
        traite=not restantes,
        bloquant=True,
        references=tuple(p.identifiant for p in restantes),
    )


def _point_de_tresorerie(
    ecritures_de_l_exercice: list[EcritureComptable], au: date, racines: tuple[str, ...]
) -> PointDeCloture:
    """Le solde de chaque compte de caisse, **en fin de journée**, jusqu'à la fin du mois.

    ⚠️ En fin de journée, et non écriture par écriture : l'ordre de saisie d'une même journée
    ne dit rien de l'ordre des opérations. Une sortie saisie avant l'entrée du même jour n'est
    pas une anomalie ; un solde négatif le soir en est une.

    Depuis le début de l'exercice, à-nouveaux compris : un solde se lit en cumul.
    """
    mouvements: dict[str, dict[date, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for e in ecritures_de_l_exercice:
        if e.etat is not EtatEcriture.VALIDEE or e.date_operation > au:
            continue
        for ligne in e.lignes:
            if ligne.compte.startswith(racines):
                signe = 1 if ligne.au_debit else -1
                mouvements[ligne.compte][e.date_operation] += signe * ligne.montant
    negatifs: list[str] = []
    for compte in sorted(mouvements):
        solde = Decimal(0)
        for jour in sorted(mouvements[compte]):
            solde += mouvements[compte][jour]
            if solde < 0:
                negatifs.append(f"{compte} au {_jj_mm(jour)} ({solde:,.0f})".replace(",", " "))
                break
    return PointDeCloture(
        code=CodePoint.TRESORERIE,
        titre="Caisse cohérente" if not negatifs else "Solde de caisse négatif",
        detail=(
            "aucun compte de caisse mouvementé"
            if not mouvements
            else "aucun solde de caisse négatif en fin de journée"
            if not negatifs
            else "une caisse ne descend pas sous zéro : " + ", ".join(negatifs)
        ),
        traite=not negatifs,
        bloquant=True,
        references=tuple(n.split(" ")[0] for n in negatifs),
    )


def _point_des_demandes(demandes: list[DemandeOuverte], au: date) -> PointDeCloture:
    ouvertes = sorted(
        (d for d in demandes if d.demandee_le <= au), key=lambda d: (d.demandee_le, d.identifiant)
    )
    relances = sorted({d.derniere_relance for d in ouvertes if d.derniere_relance})
    return PointDeCloture(
        code=CodePoint.PIECES_ATTENDUES,
        titre=(
            "Aucune pièce attendue de l'adhérent"
            if not ouvertes
            else f"{len(ouvertes)} pièce{'s' if len(ouvertes) > 1 else ''} attendue"
            f"{'s' if len(ouvertes) > 1 else ''} non reçue{'s' if len(ouvertes) > 1 else ''}"
        ),
        detail=(
            "aucune demande ouverte"
            if not ouvertes
            else (
                f"dernière relance le {relances[-1]:%d/%m/%Y}" if relances else "jamais relancées"
            )
            + " · le mois sera transmis sans elles"
        ),
        traite=not ouvertes,
        bloquant=True,
        references=tuple(d.identifiant for d in ouvertes),
    )


def _point_des_ecarts(
    ecarts: list[EcartEnSuspens], pieces: list[PieceDuMois], du: date, au: date
) -> PointDeCloture:
    """Les écarts proposés sur une pièce du mois qui attendent le second regard.

    La maquette montre « 1 anomalie majeure écartée sans motif · un motif est obligatoire avant
    clôture ». Ici, un écart sans motif **ne peut pas exister** (pas 92) ; ce qui existe, c'est
    l'écart proposé que personne n'a encore tranché. Tant qu'il attend, le constat compte, et
    le mois transmis dirait une chose que le cabinet n'a pas encore décidée.
    """
    references_du_mois = {p.reference for p in pieces if p.reference and du <= p.date <= au}
    en_suspens = sorted(
        (e for e in ecarts if e.reference_document in references_du_mois),
        key=lambda e: (e.reference_document, e.code_regle),
    )
    return PointDeCloture(
        code=CodePoint.ECARTS_EN_SUSPENS,
        titre=(
            "Aucun écart de constat en attente"
            if not en_suspens
            else f"{len(en_suspens)} écart{'s' if len(en_suspens) > 1 else ''} de constat en "
            "attente du second regard"
        ),
        detail=(
            "tous les écarts proposés sur les pièces du mois sont tranchés"
            if not en_suspens
            else " · ".join(
                f"{e.reference_document} ({e.code_regle}, {e.severite.lower()})"
                for e in en_suspens[:5]
            )
            + " · le constat compte tant que l'écart n'est pas tranché"
        ),
        traite=not en_suspens,
        bloquant=True,
        references=tuple(e.identifiant for e in en_suspens),
    )


def points_de_cloture(
    *,
    du: date,
    au: date,
    points_de_la_revue: list,
    ecritures_de_l_exercice: list[EcritureComptable],
    pieces: list[PieceDuMois],
    demandes: list[DemandeOuverte],
    ecarts_en_suspens: list[EcartEnSuspens],
    reglages: ReglagesDeLaClotureMensuelle,
) -> list[PointDeCloture]:
    """Les points actifs, dans l'ordre d'affichage, avec leur caractère bloquant du référentiel.

    `points_de_la_revue` : les quatre points calculés par `points_de_la_periode` de la revue
    (brouillons, numérotation, équilibre, rapprochement). Ils ne sont **pas recalculés ici** :
    le comptable doit voir avant de transmettre exactement ce que le réviseur verra après.
    """
    titres = {
        "BROUILLONS": ("Aucune écriture en brouillon", "Écritures en brouillon sur le mois"),
        "NUMEROTATION": ("Numérotation continue", "Trou ou doublon dans la numérotation"),
        "EQUILIBRE": ("Balance du mois équilibrée", "Balance du mois déséquilibrée"),
        "RAPPROCHEMENT": ("Rapprochement bancaire à jour", "Rapprochement bancaire à arrêter"),
    }
    calcules: dict[CodePoint, PointDeCloture] = {}
    for point in points_de_la_revue:
        juste, faux = titres[point.code]
        calcules[CodePoint(point.code)] = PointDeCloture(
            code=CodePoint(point.code),
            titre=juste if point.conforme else faux,
            detail=point.detail,
            traite=point.conforme,
            bloquant=True,
        )
    calcules[CodePoint.PIECES_A_COMPTABILISER] = _points_de_la_piece(pieces, du, au)
    calcules[CodePoint.TRESORERIE] = _point_de_tresorerie(
        ecritures_de_l_exercice, au, reglages.racines_de_caisse
    )
    calcules[CodePoint.PIECES_ATTENDUES] = _point_des_demandes(demandes, au)
    calcules[CodePoint.ECARTS_EN_SUSPENS] = _point_des_ecarts(ecarts_en_suspens, pieces, du, au)
    return [
        calcules[code].model_copy(update={"bloquant": reglages.points[code].bloquant})
        for code in ORDRE_DES_POINTS
        if reglages.points[code].actif and code in calcules
    ]


class ChiffresDuMois(BaseModel):
    model_config = ConfigDict(frozen=True)

    pieces_recues: int
    ecritures_validees: int
    achats_du_mois: Decimal
    tva_rejetee: Decimal


def chiffrer_le_mois(
    *,
    du: date,
    au: date,
    ecritures_de_l_exercice: list[EcritureComptable],
    pieces: list[PieceDuMois],
    tva_rejetee_par_ecriture,
    reglages: ReglagesDeLaClotureMensuelle,
) -> ChiffresDuMois:
    """« Le mois en chiffres ». `tva_rejetee_par_ecriture` : la fonction des conséquences
    fiscales, passée pour que ce chiffre soit **le même** que celui de la déclaration."""
    validees = [
        e
        for e in ecritures_de_l_exercice
        if e.etat is EtatEcriture.VALIDEE and du <= e.date_operation <= au
    ]
    achats = sum(
        (
            l_.montant if l_.au_debit else -l_.montant
            for e in validees
            for l_ in e.lignes
            if l_.compte.startswith(reglages.racines_d_achats)
        ),
        Decimal(0),
    )
    return ChiffresDuMois(
        pieces_recues=sum(1 for p in pieces if du <= p.recue_le <= au),
        ecritures_validees=len(validees),
        achats_du_mois=achats,
        tva_rejetee=sum((tva_rejetee_par_ecriture(e) for e in validees), Decimal(0)),
    )


# ── Le verrou ─────────────────────────────────────────────────────────────────


class PeriodeVerrouillee(BaseModel):
    model_config = ConfigDict(frozen=True)

    du: date
    au: date
    revue: str
    statut: StatutRevue
    #: Le dernier passage de relais qui a verrouillé : qui, et quand.
    depuis: datetime
    par: str


def periodes_verrouillees(
    revues: list[RevueDeDossier], reglages: ReglagesDeLaClotureMensuelle
) -> list[PeriodeVerrouillee]:
    periodes = []
    for revue in revues:
        if revue.statut not in reglages.statuts_qui_verrouillent:
            continue
        dernier = revue.historique[-1]
        periodes.append(
            PeriodeVerrouillee(
                du=revue.du,
                au=revue.au,
                revue=revue.identifiant,
                statut=revue.statut,
                depuis=dernier.le,
                par=dernier.par,
            )
        )
    return sorted(periodes, key=lambda p: p.du)


def periode_verrouillee_au(
    jour: date, periodes: list[PeriodeVerrouillee] | tuple[PeriodeVerrouillee, ...]
) -> PeriodeVerrouillee | None:
    return next((p for p in periodes if p.du <= jour <= p.au), None)
