"""La session ouverte, et pourquoi elle vit côté serveur.

─────────────────────────────────────────────────────────────────────────────────
UN JETON SIGNÉ SEUL NE SE RÉVOQUE PAS

Le réflexe courant est de tout mettre dans un JWT — compte, rôles, expiration — et
de ne rien garder côté serveur. C'est séduisant : aucune lecture, aucune table.
C'est aussi ce qui rend la révocation impossible.

Or la révocation est ici une exigence, pas un confort. Un collaborateur part le
matin ; son accès doit tomber le matin, pas à l'expiration de son jeton. Un
téléphone est perdu ; l'administrateur doit pouvoir couper. Un adhérent résilie ;
il cesse de voir son dossier le jour de la résiliation. Avec un jeton autoportant,
la seule réponse honnête serait « il gardera l'accès jusqu'à ce soir », et ce
n'est pas une réponse acceptable quand ce qui est en jeu est la liasse fiscale
d'un tiers.

La session est donc une **entité, source de vérité**, et le jeton porté par le
navigateur ne fait que la désigner. Le coût est une lecture par requête ; le
bénéfice est qu'un administrateur peut réellement fermer une porte.

LES RÔLES NE SONT PAS DANS LA SESSION

Une session ne mémorise pas les permissions de celui qui l'a ouverte. Elles sont
résolues à chaque contrôle, à la date du jour, depuis les habilitations. Les figer
à l'ouverture ferait qu'un droit retiré à 9 h resterait exercé jusqu'à midi — et
que le journal d'audit enregistrerait des actes conformes à une habilitation qui
n'existait plus.

LE SECOND FACTEUR RENFORCE UNE SESSION, IL NE LA CRÉE PAS

On ne le réclame pas à l'ouverture. Exiger un code à chaque connexion pour un
outil ouvert dix fois par jour conduit à une seule chose : le téléphone reste
posé à côté du clavier, déverrouillé, et le second facteur n'en est plus un.

Il est réclamé **au moment de l'acte sensible**. Présenter un code élève la
session pour quinze minutes — assez pour déposer cinq déclarations à la suite,
assez peu pour qu'un poste laissé ouvert ne puisse pas déposer tout l'après-midi.

`renforcee_jusqu_a` est donc une **date**, pas un drapeau : elle se périme toute
seule, comme le verrou de compte. Rien n'a besoin de venir la lever.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field

__all__ = [
    "DUREE_SESSION",
    "MotifRevocation",
    "Session",
    "SessionInvalide",
]

#: Douze heures : une journée de travail et sa marge. Assez long pour qu'une
#: comptable ne se reconnecte pas trois fois par jour, assez court pour qu'un
#: poste laissé ouvert au cabinet le soir ne serve à personne le lendemain matin.
DUREE_SESSION = timedelta(hours=12)


class MotifRevocation(StrEnum):
    """Pourquoi une session a été fermée.

    Distinguer une déconnexion volontaire d'une révocation administrative n'est
    pas cosmétique : la seconde figure au journal d'audit comme un acte posé par
    quelqu'un, et se relit lors d'un incident.
    """

    DECONNEXION = "DECONNEXION"
    EXPIRATION = "EXPIRATION"
    REVOCATION_ADMINISTRATIVE = "REVOCATION_ADMINISTRATIVE"
    CHANGEMENT_MOT_DE_PASSE = "CHANGEMENT_MOT_DE_PASSE"
    SUSPENSION_DU_COMPTE = "SUSPENSION_DU_COMPTE"


class SessionInvalide(RuntimeError):
    """La session n'existe pas, a expiré, ou a été révoquée."""


class Session(BaseModel):
    """Un accès ouvert, révocable à tout instant."""

    model_config = ConfigDict(frozen=True)

    identifiant: str = Field(min_length=1)
    compte: str = Field(min_length=1)
    locataire: str = Field(min_length=1)

    ouverte_le: datetime
    expire_le: datetime
    revoquee_le: datetime | None = None
    motif_revocation: MotifRevocation | None = None

    #: Conservés pour le journal d'audit, qui les réclame nommément
    #: (05-securite-multitenant.md § 3). Une connexion depuis une adresse
    #: inhabituelle est la première chose qu'on regarde après un incident.
    adresse_ip: str | None = None
    agent: str | None = None

    #: Jusqu'à quand la session est renforcée par un second facteur. `None` =
    #: jamais renforcée. Voir l'en-tête.
    renforcee_jusqu_a: datetime | None = None

    @computed_field
    @property
    def revoquee(self) -> bool:
        return self.revoquee_le is not None

    def active(self, a_l_instant: datetime) -> bool:
        return not self.revoquee and a_l_instant < self.expire_le

    def renforcee(self, a_l_instant: datetime) -> bool:
        """La session porte-t-elle un second facteur encore valide ?

        Résolue à l'instant, jamais mémorisée : une session renforcée à 9 h ne
        doit pas déposer à 14 h sur la foi d'un booléen figé.
        """
        return self.renforcee_jusqu_a is not None and a_l_instant < self.renforcee_jusqu_a

    def renforcer(self, a_l_instant: datetime, duree: timedelta) -> Session:
        return self.model_copy(update={"renforcee_jusqu_a": a_l_instant + duree})

    def exiger_active(self, a_l_instant: datetime) -> None:
        if self.revoquee:
            raise SessionInvalide(
                f"session {self.identifiant} révoquée le {self.revoquee_le} "
                f"({self.motif_revocation})"
            )
        if a_l_instant >= self.expire_le:
            raise SessionInvalide(f"session {self.identifiant} expirée le {self.expire_le}")

    def revoquer(self, a_l_instant: datetime, motif: MotifRevocation) -> Session:
        """Ferme la session. Une session déjà révoquée le reste, sans erreur :
        révoquer deux fois est le résultat attendu, pas une anomalie."""
        if self.revoquee:
            return self
        return self.model_copy(
            update={"revoquee_le": a_l_instant, "motif_revocation": motif}
        )
