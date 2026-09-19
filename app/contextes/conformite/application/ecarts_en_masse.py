"""Écarter des constats en masse (pas 103).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI UN GESTE À PART, ET NON UNE BOUCLE DANS L'ÉCRAN

Quatre fournisseurs sans NIU sur la facture, dont les attestations ont été reçues et
vérifiées au fichier DGI : c'est **une** décision, prise pour une **même** raison, sur
quatre constats. La prendre pièce par pièce produirait quatre motifs recopiés, et le
réviseur cesserait de les écrire vraiment.

⚠️ TOUT OU RIEN

Si un seul des constats choisis ne peut pas être écarté (politique, écart déjà ouvert,
constat disparu), **aucun** ne l'est, et le message nomme chaque pièce refusée. Écarter trois
constats sur quatre en silence laisserait croire au réviseur que sa décision est appliquée,
alors qu'une pièce continue de bloquer.

Pour tenir ce « tout ou rien » sans transaction (le dépôt en mémoire n'en a pas), chaque écart
est d'abord proposé contre un **tampon** qui lit le dépôt réel et garde les écritures pour
lui. Ce n'est qu'une fois tous acceptés que le tampon est versé au dépôt.

CE QUI NE CHANGE PAS

Chaque écart reste un écart ordinaire : même politique (sévérités, règles non écartables,
second regard), même motif minimum, même journal d'audit, même levée depuis la pièce.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.contextes.conformite.application.ecarts import appliquer_les_ecarts, proposer_un_ecart
from app.contextes.conformite.domaine.ecarts import (
    EcartDeConstat,
    EcartRefuse,
    PolitiqueDEcart,
    StatutEcart,
    verifier_le_motif,
)
from app.contextes.conformite.domaine.entites import RapportConformite
from app.contextes.conformite.domaine.ports import DepotEcarts

__all__ = ["ConsequencesDesEcarts", "consequences_des_ecarts", "ecarter_en_masse"]


class _Tampon:
    """Lit le dépôt réel, garde ses propres écritures jusqu'au versement.

    Il ne relit pas ses propres écritures : une même pièce ne peut pas être choisie deux fois
    (refusé avant), donc aucun écart du lot ne peut en gêner un autre.
    """

    def __init__(self, depot: DepotEcarts) -> None:
        self._depot = depot
        self.ecrits: list[EcartDeConstat] = []

    def pour_la_piece(self, dossier: str, reference_document: str) -> list[EcartDeConstat]:
        return self._depot.pour_la_piece(dossier, reference_document)

    def enregistrer(self, ecart: EcartDeConstat) -> None:
        self.ecrits.append(ecart)

    def verser(self) -> None:
        for ecart in self.ecrits:
            self._depot.enregistrer(ecart)


def ecarter_en_masse(
    pieces: list[tuple[str, RapportConformite]],
    *,
    code_regle: str,
    motif: str,
    motif_type: str | None,
    par: str,
    le: datetime,
    politique: PolitiqueDEcart,
    depot: DepotEcarts,
) -> list[EcartDeConstat]:
    """`pieces` : pour chaque pièce choisie, son dossier et son rapport **recalculé**."""
    if not pieces:
        raise EcartRefuse("aucune pièce choisie : il n'y a rien à écarter.")
    references = [(dossier, rapport.reference_document) for dossier, rapport in pieces]
    if len(references) != len(set(references)):
        raise EcartRefuse("une même pièce est choisie deux fois.")
    # Le motif vaut pour toutes les pièces : vérifié une fois, avant la boucle, pour que le
    # refus dise « motif trop court » et non « 4 refus sur 4 ».
    verifier_le_motif(motif, politique.motif_minimum)
    if motif_type is not None and motif_type not in {
        m.code for m in politique.motifs_pour(code_regle)
    }:
        proposes = ", ".join(m.code for m in politique.motifs_pour(code_regle)) or "aucun"
        raise EcartRefuse(
            f"le motif type « {motif_type} » n'est pas proposé pour la règle {code_regle} "
            f"(proposés : {proposes})."
        )
    tampon = _Tampon(depot)
    refus = []
    for dossier, rapport in pieces:
        try:
            proposer_un_ecart(
                rapport,
                dossier=dossier,
                code_regle=code_regle,
                motif=motif,
                motif_type=motif_type,
                par=par,
                le=le,
                politique=politique,
                depot=tampon,  # type: ignore[arg-type]
            )
        except EcartRefuse as erreur:
            refus.append(f"{rapport.reference_document} : {erreur}")
    if refus:
        raise EcartRefuse(
            f"aucun constat n'a été écarté ({len(refus)} refus sur {len(pieces)}). "
            + " | ".join(refus)
        )
    tampon.verser()
    return tampon.ecrits


class ConsequencesDesEcarts(BaseModel):
    """Ce que la décision produit, annoncé à l'écran et rendu après coup."""

    model_config = ConfigDict(frozen=True)

    ecarts: int
    effectifs: int
    en_attente: int
    #: L'enjeu des seuls écarts effectifs : un écart en attente ne lève encore rien.
    enjeu_leve: Decimal
    dossiers: list[str]
    #: Les pièces que l'écart rend comptabilisables (bloquées avant, plus après).
    pieces_comptabilisables: list[str]


def consequences_des_ecarts(
    pieces: list[tuple[str, RapportConformite]],
    ecarts: list[EcartDeConstat],
    politique: PolitiqueDEcart,
    depot: DepotEcarts,
) -> ConsequencesDesEcarts:
    effectifs = [e for e in ecarts if e.statut is StatutEcart.EFFECTIF]
    comptabilisables = []
    for dossier, brut in pieces:
        if not brut.comptabilisation_interdite:
            continue
        arbitre = appliquer_les_ecarts(
            brut, depot.pour_la_piece(dossier, brut.reference_document), politique
        )
        if not arbitre.comptabilisation_interdite:
            comptabilisables.append(brut.reference_document)
    return ConsequencesDesEcarts(
        ecarts=len(ecarts),
        effectifs=len(effectifs),
        en_attente=sum(1 for e in ecarts if e.statut is StatutEcart.EN_ATTENTE),
        enjeu_leve=sum((e.enjeu or Decimal(0) for e in effectifs), Decimal(0)),
        dossiers=sorted({e.dossier for e in ecarts}),
        pieces_comptabilisables=sorted(comptabilisables),
    )
