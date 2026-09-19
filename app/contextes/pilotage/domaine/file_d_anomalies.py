"""La file d'anomalies du réviseur : tout le portefeuille, par gravité et par enjeu (pas 117).

─────────────────────────────────────────────────────────────────────────────────
D'OÙ VIENT CE MODULE

Maquette « Parcours réviseur CGA », vue A (« File d'anomalies », UC01 et UC02 : « traiter les
constats de tout le portefeuille, en file, au clavier »). Le produit savait déjà tout faire
**dossier par dossier** ou **règle par règle** :

    la boîte de réception (E03)        une pastille de conformité par pièce, dossier par dossier
    la fiche d'une facture (E02)       ses constats, son verdict, ses écarts
    l'écart en masse (pas 103)         les constats d'**une** règle, sur une période
    la qualité des règles (pas 92)     les taux d'écartement, règle par règle

Il ne savait pas dire : « voici les six constats bloquants de votre portefeuille, le plus coûteux
d'abord ». Or c'est ainsi que travaille un réviseur : par gravité et par enjeu fiscal, pas dossier
par dossier (note 1 de la vue A).

CE QUE LA FILE ORDONNE, ET POURQUOI

1. **La gravité d'abord.** Un bloquant interdit la comptabilisation : il coûte tous les jours.
2. **Ce qui dort ensuite**, dans sa gravité : un constat oublié ne doit pas descendre sous les
   arrivées du jour.
3. **L'enjeu fiscal**, décroissant. Un constat sans enjeu chiffré passe après ceux qui en ont un :
   on ne fait pas passer l'inconnu devant le connu.
4. **L'ancienneté enfin**, décroissante : à égalité, le plus vieux d'abord.

⚠️ **Un constat qui dort est un risque** (note 2) : au-delà du seuil du référentiel (quinze jours),
il est signalé `dort` et **passe devant, dans sa gravité**, avant l'enjeu. Il ne change pas de
gravité et n'en franchit pas la frontière : un avertissement endormi devant un constat bloquant du
jour ferait traiter le moins grave d'abord, et le bloquant interdit la comptabilisation tous les
jours qu'il passe dans la file.

⚠️ **Le regroupement par règle est le gain du profil** (note 3) : dix-huit constats de la même règle
se traitent d'un bloc, avec un motif unique, par l'écran d'écart en masse. La file compte donc, pour
chaque règle, ses constats et leur enjeu cumulé.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "GroupeDeRegle",
    "LigneDAnomalie",
    "ReglagesDeLaFile",
    "compter_par_gravite",
    "grouper_par_regle",
    "ordonner_la_file",
]

#: L'ordre des gravités, du plus grave au moins grave. Celui du § 9 du dossier de design.
ORDRE_DES_GRAVITES = ("BLOQUANT", "MAJEUR", "AVERTISSEMENT", "INFORMATION")


class ReglagesDeLaFile(BaseModel):
    """`Docs/referentiel/pilotage/file_d_anomalies.yaml`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Au-delà, le constat « dort » : il est signalé et remonte d'un cran dans le tri.
    seuil_anciennete_jours: int = Field(default=15, ge=1, le=365)
    #: Les gravités montrées dans la file. L'information n'y est pas par défaut : elle n'appelle
    #: aucune décision, et noierait les constats qui en appellent une.
    gravites_traitees: tuple[str, ...] = ("BLOQUANT", "MAJEUR", "AVERTISSEMENT")
    source: str = "valeurs par défaut"

    @model_validator(mode="after")
    def _coherence(self) -> ReglagesDeLaFile:
        inconnues = [g for g in self.gravites_traitees if g not in ORDRE_DES_GRAVITES]
        if inconnues:
            raise ValueError(
                f"{self.source} : gravité(s) inconnue(s) {inconnues}. Gravités possibles : "
                f"{list(ORDRE_DES_GRAVITES)}."
            )
        if not self.gravites_traitees:
            raise ValueError(
                f"{self.source} : une file sans gravité traitée est toujours vide, et l'écran "
                "la dirait « rien à traiter »."
            )
        if ORDRE_DES_GRAVITES[0] not in self.gravites_traitees:
            raise ValueError(
                f"{self.source} : les constats bloquants ne se retirent pas de la file. Ils "
                "interdisent la comptabilisation : les cacher ne les fait pas disparaître."
            )
        return self


