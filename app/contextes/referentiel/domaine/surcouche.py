"""La surcouche d'un cabinet sur le référentiel commun (pas 95).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE CABINET PEUT DÉCIDER, ET CE QUI RESTE COMMUN

Le référentiel commun est le fichier `Docs/referentiel/parametres.yaml`, versionné, lu par
tous les cabinets de la plateforme. Depuis le pas 95, un cabinet peut **décider** sur ce
référentiel depuis l'écran, avec effet immédiat pour lui seul :

* **valider** une version que le fichier livre « à valider » (neuf valeurs légales le sont
  depuis le 18 août : le délai de dépôt de la DSF, le format du NIU, le plafond de
  l'abattement CGA…) ;
* **proposer une nouvelle version** datée d'un paramètre (un taux changé par la loi de
  finances, un délai interne revu), qui ne produit **aucun effet** tant qu'une personne
  qualifiée ne l'a pas validée.

Ces décisions ne réécrivent jamais le fichier. Elles forment une **surcouche** propre au
cabinet, appliquée à la lecture : `appliquer_la_surcouche(commun, decisions)` rend les
paramètres tels que ce cabinet les emploie. Un autre cabinet lit le fichier commun, intact.

⚠️ CE QUE CELA ENGAGE, ET POURQUOI LE CIRCUIT EST STRICT

Un cabinet qui valide un taux légal différent de celui des autres calculera autrement que
ses confrères, et c'est voulu : il répond de ses calculs, et la loi de finances arrive
souvent avant le déploiement suivant. La contrepartie est la trace : chaque version issue
d'une décision porte **qui l'a validée, quand, et sur quel texte**, exactement comme une
version du fichier, et c'est ce que le rapport de conformité recopie.

Qui peut proposer et valider, selon la nature du paramètre (LOI ou POLITIQUE_CABINET), est
lu dans `Docs/referentiel/validation/circuit.yaml`. Sans ce fichier, **personne** ne peut
rien décider : le référentiel reste en lecture seule, comme avant le pas 95.

⚠️ LES DATES, SANS RETOUCHER LE PASSÉ EN SILENCE

Une nouvelle version s'insère à sa date : la version qui couvrait cette date est
**interrompue** la veille (borne haute exclue), les versions postérieures du fichier sont
conservées. Une proposition datée **avant** la version en vigueur la plus récente est
refusée : elle réécrirait des calculs déjà rendus, et c'est une rectification, pas une
évolution. Une décision dont la version visée a disparu du fichier (le fichier commun a
été corrigé depuis) est **sans objet** : elle ne s'applique pas, et le dit.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.contextes.referentiel.domaine.entites import (
    Fondement,
    NatureParametre,
    Parametre,
    StatutValidation,
    Unite,
    VersionParametre,
)

__all__ = [
    "CircuitDeValidation",
    "DecisionRefusee",
    "DecisionSurLeReferentiel",
    "EtapeDuCircuit",
    "SorteDeDecision",
    "StatutDecision",
    "appliquer_la_surcouche",
    "decision_sans_objet",
    "valeur_conforme_a_l_unite",
]


class DecisionRefusee(ValueError):
    """Le circuit, l'état de la décision ou les dates s'opposent au geste. Message pour l'écran."""


class SorteDeDecision(StrEnum):
    #: Valider une version que le fichier commun livre « à valider ».
    VALIDATION = "VALIDATION"
    #: Une nouvelle version datée, proposée puis validée.
    NOUVELLE_VERSION = "NOUVELLE_VERSION"


class StatutDecision(StrEnum):
    #: Proposée, sans aucun effet.
    PROPOSEE = "PROPOSEE"
    #: Validée : elle s'applique aux calculs du cabinet.
    APPLIQUEE = "APPLIQUEE"
    #: Refusée par la personne qualifiée. Conservée.
    REFUSEE = "REFUSEE"


