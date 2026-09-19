"""Les cas d'usage du rapprochement bancaire (pas 101).

─────────────────────────────────────────────────────────────────────────────────
CE QUE FAIT CHAQUE CAS D'USAGE

* `importer_un_releve` : vérifie que le journal est un journal de trésorerie, que la
  période ne chevauche pas un autre relevé du même journal, puis **rapproche
  automatiquement** ce qui ne fait aucun doute. L'écran ne montre ensuite que ce qui
  résiste, avec l'écart chiffré en tête.
* `rapprocher_une_ligne`, `dissocier_une_ligne`, `justifier_une_ligne` : les gestes du
  comptable sur une ligne.
* `valider_le_rapprochement` : arrête l'état de rapprochement, qui ne bougera plus.

Aucun de ces cas d'usage ne lit l'horloge, la session ou la base : la route passe l'instant,
la personne, les écritures et le dépôt. C'est ce qui les rend testables sans rien d'autre.

⚠️ POURQUOI L'AUTOMATIQUE EST PRUDENT

Il n'apparie que si **une seule** écriture correspond **fortement**, et jamais deux lignes
du relevé sur la même écriture. Deux virements de 250 000 le même jour au même fournisseur
restent au comptable : l'automatique ne tire pas au sort.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app.contextes.comptabilite.domaine.entites import Journal, NatureJournal
from app.contextes.comptabilite.domaine.ports import DepotRapprochements
from app.contextes.comptabilite.domaine.rapprochement import (
    ForceDeCorrespondance,
    LigneDeReleve,
    ModeAppariement,
    MouvementComptable,
    NatureJustification,
    RapprochementBancaire,
    RapprochementRefuse,
    ReglagesDuRapprochement,
    StatutRapprochement,
    etat_du_rapprochement,
    proposer_des_correspondances,
)

__all__ = [
    "RapprochementIntrouvable",
    "abandonner_un_rapprochement",
    "dissocier_une_ligne",
    "importer_un_releve",
    "justifier_une_ligne",
    "rapproches_par_les_autres",
    "rapprocher_une_ligne",
    "valider_le_rapprochement",
]


class RapprochementIntrouvable(LookupError):
    """Aucun rapprochement de ce nom sur ce dossier. Rendu en 404."""


def _lire(depot: DepotRapprochements, dossier: str, identifiant: str) -> RapprochementBancaire:
    for rapprochement in depot.du_dossier(dossier):
        if rapprochement.identifiant == identifiant:
            return rapprochement
    raise RapprochementIntrouvable(f"rapprochement « {identifiant} » inconnu sur ce dossier.")


def rapproches_par_les_autres(
    depot: DepotRapprochements, rapprochement: RapprochementBancaire
) -> set[tuple[str, int]]:
    """Les lignes d'écriture déjà rapprochées par un **autre** relevé du même journal.

    Un rapprochement abandonné ne retient rien : il a été importé par erreur.
    """
    return {
        designation
        for autre in depot.du_dossier(rapprochement.dossier)
        if autre.journal == rapprochement.journal
        and autre.identifiant != rapprochement.identifiant
        and autre.statut is not StatutRapprochement.ABANDONNE
        for designation in autre.designations
    }


def importer_un_releve(
    *,
    dossier: str,
    journal: Journal,
    exercice: str,
    du: date,
    au: date,
    solde_initial: Decimal,
    solde_final: Decimal,
    lignes: list[LigneDeReleve],
    source: str,
    mouvements: list[MouvementComptable],
    reglages: ReglagesDuRapprochement,
    par: str,
    le: datetime,
    depot: DepotRapprochements,
) -> RapprochementBancaire:
    if journal.nature not in (NatureJournal.BANQUE,) or journal.compte_contrepartie is None:
        raise RapprochementRefuse(
            f"le journal {journal.code} ({journal.intitule}) n'est pas un journal de banque : "
            "seul un compte tenu par un établissement se rapproche d'un relevé. La caisse se "
            "contrôle par un comptage, pas par un relevé."
        )
    existants = depot.du_dossier(dossier)
    for autre in existants:
        if (
            autre.journal == journal.code
            and autre.statut is not StatutRapprochement.ABANDONNE
            and autre.du <= au
            and du <= autre.au
        ):
            raise RapprochementRefuse(
                f"la période chevauche le relevé du {autre.du:%d/%m/%Y} au {autre.au:%d/%m/%Y} "
                f"({autre.identifiant}, {autre.statut.value.lower()}). Une opération ne se "
                "rapproche qu'une fois ; abandonner l'autre relevé s'il a été importé par erreur."
            )
    rang = 1 + sum(1 for a in existants if a.journal == journal.code)
    try:
        rapprochement = RapprochementBancaire(
            identifiant=f"RB-{journal.code}-{au:%Y%m%d}-{rang}",
            dossier=dossier,
            journal=journal.code,
            compte=journal.compte_contrepartie,
            exercice=exercice,
            du=du,
            au=au,
            solde_initial=solde_initial,
            solde_final=solde_final,
            lignes=lignes,
            source=source,
            importe_par=par,
            importe_le=le,
        )
    except ValueError as erreur:
        # Les invariants du relevé (période, solde qui tombe juste) parlent déjà en clair.
        raise RapprochementRefuse(_premier_message(erreur)) from erreur

    deja = rapproches_par_les_autres(depot, rapprochement)
    for ligne in rapprochement.lignes:
        propositions = [
            p
            for p in proposer_des_correspondances(ligne, mouvements, deja, reglages)
            if p.force is not ForceDeCorrespondance.A_ECARTER
        ]
        fortes = [p for p in propositions if p.force is ForceDeCorrespondance.FORTE]
        if len(fortes) != 1:
            continue
        choisie = fortes[0]
        rapprochement = rapprochement.apparier(
            ligne.rang,
            choisie.mouvement,
            mode=ModeAppariement.AUTOMATIQUE,
            motifs=choisie.motifs,
            par=par,
            le=le,
            deja_rapproches=deja,
        )
        deja = deja | {choisie.mouvement.designation}
    depot.enregistrer(rapprochement)
    return rapprochement


def _premier_message(erreur: ValueError) -> str:
    """Le message d'un invariant pydantic, sans l'enrobage technique."""
    erreurs = getattr(erreur, "errors", None)
    if callable(erreurs):
        for detail in erreurs():
            return str(detail.get("msg", erreur)).removeprefix("Value error, ")
    return str(erreur)


def rapprocher_une_ligne(
    *,
    dossier: str,
    identifiant: str,
    rang: int,
    ecriture: str,
    ligne: int,
    mouvements: list[MouvementComptable],
    reglages: ReglagesDuRapprochement,
    par: str,
    le: datetime,
    depot: DepotRapprochements,
) -> RapprochementBancaire:
    rapprochement = _lire(depot, dossier, identifiant)
    mouvement = next((m for m in mouvements if m.designation == (ecriture, ligne)), None)
    if mouvement is None:
        raise RapprochementRefuse(
            f"l'écriture {ecriture} ne porte pas de ligne {ligne + 1} sur le compte "
            f"{rapprochement.compte}."
        )
    deja = rapprochement.designations | rapproches_par_les_autres(depot, rapprochement)
    releve = rapprochement.ligne(rang)
    motifs = next(
        (
            p.motifs
            for p in proposer_des_correspondances(releve, mouvements, set(), reglages)
            if p.mouvement.designation == mouvement.designation
        ),
        [],
    )
    rapproche = rapprochement.apparier(
        rang,
        mouvement,
        mode=ModeAppariement.MANUEL,
        motifs=motifs,
        par=par,
        le=le,
        deja_rapproches=deja,
    )
    depot.enregistrer(rapproche)
    return rapproche


def dissocier_une_ligne(
    *, dossier: str, identifiant: str, rang: int, depot: DepotRapprochements
) -> RapprochementBancaire:
    dissocie = _lire(depot, dossier, identifiant).dissocier(rang)
    depot.enregistrer(dissocie)
    return dissocie


def justifier_une_ligne(
    *,
    dossier: str,
    identifiant: str,
    rang: int,
    nature: NatureJustification,
    motif: str,
    par: str,
    le: datetime,
    depot: DepotRapprochements,
) -> RapprochementBancaire:
    if len(motif.strip()) < 10:
        raise RapprochementRefuse(
            "le motif compte au moins 10 caractères : il se relit à la révision."
        )
    justifie = _lire(depot, dossier, identifiant).justifier(
        rang, nature=nature, motif=motif, par=par, le=le
    )
    depot.enregistrer(justifie)
    return justifie


def valider_le_rapprochement(
    *,
    dossier: str,
    identifiant: str,
    mouvements: list[MouvementComptable],
    par: str,
    le: datetime,
    depot: DepotRapprochements,
) -> RapprochementBancaire:
    rapprochement = _lire(depot, dossier, identifiant)
    etat = etat_du_rapprochement(
        rapprochement, mouvements, rapproches_par_les_autres(depot, rapprochement)
    )
    valide = rapprochement.valider(etat, mouvements, par=par, le=le)
    depot.enregistrer(valide)
    return valide


def abandonner_un_rapprochement(
    *, dossier: str, identifiant: str, motif: str, depot: DepotRapprochements
) -> RapprochementBancaire:
    abandonne = _lire(depot, dossier, identifiant).abandonner(motif)
    depot.enregistrer(abandonne)
    return abandonne
