"""Le journal de caisse, ses totaux par axe, et les paiements Tara non rattachés.

─────────────────────────────────────────────────────────────────────────────────
18 OCTOBRE 2026, POINT 6 DE LA LISTE DU GÉRANT

Trois lectures, aucune écriture : les versements se constatent toujours sur leur objet
(proforma, échéance d'honoraires, appel de fonds), où l'axe est imposé. Voir
`domaine/journal_de_caisse.py`.

QUI LIT

* la caisse (`ENCAISSER`), pour sa journée ; la direction (`LIRE_PILOTAGE`), pour le
  suivi financier global ;
* ⚠️ et seulement les familles que la matrice lui laisse voir
  (`VOIR_VERSEMENTS_COMMERCE`, `…_HONORAIRES`, `…_FONDS_DE_TIERS`) : « l'administrateur
  peut retirer la vue sur certains versements, de façon explicite ».
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from io import BytesIO

from fastapi import APIRouter, HTTPException, Query, Response
from openpyxl import Workbook
from openpyxl.styles import Font
from pydantic import BaseModel

from app.contextes.souscription.adaptateurs.entrant.sources import (
    depot_dossiers,
    depot_proformas,
)
from app.contextes.souscription.domaine.dossier_commercial import EtatDossier
from app.contextes.souscription.domaine.fonds_de_tiers import EtatAppelDeFonds
from app.contextes.souscription.domaine.journal_de_caisse import (
    AXE_DU_SERVICE,
    AxeDeVersement,
    FamilleDeVersement,
    JournalDeCaisse,
    LigneDuJournal,
    composer_le_journal,
)
from app.contextes.souscription.domaine.paiements import NaturePaiement
from app.contextes.souscription.domaine.proforma import EtatProforma
from app.contextes.transverse.api import AccesRequis, Permission, atelier, exiger
from app.partage.formats import MOIS
from app.partage.horloge import aujourd_hui, jour_a_douala

__all__ = ["routeur"]

routeur = APIRouter(prefix="/souscription/caisse", tags=["Caisse"])

_FAMILLES = {
    FamilleDeVersement.COMMERCE: Permission.VOIR_VERSEMENTS_COMMERCE,
    FamilleDeVersement.HONORAIRES: Permission.VOIR_VERSEMENTS_HONORAIRES,
    FamilleDeVersement.FONDS_DE_TIERS: Permission.VOIR_VERSEMENTS_FONDS_DE_TIERS,
}


def _comptoir():
    from app.contextes.souscription.adaptateurs.entrant.routes_http import comptoir

    return comptoir()


def _droit_de_lecture(acces) -> Permission:
    """La caisse lit par `ENCAISSER`, la direction par `LIRE_PILOTAGE`."""
    return Permission.ENCAISSER if acces.detient(Permission.ENCAISSER) else Permission.LIRE_PILOTAGE


def _familles_visibles(acces) -> list[FamilleDeVersement]:
    """Les familles que la matrice laisse voir ; la route a déjà exigé `_droit_de_lecture`."""
    visibles = [famille for famille, droit in _FAMILLES.items() if acces.detient(droit)]
    if not visibles:
        raise HTTPException(
            status_code=403,
            detail="Action non permise : aucune famille de versements n'est visible pour "
            "vos rôles (VOIR_VERSEMENTS_…).",
        )
    return visibles


def _lignes(familles: list[FamilleDeVersement]) -> list[LigneDuJournal]:
    """Les versements encaissés, de toutes origines. ⚠️ Une famille masquée n'est même
    pas lue : ce qu'on ne doit pas voir ne transite pas par la réponse."""
    boutique = _comptoir()
    lignes: list[LigneDuJournal] = []
    paiements = boutique.paiements.encaisses()

    if FamilleDeVersement.COMMERCE in familles:
        tara = {p.reference_reglee: p for p in paiements if p.nature is NaturePaiement.PROFORMA}
        proformas = depot_proformas()
        for dossier in depot_dossiers().lister():
            if dossier.etat is not EtatDossier.PAYEE:
                continue
            vivantes = [
                p
                for p in proformas.par_dossier(dossier.reference)
                if p.etat not in (EtatProforma.REMPLACEE, EtatProforma.ANNULEE)
            ]
            if not vivantes:
                continue
            proforma = max(vivantes, key=lambda p: (p.etat is EtatProforma.ACCEPTEE, p.version))
            paiement = tara.get(proforma.numero)
            lignes.append(
                LigneDuJournal(
                    le=dossier.payee_le or dossier.depuis_le,
                    axe=AXE_DU_SERVICE.get(proforma.service, AxeDeVersement.PRESTATION),
                    objet=f"Proforma {proforma.numero} · {proforma.service.lower()}",
                    client=dossier.demande.nom,
                    dossier=dossier.inscrit_sous or dossier.reference,
                    montant=proforma.tarif.montant,
                    canal="MOBILE_MONEY" if paiement else "GUICHET",
                    reference=paiement.reference_externe if paiement else None,
                )
            )
        for paiement in paiements:
            if paiement.nature is not NaturePaiement.SOUSCRIPTION:
                continue
            try:
                souscription = boutique.souscriptions.lire(paiement.reference_reglee)
                client = f"{souscription.prospect.prenom} {souscription.prospect.nom}"
                objet = f"Abonnement en ligne · {souscription.libelle}"
            except Exception:  # noqa: BLE001 — une souscription illisible n'efface pas l'argent reçu
                client, objet = paiement.telephone, "Abonnement en ligne"
            lignes.append(
                LigneDuJournal(
                    le=paiement.confirme_le or paiement.initie_le,
                    axe=AxeDeVersement.ABONNEMENT_EN_LIGNE,
                    objet=objet,
                    client=client,
                    dossier=paiement.reference_reglee,
                    montant=paiement.montant,
                    canal="MOBILE_MONEY",
                    reference=paiement.reference_externe,
                )
            )

    if FamilleDeVersement.HONORAIRES in familles:
        for contrat in boutique.contrats_honoraires.lister():
            for r in boutique.reglements_honoraires.pour(contrat.niu):
                lignes.append(
                    LigneDuJournal(
                        le=r.constate_le,
                        axe=AxeDeVersement.HONORAIRES,
                        objet=f"Honoraires, {MOIS[r.periode.month - 1]} {r.periode.year}",
                        client=contrat.denomination,
                        dossier=contrat.niu,
                        montant=r.montant,
                        canal=r.canal.value,
                        reference=r.reference_externe,
                    )
                )

    if FamilleDeVersement.FONDS_DE_TIERS in familles:
        for appel in boutique.appels_de_fonds.lister():
            if appel.etat is EtatAppelDeFonds.A_REGLER or appel.fonds_recus_le is None:
                continue
            try:
                axe = AxeDeVersement(appel.code_obligation)
            except ValueError:
                axe = AxeDeVersement.TVA if "TVA" in appel.code_obligation else AxeDeVersement.CNPS
            lignes.append(
                LigneDuJournal(
                    le=appel.fonds_recus_le,
                    axe=axe,
                    objet=f"{appel.libelle} · {appel.periode_debut:%m/%Y}",
                    client=appel.denomination,
                    dossier=appel.niu,
                    montant=appel.montant,
                    canal=appel.canal.value if appel.canal else "GUICHET",
                    reference=appel.reference_des_fonds,
                )
            )
    return lignes


