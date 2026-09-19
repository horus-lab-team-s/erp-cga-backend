"""Ce que l'adhérent voit de ses pièces, et comment il répond au cabinet (pas 112).

─────────────────────────────────────────────────────────────────────────────────
D'OÙ VIENT CE MODULE

La maquette « Espace adhérent CGA », vue C (« Mes justificatifs »), confrontée à l'espace
existant (pas 81) :

    maquette                                        avant le pas 112
    ─────────────────────────────────────────────── ─────────────────────────────────────
    la liste d'un mois, filtrée par statut,         les vingt dernières pièces, sans mois,
    cherchée par fournisseur                        sans filtre ni recherche
    « Reçu par le cabinet », « Enregistré par le    l'état interne du traitement : « Lue »,
    cabinet », « À corriger »                       « Rapprochée », « Comptabilisée »
    répondre à une demande : deux réponses toutes   rien : l'adhérent lisait la demande
    faites et un message                            et appelait le cabinet

LE STATUT DE L'ADHÉRENT N'EST PAS L'ÉTAT DU TRAITEMENT

L'état d'une pièce (`EtatPiece`) décrit le travail du cabinet en cinq étapes. L'adhérent n'en
fait rien : « Rapprochée » ne lui dit pas s'il a quelque chose à faire. Il lit quatre statuts,
et un seul lui demande un geste :

    RECU         le cabinet l'a, il la traite               rien à faire
    ENREGISTRE   elle est dans la comptabilité              rien à faire
    CLASSE       gardée au dossier sans écriture            rien à faire
    A_CORRIGER   une facture rectificative est demandée     demander au fournisseur

⚠️ « À corriger » **l'emporte sur l'état** : une facture comptabilisée puis contestée par une
demande de rectificative ouverte demande un geste, et le dire « enregistrée » le cacherait.

⚠️ Une pièce n'a pas de « corbeille » : classée, elle reste dans la liste. « Une pièce reste
toujours consultable, même comptabilisée » (note 4 de la vue C).
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contextes.collecte.domaine.demandes import (
    DemandePiece,
    NatureDeReponse,
    ReponseDeLAdherent,
)
from app.contextes.collecte.domaine.pieces import CanalDepot, EtatPiece, PieceJustificative

__all__ = [
    "LIBELLES_PAR_DEFAUT",
    "LigneDeJustificatif",
    "ReglagesDesReponses",
    "ReponsePossible",
    "StatutPourLAdherent",
    "VueDesJustificatifs",
    "mois_de_la_piece",
    "statut_pour_l_adherent",
    "texte_de_la_reponse",
    "vue_des_justificatifs",
]


class StatutPourLAdherent(StrEnum):
    RECU = "RECU"
    ENREGISTRE = "ENREGISTRE"
    CLASSE = "CLASSE"
    A_CORRIGER = "A_CORRIGER"


#: L'ordre d'affichage des comptes : ce qui demande un geste d'abord.
ORDRE_DES_STATUTS = (
    StatutPourLAdherent.A_CORRIGER,
    StatutPourLAdherent.RECU,
    StatutPourLAdherent.ENREGISTRE,
    StatutPourLAdherent.CLASSE,
)


def statut_pour_l_adherent(
    piece: PieceJustificative, a_corriger: set[str]
) -> StatutPourLAdherent:
    """Le statut lu par l'adhérent. `a_corriger` : les pièces visées par une rectificative ouverte."""
    if piece.identifiant in a_corriger:
        return StatutPourLAdherent.A_CORRIGER
    if piece.etat is EtatPiece.ARCHIVEE:
        return StatutPourLAdherent.CLASSE
    if piece.etat is EtatPiece.COMPTABILISEE:
        return StatutPourLAdherent.ENREGISTRE
    return StatutPourLAdherent.RECU


def mois_de_la_piece(piece: PieceJustificative) -> str:
    """Le mois d'une pièce, `AAAA-MM` : celui du document, sinon celui de sa réception.

    ⚠️ La date du document d'abord : une facture de juillet envoyée le 3 août est une pièce de
    **juillet**, c'est la déclaration de juillet qui l'attend. Tant que le cabinet n'a pas lu la
    date, la réception est la seule date connue, et la pièce changera de mois une fois lue.
    """
    jour = piece.date_document or piece.recue_le.date()
    return f"{jour:%Y-%m}"


class LigneDeJustificatif(BaseModel):
    model_config = ConfigDict(frozen=True)

    identifiant: str
    #: Le fournisseur, s'il est lu ; sinon le nom du fichier envoyé, que l'adhérent reconnaît.
    fournisseur: str | None
    nom_fichier: str | None
    reference: str | None
    date_document: date | None
    montant_ttc: Decimal | None
    statut: StatutPourLAdherent
    #: Pour « à corriger » : ce qui est à corriger, tel que le cabinet l'a écrit, et la demande.
    a_corriger: str | None = None
    demande: str | None = None
    envoye_le: date
    canal: CanalDepot
    #: Le type de pièce (`FACTURE_ACHAT`, `RELEVE_BANCAIRE`…), que l'écran traduit.
    type: str


class VueDesJustificatifs(BaseModel):
    model_config = ConfigDict(frozen=True)

    mois: str
    #: Les mois qui ont au moins une pièce, du plus récent au plus ancien : le sélecteur.
    mois_disponibles: list[str]
    #: Pour l'état vide : le mois le plus récent avant celui-ci qui porte des pièces.
    mois_precedent_avec_pieces: str | None
    #: Le compte par statut **du mois**, avant filtre de statut et recherche : la ligne
    #: « 19 justificatifs envoyés · 1 à corriger » ne change pas quand on filtre.
    par_statut: dict[StatutPourLAdherent, int]
    total_du_mois: int
    lignes: list[LigneDeJustificatif]


