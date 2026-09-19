"""Le plan de travail d'un collaborateur : par quoi commencer (pas 104).

─────────────────────────────────────────────────────────────────────────────────
LA QUESTION À LAQUELLE CET OBJET RÉPOND

« Par quoi je commence ? » La maquette du parcours comptable (vue A) y répond par une file
ordonnée **par échéance, puis par gravité**, sur les seuls dossiers du collaborateur. Chaque
ligne est une tâche concrète, avec son volume et un lien vers l'écran qui la fait :

    traiter les pièces reçues        → la boîte de réception
    relancer les pièces manquantes   → les pièces attendues
    préparer une déclaration         → l'échéancier
    rapprocher un relevé bancaire    → le rapprochement (pas 101)
    reprendre les remarques          → la revue renvoyée (pas 102)
    transmettre un mois au réviseur  → la revue des dossiers (pas 102)

⚠️ LE PLAN NE STOCKE RIEN ET NE DÉCIDE DE RIEN

Il **relit** l'état des dossiers et en déduit des tâches. Une tâche faite disparaît parce que
l'état a changé (la pièce est traitée, le relevé est rapproché), jamais parce qu'on l'a
cochée. Une liste de tâches cochables vieillirait sans le dire : « fait » sans que ce soit fait.

⚠️ CE QUI EST RÉGLÉ PAR LE CABINET, ET NON ÉCRIT ICI

Les délais internes (traiter une pièce en combien de jours, rapprocher le relevé avant quel
jour du mois suivant, transmettre le mois avant quel jour), l'horizon des déclarations, les
seuils d'urgence et l'ordre de gravité des natures de tâche sont lus dans
`Docs/referentiel/pilotage/plan_de_travail.yaml`. Ce sont des politiques internes : elles
appartiennent au cabinet.

Ce module est **pur** : il reçoit l'état des dossiers déjà relevé par la route, comme
`evaluer_le_risque` reçoit ses observations. Relever mal et ordonner mal échouent
différemment ; les séparer permet de savoir lequel corriger.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "DeclarationAPreparer",
    "EtatDuDossierPourLePlan",
    "NatureDeTache",
    "PrioriteDeTache",
    "ReglagesDuPlan",
    "Tache",
    "planifier",
]


class NatureDeTache(StrEnum):
    TRAITER_PIECES = "TRAITER_PIECES"
    RELANCER_PIECES = "RELANCER_PIECES"
    PREPARER_DECLARATION = "PREPARER_DECLARATION"
    RAPPROCHER_RELEVE = "RAPPROCHER_RELEVE"
    REPRENDRE_REMARQUES = "REPRENDRE_REMARQUES"
    TRANSMETTRE_MOIS = "TRANSMETTRE_MOIS"


class PrioriteDeTache(StrEnum):
    URGENTE = "URGENTE"
    ELEVEE = "ELEVEE"
    NORMALE = "NORMALE"


class ReglagesDuPlan(BaseModel):
    """`Docs/referentiel/pilotage/plan_de_travail.yaml`. Valeurs par défaut sobres."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Une pièce reçue doit être traitée dans ce délai : l'échéance de la tâche est la
    #: réception de la plus ancienne pièce en attente, plus ce délai.
    delai_de_traitement_des_pieces_jours: int = Field(5, ge=0, le=60)
    #: Le relevé d'un mois se rapproche avant ce jour du mois suivant.
    rapprochement_avant_le_jour: int = Field(10, ge=1, le=28)
    #: Le mois se transmet au réviseur avant ce jour du mois suivant.
    transmission_avant_le_jour: int = Field(20, ge=1, le=28)
    #: Les déclarations dont l'échéance tombe dans cet horizon entrent dans le plan.
    horizon_des_declarations_jours: int = Field(15, ge=0, le=90)
    #: Échue ou à moins de ce nombre de jours : urgente.
    urgente_sous_jours: int = Field(2, ge=0, le=30)
    #: À moins de ce nombre de jours : élevée.
    elevee_sous_jours: int = Field(7, ge=0, le=60)
    #: À échéance égale, l'ordre des natures : la première est la plus grave.
    ordre_de_gravite: tuple[NatureDeTache, ...] = (
        NatureDeTache.PREPARER_DECLARATION,
        NatureDeTache.REPRENDRE_REMARQUES,
        NatureDeTache.TRAITER_PIECES,
        NatureDeTache.RAPPROCHER_RELEVE,
        NatureDeTache.TRANSMETTRE_MOIS,
        NatureDeTache.RELANCER_PIECES,
    )
    source: str = "valeurs par défaut"

    @model_validator(mode="after")
    def _coherents(self) -> ReglagesDuPlan:
        if self.urgente_sous_jours > self.elevee_sous_jours:
            raise ValueError("urgente_sous_jours dépasse elevee_sous_jours.")
        if sorted(self.ordre_de_gravite) != sorted(NatureDeTache):
            # Une nature absente de l'ordre n'aurait pas de rang, et se trierait au hasard.
            raise ValueError(
                "ordre_de_gravite doit nommer chaque nature de tâche exactement une fois : "
                + ", ".join(n.value for n in NatureDeTache)
            )
        return self