class EtapeDuCircuit(BaseModel):
    """Qui propose et qui valide, pour une nature de paramètre."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Les permissions qui ouvrent la proposition. Vide : personne ne propose.
    proposer: tuple[str, ...] = ()
    #: Les permissions qui ouvrent la validation. Vide : personne ne valide.
    valider: tuple[str, ...] = ()
    #: Vrai par défaut : celui qui propose ne valide pas sa propre proposition.
    quatre_yeux: bool = True


class CircuitDeValidation(BaseModel):
    """Le circuit du cabinet. **Sans fichier, personne ne décide rien.**"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    par_nature: dict[NatureParametre, EtapeDuCircuit] = Field(default_factory=dict)
    #: Pas 97 : le circuit des **règles de conformité** construites à l'écran. Fermé par
    #: défaut, comme le reste : sans cette entrée, personne ne propose de règle.
    regles: EtapeDuCircuit = Field(default_factory=EtapeDuCircuit)
    #: Longueur minimale du motif d'une décision (espaces de bord exclus).
    motif_minimum: int = Field(default=20, ge=10)
    source: str = "aucun fichier de circuit : le référentiel est en lecture seule"

    def etape(self, nature: NatureParametre) -> EtapeDuCircuit:
        return self.par_nature.get(nature, EtapeDuCircuit())


def valeur_conforme_a_l_unite(valeur: bool | int | float | str, unite: Unite) -> str | None:
    """`None` si la valeur convient à l'unité, sinon la raison, en clair.

    Un pourcentage écrit « 19,25 % » en texte passerait la validation du modèle (la valeur
    accepte une chaîne) et ferait tomber chaque calcul qui le lit en décimal. La vérifier à
    la proposition, c'est la refuser à celui qui peut la corriger.
    """
    if unite is Unite.REGEX:
        if not isinstance(valeur, str) or not valeur:
            return "une expression régulière est attendue, en texte."
        try:
            re.compile(valeur)
        except re.error as erreur:
            return f"expression régulière invalide : {erreur}."
        return None
    if isinstance(valeur, bool) or not isinstance(valeur, int | float):
        return f"une valeur numérique est attendue pour l'unité {unite.value}."
    if valeur < 0:
        return "une valeur négative n'a de sens pour aucune unité du référentiel."
    if unite is Unite.POURCENTAGE and valeur > 100:
        return "un pourcentage ne dépasse pas 100."
    if unite in (Unite.JOURS, Unite.JOUR_DU_MOIS, Unite.EXERCICES) and valeur != int(valeur):
        return f"un nombre entier est attendu pour l'unité {unite.value}."
    if unite is Unite.JOUR_DU_MOIS and not 1 <= valeur <= 31:
        return "un jour du mois est compris entre 1 et 31."
    return None


