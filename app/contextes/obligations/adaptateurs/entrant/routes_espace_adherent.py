"""Les échéances de l'adhérent, et la preuve qu'il a payé (pas 113), sous `/obligations`.

─────────────────────────────────────────────────────────────────────────────────
DEUX ROUTES

* `GET  /obligations/dossiers/{entreprise}/mes-echeances` (`LIRE_DOSSIER`) : les échéances à
  montrer (en retard, preuve envoyée, à venir, déposées récemment), chacune avec son titre en
  langage courant, sa période lisible, l'explication du référentiel, la période précédente et la
  prochaine échéance.
* `POST /obligations/dossiers/{entreprise}/preuves-de-paiement` (`DEPOSER_PIECE`) : « J'ai déjà
  payé : envoyer la preuve ». La quittance est d'abord déposée comme toute pièce (collecte) ; cette
  route dit **ce qu'elle règle**, et prévient ceux qui consignent les dépôts du dossier.

⚠️ CE QUE LA PREUVE NE FAIT PAS

Elle ne déclare pas l'obligation. Consigner un dépôt reste l'acte d'un collaborateur habilité, en
session renforcée (pas 59), qui lit la quittance et la joint à l'accusé. Une quittance d'un autre
trimestre, ou illisible, ne règle rien : l'adhérent lit « le cabinet la vérifie ».

⚠️ CE QUI EST REFUSÉ

* une obligation absente de l'échéancier du dossier (404, avec les périodes connues) ;
* une obligation déjà déposée (409 : il n'y a rien à prouver) ;
* une pièce inconnue, ou d'un autre dossier (404, même réponse : on ne dit pas qu'elle existe) ;
* la même pièce déjà envoyée pour la même obligation (409 : un double appui).

Les preuves se relisent au journal d'audit : c'est lui qui date et nomme l'envoi, et un second
registre divergerait.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.obligations.adaptateurs.entrant.routes_http import _entreprise
from app.contextes.obligations.adaptateurs.sortant.accuses import accuses_du_portail
from app.contextes.obligations.adaptateurs.sortant.catalogue_obligations import (
    DepotTypesObligationMemoire,
)
from app.contextes.obligations.adaptateurs.sortant.effectif import effectif_du_dossier
from app.contextes.obligations.adaptateurs.sortant.explications_adherent_yaml import (
    charger_les_echeances_de_l_adherent,
)
from app.contextes.obligations.api import ObligationInstance, generer_echeancier
from app.contextes.obligations.domaine.echeances_adherent import (
    EcheanceDeLAdherent,
    FondsRecusParLeCabinet,
    PreuveEnvoyee,
    ReglagesDesEcheancesDeLAdherent,
    VerdictDeLaPreuve,
    echeances_de_l_adherent,
    libelle_de_periode,
)
from app.contextes.transverse.api import (
    AccesRequis,
    Permission,
    atelier,
    exiger,
    exiger_dossier,
    session_de_travail,
)
from app.infrastructure.config import configuration
from app.partage.horloge import maintenant
from app.partage.locataire import courant

routeur = APIRouter(prefix="/obligations", tags=["Obligations · espace de l'adhérent"])

_catalogue = DepotTypesObligationMemoire()

#: Le type d'objet des preuves au journal d'audit, par dossier.
OBJET_PREUVE = "preuve_de_paiement"


def _reglages() -> ReglagesDesEcheancesDeLAdherent:
    return charger_les_echeances_de_l_adherent(configuration().dossier_referentiel)


def _instances(dossier, jour: date, horizon: int) -> list[ObligationInstance]:
    """L'échéancier des exercices qui touchent l'année écoulée et l'horizon.

    ⚠️ Plusieurs exercices : la période précédente d'une obligation de janvier est en décembre de
    l'exercice d'avant, et une DSF déposée en mars porte sur l'exercice clos.
    """
    debut, fin = jour - timedelta(days=400), jour + timedelta(days=horizon)
    types = _catalogue.charger(jour)
    accuses = accuses_du_portail()
    instances: list[ObligationInstance] = []
    for exercice in dossier.exercices:
        if exercice.cloture < debut or exercice.ouverture > fin:
            continue
        instances.extend(
            generer_echeancier(
                dossier,
                types,
                exercice,
                emploie_sur=effectif_du_dossier(dossier.niu),
                accuse_de=accuses,
            )
        )
    return instances


ACTION_RECUE = "obligations.preuve_de_paiement_recue"
#: ⚠️ 14 octobre 2026 : le verdict du cabinet, écrit au même journal que la preuve. Le journal est
#: chaîné et conservé dix ans : la vérification d'une quittance engage le cabinet envers l'adhérent
#: (« payée »), elle se relit au même endroit que l'envoi, et un second registre divergerait.
ACTION_VERDICT = "obligations.preuve_de_paiement_verifiee"
#: 14 octobre 2026 : sous mandat de paiement, les fonds de l'adhérent sont reçus par le cabinet.
#: Écrit par l'abonné de `FondsDeTiersReçus` (`abonne_fonds_de_tiers`).
ACTION_FONDS = "obligations.fonds_recus_par_le_cabinet"


def _fonds_recus(niu: str) -> list[FondsRecusParLeCabinet]:
    return [
        FondsRecusParLeCabinet(
            code_obligation=e.apres["obligation"],
            periode_debut=date.fromisoformat(e.apres["periode_debut"]),
            montant=Decimal(e.apres["montant"]),
            canal=e.apres.get("canal", ""),
            recus_le=e.horodatage,
        )
        for e in atelier().journal.lister(objet_type=OBJET_PREUVE, objet_id=niu)
        if e.action == ACTION_FONDS
    ]


def _preuves(niu: str | None) -> list[PreuveEnvoyee]:
    """Les preuves d'un dossier (ou de tous, `None`), chacune avec son verdict s'il existe.

    ⚠️ Le verdict se rattache par (dossier, obligation, période, pièce) : la même pièce ne
    s'envoie qu'une fois par échéance, c'est la clé que l'envoi garantit.
    """
    entrees = atelier().journal.lister(objet_type=OBJET_PREUVE, objet_id=niu)
    verdicts = {
        (e.objet_id, e.apres["obligation"], e.apres["periode_debut"], e.apres["piece"]): e
        for e in entrees
        if e.action == ACTION_VERDICT
    }
    preuves = []
    for e in entrees:
        if e.action != ACTION_RECUE:
            continue
        cle = (e.objet_id, e.apres["obligation"], e.apres["periode_debut"], e.apres["piece"])
        v = verdicts.get(cle)
        preuves.append(
            PreuveEnvoyee(
                code_obligation=e.apres["obligation"],
                periode_debut=date.fromisoformat(e.apres["periode_debut"]),
                piece=e.apres["piece"],
                envoyee_le=e.horodatage,
                par=e.acteur,
                verdict=VerdictDeLaPreuve(v.apres["verdict"]) if v else None,
                motif=v.apres.get("motif") if v else None,
                verifiee_le=v.horodatage if v else None,
                verifiee_par=v.acteur if v else None,
            )
        )
    return preuves


class VueDesEcheances(BaseModel):
    dossier: str
    denomination: str
    echeances: list[EcheanceDeLAdherent]
    #: « A_VALIDER » tant que le fiscaliste n'a pas relu les explications : l'écran le dit.
    explications_validees: bool


@routeur.get(
    "/dossiers/{entreprise}/mes-echeances",
    summary="Les échéances d'un dossier, telles que l'adhérent les lit",
    responses={404: {"description": "Dossier inconnu ou hors périmètre"}},
)
def lire_mes_echeances(acces: AccesRequis, entreprise: str) -> VueDesEcheances:
    exiger_dossier(acces, Permission.LIRE_DOSSIER, entreprise)
    reglages = _reglages()
    dossier = _entreprise(entreprise)
    jour = maintenant().date()
    montants = {a.numero: a.montant_constate for a in atelier().portail.tous()}
    return VueDesEcheances(
        dossier=entreprise,
        denomination=dossier.denomination,
        echeances=echeances_de_l_adherent(
            instances=_instances(dossier, jour, reglages.horizon_jours),
            jour=jour,
            reglages=reglages,
            montants_constates=montants,
            preuves=_preuves(entreprise),
            fonds_recus=_fonds_recus(entreprise),
        ),
        explications_validees=reglages.statut == "VALIDE",
    )


class PreuveDePaiement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code_obligation: str = Field(min_length=1, max_length=40)
    periode_debut: date
    periode_fin: date
    #: La pièce déposée juste avant (la quittance), par la collecte.
    piece: str = Field(min_length=1, max_length=80)


class PreuveRecue(BaseModel):
    preuve: PreuveEnvoyee
    titre: str
    periode: str


@routeur.post(
    "/dossiers/{entreprise}/preuves-de-paiement",
    summary="J'ai déjà payé : envoyer la preuve au cabinet",
    status_code=201,
    responses={
        404: {"description": "Obligation absente de l'échéancier, ou pièce inconnue"},
        409: {"description": "Obligation déjà déposée, ou preuve déjà envoyée"},
    },
)
def envoyer_une_preuve(
    acces: AccesRequis, entreprise: str, demande: PreuveDePaiement
) -> PreuveRecue:
    exiger_dossier(acces, Permission.DEPOSER_PIECE, entreprise)
    from app.contextes.collecte.api import DepotPiecesSql, pieces_en_memoire

    reglages = _reglages()
    dossier = _entreprise(entreprise)
    horodatage = maintenant()
    instances = _instances(dossier, horodatage.date(), reglages.horizon_jours)
    obligation = next(
        (
            o
            for o in instances
            if o.code_obligation == demande.code_obligation
            and o.periode_debut == demande.periode_debut
            and o.periode_fin == demande.periode_fin
        ),
        None,
    )
    if obligation is None:
        connues = sorted(
            libelle_de_periode(o.periode_debut, o.periode_fin)
            for o in instances
            if o.code_obligation == demande.code_obligation and not o.deposee
        )
        raise HTTPException(
            status_code=404,
            detail=f"aucune échéance {demande.code_obligation} pour cette période dans votre "
            f"dossier. Périodes à régler : {', '.join(connues) or 'aucune'}.",
        )
    if obligation.deposee:
        periode = libelle_de_periode(obligation.periode_debut, obligation.periode_fin)
        raise HTTPException(
            status_code=409,
            detail=f"{obligation.libelle} ({periode}) est déjà déposée le "
            f"{obligation.declaree_le:%d/%m/%Y} : il n'y a rien à prouver.",
        )
    session = session_de_travail()
    pieces = pieces_en_memoire() if session is None else DepotPiecesSql(session, courant())
    piece = pieces.par_identifiant(demande.piece)
    if piece is None or piece.entreprise != entreprise:
        raise HTTPException(status_code=404, detail=f"pièce {demande.piece} introuvable.")
    deja = [
        p
        for p in _preuves(entreprise)
        if p.code_obligation == obligation.code_obligation
        and p.periode_debut == obligation.periode_debut
        and p.piece == demande.piece
        # ⚠️ Une quittance refusée peut être réexpédiée corrigée : seul un envoi en attente ou
        # validé fait doublon.
        and p.verdict is not VerdictDeLaPreuve.REFUSEE
    ]
    if deja:
        raise HTTPException(
            status_code=409,
            detail=f"cette quittance a déjà été envoyée le {deja[0].envoyee_le:%d/%m/%Y} pour "
            "cette échéance : le cabinet la vérifie.",
        )
    explication = reglages.explication(obligation.code_obligation)
    titre = explication.titre if explication else obligation.libelle
    periode = libelle_de_periode(obligation.periode_debut, obligation.periode_fin)
    atelier().journal.ajouter(
        horodatage=horodatage,
        acteur=acces.compte,
        action=ACTION_RECUE,
        objet_type=OBJET_PREUVE,
        objet_id=entreprise,
        apres={
            "dossier": entreprise,
            "obligation": obligation.code_obligation,
            "libelle": titre,
            "periode": periode,
            "periode_debut": obligation.periode_debut.isoformat(),
            "periode_fin": obligation.periode_fin.isoformat(),
            "piece": piece.identifiant,
            "par": acces.nom_complet,
        },
    )
    return PreuveRecue(
        preuve=PreuveEnvoyee(
            code_obligation=obligation.code_obligation,
            periode_debut=obligation.periode_debut,
            piece=piece.identifiant,
            envoyee_le=horodatage,
            par=acces.compte,
        ),
        titre=titre,
        periode=periode,
    )


# ── La vérification des quittances (14 octobre 2026) ───────────────────────────
#
# Le cabinet a demandé « paiement de la TVA et de la CNPS, avec changement de statut ». Quand
# l'adhérent règle lui-même l'administration, sa quittance arrivait et restait « le cabinet la
# vérifie » jusqu'au dépôt de la déclaration : personne ne disait jamais « c'est payé », et
# personne n'avait d'écran pour dire « cette quittance ne va pas ».


class QuittanceAVerifier(BaseModel):
    dossier: str
    obligation: str
    libelle: str
    periode: str
    periode_debut: date
    piece: str
    envoyee_le: datetime
    par: str


@routeur.get(
    "/preuves-a-verifier",
    summary="Les quittances envoyées par les adhérents, en attente de vérification",
)
def quittances_a_verifier(acces: AccesRequis) -> list[QuittanceAVerifier]:
    """Les plus anciennes d'abord. ⚠️ Seulement celles des dossiers de sa portée."""
    exiger(acces, Permission.VERIFIER_UN_REGLEMENT)
    entrees = atelier().journal.lister(objet_type=OBJET_PREUVE)
    jugees = {
        (e.objet_id, e.apres["obligation"], e.apres["periode_debut"], e.apres["piece"])
        for e in entrees
        if e.action == ACTION_VERDICT
    }
    return [
        QuittanceAVerifier(
            dossier=e.objet_id,
            obligation=e.apres["obligation"],
            libelle=e.apres.get("libelle", e.apres["obligation"]),
            periode=e.apres.get("periode", e.apres["periode_debut"]),
            periode_debut=date.fromisoformat(e.apres["periode_debut"]),
            piece=e.apres["piece"],
            envoyee_le=e.horodatage,
            par=e.apres.get("par", e.acteur),
        )
        for e in entrees
        if e.action == ACTION_RECUE
        and acces.voit(e.objet_id)
        and (e.objet_id, e.apres["obligation"], e.apres["periode_debut"], e.apres["piece"])
        not in jugees
    ]


