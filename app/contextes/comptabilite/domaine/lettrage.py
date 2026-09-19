"""Le lettrage : apparier, sur un compte de tiers, les mouvements qui se soldent (pas 108).

─────────────────────────────────────────────────────────────────────────────────
CE QUE C'EST, POUR QUI ARRIVE AU CABINET

Sur le compte 401 d'un fournisseur, la facture est au crédit, le règlement au débit. Quand
les deux se correspondent, le comptable les **lettre** : il leur donne la même lettre (A, B…).
Ce qui reste **non lettré** est ce qui est réellement ouvert chez ce fournisseur : une facture
pas encore réglée, un règlement sans facture. C'est la question que pose la maquette
« Parcours comptable », vue C : « 2 écritures non lettrées ».

⚠️ POURQUOI LE LETTRAGE N'EST PAS ÉCRIT DANS L'ÉCRITURE

La ligne d'écriture a un champ `lettrage`, et on pourrait être tenté de le remplir. Ce serait
**réécrire une écriture validée**, que le dépôt refuse, à juste titre : une écriture validée
est intangible. Or le lettrage n'est pas un fait comptable, c'est un **rapprochement de
travail** qui se fait, se défait, se refait. Il vit donc à part, dans son propre objet, et le
grand livre superpose sa lettre aux lignes à l'affichage.

Le champ `lettrage` de la ligne garde ce qu'il a toujours porté : la lettre **reprise** d'un
logiciel client (pas 85). Elle s'affiche, marquée comme reprise ; elle ne se défait pas ici,
et une ligne ainsi lettrée ne se relettre pas.

LES RÈGLES, ET POURQUOI

* **Au moins deux lignes, d'un même compte lettrable** : un compte de charges ne se lettre
  pas, il n'y a rien à apparier.
* **Des écritures validées** : un brouillon peut encore changer de montant.
* **Un même tiers** quand les lignes en portent un : lettrer la facture d'ALPHA avec le
  règlement de BÊTA ferait disparaître deux dettes qui existent.
* **Débit égal au crédit**, à l'écart toléré près (référentiel, zéro par défaut) : un lettrage
  déséquilibré cacherait un reste dû.
* **Une ligne n'est lettrée qu'une fois** à la fois.
* **Une lettre ne se réutilise jamais** sur le compte : un lettrage défait garde la sienne, et
  le suivant prend la suivante. Sinon « la lettre C » désignerait deux appariements selon la
  date où l'on regarde.

⚠️ LE VERROU MENSUEL (PAS 107) NE S'APPLIQUE PAS

Lettrer ne change ni un montant ni une date : la balance d'un mois verrouillé reste celle que
le réviseur a relue. Refuser le lettrage d'une facture de juillet réglée en août obligerait à
attendre un renvoi pour un geste qui ne touche pas au mois.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.partage.copie import transiter

__all__ = [
    "LettrageDeLignes",
    "LettrageRefuse",
    "ReferenceDeLigne",
    "ReglagesDuLettrage",
    "StatutLettrage",
    "lettre_de_rang",
]


class LettrageRefuse(ValueError):
    """Le lettrage demandé ne tient pas. Le message dit pourquoi, pour l'écran."""


class ReglagesDuLettrage(BaseModel):
    """`Docs/referentiel/lettrage/reglages.yaml`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: L'écart admis entre débit et crédit, en FCFA. Zéro par défaut : un lettrage qui ne
    #: se solde pas cache un reste dû. Un cabinet qui admet les écarts d'arrondi le relève.
    ecart_tolere: Decimal = Field(Decimal(0), ge=0, le=1000)
    #: Exiger que toutes les lignes qui portent un tiers portent le même.
    meme_tiers_exige: bool = True
    source: str = "valeurs par défaut"


class ReferenceDeLigne(BaseModel):
    """Une ligne d'écriture : la clé de l'écriture et son rang, à partir de 0."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cle_ecriture: str = Field(min_length=1, max_length=40)
    rang: int = Field(ge=0, le=999)


class StatutLettrage(StrEnum):
    ACTIF = "ACTIF"
    DEFAIT = "DEFAIT"


def lettre_de_rang(rang: int) -> str:
    """0 → A, 25 → Z, 26 → AA, 27 → AB… Comme les colonnes d'un tableur."""
    if rang < 0:
        raise ValueError("le rang d'une lettre est positif.")
    lettres = ""
    rang += 1
    while rang:
        rang, reste = divmod(rang - 1, 26)
        lettres = chr(ord("A") + reste) + lettres
    return lettres


class LettrageDeLignes(BaseModel):
    model_config = ConfigDict(frozen=True)

    identifiant: str
    dossier: str
    exercice: str
    compte: str
    lettre: str = Field(pattern=r"^[A-Z]{1,4}$")
    lignes: tuple[ReferenceDeLigne, ...] = Field(min_length=2)
    total_debit: Decimal
    total_credit: Decimal
    tiers: str | None = None
    statut: StatutLettrage = StatutLettrage.ACTIF
    par: str
    le: datetime
    defait_par: str | None = None
    defait_le: datetime | None = None

    @model_validator(mode="after")
    def _coherent(self) -> LettrageDeLignes:
        if len(set(self.lignes)) != len(self.lignes):
            raise ValueError("une même ligne figure deux fois dans le lettrage.")
        defait = self.statut is StatutLettrage.DEFAIT
        if defait != (self.defait_par is not None and self.defait_le is not None):
            raise ValueError("un lettrage défait nomme qui l'a défait, et quand ; un actif, non.")
        return self

    @property
    def actif(self) -> bool:
        return self.statut is StatutLettrage.ACTIF

    def defaire(self, *, par: str, le: datetime) -> LettrageDeLignes:
        if not self.actif:
            raise LettrageRefuse(
                f"le lettrage {self.lettre} du compte {self.compte} est déjà défait."
            )
        return transiter(self, statut=StatutLettrage.DEFAIT, defait_par=par, defait_le=le)
