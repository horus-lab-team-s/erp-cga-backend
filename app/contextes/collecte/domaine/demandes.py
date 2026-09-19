"""La demande de pièce, et sa relance.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE MODULE PROTÈGE

Le cabinet ne peut relancer que ce qu'il sait attendre. Une demande de pièce est
donc l'acte par lequel une attente devient explicite et opposable : sans elle,
« l'adhérent n'a rien envoyé » et « le cabinet n'a rien demandé » sont
indiscernables — et c'est le cabinet qui perd cette discussion, parce que c'est lui
le professionnel.

**Chaque relance est tracée avec sa date et son canal.** Le jour où un adhérent
conteste une pénalité de retard, la liste des relances est la défense du Centre.
Une relance non tracée n'a pas eu lieu.

**Une demande satisfaite ne se relance jamais.** C'est la première cause
d'exaspération d'un adhérent, et elle décrédibilise toutes les relances suivantes,
y compris celles qui étaient justifiées.

LES JALONS SONT DES JOURS PLEINS APRÈS LA DEMANDE

J+7, J+15, J+30, puis escalade. Le troisième jalon n'est pas une quatrième
relance polie : passé un mois, ce n'est plus un oubli, c'est un dossier qui
n'avance pas, et cela devient une affaire de responsable de portefeuille et non
plus d'automate.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from app.contextes.collecte.domaine.pieces import CanalDepot, TypePiece
from app.partage.copie import transiter

__all__ = [
    "JALONS_RELANCE_PIECE",
    "SEUIL_ESCALADE_JOURS",
    "DemandePiece",
    "NatureDeReponse",
    "RelancePiece",
    "ReponseDeLAdherent",
    "StatutDemande",
    "relances_du_jour",
]

#: Jours pleins après l'émission de la demande.
JALONS_RELANCE_PIECE = (7, 15, 30)

#: Au-delà, la demande cesse d'être un rappel automatique et devient un point de
#: gestion humain.
SEUIL_ESCALADE_JOURS = 30


class StatutDemande(StrEnum):
    OUVERTE = "OUVERTE"
    SATISFAITE = "SATISFAITE"
    #: Abandonnée avec motif : la pièce n'existe pas, ou l'opération est annulée.
    CLASSEE_SANS_SUITE = "CLASSEE_SANS_SUITE"


class NatureDeReponse(StrEnum):
    """Ce que l'adhérent répond à une demande du cabinet (pas 112, maquette « Espace adhérent », vue C).

    Deux réponses toutes faites et un message libre : « trois réponses toutes faites couvrent 90 %
    des cas et évitent la frappe au clavier sur un téléphone d'entrée de gamme ». La troisième
    réponse toute faite de la maquette, « Envoyer le relevé », n'est pas une réponse : c'est le
    dépôt de la pièce, qui existe déjà (pas 81).
    """

    #: « Je l'aurai la semaine prochaine » : l'adhérent annonce une date.
    PLUS_TARD = "PLUS_TARD"
    #: « Je n'ai pas ce document » : le cabinet décide ensuite (classer, ou chercher autrement).
    INTROUVABLE = "INTROUVABLE"
    #: Un message écrit par l'adhérent.
    MESSAGE = "MESSAGE"


class ReponseDeLAdherent(BaseModel):
    """Une réponse de l'adhérent, datée et nommée.

    ⚠️ **Une réponse ne ferme jamais la demande.** « Je n'ai pas ce document » ne dit pas que la
    pièce n'existe pas : elle dit que l'adhérent ne l'a pas sous la main. C'est le cabinet qui
    classe sans suite, avec son motif (pas 74), ou qui cherche la pièce ailleurs (le fournisseur,
    la banque). Une demande close par l'adhérent ne serait plus relancée, et la déclaration
    partirait sans la pièce.
    """

    model_config = ConfigDict(frozen=True)

    nature: NatureDeReponse
    #: Obligatoire pour un message, facultatif pour une réponse toute faite (une précision).
    message: str | None = Field(default=None, max_length=1000)
    le: datetime
    #: Le compte de l'adhérent qui répond. Un collaborateur ne répond pas à sa place.
    par: str = Field(min_length=1)
    #: Pour « plus tard » : le jour annoncé, calculé par le référentiel (« la semaine prochaine »).
    annoncee_pour: date | None = None

    @field_validator("message", mode="before")
    @classmethod
    def _sans_blancs(cls, message: object) -> object:
        # Un message fait d'espaces n'est pas un message : il devient `None`, et le contrôle
        # ci-dessous le refuse pour une réponse MESSAGE.
        if isinstance(message, str):
            return message.strip() or None
        return message

    @model_validator(mode="after")
    def _coherence(self) -> ReponseDeLAdherent:
        if self.nature is NatureDeReponse.MESSAGE and self.message is None:
            raise ValueError("un message au cabinet ne peut pas être vide.")
        if self.nature is NatureDeReponse.PLUS_TARD:
            if self.annoncee_pour is None or self.annoncee_pour < self.le.date():
                raise ValueError(
                    "« plus tard » annonce un jour qui n'est pas encore passé : sans lui, le "
                    "cabinet ne sait pas quand relancer."
                )
        elif self.annoncee_pour is not None:
            raise ValueError(f"une réponse {self.nature.value} n'annonce pas de date.")
        return self


class DemandePiece(BaseModel):
    """Une pièce que le cabinet attend d'un adhérent."""

    model_config = ConfigDict(frozen=True)

    identifiant: str = Field(min_length=1)
    entreprise: str = Field(min_length=1)
    type_attendu: TypePiece
    motif: str = Field(min_length=1)

    demandee_le: date
    #: Date à laquelle la pièce doit être arrivée pour que le traitement en aval
    #: tienne. Calculée à partir de l'échéance déclarative, jamais saisie au
    #: hasard : une date d'attente sans lien avec une échéance n'engage personne.
    attendue_pour: date | None = None

    #: Vraie quand l'absence de la pièce empêche une déclaration. C'est cette
    #: qualité, et non le nombre de pièces manquantes, qui décide si un dossier
    #: peut être déposé.
    bloquante: bool = False

    statut: StatutDemande = StatutDemande.OUVERTE
    #: Pièces reçues en réponse. Une demande peut appeler plusieurs pièces — les
    #: relevés d'un trimestre, par exemple.
    pieces_recues: tuple[str, ...] = ()
    satisfaite_le: date | None = None
    motif_classement: str | None = None
    #: Le jour du classement sans suite (pas 74). Sans lui, un classement ne se date
    #: pas, et « depuis quand ne l'attend-on plus » n'a pas de réponse.
    classee_le: date | None = None

    #: La pièce dont on attend la rectification, quand c'en est une (pas 74).
    #: ⚠️ Nommée plutôt que citée dans le motif : c'est ce qui permet de refuser une
    #: seconde demande ouverte sur la même pièce, et de refuser qu'elle se satisfasse
    #: par la facture même qu'elle doit remplacer.
    piece_a_rectifier: str | None = None
    #: Qui a émis la demande. Une attente opposable engage quelqu'un.
    demandee_par: str | None = None

    #: Historique des relances émises : (date, canal). C'est la trace opposable.
    relances: tuple[tuple[date, CanalDepot], ...] = ()

    #: Les réponses de l'adhérent (pas 112), dans l'ordre. Conservées toutes : « il avait dit la
    #: semaine prochaine, il y a trois semaines » est une information pour la relance suivante.
    reponses: tuple[ReponseDeLAdherent, ...] = ()

    @model_validator(mode="after")
    def _coherence(self) -> DemandePiece:
        if self.attendue_pour is not None and self.attendue_pour < self.demandee_le:
            raise ValueError(
                f"{self.identifiant} : pièce attendue le {self.attendue_pour}, "
                f"soit avant d'avoir été demandée le {self.demandee_le}."
            )
        if self.statut is StatutDemande.SATISFAITE and self.satisfaite_le is None:
            raise ValueError(f"{self.identifiant} : une demande satisfaite porte sa date.")
        if self.statut is StatutDemande.CLASSEE_SANS_SUITE and not self.motif_classement:
            raise ValueError(
                f"{self.identifiant} : classer une demande sans suite exige un motif. "
                "C'est ce qui distingue une décision d'un abandon, et le vérificateur "
                "posera la question."
            )
        return self

    @computed_field
    @property
    def ouverte(self) -> bool:
        return self.statut is StatutDemande.OUVERTE

    @computed_field
    @property
    def derniere_reponse(self) -> ReponseDeLAdherent | None:
        return self.reponses[-1] if self.reponses else None

    def repondre(self, reponse: ReponseDeLAdherent) -> DemandePiece:
        """L'adhérent répond (pas 112). Refusé sur une demande qui n'attend plus rien, et en double.

        ⚠️ **Deux fois la même réponse le même jour est un double appui**, pas une seconde réponse :
        sur un réseau lent, l'adhérent appuie deux fois, et le cabinet recevrait deux avis.
        """
        if not self.ouverte:
            raise ValueError(
                f"{self.identifiant} : demande {self.statut}, le cabinet n'attend plus cette "
                "pièce. Votre réponse n'est pas nécessaire."
            )
        if reponse.le.date() < self.demandee_le:
            raise ValueError(
                f"{self.identifiant} : réponse du {reponse.le:%d/%m/%Y}, avant la demande du "
                f"{self.demandee_le:%d/%m/%Y}."
            )
        derniere = self.derniere_reponse
        if (
            derniere is not None
            and derniere.le.date() == reponse.le.date()
            and derniere.nature is reponse.nature
            and derniere.message == reponse.message
        ):
            raise ValueError(
                f"{self.identifiant} : cette réponse a déjà été reçue aujourd'hui par le cabinet."
            )
        return transiter(self, reponses=(*self.reponses, reponse))

    def anciennete(self, a_la_date: date) -> int:
        return (a_la_date - self.demandee_le).days

    def en_retard(self, a_la_date: date) -> bool:
        """Calculé, jamais stocké — même discipline qu'au contexte F."""
        return (
            self.ouverte
            and self.attendue_pour is not None
            and a_la_date > self.attendue_pour
        )

    def a_escalader(self, a_la_date: date) -> bool:
        return self.ouverte and self.anciennete(a_la_date) >= SEUIL_ESCALADE_JOURS

    def satisfaire(self, piece: str, le: date) -> DemandePiece:
        """⚠️ Pas 74 : refusée sur une demande qui n'est plus ouverte, et par la pièce
        même qu'elle doit remplacer. Avant, satisfaire deux fois écrasait la date et
        ajoutait une seconde pièce, et une facture pouvait « rectifier » sa propre erreur.
        """
        if not self.ouverte:
            raise ValueError(
                f"{self.identifiant} : demande {self.statut}, elle ne se satisfait plus."
            )
        if piece == self.piece_a_rectifier:
            raise ValueError(
                f"{self.identifiant} : la pièce {piece} est celle qu'il faut rectifier. "
                "Elle ne peut pas répondre à sa propre demande de rectification."
            )
        if le < self.demandee_le:
            raise ValueError(
                f"{self.identifiant} : satisfaite le {le:%d/%m/%Y}, avant d'avoir été "
                f"demandée le {self.demandee_le:%d/%m/%Y}."
            )
        return transiter(
            self,
            statut=StatutDemande.SATISFAITE,
            pieces_recues=(*self.pieces_recues, piece),
            satisfaite_le=le,
        )

    def relancer(self, le: date, canal: CanalDepot) -> DemandePiece:
        if not self.ouverte:
            raise ValueError(
                f"{self.identifiant} : demande {self.statut}, il n'y a plus rien à "
                "relancer. Relancer une demande satisfaite décrédibilise toutes les "
                "relances suivantes."
            )
        # ⚠️ Pas 74 : une relance tracée est une pièce de défense du Centre. Datée
        # d'avant la demande, ou tracée deux fois le même jour par le même canal, elle
        # affaiblit la liste entière le jour où l'adhérent la conteste.
        if le < self.demandee_le:
            raise ValueError(
                f"{self.identifiant} : relance du {le:%d/%m/%Y}, avant la demande du "
                f"{self.demandee_le:%d/%m/%Y}."
            )
        if (le, canal) in self.relances:
            raise ValueError(
                f"{self.identifiant} : une relance par {canal.value} est déjà tracée le "
                f"{le:%d/%m/%Y}."
            )
        return transiter(self, relances=(*self.relances, (le, canal)))

    def classer_sans_suite(self, motif: str, le: date) -> DemandePiece:
        """Ne plus attendre la pièce, avec un motif (pas 74).

        La pièce n'existe pas, l'opération est annulée, le fournisseur a disparu.
        Le motif est exigé par l'entité : c'est ce qui distingue une décision d'un
        abandon.
        """
        if not self.ouverte:
            raise ValueError(
                f"{self.identifiant} : demande {self.statut}, elle ne se classe plus."
            )
        return transiter(
            self,
            statut=StatutDemande.CLASSEE_SANS_SUITE,
            motif_classement=motif.strip() or None,
            classee_le=le,
        )


