"""« Mon entreprise » : ce que l'adhérent lit de son dossier, et ce qu'il signale (pas 114).

─────────────────────────────────────────────────────────────────────────────────
D'OÙ VIENT CE MODULE

Maquette « Espace adhérent CGA », vue E (« Mes documents », « Mon entreprise »), confrontée à la
fiche de l'accueil (pas 81) :

    maquette                                          avant le pas 114
    ───────────────────────────────────────────────── ─────────────────────────────────────────
    Forme « Établissement », activité, NIU, RCCM,     NIU, « Réel » ou « Impôt libératoire »,
    adresse, centre des impôts                        « CDI », assujettie ou non
    « Votre régime fiscal : Impôt Général             rien : le code du régime
    Synthétique, depuis 2019 », et une phrase
    « Votre interlocutrice : Aïcha MBALLA,            rien : l'adhérent ne savait pas qui appeler
    699 114 208 · Appeler · Écrire »
    « Signaler un changement : adresse, gérant,       rien
    activité, numéro de téléphone : le cabinet fait
    les démarches »

⚠️ L'ADHÉRENT NE MODIFIE RIEN LUI-MÊME

Note 2 de la vue E : « L'adhérent ne modifie jamais lui-même son NIU, son RCCM ou son régime : il
**signale** un changement, le cabinet instruit. » Un régime est un statut daté (pas 3), dont la
date d'effet porte des conséquences fiscales ; une adresse change le centre de rattachement, donc
les échéances. Le signalement est une **parole datée au journal**, annoncée au chargé de clientèle,
pas une écriture au dossier.

L'INTERLOCUTEUR : LE CHARGÉ DE CLIENTÈLE DU DOSSIER, SINON SON COMPTABLE

Parmi les habilitations actives qui couvrent le dossier, dans cet ordre : le chargé de clientèle,
puis le comptable. À rôle égal, **une habilitation nommée sur le dossier** passe avant une
habilitation transverse (qui couvre tout le cabinet), puis la plus récente. Aucun : l'écran dit
« le cabinet », jamais un nom inventé.

⚠️ Les textes (régimes, formes, centres, natures de changement) sont au référentiel
(`portefeuille/espace_adherent.yaml`), **à valider** comme ceux des échéances (pas 113).
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "CandidatInterlocuteur",
    "ExplicationDeRegime",
    "Interlocuteur",
    "NatureDeChangement",
    "NatureReglee",
    "ReglagesDeLaFicheAdherent",
    "SignalementDeChangement",
    "choisir_l_interlocuteur",
    "signalement_en_double",
]


class NatureDeChangement(StrEnum):
    ADRESSE = "ADRESSE"
    DIRIGEANT = "DIRIGEANT"
    ACTIVITE = "ACTIVITE"
    TELEPHONE = "TELEPHONE"
    AUTRE = "AUTRE"


class ExplicationDeRegime(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    regime: str = Field(min_length=1)
    titre: str = Field(min_length=3, max_length=80)
    explication: str = Field(min_length=10, max_length=500)


class NatureReglee(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    nature: NatureDeChangement
    libelle: str = Field(min_length=3, max_length=60)
    #: Ce que le cabinet fera, ou ce qu'il faudra fournir : « le cabinet demandera le nouveau bail ».
    aide: str = Field(min_length=10, max_length=300)


#: Les mots de la maquette pour les natures ; le référentiel peut les changer, pas en retirer une.
NATURES_PAR_DEFAUT: dict[NatureDeChangement, tuple[str, str]] = {
    NatureDeChangement.ADRESSE: (
        "Adresse",
        "Une nouvelle adresse peut changer votre centre des impôts, donc vos dates limites : le cabinet vérifie.",
    ),
    NatureDeChangement.DIRIGEANT: (
        "Gérant ou dirigeant",
        "Le cabinet vous demandera le procès-verbal de nomination.",
    ),
    NatureDeChangement.ACTIVITE: (
        "Activité",
        "Une nouvelle activité peut changer vos impôts : le cabinet vous rappelle avant toute démarche.",
    ),
    NatureDeChangement.TELEPHONE: (
        "Numéro de téléphone",
        "Écrivez le nouveau numéro : le cabinet met votre dossier à jour.",
    ),
    NatureDeChangement.AUTRE: (
        "Autre changement",
        "Décrivez le changement : le cabinet vous contacte.",
    ),
}


class ReglagesDeLaFicheAdherent(BaseModel):
    """`Docs/referentiel/portefeuille/espace_adherent.yaml`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    statut: str = Field(default="A_VALIDER", pattern=r"^(A_VALIDER|VALIDE)$")
    valide_par: str | None = None
    formes: dict[str, str] = Field(default_factory=dict)
    centres: dict[str, str] = Field(default_factory=dict)
    regimes: tuple[ExplicationDeRegime, ...] = ()
    natures: tuple[NatureReglee, ...] = ()
    #: Combien de signalements récents l'adhérent revoit, pour savoir qu'ils sont arrivés.
    signalements_montres: int = Field(default=5, ge=0, le=50)
    source: str = "valeurs par défaut"

    @model_validator(mode="before")
    @classmethod
    def _completer(cls, donnees: object) -> object:
        if not isinstance(donnees, dict):
            return donnees
        lues = list(donnees.get("natures") or [])
        presentes = {r.get("nature") if isinstance(r, dict) else r.nature for r in lues}
        for nature, (libelle, aide) in NATURES_PAR_DEFAUT.items():
            if nature.value not in presentes and nature not in presentes:
                lues.append({"nature": nature.value, "libelle": libelle, "aide": aide})
        return {**donnees, "natures": lues}

    @model_validator(mode="after")
    def _coherence(self) -> ReglagesDeLaFicheAdherent:
        natures = [n.nature for n in self.natures]
        if len(natures) != len(set(natures)):
            raise ValueError(f"{self.source} : une nature de changement est réglée deux fois.")
        regimes = [r.regime for r in self.regimes]
        if len(regimes) != len(set(regimes)):
            raise ValueError(f"{self.source} : un régime est expliqué deux fois.")
        if self.statut == "VALIDE" and not (self.valide_par or "").strip():
            raise ValueError(
                f"{self.source} : des textes « validés » nomment qui les a validés. C'est l'adhérent "
                "qui lit l'explication de son régime."
            )
        return self

    def regime(self, code: str) -> ExplicationDeRegime | None:
        return next((r for r in self.regimes if r.regime == code), None)

    def nature(self, nature: NatureDeChangement) -> NatureReglee:
        return next(n for n in self.natures if n.nature is nature)


