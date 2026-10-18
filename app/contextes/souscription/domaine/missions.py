"""La mission : ce qui suit la vente d'une prestation ponctuelle, jusqu'au livrable.

─────────────────────────────────────────────────────────────────────────────────
CE QUI MANQUAIT (15 octobre 2026)

Le catalogue vendait une « prestation ponctuelle » chiffrée sur étude ; après le
paiement, rien ne s'ouvrait : ni mission, ni livrable. Le cabinet a demandé
l'analyse et la rédaction de contrats, et la DSF ciblée, « avec des outils et un
agent, l'humain dans la boucle ».

L'AGENT PROPOSE, LE JURISTE DÉCIDE — ET C'EST UNE RÈGLE DU DOMAINE, PAS DE L'ÉCRAN

Chaque point que la lecture automatique relève (clause à risque, obligation,
manque, recommandation) entre comme une PROPOSITION en attente. Le juriste la
retient, la modifie ou l'écarte avec un motif. Une mission ne se soumet pas tant
qu'une proposition attend ; le livrable ne porte que ce qui a été retenu, dans les
mots que le juriste a validés. Le cahier des charges le pose pour l'extraction :
« la validation demeure humaine en toute hypothèse ».

DEUX REGARDS AVANT LE CLIENT

Le juriste soumet ; un second (réviseur ou direction) valide, jamais celui qui a
soumis. Alors seulement la mission est livrée.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.partage.copie import transiter

__all__ = [
    "CONSIGNES_AU_JURISTE",
    "MENTION_A_COMPLETER",
    "DecisionProposition",
    "EtatMission",
    "Gravite",
    "LectureAutomatique",
    "Mission",
    "OrigineProposition",
    "Proposition",
    "Rubrique",
    "TransitionMissionRefusee",
    "TypeMission",
]


class TransitionMissionRefusee(RuntimeError):
    """Le geste n'est pas permis dans l'état de la mission : le message dit pourquoi."""


class TypeMission(StrEnum):
    ANALYSE_CONTRAT = "ANALYSE_CONTRAT"
    REDACTION_CONTRAT = "REDACTION_CONTRAT"
    DSF_CIBLEE = "DSF_CIBLEE"


LIBELLES_TYPE = {
    TypeMission.ANALYSE_CONTRAT: "Analyse de contrat",
    TypeMission.REDACTION_CONTRAT: "Rédaction de contrat",
    TypeMission.DSF_CIBLEE: "DSF ciblée",
}


class EtatMission(StrEnum):
    #: Ouverte : le document est là, les propositions s'instruisent.
    OUVERTE = "OUVERTE"
    #: Soumise par le juriste, toutes propositions décidées : au second regard.
    A_VALIDER = "A_VALIDER"
    #: Validée par un second : le livrable est remis au client.
    LIVREE = "LIVREE"


class Rubrique(StrEnum):
    RESUME = "RESUME"
    PARTIES = "PARTIES"
    OBLIGATION = "OBLIGATION"
    CLAUSE_A_RISQUE = "CLAUSE_A_RISQUE"
    MANQUE = "MANQUE"
    RECOMMANDATION = "RECOMMANDATION"
    #: Un article d'un projet de contrat (rédaction).
    ARTICLE = "ARTICLE"


class Gravite(StrEnum):
    FAIBLE = "FAIBLE"
    MOYENNE = "MOYENNE"
    ELEVEE = "ELEVEE"


#: Ce que l'agent et les modèles écrivent là où une donnée manque.
MENTION_A_COMPLETER = "[À COMPLÉTER"

#: 18 octobre 2026 : ce qui, dans un texte retenu, s'adresse au juriste et non au client.
#: Les modèles de contrat signalent ainsi une règle légale à vérifier ; retenue telle
#: quelle, la consigne partirait dans le contrat du client.
CONSIGNES_AU_JURISTE = (MENTION_A_COMPLETER, "à vérifier par le juriste")


class OrigineProposition(StrEnum):
    AGENT = "AGENT"
    COLLABORATEUR = "COLLABORATEUR"
    #: 18 octobre 2026 (point 23) : un article type, versé depuis un modèle de contrat.
    MODELE = "MODELE"


class DecisionProposition(StrEnum):
    EN_ATTENTE = "EN_ATTENTE"
    RETENUE = "RETENUE"
    MODIFIEE = "MODIFIEE"
    ECARTEE = "ECARTEE"


class Proposition(BaseModel):
    """Un point de l'analyse, proposé puis décidé."""

    model_config = ConfigDict(frozen=True)

    rang: int = Field(ge=1)
    rubrique: Rubrique
    titre: str = Field(min_length=1, max_length=200)
    texte: str = Field(min_length=1, max_length=4000)
    #: L'extrait du contrat sur lequel le point repose, mot pour mot. ⚠️ Rendu par
    #: l'agent : le juriste le vérifie sur le document avant de retenir.
    citation: str | None = Field(default=None, max_length=2000)
    gravite: Gravite | None = None
    origine: OrigineProposition
    decision: DecisionProposition = DecisionProposition.EN_ATTENTE
    #: Le texte que le juriste fait sien (identique ou modifié) ; c'est lui qui part.
    texte_retenu: str | None = Field(default=None, max_length=4000)
    motif_ecart: str | None = Field(default=None, max_length=1000)
    decide_par: str | None = None
    decide_le: datetime | None = None

    @model_validator(mode="after")
    def _decision_complete(self) -> Proposition:
        if self.decision is DecisionProposition.EN_ATTENTE:
            return self
        if not (self.decide_par and self.decide_le):
            raise ValueError(f"proposition {self.rang} : une décision porte son auteur.")
        if self.decision in (DecisionProposition.RETENUE, DecisionProposition.MODIFIEE):
            if not self.texte_retenu:
                raise ValueError(f"proposition {self.rang} : retenue sans texte retenu.")
        if self.decision is DecisionProposition.ECARTEE and not self.motif_ecart:
            raise ValueError(f"proposition {self.rang} : écartée sans motif.")
        return self

    @property
    def retenue(self) -> bool:
        return self.decision in (DecisionProposition.RETENUE, DecisionProposition.MODIFIEE)