def _journal(acces, du: date | None, au: date | None) -> JournalDeCaisse:
    familles = _familles_visibles(acces)
    au = au or aujourd_hui()
    du = du or au.replace(day=1)
    if du > au:
        raise HTTPException(status_code=422, detail="la période commence après sa fin.")
    if au - du > timedelta(days=366):
        raise HTTPException(status_code=422, detail="un journal porte sur un an au plus.")
    return composer_le_journal(
        _lignes(familles), du=du, au=au, familles=familles, jour_de=jour_a_douala
    )


@routeur.get("/journal", summary="Le journal de caisse et ses totaux par axe")
def lire_le_journal(
    acces: AccesRequis,
    du: date | None = Query(None, description="Premier jour inclus ; défaut : le 1er du mois"),
    au: date | None = Query(None, description="Dernier jour inclus ; défaut : aujourd'hui"),
) -> JournalDeCaisse:
    exiger(acces, _droit_de_lecture(acces))
    return _journal(acces, du, au)


_LIBELLES_AXE = {
    AxeDeVersement.INSCRIPTION: "Inscription",
    AxeDeVersement.CREATION: "Création d'entreprise",
    AxeDeVersement.PRESTATION: "Prestation",
    AxeDeVersement.ABONNEMENT_EN_LIGNE: "Abonnement en ligne",
    AxeDeVersement.HONORAIRES: "Honoraires",
    AxeDeVersement.TVA: "TVA (fonds de tiers)",
    AxeDeVersement.CNPS: "CNPS (fonds de tiers)",
    AxeDeVersement.IRPP_SALAIRES: "IRPP sur salaires (fonds de tiers)",
}


