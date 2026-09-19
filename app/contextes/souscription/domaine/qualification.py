"""La qualification : ce qu'on apprend du client, en données typées.

─────────────────────────────────────────────────────────────────────────────────
L'ÉTAPE LA PLUS IMPORTANTE DU PARCOURS, ET CELLE QU'ON SOUS-ESTIME

Le responsable ne rédige pas des notes. Il remplit un questionnaire dont chaque
réponse est une **donnée typée** : forme juridique choisie dans une liste, nombre
d'associés en entier, capital social en montant, chiffre d'affaires en tranche,
activité selon la nomenclature.

**La différence entre du texte libre et une donnée typée est toute la différence
entre un dossier qu'un humain doit relire et un dossier qu'un moteur peut
évaluer.**

Le champ libre existe, il sert aux nuances, et **il n'entre dans aucun calcul**.
Ce n'est pas une restriction technique : c'est ce qui garantit qu'un montant
proposé est explicable. Un chiffrage qui dépendrait d'une phrase écrite à la
volée ne se rejouerait pas et ne se défendrait pas devant un client.

LE PIÈGE, NOMMÉ PAR LE DOCUMENT DE CONCEPTION

> « Stocker les réponses dans un champ de texte libre, ou dans un objet sans
> schéma. Le jour où le moteur de tarification en a besoin, il faut tout
> ressaisir, et le corpus d'entraînement devient inexploitable. »

C'est pourquoi une réponse à une question qui n'est pas au questionnaire est
**refusée**, et non rangée dans un coin. Un dictionnaire qui accepte tout est un
objet sans schéma déguisé.

LE QUESTIONNAIRE EST UNE CONFIGURATION

La liste des questions, leur ordre, leur caractère obligatoire et leur type
dépendent du service demandé et changent avec l'offre. **Ajouter une question ne
doit pas demander un déploiement.**

⚠️ Cela contredit en apparence l'en-tête de `app/moteur/faits.py`, qui affirmait
qu'un schéma de faits vit dans le code. L'affirmation était trop large et a été
corrigée là-bas : un schéma vit **là où vit son sujet**. Le sujet d'une facture
est un objet de code, donc son schéma est du code ; le sujet d'une qualification
est un questionnaire rempli par un humain, donc son schéma est configuré.

Le noyau ne voit pas la différence : il reçoit un `SchemaDeFaits`, d'où qu'il
vienne. C'est le quatrième usage du même moteur, et rien ne lui a été ajouté.

LA VERSION DU QUESTIONNAIRE EST CONSERVÉE

Pour la même raison que la version du barème employé au chiffrage : six mois plus
tard, personne ne doit se demander sur quelles questions un dossier a été
qualifié. Un questionnaire qui change rendrait une qualification ancienne
illisible, et le montant qui en découle inexplicable.

CHAQUE RÉPONSE PORTE SA SOURCE ET SON HORODATAGE

Une valeur déclarée par le client au téléphone et une valeur lue sur un document
n'ont pas le même poids. Le régime réel se constate sur pièces, pas sur
déclaration, et le jour où l'on voudra distinguer les deux, l'information sera
là. La collecter après coup serait impossible.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.moteur.faits import Fait, SchemaDeFaits, TypeFait

__all__ = [
    "LONGUEUR_NOTE",
    "Qualification",
    "Question",
    "Questionnaire",
    "Reponse",
    "ReponseInvalide",
    "SourceReponse",
    "ouvrir_une_qualification",
]

#: Le champ libre. Assez pour une nuance, trop peu pour un compte rendu : ce qui
#: mérite un compte rendu mérite une question.
LONGUEUR_NOTE = 4_000


class ReponseInvalide(ValueError):
    """La réponse ne correspond pas à ce que la question attend.

    Refusée à la saisie, où le responsable a le client au téléphone et peut
    demander. La laisser passer la ferait découvrir au chiffrage, quand il a
    raccroché.
    """


class SourceReponse(StrEnum):
    """D'où vient la valeur. Voir l'en-tête : elles n'ont pas le même poids."""

    #: Le client l'a dit. Non vérifié.
    DECLAREE = "DECLAREE"
    #: Lue sur une pièce fournie. Vérifiable, et vérifiée.
    CONSTATEE = "CONSTATEE"
    #: Déduite d'une autre source par le système, ou reprise de la demande de
    #: contact. Traçable, mais pas prononcée par le client.
    DEDUITE = "DEDUITE"


