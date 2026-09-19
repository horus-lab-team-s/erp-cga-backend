"""La revue d'un mois transmis : les quatre yeux sur la tenue comptable (pas 102).

─────────────────────────────────────────────────────────────────────────────────
LE CIRCUIT, POUR QUI ARRIVE AU CABINET

Le comptable tient les écritures d'un dossier. Quand un mois est prêt, il le **transmet**
au réviseur. Le réviseur ne relit pas tout : il contrôle un **échantillon** que le système
lui propose (les écritures atypiques), et il attache des **remarques** à des objets précis
(une écriture, une pièce, un compte). S'il y a des remarques, il **renvoie** le mois ; le
comptable **répond** à chacune, puis **retransmet**. Le réviseur **clôt** les remarques
satisfaites et **valide** le mois.

    comptable ── transmet ──▶ TRANSMISE ── valide (aucune remarque ouverte) ──▶ VALIDEE
                                 │  ▲
                        renvoie  │  │ retransmet (chaque remarque a une réponse)
                                 ▼  │
                              RENVOYEE

⚠️ UNE REMARQUE EST TOUJOURS RATTACHÉE À UN OBJET

« Revoir les immobilisations » en fin de dossier ne se retrouve pas ; « 2026/AC/000184 :
compte 6011 au lieu de 2441, c'est un investissement » se retrouve sur l'écriture. La
maquette le dit, et l'entité le rend impossible autrement.

⚠️ LES QUATRE YEUX NE SONT PAS UNE POLITESSE

Celui qui a transmis ne valide pas sa propre revue, même s'il détient la permission. Une
revue que son auteur peut valider est une signature que personne n'a relue.

⚠️ L'ÉCHANTILLON EST FIGÉ À LA TRANSMISSION

Il est calculé sur les écritures du mois **au moment où le mois est transmis**, et gardé.
Recalculé à chaque lecture, il changerait sous les yeux du réviseur dès qu'une écriture est
corrigée, et « j'ai contrôlé l'échantillon » ne voudrait plus rien dire. Une retransmission
le recalcule, parce que les écritures ont pu changer : c'est une nouvelle version du mois.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contextes.comptabilite.domaine.entites import EcritureComptable, TypeEcriture
from app.partage.copie import transiter

__all__ = [
    "CritereAtypique",
    "EcritureEchantillonnee",
    "NatureObjet",
    "ObjetDeRemarque",
    "PassageDeRelais",
    "ReglagesDeLEchantillon",
    "Remarque",
    "RevueDeDossier",
    "RevueRefusee",
    "StatutRemarque",
    "StatutRevue",
    "echantillonner",
]


class RevueRefusee(ValueError):
    """Le geste n'est pas permis à ce stade. Le message dit pourquoi, pour l'écran."""


# ── L'échantillon ─────────────────────────────────────────────────────────────


class CritereAtypique(StrEnum):
    #: Un montant rond et élevé (500 000, 1 000 000) : souvent une estimation, un acompte
    #: saisi sans pièce définitive, ou un arrangement.
    MONTANT_ROND = "MONTANT_ROND"
    #: Parmi les plus gros montants du mois : là où une erreur coûte le plus.
    MONTANT_ELEVE = "MONTANT_ELEVE"
    #: Un compte rarement mouvementé dans l'exercice : l'erreur d'imputation se cache là.
    COMPTE_RARE = "COMPTE_RARE"
    #: Une contre-passation : une correction, dont il faut comprendre la cause.
    CONTRE_PASSATION = "CONTRE_PASSATION"


_RAISONS = {
    CritereAtypique.MONTANT_ROND: "montant rond",
    CritereAtypique.MONTANT_ELEVE: "parmi les plus gros montants du mois",
    CritereAtypique.COMPTE_RARE: "compte rarement mouvementé",
    CritereAtypique.CONTRE_PASSATION: "contre-passation",
}


class ReglagesDeLEchantillon(BaseModel):
    """`Docs/referentiel/revue/echantillon.yaml`. Les valeurs par défaut sont sobres."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Un montant est « rond » s'il est multiple de ce pas **et** au moins égal au seuil.
    pas_du_montant_rond: Decimal = Field(Decimal(100000), gt=0)
    seuil_du_montant_rond: Decimal = Field(Decimal(500000), ge=0)
    #: Combien des plus gros montants du mois entrent d'office.
    plus_gros_montants: int = Field(3, ge=0, le=50)
    #: Un compte est « rare » s'il porte au plus ce nombre de lignes dans l'exercice.
    compte_rare_au_plus: int = Field(2, ge=0, le=20)
    #: Le plafond de l'échantillon : au-delà, ce n'est plus un échantillon, c'est une relecture.
    taille_maximum: int = Field(20, ge=1, le=200)
    source: str = "valeurs par défaut"