@routeur.get(
    "/journal.xlsx",
    summary="Le journal de caisse en Excel",
    response_class=Response,
    responses={
        200: {"content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}}}
    },
)
def exporter_le_journal(
    acces: AccesRequis, du: date | None = Query(None), au: date | None = Query(None)
) -> Response:
    exiger(acces, _droit_de_lecture(acces))
    journal = _journal(acces, du, au)
    classeur = Workbook()
    feuille = classeur.active
    feuille.title = "Journal"
    gras = Font(bold=True)
    feuille.append([f"Journal de caisse du {journal.du:%d/%m/%Y} au {journal.au:%d/%m/%Y}"])
    feuille["A1"].font = gras
    feuille.append([])
    feuille.append(
        ["Date", "Axe", "Objet", "Client", "Dossier", "Canal", "Référence", "Montant (FCFA)"]
    )
    for cellule in feuille[3]:
        cellule.font = gras
    for ligne in journal.lignes:
        feuille.append(
            [
                jour_a_douala(ligne.le).strftime("%d/%m/%Y"),
                _LIBELLES_AXE[ligne.axe],
                ligne.objet,
                ligne.client,
                ligne.dossier,
                "Mobile money (Tara)" if ligne.canal == "MOBILE_MONEY" else "Guichet",
                ligne.reference or "",
                float(ligne.montant),
            ]
        )
    totaux = classeur.create_sheet("Totaux par axe")
    totaux.append(["Axe", "Versements", "Montant", "dont mobile money", "dont guichet"])
    for cellule in totaux[1]:
        cellule.font = gras
    for t in journal.totaux:
        totaux.append(
            [
                _LIBELLES_AXE[t.axe],
                t.nombre,
                float(t.montant),
                float(t.dont_mobile_money),
                float(t.dont_guichet),
            ]
        )
    totaux.append([])
    totaux.append(["Total du cabinet (hors fonds de tiers)", "", float(journal.total_du_cabinet)])
    totaux.append(
        ["Fonds de tiers, dus à l'administration", "", float(journal.total_des_fonds_de_tiers)]
    )
    for colonne, largeur in zip("ABCDEFGH", (12, 26, 40, 30, 18, 20, 24, 16), strict=True):
        feuille.column_dimensions[colonne].width = largeur
    tampon = BytesIO()
    classeur.save(tampon)
    nom = f"journal-de-caisse-{journal.du:%Y%m%d}-{journal.au:%Y%m%d}.xlsx"
    return Response(
        content=tampon.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nom}"'},
    )


class NotificationNonRattachee(BaseModel):
    """Une notification Tara que rien n'attendait : un encaissement a peut-être eu lieu."""

    recue_le: datetime
    reference_externe: str | None
    identifiant_produit: str | None
    telephone: str | None
    montant: str | None
    reussi: bool | None


@routeur.get(
    "/non-rattaches",
    summary="Les notifications Tara qui ne se rapprochent d'aucun paiement attendu",
)
def lire_les_non_rattaches(acces: AccesRequis) -> list[NotificationNonRattachee]:
    """⚠️ À traiter à la main, jamais à rattacher au hasard : le cabinet appelle le numéro,
    retrouve l'objet réglé, et le constate au guichet sous la référence de l'opérateur."""
    exiger(acces, Permission.ENCAISSER)
    entrees = [
        e
        for e in atelier().journal.lister(objet_type="notification")
        if e.action == "paiement.non_affecte"
    ]
    return [
        NotificationNonRattachee(
            recue_le=e.horodatage,
            reference_externe=(e.apres or {}).get("reference_externe"),
            identifiant_produit=(e.apres or {}).get("identifiant_produit"),
            telephone=(e.apres or {}).get("telephone"),
            montant=(e.apres or {}).get("montant"),
            reussi=(e.apres or {}).get("reussi"),
        )
        for e in reversed(entrees)
    ]