class Question(BaseModel):
    """Une question du questionnaire, et ce qu'elle attend.

    Elle produit un `Fait` du moteur : c'est le même objet, vu du côté de celui
    qui le remplit plutôt que du côté de celui qui l'évalue.
    """

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1)
    libelle: str = Field(min_length=1)
    type: TypeFait
    #: Plus petit = posé plus tôt. L'ordre compte : un questionnaire qui commence
    #: par le capital social avant d'avoir demandé la forme juridique se fait
    #: interrompre par le client.
    rang: int = Field(default=0, ge=0)
    obligatoire: bool = False
    #: Obligatoire pour un ENUM, refusé ailleurs. Le `Fait` du moteur applique la
    #: même règle, et c'est lui qui la tient.
    valeurs: tuple[str, ...] | None = None
    unite: str | None = None
    #: Ce que le responsable lit à l'écran quand il hésite. Une question mal
    #: comprise se remplit quand même, et de travers.
    aide: str = ""

    @property
    def fait(self) -> Fait:
        return Fait(
            code=self.code,
            type=self.type,
            libelle=self.libelle,
            unite=self.unite,
            valeurs=self.valeurs,
        )


class Questionnaire(BaseModel):
    """Les questions posées pour un service donné, à une version donnée.

    ⚠️ **La version n'est pas décorative.** Elle est conservée dans chaque
    qualification, comme la version du barème l'est dans chaque proposition
    tarifaire. Six mois plus tard, personne ne doit se demander sur quelles
    questions un dossier a été qualifié.
    """

    model_config = ConfigDict(frozen=True)

    service: str = Field(min_length=1)
    version: str = Field(min_length=1)
    questions: tuple[Question, ...] = ()

    @model_validator(mode="after")
    def _il_produit_un_schema_valide(self) -> Questionnaire:
        """Éprouvé à la construction, et non paresseusement au premier appel.

        Les doublons de code sont vérifiés ici, et le reste l'est en construisant
        le schéma : c'est le `Fait` du moteur qui exige qu'un ENUM déclare ses
        valeurs, et le laisser trancher évite d'écrire la même règle deux fois.

        La construction était paresseuse au premier jet : `schema()` échouait, le
        constructeur non. Un questionnaire invalide pouvait donc exister en
        mémoire et ne se briser qu'au moment de chiffrer. **Un objet qu'on ne
        peut pas construire de travers vaut mieux qu'un objet qu'on vérifie plus
        tard**, parce que « plus tard » finit par vouloir dire « en entretien ».
        """
        codes = [q.code for q in self.questions]
        doublons = sorted({c for c in codes if codes.count(c) > 1})
        if doublons:
            raise ValueError(
                f"questionnaire {self.service} : questions déclarées deux fois "
                f"{doublons}. La seconde écraserait la première, et laquelle "
                "dépendrait de l'ordre du fichier."
            )
        self.schema()
        return self

    @property
    def ordonnees(self) -> tuple[Question, ...]:
        return tuple(sorted(self.questions, key=lambda q: (q.rang, q.code)))

    @property
    def obligatoires(self) -> tuple[str, ...]:
        return tuple(q.code for q in self.ordonnees if q.obligatoire)

    def question(self, code: str) -> Question | None:
        return next((q for q in self.questions if q.code == code), None)

    def schema(self) -> SchemaDeFaits:
        """Le questionnaire, vu par le moteur.

        C'est le pont, et il tient en une ligne : le même objet sert à guider une
        saisie humaine et à valider un prédicat de tarification. Deux
        déclarations séparées dériveraient, et la dérive ne se verrait qu'au
        premier chiffrage faux.
        """
        return SchemaDeFaits(
            domaine=f"QUALIFICATION_{self.service.upper().replace('-', '_')}",
            faits=tuple(q.fait for q in self.ordonnees),
        )


