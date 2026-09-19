"""Le travail périodique qui envoie les rappels d'échéance aux adhérents (pas 115).

⚠️ **Adaptateur entrant.** L'ordonnanceur appelle ce code comme une requête appelle une route (voir
`souscription/adaptateurs/entrant/travail_de_veille.py`, même motif).

─────────────────────────────────────────────────────────────────────────────────
CE QU'UN PASSAGE FAIT

Pour chaque dossier du locataire qui a au moins un compte adhérent actif : chaque période à venir,
non déposée et sans preuve (`echeances_a_rappeler`, à côté des cartes de « Mes échéances » du pas
113, avec les mêmes titres et périodes ; pas les cartes elles-mêmes, qui cachent les périodes
suivantes derrière un retard : voir la fonction) ; pour
chaque compte, son réglage ; pour chaque carte, le jalon dû (`jalon_du`). Un rappel dû et jamais
envoyé part **par courriel** (gabarit `echeance.rappel`) et **dans l'espace** (une entrée du journal
annoncée au compte par un abonnement).

⚠️ UNE FOIS PAR COMPTE, PAR ÉCHÉANCE ET PAR JALON

La clé du rappel est écrite au journal avec lui : « déjà envoyé » se relit au passage suivant, et le
travail peut tourner toutes les heures, redémarrer ou être relancé à la main sans doubler un
message. Le courriel part **avant** l'écriture au journal, dans la même transaction du tour : un
envoi qui lève empêche l'écriture, et le rappel repartira au passage suivant plutôt que d'être
compté comme envoyé sans l'avoir été.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import timedelta

from app.contextes.obligations.adaptateurs.entrant.routes_espace_adherent import (
    _instances,
    _preuves,
    _reglages as _reglages_des_echeances,
)
from app.contextes.obligations.adaptateurs.entrant.routes_http import depot_entreprises
from app.contextes.obligations.adaptateurs.entrant.routes_rappels import (
    preference_du_compte,
    reglages_des_rappels,
)
from app.contextes.obligations.domaine.echeances_adherent import echeances_a_rappeler
from app.contextes.obligations.domaine.rappels_adherent import jalon_du
from app.contextes.transverse.api import EtatCompte, Role, atelier, habilitations_actives
from app.orchestration.ordonnanceur import Travail
from app.partage.horloge import maintenant

__all__ = ["NOM_TRAVAIL", "OBJET_RAPPEL", "envoyer_les_rappels", "travail_des_rappels"]

NOM_TRAVAIL = "rappels-d-echeance"
#: Le type d'objet des rappels envoyés au journal, par compte.
OBJET_RAPPEL = "rappel_d_echeance"


def travail_des_rappels() -> Travail:
    return Travail(
        nom=NOM_TRAVAIL,
        cadence=timedelta(minutes=reglages_des_rappels().cadence_minutes),
        objet=(
            "envoie aux adhérents les rappels d'échéance qu'ils ont réglés (par défaut 7 et 2 "
            "jours avant), une fois par échéance et par jalon"
        ),
    )


def cle_du_rappel(dossier: str, code: str, periode_debut, jalon: int) -> str:
    """⚠️ Le dossier est dans la clé : un adhérent qui suit deux entreprises employeuses a deux CNPS
    d'août. Sans lui, le rappel de la seconde passait pour déjà envoyé (vu en écrivant la batterie de
    mutations du pas 115)."""
    return f"{dossier}:{code}:{periode_debut.isoformat()}:J-{jalon}"


def envoyer_les_rappels() -> str:
    boutique = atelier()
    instant = maintenant()
    jour = instant.date()
    rappels = reglages_des_rappels()
    reglages = _reglages_des_echeances()
    envoyes = 0
    for dossier in depot_entreprises().lister():
        comptes = []
        for h in habilitations_actives(boutique.habilitations.pour_dossier(dossier.niu), jour):
            if h.role is not Role.ADHERENT:
                continue
            compte = boutique.comptes.lire(h.compte)
            if compte.etat is EtatCompte.ACTIF:
                comptes.append(compte)
        if not comptes:
            continue
        a_venir = echeances_a_rappeler(
            instances=_instances(dossier, jour, reglages.horizon_jours),
            jour=jour,
            reglages=reglages,
            preuves=_preuves(dossier.niu),
        )
        for compte in comptes:
            preference = preference_du_compte(compte.identifiant, rappels)
            if not preference.actifs:
                continue
            deja = {
                e.apres.get("cle")
                for e in boutique.journal.lister(objet_type=OBJET_RAPPEL, objet_id=compte.identifiant)
                if e.action == "obligations.rappel_d_echeance"
            }
            for carte in a_venir:
                jalon = jalon_du(carte.jours, preference.jalons)
                if jalon is None:
                    continue
                cle = cle_du_rappel(dossier.niu, carte.code_obligation, carte.periode_debut, jalon)
                if cle in deja:
                    continue
                contexte = {
                    "prenom": compte.prenom,
                    "titre": carte.titre,
                    "periode": carte.periode,
                    "echeance": f"{carte.echeance:%d/%m/%Y}",
                    "jours": str(carte.jours),
                }
                boutique.notifications.envoyer(
                    "echeance.rappel", destinataire=compte.courriel, contexte=contexte
                )
                boutique.journal.ajouter(
                    horodatage=instant,
                    acteur="systeme",
                    action="obligations.rappel_d_echeance",
                    objet_type=OBJET_RAPPEL,
                    objet_id=compte.identifiant,
                    apres={**contexte, "cle": cle, "compte": compte.identifiant, "dossier": dossier.niu},
                )
                # Défensif : les clés d'un même passage sont distinctes (dossier, obligation, période,
                # jalon), et la batterie du pas 115 l'a montré (mutant équivalent). L'ajout protège le
                # jour où une même échéance apparaîtrait deux fois dans la lecture.
                deja.add(cle)
                envoyes += 1
    return f"{envoyes} rappel(s) d'échéance envoyé(s)"
