"""Les annexes de la DSF (16 octobre 2026) : balances auxiliaires, déclarations par tiers
de la DGI, livres légaux en PDF.

Extrait de `routes_http.py`, qui dépassait 1 600 lignes. Les routes gardent leurs chemins
(`/comptabilite/...`) ; le routeur est monté à côté de celui de la comptabilité.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from app.contextes.comptabilite.adaptateurs.entrant.routes_http import (
    depot,
    depot_du_plan,
    dossier_du_portefeuille,
)
from app.contextes.comptabilite.adaptateurs.sortant.livres_legaux import (
    EnteteDuLivre,
    Livre,
    rendre_le_livre,
)
from app.contextes.comptabilite.domaine.balance_auxiliaire import NatureAuxiliaire

# ⚠️ L'arête `comptabilite → conformite` est déclarée au registre des services :
# c'est elle qui autorise cet import, et `test_architecture.py` la garde.
from app.contextes.transverse.api import (
    AccesRequis,
    Permission,
    exiger_dossier,
    session_de_travail,
)
from app.partage.horloge import aujourd_hui, maintenant
from app.partage.locataire import courant

routeur = APIRouter(prefix="/comptabilite", tags=["Comptabilité"])


# ── Les balances auxiliaires, annexes de la DSF (15 octobre 2026) ────────────────


class LigneAuxiliaire(BaseModel):
    tiers: str | None
    denomination: str | None
    niu: str | None
    report: Decimal
    debit: Decimal
    credit: Decimal
    solde: Decimal


def _balance_auxiliaire_du_dossier(entreprise: str, exercice: str, nature):
    """Les lignes, et la fiche de chaque tiers : le registre du dossier d'abord, puis le
    code lui-même quand il a la forme d'un NIU au référentiel (une écriture née d'une
    facture lue porte le NIU du fournisseur)."""
    import re

    from app.contextes.comptabilite.adaptateurs.sortant.balance_auxiliaire_excel import (
        FicheDuTiers,
    )
    from app.contextes.comptabilite.domaine.balance_auxiliaire import balance_auxiliaire
    from app.contextes.referentiel.api import (
        AucuneVersionApplicable,
        ParametreInconnu,
        service_parametres,
    )

    lignes = balance_auxiliaire(depot(entreprise).lister(exercice), nature)
    dossier = dossier_du_portefeuille(entreprise)
    fiches: dict[str, FicheDuTiers] = {}
    for t in dossier.tiers if dossier else []:
        fiche = FicheDuTiers(denomination=t.denomination, niu=t.niu)
        fiches[t.code] = fiche
        if t.niu:
            fiches.setdefault(t.niu, fiche)
    try:
        motif = service_parametres().valeur_texte("FORMAT_NIU", aujourd_hui())
    except (ParametreInconnu, AucuneVersionApplicable):  # sans format, on ne devine pas
        motif = None
    noms = _noms_des_emetteurs(entreprise)
    for ligne in lignes:
        if ligne.tiers and ligne.tiers not in fiches and motif and re.match(motif, ligne.tiers):
            fiches[ligne.tiers] = FicheDuTiers(
                denomination=noms.get(ligne.tiers, ""), niu=ligne.tiers
            )
    return lignes, fiches, dossier


def _noms_des_emetteurs(entreprise: str) -> dict[str, str]:
    """Le nom de chaque émetteur d'une facture du dossier, par son NIU (15 octobre 2026).

    Une écriture née d'une facture porte le NIU du fournisseur, pas son nom : sans ceci,
    la balance auxiliaire montrait une colonne « raison sociale » vide. Deux sources, les
    factures lues à la collecte puis celles du contrôle de conformité ; la première lue
    l'emporte, et une source en panne laisse la colonne vide plutôt que l'export en échec.
    """
    import logging

    journal = logging.getLogger(__name__)
    noms: dict[str, str] = {}
    try:
        from app.contextes.collecte.api import DepotPiecesSql, pieces_en_memoire

        session = session_de_travail()
        pieces = pieces_en_memoire() if session is None else DepotPiecesSql(session, courant())
        for piece in pieces.du_dossier(entreprise):
            lue = piece.facture
            if lue is not None and lue.niu_emetteur:
                noms.setdefault(lue.niu_emetteur, lue.emetteur)
    except Exception:  # noqa: BLE001 — un nom manquant ne bloque pas une balance
        journal.warning("Noms des émetteurs : pièces de %s illisibles.", entreprise)
    try:
        from app.contextes.conformite.api import FACTURES_DEMO

        for facture in FACTURES_DEMO.values():
            e = facture.emetteur
            if facture.destinataire.niu == entreprise and e.niu and e.denomination:
                noms.setdefault(e.niu, e.denomination)
    except Exception:  # noqa: BLE001
        journal.warning("Noms des émetteurs : factures de conformité illisibles.")
    return noms


@routeur.get(
    "/dossiers/{entreprise}/balances-auxiliaires/{nature}",
    summary="La balance auxiliaire clients ou fournisseurs, tiers par tiers",
    responses={403: {"description": "Habilitation insuffisante sur ce dossier"}},
)
def lire_la_balance_auxiliaire(
    acces: AccesRequis,
    entreprise: str,
    nature: NatureAuxiliaire,
    exercice: str = Query(description="L'exercice, par exemple « 2026 »."),
) -> list[LigneAuxiliaire]:
    """Sur les écritures VALIDÉES. Voir `domaine/balance_auxiliaire.py`."""
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    lignes, fiches, _ = _balance_auxiliaire_du_dossier(entreprise, exercice, nature)
    return [
        LigneAuxiliaire(
            tiers=x.tiers,
            denomination=(fiches[x.tiers].denomination or None) if x.tiers in fiches else None,
            niu=fiches[x.tiers].niu if x.tiers in fiches else None,
            report=x.report,
            debit=x.debit,
            credit=x.credit,
            solde=x.solde,
        )
        for x in lignes
    ]


@routeur.get(
    "/dossiers/{entreprise}/balances-auxiliaires/{nature}/excel",
    summary="La balance auxiliaire en Excel, annexe de la DSF",
    response_class=Response,
    responses={
        200: {"content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}}},
        403: {"description": "Habilitation insuffisante sur ce dossier"},
    },
)
def exporter_la_balance_auxiliaire(
    acces: AccesRequis,
    entreprise: str,
    nature: NatureAuxiliaire,
    exercice: str = Query(description="L'exercice, par exemple « 2026 »."),
) -> Response:
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    from app.contextes.comptabilite.adaptateurs.sortant.balance_auxiliaire_excel import (
        EnteteBalanceAuxiliaire,
        rendre_la_balance_auxiliaire,
    )

    lignes, fiches, dossier = _balance_auxiliaire_du_dossier(entreprise, exercice, nature)
    octets = rendre_la_balance_auxiliaire(
        lignes,
        EnteteBalanceAuxiliaire(
            niu=entreprise,
            denomination=dossier.denomination if dossier else entreprise,
            exercice=exercice,
            nature=nature,
            editee_le=maintenant(),
        ),
        fiches,
    )
    return Response(
        content=octets,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{entreprise}-{exercice}-balance-auxiliaire-'
                f'{nature.value}.xlsx"'
            )
        },
    )


# ── Les déclarations annuelles par tiers de la DGI (15 octobre 2026) ──────────────


class MontantDeclare(BaseModel):
    ht: Decimal
    tva: Decimal


class LigneDeclaree(BaseModel):
    tiers: str
    denomination: str | None
    niu: str | None
    pays: str | None
    sigle: str | None = None
    telephone: str | None = None
    ville: str | None = None
    #: Au registre des tiers du dossier : sinon, sa fiche est à créer avant le dépôt.
    au_registre: bool = False
    etranger: bool = False
    ouverture: MontantDeclare
    facturation: MontantDeclare
    paiement: MontantDeclare
    cloture: MontantDeclare


class DeclarationParTiers(BaseModel):
    nature: str
    #: Le nom exact du modèle de la DGI, qui est aussi celui du fichier à téléverser.
    nom_du_fichier: str
    lignes: list[LigneDeclaree]
    avertissements: list[str]


def _declaration_par_tiers(entreprise: str, exercice: str, nature: str):
    """Les lignes du modèle de la DGI et la fiche de chaque tiers. « tiers » ne garde que
    les sommes versées (locations, honoraires) : voir `domaine/declaration_par_tiers.py`."""
    from app.contextes.comptabilite.adaptateurs.sortant.declaration_par_tiers_dgi import (
        FicheDeclaree,
    )
    from app.contextes.comptabilite.domaine.declaration_par_tiers import declaration_par_tiers

    if nature not in ("fournisseurs", "tiers"):
        raise HTTPException(status_code=404, detail="« fournisseurs » ou « tiers ».")
    lignes = declaration_par_tiers(depot(entreprise).lister(exercice))
    if nature == "tiers":
        lignes = [x for x in lignes if x.sommes_versees]
    _, fiches_auxiliaires, dossier = _balance_auxiliaire_du_dossier(
        entreprise, exercice, NatureAuxiliaire.FOURNISSEURS
    )
    registre = _registre_des_tiers(dossier)

    def fiche(code: str, f) -> FicheDeclaree:
        t = registre.get(code)
        if t is None:
            return FicheDeclaree(denomination=f.denomination, niu=f.niu)
        return FicheDeclaree(
            denomination=f.denomination,
            niu=f.niu,
            pays=t.pays if t.etranger else "CAMEROUN",
            sigle=t.sigle,
            telephone=t.telephone,
            ville=t.ville,
        )

    fiches = {code: fiche(code, f) for code, f in fiches_auxiliaires.items()}
    avertissements = [
        "Règlements et soldes sont des montants TTC : ils sont répartis entre HT et TVA à la "
        "proportion des factures du même tiers sur l'exercice."
    ]
    if nature == "tiers":
        avertissements.append(
            "Sont retenus les tiers dont une facture de l'exercice touche les locations (622) "
            "ou les honoraires et commissions (632) : lecture du modèle à faire valider par "
            "le fiscaliste."
        )
    sans_nom = [
        x.tiers for x in lignes if not (fiches.get(x.tiers) and fiches[x.tiers].denomination)
    ]
    if sans_nom:
        avertissements.append(
            f"{len(sans_nom)} tiers sans raison sociale connue : à compléter au registre "
            "des tiers du dossier avant le dépôt."
        )
    if any(fiches.get(x.tiers) and fiches[x.tiers].pays is None for x in lignes):
        avertissements.append("Pays de résidence des fournisseurs étrangers à compléter.")
    incomplets = [
        x.tiers
        for x in lignes
        if not (fiches.get(x.tiers) and fiches[x.tiers].telephone and fiches[x.tiers].ville)
    ]
    if incomplets:
        avertissements.append(
            f"{len(incomplets)} tiers sans téléphone ou sans ville : à compléter au registre "
            "des tiers du dossier, repris ensuite par chaque export."
        )
    return lignes, fiches, avertissements


def _registre_des_tiers(dossier) -> dict:
    """Les fiches du registre du dossier, par code et par NIU : une écriture née d'une
    facture lue porte le NIU du fournisseur. Le code l'emporte sur un NIU homonyme."""
    if dossier is None:
        return {}
    registre = {t.niu: t for t in dossier.tiers if t.niu}
    registre.update({t.code: t for t in dossier.tiers})
    return registre


