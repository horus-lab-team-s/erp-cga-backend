"""Les règles du cabinet : les construire sans syntaxe, et le journal des dérogations.

Extrait de `routes_http.py` le 16 octobre 2026. Les routes gardent leurs chemins ; le
routeur est monté à côté du sien.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.conformite.adaptateurs.entrant.routes_http import (
    DecisionDuSecondRegard,
    reserver_au_cabinet,
)
from app.contextes.conformite.adaptateurs.sortant.depots_regles_du_cabinet import (
    depot_des_regles_du_cabinet,
)
from app.contextes.conformite.adaptateurs.sortant.donnees_demo import FACTURES_DEMO
from app.contextes.conformite.api import (
    JournalDesDerogations,
    QualiteDesRegles,
    derogations_du_perimetre,
    lire_le_journal_des_derogations,
    mesurer_la_qualite_des_regles,
    noms_des_comptes,
)
from app.contextes.conformite.application.moteur_conformite import MoteurConformite
from app.contextes.conformite.application.regles_du_cabinet import (
    ConstructionDeRegle,
    HorsDuCircuitDesRegles,
    construire,
    eprouver,
    proposer_une_regle,
    retirer_une_regle,
    trancher_une_regle,
)
from app.contextes.conformite.domaine.constructeur import (
    ConstructionRefusee,
    decrire,
    faits_constructibles,
    operateurs_pour,
)
from app.contextes.conformite.domaine.entites import FactureAControler, RapportConformite, Regle
from app.contextes.conformite.domaine.regles_du_cabinet import (
    PropositionDeRegle,
    PropositionRefusee,
    StatutProposition,
)
from app.contextes.conformite.domaine.schema_faits import SCHEMA_FACTURE
from app.contextes.transverse.api import (
    Acces,
    AccesRequis,
    Permission,
    atelier,
    exiger,
)
from app.partage.erreurs import message_lisible
from app.partage.horloge import aujourd_hui, jour_a_douala, maintenant

routeur = APIRouter(prefix="/conformite", tags=["Conformité"])


# ── Construire une règle sans syntaxe (pas 97) ───────────────────────────────
#
# ⚠️ LE GESTE LE PLUS LOURD DU PRODUIT, ET POURQUOI IL EST ENCADRÉ AINSI
#
# Une règle validée ici contrôle **toutes** les factures du cabinet au contrôle suivant.
# Trois garde-fous se cumulent : le constructeur ne laisse écrire aucune syntaxe (il pose
# lui-même la négation et vérifie chaque condition au schéma et au référentiel), la règle
# est **éprouvée** sur les factures avant d'être proposée, et une autre personne la valide
# selon le circuit du cabinet (`validation/circuit.yaml`, entrée `regles`).


class FaitDuCatalogue(BaseModel):
    code: str
    libelle: str
    type: str
    unite: str | None
    valeurs: list[str] | None
    operateurs: list[str]


class ParametreDuCatalogue(BaseModel):
    code: str
    libelle: str
    unite: str


class CatalogueDuConstructeur(BaseModel):
    faits: list[FaitDuCatalogue]
    parametres: list[ParametreDuCatalogue]
    #: Ce que le constructeur ne sait pas exprimer, dit en clair.
    limites: str
    peut_proposer: bool
    peut_valider: bool
    quatre_yeux: bool
    motif_minimum: int




def _unites_des_parametres() -> dict[str, str]:
    from app.contextes.referentiel.api import parametres_du_cabinet

    return {p.code: p.unite.value for p in parametres_du_cabinet()}


def _controler_avec(regle: Regle, facture: FactureAControler) -> RapportConformite:
    """Le moteur du cabinet réduit à **cette seule règle**, pour l'essai."""
    from app.contextes.referentiel.api import service_parametres

    return MoteurConformite(regles=[regle], parametres=service_parametres()).controler(facture)


@routeur.get("/constructeur/catalogue", summary="Ce qu'une règle construite peut lire et comparer")
def catalogue_du_constructeur(acces: AccesRequis) -> CatalogueDuConstructeur:
    from app.contextes.referentiel.api import circuit_de_validation, parametres_du_cabinet

    exiger(acces, Permission.LIRE_DOSSIER)
    reserver_au_cabinet(acces)
    circuit = circuit_de_validation()
    detenues = {p.value for p in acces.permissions}
    return CatalogueDuConstructeur(
        faits=[
            FaitDuCatalogue(
                code=f.code,
                libelle=f.libelle,
                type=f.type.value,
                unite=f.unite,
                valeurs=list(f.valeurs) if f.valeurs else None,
                operateurs=[o.value for o in operateurs_pour(f)],
            )
            for f in faits_constructibles(SCHEMA_FACTURE)
        ],
        parametres=[
            ParametreDuCatalogue(code=p.code, libelle=p.libelle, unite=p.unite.value)
            for p in parametres_du_cabinet()
        ],
        limites=(
            "Les lignes de détail d'une facture ne sont pas proposées : une condition sur « une "
            "ligne » est ambiguë. Ces règles restent écrites en fichier et relues en revue."
        ),
        peut_proposer=bool(detenues.intersection(circuit.regles.proposer)),
        peut_valider=bool(detenues.intersection(circuit.regles.valider)),
        quatre_yeux=circuit.regles.quatre_yeux,
        motif_minimum=circuit.motif_minimum,
    )


