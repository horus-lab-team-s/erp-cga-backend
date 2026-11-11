"""L'exercice 2025 de CABINET NGUEMA CONSEIL, repris du logiciel précédent du client.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE JEU, ET POURQUOI À PART (14 octobre 2026)

La DSF se prépare sur un exercice clos. Or tous les exercices clos du jeu d'essai
étaient vides : une DSF de démonstration n'aurait porté que des zéros. Ce dossier
est celui que la démonstration laisse volontairement en retard
(`demonstration_a_jour.DOSSIER_EN_RETARD_VOULU`) : sa DSF 2025 reste à faire, et
c'est elle que le cabinet régularise à l'écran.

⚠️ CE N'EST PAS UNE COMPTABILITÉ DÉRIVÉE DES PIÈCES, à la différence de
`donnees_demo.py`. Ce sont les écritures d'un exercice REPRIS du logiciel
précédent du client, comme le permet la reprise (`application/reprise.py`) : un
cabinet qui accueille un adhérent reprend ses livres, il ne les ressaisit pas
pièce par pièce. Elles sont donc à part, et le disent.

Un cabinet de conseil au régime de l'IGS : des honoraires, un loyer, du matériel
informatique et son amortissement, l'IGS versé par trimestre. Les chiffres
s'additionnent : résultat 27 520 000, bilan 32 520 000 de part et d'autre.

⚠️ L'À-NOUVEAU 2026 EST CELUI QUE LA CLÔTURE AURAIT POSÉ. La comptabilité ne lit pas
la clôture (l'arête va dans l'autre sens) : il est donc écrit ici, et
`test_dsf.py` vérifie qu'il est identique, ligne à ligne, à ce que rend
`lignes_d_a_nouveau` sur la balance 2025.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app.contextes.comptabilite.domaine.entites import (
    EcritureComptable,
    EtatEcriture,
    LigneEcriture,
    Sens,
)

__all__ = ["DOSSIER_REPRIS", "ecritures_reprises"]

#: CABINET NGUEMA CONSEIL.
DOSSIER_REPRIS = "P027788990011M"

_REPRISE_VALIDEE_PAR = "Rodrigue BIYA'A"
_REPRISE_VALIDEE_LE = datetime(2026, 1, 20, 10, 0)
D, C = Sens.DEBIT, Sens.CREDIT


def _ecriture(exercice, journal, numero, jour, libelle, lignes, piece=None) -> EcritureComptable:
    return EcritureComptable(
        journal=journal,
        exercice=exercice,
        numero=numero,
        date_operation=jour,
        libelle=libelle,
        piece_justificative=piece or f"REPRISE-{exercice}-{journal}-{numero}",
        reference_externe=f"LOGICIEL-PRECEDENT-{journal}{numero:04d}",
        lignes=[
            LigneEcriture(compte=compte, libelle=lib, sens=sens, montant=Decimal(montant))
            for compte, sens, montant, lib in lignes
        ],
        etat=EtatEcriture.VALIDEE,
        validee_par=_REPRISE_VALIDEE_PAR,
        validee_le=_REPRISE_VALIDEE_LE,
    )


def _exercice_2025() -> list[EcritureComptable]:
    e = []
    od = bq = ve = ac = 0

    def OD(*a, **k):
        nonlocal od
        od += 1
        e.append(_ecriture("2025", "OD", od, *a, **k))

    def BQ(*a, **k):
        nonlocal bq
        bq += 1
        e.append(_ecriture("2025", "BQ", bq, *a, **k))

    def VE(*a, **k):
        nonlocal ve
        ve += 1
        e.append(_ecriture("2025", "VE", ve, *a, **k))

    def AC(*a, **k):
        nonlocal ac
        ac += 1
        e.append(_ecriture("2025", "AC", ac, *a, **k))

    BQ(
        date(2025, 1, 2),
        "Apport de l'exploitant",
        [("521", D, 5000000, "Apport"), ("101", C, 5000000, "Apport de l'exploitant")],
    )
    AC(
        date(2025, 2, 10),
        "Matériel informatique",
        [("24", D, 2400000, "Ordinateurs et serveur"), ("401", C, 2400000, "INFOTECH DOUALA")],
    )
    BQ(
        date(2025, 2, 28),
        "Règlement INFOTECH DOUALA",
        [("401", D, 2400000, "INFOTECH DOUALA"), ("521", C, 2400000, "Virement")],
    )
    for trimestre, (montant, encaisse) in enumerate(
        [(9500000, 9500000), (10200000, 10200000), (8800000, 8800000), (10000000, 6500000)],
        start=1,
    ):
        fin = date(2025, 3 * trimestre, 28)
        VE(
            fin,
            f"Honoraires du trimestre {trimestre}",
            [("411", D, montant, "Clients"), ("706", C, montant, "Honoraires de conseil")],
        )
        BQ(
            fin,
            f"Encaissements du trimestre {trimestre}",
            [("521", D, encaisse, "Encaissements"), ("411", C, encaisse, "Clients")],
        )
        BQ(
            fin,
            f"Loyer du trimestre {trimestre}",
            [("622", D, 750000, "Loyer du bureau, Akwa"), ("521", C, 750000, "Loyer")],
        )
        BQ(
            fin,
            f"Téléphone et internet, trimestre {trimestre}",
            [("628", D, 300000, "Téléphone et internet"), ("521", C, 300000, "Opérateur")],
        )
        BQ(
            fin,
            f"IGS, versement du trimestre {trimestre}",
            [("641", D, 300000, "Impôt général synthétique"), ("521", C, 300000, "DGI")],
        )
    AC(
        date(2025, 6, 30),
        "Sous-traitance d'une mission d'audit",
        [("632", D, 4000000, "Consultant associé"), ("401", C, 4000000, "M. ATANGANA")],
    )
    BQ(
        date(2025, 7, 15),
        "Règlement M. ATANGANA",
        [("401", D, 4000000, "M. ATANGANA"), ("521", C, 4000000, "Virement")],
    )
    BQ(
        date(2025, 9, 30),
        "Fournitures et électricité",
        [("605", D, 600000, "Électricité et fournitures"), ("521", C, 600000, "ENEO, papeterie")],
    )
    BQ(
        date(2025, 12, 31),
        "Frais bancaires de l'année",
        [("631", D, 180000, "Agios et commissions"), ("521", C, 180000, "Banque")],
    )
    OD(
        date(2025, 12, 31),
        "Dotation aux amortissements",
        [
            ("681", D, 800000, "Matériel informatique, 1/3"),
            ("28", C, 800000, "Amortissement du matériel"),
        ],
    )
    return e


#: L'à-nouveau 2026, tel que `lignes_d_a_nouveau` le pose sur la balance 2025.
_A_NOUVEAU_2026 = [
    ("101", C, 5000000),
    ("24", D, 2400000),
    ("28", C, 800000),
    ("411", D, 3500000),
    ("521", D, 27420000),
    ("13", C, 27520000),
]


def _a_nouveau_2026() -> EcritureComptable:
    return _ecriture(
        "2026",
        "AN",
        1,
        date(2026, 1, 1),
        "À-nouveaux de l'exercice 2025",
        [
            (
                compte,
                sens,
                montant,
                "Résultat de l'exercice précédent" if compte == "13" else "À-nouveau",
            )
            for compte, sens, montant in _A_NOUVEAU_2026
        ],
        piece="BALANCE-2025",
    )


def ecritures_reprises(entreprise: str) -> list[EcritureComptable]:
    """L'exercice repris, et son à-nouveau ; rien pour les autres dossiers."""
    if entreprise != DOSSIER_REPRIS:
        return []
    return [*_exercice_2025(), _a_nouveau_2026()]
