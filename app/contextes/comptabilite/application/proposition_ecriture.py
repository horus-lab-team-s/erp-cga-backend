"""Proposer l'écriture d'une facture d'achat contrôlée.

─────────────────────────────────────────────────────────────────────────────────
CE QUE FAIT CE CAS D'USAGE

Il prend une facture et son rapport de conformité, et rend l'écriture comptable
correspondante, **en brouillon**, déjà porteuse de ses attributs fiscaux.

C'est le moteur de l'écran E10 du dossier de design — « saisie et imputation
comptable, attribut fiscal hérité » — et c'est ce qui referme le parcours
E03 → E02 → E10 des maquettes : la boîte de réception mène au rapport, le rapport
mène à l'écriture.

POURQUOI « PROPOSER » ET NON « ENREGISTRER »

Le comptable garde la main. Le système impute d'après les règles du cabinet, mais
il n'enregistre rien : l'écriture rendue est un brouillon que le comptable relit,
corrige et valide. Un logiciel qui comptabiliserait tout seul déplacerait la
responsabilité vers l'éditeur — or c'est le Centre qui engage son agrément.

LE RÉGIME COMMANDE LA FORME DE L'ÉCRITURE

Une entreprise au régime du réel récupère la TVA : l'écriture compte trois lignes,
dont une en classe 4. Une entreprise au régime synthétique ne la récupère jamais :
la TVA s'incorpore au coût d'achat, et l'écriture n'en compte que deux.

C'est la même facture, le même fournisseur, le même montant — et deux écritures
différentes. Le régime du **destinataire** décide, pas celui de l'émetteur.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.contextes.comptabilite.application.consequences_fiscales import (
    appliquer_rapport,
    verifier_comptabilisation_autorisee,
)
from app.contextes.comptabilite.domaine.entites import (
    EcritureComptable,
    LigneEcriture,
    Sens,
)
from app.contextes.comptabilite.domaine.imputation import PlanImputation, resoudre_compte
from app.contextes.conformite.api import (
    FactureAControler,
    RapportConformite,
    RegimeEmetteur,
)

__all__ = [
    "FactureDUnAutreDossier",
    "ImputationImpossible",
    "proposer_ecriture_achat",
    "rattacher_au_dossier",
]


class FactureDUnAutreDossier(ValueError):
    """La facture désigne un autre destinataire que le dossier où on la propose (pas 73)."""


def rattacher_au_dossier(
    facture: FactureAControler, *, niu: str, regime: RegimeEmetteur
) -> tuple[FactureAControler, list[str]]:
    """La facture, avec le destinataire **que le portefeuille connaît**, et ce qui a changé.

    ─────────────────────────────────────────────────────────────────────────
    ⚠️ POURQUOI CETTE FONCTION EXISTE (pas 73)

    La route de proposition affirmait : « le régime n'est pas demandé : il est lu au
    portefeuille. Le laisser fournir par l'appelant permettrait de récupérer une
    TVA qu'une entreprise au synthétique ne récupère jamais, en cochant une case. »
    **Aucune ligne ne le lisait.** `proposer_ecriture_achat` décidait de la TVA
    récupérable sur `facture.destinataire.regime`, c'est-à-dire sur la requête.

    Essai, avant correction, sur ETS TCHOUMBA & FILS, au régime IGS :

        destinataire déclaré IGS    612 1 431 000 / 401 1 431 000
        destinataire déclaré REEL   612 1 200 000 / 4451 231 000 / 401 1 431 000

    231 000 FCFA de TVA déductible pour une entreprise qui n'en déduit aucune, par un
    mot changé dans la requête. Et une facture adressée à SARL BATIMENT PLUS se
    proposait sans refus dans le dossier d'ETS TCHOUMBA.

    ⚠️ CE QUE FAIT LA FONCTION

    - Un destinataire **nommé par un autre NIU** : refus. La facture n'est pas de
      ce dossier, et la comptabiliser ici ferait porter une charge à une entreprise
      qui ne l'a pas.
    - Un destinataire **sans NIU** : il reçoit celui du dossier. La pièce arrive
      souvent incomplète, et c'est le dossier où on la propose qui la rattache.
    - Le **régime** est toujours celui du portefeuille. S'il diffère de celui que
      la facture déclarait, la rectification est dite : un comptable qui voit la TVA
      disparaître doit savoir pourquoi, sinon il la « corrige » à la main.

    Pure : elle ne lit rien. La route lit le portefeuille et lui donne le régime.
    ─────────────────────────────────────────────────────────────────────────
    """
    destinataire = facture.destinataire
    if destinataire.niu and destinataire.niu != niu:
        raise FactureDUnAutreDossier(
            f"facture {facture.document.reference} adressée à {destinataire.niu}, et non "
            f"au dossier {niu}. La proposer ici ferait porter sa charge à une entreprise "
            "qui ne l'a pas reçue."
        )
    rectifications: list[str] = []
    if not destinataire.niu:
        rectifications.append(
            f"destinataire sans NIU sur la facture : rattaché au dossier {niu}."
        )
    if destinataire.regime is not regime:
        rectifications.append(
            f"régime du destinataire déclaré {destinataire.regime.value}, régime du "
            f"dossier au portefeuille {regime.value} : le portefeuille fait foi, et la "
            "TVA récupérable en découle."
        )
    rattachee = facture.model_copy(
        update={"destinataire": destinataire.model_copy(update={"niu": niu, "regime": regime})}
    )
    return rattachee, rectifications


class ImputationImpossible(ValueError):
    """La facture ne permet pas de construire une écriture équilibrée.

    Ce n'est pas un défaut du logiciel : c'est une facture dont les montants ne
    se recoupent pas. Le contexte D l'a déjà signalé — la règle `FAC-CAL-002`
    relève l'écart entre le détail des lignes et le total hors taxes, et demande
    une facture rectificative.

    L'imputation refuse alors de deviner. Répartir l'écart au prorata, ou le
    passer en écart de conversion, produirait une écriture équilibrée mais fausse,
    et personne ne saurait plus lequel des deux montants faisait foi.
    """


def _lignes_de_charge(
    facture: FactureAControler, plan: PlanImputation
) -> list[LigneEcriture]:
    """Une ligne de charge par ligne de facture, imputée selon les règles.

    Sans détail de lignes, on impute la totalité du hors taxes sur le compte par
    défaut : c'est ce que ferait un comptable devant une facture qui ne détaille
    rien.
    """
    if not facture.lignes:
        return [
            LigneEcriture(
                compte=plan.compte_charge_par_defaut,
                libelle=f"Achat {facture.document.reference}",
                sens=Sens.DEBIT,
                montant=facture.montants.total_ht,
            )
        ]

    somme = sum((ligne.montant_ht for ligne in facture.lignes), Decimal(0))
    if somme != facture.montants.total_ht:
        raise ImputationImpossible(
            f"facture {facture.document.reference} : le détail des lignes totalise "
            f"{somme} alors que le total hors taxes annoncé est "
            f"{facture.montants.total_ht}. Écart de {abs(somme - facture.montants.total_ht)}. "
            "Demander une facture rectificative avant comptabilisation — voir FAC-CAL-002."
        )

    return [
        LigneEcriture(
            compte=resoudre_compte(ligne.designation, plan),
            libelle=ligne.designation,
            sens=Sens.DEBIT,
            montant=ligne.montant_ht,
        )
        for ligne in facture.lignes
    ]


def proposer_ecriture_achat(
    facture: FactureAControler,
    rapport: RapportConformite,
    plan: PlanImputation,
    *,
    journal: str,
    exercice: str,
    numero: int,
    saisie_par: str | None = None,
    date_operation: date | None = None,
) -> EcritureComptable:
    """Rend l'écriture d'achat correspondant à la facture, en brouillon.

    Lève `ComptabilisationInterdite` si la pièce porte une anomalie bloquante.
    La vérification a lieu **avant** toute construction : une écriture fabriquée
    puis jetée aurait consommé un numéro de séquence, donc créé un trou — et un
    trou dans un journal est le premier signal que cherche un contrôleur.
    """
    verifier_comptabilisation_autorisee(rapport)

    montants = facture.montants
    attendu = montants.total_ht + montants.total_tva
    if attendu != montants.total_ttc:
        raise ImputationImpossible(
            f"facture {facture.document.reference} : hors taxes {montants.total_ht} "
            f"plus TVA {montants.total_tva} donnent {attendu}, alors que le total toutes "
            f"taxes annoncé est {montants.total_ttc}. Aucune écriture équilibrée ne peut "
            "en être tirée."
        )

    lignes = _lignes_de_charge(facture, plan)

    # Le régime du destinataire — l'adhérent — décide, jamais celui du fournisseur.
    assujetti = facture.destinataire.regime is RegimeEmetteur.REEL

    if montants.total_tva > 0:
        if assujetti:
            lignes.append(
                LigneEcriture(
                    compte=plan.compte_tva_deductible,
                    libelle="TVA récupérable sur achats",
                    sens=Sens.DEBIT,
                    montant=montants.total_tva,
                )
            )
        elif plan.compte_tva_non_recuperable is not None:
            # La TVA est un coût, porté à part pour rester traçable.
            lignes.append(
                LigneEcriture(
                    compte=plan.compte_tva_non_recuperable,
                    libelle="TVA non récupérable",
                    sens=Sens.DEBIT,
                    montant=montants.total_tva,
                )
            )
        else:
            # Incorporée au coût d'achat : deux lignes au lieu de trois, et la TVA
            # disparaît de la classe 4. C'est la forme la plus répandue, et c'est
            # ce que le point de variation de `PlanImputation` laisse choisir.
            premiere = lignes[0]
            lignes[0] = premiere.model_copy(
                update={"montant": premiere.montant + montants.total_tva}
            )

    lignes.append(
        LigneEcriture(
            compte=plan.compte_fournisseur,
            libelle=facture.emetteur.denomination or "Fournisseur non identifié",
            sens=Sens.CREDIT,
            montant=montants.total_ttc,
            tiers=facture.emetteur.niu,
        )
    )

    ecriture = EcritureComptable(
        journal=journal,
        exercice=exercice,
        numero=numero,
        date_operation=date_operation or facture.document.date_emission,
        libelle=(
            f"{facture.emetteur.denomination or 'Fournisseur'} — "
            f"{facture.document.reference}"
        ),
        piece_justificative=facture.document.reference,
        reference_externe=facture.document.reference,
        lignes=lignes,
        saisie_par=saisie_par,
    )

    # Les constats deviennent des attributs portés par les lignes concernées.
    return appliquer_rapport(ecriture, rapport, prefixe_tva=plan.compte_tva_deductible)