class VerdictDemande(BaseModel):
    code_obligation: str = Field(min_length=1)
    periode_debut: date
    piece: str = Field(min_length=1)
    verdict: VerdictDeLaPreuve
    #: Exigé pour un refus : c'est ce que l'adhérent lira pour envoyer la bonne quittance.
    motif: str | None = Field(default=None, max_length=500)


def _prevenir_du_verdict(entreprise: str, demande, motif: str, jour: date) -> None:
    """15 octobre 2026 : l'adhérent apprend le verdict par courriel, pas à sa prochaine visite.

    ⚠️ Un refus qu'il ne découvre pas laisse courir l'échéance : c'est le cas qui compte.
    Ne lève jamais (voir `prevenir_les_adherents`).
    """
    from app.contextes.transverse.api import prevenir_les_adherents

    dossier = _entreprise(entreprise)
    reglages = _reglages()
    instance = next(
        (
            o
            for o in _instances(dossier, jour, reglages.horizon_jours)
            if o.code_obligation == demande.code_obligation
            and o.periode_debut == demande.periode_debut
        ),
        None,
    )
    contexte = {
        "obligation": instance.libelle if instance else demande.code_obligation,
        "periode": (
            libelle_de_periode(instance.periode_debut, instance.periode_fin)
            if instance
            else f"{demande.periode_debut:%m/%Y}"
        ),
        "denomination": dossier.denomination,
        "lien": f"{configuration().adresse_publique_site.rstrip('/')}/fr/mon-espace/echeances",
    }
    if demande.verdict is VerdictDeLaPreuve.REFUSEE:
        prevenir_les_adherents(
            atelier(),
            entreprise,
            "adherent.quittance_refusee",
            {**contexte, "motif": motif},
            jour=jour,
        )
    else:
        prevenir_les_adherents(
            atelier(), entreprise, "adherent.quittance_verifiee", contexte, jour=jour
        )