class EcritureEchantillonnee(BaseModel):
    model_config = ConfigDict(frozen=True)

    ecriture: str
    date: date
    libelle: str
    montant: Decimal
    criteres: list[CritereAtypique]
    #: Les raisons en clair, pour l'écran : « montant rond · compte 6011 rarement mouvementé ».
    raisons: list[str]


def echantillonner(
    du_mois: list[EcritureComptable],
    de_l_exercice: list[EcritureComptable],
    reglages: ReglagesDeLEchantillon,
) -> list[EcritureEchantillonnee]:
    """Les écritures atypiques du mois, les plus signalées d'abord, dans la limite du plafond.

    `de_l_exercice` sert à juger qu'un compte est rare : sur le seul mois, tout compte
    mouvementé une fois paraîtrait rare.
    """
    usages = Counter(ligne.compte for e in de_l_exercice for ligne in e.lignes)
    plus_gros = {
        e.cle
        for e in sorted(du_mois, key=lambda e: (e.montant, e.cle), reverse=True)[
            : reglages.plus_gros_montants
        ]
    }
    retenues = []
    for ecriture in du_mois:
        criteres: list[CritereAtypique] = []
        raisons: list[str] = []
        if (
            ecriture.montant >= reglages.seuil_du_montant_rond
            and ecriture.montant % reglages.pas_du_montant_rond == 0
        ):
            criteres.append(CritereAtypique.MONTANT_ROND)
            raisons.append(_RAISONS[CritereAtypique.MONTANT_ROND])
        if ecriture.cle in plus_gros:
            criteres.append(CritereAtypique.MONTANT_ELEVE)
            raisons.append(_RAISONS[CritereAtypique.MONTANT_ELEVE])
        rares = sorted(
            {
                l_.compte
                for l_ in ecriture.lignes
                if usages[l_.compte] <= reglages.compte_rare_au_plus
            }
        )
        if rares:
            criteres.append(CritereAtypique.COMPTE_RARE)
            raisons.append(
                f"compte{'s' if len(rares) > 1 else ''} {', '.join(rares)} rarement mouvementé"
                f"{'s' if len(rares) > 1 else ''}"
            )
        if ecriture.type is TypeEcriture.CONTREPASSATION:
            criteres.append(CritereAtypique.CONTRE_PASSATION)
            raisons.append(_RAISONS[CritereAtypique.CONTRE_PASSATION])
        if criteres:
            retenues.append(
                EcritureEchantillonnee(
                    ecriture=ecriture.cle,
                    date=ecriture.date_operation,
                    libelle=ecriture.libelle,
                    montant=ecriture.montant,
                    criteres=criteres,
                    raisons=raisons,
                )
            )
    retenues.sort(key=lambda e: (-len(e.criteres), -e.montant, e.ecriture))
    return retenues[: reglages.taille_maximum]


# ── Les remarques ─────────────────────────────────────────────────────────────


class NatureObjet(StrEnum):
    ECRITURE = "ECRITURE"
    PIECE = "PIECE"
    COMPTE = "COMPTE"


class ObjetDeRemarque(BaseModel):
    model_config = ConfigDict(frozen=True)

    nature: NatureObjet
    #: `2026/AC/000184`, `PJ-2026-0024` ou `6011`.
    reference: str = Field(min_length=1, max_length=64)


class StatutRemarque(StrEnum):
    #: Posée par le réviseur, en attente du comptable.
    OUVERTE = "OUVERTE"
    #: Le comptable a répondu (corrigé, ou expliqué) ; le réviseur doit en juger.
    TRAITEE = "TRAITEE"
    #: Le réviseur est satisfait. Close, jamais effacée.
    CLOSE = "CLOSE"


