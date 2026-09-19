"""Les écritures de démonstration, dérivées des pièces reçues.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE JEU DÉRIVE DE LA COLLECTE, ET NON L'INVERSE

Une écriture n'existe pas toute seule : elle vient d'une pièce, contrôlée, imputée,
puis validée. Construire ici un jeu d'écritures indépendant produirait une
comptabilité que rien ne justifie — exactement ce que la plateforme reproche aux
logiciels qu'elle remplace.

Ce module part donc des pièces du contexte C marquées « comptabilisée », retrouve
la facture correspondante chez D, la fait contrôler par le **vrai moteur**, et
demande à `proposer_ecriture_achat` l'écriture qui en découle. L'arête
`comptabilite → collecte` existe pour cela, et elle va dans le bon sens : la
collecte n'a pas le droit de connaître la comptabilité.

Conséquence heureuse : il n'y a aucune convention de numérotation à maintenir en
double. L'écriture porte la clé que la pièce annonce, parce qu'elle est construite
à partir d'elle.

CE QUE LE JEU MONTRE

Le régime du destinataire commande la forme de l'écriture. ETS TCHOUMBA & FILS et
CABINET NGUEMA CONSEIL relèvent du synthétique : leurs achats donnent des écritures
à **deux lignes**, la TVA s'incorporant au coût. Les quatre autres adhérents sont au
réel : trois lignes, dont une de TVA récupérable. Même facture, même fournisseur,
deux écritures différentes.

⚠️ Le plan d'imputation est le même pour tous les dossiers de démonstration. En
production il est propre à chaque adhérent — une entreprise de BTP et une clinique
n'imputent pas les mêmes achats sur les mêmes comptes.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache

from app.contextes.collecte.api import PIECES_DEMO, EtatPiece, PieceJustificative
from app.contextes.comptabilite.application.consequences_fiscales import (
    appliquer_rapport,
)
from app.contextes.comptabilite.application.proposition_ecriture import (
    ImputationImpossible,
    proposer_ecriture_achat,
)
from app.contextes.comptabilite.domaine.entites import EcritureComptable
from app.contextes.comptabilite.domaine.imputation import PlanImputation, RegleImputation
from app.contextes.conformite.api import FACTURES_DEMO

__all__ = ["PLAN_IMPUTATION_DEMO", "DepotPlanImputationMemoire", "ecritures_demo"]

#: Le comptable qui a validé, et quand. Une validation engage une personne, pas un
#: logiciel — c'est la règle du contexte E, et le jeu de démonstration s'y plie.
_VALIDEE_PAR = "Rodrigue BIYA'A"
_VALIDEE_LE = datetime(2026, 8, 3, 9, 30)


PLAN_IMPUTATION_DEMO = PlanImputation(
    compte_charge_par_defaut="604",
    regles=[
        RegleImputation(
            motif=r"(?i)ciment|fer à béton|fers à béton|sable|gravier|parpaing",
            compte="604",
            libelle="Matériaux de construction",
            priorite=90,
        ),
        RegleImputation(
            motif=r"(?i)farine|semence|engrais|maïs|blé",
            compte="602",
            libelle="Matières premières",
            priorite=90,
        ),
        RegleImputation(
            motif=r"(?i)marchandise|grossiste",
            compte="601",
            libelle="Achats de marchandises",
            priorite=80,
        ),
        RegleImputation(
            motif=r"(?i)transport",
            compte="612",
            libelle="Transports sur achats",
            priorite=85,
        ),
        RegleImputation(
            motif=r"(?i)location|bail",
            compte="622",
            libelle="Locations et charges locatives",
            priorite=85,
        ),
        RegleImputation(
            motif=r"(?i)électricité|eau|carburant|gasoil",
            compte="605",
            libelle="Eau, électricité, carburants",
            priorite=85,
        ),
        RegleImputation(
            motif=r"(?i)fibre|abonnement|télécom|téléphone",
            compte="628",
            libelle="Frais de télécommunications",
            priorite=85,
        ),
        RegleImputation(
            motif=r"(?i)ramette|cartouche|fourniture de bureau|papeterie",
            compte="605",
            libelle="Fournitures de bureau",
            priorite=70,
        ),
        RegleImputation(
            motif=r"(?i)gardiennage|blanchissage|nettoyage",
            compte="638",
            libelle="Autres charges externes",
            priorite=70,
        ),
        RegleImputation(
            motif=r"(?i)consommable médical|réactif|pharma",
            compte="602",
            libelle="Consommables médicaux",
            priorite=85,
        ),
    ],
)


class DepotPlanImputationMemoire:
    """Réalisation du port `DepotPlanImputation`.

    Le port exige de lever plutôt que de rendre un plan vide : imputer tout un
    flux entrant sur un compte par défaut inventé produirait une comptabilité
    qu'il faudrait entièrement reprendre. Ce dépôt de démonstration sert le même
    plan à tous les dossiers, et le dit.
    """

    def __init__(self, plans: dict[str, PlanImputation] | None = None) -> None:
        self._plans = plans or {}

    def charger(self, entreprise: str) -> PlanImputation:
        return self._plans.get(entreprise, PLAN_IMPUTATION_DEMO)


@lru_cache
def _moteur():
    from app.contextes.conformite.api import DepotReglesYaml, MoteurConformite
    from app.contextes.referentiel.api import DepotParametresYaml, ServiceParametres
    from app.infrastructure.config import configuration

    referentiel = configuration().dossier_referentiel
    return MoteurConformite(
        regles=DepotReglesYaml(referentiel / "regles").charger(),
        parametres=ServiceParametres.depuis_depot(
            DepotParametresYaml(referentiel / "parametres.yaml")
        ),
    )


def _numero(cle_ecriture: str) -> int:
    """« 2026/AC/000007 » → 7."""
    return int(cle_ecriture.rsplit("/", 1)[1])


@lru_cache
def _toutes() -> tuple[EcritureComptable, ...]:
    comptabilisees: list[PieceJustificative] = sorted(
        (
            piece
            for piece in PIECES_DEMO.values()
            if piece.etat is EtatPiece.COMPTABILISEE and piece.reference_ecriture
        ),
        key=lambda piece: (piece.entreprise, piece.reference_ecriture or ""),
    )

    moteur = _moteur()
    ecritures: list[EcritureComptable] = []
    for piece in comptabilisees:
        facture = FACTURES_DEMO[piece.reference_document or ""]
        rapport = moteur.controler(facture)
        try:
            brouillon = proposer_ecriture_achat(
                facture,
                rapport,
                PLAN_IMPUTATION_DEMO,
                journal="AC",
                exercice="2026",
                numero=_numero(piece.reference_ecriture or ""),
                saisie_par=_VALIDEE_PAR,
            )
        except ImputationImpossible:
            # Une facture dont les montants ne se recoupent pas n'a pas d'écriture,
            # et c'est le comportement voulu : l'imputation refuse de deviner.
            continue
        # ⚠️ `appliquer_rapport` était manquant, et le manque ne se voyait nulle
        # part : les écritures s'enregistraient, le grand livre s'affichait, la
        # balance tenait. Seule la clôture l'a révélé — son tableau de passage
        # restait vide parce qu'aucune ligne ne portait d'attribut fiscal, et la
        # promesse « une conséquence chiffrée qui se propage jusqu'à la liasse »
        # n'était démontrable sur aucun écran.
        #
        # C'est le maillon 3 de la chaîne de traçabilité du § 04 : sans lui, on
        # ne remonte pas d'une réintégration jusqu'à la photo de la facture.
        avec_consequences = appliquer_rapport(brouillon, rapport)
        ecritures.append(
            avec_consequences.model_copy(
                update={"piece_justificative": piece.identifiant}
            ).valider(_VALIDEE_PAR, _VALIDEE_LE)
        )
    return tuple(ecritures)


def ecritures_demo(entreprise: str) -> list[EcritureComptable]:
    """Les écritures d'achat d'un dossier, construites depuis ses pièces.

    Le filtre passe par les pièces : `EcritureComptable` ne porte pas
    d'identifiant d'entreprise, et c'est la pièce qui sait à quel dossier elle
    appartient — voir l'en-tête de `depot_ecritures_memoire.py`.
    """
    du_dossier = {
        piece.identifiant
        for piece in PIECES_DEMO.values()
        if piece.entreprise == entreprise
    }
    return [e for e in _toutes() if e.piece_justificative in du_dossier]