class DeclarationAPreparer(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    libelle: str
    periode_debut: date
    echeance: date


class EtatDuDossierPourLePlan(BaseModel):
    """Ce que la route a relevé sur un dossier. Des références, jamais des comptes seuls."""

    model_config = ConfigDict(frozen=True)

    niu: str
    denomination: str
    #: Les pièces reçues non traitées, avec leur date de réception.
    pieces_a_traiter: tuple[tuple[str, date], ...] = ()
    #: Les demandes ouvertes échues : c'est l'adhérent qu'il faut relancer.
    demandes_echues: tuple[str, ...] = ()
    #: Les obligations non déposées, toutes échéances confondues : le plan filtre.
    declarations: tuple[DeclarationAPreparer, ...] = ()
    #: Les journaux de banque mouvementés sur le mois précédent sans rapprochement arrêté
    #: couvrant sa fin.
    releves_a_rapprocher: tuple[str, ...] = ()
    #: La revue renvoyée au comptable, et ses remarques ouvertes.
    revue_renvoyee: tuple[str, int] | None = None
    #: Vrai si le mois précédent a des écritures et n'a jamais été transmis.
    mois_precedent_a_transmettre: bool = False


class Tache(BaseModel):
    model_config = ConfigDict(frozen=True)

    nature: NatureDeTache
    dossier: str
    denomination: str
    libelle: str
    #: « 3 pièces », « juillet », « 2 remarques ».
    volume: str
    echeance: date | None
    en_retard: bool
    priorite: PrioriteDeTache
    #: L'écran qui fait la tâche, sans le préfixe de langue.
    lien: str
    elements: tuple[str, ...] = ()


_MOIS = [
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
]


def _pluriel(n: int, mot: str) -> str:
    return f"{n} {mot}{'s' if n > 1 else ''}"


def planifier(
    etats: list[EtatDuDossierPourLePlan], reglages: ReglagesDuPlan, jour: date
) -> list[Tache]:
    """Les tâches de tous les dossiers, **par échéance puis par gravité**.

    Une tâche sans échéance (rien ne la date) passe après toutes celles qui en ont une, mais
    avant rien : l'ordre des natures la départage.
    """
    mois_precedent_fin = jour.replace(day=1) - timedelta(days=1)
    mois_precedent = _MOIS[mois_precedent_fin.month - 1]

    def jour_du_mois_courant(n: int) -> date:
        # Les réglages bornent ces jours à 28 : tout mois les contient, février compris.
        return jour.replace(day=n)

    def priorite(echeance: date | None) -> PrioriteDeTache:
        if echeance is None:
            return PrioriteDeTache.NORMALE
        restant = (echeance - jour).days
        if restant <= reglages.urgente_sous_jours:
            return PrioriteDeTache.URGENTE
        if restant <= reglages.elevee_sous_jours:
            return PrioriteDeTache.ELEVEE
        return PrioriteDeTache.NORMALE

    taches: list[Tache] = []

    def ajouter(nature, etat, libelle, volume, echeance, lien, elements=()):
        taches.append(
            Tache(
                nature=nature,
                dossier=etat.niu,
                denomination=etat.denomination,
                libelle=libelle,
                volume=volume,
                echeance=echeance,
                en_retard=echeance is not None and echeance < jour,
                priorite=priorite(echeance),
                lien=lien,
                elements=tuple(elements),
            )
        )

    for etat in etats:
        if etat.pieces_a_traiter:
            plus_ancienne = min(recue for _, recue in etat.pieces_a_traiter)
            ajouter(
                NatureDeTache.TRAITER_PIECES,
                etat,
                "Traiter les pièces reçues",
                _pluriel(len(etat.pieces_a_traiter), "pièce"),
                plus_ancienne + timedelta(days=reglages.delai_de_traitement_des_pieces_jours),
                f"/pieces?dossier={etat.niu}",
                [p for p, _ in etat.pieces_a_traiter],
            )
        if etat.demandes_echues:
            ajouter(
                NatureDeTache.RELANCER_PIECES,
                etat,
                "Relancer les pièces manquantes",
                _pluriel(len(etat.demandes_echues), "pièce"),
                None,
                "/pieces/attendues",
                etat.demandes_echues,
            )
        # ⚠️ Les déclarations en retard sont **regroupées** en une tâche par dossier. Une par
        # obligation noyait le plan (79 tâches pour trois dossiers sur le jeu de démonstration),
        # et une file de 79 lignes ne dit plus par quoi commencer. Celles à venir dans l'horizon
        # restent distinctes : chacune a sa date, et c'est elle qui ordonne.
        horizon = jour + timedelta(days=reglages.horizon_des_declarations_jours)
        en_retard = sorted(
            (d for d in etat.declarations if d.echeance < jour), key=lambda d: d.echeance
        )
        if en_retard:
            ajouter(
                NatureDeTache.PREPARER_DECLARATION,
                etat,
                "Régulariser les déclarations en retard",
                _pluriel(len(en_retard), "déclaration"),
                en_retard[0].echeance,
                f"/obligations?dossier={etat.niu}",
                [f"{d.code} {d.periode_debut:%m/%Y}" for d in en_retard],
            )
        for declaration in sorted(etat.declarations, key=lambda d: d.echeance):
            if not (jour <= declaration.echeance <= horizon):
                continue
            debut = declaration.periode_debut
            ajouter(
                NatureDeTache.PREPARER_DECLARATION,
                etat,
                f"Préparer : {declaration.libelle}",
                f"{_MOIS[debut.month - 1]} {debut.year}",
                declaration.echeance,
                f"/obligations?dossier={etat.niu}",
                [f"{declaration.code} {debut:%m/%Y}"],
            )
        for journal in etat.releves_a_rapprocher:
            ajouter(
                NatureDeTache.RAPPROCHER_RELEVE,
                etat,
                f"Rapprocher le relevé ({journal})",
                mois_precedent,
                jour_du_mois_courant(reglages.rapprochement_avant_le_jour),
                f"/comptabilite/rapprochement?dossier={etat.niu}",
                [journal],
            )
        if etat.revue_renvoyee is not None:
            identifiant, ouvertes = etat.revue_renvoyee
            ajouter(
                NatureDeTache.REPRENDRE_REMARQUES,
                etat,
                "Reprendre les remarques du réviseur",
                _pluriel(ouvertes, "remarque"),
                # Un mois renvoyé attend : c'est à faire aujourd'hui.
                jour,
                f"/comptabilite/revues/{identifiant}?dossier={etat.niu}",
                [identifiant],
            )
        if etat.mois_precedent_a_transmettre:
            ajouter(
                NatureDeTache.TRANSMETTRE_MOIS,
                etat,
                "Transmettre le mois au réviseur",
                mois_precedent,
                jour_du_mois_courant(reglages.transmission_avant_le_jour),
                "/comptabilite/revues",
            )

    rang = {nature: i for i, nature in enumerate(reglages.ordre_de_gravite)}
    return sorted(
        taches,
        # Une tâche sans échéance prend la date la plus lointaine : elle passe après toutes.
        key=lambda t: (
            t.echeance or date.max,
            rang[t.nature],
            t.denomination,
        ),
    )