class DecisionSurLeReferentiel(BaseModel):
    """Une décision d'un cabinet sur un paramètre, avec son histoire.

    Immuable : chaque geste rend un nouvel objet. Les transitions vivent ici pour qu'aucune
    route ne puisse valider sa propre proposition en oubliant une vérification.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    identifiant: str
    code: str
    sorte: SorteDeDecision
    #: La date d'effet visée : celle de la version à valider, ou de la nouvelle version.
    applicable_du: date
    #: Pour une nouvelle version seulement.
    valeur: bool | int | float | str | None = None
    fondement: Fondement | None = None
    note: str | None = None

    motif: str
    propose_par: str
    #: Le nom en clair : c'est lui qui figurera en `valide_par` de la version, lu par un
    #: vérificateur qui ne connaît pas les identifiants de comptes.
    propose_par_nom: str
    propose_le: datetime
    statut: StatutDecision

    tranche_par: str | None = None
    tranche_par_nom: str | None = None
    tranche_le: datetime | None = None
    motif_de_la_decision: str | None = None

    #: Pas 98 : la décision validée cesse de s'appliquer **à compter de** cette date. Elle
    #: n'est pas effacée : son statut reste APPLIQUEE, et l'historique dit qui l'a retirée.
    fin_d_effet: date | None = None
    retire_par: str | None = None
    retire_par_nom: str | None = None
    retire_le: datetime | None = None
    motif_du_retrait: str | None = None

    @property
    def retiree(self) -> bool:
        return self.fin_d_effet is not None

    def retirer(
        self,
        *,
        par: str,
        nom: str,
        le: datetime,
        a_compter_du: date,
        motif: str,
        circuit: CircuitDeValidation,
    ) -> DecisionSurLeReferentiel:
        """Mettre fin à une décision validée, **vers l'avant seulement** (pas 98).

        ─────────────────────────────────────────────────────────────────────────
        ⚠️ POURQUOI LE RETRAIT NE REMONTE PAS LE TEMPS

        Une version validée a servi à des calculs rendus : une TVA rejetée, une échéance
        annoncée. La retirer rétroactivement changerait ces résultats sans que personne ne
        les relise, la faute exacte que la date d'effet d'une proposition interdit déjà. Le
        retrait prend donc effet **aujourd'hui au plus tôt** ; à compter de cette date, la
        valeur du référentiel commun reprend.

        Une nouvelle version pas encore entrée en vigueur se retire à sa propre date d'effet :
        elle ne s'applique alors jamais.
        ─────────────────────────────────────────────────────────────────────────
        """
        if self.statut is not StatutDecision.APPLIQUEE or self.retiree:
            raise DecisionRefusee(
                f"la décision {self.identifiant} ne s'applique pas : il n'y a rien à retirer."
            )
        if a_compter_du < le.date():
            raise DecisionRefusee(
                f"un retrait ne prend pas effet dans le passé ({a_compter_du}) : les calculs "
                "déjà rendus changeraient sans que personne ne les relise."
            )
        if self.sorte is SorteDeDecision.NOUVELLE_VERSION and a_compter_du < self.applicable_du:
            raise DecisionRefusee(
                f"la version ne prend effet que le {self.applicable_du} : la retirer à compter "
                "de cette date suffit pour qu'elle ne s'applique jamais."
            )
        if len(motif.strip()) < circuit.motif_minimum:
            raise DecisionRefusee(
                f"le motif doit compter au moins {circuit.motif_minimum} caractères : il dit "
                "pourquoi la décision cesse de valoir."
            )
        return self.model_copy(
            update={
                "fin_d_effet": a_compter_du,
                "retire_par": par,
                "retire_par_nom": nom,
                "retire_le": le,
                "motif_du_retrait": motif.strip(),
            }
        )

    def valider(
        self,
        *,
        par: str,
        nom: str,
        le: datetime,
        motif: str,
        circuit: CircuitDeValidation,
        nature: NatureParametre,
    ) -> DecisionSurLeReferentiel:
        self._tranchable(par=par, motif=motif, circuit=circuit, nature=nature)
        return self.model_copy(
            update={
                "statut": StatutDecision.APPLIQUEE,
                "tranche_par": par,
                "tranche_par_nom": nom,
                "tranche_le": le,
                "motif_de_la_decision": motif.strip(),
            }
        )

    def refuser(
        self,
        *,
        par: str,
        nom: str,
        le: datetime,
        motif: str,
        circuit: CircuitDeValidation,
        nature: NatureParametre,
    ) -> DecisionSurLeReferentiel:
        self._tranchable(par=par, motif=motif, circuit=circuit, nature=nature)
        return self.model_copy(
            update={
                "statut": StatutDecision.REFUSEE,
                "tranche_par": par,
                "tranche_par_nom": nom,
                "tranche_le": le,
                "motif_de_la_decision": motif.strip(),
            }
        )

    def _tranchable(
        self, *, par: str, motif: str, circuit: CircuitDeValidation, nature: NatureParametre
    ) -> None:
        if self.statut is not StatutDecision.PROPOSEE:
            raise DecisionRefusee(
                f"la décision {self.identifiant} est {self.statut.value} : seule une "
                "proposition se tranche."
            )
        if circuit.etape(nature).quatre_yeux and par == self.propose_par:
            raise DecisionRefusee(
                "le circuit du cabinet exige qu'une autre personne valide ce qui a été proposé."
            )
        if len(motif.strip()) < circuit.motif_minimum:
            raise DecisionRefusee(
                f"le motif doit compter au moins {circuit.motif_minimum} caractères : il dit "
                "sur quel texte ou quelle décision la version repose."
            )


def decision_sans_objet(
    parametre: Parametre | None, decision: DecisionSurLeReferentiel
) -> str | None:
    """Pourquoi une décision appliquée ne peut plus s'appliquer, ou `None` si elle le peut.

    Le fichier commun évolue : un paramètre retiré, une version « à valider » corrigée ou
    déjà validée par la plateforme. La décision reste au dépôt ; elle ne s'applique plus, et
    l'écran le dit.
    """
    if parametre is None:
        return "le paramètre n'existe plus dans le référentiel commun."
    if decision.sorte is SorteDeDecision.VALIDATION:
        visee = next(
            (v for v in parametre.versions if v.applicable_du == decision.applicable_du), None
        )
        if visee is None:
            return "la version visée n'existe plus dans le référentiel commun."
        if visee.statut is StatutValidation.VALIDE:
            return "la version est désormais validée dans le référentiel commun."
    return None


def _inserer(
    parametre: Parametre, nouvelle: VersionParametre, fin: date | None = None
) -> Parametre:
    """Insère une version à sa date, en interrompant celle qui la couvrait.

    Pas 98 : avec une `fin`, la version insérée s'arrête à cette date, et la version du
    fichier qu'elle avait interrompue **reprend** jusqu'à sa propre échéance. Une fin égale
    à la date d'effet : rien n'est inséré, la décision ne s'est jamais appliquée.
    """
    du = nouvelle.applicable_du
    if fin is not None and fin <= du:
        return parametre
    versions: list[VersionParametre] = []
    suivante: date | None = None
    interrompue: VersionParametre | None = None
    for version in sorted(parametre.versions, key=lambda v: v.applicable_du):
        if version.applicable_du == du:
            # La décision du cabinet remplace la version du fichier à la même date.
            interrompue = version
            continue
        if version.applicable_du < du and (
            version.applicable_au is None or version.applicable_au > du
        ):
            interrompue = version
            versions.append(version.model_copy(update={"applicable_au": du}))
            continue
        if version.applicable_du > du and suivante is None:
            suivante = version.applicable_du
        versions.append(version)
    arret = suivante if fin is None else (fin if suivante is None else min(fin, suivante))
    versions.append(nouvelle.model_copy(update={"applicable_au": arret}))
    if fin is not None and interrompue is not None and (suivante is None or fin < suivante):
        reprise_jusqu_au = interrompue.applicable_au
        if reprise_jusqu_au is None or reprise_jusqu_au > fin:
            versions.append(
                interrompue.model_copy(
                    update={"applicable_du": fin, "applicable_au": reprise_jusqu_au}
                )
            )
    return parametre.model_copy(
        update={"versions": sorted(versions, key=lambda v: v.applicable_du)}
    )


def appliquer_la_surcouche(
    parametres: Iterable[Parametre], decisions: Iterable[DecisionSurLeReferentiel]
) -> list[Parametre]:
    """Les paramètres tels que **ce cabinet** les emploie.

    Seules les décisions `APPLIQUEE` comptent, dans l'ordre où elles ont été tranchées. Le
    résultat repasse par la validation du modèle (`Parametre.model_validate`) : une
    surcouche qui produirait deux versions chevauchantes lèverait ici, et non dans un
    calcul trois écrans plus loin.
    """
    par_code = {p.code: p for p in parametres}
    appliquees = sorted(
        (d for d in decisions if d.statut is StatutDecision.APPLIQUEE),
        key=lambda d: (d.tranche_le or d.propose_le, d.identifiant),
    )
    for decision in appliquees:
        parametre = par_code.get(decision.code)
        if parametre is None or decision.tranche_le is None:
            continue
        if decision_sans_objet(parametre, decision) is not None:
            continue
        if decision.sorte is SorteDeDecision.VALIDATION and decision.retiree:
            # Une validation retirée : la version redevient « à valider » pour le cabinet.
            continue
        signataire = decision.tranche_par_nom or decision.propose_par_nom
        if decision.sorte is SorteDeDecision.VALIDATION:
            versions = [
                v.model_copy(
                    update={
                        "statut": StatutValidation.VALIDE,
                        "valide_par": signataire,
                        "valide_le": decision.tranche_le.date(),
                    }
                )
                if v.applicable_du == decision.applicable_du
                else v
                for v in parametre.versions
            ]
            par_code[decision.code] = parametre.model_copy(update={"versions": versions})
            continue
        if decision.fondement is None or decision.valeur is None:
            continue
        # ⚠️ La borne est reprise de la version qui précédait : c'est une donnée légale du
        # seuil (« dès » ou « au-delà de »), que changer la valeur ne change pas. L'omettre
        # ferait lever chaque comparaison au seuil dès le lendemain de la validation.
        precedente = max(
            (v for v in parametre.versions if v.applicable_du < decision.applicable_du),
            key=lambda v: v.applicable_du,
            default=None,
        )
        nouvelle = VersionParametre(
            valeur=decision.valeur,
            applicable_du=decision.applicable_du,
            statut=StatutValidation.VALIDE,
            fondement=decision.fondement,
            note=decision.note,
            valide_par=signataire,
            valide_le=decision.tranche_le.date(),
            borne=precedente.borne if precedente is not None else None,
        )
        par_code[decision.code] = _inserer(parametre, nouvelle, decision.fin_d_effet)
    return [Parametre.model_validate(p.model_dump()) for p in par_code.values()]
