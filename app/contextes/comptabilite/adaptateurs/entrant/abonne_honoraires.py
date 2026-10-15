"""Un règlement d'honoraires propose son écriture dans les livres de l'adhérent (15 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE CABINET A DEMANDÉ : « pas de double saisie ». L'adhérent règle ses honoraires
au guichet ou par mobile money ; la Souscription le constate. Sans ce pont, le
comptable ressaisissait la même somme dans les livres du même adhérent.

L'ÉCRITURE PROPOSÉE, ET POURQUOI EN BROUILLON

    632 Rémunérations d'intermédiaires et de conseils   D  montant
    571 Caisse (guichet)  ou  5231 Monnaie électronique (mobile money)   C  montant

Journal CA ou MM selon le canal. Elle naît EN BROUILLON, comme toute écriture : le
comptable la relit et la valide ; il peut la corriger (le compte de trésorerie réel de
l'adhérent, par exemple). Elle n'est jamais validée d'office.

⚠️ REJOUABLE : le relais garantit « au moins une fois ». L'écriture porte la référence
`HON-<identifiant du règlement>` ; une écriture qui la porte déjà n'est pas refaite.

⚠️ ELLE NE FORCE RIEN : pas d'exercice ouvert à la date du règlement, période
verrouillée, compte absent du plan : l'écriture n'est pas proposée, et le refus est
journalisé pour le comptable. Le règlement, lui, reste acquis.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.orchestration.boite_d_envoi import EvenementSortant

__all__ = ["abonne_honoraires_regles", "proposer_l_ecriture_des_honoraires"]

_journal = logging.getLogger(__name__)

COMPTE_HONORAIRES = "632"
TRESORERIE = {"GUICHET": ("CA", "571"), "MOBILE_MONEY": ("MM", "5231")}


def proposer_l_ecriture_des_honoraires(
    evenement: EvenementSortant, *, unite: Any = None
) -> str | None:
    """Rend la clé de l'écriture proposée, ou `None` si elle existait ou ne se propose pas."""
    from app.contextes.comptabilite.adaptateurs.entrant.routes_http import (
        _dossier_du_portefeuille,
        _journaux,
        _plan,
        depot,
        periodes_verrouillees_du_dossier,
    )
    from app.contextes.comptabilite.application.tenue_du_journal import (
        BrouillonEcriture,
        SaisieRefusee,
        enregistrer_une_ecriture,
    )
    from app.contextes.comptabilite.domaine.entites import LigneEcriture, Sens
    from app.contextes.transverse.api import unite_de_travail as unite_de_la_plateforme
    from app.infrastructure.config import configuration
    from app.partage.locataire import etabli

    charge = evenement.charge
    niu = charge["niu"]
    reference = f"HON-{charge['identifiant']}"
    paye_le = datetime.fromisoformat(charge["constate_le"]).date()
    periode = charge["periode"]
    montant = Decimal(charge["montant"])
    journal, tresorerie = TRESORERIE.get(charge["canal"], ("OD", "571"))
    locataire = configuration().locataire_par_defaut

    with etabli(locataire), (unite or unite_de_la_plateforme)(locataire) as boutique:
        dossier = _dossier_du_portefeuille(niu)
        exercice = next(
            (
                e
                for e in (dossier.exercices if dossier else [])
                if e.ouverture <= paye_le <= e.cloture and not e.clos
            ),
            None,
        )

        def refuser(motif: str) -> None:
            boutique.journal.ajouter(
                horodatage=datetime.fromisoformat(charge["constate_le"]),
                acteur="systeme",
                action="comptabilite.honoraires_non_proposes",
                objet_type="honoraires",
                objet_id=f"{niu}/{periode}",
                apres={"dossier": niu, "reference": reference, "motif": motif},
            )

        if exercice is None:
            refuser(f"aucun exercice ouvert du dossier au {paye_le:%d/%m/%Y}")
            return None
        depot_du_dossier = depot(niu)
        if any(e.reference_externe == reference for e in depot_du_dossier.lister(exercice.libelle)):
            return None
        mois = f"{periode[5:7]}/{periode[:4]}"
        brouillon = BrouillonEcriture(
            journal=journal,
            exercice=exercice.libelle,
            date_operation=paye_le,
            libelle=f"Honoraires CGA BRCG, période {mois}",
            piece_justificative=charge["reference"],
            reference_externe=reference,
            lignes=[
                LigneEcriture(
                    compte=COMPTE_HONORAIRES,
                    libelle=f"Honoraires du centre de gestion, {mois}",
                    sens=Sens.DEBIT,
                    montant=montant,
                    tiers="CGA BRCG",
                ),
                LigneEcriture(
                    compte=tresorerie,
                    libelle=f"Règlement {charge['reference']}",
                    sens=Sens.CREDIT,
                    montant=montant,
                ),
            ],
        )
        try:
            ecriture = enregistrer_une_ecriture(
                brouillon,
                journaux=_journaux.charger(),
                plan=_plan.charger(),
                exercice=exercice,
                periodes_verrouillees=periodes_verrouillees_du_dossier(niu),
                depot=depot_du_dossier,
                par="systeme",
            )
        except SaisieRefusee as refus:
            refuser(str(refus))
            return None
        return ecriture.cle


def abonne_honoraires_regles(unite: Any = None):
    """La fabrique inscrite au relais."""
    return lambda evenement: proposer_l_ecriture_des_honoraires(evenement, unite=unite)
