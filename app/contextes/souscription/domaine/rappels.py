"""Le carnet des rappels à passer : ce que la machine confie à un humain.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE CARNET EXISTE, ALORS QUE LA VEILLE EXISTE DÉJÀ

Le dossier commercial sait dire qu'il est **en souffrance** : resté trop longtemps
dans le même état. C'est une alerte de flux, et elle répond à « qu'est-ce qui est
bloqué ».

Un rappel répond à autre chose : « le système a décidé de vous confier ce
contact-ci, pour ce motif-là ». Un client sans courriel et sans consentement à la
messagerie ne sera jamais joignable autrement qu'au téléphone, et son dossier peut
n'être en souffrance nulle part — la relance est due, elle n'a simplement aucun
canal automatique.

⚠️ **Sans ce carnet, le repli par appel ne serait pas un repli.** Il serait un
silence : la relance serait comptée comme remise, et personne n'appellerait. Le
plancher du plan de contact deviendrait un trou.

CE QU'UN RAPPEL N'EST PAS

Ce n'est pas un journal d'appels. `AppelJournalise`, dans la conversation, note un
appel qui **a eu lieu** : sa durée, son issue. Un rappel note un appel **à passer**.
Les deux se suivent dans le temps et ne se confondent pas : l'un est une intention,
l'autre un fait.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.partage.copie import transiter

__all__ = ["RappelAPasser", "RappelDejaFait"]


class RappelDejaFait(ValueError):
    """On ne clôt pas deux fois le même rappel."""


class RappelAPasser(BaseModel):
    """Un contact que la machine n'a pas su prendre, et qu'elle confie.

    ⚠️ **Il porte son motif en clair.** Un carnet qui listerait des références de
    dossier sans dire pourquoi obligerait le responsable à rouvrir chaque dossier
    pour comprendre ce qu'on attend de lui. Il cesserait de le lire, et le plancher
    du plan de contact redeviendrait un trou.
    """

    model_config = ConfigDict(frozen=True)

    identifiant: str = Field(min_length=1, max_length=64)
    dossier: str = Field(min_length=1, max_length=64)
    #: Ce qu'on attend, en une phrase lisible par un humain pressé.
    motif: str = Field(min_length=1, max_length=300)
    cree_le: datetime
    #: `None` tant que personne n'a rappelé.
    fait_le: datetime | None = None
    #: Qui a rappelé. ⚠️ Obligatoire dès que le rappel est fait : un carnet où l'on
    #: peut clore sans se nommer ne dit plus qui a parlé au client.
    fait_par: str | None = None

    @property
    def en_attente(self) -> bool:
        return self.fait_le is None

    @model_validator(mode="after")
    def _fait_par_accompagne_fait_le(self) -> RappelAPasser:
        if (self.fait_le is None) != (self.fait_par is None):
            raise ValueError(
                f"rappel {self.identifiant} : « fait_le » et « fait_par » vont "
                "ensemble. Un rappel clos sans nom ne dit plus qui a parlé au "
                "client, et un nom sans date ne dit pas quand."
            )
        return self

    def fait(self, par: str, a_l_instant: datetime) -> RappelAPasser:
        """Clôt le rappel. **Refuse le second appel**, jamais silencieusement.

        ⚠️ Rendre `self` sur un rappel déjà clos ferait qu'un second collaborateur
        croirait avoir pris le contact alors que le premier l'avait pris. Deux
        appels au même client, à quelques minutes, sur le même sujet.
        """
        if self.fait_le is not None:
            raise RappelDejaFait(
                f"rappel {self.identifiant} déjà clos par {self.fait_par} le "
                f"{self.fait_le:%d/%m/%Y à %H:%M}. Un second appel au même client "
                "sur le même sujet ne se rattrape pas."
            )
        if not par.strip():
            raise ValueError("clore un rappel demande de se nommer")
        return transiter(self, fait_le=a_l_instant, fait_par=par)
