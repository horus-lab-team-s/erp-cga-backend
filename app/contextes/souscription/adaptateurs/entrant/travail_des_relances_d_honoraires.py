"""Le travail de fond qui relance les adhérents sur leurs honoraires (9 novembre 2026).

⚠️ **Adaptateur entrant.** L'ordonnanceur appelle ce code comme une requête appelle une
route (même motif que `travail_de_rappel_des_rendez_vous.py`).

─────────────────────────────────────────────────────────────────────────────────
CE QU'UN PASSAGE FAIT

Pour chaque dossier sous contrat d'honoraires : les paliers atteints ce jour
(`domaine/relances_d_honoraires.py`), moins ceux déjà traités ; la relance la plus grave
part par courriel aux comptes ADHÉRENT actifs du dossier, et arrive dans leur cloche
(une entrée du journal par compte, annoncée par l'abonnement
`honoraires.relance_adressee`). Les autres paliers nouveaux sont inscrits comme couverts.

⚠️ REJOUABLE. Le journal d'audit sert de mémoire, comme pour les alertes d'expiration :
une entrée `relance_honoraires` par palier et par échéance (`objet_id` = la clé). Le
travail peut tourner toutes les heures, redémarrer ou être relancé à la main sans
doubler un message. Le courriel part avant l'écriture au journal, dans la transaction
du tour : un tour qui échoue ne compte rien comme envoyé.

⚠️ UN DOSSIER SANS DESTINATAIRE N'EST PAS MARQUÉ. Sans adhérent actif ayant une adresse,
rien n'est inscrit : le jour où un compte s'ouvre, il reçoit le palier en cours.

⚠️ CADENCE HORAIRE, comme les alertes d'expiration : la garde rend les passages
supplémentaires gratuits, et une suspension se dit le jour même plutôt que le lendemain.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.contextes.souscription.domaine.relances_d_honoraires import (
    RelanceDHonoraires,
    relance_a_envoyer,
    relances_atteintes,
)
from app.contextes.souscription.domaine.situation_de_reglement import (
    REGLE_D_OFFICE,
    RegleDeDepassement,
)
from app.contextes.transverse.api import EtatCompte, Role, atelier, habilitations_actives
from app.infrastructure.config import configuration
from app.orchestration.ordonnanceur import Travail
from app.partage.formats import ESPACE_FINE, MOIS, date_longue, montant
from app.partage.horloge import aujourd_hui, maintenant

__all__ = [
    "ACTION_ADRESSEE",
    "NOM_TRAVAIL",
    "OBJET_RELANCE",
    "relancer_les_honoraires",
    "travail_des_relances_d_honoraires",
]

NOM_TRAVAIL = "relances-des-honoraires"
#: La trace, une par palier et par échéance : la mémoire du travail.
OBJET_RELANCE = "relance_honoraires"
ACTION_ENVOYEE = "honoraires.relance_envoyee"
ACTION_COUVERTE = "honoraires.relance_couverte"
#: Une par compte prévenu : ce que lit la cloche de l'adhérent.
OBJET_AVIS = "relance_honoraires_avis"
ACTION_ADRESSEE = "honoraires.relance_adressee"

#: Ce que la cloche affiche, par palier.
_TITRES = {
    "echeance_a_regler": "Honoraires de {periode} à régler",
    "en_retard": "Honoraires de {periode} en retard",
    "preavis_suspension": "Votre espace sera suspendu le {suspension_le}",
    "acces_suspendu": "Votre espace est suspendu",
}
_TEXTES = {
    "echeance_a_regler": "{montant} FCFA, à régler au plus tard le {limite}.",
    "en_retard": "{montant} FCFA exigibles le {exigible_le}. Total dû : {du} FCFA.",
    "preavis_suspension": "Total dû : {du} FCFA. Réglez avant cette date pour garder l'accès.",
    "acces_suspendu": "Total dû : {du} FCFA. Vos honoraires restent consultables et réglables.",
}


def travail_des_relances_d_honoraires() -> Travail:
    return Travail(
        nom=NOM_TRAVAIL,
        cadence=timedelta(hours=1),
        objet=(
            "relance l'adhérent sur ses honoraires : échéance à régler, retard, préavis de "
            "suspension, accès suspendu ; une fois par palier et par échéance"
        ),
    )


def _comptoir():
    from app.contextes.souscription.adaptateurs.entrant.routes_http import comptoir

    return comptoir()


def _destinataires(boutique, niu: str, jour: date):
    """Les comptes ADHÉRENT habilités ce jour, actifs et joignables par courriel."""
    comptes = []
    for h in habilitations_actives(boutique.habilitations.pour_dossier(niu), jour):
        if h.role is not Role.ADHERENT:
            continue
        try:
            compte = boutique.comptes.lire(h.compte)
        except LookupError:
            continue
        if compte.etat is EtatCompte.ACTIF and compte.courriel:
            comptes.append(compte)
    return comptes


def _fcfa(valeur: Decimal) -> str:
    return montant(valeur).replace(ESPACE_FINE, " ")


def _periode(r: RelanceDHonoraires) -> str:
    """« novembre 2026 » pour un mois ; sinon les bornes de la période."""
    if (r.fin_de_periode - r.periode).days < 31:
        return f"{MOIS[r.periode.month - 1]} {r.periode.year}"
    return f"du {date_longue(r.periode)} au {date_longue(r.fin_de_periode)}"


def contexte_de_la_relance(
    r: RelanceDHonoraires, denomination: str, regle: RegleDeDepassement, jour: date
) -> dict[str, str]:
    """Le contexte du courriel, sans le prénom (posé par compte)."""
    base = configuration().adresse_publique_site.rstrip("/")
    suspension = r.suspension_le
    return {
        "denomination": denomination,
        "periode": _periode(r),
        "montant": _fcfa(r.montant),
        "du": _fcfa(r.du),
        "exigible_le": date_longue(r.exigible_le),
        "limite": date_longue(r.limite),
        "suspension_le": date_longue(suspension) if suspension else "",
        "jours": str(max((suspension - jour).days, 0)) if suspension else "",
        "mois": str(regle.acces_client_mois),
        "lien": f"{base}/fr/mon-espace/honoraires",
    }


def relancer_les_honoraires(_boite=None, a_la_date: date | None = None) -> str:
    jour = a_la_date or aujourd_hui()
    instant = maintenant()
    boutique = atelier()
    comptoir = _comptoir()
    regle = comptoir.regle_de_depassement.lire() or REGLE_D_OFFICE
    deja = {
        str(e.objet_id) for e in boutique.journal.lister(objet_type=OBJET_RELANCE)
    }
    envoyees = 0
    for contrat in comptoir.contrats_honoraires.lister():
        atteintes = relances_atteintes(
            contrat,
            comptoir.reglements_honoraires.pour(contrat.niu),
            regle,
            comptoir.graces_de_reglement.pour(contrat.niu),
            jour,
        )
        choisie, couvertes = relance_a_envoyer(atteintes, deja)
        if choisie is None:
            continue
        comptes = _destinataires(boutique, contrat.niu, jour)
        if not comptes:
            continue
        contexte = contexte_de_la_relance(choisie, contrat.denomination, regle, jour)
        prevenus = 0
        for compte in comptes:
            if boutique.notifications.envoyer(
                choisie.palier.gabarit,
                destinataire=compte.courriel,
                contexte={"prenom": compte.appellation, **contexte},
            ):
                prevenus += 1
            boutique.journal.ajouter(
                horodatage=instant,
                acteur="systeme",
                action=ACTION_ADRESSEE,
                objet_type=OBJET_AVIS,
                objet_id=compte.identifiant,
                apres={
                    "compte": compte.identifiant,
                    "dossier": contrat.niu,
                    "palier": choisie.palier.value,
                    "cle": choisie.cle,
                    "titre": _TITRES[choisie.palier.value].format(**contexte),
                    "texte": _TEXTES[choisie.palier.value].format(**contexte),
                },
            )
        boutique.journal.ajouter(
            horodatage=instant,
            acteur="systeme",
            action=ACTION_ENVOYEE,
            objet_type=OBJET_RELANCE,
            objet_id=choisie.cle,
            apres={
                "dossier": contrat.niu,
                "palier": choisie.palier.value,
                "periode": choisie.periode.isoformat(),
                "comptes": len(comptes),
                "courriels_partis": prevenus,
            },
        )
        for r in couvertes:
            boutique.journal.ajouter(
                horodatage=instant,
                acteur="systeme",
                action=ACTION_COUVERTE,
                objet_type=OBJET_RELANCE,
                objet_id=r.cle,
                apres={
                    "dossier": contrat.niu,
                    "palier": r.palier.value,
                    "periode": r.periode.isoformat(),
                    "couverte_par": choisie.cle,
                },
            )
        deja.update({choisie.cle, *(r.cle for r in couvertes)})
        envoyees += 1
    return f"{envoyees} relance(s) d'honoraires envoyée(s)"