class EssaiDeConstruction(BaseModel):
    phrase: str
    predicat: dict
    eprouvees: int
    reagit_sur: list[str]
    reagit_partout: bool


@routeur.post(
    "/constructeur/essai",
    summary="Éprouver une règle construite, sans rien enregistrer",
    responses={422: {"description": "Condition impossible à traduire"}},
)
def eprouver_une_construction(
    acces: AccesRequis, construction: ConstructionDeRegle
) -> EssaiDeConstruction:
    """La phrase, le prédicat produit et les factures sur lesquelles la règle réagirait.

    Rien n'est enregistré, et la règle n'entre dans aucun contrôle : c'est un essai.
    """
    exiger(acces, Permission.CONTROLER_CONFORMITE)
    reserver_au_cabinet(acces)
    try:
        regle = construire(
            construction, code="CAB-ESSAI", unite_du_parametre=_unites_des_parametres()
        )
    except (ConstructionRefusee, ValueError) as refus:
        # `construire` valide une `Regle` : une ValidationError pydantic rendrait sa trace brute.
        raise HTTPException(status_code=422, detail=message_lisible(refus)) from refus
    essai = eprouver(regle, FACTURES_DEMO.values(), _controler_avec)
    return EssaiDeConstruction(
        phrase=decrire(construction.conditions, construction.combinaison, SCHEMA_FACTURE),
        predicat=regle.predicat,
        eprouvees=essai.eprouvees,
        reagit_sur=essai.reagit_sur,
        reagit_partout=essai.reagit_partout,
    )


class DemandeDeRegle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    construction: ConstructionDeRegle
    motif: str = Field(min_length=10, max_length=2000)


def _journaliser_la_regle(acces: Acces, action: str, proposition: PropositionDeRegle) -> None:
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action=action,
        objet_type="regle",
        objet_id=proposition.regle.code,
        apres={
            "proposition": proposition.identifiant,
            "regle": proposition.regle.code,
            "libelle": proposition.regle.libelle,
            "severite": proposition.regle.severite.value,
            "reagit": len(proposition.essai.reagit_sur),
            "eprouvees": proposition.essai.eprouvees,
            "statut": proposition.statut.value,
            "fin_d_effet": None
            if proposition.fin_d_effet is None
            else proposition.fin_d_effet.isoformat(),
        },
        motif=proposition.motif_du_retrait or proposition.motif_de_la_decision or proposition.motif,
    )


@routeur.post(
    "/regles/propositions",
    summary="Proposer une règle construite (éprouvée à la proposition)",
    status_code=201,
    responses={
        403: {"description": "Hors du circuit du cabinet"},
        409: {"description": "Motif, date, ou règle qui réagit sur toutes les factures"},
        422: {"description": "Condition impossible à traduire"},
    },
)
def proposer_une_regle_construite(
    acces: AccesRequis, demande: DemandeDeRegle
) -> PropositionDeRegle:
    """**Sans effet** tant qu'une autre personne désignée par le circuit ne l'a pas validée."""
    from app.contextes.referentiel.api import circuit_de_validation

    exiger(acces, Permission.CONTROLER_CONFORMITE)
    reserver_au_cabinet(acces)
    circuit = circuit_de_validation()
    try:
        proposition = proposer_une_regle(
            demande.construction,
            motif=demande.motif,
            par=acces.compte,
            nom=acces.nom_complet,
            le=maintenant(),
            permissions={p.value for p in acces.permissions},
            designees=circuit.regles.proposer,
            motif_minimum=circuit.motif_minimum,
            unite_du_parametre=_unites_des_parametres(),
            factures=FACTURES_DEMO.values(),
            controler=_controler_avec,
            depot=depot_des_regles_du_cabinet(),
        )
    except HorsDuCircuitDesRegles as refus:
        raise HTTPException(status_code=403, detail=str(refus)) from refus
    except PropositionRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    except (ConstructionRefusee, ValueError) as refus:
        # `construire` valide une `Regle` : une ValidationError pydantic rendrait sa trace brute.
        raise HTTPException(status_code=422, detail=message_lisible(refus)) from refus
    _journaliser_la_regle(acces, "conformite.regle_proposee", proposition)
    return proposition


