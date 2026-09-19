"""Le chiffrage : un intervalle, jamais un prix.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI UN INTERVALLE

Le centre ne pratique pas des prix fixes, et il a de bonnes raisons : un même
service demande plus de travail selon le client, les tarifs des prestataires
évoluent, et la relation commerciale suppose une marge de négociation.

Le moteur rend donc trois valeurs, et chacune protège quelqu'un :

    plancher    le centre     en dessous, la prestation lui coûte de l'argent
    référence   la cohérence  deux clients au même profil, le même montant
    plafond     le client     au dessus, motif obligatoire et validation d'un cran

Rendre un nombre unique aurait un défaut simple : le responsable en dévierait de
toute façon, et l'écart ne serait mesuré nulle part.

⚠️ LES POURCENTAGES S'APPLIQUENT À LA BASE, PAS AU CUMUL

C'est la décision la moins visible du module et la plus importante.

Un ajustement de dix pour cent appliqué au **cumul** rendrait le résultat
dépendant de l'ordre des règles : ajouter cinquante mille puis dix pour cent ne
donne pas dix pour cent puis cinquante mille. Or l'ordre des règles est celui du
répertoire de fichiers, c'est-à-dire l'alphabet.

**Un tarif qui change parce qu'un fiscaliste a renommé un fichier est
indéfendable.** Les pourcentages portent donc tous sur la base, ce qui les rend
commutatifs, et un test le vérifie en mélangeant les règles.

LES FRAIS OFFICIELS NE SONT PAS DES HONORAIRES

Frais de notaire, droits d'enregistrement, frais de guichet : le centre les
avance, il ne les gagne pas. Ils entrent donc dans la proposition comme des
**débours**, à leur montant, et :

* aucune règle ne les ajuste ;
* l'amplitude de négociation ne les touche pas.

Négocier ne peut pas porter sur l'argent d'un tiers. Confondre les deux ferait
qu'un rabais de vingt pour cent accordé au client sortirait de la poche du
centre sur la part qu'il ne gagne pas, et personne ne s'en apercevrait avant le
bilan.

LA VERSION DU BARÈME EST CONSERVÉE, ET C'EST UNE OBLIGATION

Le piège nommé par le document de conception : « Ne pas conserver la version du
barème employée. Six mois plus tard, personne ne peut expliquer comment ce
montant a été obtenu, et le client conteste. »

Une proposition sans version est refusée à la construction.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contextes.referentiel.contrats import Fondement, StatutValidation
from app.contextes.souscription.domaine.qualification import Questionnaire
from app.moteur.agregation import intervalle_autour
from app.moteur.chemins import chemins_cites
from app.moteur.consequence import Consequence, TypeConsequence
from app.moteur.evaluation import Declenchement, evaluer_regles
from app.moteur.faits import Fait, SchemaDeFaits, TypeFait

__all__ = [
    "Bareme",
    "BaremeIntrouvable",
    "Debours",
    "FAITS_DU_CHIFFRAGE",
    "LigneTarifaire",
    "Proposition",
    "RegleDeTarification",
    "UNITE",
    "UNITE_TAUX",
    "VALORISER_L_AJUSTEMENT",
    "chiffrer",
    "schema_de_tarification",
]

#: L'unité de tout ce module. Déclarée une fois : une valeur sans unité invite à
#: comparer des francs à des pourcentages, ce qui finit toujours par arriver.
UNITE = "FCFA"

#: L'unité d'un ajustement proportionnel. C'est elle, et **non la magnitude de la
#: valeur**, qui dit à la valorisation qu'il faut multiplier par la base.
#:
#: La première version devinait : « une valeur strictement comprise entre moins un et
#: un est un taux ». Cela tenait tant qu'aucun ajustement en francs ne valait moins
#: d'un franc, c'est-à-dire tant que personne n'écrivait une remise de cinquante
#: centimes. Une heuristique dans un moteur de prix est un défaut en attente : elle ne
#: se trompe qu'une fois, et elle se trompe sur une facture.
UNITE_TAUX = "taux_de_la_base"

#: Ce que le chiffrage ajoute aux faits de la qualification.
#:
#: `base` en fait partie, et ce n'est pas un détail de mise en œuvre : c'est ce
#: qui permet à un ajustement en pourcentage de se chiffrer sans regarder le
#: cumul, donc de commuter. Voir l'en-tête.
FAITS_DU_CHIFFRAGE = (
    Fait(code="base", type=TypeFait.DECIMAL, libelle="Montant de base du barème", unite=UNITE),
    Fait(
        code="score_charge",
        type=TypeFait.ENTIER,
        libelle="Score de la matrice de charge",
        unite="points",
    ),
    Fait(code="service", type=TypeFait.TEXTE, libelle="Service demandé"),
)


class BaremeIntrouvable(LookupError):
    """Aucun barème pour ce service à cette date."""


def schema_de_tarification(questionnaire: Questionnaire) -> SchemaDeFaits:
    """Le schéma du questionnaire, augmenté de ce que le chiffrage apporte.

    ⚠️ **Une composition, pas une déclaration parallèle.** Les faits de
    tarification sont ceux qu'un responsable a saisis, plus trois que le système
    calcule. Les redéclarer ici créerait deux listes à tenir d'accord, et la
    dérive ne se verrait qu'au premier chiffrage faux.

    C'est aussi ce qui fait que le questionnaire commande la tarification : y
    ajouter une question, c'est ouvrir un fait de plus aux règles de prix, sans
    déploiement.
    """
    return SchemaDeFaits(
        domaine=f"TARIFICATION_{questionnaire.service.upper().replace('-', '_')}",
        faits=(*questionnaire.schema().faits, *FAITS_DU_CHIFFRAGE),
    )


# ── Le barème ────────────────────────────────────────────────────────────────


class Bareme(BaseModel):
    """La base d'un service, et l'amplitude de négociation admise autour d'elle.

    Il vit au référentiel, daté comme les paramètres légaux. Sa **version** est
    recopiée dans chaque proposition : voir l'en-tête.
    """

    model_config = ConfigDict(frozen=True)

    service: str = Field(min_length=1)
    #: Recopiée dans la proposition. Deux barèmes ne portent jamais la même sur
    #: des valeurs différentes, sinon la traçabilité ne trace rien.
    version: str = Field(min_length=1)
    base: Decimal = Field(gt=0)

    applicable_du: date
    applicable_au: date | None = None

    #: Des proportions : `0.20` pour vingt pour cent. Le barème les déclare,
    #: parce qu'une amplitude en dur serait la valeur commerciale codée dans le
    #: moteur que ce projet cherche à éviter.
    baisse_maximale: Decimal = Field(default=Decimal("0.20"), ge=0, le=1)
    hausse_maximale: Decimal = Field(default=Decimal("0.50"), ge=0)

    fondement: Fondement
    statut: StatutValidation = StatutValidation.A_VALIDER
    valide_par: str | None = None
    valide_le: date | None = None

    @model_validator(mode="after")
    def _coherence(self) -> Bareme:
        if self.applicable_au is not None and self.applicable_au <= self.applicable_du:
            raise ValueError(f"{self.service} : borne de validité incohérente")
        if self.statut is StatutValidation.VALIDE and not (
            self.valide_par and self.valide_le
        ):
            raise ValueError(
                f"{self.service} : un barème VALIDE doit porter valide_par et "
                "valide_le. Sans signataire nommé, le prix n'engage personne."
            )
        return self

    def en_vigueur(self, a_la_date: date) -> bool:
        if a_la_date < self.applicable_du:
            return False
        return self.applicable_au is None or a_la_date < self.applicable_au


def bareme_pour(
    baremes: Sequence[Bareme], service: str, a_la_date: date
) -> Bareme:
    """Le barème d'un service **à une date**, jamais « le barème courant ».

    Un devis reçu le 20 mars et ouvert le 2 avril doit afficher le barème de
    mars. Lire la valeur courante ferait voir au client un autre chiffre que
    celui qu'on lui avait annoncé, et le cabinet n'aurait aucun moyen de savoir
    lequel il avait promis.
    """
    for bareme in baremes:
        if bareme.service == service and bareme.en_vigueur(a_la_date):
            return bareme
    connus = ", ".join(sorted({b.service for b in baremes})) or "aucun"
    raise BaremeIntrouvable(
        f"aucun barème pour « {service} » au {a_la_date}. Services tarifés : {connus}."
    )


# ── Les règles ───────────────────────────────────────────────────────────────


class RegleDeTarification(BaseModel):
    """Un critère de prix. Satisfait `RegleEvaluable`.

    ⚠️ **Convention du projet : un prédicat exprime la normalité.** VRAI = le cas
    ordinaire, aucun ajustement. FAUX = le critère est atteint, l'ajustement
    s'applique. C'est la même convention que les quatre autres domaines.
    """

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1)
    libelle: str = Field(min_length=1)
    applicable_du: date
    applicable_au: date | None = None
    predicat: dict[str, Any]

    #: Un montant fixe, en francs. Peut être négatif : une remise est un
    #: ajustement comme un autre, et l'écrire ainsi évite un second mécanisme.
    montant: Decimal | None = None
    #: Une proportion de la **base**, `0.15` pour quinze pour cent. Voir
    #: l'en-tête : jamais du cumul, sinon l'ordre des règles change le prix.
    taux: Decimal | None = None

    fondement: Fondement
    statut: StatutValidation = StatutValidation.A_VALIDER
    valide_par: str | None = None
    valide_le: date | None = None

    @model_validator(mode="after")
    def _coherence(self) -> RegleDeTarification:
        if self.applicable_au is not None and self.applicable_au <= self.applicable_du:
            raise ValueError(f"{self.code} : borne de validité incohérente")
        if not self.predicat:
            raise ValueError(f"{self.code} : prédicat vide")
        if (self.montant is None) == (self.taux is None):
            raise ValueError(
                f"{self.code} : un ajustement est **soit** un montant, **soit** un "
                "taux. Les deux à la fois rendraient le total dépendant de l'ordre "
                "dans lequel on les applique ; aucun des deux ne fait rien."
            )
        if self.statut is StatutValidation.VALIDE and not (
            self.valide_par and self.valide_le
        ):
            raise ValueError(
                f"{self.code} : une règle de prix VALIDE doit porter valide_par et "
                "valide_le. Sans signataire nommé, l'écart n'est opposable à personne."
            )
        return self

    @property
    def consequence(self) -> Consequence:
        """L'unité **déclare** la nature de l'ajustement.

        Un taux est porté tel quel, avec `UNITE_TAUX` ; c'est la valorisation
        qui le transforme en francs, parce qu'elle seule connaît la base. Le
        noyau, lui, ne compare jamais deux unités et n'en convertit aucune : il
        transporte.
        """
        proportionnel = self.taux is not None
        return Consequence(
            type=TypeConsequence.AJUSTEMENT,
            libelle=self.libelle,
            unite=UNITE_TAUX if proportionnel else UNITE,
            valeur_numerique=self.taux if proportionnel else self.montant,
        )

    @property
    def proportionnel(self) -> bool:
        return self.taux is not None

    def en_vigueur(self, a_la_date: date) -> bool:
        if a_la_date < self.applicable_du:
            return False
        return self.applicable_au is None or a_la_date < self.applicable_au

    def concerne(self, faits: Mapping[str, Any]) -> bool:
        """Toute règle porte sur tout chiffrage.

        Une règle qui ne vaudrait que pour certains services exprime cette
        restriction **dans son prédicat**, où elle se lit et se change sans code.
        """
        return True


def VALORISER_L_AJUSTEMENT(  # noqa: N802 — nommée comme un port, en majuscules
    consequence: Consequence, faits: Mapping[str, Any]
) -> Decimal | None:
    """Chiffre un ajustement en francs.

    ⚠️ **Un taux se multiplie par la base, jamais par le cumul courant.** C'est
    ici que la commutativité se joue, et c'est pourquoi `base` est un fait :
    la valorisation ne voit que les faits, donc elle ne peut pas voir le cumul,
    donc elle ne peut pas produire un résultat dépendant de l'ordre.

    La contrainte du moteur devient ici une garantie du domaine, et c'est la
    seconde fois que cela arrive dans ce projet.
    """
    if consequence.valeur_numerique is None:
        return None
    valeur = consequence.valeur_numerique
    if consequence.unite != UNITE_TAUX:
        return valeur
    base = Decimal(str(faits.get("base", 0)))
    return (base * valeur).quantize(Decimal("1"))


@dataclass(frozen=True)
class _ConstatTarifaire:
    """Le constat minimal qu'un agrégateur exige : sévérité, enjeu, conséquence.

    ⚠️ Le moteur rend des `Declenchement`, qui portent la **règle** ; l'agrégateur
    attend des constats, qui portent la **conséquence**. Chaque domaine fait ce
    pont lui-même, en trois lignes, et c'est délibéré : un noyau qui saurait
    dériver l'un de l'autre imposerait la forme de règle d'un domaine aux quatre
    autres.

    Ce domaine n'a pas de sévérité — un ajustement de prix n'est ni grave ni
    bénin. On emploie le code de la règle comme clé de dénombrement, ce qui donne
    au passage le détail par critère sans structure supplémentaire.
    """

    severite: str
    enjeu: Decimal | None
    consequence: Consequence

    @classmethod
    def depuis(cls, declenchement: Declenchement) -> _ConstatTarifaire:
        return cls(
            severite=declenchement.regle.code,
            enjeu=declenchement.enjeu,
            consequence=declenchement.regle.consequence,
        )


# ── Ce que le chiffrage rend ─────────────────────────────────────────────────


class LigneTarifaire(BaseModel):
    """Une ligne de la proposition, avec ce qui la justifie."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1)
    libelle: str = Field(min_length=1)
    montant: Decimal
    #: D'où vient cette ligne. Sans lui, un client qui conteste n'obtient qu'un
    #: total, et le responsable qu'un haussement d'épaules.
    fondement: str = ""


