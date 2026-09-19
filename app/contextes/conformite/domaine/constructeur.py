"""Construire une règle de conformité sans écrire de prédicat (pas 97).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI UN CONSTRUCTEUR, ET NON UN ÉDITEUR DE PRÉDICAT

Une règle du référentiel porte un prédicat JSONLogic dont la convention est contre-intuitive :
**VRAI = conforme, FAUX = constat émis**. Une personne, elle, pense la règle à l'envers :
« constat *si* le règlement est en espèces *et* le montant dépasse le seuil ». Écrire le
prédicat à la main, c'est inverser cette phrase sans se tromper, choisir entre `and` et `or`
après l'inversion (De Morgan), citer le bon chemin de fait, et comparer un montant à un
paramètre de la bonne unité. Chacune de ces fautes produit une règle qui charge sans
erreur et **accuse toutes les factures**, ou n'en accuse aucune.

Le constructeur prend la phrase telle qu'on la pense : des **conditions d'anomalie**, reliées
par « toutes » ou « au moins une ». Il vérifie chaque condition contre le schéma des faits
et le référentiel, puis produit lui-même le prédicat, inversé : `{"!": anomalie}`. Personne
n'écrit la négation.

CE QU'IL SAIT EXPRIMER, ET CE QU'IL NE SAIT PAS

Les faits simples de la facture (document, parties, montants, règlement), comparés à une
valeur, à un paramètre du référentiel ou à un autre fait. **Pas les lignes de détail** : une
condition sur « au moins une ligne » demande un quantificateur dont la lecture est
ambiguë (« une ligne » ou « toutes les lignes » ?) ; ces règles restent écrites et relues
en fichier. Le catalogue le dit, plutôt que de le taire.

⚠️ UN FAIT ABSENT N'ACCUSE PAS

« Constat si le total TTC dépasse le seuil » ne se déclenche pas sur une facture sans total :
la comparaison à une valeur absente rend faux, donc pas d'anomalie. C'est le sens voulu :
une information manquante se signale par une condition explicite (« est vide »), jamais par
l'échec d'une comparaison.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.moteur.faits import Fait, SchemaDeFaits, TypeFait

__all__ = [
    "Combinaison",
    "ConditionDAnomalie",
    "ConstructionRefusee",
    "Operateur",
    "compiler_l_anomalie",
    "decrire",
    "faits_constructibles",
    "operateurs_pour",
]


class ConstructionRefusee(ValueError):
    """Une condition ne se traduit pas en prédicat sûr. Le message dit laquelle et pourquoi."""


class Operateur(StrEnum):
    EST_VIDE = "EST_VIDE"
    N_EST_PAS_VIDE = "N_EST_PAS_VIDE"
    EGAL = "EGAL"
    DIFFERENT = "DIFFERENT"
    SUPERIEUR = "SUPERIEUR"
    SUPERIEUR_OU_EGAL = "SUPERIEUR_OU_EGAL"
    INFERIEUR = "INFERIEUR"
    INFERIEUR_OU_EGAL = "INFERIEUR_OU_EGAL"
    CORRESPOND_AU_MOTIF = "CORRESPOND_AU_MOTIF"
    NE_CORRESPOND_PAS_AU_MOTIF = "NE_CORRESPOND_PAS_AU_MOTIF"


#: En clair, pour la phrase que relit le valideur.
LIBELLES: dict[Operateur, str] = {
    Operateur.EST_VIDE: "est vide",
    Operateur.N_EST_PAS_VIDE: "est renseigné",
    Operateur.EGAL: "est égal à",
    Operateur.DIFFERENT: "est différent de",
    Operateur.SUPERIEUR: "est supérieur à",
    Operateur.SUPERIEUR_OU_EGAL: "est supérieur ou égal à",
    Operateur.INFERIEUR: "est inférieur à",
    Operateur.INFERIEUR_OU_EGAL: "est inférieur ou égal à",
    Operateur.CORRESPOND_AU_MOTIF: "correspond au motif",
    Operateur.NE_CORRESPOND_PAS_AU_MOTIF: "ne correspond pas au motif",
}

_SANS_COMPARANT = {Operateur.EST_VIDE, Operateur.N_EST_PAS_VIDE}
_ORDRE = {
    Operateur.SUPERIEUR,
    Operateur.SUPERIEUR_OU_EGAL,
    Operateur.INFERIEUR,
    Operateur.INFERIEUR_OU_EGAL,
}
_MOTIF = {Operateur.CORRESPOND_AU_MOTIF, Operateur.NE_CORRESPOND_PAS_AU_MOTIF}
_JSONLOGIC = {
    Operateur.EGAL: "==",
    Operateur.DIFFERENT: "!=",
    Operateur.SUPERIEUR: ">",
    Operateur.SUPERIEUR_OU_EGAL: ">=",
    Operateur.INFERIEUR: "<",
    Operateur.INFERIEUR_OU_EGAL: "<=",
}


def operateurs_pour(fait: Fait) -> tuple[Operateur, ...]:
    """Les opérateurs qui ont un sens pour ce type de fait. L'écran ne propose que ceux-là."""
    base = (Operateur.EST_VIDE, Operateur.N_EST_PAS_VIDE)
    if fait.type in (TypeFait.DECIMAL, TypeFait.ENTIER, TypeFait.DATE):
        return (*base, Operateur.EGAL, Operateur.DIFFERENT, *sorted(_ORDRE))
    if fait.type is TypeFait.TEXTE:
        return (*base, Operateur.EGAL, Operateur.DIFFERENT, *sorted(_MOTIF))
    if fait.type in (TypeFait.ENUM, TypeFait.BOOLEEN):
        return (*base, Operateur.EGAL, Operateur.DIFFERENT)
    return ()