@routeur.get("/regles/propositions", summary="Les règles construites par le cabinet")
def lister_les_regles_du_cabinet(acces: AccesRequis) -> list[PropositionDeRegle]:
    """De la plus récente à la plus ancienne ; les refusées restent lisibles."""
    exiger(acces, Permission.LIRE_DOSSIER)
    reserver_au_cabinet(acces)
    return list(reversed(depot_des_regles_du_cabinet().toutes()))


class DemandeDeTranchageDeRegle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: DecisionDuSecondRegard
    motif: str = Field(min_length=10, max_length=2000)


@routeur.post(
    "/regles/propositions/{identifiant}/tranchage",
    summary="Valider ou refuser une règle construite",
    responses={
        403: {"description": "Hors du circuit du cabinet"},
        404: {"description": "Proposition inconnue"},
        409: {"description": "Déjà tranchée, ou construite par la même personne"},
    },
)
def trancher_une_regle_construite(
    acces: AccesRequis, identifiant: str, demande: DemandeDeTranchageDeRegle
) -> PropositionDeRegle:
    """Validée, la règle entre au contrôle suivant **du cabinet**, signée par le valideur."""
    from app.contextes.referentiel.api import circuit_de_validation

    exiger(acces, Permission.LIRE_DOSSIER)
    reserver_au_cabinet(acces)
    circuit = circuit_de_validation()
    try:
        proposition = trancher_une_regle(
            identifiant,
            valider=demande.decision is DecisionDuSecondRegard.CONFIRMER,
            motif=demande.motif,
            par=acces.compte,
            nom=acces.nom_complet,
            le=maintenant(),
            permissions={p.value for p in acces.permissions},
            designees=circuit.regles.valider,
            quatre_yeux=circuit.regles.quatre_yeux,
            motif_minimum=circuit.motif_minimum,
            depot=depot_des_regles_du_cabinet(),
        )
    except HorsDuCircuitDesRegles as refus:
        raise HTTPException(status_code=403, detail=str(refus)) from refus
    except LookupError as refus:
        raise HTTPException(status_code=404, detail=str(refus)) from refus
    except PropositionRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    action = (
        "conformite.regle_validee"
        if proposition.statut is StatutProposition.APPLIQUEE
        else "conformite.regle_refusee"
    )
    _journaliser_la_regle(acces, action, proposition)
    return proposition


class DemandeDeRetraitDeRegle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    a_compter_du: date
    motif: str = Field(min_length=10, max_length=2000)


@routeur.post(
    "/regles/propositions/{identifiant}/retrait",
    summary="Mettre fin à une règle du cabinet, à compter d'une date",
    responses={
        403: {"description": "Hors du circuit du cabinet"},
        404: {"description": "Proposition inconnue"},
        409: {"description": "Règle pas en vigueur, date passée ou motif trop court"},
    },
)
def retirer_une_regle_du_cabinet(
    acces: AccesRequis, identifiant: str, demande: DemandeDeRetraitDeRegle
) -> PropositionDeRegle:
    """Pas 98. À compter de la date, la règle ne contrôle plus ; les contrôles rendus restent."""
    from app.contextes.referentiel.api import circuit_de_validation

    exiger(acces, Permission.LIRE_DOSSIER)
    reserver_au_cabinet(acces)
    circuit = circuit_de_validation()
    try:
        proposition = retirer_une_regle(
            identifiant,
            a_compter_du=demande.a_compter_du,
            motif=demande.motif,
            par=acces.compte,
            nom=acces.nom_complet,
            le=maintenant(),
            permissions={p.value for p in acces.permissions},
            designees=circuit.regles.valider,
            motif_minimum=circuit.motif_minimum,
            depot=depot_des_regles_du_cabinet(),
        )
    except HorsDuCircuitDesRegles as refus:
        raise HTTPException(status_code=403, detail=str(refus)) from refus
    except LookupError as refus:
        raise HTTPException(status_code=404, detail=str(refus)) from refus
    except PropositionRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    _journaliser_la_regle(acces, "conformite.regle_retiree", proposition)
    return proposition


# ── Le journal des dérogations et la qualité des règles (pas 99) ─────────────
#
# L'entrée « Conformité » du menu était marquée « à venir » depuis la première coquille.
# Elle porte les deux écrans du parcours réviseur : ce que le cabinet a écarté (et qu'un
# vérificateur demandera à voir), et ce que ces écarts disent de la justesse des règles.


