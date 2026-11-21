"""La fiche signalétique de la DSF : ce que les fiches R1, R2 et R3 exigent (15 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI

Le dossier portait l'identité et les chiffres de l'entreprise, pas tout ce que la DSF
demande en tête : code d'activité NACAM, numéro CNPS employeur, coordonnées, contrôle
de l'entité, conseil d'administration, signataire des états, domiciliation bancaire
(modèle officiel « DSF Normal », fiches R1 à R3). Ces informations, c'est le CLIENT
qui les connaît : il les renseigne dans son espace, le cabinet les valide.

LE CYCLE
    BROUILLON ──soumettre──▶ SOUMISE ──valider──▶ VALIDEE
        ▲                       │
        └──────renvoyer (motif)─┘
Modifier une fiche validée la ramène au brouillon : la DSF ne s'établit que sur une
fiche que le cabinet a relue telle qu'elle est.

⚠️ RIEN NE SE SOUMET INCOMPLET : `manquants()` dit ce que la DSF exigera, dans les mots
du client. L'agent de suggestion, quand il viendra, préremplira cette même fiche ;
il ne la validera jamais.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ActiviteDeclaree",
    "ControleDeLEntite",
    "EtatFiche",
    "FicheRefusee",
    "FicheSignaletique",
    "MembreDuConseil",
]


class FicheRefusee(ValueError):
    """La transition demandée n'est pas permise dans cet état."""


class EtatFiche(StrEnum):
    BROUILLON = "BROUILLON"
    SOUMISE = "SOUMISE"
    VALIDEE = "VALIDEE"


class ControleDeLEntite(StrEnum):
    """Fiche R2 : « contrôle de l'entité »."""

    PUBLIC = "PUBLIC"
    PRIVE_NATIONAL = "PRIVE_NATIONAL"
    PRIVE_ETRANGER = "PRIVE_ETRANGER"


class ActiviteDeclaree(BaseModel):
    """Fiche R2 : une activité, son code de la nomenclature NACAM (INS), sa part du CA."""

    model_config = ConfigDict(frozen=True)

    designation: str = Field(min_length=2, max_length=200)
    code_nacam: str = Field(min_length=2, max_length=12)
    chiffre_affaires_ht: Decimal | None = Field(default=None, ge=0)


class MembreDuConseil(BaseModel):
    """Fiche R3 : un membre du conseil d'administration."""

    model_config = ConfigDict(frozen=True)

    nom: str = Field(min_length=1, max_length=120)
    prenoms: str | None = Field(default=None, max_length=120)
    qualite: str = Field(min_length=1, max_length=120)
    adresse: str | None = Field(default=None, max_length=300)


#: Ce que la DSF ne peut pas omettre, dans les mots du client.
_EXIGES: tuple[tuple[str, str], ...] = (
    ("telephone", "le numéro de téléphone de l'entreprise"),
    ("ville", "la ville du siège"),
    ("adresse_geographique", "l'adresse géographique (immeuble, rue, quartier)"),
    ("controle", "qui contrôle l'entreprise (public, privé national ou étranger)"),
    ("signataire_nom", "le nom du signataire des états financiers"),
    ("signataire_qualite", "la qualité du signataire des états financiers"),
    ("banque", "la banque de l'entreprise"),
    ("numero_de_compte", "le numéro de compte bancaire"),
)