class LigneDAnomalie(BaseModel):
    """Un constat à traiter, avec ce qu'il faut pour décider sans ouvrir la pièce."""

    model_config = ConfigDict(frozen=True)

    piece: str
    dossier: str
    denomination: str
    date_piece: date
    fournisseur: str | None
    code_regle: str
    libelle_regle: str
    gravite: str
    message: str
    #: L'enjeu fiscal du constat, quand la règle sait le chiffrer.
    enjeu: Decimal | None
    #: Jours écoulés depuis la date de la pièce.
    anciennete: int
    #: Au-delà du seuil : le constat dort, et remonte dans la file.
    dort: bool
    #: Qui a déposé la pièce, quand la collecte le sait : un collaborateur pour un dépôt au
    #: cabinet, **l'entreprise elle-même** pour un dépôt au portail. La maquette parle de
    #: « collaborateur » ; les données disent « déposée par », et c'est ce qui est affiché : nommer
    #: « collaborateur » une colonne qui porte le nom de l'adhérent ferait chercher un coupable
    #: au cabinet.
    depose_par: str | None
    #: Un écart est déjà ouvert sur ce constat : il n'est plus à décider, il est décidé.
    ecart_ouvert: bool = False


class GroupeDeRegle(BaseModel):
    model_config = ConfigDict(frozen=True)

    code_regle: str
    libelle_regle: str
    gravite: str
    constats: int
    enjeu_cumule: Decimal
    dossiers: int


def _rang_de_gravite(gravite: str) -> int:
    return ORDRE_DES_GRAVITES.index(gravite) if gravite in ORDRE_DES_GRAVITES else len(ORDRE_DES_GRAVITES)


def ordonner_la_file(lignes: list[LigneDAnomalie]) -> list[LigneDAnomalie]:
    """Gravité, puis ce qui dort, puis l'enjeu décroissant, puis l'ancienneté décroissante.

    ⚠️ **Un constat qui dort ne change pas de gravité et n'en sort pas.** Un premier essai le faisait
    monter d'un cran : un avertissement de deux mois, sans enjeu chiffré, passait alors devant un
    constat bloquant du jour à 500 000 F. Un bloquant interdit la comptabilisation : il reste devant.
    """

    def cle(l_: LigneDAnomalie):
        return (
            _rang_de_gravite(l_.gravite),
            0 if l_.dort else 1,
            -(l_.enjeu if l_.enjeu is not None else Decimal(-1)),
            -l_.anciennete,
            l_.dossier,
            l_.piece,
            l_.code_regle,
        )

    return sorted(lignes, key=cle)


def compter_par_gravite(lignes: list[LigneDAnomalie]) -> dict[str, int]:
    """Les compteurs de l'en-tête, dans l'ordre des gravités : « 6 bloquantes, 14 majeures… »."""
    return {
        gravite: sum(1 for l_ in lignes if l_.gravite == gravite)
        for gravite in ORDRE_DES_GRAVITES
        if any(l_.gravite == gravite for l_ in lignes)
    }


def grouper_par_regle(lignes: list[LigneDAnomalie]) -> list[GroupeDeRegle]:
    """Les règles de la file, la plus fournie d'abord : c'est par là qu'on traite d'un bloc."""
    groupes: dict[str, GroupeDeRegle] = {}
    dossiers: dict[str, set[str]] = {}
    for ligne in lignes:
        dossiers.setdefault(ligne.code_regle, set()).add(ligne.dossier)
        courant = groupes.get(ligne.code_regle)
        if courant is None:
            groupes[ligne.code_regle] = GroupeDeRegle(
                code_regle=ligne.code_regle,
                libelle_regle=ligne.libelle_regle,
                gravite=ligne.gravite,
                constats=1,
                enjeu_cumule=ligne.enjeu or Decimal(0),
                dossiers=1,
            )
            continue
        groupes[ligne.code_regle] = courant.model_copy(
            update={
                "constats": courant.constats + 1,
                "enjeu_cumule": courant.enjeu_cumule + (ligne.enjeu or Decimal(0)),
                # ⚠️ La gravité la plus forte du groupe : une règle qui produit un bloquant et des
                # avertissements se traite comme un bloquant.
                "gravite": ligne.gravite
                if _rang_de_gravite(ligne.gravite) < _rang_de_gravite(courant.gravite)
                else courant.gravite,
            }
        )
    for code, groupe in groupes.items():
        groupes[code] = groupe.model_copy(update={"dossiers": len(dossiers[code])})
    return sorted(
        groupes.values(),
        key=lambda g: (_rang_de_gravite(g.gravite), -g.constats, -g.enjeu_cumule, g.code_regle),
    )