@routeur.post(
    "/dossiers/{entreprise}/preuves-de-paiement/verdict",
    summary="Valider ou refuser la quittance envoyée par l'adhérent",
    responses={
        404: {"description": "Aucune quittance de ce dossier pour cette échéance"},
        409: {"description": "Quittance déjà vérifiée"},
        422: {"description": "Refus sans motif"},
    },
)
def juger_une_quittance(
    acces: AccesRequis, entreprise: str, demande: VerdictDemande
) -> PreuveEnvoyee:
    """⚠️ UN VERDICT NE SE REPREND PAS. Il est écrit au journal chaîné ; une erreur se répare en
    demandant à l'adhérent une nouvelle quittance, pas en effaçant ce qui a été dit."""
    exiger_dossier(acces, Permission.VERIFIER_UN_REGLEMENT, entreprise)
    motif = (demande.motif or "").strip()
    if demande.verdict is VerdictDeLaPreuve.REFUSEE and len(motif) < 5:
        raise HTTPException(
            status_code=422,
            detail="Un refus dit pourquoi : l'adhérent doit savoir quelle quittance renvoyer.",
        )
    preuve = next(
        (
            p
            for p in _preuves(entreprise)
            if p.code_obligation == demande.code_obligation
            and p.periode_debut == demande.periode_debut
            and p.piece == demande.piece
        ),
        None,
    )
    if preuve is None:
        raise HTTPException(
            status_code=404, detail="Aucune quittance de ce dossier pour cette échéance."
        )
    if preuve.verdict is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Cette quittance est déjà {preuve.verdict.value.lower()} "
            f"depuis le {preuve.verifiee_le:%d/%m/%Y}.",
        )
    horodatage = maintenant()
    atelier().journal.ajouter(
        horodatage=horodatage,
        acteur=acces.compte,
        action=ACTION_VERDICT,
        objet_type=OBJET_PREUVE,
        objet_id=entreprise,
        apres={
            "obligation": demande.code_obligation,
            "periode_debut": demande.periode_debut.isoformat(),
            "piece": demande.piece,
            "verdict": demande.verdict.value,
            **({"motif": motif} if motif else {}),
        },
    )
    try:
        _prevenir_du_verdict(entreprise, demande, motif, horodatage.date())
    except Exception:  # noqa: BLE001 — le verdict est écrit ; l'avis ne doit pas l'annuler
        logging.getLogger(__name__).exception("Avis du verdict sur %s non envoyé.", entreprise)
    return preuve.model_copy(
        update={
            "verdict": demande.verdict,
            "motif": motif or None,
            "verifiee_le": horodatage,
            "verifiee_par": acces.compte,
        }
    )


