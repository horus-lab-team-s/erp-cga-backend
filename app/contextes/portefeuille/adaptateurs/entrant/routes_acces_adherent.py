"""L'accès des adhérents d'un dossier, et le lien que le chargé de clientèle leur renvoie (pas 116).

─────────────────────────────────────────────────────────────────────────────────
DEUX ROUTES, SOUS `/portefeuille`, POUR LE CHARGÉ DE CLIENTÈLE (`RELANCER_ADHERENT`)

* `GET  /portefeuille/entreprises/{niu}/acces-adherents` : les comptes adhérents du dossier, leur
  état (en attente d'activation, actif, suspendu) et les liens déjà renvoyés.
* `POST /portefeuille/entreprises/{niu}/acces-adherents/{compte}/lien` : renvoie un lien d'accès,
  **à l'adresse du compte**, avec la vérification écrite par le chargé de clientèle.

Pourquoi au portefeuille et non au transverse : le courriel nomme l'entreprise (« votre lien d'accès
à l'espace de SARL BATIMENT PLUS »), et le transverse ne lit pas les dossiers. Les règles du geste
(type du lien, limite du jour) vivent au transverse, qui garde les comptes : voir
`transverse/domaine/acces_adherent.py`.

⚠️ Réservé au chargé de clientèle, pas au comptable ni à l'administrateur : c'est lui qui connaît
l'adhérent et répond à son appel. L'administration garde ses propres gestes (suspension, levée).
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.portefeuille.adaptateurs.entrant.routes_http import _lire
from app.contextes.transverse.api import (
    AccesRequis,
    EtatCompte,
    Permission,
    RenvoiRefuse,
    Role,
    TypeJeton,
    atelier,
    emettre_jeton,
    exiger_dossier,
    habilitations_actives,
    reglages_du_renvoi,
    renvois_du_jour,
    type_de_lien,
)
from app.partage.horloge import maintenant

routeur = APIRouter(prefix="/portefeuille", tags=["Portefeuille · accès des adhérents"])

ACTION_RENVOI = "compte.lien_d_acces_renvoye"

#: Ce que le courriel dit du lien, selon son type.
_GESTES = {
    TypeJeton.ACTIVATION: ("définir votre mot de passe et ouvrir votre espace", "7 jours", "/activation"),
    TypeJeton.REINITIALISATION: ("choisir un nouveau mot de passe", "2 heures", "/reinitialisation"),
}


class LienRenvoye(BaseModel):
    type: str
    le: datetime
    par: str
    verification: str


class AccesDUnAdherent(BaseModel):
    compte: str
    nom: str
    courriel: str
    telephone: str | None
    etat: EtatCompte
    liens_renvoyes: list[LienRenvoye]


def _renvois(compte: str) -> list[LienRenvoye]:
    return [
        LienRenvoye(
            type=e.apres["type"], le=e.horodatage, par=e.apres["par"], verification=e.apres["verification"]
        )
        for e in atelier().journal.lister(objet_type="compte", objet_id=compte)
        if e.action == ACTION_RENVOI
    ]


def _adherents(niu: str):
    boutique = atelier()
    jour = maintenant().date()
    comptes = []
    for h in habilitations_actives(boutique.habilitations.pour_dossier(niu), jour):
        # `pour_dossier` ne rend que les habilitations qui couvrent ce dossier, et un adhérent a
        # toujours une portée nommée : le rôle suffit (la batterie du pas 116 a montré qu'un
        # contrôle de portée ajouté ici ne changeait rien).
        if h.role is Role.ADHERENT:
            comptes.append(boutique.comptes.lire(h.compte))
    return comptes


@routeur.get(
    "/entreprises/{niu}/acces-adherents",
    summary="Les comptes adhérents d'un dossier, et les liens renvoyés",
    responses={404: {"description": "Dossier inconnu ou hors périmètre"}},
)
def lire_les_acces(acces: AccesRequis, niu: str) -> list[AccesDUnAdherent]:
    exiger_dossier(acces, Permission.RELANCER_ADHERENT, niu)
    _lire(niu)
    return [
        AccesDUnAdherent(
            compte=c.identifiant,
            nom=f"{c.prenom} {c.nom}",
            courriel=c.courriel,
            telephone=c.telephone,
            etat=c.etat,
            liens_renvoyes=sorted(_renvois(c.identifiant), key=lambda r: r.le, reverse=True),
        )
        for c in sorted(_adherents(niu), key=lambda c: (c.nom, c.prenom))
    ]


class DemandeDeLien(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Comment l'interlocuteur a été reconnu : « rappelé au numéro du dossier », « venu avec sa CNI ».
    verification: str = Field(min_length=1, max_length=300)


@routeur.post(
    "/entreprises/{niu}/acces-adherents/{compte}/lien",
    summary="Renvoyer un lien d'accès à un adhérent, à l'adresse de son compte",
    status_code=201,
    responses={
        404: {"description": "Dossier ou compte adhérent inconnu sur ce dossier"},
        409: {"description": "Compte suspendu, ou limite de renvois du jour atteinte"},
        422: {"description": "Vérification trop courte"},
    },
)
def renvoyer_un_lien(acces: AccesRequis, niu: str, compte: str, demande: DemandeDeLien) -> LienRenvoye:
    exiger_dossier(acces, Permission.RELANCER_ADHERENT, niu)
    dossier = _lire(niu)
    reglages = reglages_du_renvoi()
    verification = demande.verification.strip()
    if len(verification) < reglages.verification_minimum:
        raise HTTPException(
            status_code=422,
            detail="écrivez comment vous avez reconnu votre interlocuteur (par exemple : rappelé au "
            "numéro du dossier). Ce texte va au journal, à votre nom.",
        )
    titulaire = next((c for c in _adherents(niu) if c.identifiant == compte), None)
    if titulaire is None:
        # Même réponse pour un compte inconnu et pour le compte d'un autre dossier.
        raise HTTPException(status_code=404, detail=f"aucun compte adhérent {compte} sur ce dossier.")
    instant = maintenant()
    try:
        type_jeton = type_de_lien(titulaire.etat)
        if renvois_du_jour([r.le.date() for r in _renvois(compte)], instant.date()) >= reglages.renvois_par_jour:
            raise RenvoiRefuse(
                f"{reglages.renvois_par_jour} lien(s) déjà renvoyé(s) aujourd'hui à ce compte. Au-delà, "
                "chaque lien valide de plus est un risque : faites venir l'adhérent au cabinet."
            )
    except RenvoiRefuse as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus

    boutique = atelier()
    _, secret = emettre_jeton(
        titulaire,
        type_jeton,
        identifiant_jeton=f"J-{uuid.uuid4().hex[:12]}",
        jetons=boutique.jetons,
        journal=boutique.journal,
        a_l_instant=instant,
        emis_par=acces.compte,
    )
    geste, validite, chemin = _GESTES[type_jeton]
    boutique.notifications.envoyer(
        "compte.acces_renvoye",
        # ⚠️ L'adresse du compte, jamais une adresse reçue : voir l'en-tête du domaine.
        destinataire=titulaire.courriel,
        contexte={
            "prenom": titulaire.prenom,
            "denomination": dossier.denomination,
            "par": acces.nom_complet,
            "geste": geste,
            "validite": validite,
            "lien": f"{chemin}?jeton={secret}",
        },
    )
    boutique.journal.ajouter(
        horodatage=instant,
        acteur=acces.compte,
        action=ACTION_RENVOI,
        objet_type="compte",
        objet_id=compte,
        apres={
            "dossier": niu,
            "type": type_jeton.value,
            "verification": verification,
            "par": acces.nom_complet,
        },
    )
    return LienRenvoye(type=type_jeton.value, le=instant, par=acces.nom_complet, verification=verification)


__all__ = ["routeur"]