class Reponse(BaseModel):
    """Une valeur typée, avec sa provenance."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1)
    #: Déjà convertie au type de la question. Voir `_convertir`.
    valeur: bool | int | Decimal | str | date | tuple[str, ...]
    source: SourceReponse
    saisie_le: datetime


class Qualification(BaseModel):
    """Ce qu'on a appris, et ce qui manque encore.

    Figée comme le reste du domaine : répondre rend une nouvelle qualification.
    """

    model_config = ConfigDict(frozen=True)

    dossier: str = Field(min_length=1)
    service: str = Field(min_length=1)
    #: La version du questionnaire employé. Voir l'en-tête de `Questionnaire`.
    version_questionnaire: str = Field(min_length=1)

    reponses: tuple[Reponse, ...] = ()

    #: Le champ libre. **Il n'entre dans aucun calcul**, et un test le vérifie.
    #: Il sert aux nuances : « le client hésite encore sur le nombre d'associés »,
    #: « son comptable actuel part à la retraite ». Ce qui mérite d'entrer dans un
    #: calcul mérite une question.
    note: str = Field(default="", max_length=LONGUEUR_NOTE)

    # ── Ce que la qualification sait dire d'elle-même ───────────────────────

    def reponse(self, code: str) -> Reponse | None:
        return next((r for r in self.reponses if r.code == code), None)

    def manquantes(self, questionnaire: Questionnaire) -> tuple[str, ...]:
        """Les questions obligatoires encore sans réponse, dans l'ordre de saisie.

        Dans l'ordre, et non par ordre alphabétique : c'est une liste que le
        responsable parcourt à l'écran, pas un ensemble.
        """
        repondues = {r.code for r in self.reponses}
        return tuple(
            code for code in questionnaire.obligatoires if code not in repondues
        )

    def complete(self, questionnaire: Questionnaire) -> bool:
        """Toutes les obligatoires sont remplies.

        Prend le questionnaire en argument plutôt que de le porter : une
        qualification se relit longtemps après, et le questionnaire aura changé.
        C'est l'appelant qui décide lequel il confronte, celui d'aujourd'hui ou
        celui de sa version.
        """
        return not self.manquantes(questionnaire)

    def avancement(self, questionnaire: Questionnaire) -> tuple[int, int]:
        """Répondues sur total, obligatoires comprises. Pour la barre à l'écran."""
        connues = {q.code for q in questionnaire.questions}
        return (
            len([r for r in self.reponses if r.code in connues]),
            len(questionnaire.questions),
        )

    def faits(self) -> Mapping[str, Any]:
        """Ce que le moteur de tarification lira.

        ⚠️ **La note libre n'y figure pas**, et c'est la garantie que le chiffrage
        est explicable. Un montant qui dépendrait d'une phrase écrite à la volée
        ne se rejouerait pas et ne se défendrait pas devant un client.

        Les questions non répondues sont **absentes** plutôt que nulles. Un
        prédicat qui les cite le dira par `missing`, ce que le moteur sait faire ;
        les poser à `None` ferait qu'une comparaison numérique les traiterait
        comme zéro, et un chiffre d'affaires inconnu vaudrait zéro franc.
        """
        return {r.code: r.valeur for r in self.reponses}

    # ── La saisie ───────────────────────────────────────────────────────────

    def repondre(
        self,
        questionnaire: Questionnaire,
        code: str,
        valeur: object,
        a_l_instant: datetime,
        *,
        source: SourceReponse = SourceReponse.DECLAREE,
    ) -> Qualification:
        """Enregistre une réponse, après l'avoir convertie et vérifiée.

        ─────────────────────────────────────────────────────────────────────
        TROIS REFUS, ET CHACUN ÉVITE UN DÉFAUT DIFFÉRENT

        **Une question inconnue est refusée.** C'est le piège nommé par le
        document : un objet qui accepte tout est un objet sans schéma déguisé, et
        le jour où le moteur en a besoin il faut tout ressaisir.

        **Une valeur du mauvais type est refusée.** À la saisie, où le
        responsable a le client au téléphone et peut demander. La laisser passer
        la ferait découvrir au chiffrage, quand il a raccroché.

        **Une valeur vide sur une question obligatoire est refusée.** Enregistrer
        un vide ferait sortir la question de la liste des manquantes tout en ne
        répondant à rien.

        RÉPONDRE DEUX FOIS REMPLACE

        Le client se corrige en cours d'entretien, c'est normal. La dernière
        réponse fait foi, avec son horodatage. L'historique des corrections
        appartient au journal d'audit, pas ici : le porter doublerait la taille
        de l'objet pour une question qu'on pose une fois par an.
        ─────────────────────────────────────────────────────────────────────
        """
        question = questionnaire.question(code)
        if question is None:
            connues = ", ".join(q.code for q in questionnaire.ordonnees) or "aucune"
            raise ReponseInvalide(
                f"« {code} » n'est pas une question du questionnaire "
                f"{questionnaire.service}. Questions connues : {connues}. "
                "Ranger une réponse hors schéma la rendrait invisible au moteur."
            )
        convertie = _convertir(question, valeur)
        if convertie is None:
            if question.obligatoire:
                raise ReponseInvalide(
                    f"« {code} » est obligatoire et la réponse est vide. "
                    "L'enregistrer la ferait sortir de la liste des questions "
                    "manquantes sans répondre à rien."
                )
            # Effacer une réponse facultative est un geste légitime : le client
            # revient sur ce qu'il avait dit sans rien mettre à la place.
            return self._sans(code)

        precedentes = self._sans(code).reponses
        reponse = Reponse(
            code=code, valeur=convertie, source=source, saisie_le=a_l_instant
        )
        return self.model_copy(update={"reponses": (*precedentes, reponse)})

    def _sans(self, code: str) -> Qualification:
        return self.model_copy(
            update={"reponses": tuple(r for r in self.reponses if r.code != code)}
        )

    def avec_note(self, note: str) -> Qualification:
        """Remplace le champ libre. Il n'entre dans aucun calcul.

        ⚠️ **La borne est vérifiée ici, à la main, et il le faut.**

        `model_copy` **ne revalide pas** : c'est documenté chez Pydantic et
        facile à oublier, parce que tout le domaine repose dessus pour ses
        transitions. `max_length` sur le champ protège le constructeur et laisse
        passer une copie.

        Ailleurs dans ce contexte, la conséquence est nulle : les transitions
        écrivent des valeurs calculées ou des champs sans contrainte. Ici, la
        valeur vient d'un formulaire, et la borne existe justement pour lui.

        La règle à retenir : **toute copie qui écrit un champ contraint depuis
        une saisie doit vérifier elle-même.** Le défaut a été trouvé par un test
        qui attendait un refus et n'en a pas eu.
        """
        propre = note.strip()
        if len(propre) > LONGUEUR_NOTE:
            raise ReponseInvalide(
                f"la note dépasse {LONGUEUR_NOTE} caractères ({len(propre)}). "
                "Ce qui mérite un compte rendu mérite une question, avec un type."
            )
        return self.model_copy(update={"note": propre})