def faits_constructibles(schema: SchemaDeFaits) -> tuple[Fait, ...]:
    """Les faits simples. Les listes et leurs éléments (`lignes[]…`) sont exclus : voir en-tête."""
    return tuple(f for f in schema.faits if f.type is not TypeFait.LISTE and "[]" not in f.code)


class Combinaison(StrEnum):
    #: Constat si **toutes** les conditions sont réunies.
    TOUTES = "TOUTES"
    #: Constat si **au moins une** condition est réunie.
    AU_MOINS_UNE = "AU_MOINS_UNE"


class ConditionDAnomalie(BaseModel):
    """« Le fait … est … à … » : une condition qui, réunie, signale l'anomalie.

    Le comparant est **exactement un** de : une valeur écrite, un paramètre du référentiel
    (résolu à la date de la facture), un autre fait de la facture. Aucun pour « est vide ».
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fait: str = Field(min_length=1)
    operateur: Operateur
    valeur: str | float | int | bool | None = None
    parametre: str | None = None
    autre_fait: str | None = None

    @model_validator(mode="after")
    def _un_seul_comparant(self) -> ConditionDAnomalie:
        fournis = [x for x in (self.valeur, self.parametre, self.autre_fait) if x is not None]
        if self.operateur in _SANS_COMPARANT and fournis:
            raise ValueError(f"« {LIBELLES[self.operateur]} » ne compare à rien.")
        if self.operateur not in _SANS_COMPARANT and len(fournis) != 1:
            raise ValueError(
                f"« {LIBELLES[self.operateur]} » compare à une seule chose : une valeur, un "
                "paramètre ou un autre fait."
            )
        return self


def _valeur_admise(fait: Fait, valeur: Any) -> Any:
    """La valeur écrite, convertie au type du fait, ou un refus en clair."""
    if fait.type is TypeFait.ENUM:
        if str(valeur) not in (fait.valeurs or ()):
            raise ConstructionRefusee(
                f"« {fait.libelle} » ne prend pas la valeur « {valeur} ». "
                f"Valeurs possibles : {', '.join(fait.valeurs or ())}."
            )
        return str(valeur)
    if fait.type is TypeFait.BOOLEEN:
        if isinstance(valeur, bool):
            return valeur
        if str(valeur).lower() in ("oui", "vrai", "true"):
            return True
        if str(valeur).lower() in ("non", "faux", "false"):
            return False
        raise ConstructionRefusee(f"« {fait.libelle} » attend oui ou non.")
    if fait.type in (TypeFait.DECIMAL, TypeFait.ENTIER):
        try:
            return float(str(valeur).replace(" ", "").replace(",", "."))
        except ValueError as erreur:
            raise ConstructionRefusee(
                f"« {fait.libelle} » attend un nombre, et non « {valeur} »."
            ) from erreur
    if fait.type is TypeFait.DATE:
        texte = str(valeur)
        if len(texte) != 10 or texte[4] != "-" or texte[7] != "-":
            raise ConstructionRefusee(f"« {fait.libelle} » attend une date AAAA-MM-JJ.")
        return texte
    return str(valeur)


def _condition_en_predicat(
    condition: ConditionDAnomalie,
    schema: SchemaDeFaits,
    unite_du_parametre: dict[str, str],
) -> dict[str, Any]:
    fait = schema.fait(condition.fait)
    if fait is None or fait not in faits_constructibles(schema):
        raise ConstructionRefusee(
            f"« {condition.fait} » n'est pas un fait qu'une règle construite peut lire."
        )
    if condition.operateur not in operateurs_pour(fait):
        raise ConstructionRefusee(
            f"« {fait.libelle} » ne se compare pas avec « {LIBELLES[condition.operateur]} »."
        )
    sujet = {"var": fait.code}
    if condition.operateur is Operateur.EST_VIDE:
        return {"!": [sujet]}
    if condition.operateur is Operateur.N_EST_PAS_VIDE:
        return {"!!": [sujet]}

    if condition.parametre is not None:
        unite = unite_du_parametre.get(condition.parametre)
        if unite is None:
            raise ConstructionRefusee(
                f"le paramètre « {condition.parametre} » n'existe pas au référentiel."
            )
        attendu_motif = condition.operateur in _MOTIF
        if attendu_motif != (unite == "REGEX"):
            raise ConstructionRefusee(
                f"le paramètre « {condition.parametre} » ({unite}) ne convient pas à "
                f"« {LIBELLES[condition.operateur]} »."
            )
        comparant: Any = {"param": condition.parametre}
    elif condition.autre_fait is not None:
        autre = schema.fait(condition.autre_fait)
        if autre is None or autre not in faits_constructibles(schema):
            raise ConstructionRefusee(f"« {condition.autre_fait} » n'est pas un fait lisible.")
        if autre.type is not fait.type or autre.unite != fait.unite:
            raise ConstructionRefusee(
                f"« {fait.libelle} » et « {autre.libelle} » ne sont pas de même nature : on ne "
                "compare pas des francs à une date."
            )
        comparant = {"var": autre.code}
    else:
        if condition.operateur in _MOTIF:
            raise ConstructionRefusee(
                "un motif se prend au référentiel (un paramètre REGEX validé), jamais écrit ici : "
                "une expression mal bornée accuserait des factures conformes."
            )
        comparant = _valeur_admise(fait, condition.valeur)

    if condition.operateur is Operateur.CORRESPOND_AU_MOTIF:
        return {"regex": [sujet, comparant]}
    if condition.operateur is Operateur.NE_CORRESPOND_PAS_AU_MOTIF:
        return {"!": [{"regex": [sujet, comparant]}]}
    return {_JSONLOGIC[condition.operateur]: [sujet, comparant]}


def compiler_l_anomalie(
    conditions: list[ConditionDAnomalie],
    combinaison: Combinaison,
    *,
    schema: SchemaDeFaits,
    unite_du_parametre: dict[str, str],
) -> dict[str, Any]:
    """Le prédicat de la règle : **VRAI = conforme**, c'est-à-dire la négation de l'anomalie.

    ⚠️ La négation est posée **ici, une fois**, autour de l'anomalie entière. Nier chaque
    condition et changer « toutes » en « au moins une » serait équivalent (De Morgan), et
    c'est précisément l'étape où une personne se trompe.
    """
    if not conditions:
        raise ConstructionRefusee("une règle demande au moins une condition d'anomalie.")
    if len(conditions) > 6:
        raise ConstructionRefusee(
            "six conditions au plus : au-delà, la règle ne se relit plus d'un coup d'œil, et une "
            "règle qu'on ne relit pas ne se valide pas."
        )
    parties = [_condition_en_predicat(c, schema, unite_du_parametre) for c in conditions]
    anomalie = (
        parties[0]
        if len(parties) == 1
        else {("and" if combinaison is Combinaison.TOUTES else "or"): parties}
    )
    return {"!": [anomalie]}


def decrire(
    conditions: list[ConditionDAnomalie], combinaison: Combinaison, schema: SchemaDeFaits
) -> str:
    """La phrase que relit le valideur, construite des mêmes conditions que le prédicat."""
    morceaux = []
    for c in conditions:
        fait = schema.fait(c.fait)
        libelle = fait.libelle if fait else c.fait
        # Minuscule initiale : le libellé est pris en milieu de phrase.
        nom = libelle[:1].lower() + libelle[1:]
        if c.operateur in _SANS_COMPARANT:
            morceaux.append(f"{nom} {LIBELLES[c.operateur]}")
        elif c.parametre is not None:
            morceaux.append(f"{nom} {LIBELLES[c.operateur]} la valeur du paramètre {c.parametre}")
        elif c.autre_fait is not None:
            autre = schema.fait(c.autre_fait)
            morceaux.append(
                f"{nom} {LIBELLES[c.operateur]} {autre.libelle if autre else c.autre_fait}"
            )
        else:
            morceaux.append(f"{nom} {LIBELLES[c.operateur]} {c.valeur}")
    lien = " et " if combinaison is Combinaison.TOUTES else " ou "
    return "Constat si " + lien.join(morceaux) + "."
