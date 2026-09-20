"""Les accusés de dépôt du jeu de démonstration.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

L'amorçage versait des comptes, des dossiers, des pièces et des écritures, mais
**aucun accusé de dépôt**. Conséquence, mesurée depuis l'espace adhérent :
`GET /obligations/dossiers/{niu}/mes-documents` rendait une liste vide sur un
dossier adhérent depuis juin 2022. Un cabinet qui dépose pour un adhérent depuis
trois ans a forcément des accusés ; leur absence était un trou du jeu d'essai,
pas une propriété du produit.

⚠️ LES ACCUSÉS PASSENT PAR LE DOMAINE, ET NON PAR LA TABLE

`enregistrer_accuse` vérifie que l'accusé porte bien sur le document préparé —
même référence, même empreinte, même guichet — et refuse un second dépôt de la
même période. Écrire directement dans la table contournerait ces trois gardes et
laisserait entrer un jeu d'essai que le produit lui-même refuserait. On construit
donc le document, puis l'accusé qui lui correspond.

⚠️ UNE HISTOIRE COHÉRENTE, ET NON SIX LIGNES AU HASARD

Le dossier était à jour en 2024 — cotisations, TVA et retenues déposées dans les
délais — puis a décroché en 2025, ce que l'échéancier montre déjà par ses cinq
cent quatre-vingts jours de retard. Les accusés racontent la première moitié de
cette histoire, sans laquelle l'adhérent ne voit que ses manquements.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app.contextes.transverse.domaine.teledeclaration import (
    AccuseReception,
    DocumentATransmettre,
    FormatTransmission,
    ModeDepot,
    Portail,
)

__all__ = ["ACCUSES_DEMO"]

#: Le NIU du dossier de démonstration qui porte l'historique.
_BATIMENT = "M081234567890P"

#: Le réviseur qui a consigné les dépôts. ⚠️ Un accusé sans auteur ne prouve
#: rien : c'est ce nom qu'on cite quand l'administration conteste une date.
_REVISEUR = "C-003"

#: La CNPS a son propre guichet. Un accusé CNPS ne prouve rien devant la DGI, et
#: le domaine refuse de les confondre.
_GUICHET = {
    "CNPS": Portail.CNPS_DIPE,
}


def _depot(
    code: str,
    debut: date,
    fin: date,
    numero: str,
    depose_le: datetime,
    montant: str | None,
    precision: str,
) -> tuple[DocumentATransmettre, AccuseReception]:
    """Un document et l'accusé qui lui correspond, à l'octet près."""
    portail = _GUICHET.get(code, Portail.DGI_TELEDECLARATION)
    document = DocumentATransmettre(
        portail=portail,
        code_document=code,
        entreprise=_BATIMENT,
        periode_debut=debut,
        periode_fin=fin,
        format=FormatTransmission.SAISIE_MANUELLE,
        # ⚠️ Le contenu n'est pas la déclaration : c'est ce qui a été REMIS au
        # guichet, et pour un dépôt manuel c'est un constat. L'empreinte porte
        # sur ce constat, et l'en-tête du domaine le dit explicitement.
        contenu=f"{code} {debut:%Y-%m-%d}/{fin:%Y-%m-%d} déposé au guichet",
    )
    accuse = AccuseReception(
        numero=numero,
        portail=portail,
        reference_document=document.reference,
        depose_le=depose_le,
        mode=ModeDepot.MANUEL,
        empreinte_deposee=document.empreinte,
        depose_par=_REVISEUR,
        montant_constate=Decimal(montant) if montant is not None else None,
        precision=precision,
    )
    return document, accuse


#: Six dépôts de 2024, dans l'ordre où ils ont eu lieu.
ACCUSES_DEMO: list[tuple[DocumentATransmettre, AccuseReception]] = [
    _depot(
        "CNPS", date(2024, 1, 1), date(2024, 1, 31),
        "CNPS-2024-01-004871", datetime(2024, 2, 14, 10, 30),
        "412500", "Déposé au guichet CNPS Bonabéri",
    ),
    _depot(
        "TVA", date(2024, 1, 1), date(2024, 1, 31),
        "DGI-2024-01-118402", datetime(2024, 2, 15, 9, 5),
        "1275000", "Télédéclaration DGI, accusé imprimé",
    ),
    _depot(
        "IRPP_SALAIRES", date(2024, 1, 1), date(2024, 1, 31),
        "DGI-2024-01-118403", datetime(2024, 2, 15, 9, 12),
        "318750", "Télédéclaration DGI, même session que la TVA",
    ),
    _depot(
        "CNPS", date(2024, 2, 1), date(2024, 2, 29),
        "CNPS-2024-02-005130", datetime(2024, 3, 14, 11, 0),
        "412500", "Déposé au guichet CNPS Bonabéri",
    ),
    _depot(
        "TVA", date(2024, 2, 1), date(2024, 2, 29),
        "DGI-2024-02-121877", datetime(2024, 3, 15, 8, 48),
        "1340000", "Télédéclaration DGI",
    ),
    _depot(
        # La déclaration annuelle de l'exercice 2023, déposée dans les délais.
        #
        # ⚠️ AUCUN MONTANT, et c'est le point. Constaté sur l'appareil : avec
        # zéro, l'écran affichait « 0 F » sous « Déclaration annuelle », ce qui
        # laisse croire que rien n'était dû. Une DSF n'est pas un paiement :
        # c'est une déclaration, et son accusé ne porte pas de montant.
        "DSF", date(2023, 1, 1), date(2023, 12, 31),
        "DGI-2024-DSF-009214", datetime(2024, 3, 14, 16, 20),
        None, "Liasse fiscale complète, déposée au centre divisionnaire",
    ),
]