# ── Mes documents (pas 114, vue E) ────────────────────────────────────────────
#
# ⚠️ CE QUE CETTE LISTE EST, ET CE QU'ELLE N'EST PAS
#
# La maquette liste « Attestation de non-redevance », « Reçu de dépôt », « Déclaration IGS », «
# États
# financiers », « Attestation d'adhésion », « Contrat de mission ». La plateforme ne produit
# aujourd'hui **aucun document officiel** : une attestation « dont une banque peut vérifier
# l'authenticité auprès du CGA » suppose un moyen de vérification, une signature et un modèle
# arrêté par le cabinet (question Q29). Ce qui existe, et qui est vrai, ce sont **les accusés de
# dépôt** consignés au dossier : le numéro rendu par le guichet, la date opposable, le montant.
# La liste les rend, un par dépôt, et l'écran en tire un reçu imprimable qui dit ce qu'il est : le
# relevé d'un accusé, pas l'accusé délivré par l'administration.

_GUICHETS = {"DGI_TELEDECLARATION": "Impôts (DGI)", "CNPS_DIPE": "CNPS"}


class DocumentDeDepot(BaseModel):
    numero: str
    code_obligation: str
    titre: str
    periode: str
    periode_debut: date
    periode_fin: date
    depose_le: datetime
    guichet: str
    montant_constate: Decimal | None
    #: Un justificatif est archivé avec l'accusé (la quittance, la capture) : sinon, l'accusé
    #: repose sur la parole de celui qui a saisi le numéro, et le reçu le dit.
    verifiable: bool