def vue_des_justificatifs(
    *,
    pieces: list[PieceJustificative],
    demandes_ouvertes: list[DemandePiece],
    mois: str,
    statut: StatutPourLAdherent | None = None,
    recherche: str | None = None,
) -> VueDesJustificatifs:
    rectificatives = {
        d.piece_a_rectifier: d for d in demandes_ouvertes if d.piece_a_rectifier is not None
    }
    a_corriger = set(rectificatives)
    disponibles = sorted({mois_de_la_piece(p) for p in pieces}, reverse=True)
    du_mois = [p for p in pieces if mois_de_la_piece(p) == mois]
    lignes = []
    for piece in du_mois:
        statut_lu = statut_pour_l_adherent(piece, a_corriger)
        demande = rectificatives.get(piece.identifiant)
        lignes.append(
            LigneDeJustificatif(
                identifiant=piece.identifiant,
                fournisseur=piece.emetteur,
                nom_fichier=piece.nom_fichier,
                reference=piece.reference_document,
                date_document=piece.date_document,
                montant_ttc=piece.montant_ttc,
                statut=statut_lu,
                a_corriger=demande.motif if demande else None,
                demande=demande.identifiant if demande else None,
                envoye_le=piece.depose_le,
                canal=piece.canal,
                type=piece.type.value,
            )
        )
    par_statut = {s: sum(1 for l_ in lignes if l_.statut is s) for s in ORDRE_DES_STATUTS}
    retenues = [l_ for l_ in lignes if statut is None or l_.statut is statut]
    if recherche and recherche.strip():
        cherche = recherche.strip().casefold()
        retenues = [
            l_
            for l_ in retenues
            if any(
                cherche in (texte or "").casefold()
                for texte in (l_.fournisseur, l_.reference, l_.nom_fichier)
            )
        ]
    # Ce qui demande un geste d'abord, puis le plus récent.
    rang = {s: i for i, s in enumerate(ORDRE_DES_STATUTS)}
    retenues.sort(
        key=lambda l_: (rang[l_.statut], -(l_.date_document or l_.envoye_le).toordinal())
    )
    return VueDesJustificatifs(
        mois=mois,
        mois_disponibles=disponibles,
        mois_precedent_avec_pieces=next((m for m in disponibles if m < mois), None),
        par_statut=par_statut,
        total_du_mois=len(lignes),
        lignes=retenues,
    )


# ── Les réponses au cabinet ───────────────────────────────────────────────────


#: Les mots de la maquette. Le référentiel peut les changer ; il ne peut pas en retirer un.
LIBELLES_PAR_DEFAUT: dict[NatureDeReponse, str] = {
    NatureDeReponse.PLUS_TARD: "Je l'aurai la semaine prochaine",
    NatureDeReponse.INTROUVABLE: "Je n'ai pas ce document",
}


class ReponsePossible(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    nature: NatureDeReponse
    libelle: str = Field(min_length=3, max_length=80)


class ReglagesDesReponses(BaseModel):
    """`Docs/referentiel/collecte/reponses_adherent.yaml`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Ce que « la semaine prochaine » veut dire, en jours après la réponse.
    jours_si_plus_tard: int = Field(default=7, ge=1, le=60)
    reponses: tuple[ReponsePossible, ...] = ()
    source: str = "valeurs par défaut"

    @model_validator(mode="before")
    @classmethod
    def _completer(cls, donnees: object) -> object:
        """Une réponse absente du fichier reprend son libellé par défaut, à sa place."""
        if not isinstance(donnees, dict):
            return donnees
        lues = list(donnees.get("reponses") or [])
        presentes = {
            (r.get("nature") if isinstance(r, dict) else getattr(r, "nature", None)) for r in lues
        }
        for nature, libelle in LIBELLES_PAR_DEFAUT.items():
            if nature.value not in presentes and nature not in presentes:
                lues.append({"nature": nature.value, "libelle": libelle})
        return {**donnees, "reponses": lues}

    @model_validator(mode="after")
    def _coherence(self) -> ReglagesDesReponses:
        natures = [r.nature for r in self.reponses]
        if len(natures) != len(set(natures)):
            raise ValueError(f"{self.source} : une réponse toute faite est réglée deux fois.")
        if NatureDeReponse.MESSAGE in natures:
            raise ValueError(
                f"{self.source} : MESSAGE n'est pas une réponse toute faite, c'est le champ libre. "
                "Le régler ici afficherait un bouton qui enverrait un message vide."
            )
        return self


def texte_de_la_reponse(reponse: ReponseDeLAdherent, reglages: ReglagesDesReponses) -> str:
    """La réponse telle que le cabinet la lit : le libellé en toutes lettres, le jour annoncé, le
    message. **Une seule rédaction**, pour l'avis du cabinet et pour l'écran de relance : deux
    rédactions finiraient par dire deux choses de la même réponse."""
    libelles = {r.nature: r.libelle for r in reglages.reponses}
    return " · ".join(
        morceau
        for morceau in (
            libelles.get(reponse.nature),
            f"annoncée pour le {reponse.annoncee_pour:%d/%m/%Y}" if reponse.annoncee_pour else None,
            f"« {reponse.message} »" if reponse.message else None,
        )
        if morceau
    )