def _convertir(question: Question, valeur: object) -> Any | None:
    """La valeur au type de la question, ou `None` si elle est vide.

    ⚠️ **Un booléen n'est pas un entier**, bien que Python en décide autrement :
    `isinstance(True, int)` rend vrai. Sans cette garde, cocher une case sur une
    question qui attend un nombre d'associés enregistrerait « 1 associé », et
    rien ne le signalerait jamais.
    """
    if valeur is None or (isinstance(valeur, str) and not valeur.strip()):
        return None

    attendu = question.type
    try:
        if attendu is TypeFait.BOOLEEN:
            return _booleen(valeur)
        if attendu is TypeFait.ENTIER:
            if isinstance(valeur, bool):
                raise ReponseInvalide(
                    f"« {question.code} » attend un entier, une case cochée a été "
                    "fournie. Python considère `True` comme valant 1 : sans ce "
                    "refus, la valeur serait enregistrée sans que rien ne le dise."
                )
            return int(str(valeur).strip())
        if attendu is TypeFait.DECIMAL:
            if isinstance(valeur, bool):
                raise ReponseInvalide(
                    f"« {question.code} » attend un montant, une case cochée a été "
                    "fournie."
                )
            return Decimal(str(valeur).strip().replace(" ", ""))
        if attendu is TypeFait.DATE:
            return valeur if isinstance(valeur, date) else date.fromisoformat(str(valeur))
        if attendu is TypeFait.ENUM:
            return _enum(question, valeur)
        if attendu is TypeFait.LISTE:
            return _liste(valeur)
    except ReponseInvalide:
        raise
    except (ValueError, TypeError, InvalidOperation) as echec:
        raise ReponseInvalide(
            f"« {question.code} » attend {attendu}, « {valeur} » n'en est pas un : "
            f"{echec}"
        ) from echec

    return str(valeur).strip()


def _booleen(valeur: object) -> bool:
    """Accepte les formes qu'un formulaire produit, refuse le reste.

    Convertir par la vérité de Python ferait de la chaîne « non » un vrai, ce qui
    est exactement la réponse inverse de celle que le client a donnée.
    """
    if isinstance(valeur, bool):
        return valeur
    texte = str(valeur).strip().casefold()
    if texte in {"true", "vrai", "oui", "1", "o", "yes"}:
        return True
    if texte in {"false", "faux", "non", "0", "n", "no"}:
        return False
    raise ReponseInvalide(
        f"« {valeur} » n'est ni un oui ni un non. La vérité de Python ferait de "
        "« non » un vrai, ce qui est l'inverse de la réponse donnée."
    )


def _enum(question: Question, valeur: object) -> str:
    texte = str(valeur).strip()
    admises = question.valeurs or ()
    if texte not in admises:
        raise ReponseInvalide(
            f"« {question.code} » n'admet que {list(admises)} ; « {texte} » n'en "
            "fait pas partie. Une valeur hors liste ferait échouer chaque règle "
            "qui compare à cette liste, sans qu'aucune ne le dise."
        )
    return texte


def _liste(valeur: object) -> tuple[str, ...]:
    if isinstance(valeur, str):
        return tuple(m.strip() for m in valeur.split(",") if m.strip())
    return tuple(str(m).strip() for m in valeur if str(m).strip())


def ouvrir_une_qualification(
    dossier: str, questionnaire: Questionnaire
) -> Qualification:
    """Une qualification vide, adossée à une version de questionnaire."""
    return Qualification(
        dossier=dossier,
        service=questionnaire.service,
        version_questionnaire=questionnaire.version,
    )