@routeur.get(
    "/dossiers/{entreprise}/declarations-par-tiers/{nature}",
    summary="Achats par fournisseur ou sommes versées aux tiers, comme les modèles de la DGI",
    responses={403: {"description": "Habilitation insuffisante sur ce dossier"}},
)
def lire_la_declaration_par_tiers(
    acces: AccesRequis,
    entreprise: str,
    nature: str,
    exercice: str = Query(description="L'exercice, par exemple « 2026 »."),
) -> DeclarationParTiers:
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    from app.contextes.comptabilite.adaptateurs.sortant.declaration_par_tiers_dgi import MODELES

    lignes, fiches, avertissements = _declaration_par_tiers(entreprise, exercice, nature)
    registre = _registre_des_tiers(dossier_du_portefeuille(entreprise))

    def montant(m) -> MontantDeclare:
        return MontantDeclare(ht=m.ht, tva=m.tva)

    return DeclarationParTiers(
        nature=nature,
        nom_du_fichier=MODELES[nature],
        avertissements=avertissements,
        lignes=[
            LigneDeclaree(
                tiers=x.tiers,
                denomination=(fiches[x.tiers].denomination or None) if x.tiers in fiches else None,
                niu=fiches[x.tiers].niu if x.tiers in fiches else x.tiers,
                pays=fiches[x.tiers].pays if x.tiers in fiches else "CAMEROUN",
                sigle=fiches[x.tiers].sigle if x.tiers in fiches else None,
                telephone=fiches[x.tiers].telephone if x.tiers in fiches else None,
                ville=fiches[x.tiers].ville if x.tiers in fiches else None,
                au_registre=x.tiers in registre,
                etranger=bool(registre.get(x.tiers) and registre[x.tiers].etranger),
                ouverture=montant(x.ouverture),
                facturation=montant(x.facturation),
                paiement=montant(x.paiement),
                cloture=montant(x.cloture),
            )
            for x in lignes
        ],
    )


