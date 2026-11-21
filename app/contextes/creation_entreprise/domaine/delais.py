"""Les délais de la création, et leurs dépassements (lot 4, 9 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LA RESPONSABLE A DEMANDÉ (réunion du 4 novembre 2026, demandes 12 et 13)

« Une fois le paiement reçu, le montage des statuts commence, avec un délai défini pour les
monter et les remettre. » « Il faut aussi un délai de validation des statuts : certains
clients laissent passer des mois. Il faut des délais clairs et, au besoin, des pénalités.
Cela nous oblige aussi à être efficaces. » « Fixer un délai de retrait des documents ; les
originaux sont scannés puis conservés par le CGA. »

TROIS DÉLAIS, TOUS DU CABINET (`RegleDesDelaisDeCreation`)

* RÉDACTION : du début de la constitution aux statuts transmis au client — le cabinet ;
* VALIDATION : des statuts transmis aux statuts signés reçus — le client ;
* RETRAIT : des originaux prêts (étape « Remise ») à leur remise — le client.

⚠️ AUCUNE DURÉE N'EST ÉCRITE ICI, ET AUCUNE PAR DÉFAUT. Un délai que la direction n'a pas
fixé ne se suit pas : la réunion n'en a donné aucun, et un délai inventé ferait relancer ou
pénaliser à tort. La pénalité, elle, se DÉCIDE par un responsable (voir la souscription) :
le système signale le dépassement, il ne sanctionne pas seul.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DelaiDepasse",
    "NatureDuDelai",
    "REGLE_SANS_DELAIS",
    "RegleDesDelaisDeCreation",
    "delais_depasses",
]


class RegleDesDelaisDeCreation(BaseModel):
    model_config = ConfigDict(frozen=True)

    delai_redaction_statuts_jours: int | None = Field(default=None, ge=1, le=365)
    delai_validation_statuts_jours: int | None = Field(default=None, ge=1, le=365)
    delai_retrait_originaux_jours: int | None = Field(default=None, ge=1, le=730)
    motif: str = Field(min_length=5, max_length=500)
    fixee_par: str = Field(min_length=1)
    fixee_le: datetime


#: Rien de fixé : rien ne se suit.
REGLE_SANS_DELAIS = RegleDesDelaisDeCreation(
    motif="aucun délai fixé par la direction",
    fixee_par="systeme",
    fixee_le=datetime(2026, 11, 9),
)


class NatureDuDelai(StrEnum):
    #: Le cabinet : statuts à rédiger et transmettre.
    REDACTION_STATUTS = "REDACTION_STATUTS"
    #: Le client : statuts à valider et signer.
    VALIDATION_STATUTS = "VALIDATION_STATUTS"
    #: Le client : originaux à retirer au cabinet.
    RETRAIT_ORIGINAUX = "RETRAIT_ORIGINAUX"


class DelaiDepasse(BaseModel):
    model_config = ConfigDict(frozen=True)

    nature: NatureDuDelai
    #: Qui doit agir : le cabinet, ou le client.
    du_fait_du_client: bool
    libelle: str
    depuis: date
    echeance: date
    jours_de_retard: int = Field(ge=1)


def delais_depasses(
    dossier, regle: RegleDesDelaisDeCreation, a_la_date: date
) -> list[DelaiDepasse]:
    """Les délais dépassés de ce dossier de création. Pur : il relit, il ne décide rien."""
    from app.contextes.creation_entreprise.domaine.entites import EtapeCreation

    if dossier.clos:
        return []
    sortie: list[DelaiDepasse] = []

    def depasse(nature, client, libelle, depuis, delai):
        if depuis is None or delai is None:
            return
        echeance = depuis + timedelta(days=delai)
        if a_la_date > echeance:
            sortie.append(
                DelaiDepasse(
                    nature=nature,
                    du_fait_du_client=client,
                    libelle=libelle,
                    depuis=depuis,
                    echeance=echeance,
                    jours_de_retard=(a_la_date - echeance).days,
                )
            )

    statuts_recus = any(p.code == "STATUTS" and p.fournie for p in dossier.pieces)
    if dossier.etape is EtapeCreation.CONSTITUTION and dossier.statuts_transmis_le is None:
        depasse(
            NatureDuDelai.REDACTION_STATUTS,
            False,
            "Statuts à rédiger et transmettre au client",
            dossier.depuis_le(EtapeCreation.CONSTITUTION),
            regle.delai_redaction_statuts_jours,
        )
    if dossier.statuts_transmis_le is not None and not statuts_recus:
        depasse(
            NatureDuDelai.VALIDATION_STATUTS,
            True,
            "Statuts transmis, pas encore validés ni signés par le client",
            dossier.statuts_transmis_le,
            regle.delai_validation_statuts_jours,
        )
    if dossier.etape is EtapeCreation.LIVRAISON and dossier.originaux_remis_le is None:
        depasse(
            NatureDuDelai.RETRAIT_ORIGINAUX,
            True,
            "Originaux prêts, pas encore retirés par le client",
            dossier.depuis_le(EtapeCreation.LIVRAISON),
            regle.delai_retrait_originaux_jours,
        )
    return sortie
