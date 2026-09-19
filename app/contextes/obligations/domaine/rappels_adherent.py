"""Les rappels d'échéance que l'adhérent règle, et ceux qui sont dus (pas 115).

─────────────────────────────────────────────────────────────────────────────────
D'OÙ VIENT CE MODULE

Maquette « Espace adhérent CGA », vue F (« Réglages et états »), le réglage « Rappels avant les
échéances · 7 jours et 2 jours avant ». Rien n'envoyait de rappel à l'adhérent : le cabinet relançait
les retards (pas 55), une fois le mal fait. Un réglage sans envoi derrière serait un interrupteur
qui ne commande rien : ce pas construit **les deux**, la préférence et le travail périodique qui
l'honore.

LE JALON DÛ, ET POURQUOI « DÛ » PLUTÔT QUE « LE JOUR MÊME »

Les jalons choisis (par défaut 7 et 2 jours) sont des seuils, pas des dates : le rappel « J-7 » est
dû dès que l'échéance est à 7 jours **ou moins**, et il part une seule fois. Un travail arrêté le
jour J-7 (redémarrage, panne) envoie donc le rappel J-7 le lendemain, à J-6, au lieu de le perdre.
Quand deux jalons sont franchis sans envoi (reprise à J-1), **seul le plus proche part** : deux
messages le même jour pour la même échéance lasseraient plus qu'ils n'aideraient.

    jours restants   jalons [7, 2]   ce qui part
    ──────────────   ─────────────   ──────────────────────────────────────
    9                aucun dû        rien
    7, 6… 3          7               le rappel J-7, une fois
    2, 1, 0          2               le rappel J-2, une fois
    en retard        aucun           rien : c'est la relance du cabinet

⚠️ Une échéance dont la preuve de paiement est envoyée (pas 113) ou déjà déposée n'est pas rappelée :
seules les cartes « à venir » le sont.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "PreferenceDeRappels",
    "ReglagesDesRappels",
    "jalon_du",
]


class ReglagesDesRappels(BaseModel):
    """`Docs/referentiel/obligations/rappels_adherent.yaml`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Les jalons que l'adhérent peut choisir, en jours avant l'échéance.
    jalons_possibles: tuple[int, ...] = (7, 3, 2, 1)
    #: Ce qui vaut pour un adhérent qui n'a jamais rien réglé.
    actifs_par_defaut: bool = True
    jalons_par_defaut: tuple[int, ...] = (7, 2)
    #: Le rythme du travail périodique. Une heure suffit : un rappel à J-7 n'est pas à l'heure près.
    cadence_minutes: int = Field(default=60, ge=5, le=1440)
    #: Pourquoi « Recevoir aussi par WhatsApp » est grisé : un canal inactif dit pourquoi.
    motif_whatsapp: str = Field(
        default="Le compte d'envoi WhatsApp du cabinet n'est pas encore ouvert.", min_length=10
    )
    source: str = "valeurs par défaut"

    @model_validator(mode="after")
    def _coherence(self) -> ReglagesDesRappels:
        if not self.jalons_possibles or any(j < 0 or j > 60 for j in self.jalons_possibles):
            raise ValueError(f"{self.source} : un jalon est un nombre de jours entre 0 et 60.")
        if len(set(self.jalons_possibles)) != len(self.jalons_possibles):
            raise ValueError(f"{self.source} : un jalon possible est écrit deux fois.")
        if not set(self.jalons_par_defaut) <= set(self.jalons_possibles):
            raise ValueError(
                f"{self.source} : les jalons par défaut {list(self.jalons_par_defaut)} doivent être "
                f"parmi les jalons possibles {list(self.jalons_possibles)} : l'écran ne saurait pas "
                "afficher un réglage que l'adhérent ne peut pas choisir."
            )
        if self.actifs_par_defaut and not self.jalons_par_defaut:
            raise ValueError(
                f"{self.source} : des rappels actifs par défaut sans aucun jalon ne partiraient "
                "jamais, et l'écran les dirait actifs."
            )
        return self


class PreferenceDeRappels(BaseModel):
    """Le réglage d'un compte adhérent. `defini` : faux tant qu'il n'a rien réglé (défaut du référentiel)."""

    model_config = ConfigDict(frozen=True)

    actifs: bool
    jalons: tuple[int, ...]
    defini: bool = False

    @model_validator(mode="after")
    def _coherence(self) -> PreferenceDeRappels:
        if self.actifs and not self.jalons:
            raise ValueError(
                "choisissez au moins un moment pour être prévenu, ou désactivez les rappels."
            )
        if len(set(self.jalons)) != len(self.jalons):
            raise ValueError("un moment de rappel est choisi deux fois.")
        return self

    def verifier(self, reglages: ReglagesDesRappels) -> None:
        hors = sorted(set(self.jalons) - set(reglages.jalons_possibles))
        if hors:
            raise ValueError(
                f"{hors} : ces moments ne sont pas proposés. Choix possibles : "
                f"{sorted(reglages.jalons_possibles, reverse=True)} jours avant."
            )


def jalon_du(jours_restants: int, jalons: tuple[int, ...]) -> int | None:
    """Le jalon dû pour une échéance à `jours_restants` jours : le plus proche franchi, ou `None`."""
    if jours_restants < 0:
        return None
    franchis = [j for j in jalons if jours_restants <= j]
    return min(franchis) if franchis else None
