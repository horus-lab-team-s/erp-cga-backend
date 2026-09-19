"""Ce que l'adhérent lit en ouvrant son espace : ce qui manque, ou que tout est complet (pas 112).

─────────────────────────────────────────────────────────────────────────────────
D'OÙ VIENT CE MODULE

Maquette « Espace adhérent CGA », vue B, note 1 : « Le bandeau d'état est la première chose lue
et ne dit qu'une chose : ce qui manque, ou que tout est complet. » L'espace du pas 81 listait les
demandes ouvertes plus bas dans la page, sans rien dire du mois : un adhérent sans demande ne
savait pas si son dossier était complet, ou si personne ne l'avait encore regardé.

QUATRE ÉTATS, ET POURQUOI PAS DEUX

    A_ENVOYER         le cabinet a demandé des pièces, encore ouvertes
    EN_VERIFICATION   rien n'est demandé, mais la plateforme déduit qu'il pourrait manquer quelque
                      chose (pas 111) et le cabinet ne l'a pas encore demandé
    AUCUNE_PIECE      rien n'est demandé, rien n'est déduit, et aucune pièce du mois n'est arrivée
    COMPLET           rien n'est demandé, rien n'est déduit, des pièces sont arrivées

⚠️ **« Complet » ne se dit que s'il est vrai.** Une attente déduite (un relevé bancaire absent,
une série de factures interrompue) n'est pas montrée à l'adhérent tant que le cabinet ne l'a pas
demandée : c'est une présomption, et le comptable sait peut-être que la série s'arrête. Mais
elle interdit de dire « complet ». L'écran dit alors « le cabinet vérifie », ce qui est exact.

⚠️ **Un mois sans aucune pièce n'est pas « complet ».** Une entreprise qui n'a rien acheté ni
vendu existe, mais c'est rare, et l'annoncer complet à celle qui a oublié d'envoyer ses factures
est l'erreur la plus coûteuse de l'écran. L'état vide est « prescriptif » (vue F, note 2) : il dit
quoi faire.

LES MOTS SONT AU RÉFÉRENTIEL

`Docs/referentiel/pilotage/espace_adherent.yaml`. Un contrôle refuse le texte qui tairait la date
limite quand des pièces manquent, ou qui citerait une valeur que l'écran ne connaît pas : un
`{date_limite}` mal orthographié partirait tel quel sur le téléphone de l'adhérent.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import string
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "AttendueDeLAdherent",
    "Bandeau",
    "EtatDuMois",
    "ReglagesDeLEspaceAdherent",
    "TexteDeBandeau",
    "achats_du_mois",
    "de_mois",
    "etat_du_mois",
    "rendre_le_bandeau",
]


class EtatDuMois(StrEnum):
    A_ENVOYER = "A_ENVOYER"
    EN_VERIFICATION = "EN_VERIFICATION"
    AUCUNE_PIECE = "AUCUNE_PIECE"
    COMPLET = "COMPLET"


#: Les valeurs qu'un texte de bandeau peut citer. Toute autre est refusée au chargement.
VALEURS_CITABLES = frozenset({"mois", "de_mois", "pieces", "date_limite"})


def de_mois(nom_du_mois: str) -> str:
    """« de juillet 2026 », mais « d'août 2026 », « d'avril », « d'octobre ».

    ⚠️ Pas 112, trouvé à l'essai réel : « Votre mois de août 2026 ». Le référentiel cite
    `{de_mois}` là où la préposition précède le mois.
    """
    return f"d'{nom_du_mois}" if nom_du_mois[:1] in "aeiouéèêâ" else f"de {nom_du_mois}"


class TexteDeBandeau(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    titre: str = Field(min_length=3, max_length=120)
    detail: str = Field(min_length=3, max_length=300)

    def citees(self) -> set[str]:
        return {
            nom
            for texte in (self.titre, self.detail)
            for _, nom, _, _ in string.Formatter().parse(texte)
            if nom is not None
        }


class ReglagesDeLEspaceAdherent(BaseModel):
    """`Docs/referentiel/pilotage/espace_adherent.yaml`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Les comptes dont le solde fait « vos achats du mois ». Les mêmes racines que la clôture
    #: mensuelle par défaut : deux définitions des achats donneraient deux montants.
    racines_d_achats: tuple[str, ...] = ("60",)

    a_envoyer: TexteDeBandeau = TexteDeBandeau(
        titre="Il manque {pieces}",
        detail="Envoyez-les avant le {date_limite} pour que votre déclaration soit prête à temps.",
    )
    #: La date annoncée est passée : la répéter ne ferait rien revenir.
    a_envoyer_en_retard: TexteDeBandeau = TexteDeBandeau(
        titre="Il manque {pieces}",
        detail="La date prévue est passée : envoyez-les dès que possible, le cabinet vous attend.",
    )
    #: Aucune date n'a été annoncée par le cabinet.
    a_envoyer_sans_date: TexteDeBandeau = TexteDeBandeau(
        titre="Il manque {pieces}",
        detail="Envoyez-les dès que possible pour que votre déclaration soit prête à temps.",
    )
    en_verification: TexteDeBandeau = TexteDeBandeau(
        titre="Le cabinet vérifie votre mois {de_mois}",
        detail="Il vous écrira s'il lui manque une pièce. Vous n'avez rien à faire pour l'instant.",
    )
    aucune_piece: TexteDeBandeau = TexteDeBandeau(
        titre="Aucun justificatif pour {mois}",
        detail="Si vous avez acheté ou vendu en {mois}, envoyez vos factures : c'est le "
        "meilleur moment, et cela évite de les chercher plus tard.",
    )
    complet: TexteDeBandeau = TexteDeBandeau(
        titre="Votre dossier {de_mois} est complet",
        detail="Le cabinet prépare votre déclaration. Vous n'avez rien à faire.",
    )
    source: str = "valeurs par défaut"

    @model_validator(mode="after")
    def _coherence(self) -> ReglagesDeLEspaceAdherent:
        if not self.racines_d_achats or any(not r.isdigit() for r in self.racines_d_achats):
            raise ValueError(f"{self.source} : racines_d_achats sont des débuts de numéro de compte.")
        for nom in (
            "a_envoyer",
            "a_envoyer_en_retard",
            "a_envoyer_sans_date",
            "en_verification",
            "aucune_piece",
            "complet",
        ):
            texte: TexteDeBandeau = getattr(self, nom)
            if "de {mois}" in f"{texte.titre} {texte.detail}":
                raise ValueError(
                    f"{self.source} : {nom} écrit « de {{mois}} », qui donnerait « de août ». "
                    "Écrire {de_mois}, qui fait l'élision."
                )
            inconnues = texte.citees() - VALEURS_CITABLES
            if inconnues:
                raise ValueError(
                    f"{self.source} : {nom} cite {sorted(inconnues)}, que l'écran ne connaît pas. "
                    f"Valeurs possibles : {sorted(VALEURS_CITABLES)}."
                )
        if "date_limite" not in self.a_envoyer.citees():
            raise ValueError(
                f"{self.source} : a_envoyer doit citer {{date_limite}}. Dire qu'il manque des "
                "pièces sans dire avant quand, c'est ne pas dire qu'il y a urgence."
            )
        for nom in (
            "a_envoyer_en_retard",
            "a_envoyer_sans_date",
            "en_verification",
            "aucune_piece",
            "complet",
        ):
            if "date_limite" in getattr(self, nom).citees():
                raise ValueError(f"{self.source} : {nom} n'a pas de date limite à citer.")
        return self