class LectureAutomatique(BaseModel):
    """Une lecture par l'agent : quand, par quel moteur, et ce qui a pu l'empêcher."""

    model_config = ConfigDict(frozen=True)

    moteur: str
    faite_le: datetime
    demandee_par: str
    propositions: int = 0
    incident: str | None = None


class Mission(BaseModel):
    model_config = ConfigDict(frozen=True)

    identifiant: str = Field(min_length=1)
    type: TypeMission
    client: str = Field(min_length=1, max_length=255)
    client_courriel: str | None = None
    #: Le dossier commercial payé d'où vient la mission, quand elle vient d'une vente.
    reference_commerciale: str | None = None
    objet: str = Field(min_length=1, max_length=500)
    responsable: str = Field(min_length=1)
    ouverte_le: datetime
    etat: EtatMission = EtatMission.OUVERTE

    #: Le document à analyser, au magasin de fichiers.
    document_empreinte: str | None = None
    document_nom: str | None = None

    #: Rédaction : le cahier du juriste (parties, objet, prix, durée, particularités).
    cahier: str | None = Field(default=None, max_length=8000)
    #: Rédaction : le type de contrat (« bail à usage professionnel », « prestation de
    #: services »…), qui titre le projet.
    type_de_contrat: str | None = Field(default=None, max_length=200)
    #: DSF ciblée : le dossier et l'exercice dont la DSF est l'objet de la mission ; le
    #: travail se fait dans le parcours DSF (préparée, validée, déposée).
    dossier_niu: str | None = None
    exercice: str | None = None

    propositions: tuple[Proposition, ...] = ()
    lectures: tuple[LectureAutomatique, ...] = ()

    soumise_par: str | None = None
    soumise_le: datetime | None = None
    validee_par: str | None = None
    validee_le: datetime | None = None
    #: Le dernier renvoi du second regard, et pourquoi.
    renvoi: str | None = None

    @model_validator(mode="after")
    def _etats_coherents(self) -> Mission:
        if self.etat in (EtatMission.A_VALIDER, EtatMission.LIVREE) and not (
            self.soumise_par and self.soumise_le
        ):
            raise ValueError(f"{self.identifiant} : une mission soumise porte son auteur.")
        if self.etat is EtatMission.LIVREE and not (self.validee_par and self.validee_le):
            raise ValueError(f"{self.identifiant} : une mission livrée porte son valideur.")
        rangs = [p.rang for p in self.propositions]
        if len(rangs) != len(set(rangs)):
            raise ValueError(f"{self.identifiant} : deux propositions au même rang.")
        return self

    # ── Lire ─────────────────────────────────────────────────────────────────

    @property
    def en_attente(self) -> tuple[Proposition, ...]:
        return tuple(p for p in self.propositions if p.decision is DecisionProposition.EN_ATTENTE)

    @property
    def retenues(self) -> tuple[Proposition, ...]:
        return tuple(p for p in self.propositions if p.retenue)

    def _exiger_ouverte(self, geste: str) -> None:
        if self.etat is not EtatMission.OUVERTE:
            raise TransitionMissionRefusee(
                f"{self.identifiant} est {self.etat.value.lower().replace('_', ' ')} : "
                f"{geste} n'est possible que sur une mission ouverte."
            )

    # ── Les gestes ───────────────────────────────────────────────────────────

    def joindre(self, *, empreinte: str, nom: str) -> Mission:
        self._exiger_ouverte("joindre un document")
        return transiter(self, document_empreinte=empreinte, document_nom=nom)

    def ecrire_le_cahier(self, *, type_de_contrat: str, cahier: str) -> Mission:
        if self.type is not TypeMission.REDACTION_CONTRAT:
            raise TransitionMissionRefusee("le cahier de rédaction est celui d'une rédaction.")
        self._exiger_ouverte("écrire le cahier")
        return transiter(self, type_de_contrat=type_de_contrat, cahier=cahier)

    def ajouter(
        self,
        propositions: list[dict],
        *,
        origine: OrigineProposition,
        lecture: LectureAutomatique | None = None,
    ) -> Mission:
        """Ajoute des propositions EN ATTENTE, à la suite des rangs existants."""
        self._exiger_ouverte("ajouter une proposition")
        depart = max((p.rang for p in self.propositions), default=0)
        nouvelles = tuple(
            Proposition(rang=depart + i, origine=origine, **p)
            for i, p in enumerate(propositions, start=1)
        )
        return transiter(
            self,
            propositions=(*self.propositions, *nouvelles),
            lectures=(*self.lectures, lecture) if lecture else self.lectures,
        )

    def decider(
        self,
        rang: int,
        *,
        decision: DecisionProposition,
        par: str,
        a_l_instant: datetime,
        texte_retenu: str | None = None,
        motif: str | None = None,
    ) -> Mission:
        """Retenir, modifier ou écarter une proposition. Une décision se reprend tant
        que la mission est ouverte : c'est le juriste qui instruit."""
        self._exiger_ouverte("décider d'une proposition")
        if decision is DecisionProposition.EN_ATTENTE:
            raise TransitionMissionRefusee("une décision retient, modifie ou écarte.")
        cible = next((p for p in self.propositions if p.rang == rang), None)
        if cible is None:
            raise TransitionMissionRefusee(f"{self.identifiant} n'a pas de proposition {rang}.")
        if decision is DecisionProposition.RETENUE:
            texte_retenu = cible.texte
        decidee = transiter(
            cible,
            decision=decision,
            texte_retenu=texte_retenu if decision is not DecisionProposition.ECARTEE else None,
            motif_ecart=motif if decision is DecisionProposition.ECARTEE else None,
            decide_par=par,
            decide_le=a_l_instant,
        )
        return transiter(
            self,
            propositions=tuple(decidee if p.rang == rang else p for p in self.propositions),
        )

    def reaffecter(self, responsable: str) -> Mission:
        """Confier la mission à un autre collaborateur (18 octobre 2026, point 22).

        ⚠️ Pas une mission livrée : son livrable est parti, et qui l'a instruite doit le
        rester au dossier. Le motif et l'auteur du geste vont au journal d'audit."""
        if self.etat is EtatMission.LIVREE:
            raise TransitionMissionRefusee("la mission est livrée : elle ne change plus de main.")
        if responsable == self.responsable:
            raise TransitionMissionRefusee("ce collaborateur est déjà responsable de la mission.")
        return transiter(self, responsable=responsable)

    def soumettre(self, *, par: str, a_l_instant: datetime) -> Mission:
        self._exiger_ouverte("soumettre")
        if self.en_attente:
            rangs = ", ".join(str(p.rang) for p in self.en_attente)
            raise TransitionMissionRefusee(
                f"{len(self.en_attente)} proposition(s) attendent une décision ({rangs}) : "
                "rien ne part au client sans que le juriste l'ait retenu ou écarté."
            )
        if not self.retenues:
            raise TransitionMissionRefusee("aucune proposition retenue : le livrable serait vide.")
        # 18 octobre 2026 (point 23) : l'agent et les modèles laissent « [À COMPLÉTER : …] »
        # là où le cahier ne disait rien. Retenue telle quelle, la mention partirait au client.
        a_completer = [
            str(p.rang)
            for p in self.retenues
            if any(c.lower() in (p.texte_retenu or "").lower() for c in CONSIGNES_AU_JURISTE)
        ]
        if a_completer:
            raise TransitionMissionRefusee(
                f"des articles portent encore « {MENTION_A_COMPLETER} » ou une consigne au "
                f"juriste ({', '.join(a_completer)}) : reformulez-les avec la donnée du client."
            )
        return transiter(
            self, etat=EtatMission.A_VALIDER, soumise_par=par, soumise_le=a_l_instant, renvoi=None
        )

    def valider(self, *, par: str, a_l_instant: datetime) -> Mission:
        if self.etat is not EtatMission.A_VALIDER:
            raise TransitionMissionRefusee(
                f"{self.identifiant} n'est pas soumise : seule une mission soumise se valide."
            )
        if par == self.soumise_par:
            raise TransitionMissionRefusee(
                f"{self.identifiant} a été soumise par vous : la validation est le regard "
                "d'un autre."
            )
        return transiter(self, etat=EtatMission.LIVREE, validee_par=par, validee_le=a_l_instant)

    def renvoyer(self, *, motif: str) -> Mission:
        if self.etat is not EtatMission.A_VALIDER:
            raise TransitionMissionRefusee(f"{self.identifiant} n'est pas soumise.")
        return transiter(
            self, etat=EtatMission.OUVERTE, soumise_par=None, soumise_le=None, renvoi=motif
        )