class CandidatInterlocuteur(BaseModel):
    """Une habilitation active sur le dossier, avec son compte : de quoi choisir, rien de plus."""

    model_config = ConfigDict(frozen=True)

    compte: str
    role: str
    #: Nommée sur le dossier (portée explicite), ou transverse (tout le cabinet).
    nommee_sur_le_dossier: bool
    debut: date
    nom: str
    courriel: str
    telephone: str | None = None


class Interlocuteur(BaseModel):
    model_config = ConfigDict(frozen=True)

    nom: str
    role: str
    courriel: str
    telephone: str | None


#: L'ordre des rôles à qui l'adhérent s'adresse. Le réviseur et la direction ne sont pas
#: l'interlocuteur quotidien : un adhérent qui les appellerait contournerait son chargé de clientèle.
ROLES_INTERLOCUTEURS = ("CHARGE_CLIENTELE", "COMPTABLE")


def choisir_l_interlocuteur(candidats: list[CandidatInterlocuteur]) -> Interlocuteur | None:
    retenus = [c for c in candidats if c.role in ROLES_INTERLOCUTEURS]
    if not retenus:
        return None
    choisi = min(
        retenus,
        key=lambda c: (
            ROLES_INTERLOCUTEURS.index(c.role),
            not c.nommee_sur_le_dossier,
            -c.debut.toordinal(),
            c.compte,
        ),
    )
    return Interlocuteur(
        nom=choisi.nom, role=choisi.role, courriel=choisi.courriel, telephone=choisi.telephone
    )


class SignalementDeChangement(BaseModel):
    model_config = ConfigDict(frozen=True)

    nature: NatureDeChangement
    message: str
    le: datetime
    par: str


def signalement_en_double(
    nouveau: SignalementDeChangement, precedents: list[SignalementDeChangement]
) -> bool:
    """Le même signalement, le même jour, par le même compte : un double appui, pas un second changement."""
    return any(
        p.nature is nouveau.nature
        and p.message.strip() == nouveau.message.strip()
        and p.le.date() == nouveau.le.date()
        and p.par == nouveau.par
        for p in precedents
    )