class AttendueDeLAdherent(BaseModel):
    """Une pièce que le cabinet a demandée, dans les mots de l'adhérent."""

    model_config = ConfigDict(frozen=True)

    demande: str
    libelle: str
    type_attendu: str
    demandee_le: date
    attendue_pour: date | None
    en_retard: bool
    bloquante: bool
    #: Une facture reçue dont le cabinet demande la version corrigée (pas 74).
    a_corriger: bool
    #: La pièce à corriger, pour que l'adhérent la retrouve dans ses justificatifs.
    piece_a_rectifier: str | None = None
    #: Sa dernière réponse, telle que le cabinet la lit : l'adhérent voit qu'elle est arrivée.
    reponse: str | None = None
    reponse_le: datetime | None = None


class Bandeau(BaseModel):
    model_config = ConfigDict(frozen=True)

    etat: EtatDuMois
    titre: str
    detail: str


def etat_du_mois(
    *,
    attendues: list[AttendueDeLAdherent],
    attentes_deduites_sans_demande: int,
    pieces_du_mois: int,
) -> EtatDuMois:
    """L'état, dans l'ordre où il se décide : ce qui est demandé l'emporte sur tout."""
    if attendues:
        return EtatDuMois.A_ENVOYER
    if attentes_deduites_sans_demande > 0:
        return EtatDuMois.EN_VERIFICATION
    if pieces_du_mois == 0:
        return EtatDuMois.AUCUNE_PIECE
    return EtatDuMois.COMPLET


def _pieces(nombre: int) -> str:
    return f"{nombre} justificatif{'s' if nombre > 1 else ''}"


def rendre_le_bandeau(
    etat: EtatDuMois,
    *,
    attendues: list[AttendueDeLAdherent],
    nom_du_mois: str,
    jour: date,
    reglages: ReglagesDeLEspaceAdherent,
) -> tuple[Bandeau, date | None]:
    """Le bandeau rendu, et la date limite annoncée (la plus proche des dates demandées).

    ⚠️ La date limite est **la plus proche**, pas la dernière : annoncer la plus lointaine
    laisserait passer la première pièce attendue.
    """
    # « juillet 2026 » dans le bandeau : l'année reste, un adhérent en retard d'un an existe.
    valeurs = {
        "mois": nom_du_mois,
        "de_mois": de_mois(nom_du_mois),
        "pieces": _pieces(len(attendues)),
        "date_limite": "",
    }
    limite = min((a.attendue_pour for a in attendues if a.attendue_pour), default=None)
    if etat is EtatDuMois.A_ENVOYER:
        if limite is None:
            texte = reglages.a_envoyer_sans_date
        elif limite < jour:
            texte = reglages.a_envoyer_en_retard
        else:
            texte = reglages.a_envoyer
            valeurs["date_limite"] = f"{limite:%d/%m/%Y}"
    else:
        texte = {
            EtatDuMois.EN_VERIFICATION: reglages.en_verification,
            EtatDuMois.AUCUNE_PIECE: reglages.aucune_piece,
            EtatDuMois.COMPLET: reglages.complet,
        }[etat]
    return (
        Bandeau(
            etat=etat, titre=texte.titre.format(**valeurs), detail=texte.detail.format(**valeurs)
        ),
        limite,
    )


def achats_du_mois(lignes: list[tuple[str, bool, Decimal]], racines: tuple[str, ...]) -> Decimal:
    """Le solde des comptes d'achats : (compte, au débit, montant) des écritures validées du mois."""
    return sum(
        (montant if au_debit else -montant for compte, au_debit, montant in lignes if compte.startswith(racines)),
        Decimal(0),
    )