class Remarque(BaseModel):
    model_config = ConfigDict(frozen=True)

    rang: int = Field(ge=1)
    objet: ObjetDeRemarque
    texte: str = Field(min_length=10, max_length=1000)
    par: str
    le: datetime
    statut: StatutRemarque = StatutRemarque.OUVERTE
    reponse: str | None = None
    repondue_par: str | None = None
    repondue_le: datetime | None = None
    close_par: str | None = None
    close_le: datetime | None = None


# ── La revue ──────────────────────────────────────────────────────────────────


class StatutRevue(StrEnum):
    TRANSMISE = "TRANSMISE"
    RENVOYEE = "RENVOYEE"
    VALIDEE = "VALIDEE"


class PassageDeRelais(BaseModel):
    """Une ligne de l'histoire : qui a fait passer le mois à quel état, et avec quel mot."""

    model_config = ConfigDict(frozen=True)

    statut: StatutRevue
    par: str
    #: Le compte, pour notifier la bonne personne ; `par` est le nom, pour l'écran.
    compte: str
    le: datetime
    message: str | None = None


class RevueDeDossier(BaseModel):
    """Un mois d'un dossier soumis à la revue. Immuable : chaque geste rend une nouvelle revue,
    revalidée par `transiter`."""

    model_config = ConfigDict(frozen=True)

    identifiant: str
    dossier: str
    exercice: str
    du: date
    au: date
    statut: StatutRevue
    #: Le compte et le nom de celui qui a transmis **la première fois** : c'est lui qui
    #: reçoit les renvois, et lui qui ne peut pas valider.
    transmise_par_compte: str
    transmise_par: str
    echantillon: list[EcritureEchantillonnee] = Field(default_factory=list)
    #: Nombre d'écritures du mois à la dernière transmission : « 12 sur 47 ».
    ecritures_du_mois: int = Field(ge=0)
    remarques: list[Remarque] = Field(default_factory=list)
    historique: list[PassageDeRelais] = Field(min_length=1)
    validee_par: str | None = None
    validee_le: datetime | None = None

    @model_validator(mode="after")
    def _coherente(self) -> RevueDeDossier:
        if self.du > self.au:
            raise ValueError("la période de revue commence après sa fin.")
        rangs = [r.rang for r in self.remarques]
        if len(rangs) != len(set(rangs)):
            raise ValueError("deux remarques portent le même rang.")
        if self.statut is StatutRevue.VALIDEE and self.validee_par is None:
            raise ValueError("une revue validée nomme celui qui l'a validée.")
        if self.statut is StatutRevue.VALIDEE and any(
            r.statut is not StatutRemarque.CLOSE for r in self.remarques
        ):
            raise ValueError("une revue validée ne garde aucune remarque ouverte ou à juger.")
        return self

    # ── Lectures ──────────────────────────────────────────────────────────────

    def remarque(self, rang: int) -> Remarque:
        for remarque in self.remarques:
            if remarque.rang == rang:
                return remarque
        raise RevueRefusee(f"la revue n'a pas de remarque {rang}.")

    def compter(self, statut: StatutRemarque) -> int:
        return sum(1 for r in self.remarques if r.statut is statut)

    # ── Gestes ────────────────────────────────────────────────────────────────

    def _passage(self, statut: StatutRevue, par: str, compte: str, le: datetime, message=None):
        return [
            *self.historique,
            PassageDeRelais(statut=statut, par=par, compte=compte, le=le, message=message),
        ]

    def remarquer(
        self, objet: ObjetDeRemarque, texte: str, *, par: str, le: datetime
    ) -> RevueDeDossier:
        if self.statut is not StatutRevue.TRANSMISE:
            raise RevueRefusee(
                "une remarque se pose sur un mois transmis : celui-ci est "
                f"{self.statut.value.lower()}."
            )
        rang = 1 + max((r.rang for r in self.remarques), default=0)
        remarque = Remarque(rang=rang, objet=objet, texte=texte.strip(), par=par, le=le)
        return transiter(self, remarques=[*self.remarques, remarque])

    def renvoyer(
        self, *, par: str, compte: str, le: datetime, message: str | None
    ) -> RevueDeDossier:
        if self.statut is not StatutRevue.TRANSMISE:
            raise RevueRefusee(
                f"seul un mois transmis se renvoie ; celui-ci est {self.statut.value.lower()}."
            )
        if not self.compter(StatutRemarque.OUVERTE):
            raise RevueRefusee(
                "renvoyer sans remarque ouverte ne dit pas au comptable quoi corriger : poser "
                "d'abord les remarques, ou valider."
            )
        return transiter(
            self,
            statut=StatutRevue.RENVOYEE,
            historique=self._passage(StatutRevue.RENVOYEE, par, compte, le, message),
        )

    def repondre(self, rang: int, reponse: str, *, par: str, le: datetime) -> RevueDeDossier:
        if self.statut is not StatutRevue.RENVOYEE:
            raise RevueRefusee(
                "on répond aux remarques d'un mois renvoyé ; celui-ci est "
                f"{self.statut.value.lower()}."
            )
        remarque = self.remarque(rang)
        if remarque.statut is StatutRemarque.CLOSE:
            raise RevueRefusee(f"la remarque {rang} est close : elle n'attend plus de réponse.")
        if len(reponse.strip()) < 10:
            raise RevueRefusee("la réponse compte au moins 10 caractères : dire ce qui a été fait.")
        repondue = transiter(
            remarque,
            statut=StatutRemarque.TRAITEE,
            reponse=reponse.strip(),
            repondue_par=par,
            repondue_le=le,
        )
        return transiter(
            self, remarques=[repondue if r.rang == rang else r for r in self.remarques]
        )

    def retransmettre(
        self,
        *,
        par: str,
        compte: str,
        le: datetime,
        echantillon: list[EcritureEchantillonnee],
        ecritures_du_mois: int,
        message: str | None,
    ) -> RevueDeDossier:
        if self.statut is not StatutRevue.RENVOYEE:
            raise RevueRefusee(
                f"seul un mois renvoyé se retransmet ; celui-ci est {self.statut.value.lower()}."
            )
        sans_reponse = [r.rang for r in self.remarques if r.statut is StatutRemarque.OUVERTE]
        if sans_reponse:
            raise RevueRefusee(
                f"remarque(s) {', '.join(map(str, sans_reponse))} sans réponse : "
                "répondre à chacune avant de retransmettre."
            )
        return transiter(
            self,
            statut=StatutRevue.TRANSMISE,
            echantillon=echantillon,
            ecritures_du_mois=ecritures_du_mois,
            historique=self._passage(StatutRevue.TRANSMISE, par, compte, le, message),
        )

    def clore_la_remarque(self, rang: int, *, par: str, le: datetime) -> RevueDeDossier:
        if self.statut is not StatutRevue.TRANSMISE:
            raise RevueRefusee(
                "le réviseur clôt les remarques d'un mois qui lui est transmis ; celui-ci est "
                f"{self.statut.value.lower()}."
            )
        remarque = self.remarque(rang)
        if remarque.statut is StatutRemarque.CLOSE:
            raise RevueRefusee(f"la remarque {rang} est déjà close.")
        close = transiter(remarque, statut=StatutRemarque.CLOSE, close_par=par, close_le=le)
        return transiter(self, remarques=[close if r.rang == rang else r for r in self.remarques])

    def valider(self, *, par: str, compte: str, le: datetime) -> RevueDeDossier:
        if self.statut is not StatutRevue.TRANSMISE:
            raise RevueRefusee(
                f"seul un mois transmis se valide ; celui-ci est {self.statut.value.lower()}."
            )
        if compte == self.transmise_par_compte:
            raise RevueRefusee(
                "celui qui a transmis le mois ne valide pas sa propre revue : il faut une autre "
                "paire d'yeux."
            )
        restantes = [r.rang for r in self.remarques if r.statut is not StatutRemarque.CLOSE]
        if restantes:
            raise RevueRefusee(
                f"remarque(s) {', '.join(map(str, restantes))} non close(s) : les clore, ou "
                "renvoyer le mois."
            )
        return transiter(
            self,
            statut=StatutRevue.VALIDEE,
            validee_par=par,
            validee_le=le,
            historique=self._passage(StatutRevue.VALIDEE, par, compte, le),
        )
