"""Le registre des immobilisations et leur amortissement (17 octobre 2026), sous `/comptabilite`.

─────────────────────────────────────────────────────────────────────────────────
TROIS GESTES

* tenir le registre : saisir ou modifier une fiche, sortir un bien (`SAISIR_ECRITURE`) ;
* lire le tableau de l'exercice, à l'écran ou en Excel (`LIRE_COMPTABILITE`) ;
* passer la dotation de l'exercice (`SAISIR_ECRITURE`) : une écriture au journal des
  opérations diverses, datée de la clôture, **en brouillon**. Elle se relit et se valide
  comme toute écriture (`VALIDER_ECRITURE`), puis passe à la revue du mois : le calcul
  propose, le comptable engage.

⚠️ UNE SEULE DOTATION VIVANTE PAR EXERCICE. L'écriture porte la référence
`DOTATIONS-<exercice>` ; une seconde est refusée tant que la première n'est pas
contre-passée. Le double clic ne double pas la charge.

Le calcul et ses règles : voir `domaine/immobilisations.py`.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, computed_field

from app.contextes.comptabilite.adaptateurs.entrant.routes_http import (
    depot,
    depot_des_journaux,
    depot_du_plan,
    dossier_du_portefeuille,
    exercice_du_portefeuille,
    periodes_verrouillees_du_dossier,
)
from app.contextes.comptabilite.adaptateurs.sortant.depots_immobilisations import (
    depot_des_immobilisations,
)
from app.contextes.comptabilite.api import (
    BrouillonEcriture,
    EcritureComptable,
    LigneEcriture,
    SaisieRefusee,
    balance,
    enregistrer_une_ecriture,
)
from app.contextes.comptabilite.domaine.entites import Sens
from app.contextes.comptabilite.domaine.immobilisations import (
    COMPTE_AMORTISSEMENT,
    COMPTE_DOTATION,
    CategorieImmobilisation,
    Immobilisation,
    ImmobilisationRefusee,
    TableauDesAmortissements,
    dotation_de_l_exercice,
    tableau_des_amortissements,
)
from app.contextes.transverse.api import Acces, AccesRequis, Permission, atelier, exiger_dossier
from app.partage.erreurs import message_lisible
from app.partage.horloge import maintenant

__all__ = ["routeur"]

routeur = APIRouter(prefix="/comptabilite", tags=["Comptabilité · immobilisations"])

_JOURNAL = "OD"


def _reference(exercice: str) -> str:
    return f"DOTATIONS-{exercice}"


def _tracer(acces: Acces, action: str, fiche: Immobilisation, **apres) -> None:
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action=f"comptabilite.{action}",
        objet_type="immobilisation",
        objet_id=f"{fiche.dossier}/{fiche.code}",
        apres={
            "libelle": fiche.libelle,
            "valeur_origine": str(fiche.valeur_origine),
            "mise_en_service": fiche.mise_en_service.isoformat(),
            "duree_annees": fiche.duree_annees,
            **apres,
        },
    )


class SaisieDImmobilisation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    libelle: str = Field(min_length=2, max_length=160)
    categorie: CategorieImmobilisation
    valeur_origine: Decimal = Field(gt=0, max_digits=15, decimal_places=0)
    mise_en_service: date
    duree_annees: int | None = Field(default=None, ge=1, le=50)
    fournisseur: str | None = Field(default=None, max_length=160)
    piece: str | None = Field(default=None, max_length=64)


class SortieDImmobilisation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sortie_le: date
    motif: str = Field(min_length=3, max_length=300)


def _fiche(entreprise: str, code: str) -> Immobilisation:
    fiche = depot_des_immobilisations().lire(entreprise, code)
    if fiche is None:
        raise HTTPException(status_code=404, detail=f"immobilisation {code} introuvable.")
    return fiche


def _bornes(entreprise: str, exercice: str):
    trouve = exercice_du_portefeuille(entreprise, exercice)
    if trouve is None:
        raise HTTPException(
            status_code=404, detail=f"exercice {exercice} inconnu du dossier {entreprise}."
        )
    return trouve


@routeur.get(
    "/dossiers/{entreprise}/immobilisations",
    summary="Le registre des immobilisations du dossier",
)
def lister_les_immobilisations(acces: AccesRequis, entreprise: str) -> list[Immobilisation]:
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    return depot_des_immobilisations().du_dossier(entreprise)


@routeur.put(
    "/dossiers/{entreprise}/immobilisations/{code}",
    summary="Saisir ou modifier une fiche d'immobilisation",
    responses={
        409: {"description": "Base figée par une dotation passée, ou bien sorti"},
        422: {"description": "Fiche incohérente (durée d'un terrain, code invalide)"},
    },
)
def enregistrer_une_immobilisation(
    acces: AccesRequis, entreprise: str, code: str, saisie: SaisieDImmobilisation
) -> Immobilisation:
    exiger_dossier(acces, Permission.SAISIR_ECRITURE, entreprise)
    depot_ = depot_des_immobilisations()
    code = code.strip().upper()
    existante = depot_.lire(entreprise, code)
    try:
        if existante is None:
            fiche = Immobilisation(
                dossier=entreprise,
                code=code,
                enregistree_par=acces.compte,
                enregistree_le=maintenant(),
                **saisie.model_dump(),
            )
        elif existante.sortie_le is not None:
            raise ImmobilisationRefusee(f"{code} est sorti du registre : il ne se modifie plus.")
        else:
            fiche = existante.modifier(**saisie.model_dump())
    except ImmobilisationRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    except (ValidationError, ValueError) as invalide:
        raise HTTPException(status_code=422, detail=message_lisible(invalide)) from invalide
    depot_.enregistrer(fiche)
    _tracer(
        acces, "immobilisation_saisie" if existante is None else "immobilisation_modifiee", fiche
    )
    return fiche


@routeur.post(
    "/dossiers/{entreprise}/immobilisations/{code}/sortie",
    summary="Sortir un bien du registre : cession, mise au rebut, erreur de saisie",
    responses={404: {"description": "Fiche inconnue"}, 409: {"description": "Déjà sorti"}},
)
def sortir_une_immobilisation(
    acces: AccesRequis, entreprise: str, code: str, sortie: SortieDImmobilisation
) -> Immobilisation:
    exiger_dossier(acces, Permission.SAISIR_ECRITURE, entreprise)
    fiche = _fiche(entreprise, code.strip().upper())
    if fiche.sortie_le is not None:
        raise HTTPException(status_code=409, detail=f"{fiche.code} est déjà sorti du registre.")
    try:
        sortie_ = fiche.modifier(sortie_le=sortie.sortie_le, motif_sortie=sortie.motif.strip())
    except (ValidationError, ValueError) as invalide:
        raise HTTPException(status_code=422, detail=message_lisible(invalide)) from invalide
    depot_des_immobilisations().enregistrer(sortie_)
    _tracer(
        acces,
        "immobilisation_sortie",
        sortie_,
        sortie_le=sortie.sortie_le.isoformat(),
        motif=sortie_.motif_sortie,
    )
    return sortie_


def _tableau(entreprise: str, exercice: str) -> TableauDesAmortissements:
    bornes = _bornes(entreprise, exercice)
    return tableau_des_amortissements(
        entreprise,
        exercice,
        bornes.ouverture,
        bornes.cloture,
        depot_des_immobilisations().du_dossier(entreprise),
    )


class ControleAvecLaBalance(BaseModel):
    """Le registre face à la balance des écritures validées, à la clôture.

    Un bien acheté et comptabilisé, mais oublié au registre, fausserait le tableau sans
    bruit : l'écart le montre. Une dotation encore en brouillon en fait un aussi, jusqu'à
    sa validation."""

    brut_registre: Decimal
    brut_balance: Decimal
    amortissements_registre: Decimal
    amortissements_balance: Decimal

    @computed_field
    @property
    def concorde(self) -> bool:
        return (
            self.brut_registre == self.brut_balance
            and self.amortissements_registre == self.amortissements_balance
        )


class TableauEtDotation(BaseModel):
    tableau: TableauDesAmortissements
    #: La clé de l'écriture de dotation vivante de l'exercice, s'il y en a une.
    ecriture_de_dotation: str | None
    controle: ControleAvecLaBalance


_RACINES_IMMOBILISEES = tuple(c.value for c in CategorieImmobilisation)


def _controle(entreprise: str, exercice: str, tableau: TableauDesAmortissements):
    soldes = balance(depot(entreprise).lister(exercice))
    brut = sum((s.solde for s in soldes if s.compte.startswith(_RACINES_IMMOBILISEES)), Decimal(0))
    amortis = -sum(
        (s.solde for s in soldes if s.compte.startswith(COMPTE_AMORTISSEMENT)), Decimal(0)
    )
    return ControleAvecLaBalance(
        brut_registre=tableau.total.brut_cloture,
        brut_balance=brut,
        amortissements_registre=tableau.total.amortissements_cloture,
        amortissements_balance=amortis,
    )


def _dotation_vivante(entreprise: str, exercice: str) -> EcritureComptable | None:
    ecritures = depot(entreprise).lister(exercice)
    annulees = {e.ecriture_contrepassee for e in ecritures if e.ecriture_contrepassee}
    return next(
        (
            e
            for e in ecritures
            # La contre-passation reprend la référence de l'écriture qu'elle annule : elle
            # n'est pas une dotation, et la dotation qu'elle annule n'est plus vivante.
            if e.reference_externe == _reference(exercice)
            and e.ecriture_contrepassee is None
            and e.cle not in annulees
        ),
        None,
    )


@routeur.get(
    "/dossiers/{entreprise}/amortissements/{exercice}",
    summary="Le tableau des amortissements de l'exercice",
)
def lire_le_tableau_des_amortissements(
    acces: AccesRequis, entreprise: str, exercice: str
) -> TableauEtDotation:
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    vivante = _dotation_vivante(entreprise, exercice)
    tableau = _tableau(entreprise, exercice)
    return TableauEtDotation(
        tableau=tableau,
        ecriture_de_dotation=vivante.cle if vivante else None,
        controle=_controle(entreprise, exercice, tableau),
    )


@routeur.get(
    "/dossiers/{entreprise}/amortissements/{exercice}/excel",
    summary="Le tableau des amortissements en Excel, annexe de la DSF",
    response_class=Response,
    responses={
        200: {"content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}}},
    },
)
def exporter_le_tableau_des_amortissements(
    acces: AccesRequis, entreprise: str, exercice: str
) -> Response:
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    from app.contextes.comptabilite.adaptateurs.sortant.tableau_amortissements_excel import (
        rendre_le_tableau_des_amortissements,
    )

    dossier = dossier_du_portefeuille(entreprise)
    octets = rendre_le_tableau_des_amortissements(
        _tableau(entreprise, exercice),
        denomination=dossier.denomination if dossier else entreprise,
        editee_le=maintenant(),
    )
    return Response(
        content=octets,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{entreprise}-{exercice}-tableau-des-amortissements.xlsx"'
            )
        },
    )


@routeur.post(
    "/dossiers/{entreprise}/amortissements/{exercice}/dotation",
    summary="Passer la dotation de l'exercice : une écriture en brouillon, à valider",
    status_code=201,
    responses={
        404: {"description": "Exercice inconnu"},
        409: {"description": "Dotation déjà passée, rien à doter, ou saisie refusée"},
    },
)
def passer_la_dotation(acces: AccesRequis, entreprise: str, exercice: str) -> EcritureComptable:
    exiger_dossier(acces, Permission.SAISIR_ECRITURE, entreprise)
    bornes = _bornes(entreprise, exercice)
    vivante = _dotation_vivante(entreprise, exercice)
    if vivante is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"la dotation de {exercice} est déjà passée (écriture {vivante.cle}) : "
                "contre-passez-la avant d'en passer une autre."
            ),
        )
    depot_ = depot_des_immobilisations()
    dotees: list[tuple[Immobilisation, Decimal]] = [
        (i, montant)
        for i in depot_.du_dossier(entreprise)
        if (montant := dotation_de_l_exercice(i, bornes.ouverture, bornes.cloture)) > 0
    ]
    if not dotees:
        raise HTTPException(
            status_code=409,
            detail=f"aucune dotation à passer sur {exercice} : le registre est vide ou amorti.",
        )
    lignes: list[LigneEcriture] = []
    for fiche, montant in dotees:
        texte = f"Dotation {fiche.code} · {fiche.libelle}"[:120]
        lignes.append(
            LigneEcriture(compte=COMPTE_DOTATION, libelle=texte, sens=Sens.DEBIT, montant=montant)
        )
        lignes.append(
            LigneEcriture(
                compte=COMPTE_AMORTISSEMENT, libelle=texte, sens=Sens.CREDIT, montant=montant
            )
        )
    try:
        ecriture = enregistrer_une_ecriture(
            BrouillonEcriture(
                journal=_JOURNAL,
                exercice=exercice,
                date_operation=bornes.cloture,
                libelle=f"Dotations aux amortissements de l'exercice {exercice}",
                # La pièce d'une dotation est le tableau des amortissements de l'exercice,
                # édité depuis le registre : la validation exige une pièce, et c'est celle-là.
                piece_justificative=f"TABLEAU-AMORTISSEMENTS-{exercice}",
                reference_externe=_reference(exercice),
                lignes=lignes,
            ),
            journaux=depot_des_journaux.charger(),
            plan=depot_du_plan.charger(),
            exercice=bornes,
            periodes_verrouillees=periodes_verrouillees_du_dossier(entreprise),
            depot=depot(entreprise),
            par=acces.compte,
        )
    except SaisieRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    for fiche, _ in dotees:
        depot_.enregistrer(fiche.marquer_dotee(exercice))
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action="comptabilite.dotation_passee",
        objet_type="ecriture",
        objet_id=f"{entreprise}/{ecriture.cle}",
        apres={
            "exercice": exercice,
            "biens": [f.code for f, _ in dotees],
            "total": str(sum((m for _, m in dotees), Decimal(0))),
        },
    )
    return ecriture
