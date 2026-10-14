"""Constater les fonds remis, et le reversement à l'administration (14 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
UN SEUL CORPS POUR LES DEUX PORTES

Les fonds arrivent par la notification de l'opérateur (mobile money) ou par le
constat de la caisse (guichet). Les deux portes appellent `constater_les_fonds` :
recopiées, l'une aurait publié l'événement et l'autre non, et l'échéance de
l'adhérent n'aurait changé que pour un canal sur deux.

LES ÉVÉNEMENTS, PARCE QUE LE GRAPHE L'EXIGE

L'échéance de l'adhérent vit aux Obligations, qui ne lisent pas la Souscription ;
la Souscription, elle, ne lit que le portefeuille. Ce qui doit traverser traverse
par la boîte d'envoi, le mécanisme prévu, dans la même transaction que l'appel :
`FondsDeTiersReçus`, puis `FondsDeTiersReversés`.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime

from app.contextes.souscription.domaine.fonds_de_tiers import AppelDeFonds
from app.contextes.souscription.domaine.honoraires import CanalDeReglement
from app.orchestration.boite_d_envoi import deposer

__all__ = [
    "NOM_FONDS_RECUS",
    "NOM_FONDS_REVERSES",
    "constater_les_fonds",
    "constater_le_reversement",
]

NOM_FONDS_RECUS = "FondsDeTiersReçus"
NOM_FONDS_REVERSES = "FondsDeTiersReversés"


def _charge(appel: AppelDeFonds) -> dict:
    """⚠️ Le strict nécessaire à l'échéance de l'adhérent, et rien de plus."""
    return {
        "niu": appel.niu,
        "code_obligation": appel.code_obligation,
        "libelle": appel.libelle,
        "periode_debut": appel.periode_debut.isoformat(),
        "periode_fin": appel.periode_fin.isoformat(),
        "montant": str(appel.montant),
    }


def constater_les_fonds(
    appels,
    boite,
    appel: AppelDeFonds,
    *,
    canal: CanalDeReglement,
    reference: str,
    a_l_instant: datetime,
    par: str,
) -> AppelDeFonds:
    """Lève `TransitionDeFondsRefusee` si les fonds sont déjà reçus."""
    recu = appel.recevoir_les_fonds(
        canal=canal, reference=reference, a_l_instant=a_l_instant, par=par
    )
    appels.enregistrer(recu)
    deposer(
        boite,
        f"fr-{appel.identifiant}",
        NOM_FONDS_RECUS,
        appel.identifiant,
        {**_charge(recu), "canal": canal.value, "recus_le": a_l_instant.isoformat()},
        a_l_instant,
    )
    return recu


def constater_le_reversement(
    appels, boite, appel: AppelDeFonds, *, quittance: str, a_l_instant: datetime, par: str
) -> AppelDeFonds:
    """Lève `TransitionDeFondsRefusee` si les fonds ne sont pas reçus, ou déjà reversés."""
    reverse = appel.reverser(quittance=quittance, a_l_instant=a_l_instant, par=par)
    appels.enregistrer(reverse)
    deposer(
        boite,
        f"fv-{appel.identifiant}",
        NOM_FONDS_REVERSES,
        appel.identifiant,
        {**_charge(reverse), "quittance": quittance, "reverse_par": par},
        a_l_instant,
    )
    return reverse


class SuiteDeFondsDeTiers:
    """Ce que la notification de l'opérateur déclenche pour des fonds de tiers.

    ⚠️ ELLE NE LÈVE JAMAIS (voir `SuiteDuPaiement`), et elle est REJOUABLE : des
    fonds déjà reçus sous la même référence ne se constatent pas deux fois. Des fonds
    reçus sous une AUTRE référence pour le même appel sont un double versement : il
    est journalisé pour que la caisse rembourse, l'argent reste encaissé.
    """

    def __init__(self, *, appels, boite, journal) -> None:
        self._appels = appels
        self._boite = boite
        self._journal = journal

    def encaisser(self, paiement, a_l_instant: datetime):
        from app.contextes.souscription.application.encaissement import Aboutissement
        from app.contextes.souscription.domaine.fonds_de_tiers import EtatAppelDeFonds

        reference = paiement.reference_externe or paiement.identifiant
        try:
            appel = self._appels.lire(paiement.reference_reglee)
            if appel is None:
                return self._echec(paiement, "appel de fonds introuvable", a_l_instant)
            if appel.etat is not EtatAppelDeFonds.A_REGLER:
                if appel.reference_des_fonds == reference:
                    return Aboutissement(abouti=True, message="fonds déjà constatés")
                return self._echec(
                    paiement,
                    f"DOUBLE VERSEMENT : fonds déjà reçus ({appel.reference_des_fonds}). "
                    "L'encaissement est acquis ; la caisse doit rembourser.",
                    a_l_instant,
                )
            constater_les_fonds(
                self._appels,
                self._boite,
                appel,
                canal=CanalDeReglement.MOBILE_MONEY,
                reference=reference,
                a_l_instant=a_l_instant,
                par="systeme",
            )
        except Exception as echec:  # noqa: BLE001 — voir l'en-tête : on ne lève pas
            return self._echec(paiement, f"{type(echec).__name__}: {echec}", a_l_instant)
        return Aboutissement(abouti=True, message="fonds de tiers reçus")

    def refuser(self, paiement, motif: str, a_l_instant: datetime) -> None:
        self._journal.ajouter(
            horodatage=a_l_instant,
            acteur="systeme",
            action="paiement.fonds_de_tiers_refuse",
            objet_type="appel_de_fonds",
            objet_id=paiement.reference_reglee,
            apres={"motif": motif},
        )

    def _echec(self, paiement, motif: str, a_l_instant: datetime):
        from app.contextes.souscription.application.encaissement import Aboutissement

        self._journal.ajouter(
            horodatage=a_l_instant,
            acteur="systeme",
            action="paiement.fonds_de_tiers_a_traiter",
            objet_type="appel_de_fonds",
            objet_id=paiement.reference_reglee,
            apres={"motif": motif, "paiement": paiement.identifiant},
        )
        return Aboutissement(abouti=False, message=motif)