class MesDocuments(BaseModel):
    dossier: str
    denomination: str
    documents: list[DocumentDeDepot]


@routeur.get(
    "/dossiers/{entreprise}/mes-documents",
    summary="Les accusés de dépôt d'un dossier, tels que l'adhérent les lit",
    responses={404: {"description": "Dossier inconnu ou hors périmètre"}},
)
def lire_mes_documents(acces: AccesRequis, entreprise: str) -> MesDocuments:
    exiger_dossier(acces, Permission.LIRE_DOSSIER, entreprise)
    reglages = _reglages()
    dossier = _entreprise(entreprise)
    documents = []
    for accuse in atelier().portail.tous():
        morceaux = accuse.reference_document.split("/")
        # La référence est « {dossier}/{obligation}/{AAAAMMJJ}-{AAAAMMJJ} » (teledeclaration.py).
        if len(morceaux) != 3 or morceaux[0] != entreprise:
            continue
        code, periode = morceaux[1], morceaux[2]
        try:
            debut = datetime.strptime(periode[:8], "%Y%m%d").date()
            fin = datetime.strptime(periode[9:17], "%Y%m%d").date()
        except ValueError:
            continue
        explication = reglages.explication(code)
        documents.append(
            DocumentDeDepot(
                numero=accuse.numero,
                code_obligation=code,
                titre=explication.titre if explication else code,
                periode=libelle_de_periode(debut, fin),
                periode_debut=debut,
                periode_fin=fin,
                depose_le=accuse.depose_le,
                guichet=_GUICHETS.get(accuse.portail.value, accuse.portail.value),
                montant_constate=accuse.montant_constate,
                verifiable=accuse.verifiable,
            )
        )
    return MesDocuments(
        dossier=entreprise,
        denomination=dossier.denomination,
        documents=sorted(documents, key=lambda d: (d.depose_le, d.numero), reverse=True),
    )


__all__ = ["routeur"]