class Debours(BaseModel):
    """Un frais avancé pour le compte du client. **Ce n'est pas un honoraire.**

    Ni ajusté par une règle, ni touché par l'amplitude de négociation :
    négocier ne peut pas porter sur l'argent d'un tiers.
    """

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1)
    libelle: str = Field(min_length=1)
    montant: Decimal = Field(ge=0)
    fondement: str = ""


class Proposition(BaseModel):
    """Ce que le moteur rend. Une proposition n'engage personne.

    Elle devient opposable à la validation, qui est un geste habilité et
    distinct : voir le parcours, étape 6.
    """

    model_config = ConfigDict(frozen=True)

    service: str = Field(min_length=1)
    #: ⚠️ Obligatoire. Voir l'en-tête : sans elle, personne ne peut réexpliquer
    #: le montant six mois plus tard.
    version_bareme: str = Field(min_length=1)
    a_la_date: date

    base: Decimal
    plancher: Decimal
    reference: Decimal
    plafond: Decimal

    #: Les ajustements retenus, dans l'ordre des règles.
    lignes: tuple[LigneTarifaire, ...] = ()
    #: Les frais avancés, hors honoraires.
    debours: tuple[Debours, ...] = ()
    #: Les règles qui n'ont pas pu être évaluées. Signalées, jamais tues : un
    #: critère silencieusement absent est indiscernable d'un critère non atteint,
    #: et le prix serait faux sans que rien ne le dise.
    echecs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _l_intervalle_se_tient(self) -> Proposition:
        if not self.plancher <= self.reference <= self.plafond:
            raise ValueError(
                f"{self.service} : intervalle incohérent "
                f"({self.plancher} / {self.reference} / {self.plafond}). "
                "Un plancher au dessus de la référence ferait refuser le montant "
                "que le système recommande lui-même."
            )
        return self

    @property
    def total_des_debours(self) -> Decimal:
        return sum((d.montant for d in self.debours), Decimal(0))

    @property
    def total_de_reference(self) -> Decimal:
        """Ce que le client paiera si le responsable ne négocie rien."""
        return self.reference + self.total_des_debours

    def dans_l_intervalle(self, montant: Decimal) -> bool:
        """Le montant proposé tient-il sans motif ?

        Porte sur les **honoraires seuls**, jamais sur le total : les débours ne
        se négocient pas, et les inclure ferait croire qu'un rabais peut porter
        sur des droits d'enregistrement.
        """
        return self.plancher <= montant <= self.plafond