@routeur.get("/derogations", summary="Le journal des dérogations du cabinet")
def journal_des_derogations(
    acces: AccesRequis,
    regle: str | None = Query(None),
    auteur: str | None = Query(None, description="Compte qui a proposé l'écart"),
    dossier: str | None = Query(None, description="NIU"),
    statut: str | None = Query(None),
    du: date | None = Query(None, description="Proposées à partir de ce jour (pas 106)"),
    au: date | None = Query(None, description="Proposées jusqu'à ce jour inclus (pas 106)"),
) -> JournalDesDerogations:
    """Tous les écarts de constats du périmètre, du plus récent au plus ancien, avec leur motif.

    ⚠️ **`LIRE_AUDIT`, et non `CONTROLER_CONFORMITE`.** Le journal est ce qu'un vérificateur
    demande à voir : il est ouvert au réviseur, à la direction, et à l'inspecteur **sur son
    seul dossier** (`restreindre`). Un comptable, qui contrôle sans auditer, ne le lit pas.
    Rien ne s'y modifie : une dérogation se lève, elle ne s'efface pas.
    """
    exiger(acces, Permission.LIRE_AUDIT)
    # Pas 106 : le calcul vit dans `api.py`, pour que le rapport mensuel lise les mêmes chiffres.
    return lire_le_journal_des_derogations(
        acces, regle=regle, auteur=auteur, dossier=dossier, statut=statut, du=du, au=au
    )


@routeur.get(
    "/derogations/export",
    summary="Exporter le journal des dérogations pour un contrôle",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
def exporter_les_derogations(
    acces: AccesRequis,
    regle: str | None = Query(None),
    auteur: str | None = Query(None),
    dossier: str | None = Query(None),
    statut: str | None = Query(None),
) -> Response:
    """Le même journal, en CSV séparé par des points-virgules, avec les mêmes filtres.

    ⚠️ UTF-8 avec marque d'ordre des octets : sans elle, le tableur d'un vérificateur lit
    « Ã© » à la place des accents du motif, et le motif est la raison même de l'export.
    """
    import csv
    import io

    exiger(acces, Permission.LIRE_AUDIT)
    noms = noms_des_comptes()
    tampon = io.StringIO()
    ecrivain = csv.writer(tampon, delimiter=";")
    ecrivain.writerow(
        ["Date", "Dossier", "Pièce", "Règle", "Sévérité", "Enjeu levé (FCFA)", "Statut", "Motif",
         "Proposé par", "Second regard", "Motif du second regard", "Levé par", "Motif de levée"]
    )
    for e in derogations_du_perimetre(
        acces, regle=regle, auteur=auteur, dossier=dossier, statut=statut
    ):
        ecrivain.writerow(
            [
                jour_a_douala(e.propose_le).isoformat(),
                e.dossier, e.reference_document, e.code_regle,
                e.severite.value, "" if e.enjeu is None else str(e.enjeu), e.statut.value, e.motif,
                noms.get(e.propose_par, e.propose_par),
                noms.get(e.tranche_par or "", e.tranche_par or ""),
                e.motif_du_second_regard or "", noms.get(e.leve_par or "", e.leve_par or ""),
                e.motif_de_levee or "",
            ]
        )
    return Response(
        content="﻿" + tampon.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="journal-des-derogations.csv"'},
    )


@routeur.get("/regles/qualite", summary="La qualité des règles, mesurée par les écarts")
def qualite_des_regles(
    acces: AccesRequis,
    du: date | None = Query(None, description="Par défaut : 90 jours avant « au »"),
    au: date | None = Query(None, description="Par défaut : aujourd'hui"),
) -> QualiteDesRegles:
    """Pour chaque règle en vigueur, sur les pièces du périmètre émises dans la période :
    constats émis, constats écartés, enjeu retenu, et la lecture selon les seuils du référentiel.

    Les pièces sont contrôlées ici, par le moteur du cabinet, et arbitrées par ses écarts :
    un rapport ne se conserve pas, il se recalcule (voir `RapportConformite`).
    """
    exiger(acces, Permission.CONTROLER_CONFORMITE)
    fin = au or aujourd_hui()
    debut = du or fin - timedelta(days=90)
    if debut > fin:
        raise HTTPException(status_code=422, detail="la période commence après sa fin.")
    # Pas 106 : le calcul vit dans `api.py`, partagé avec le rapport mensuel.
    return mesurer_la_qualite_des_regles(acces, debut, fin)