@routeur.get(
    "/dossiers/{entreprise}/declarations-par-tiers/{nature}/excel",
    summary="Le modèle de la DGI rempli, sous son nom exact",
    response_class=Response,
    responses={
        200: {"content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}}},
        403: {"description": "Habilitation insuffisante sur ce dossier"},
    },
)
def exporter_la_declaration_par_tiers(
    acces: AccesRequis,
    entreprise: str,
    nature: str,
    exercice: str = Query(description="L'exercice, par exemple « 2026 »."),
) -> Response:
    """⚠️ Le fichier garde le NOM du modèle : le portail refuse un fichier renommé."""
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    from app.contextes.comptabilite.adaptateurs.sortant.declaration_par_tiers_dgi import (
        MODELES,
        remplir_la_declaration_par_tiers,
    )

    lignes, fiches, _ = _declaration_par_tiers(entreprise, exercice, nature)
    return Response(
        content=remplir_la_declaration_par_tiers(nature, lignes, fiches),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{MODELES[nature]}"'},
    )


# ── Les livres obligatoires, en PDF (14 octobre 2026) ─────────────────────────────


@routeur.get(
    "/dossiers/{entreprise}/livres/{livre}",
    summary="Un livre obligatoire en PDF : livre-journal, grand livre ou balance générale",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}}},
        403: {"description": "Habilitation insuffisante sur ce dossier"},
        422: {"description": "Livre inconnu, ou période à l'envers"},
    },
)
def editer_un_livre(
    acces: AccesRequis,
    entreprise: str,
    livre: Livre,
    exercice: str = Query(description="L'exercice, par exemple « 2026 »."),
    du: date | None = Query(None, description="Début de période (le grand livre reporte avant)."),
    au: date | None = Query(None, description="Date d'arrêté incluse."),
) -> Response:
    """Le livre, mis en page sur les écritures VALIDÉES : voir `livres_legaux.py`.

    ⚠️ Les chiffres sont ceux de la balance et du grand livre de l'écran, par les mêmes
    projections : un livre légal qui divergerait de l'écran serait le pire des deux.
    """
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    if du is not None and au is not None and du > au:
        raise HTTPException(
            status_code=422,
            detail=f"période à l'envers : du {du:%d/%m/%Y} au {au:%d/%m/%Y}.",
        )
    dossier = dossier_du_portefeuille(entreprise)
    plan = {c.numero: c.intitule for c in depot_du_plan.charger()}

    def intitule(numero: str) -> str:
        # Un sous-compte de l'adhérent (401100) prend l'intitulé de sa racine au plan.
        for longueur in range(len(numero), 0, -1):
            if numero[:longueur] in plan:
                return plan[numero[:longueur]]
        return ""

    octets = rendre_le_livre(
        livre,
        depot(entreprise).lister(exercice),
        EnteteDuLivre(
            niu=entreprise,
            denomination=dossier.denomination if dossier else entreprise,
            exercice=exercice,
            du=du,
            au=au,
            edite_le=maintenant(),
        ),
        intitule,
    )
    return Response(
        content=octets,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{entreprise}-{exercice}-{livre.value}.pdf"'
            )
        },
    )

