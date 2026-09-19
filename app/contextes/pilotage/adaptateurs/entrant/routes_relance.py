"""Relancer un adhérent des pièces qui manquent à son mois (pas 111), sous `/pilotage`.

─────────────────────────────────────────────────────────────────────────────────
DEUX ROUTES

* `GET  /pilotage/dossiers/{niu}/relance` : ce qui manque (déduit), l'historique des relances,
  les canaux, les modèles, et **l'aperçu du message** pour la sélection et le modèle demandés.
  L'aperçu est rendu ici, par la même fonction que l'envoi : l'écran ne compose rien.
* `POST /pilotage/dossiers/{niu}/relance` : envoie. Pour chaque pièce cochée, la demande est
  créée si elle n'existe pas (collecte), la relance est tracée par canal, l'adhérent est prévenu
  dans son espace (une entrée du journal, annoncée par un abonnement) et par courriel.

⚠️ CE QUI EST REFUSÉ, AVANT TOUT ENVOI

* une pièce qui n'est pas dans l'attente du mois (la sélection est recalculée ici, jamais crue) ;
* un canal inactif, avec son motif ;
* un dossier sans compte adhérent actif : « envoyé » serait faux ; relancer par téléphone, et
  tracer la relance depuis les pièces attendues ;
* une pièce **déjà relancée aujourd'hui par ce canal** : un double clic ne fait pas partir deux
  messages. Rien n'est envoyé si une seule pièce est dans ce cas.

`RELANCER_PIECES` : le comptable, le chargé de clientèle, le réviseur.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.collecte.api import (
    CanalDepot,
    DemandeRefusee,
    TypePiece,
    demander_une_piece,
    reglages_des_reponses,
    texte_de_la_reponse,
    tracer_une_relance,
)
from app.contextes.comptabilite.api import (
    JOURNAUX_CABINET,
    NatureJournal,
    StatutRapprochement,
    depot_des_rapprochements,
)
from app.contextes.pilotage.adaptateurs.entrant.routes_http import (
    _depot_demandes,
    _depot_dossiers,
    _depot_pieces,
    _echeancier,
    _ecritures,
    _sans_echouer,
)
from app.contextes.pilotage.adaptateurs.sortant.pieces_manquantes_yaml import (
    charger_les_reglages_des_pieces_manquantes,
)
from app.contextes.pilotage.domaine.pieces_manquantes import (
    AnomalieBloquante,
    AttenteDePiece,
    CanalDeRelance,
    DemandeObservee,
    LigneDeReleveOuverte,
    ModeleDeRelance,
    PieceObservee,
    ReglageDeCanal,
    ReglagesDesPiecesManquantes,
    RelanceRendue,
    attentes_du_mois,
    nom_du_mois,
    rendre_la_relance,
)
from app.contextes.transverse.api import (
    Acces,
    AccesRequis,
    EtatCompte,
    Permission,
    Role,
    atelier,
    depot_des_lectures,
    exiger_dossier,
    habilitations_actives,
    session_de_travail,
)
from app.infrastructure.config import configuration
from app.partage.horloge import maintenant

routeur = APIRouter(prefix="/pilotage", tags=["Pilotage · relance des pièces"])

#: Le canal de relance, tel que la demande de la collecte le trace.
_CANAL_TRACE = {
    CanalDeRelance.APPLICATION: CanalDepot.PORTAIL,
    CanalDeRelance.COURRIEL: CanalDepot.COURRIEL,
    CanalDeRelance.WHATSAPP: CanalDepot.WHATSAPP,
}


def _reglages() -> ReglagesDesPiecesManquantes:
    return charger_les_reglages_des_pieces_manquantes(configuration().dossier_referentiel)


def _bornes(mois: str) -> tuple[date, date]:
    debut = date(int(mois[:4]), int(mois[5:7]), 1)
    return debut, (debut + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def _dossier(niu: str):
    dossier = next((d for d in _depot_dossiers().lister() if d.niu == niu), None)
    if dossier is None:
        raise HTTPException(status_code=404, detail=f"dossier {niu} inconnu.")
    return dossier


def _relever_les_attentes(dossier, du: date, au: date, reglages) -> list[AttenteDePiece]:
    niu = dossier.niu
    pieces_brutes = _sans_echouer(lambda: _depot_pieces().du_dossier(niu), [])
    pieces = [
        PieceObservee(
            identifiant=p.identifiant,
            reference=p.reference_document,
            emetteur=p.emetteur,
            montant=p.montant_ttc,
            date=p.date_document or p.recue_le.date(),
        )
        for p in pieces_brutes
    ]
    reponses = reglages_des_reponses()
    demandes = [
        DemandeObservee(
            identifiant=d.identifiant,
            motif=d.motif,
            type_attendu=d.type_attendu.value,
            demandee_le=d.demandee_le,
            piece_a_rectifier=d.piece_a_rectifier,
            reponse=(
                texte_de_la_reponse(d.derniere_reponse, reponses) if d.derniere_reponse else None
            ),
            reponse_le=d.derniere_reponse.le if d.derniere_reponse else None,
        )
        for d in _sans_echouer(lambda: _depot_demandes().ouvertes(niu), [])
    ]

    rapprochements = [
        r
        for r in _sans_echouer(lambda: depot_des_rapprochements().du_dossier(niu), [])
        if r.statut is not StatutRapprochement.ABANDONNE
    ]
    ecritures = _sans_echouer(lambda: _ecritures(niu).toutes(str(du.year)), [])
    du_mois = [e for e in ecritures if du <= e.date_operation <= au]
    # Même règle que le plan de travail (pas 104), mais un relevé **importé** suffit : c'est la
    # pièce de l'adhérent qu'on attend, pas le rapprochement du comptable.
    journaux_sans_releve = [
        j.code
        for j in JOURNAUX_CABINET
        if j.nature is NatureJournal.BANQUE
        and j.compte_contrepartie
        and any(l_.compte.startswith(j.compte_contrepartie) for e in du_mois for l_ in e.lignes)
        and not any(r.journal == j.code and r.du <= au <= r.au for r in rapprochements)
    ]
    lignes_ouvertes = []
    for r in rapprochements:
        for ligne in r.lignes:
            justification = r.justification(ligne.rang)
            if r.appariement(ligne.rang) is None and (
                justification is None or justification.nature.value == "PIECE_DEMANDEE"
            ):
                lignes_ouvertes.append(
                    LigneDeReleveOuverte(
                        journal=r.journal,
                        rang=ligne.rang,
                        date=ligne.date,
                        libelle=ligne.libelle,
                        montant=ligne.montant,
                    )
                )

    obligations = [
        i.code_obligation
        for i in _sans_echouer(lambda: _echeancier(dossier, str(du.year), au), [])
        if i.periode_debut <= au and du <= i.periode_fin
    ]

    anomalies = []
    try:
        from app.contextes.conformite.api import (
            FACTURES_DEMO,
            moteur_par_defaut,
            rapport_arbitre,
        )

        moteur = moteur_par_defaut()
        for p in pieces_brutes:
            facture = FACTURES_DEMO.get(p.reference_document or "")
            if facture is None or not p.en_attente_de_traitement:
                continue
            date_piece = p.date_document or p.recue_le.date()
            if not du <= date_piece <= au:
                continue
            rapport = rapport_arbitre(moteur.controler(facture), niu)
            if rapport.comptabilisation_interdite:
                bloquant = next(c for c in rapport.constats if c.severite.value == "BLOQUANT")
                anomalies.append(
                    AnomalieBloquante(
                        piece=p.identifiant,
                        reference=p.reference_document,
                        emetteur=p.emetteur,
                        montant=p.montant_ttc,
                        regle=bloquant.code_regle,
                    )
                )
    except (AttributeError, TypeError, NameError):
        raise
    except Exception:  # noqa: BLE001
        # ⚠️ Le contrôle de conformité indisponible n'empêche pas de relancer le reste : les
        # autres origines restent justes, et l'écran le dit (aucune anomalie relevée).
        anomalies = []

    return attentes_du_mois(
        du=du,
        au=au,
        pieces=pieces,
        demandes_ouvertes=demandes,
        journaux_sans_releve=journaux_sans_releve,
        lignes_ouvertes=lignes_ouvertes,
        obligations_du_mois=obligations,
        anomalies=anomalies,
        reglages=reglages,
    )


def _date_limite(dossier, du: date, au: date, jour: date, reglages) -> tuple[date, bool, bool]:
    """L'échéance de l'obligation de référence du mois, moins le délai. Sans elle, une date
    estimée (quinze jours après la fin du mois), et la réponse le dit.

    ⚠️ Jamais dans le passé : relancer juillet le 17 septembre en annonçant « avant le 12/08 »
    ne ferait revenir aucune pièce. La date est alors reportée de `delai_si_echeance_passee`
    jours après l'envoi, et la réponse dit que l'échéance est dépassée.

    Rend (date limite, estimée, échéance dépassée).
    """
    limite, estimee = au + timedelta(days=15 - reglages.jours_avant_echeance), True
    for obligation in _sans_echouer(lambda: _echeancier(dossier, str(du.year), au), []):
        if (
            obligation.code_obligation == reglages.obligation_de_reference
            and obligation.periode_debut <= du
            and au <= obligation.periode_fin
        ):
            limite = obligation.echeance - timedelta(days=reglages.jours_avant_echeance)
            estimee = False
            break
    if limite < jour:
        return jour + timedelta(days=reglages.delai_si_echeance_passee), estimee, True
    return limite, estimee, False


class Destinataire(BaseModel):
    compte: str
    nom: str
    courriel: str


def _destinataires(niu: str, jour: date) -> list[Destinataire]:
    boutique = atelier()
    retenus = []
    for h in habilitations_actives(boutique.habilitations.pour_dossier(niu), jour):
        if h.role is not Role.ADHERENT:
            continue
        compte = boutique.comptes.lire(h.compte)
        if compte.etat is not EtatCompte.ACTIF:
            continue
        retenus.append(
            Destinataire(
                compte=compte.identifiant,
                nom=f"{compte.prenom} {compte.nom}".strip(),
                courriel=compte.courriel,
            )
        )
    return sorted(retenus, key=lambda d: d.nom)


class EvenementDeRelance(BaseModel):
    quand: datetime | date
    canal: str
    contenu: str
    #: « Lu », « Non lu » (application), « Envoyé » (courriel), « Tracée » (relance consignée).
    etat: str


def _historique(niu: str) -> list[EvenementDeRelance]:
    evenements: list[EvenementDeRelance] = []
    journal = atelier().journal
    lectures = depot_des_lectures(session_de_travail())
    envois = journal.lister(objet_type="relance_de_pieces", objet_id=niu)
    for envoi in envois:
        if envoi.action != "pilotage.relance_envoyee":
            continue
        canaux = envoi.apres.get("canaux", [])
        pour_l_adherent = [
            e
            for e in envois
            if e.action == "pilotage.relance_a_l_adherent"
            and e.apres.get("envoi") == envoi.apres.get("envoi")
        ]
        for canal in canaux:
            if canal == CanalDeRelance.APPLICATION.value:
                lus = [
                    (lectures.lu_jusqu_au_rang(e.apres["compte"]) or 0) >= e.rang
                    for e in pour_l_adherent
                ]
                etat = "Lu" if lus and all(lus) else "Non lu"
            else:
                etat = "Envoyé"
            evenements.append(
                EvenementDeRelance(
                    quand=envoi.horodatage,
                    canal=canal,
                    contenu=f"{envoi.apres.get('nombre', 0)} pièce(s) demandée(s) : "
                    + ", ".join(envoi.apres.get("pieces", [])[:4]),
                    etat=etat,
                )
            )
    # Les relances tracées à la main (pas 74), qui ne sont pas passées par cet écran.
    envoyees = {
        (e.horodatage.date(), c)
        for e in envois
        if e.action == "pilotage.relance_envoyee"
        for c in e.apres.get("canaux", [])
    }
    inverse = {v: k for k, v in _CANAL_TRACE.items()}
    for demande in _sans_echouer(lambda: _depot_demandes().ouvertes(niu), []):
        for jour, canal in demande.relances:
            nom = inverse.get(canal, canal).value if canal in inverse else canal.value
            if (jour, nom) in envoyees:
                continue
            evenements.append(
                EvenementDeRelance(quand=jour, canal=nom, contenu=demande.motif, etat="Tracée")
            )

    def cle(e: EvenementDeRelance):
        return e.quand.isoformat() if isinstance(e.quand, datetime) else f"{e.quand.isoformat()}T"

    return sorted(evenements, key=cle, reverse=True)


class VueDeRelance(BaseModel):
    dossier: str
    denomination: str
    mois: str
    du: date
    au: date
    attentes: list[AttenteDePiece]
    #: Les codes retenus pour l'aperçu : ceux demandés, ou toutes les attentes.
    selection: list[str]
    date_limite: date
    date_limite_estimee: bool
    #: L'échéance du mois est passée : la date annoncée a été reportée après l'envoi.
    echeance_depassee: bool
    modeles: list[ModeleDeRelance]
    modele: str
    apercu: RelanceRendue
    canaux: list[ReglageDeCanal]
    destinataires: list[str]
    historique: list[EvenementDeRelance]
    derniere_relance: datetime | date | None


def _vue(acces: Acces, niu: str, mois: str, codes: list[str] | None, modele: str | None):
    reglages = _reglages()
    dossier = _dossier(niu)
    du, au = _bornes(mois)
    attentes = _relever_les_attentes(dossier, du, au, reglages)
    connus = {a.code for a in attentes}
    selection = [
        c for c in (codes if codes is not None else [a.code for a in attentes]) if c in connus
    ]
    choisi = next((m for m in reglages.modeles if m.code == modele), reglages.modeles[0])
    limite, estimee, depassee = _date_limite(dossier, du, au, maintenant().date(), reglages)
    retenues = [a for a in attentes if a.code in selection]
    apercu = rendre_la_relance(
        choisi,
        attentes=retenues,
        du=du,
        date_limite=limite,
        signataire=acces.nom_complet,
        cabinet=reglages.cabinet,
    )
    historique = _historique(niu)
    return (
        VueDeRelance(
            dossier=niu,
            denomination=dossier.denomination,
            mois=mois,
            du=du,
            au=au,
            attentes=attentes,
            selection=selection,
            date_limite=limite,
            date_limite_estimee=estimee,
            echeance_depassee=depassee,
            modeles=list(reglages.modeles),
            modele=choisi.code,
            apercu=apercu,
            canaux=list(reglages.canaux),
            destinataires=[d.nom for d in _destinataires(niu, maintenant().date())],
            historique=historique,
            derniere_relance=historique[0].quand if historique else None,
        ),
        reglages,
        attentes,
        retenues,
        choisi,
        limite,
    )


@routeur.get(
    "/dossiers/{niu}/relance",
    summary="Ce qui manque au mois d'un dossier, et l'aperçu de sa relance",
    responses={404: {"description": "Dossier inconnu ou hors périmètre"}},
)
def lire_la_relance(
    acces: AccesRequis,
    niu: str,
    mois: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    attentes: str | None = Query(None, description="Codes séparés par des virgules"),
    modele: str | None = Query(None),
) -> VueDeRelance:
    exiger_dossier(acces, Permission.RELANCER_PIECES, niu)
    codes = None if attentes is None else [c for c in attentes.split(",") if c]
    vue, *_ = _vue(acces, niu, mois, codes, modele)
    return vue


class DemandeDeRelance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mois: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    attentes: list[str] = Field(min_length=1, max_length=50)
    canaux: list[CanalDeRelance] = Field(min_length=1, max_length=3)
    modele: str = Field(min_length=2, max_length=40)


class ResultatDeRelance(BaseModel):
    envoi: str
    demandes_creees: list[str]
    demandes_relancees: list[str]
    destinataires: int
    canaux: list[CanalDeRelance]
    message: str


@routeur.post(
    "/dossiers/{niu}/relance",
    summary="Envoyer la relance des pièces cochées",
    status_code=201,
    responses={
        404: {"description": "Dossier inconnu ou hors périmètre"},
        409: {"description": "Déjà relancée aujourd'hui par ce canal"},
        422: {"description": "Pièce hors attente, canal inactif, aucun destinataire"},
    },
)
def envoyer_la_relance(
    acces: AccesRequis, niu: str, demande: DemandeDeRelance
) -> ResultatDeRelance:
    exiger_dossier(acces, Permission.RELANCER_PIECES, niu)
    vue, reglages, attentes, retenues, modele, limite = _vue(
        acces, niu, demande.mois, demande.attentes, demande.modele
    )
    if modele.code != demande.modele:
        raise HTTPException(status_code=422, detail=f"modèle « {demande.modele} » inconnu.")
    inconnues = sorted(set(demande.attentes) - {a.code for a in attentes})
    if inconnues:
        raise HTTPException(
            status_code=422,
            detail=f"pièce(s) hors de l'attente de {demande.mois} : {', '.join(inconnues)}. "
            "Recharger l'écran : l'attente a pu changer.",
        )
    canaux = list(dict.fromkeys(demande.canaux))
    for canal in canaux:
        reglage = reglages.canal(canal)
        if reglage is None or not reglage.actif:
            motif = reglage.motif if reglage else "canal non réglé au référentiel"
            raise HTTPException(status_code=422, detail=f"canal {canal.value} inactif : {motif}")
    jour = maintenant().date()
    destinataires = _destinataires(niu, jour)
    if not destinataires:
        raise HTTPException(
            status_code=422,
            detail="aucun compte adhérent actif sur ce dossier : la relance ne partirait vers "
            "personne. Relancer par téléphone, puis tracer la relance depuis les pièces attendues.",
        )

    depot = _depot_demandes()
    # ⚠️ Tout se vérifie avant d'écrire : une relance ne part pas à moitié.
    deja = []
    for attente in retenues:
        if attente.demande is None:
            continue
        existante = depot.par_identifiant(attente.demande)
        for canal in canaux:
            if existante is not None and (jour, _CANAL_TRACE[canal]) in existante.relances:
                deja.append(f"{attente.libelle} ({canal.value})")
    if deja:
        raise HTTPException(
            status_code=409,
            detail="déjà relancée aujourd'hui : "
            + " ; ".join(deja)
            + ". Rien n'est envoyé : un second message le même jour lasserait l'adhérent.",
        )

    creees, relancees = [], []
    try:
        for attente in retenues:
            identifiant = attente.demande or f"ATT-{demande.mois}-{attente.code}"
            if attente.demande is None:
                demander_une_piece(
                    identifiant=identifiant,
                    entreprise=niu,
                    type_attendu=TypePiece(attente.type_attendu),
                    motif=attente.libelle,
                    attendue_pour=limite,
                    le=jour,
                    par=acces.compte,
                    demandes=depot,
                )
                creees.append(identifiant)
            for canal in canaux:
                tracer_une_relance(identifiant, canal=_CANAL_TRACE[canal], le=jour, demandes=depot)
            relancees.append(identifiant)
    except DemandeRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus

    horodatage = maintenant()
    envoi = f"{niu}:{horodatage.isoformat()}"
    rendu = vue.apercu
    boutique = atelier()
    if CanalDeRelance.APPLICATION in canaux:
        for destinataire in destinataires:
            boutique.journal.ajouter(
                horodatage=horodatage,
                acteur=acces.compte,
                action="pilotage.relance_a_l_adherent",
                objet_type="relance_de_pieces",
                objet_id=niu,
                apres={
                    "envoi": envoi,
                    "dossier": niu,
                    "compte": destinataire.compte,
                    "mois": nom_du_mois(vue.du),
                    "nombre": len(retenues),
                    "date_limite": f"{limite:%d/%m/%Y}",
                },
            )
    if CanalDeRelance.COURRIEL in canaux:
        for destinataire in destinataires:
            boutique.notifications.envoyer(
                "piece.relance",
                destinataire=destinataire.courriel,
                contexte={
                    "prenom": destinataire.nom.split(" ")[0],
                    "mois": nom_du_mois(vue.du),
                    "introduction": rendu.introduction,
                    "liste": " ; ".join(rendu.liste),
                    "conclusion": rendu.conclusion,
                    "signature": rendu.signature,
                },
            )
    boutique.journal.ajouter(
        horodatage=horodatage,
        acteur=acces.compte,
        action="pilotage.relance_envoyee",
        objet_type="relance_de_pieces",
        objet_id=niu,
        apres={
            "envoi": envoi,
            "dossier": niu,
            "mois": demande.mois,
            "pieces": rendu.liste,
            "nombre": len(retenues),
            "canaux": [c.value for c in canaux],
            "destinataires": len(destinataires),
            "modele": modele.code,
            "par": acces.nom_complet,
        },
    )
    return ResultatDeRelance(
        envoi=envoi,
        demandes_creees=creees,
        demandes_relancees=relancees,
        destinataires=len(destinataires),
        canaux=canaux,
        message=rendu.texte,
    )


__all__ = ["routeur"]