class RelancePiece(BaseModel):
    """Une relance à émettre aujourd'hui pour une demande donnée."""

    model_config = ConfigDict(frozen=True)

    demande: DemandePiece
    jalon: int
    canal: CanalDepot

    @computed_field
    @property
    def escalade(self) -> bool:
        return self.jalon >= SEUIL_ESCALADE_JOURS


def relances_du_jour(
    demandes: list[DemandePiece],
    a_la_date: date,
    *,
    jalons: tuple[int, ...] = JALONS_RELANCE_PIECE,
    canal_par_defaut: CanalDepot = CanalDepot.WHATSAPP,
    canal_par_entreprise: dict[str, CanalDepot] | None = None,
) -> list[RelancePiece]:
    """Les relances à émettre ce jour-là, et elles seules.

    Le canal préféré de l'adhérent l'emporte : relancer par courriel un artisan
    qui ne consulte que WhatsApp revient à ne pas relancer, tout en produisant la
    trace qui laissera croire qu'on l'a fait. C'est pire que de ne rien envoyer,
    parce que cela endort la vigilance du cabinet.
    """
    preferences = canal_par_entreprise or {}
    relances: list[RelancePiece] = []
    for demande in demandes:
        if not demande.ouverte:
            continue
        age = demande.anciennete(a_la_date)
        if age in jalons:
            relances.append(
                RelancePiece(
                    demande=demande,
                    jalon=age,
                    canal=preferences.get(demande.entreprise, canal_par_defaut),
                )
            )
    return sorted(relances, key=lambda r: (-r.jalon, r.demande.identifiant))