class FicheSignaletique(BaseModel):
    model_config = ConfigDict(frozen=True)

    # ── Fiche R1 : coordonnées et renseignements divers ──
    telephone: str | None = Field(default=None, max_length=40)
    courriel: str | None = Field(default=None, max_length=200)
    boite_postale: str | None = Field(default=None, max_length=40)
    ville: str | None = Field(default=None, max_length=80)
    adresse_geographique: str | None = Field(default=None, max_length=300)
    numero_cnps_employeur: str | None = Field(default=None, max_length=40)
    code_importateur: str | None = Field(default=None, max_length=40)
    personne_a_contacter: str | None = Field(default=None, max_length=200)
    professionnel_des_etats: str | None = Field(default=None, max_length=300)
    signataire_nom: str | None = Field(default=None, max_length=120)
    signataire_qualite: str | None = Field(default=None, max_length=120)
    etats_approuves_en_ag: bool | None = None
    banque: str | None = Field(default=None, max_length=120)
    numero_de_compte: str | None = Field(default=None, max_length=60)

    # ── Fiche R2 : identification et activités ──
    #: Code pays du siège (note 36 du modèle) : 09 pour le Cameroun.
    pays_du_siege: str = Field(default="09", min_length=2, max_length=2)
    etablissements_au_cameroun: int | None = Field(default=None, ge=0)
    etablissements_hors_cameroun: int | None = Field(default=None, ge=0)
    premiere_annee_au_cameroun: int | None = Field(default=None, ge=1900, le=2100)
    controle: ControleDeLEntite | None = None
    activites: tuple[ActiviteDeclaree, ...] = ()

    # ── Fiche R3 : conseil d'administration (les dirigeants sont au dossier) ──
    conseil_d_administration: tuple[MembreDuConseil, ...] = ()

    # ── Le cycle ──
    etat: EtatFiche = EtatFiche.BROUILLON
    modifiee_le: datetime | None = None
    modifiee_par: str | None = None
    soumise_le: datetime | None = None
    validee_le: datetime | None = None
    validee_par: str | None = None
    motif_du_renvoi: str | None = None

    def manquants(self) -> list[str]:
        """Ce qui manque encore pour établir la DSF, dans les mots du client."""
        absents = [libelle for champ, libelle in _EXIGES if not getattr(self, champ)]
        if not self.activites:
            absents.append("au moins une activité, avec son code NACAM")
        return absents

    def modifier(self, champs: dict, *, par: str, a_l_instant: datetime) -> FicheSignaletique:
        """Toute modification ramène au brouillon : une fiche validée se relit à neuf."""
        if self.etat is EtatFiche.SOUMISE:
            raise FicheRefusee(
                "la fiche est soumise au cabinet : attendez sa réponse avant de la modifier."
            )
        # ⚠️ Revalidée en entier, et non `model_copy` : celle-ci ne valide rien, et les
        # activités reçues en dictionnaires restaient des dictionnaires (constaté par
        # l'avertissement de sérialisation, le 15 octobre 2026).
        return FicheSignaletique.model_validate(
            {
                **self.model_dump(),
                **champs,
                "etat": EtatFiche.BROUILLON,
                "modifiee_le": a_l_instant,
                "modifiee_par": par,
                "validee_le": None,
                "validee_par": None,
            }
        )

    def soumettre(self, *, a_l_instant: datetime) -> FicheSignaletique:
        if self.etat is not EtatFiche.BROUILLON:
            raise FicheRefusee(f"une fiche {self.etat.value.lower()} ne se soumet pas.")
        manque = self.manquants()
        if manque:
            raise FicheRefusee("il manque encore : " + " ; ".join(manque) + ".")
        return self.model_copy(
            update={"etat": EtatFiche.SOUMISE, "soumise_le": a_l_instant, "motif_du_renvoi": None}
        )

    def valider(self, *, par: str, a_l_instant: datetime) -> FicheSignaletique:
        if self.etat is not EtatFiche.SOUMISE:
            raise FicheRefusee("seule une fiche soumise par le client se valide.")
        return self.model_copy(
            update={"etat": EtatFiche.VALIDEE, "validee_le": a_l_instant, "validee_par": par}
        )

    def renvoyer(self, *, motif: str) -> FicheSignaletique:
        if self.etat is not EtatFiche.SOUMISE:
            raise FicheRefusee("seule une fiche soumise se renvoie au client.")
        motif = motif.strip()
        if len(motif) < 10:
            raise FicheRefusee(
                "un renvoi dit au client ce qu'il doit corriger (10 caractères au moins)."
            )
        return self.model_copy(update={"etat": EtatFiche.BROUILLON, "motif_du_renvoi": motif})