def chiffrer(
    faits_de_qualification: Mapping[str, Any],
    *,
    bareme: Bareme,
    regles: Sequence[RegleDeTarification],
    a_la_date: date,
    score_charge: int = 0,
    debours: Sequence[Debours] = (),
) -> Proposition:
    """Applique le barème et les règles, et rend l'intervalle.

    ─────────────────────────────────────────────────────────────────────────
    QUATRE OPÉRATIONS, TOUTES DÉCLARÉES

    1. **La base** vient du barème daté du service.
    2. **Les ajustements** viennent des règles dont le prédicat rend faux, en
       montant ou en proportion de la base.
    3. **La charge** entre comme un fait parmi les autres, ce qui relie
       directement l'effort estimé au prix demandé sans mécanisme séparé.
    4. **L'amplitude** vient du barème, et encadre la référence obtenue.

    Aucune de ces quatre n'est écrite dans ce fichier : elles viennent toutes du
    référentiel, et c'est ce qui permet au centre de réviser ses prix sans
    développeur.
    ─────────────────────────────────────────────────────────────────────────
    """
    faits = {
        **faits_de_qualification,
        "base": bareme.base,
        "score_charge": score_charge,
        "service": bareme.service,
    }
    # ⚠️ **UN FAIT SANS RÉPONSE NE DÉCLENCHE AUCUN AJUSTEMENT.** (pas 66)
    #
    # Les questions facultatives restées sans réponse sont absentes des faits, et le
    # moteur lit un fait absent comme `None`. Or la convention est « faux =
    # déclenchement » : `statuts_apportes == false` rendait faux sur une question sans
    # réponse, et la remise « Statuts apportés par le client » (−50 000 FCFA)
    # s'appliquait à qui n'avait rien dit. Une information inconnue valait remise.
    #
    # Le moteur n'est pas changé : il sert quatre domaines, et en conformité une
    # facture sans NIU doit bien déclencher sa règle. C'est la tarification qui décide
    # qu'un prix ne se fonde pas sur ce qu'on ignore. L'ajustement est écarté et la
    # proposition le **dit** dans ses échecs, pour que le responsable pose la question.
    #
    # Une règle qui cite `missing` traite l'absence elle-même : elle reste évaluée.
    absents = {
        regle.code: sorted(
            chemin
            for chemin in chemins_cites(regle.predicat)
            if "[]" not in chemin and chemin not in faits
        )
        for regle in regles
        if "missing" not in repr(regle.predicat)
    }
    ecartees = {code: faits_absents for code, faits_absents in absents.items() if faits_absents}
    evaluation = evaluer_regles(
        regles,
        faits,
        a_la_date,
        valorisation=VALORISER_L_AJUSTEMENT,
        ignorer=lambda regle: regle.code in ecartees,
    )
    echecs_sans_reponse = tuple(
        f"{code} : sans réponse à « {', '.join(faits_absents)} », ajustement non appliqué"
        for code, faits_absents in sorted(ecartees.items())
        # Seules les règles qui s'appliqueraient à ce service, à cette date : une règle
        # d'un autre service cite des faits que ce questionnaire ne pose jamais.
        if any(r.code == code and r.en_vigueur(a_la_date) and r.concerne(faits) for r in regles)
    )
    agregat = intervalle_autour(
        bareme.base, bareme.baisse_maximale, bareme.hausse_maximale, unite=UNITE
    )(tuple(_ConstatTarifaire.depuis(d) for d in evaluation.declenchements))

    plancher, plafond = agregat.intervalle
    return Proposition(
        service=bareme.service,
        version_bareme=bareme.version,
        a_la_date=a_la_date,
        base=bareme.base,
        plancher=plancher.quantize(Decimal("1")),
        reference=agregat.valeur.quantize(Decimal("1")),
        plafond=plafond.quantize(Decimal("1")),
        lignes=tuple(
            LigneTarifaire(
                code=d.regle.code,
                libelle=d.regle.libelle,
                montant=d.enjeu or Decimal(0),
                fondement=d.regle.fondement.texte,
            )
            for d in evaluation.declenchements
        ),
        debours=tuple(debours),
        echecs=echecs_sans_reponse
        + tuple(f"{e.code_regle} : {e.motif}" for e in evaluation.echecs),
    )
