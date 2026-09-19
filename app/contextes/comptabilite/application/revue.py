"""Les cas d'usage de la revue d'un mois transmis (pas 102).

Aucun ne lit l'horloge, la session ni la base : la route passe l'instant, la personne, les
écritures et le dépôt. Voir l'en-tête de `domaine/revue.py` pour le circuit.

⚠️ CE QUI EST VÉRIFIÉ ICI ET NON DANS L'ENTITÉ

Ce qui demande de connaître les écritures : qu'un mois transmis n'a plus de brouillon,
qu'il ne chevauche pas un mois déjà transmis, et qu'une remarque désigne un objet qui existe
vraiment sur la période. L'entité, elle, ne connaît que la revue.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.contextes.comptabilite.domaine.cloture_mensuelle import PointDeCloture
from app.contextes.comptabilite.domaine.entites import EcritureComptable, EtatEcriture
from app.contextes.comptabilite.domaine.ports import DepotRevues
from app.contextes.comptabilite.domaine.projections import (
    balance,
    controle_balance_equilibree,
    sequences_incompletes,
)
from app.contextes.comptabilite.domaine.rapprochement import (
    RapprochementBancaire,
    StatutRapprochement,
)
from app.contextes.comptabilite.domaine.revue import (
    NatureObjet,
    ObjetDeRemarque,
    PassageDeRelais,
    ReglagesDeLEchantillon,
    RevueDeDossier,
    RevueRefusee,
    StatutRevue,
    echantillonner,
)

__all__ = [
    "PointDeControle",
    "RevueIntrouvable",
    "du_mois",
    "exiger_une_cloture_sans_point_bloquant",
    "points_de_controle",
    "points_de_la_periode",
    "remarquer",
    "transmettre_un_mois",
]


class RevueIntrouvable(LookupError):
    """Aucune revue de ce nom sur ce dossier. Rendue en 404."""


def du_mois(ecritures: list[EcritureComptable], du: date, au: date) -> list[EcritureComptable]:
    return [e for e in ecritures if du <= e.date_operation <= au]


def transmettre_un_mois(
    *,
    dossier: str,
    exercice: str,
    du: date,
    au: date,
    ecritures_de_l_exercice: list[EcritureComptable],
    reglages: ReglagesDeLEchantillon,
    par: str,
    compte: str,
    le: datetime,
    message: str | None,
    depot: DepotRevues,
    points_de_cloture: list[PointDeCloture],
) -> RevueDeDossier:
    """Transmet la période au réviseur, ce qui la **verrouille** (pas 107).

    `points_de_cloture` : les points de la clôture mensuelle, calculés par l'appelant sur les
    mêmes faits. Exigé sans défaut : transmettre sans les avoir regardés, c'est ce que ce pas
    retire.
    """
    if du > au:
        raise RevueRefusee("la période commence après sa fin.")
    if au >= le.date():
        # ⚠️ Pas 107 : transmettre verrouille. Verrouiller un mois qui n'est pas fini
        # interdirait d'y saisir les opérations des jours qui restent.
        raise RevueRefusee(
            f"la période se termine le {au:%d/%m/%Y} : elle n'est pas finie, et la transmettre "
            "la verrouillerait avant que ses dernières opérations soient saisies."
        )
    mois = du_mois(ecritures_de_l_exercice, du, au)
    if not mois:
        raise RevueRefusee(
            f"aucune écriture du {du:%d/%m/%Y} au {au:%d/%m/%Y} : il n'y a rien à réviser."
        )
    brouillons = sorted(e.cle for e in mois if e.etat is EtatEcriture.BROUILLON)
    if brouillons:
        # ⚠️ Un brouillon peut encore changer : le réviser, c'est relire un texte qui n'est pas
        # arrêté. Le comptable valide (ou corrige) d'abord.
        raise RevueRefusee(
            f"{len(brouillons)} écriture(s) en brouillon sur la période "
            f"({', '.join(brouillons[:5])}{'…' if len(brouillons) > 5 else ''}) : "
            "les valider avant de transmettre."
        )
    exiger_une_cloture_sans_point_bloquant(points_de_cloture)
    for autre in depot.du_dossier(dossier):
        if autre.du <= au and du <= autre.au:
            raise RevueRefusee(
                f"la période chevauche la revue du {autre.du:%d/%m/%Y} au {autre.au:%d/%m/%Y} "
                f"({autre.statut.value.lower()}). Un mois ne se révise qu'une fois."
            )
    try:
        revue = RevueDeDossier(
            identifiant=f"REV-{du:%Y%m%d}-{au:%Y%m%d}",
            dossier=dossier,
            exercice=exercice,
            du=du,
            au=au,
            statut=StatutRevue.TRANSMISE,
            transmise_par_compte=compte,
            transmise_par=par,
            echantillon=echantillonner(mois, ecritures_de_l_exercice, reglages),
            ecritures_du_mois=len(mois),
            historique=[
                PassageDeRelais(
                    statut=StatutRevue.TRANSMISE, par=par, compte=compte, le=le, message=message
                )
            ],
        )
    except ValueError as erreur:
        raise RevueRefusee(str(erreur)) from erreur
    depot.enregistrer(revue)
    return revue


def exiger_une_cloture_sans_point_bloquant(points: list[PointDeCloture]) -> None:
    """« La transmission reste désactivée tant qu'un point bloquant subsiste, avec le compte
    affiché sur le bouton : jamais de refus muet » (maquette, vue G, note 1).

    L'écran désactive le bouton ; ce contrôle-ci refuse la requête qui passerait quand même
    (un second onglet resté ouvert, un appel direct). Le message nomme chaque point.
    """
    bloquants = [p for p in points if p.bloque]
    if bloquants:
        raise RevueRefusee(
            f"{len(bloquants)} point{'s' if len(bloquants) > 1 else ''} bloquant"
            f"{'s' if len(bloquants) > 1 else ''} à traiter avant de transmettre : "
            + " ; ".join(f"{p.titre} ({p.detail})" for p in bloquants)
            + "."
        )


def remarquer(
    revue: RevueDeDossier,
    *,
    objet: ObjetDeRemarque,
    texte: str,
    ecritures_de_l_exercice: list[EcritureComptable],
    par: str,
    le: datetime,
) -> RevueDeDossier:
    """Pose une remarque, après avoir vérifié que l'objet existe **sur la période revue**.

    Une remarque sur `2026/AC/000999` qui n'existe pas, ou sur une écriture d'un autre mois,
    ne se retrouverait jamais : le comptable la lirait sans savoir où regarder.
    """
    mois = du_mois(ecritures_de_l_exercice, revue.du, revue.au)
    reference = objet.reference.strip()
    connus = {
        NatureObjet.ECRITURE: {e.cle for e in mois},
        NatureObjet.PIECE: {e.piece_justificative for e in mois if e.piece_justificative},
        NatureObjet.COMPTE: {l_.compte for e in mois for l_ in e.lignes},
    }[objet.nature]
    if reference not in connus:
        libelles = {
            NatureObjet.ECRITURE: "aucune écriture",
            NatureObjet.PIECE: "aucune pièce",
            NatureObjet.COMPTE: "aucun compte mouvementé",
        }
        raise RevueRefusee(
            f"{libelles[objet.nature]} « {reference} » sur la période du {revue.du:%d/%m/%Y} "
            f"au {revue.au:%d/%m/%Y} : une remarque se rattache à un objet de la revue."
        )
    return revue.remarquer(objet.model_copy(update={"reference": reference}), texte, par=par, le=le)


class PointDeControle(BaseModel):
    """Un contrôle automatique affiché en tête de la revue. Il **informe**, il ne bloque pas :
    le réviseur juge. Seuls les brouillons bloquent, et dès la transmission."""

    model_config = ConfigDict(frozen=True)

    code: str
    libelle: str
    conforme: bool
    detail: str


def points_de_controle(
    revue: RevueDeDossier,
    ecritures_de_l_exercice: list[EcritureComptable],
    journaux_de_banque: dict[str, str],
    rapprochements: list[RapprochementBancaire],
) -> list[PointDeControle]:
    """`journaux_de_banque` : code du journal vers son compte (`{"BQ": "521"}`)."""
    return points_de_la_periode(
        revue.du,
        revue.au,
        ecritures_de_l_exercice,
        journaux_de_banque,
        rapprochements,
        transmise=True,
    )


def points_de_la_periode(
    du: date,
    au: date,
    ecritures_de_l_exercice: list[EcritureComptable],
    journaux_de_banque: dict[str, str],
    rapprochements: list[RapprochementBancaire],
    *,
    transmise: bool,
) -> list[PointDeControle]:
    """Les quatre points sur une période, **avant ou après** sa transmission (pas 107).

    La clôture mensuelle du comptable les affiche avant de transmettre, la revue du réviseur
    après : un seul calcul, pour que les deux écrans ne puissent pas se contredire. Seule la
    phrase des brouillons change (« depuis la transmission » n'a de sens qu'après).
    """
    mois = du_mois(ecritures_de_l_exercice, du, au)
    points = []

    brouillons = [e.cle for e in mois if e.etat is EtatEcriture.BROUILLON]
    points.append(
        PointDeControle(
            code="BROUILLONS",
            libelle="Aucune écriture en brouillon sur la période",
            conforme=not brouillons,
            detail="toutes validées"
            if not brouillons
            else f"{len(brouillons)} en brouillon"
            + (" depuis la transmission" if transmise else "")
            + " : "
            + ", ".join(brouillons[:5]),
        )
    )

    trous = sequences_incompletes(ecritures_de_l_exercice)
    points.append(
        PointDeControle(
            code="NUMEROTATION",
            libelle="Numérotation continue des journaux de l'exercice",
            conforme=not trous,
            detail="sans trou ni doublon"
            if not trous
            else "; ".join(
                f"{t.journal} : manquants {t.numeros_manquants[:5]}, "
                f"doublons {t.numeros_en_double[:5]}"
                for t in trous
            ),
        )
    )

    equilibree = controle_balance_equilibree(balance(mois))
    points.append(
        PointDeControle(
            code="EQUILIBRE",
            libelle="Balance du mois équilibrée",
            conforme=equilibree,
            detail="débits égaux aux crédits" if equilibree else "débits et crédits diffèrent",
        )
    )

    mouvementes = sorted(
        {
            code
            for code, compte in journaux_de_banque.items()
            for e in mois
            if any(l_.compte.startswith(compte) for l_ in e.lignes)
        }
    )
    non_couverts = [
        code
        for code in mouvementes
        if not any(
            r.journal == code
            and r.statut is StatutRapprochement.VALIDE
            and r.du <= au <= r.au
            for r in rapprochements
        )
    ]
    points.append(
        PointDeControle(
            code="RAPPROCHEMENT",
            libelle="Comptes de banque rapprochés à la fin du mois",
            conforme=not non_couverts,
            detail=(
                "aucun compte de banque mouvementé"
                if not mouvementes
                else "rapprochement arrêté pour " + ", ".join(mouvementes)
                if not non_couverts
                else f"aucun rapprochement arrêté couvrant le {au:%d/%m/%Y} pour "
                + ", ".join(non_couverts)
            ),
        )
    )
    return points
