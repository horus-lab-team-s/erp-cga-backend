"""Inscrire une adhésion au Centre, et la résilier.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE EXISTE

L'adhésion est l'objet même d'un centre de gestion agréé, et le portefeuille ne
savait pas l'écrire. Le pas 46 a rendu l'abattement CGA dépendant de l'adhésion
réelle ; il fallait donc qu'une adhésion réelle puisse s'inscrire autrement qu'à la
main en base.

⚠️ LA DATE D'EFFET EST FISCALEMENT PORTEUSE

L'entité le dit depuis le premier jour : « c'est elle qui ouvre l'abattement sur le
bénéfice et l'exonération temporaire de patente. Une adhésion antidatée accorderait
rétroactivement des avantages que l'entreprise n'avait pas, et exposerait le Centre
autant que l'adhérent. »

D'où une asymétrie délibérée entre les deux gestes :

    adhérer    jamais avant le jour de l'inscription
               le sens qui accorde un avantage ne remonte pas le temps
    résilier   une date passée est admise, pourvu qu'aucun exercice clos
               ne la traverse ; le sens qui retire un avantage est le sens
               prudent, mais il ne réécrit pas ce que le Centre a attesté

⚠️ L'ÉLIGIBILITÉ À L'ADMISSION SE DÉCLARE, ET C'EST ASSUMÉ

CGI art. 118 : le centre assiste les entreprises dont le chiffre d'affaires annuel
n'excède pas 100 millions. Au pas 44, la règle était « le chiffre se lit dans les
livres, jamais déclaré ». Elle ne peut pas valoir ici : une entreprise qui adhère
n'a, en général, encore aucune écriture chez le Centre, et le portefeuille ne lit
pas la comptabilité.

Le chiffre est donc **déclaré, avec sa source** — la dernière liasse de
l'entreprise, le plus souvent — et c'est une première barrière, pas la seule. La
seconde est celle du pas 46 : à chaque liasse, le droit à l'abattement est apprécié
sur les livres. Une déclaration fausse à l'admission ne produit donc aucun
avantage, elle se voit à la première clôture.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from decimal import Decimal

from pydantic import ValidationError

from app.contextes.portefeuille.domaine.entites import Adhesion, Entreprise
from app.contextes.portefeuille.domaine.temporel import MotifChangement
from app.contextes.referentiel.contrats import Borne, seuil_atteint
from app.partage.copie import transiter

__all__ = [
    "AdhesionRefusee",
    "inscrire_une_adhesion",
    "prochain_numero_d_adhesion",
    "resilier_l_adhesion",
]


class AdhesionRefusee(ValueError):
    """L'adhésion ou la résiliation demandée ne peut pas s'inscrire sur ce dossier."""


_FORME_DU_NUMERO = re.compile(r"^ADH-(\d{4})-(\d{3,})$")


def _francs(montant: Decimal) -> str:
    """Un montant en francs, milliers séparés par une espace."""
    return f"{montant:,.0f}".replace(",", " ")


def prochain_numero_d_adhesion(dossiers: Iterable[Entreprise], annee: int) -> str:
    """Le numéro suivant de l'année, pour tout le portefeuille.

    ⚠️ **Le numéro n'est pas reçu, il est attribué**, pour la même raison qu'une
    écriture comptable : la continuité de la séquence est une propriété du registre
    entier. Un numéro saisi à la main se retrouve un jour en double, et deux
    adhérents se disputent la même attestation.

    ⚠️ Deux inscriptions simultanées obtiendraient le même numéro. La limite est
    celle de la numérotation des écritures, et elle est acceptée pour la même
    raison : sur le volume d'un cabinet, la collision est théorique, et le contrôle
    d'unicité de `inscrire_une_adhesion` la refuse au lieu de l'écrire.
    """
    rangs = [
        int(m.group(2))
        for dossier in dossiers
        for adhesion in dossier.adhesions
        if adhesion.numero and (m := _FORME_DU_NUMERO.match(adhesion.numero))
        and int(m.group(1)) == annee
    ]
    return f"ADH-{annee}-{max(rangs, default=0) + 1:03d}"


def inscrire_une_adhesion(
    entreprise: Entreprise,
    a_compter_du: date,
    *,
    numero: str,
    inscrite_le: date,
    chiffre_affaires_declare: Decimal,
    source_du_chiffre: str,
    seuil_adhesion: Decimal | None,
    borne_adhesion: Borne | None,
    numeros_existants: Iterable[str],
) -> Entreprise:
    """Rend l'entreprise adhérente à compter de cette date, ou dit pourquoi elle ne peut pas l'être.

    ⚠️ **`inscrite_le` est reçu, jamais lu sur l'horloge ici.** Le domaine ne connaît
    pas l'heure : la route la lui donne. C'est ce qui rend la règle d'antidate
    vérifiable sans attendre minuit.
    """
    if not source_du_chiffre.strip():
        raise AdhesionRefusee(
            "le chiffre d'affaires déclaré doit nommer sa source : la liasse de "
            "l'exercice, une attestation, un relevé. Un chiffre sans source ne se "
            "vérifie pas, et c'est le Centre qui atteste l'éligibilité."
        )

    if a_compter_du < inscrite_le:
        raise AdhesionRefusee(
            f"une adhésion ne prend pas effet avant le jour de son inscription "
            f"({inscrite_le:%d/%m/%Y}). La date d'effet ouvre l'abattement et "
            "l'exonération de patente : antidater accorderait des avantages que "
            "l'entreprise n'avait pas, et le Centre les attesterait."
        )

    en_cours = entreprise.adhesion_au(a_compter_du)
    if en_cours is not None:
        raise AdhesionRefusee(
            f"le dossier est déjà adhérent au {a_compter_du:%d/%m/%Y}, sous le numéro "
            f"{en_cours.numero}. Une seconde adhésion ne s'inscrit qu'après résiliation "
            "de la première."
        )

    posterieures = [a for a in entreprise.adhesions if a.debut > a_compter_du]
    if posterieures:
        raise AdhesionRefusee(
            f"une adhésion commence déjà le {posterieures[0].debut:%d/%m/%Y}. Inscrire "
            "celle-ci avant la chevaucherait."
        )

    if seuil_adhesion is None or borne_adhesion is None:
        raise AdhesionRefusee(
            "le référentiel ne fixe aucun seuil d'adhésion à cette date : "
            "l'éligibilité ne peut pas être vérifiée, donc pas attestée."
        )
    if seuil_atteint(chiffre_affaires_declare, seuil_adhesion, borne_adhesion):
        raise AdhesionRefusee(
            f"chiffre d'affaires déclaré de {_francs(chiffre_affaires_declare)} FCFA, "
            f"au-delà du seuil d'adhésion de {_francs(seuil_adhesion)} FCFA (CGI art. "
            "118). L'adhésion ne produirait aucun avantage fiscal, et la promettre "
            "serait une faute commerciale autant que professionnelle."
        )

    if numero in set(numeros_existants):
        raise AdhesionRefusee(
            f"le numéro {numero} est déjà attribué dans le portefeuille. Deux "
            "adhérents ne partagent pas une attestation."
        )

    # ⚠️ **Les règles de l'entité ressortent en refus nommé**, sans être recopiées.
    # Une adhésion antérieure à la création de l'entreprise est refusée par
    # l'entité elle-même ; la première version rejouait ce contrôle ici, et une
    # mutation a montré que le supprimer ne changeait rien. Deux contrôles qui
    # disent la même chose divergent au premier correctif. Celui de l'entité reste,
    # et sa phrase est rendue telle quelle au réviseur, en `409` plutôt qu'en
    # erreur interne.
    try:
        return _avec_l_adhesion(
            entreprise, a_compter_du, numero, chiffre_affaires_declare, source_du_chiffre
        )
    except ValidationError as refus_de_l_entite:
        phrases = (
            str(erreur["msg"]).removeprefix("Value error, ")
            for erreur in refus_de_l_entite.errors()
        )
        raise AdhesionRefusee("; ".join(phrases)) from refus_de_l_entite


def _avec_l_adhesion(
    entreprise: Entreprise,
    a_compter_du: date,
    numero: str,
    chiffre_affaires_declare: Decimal,
    source_du_chiffre: str,
) -> Entreprise:
    return transiter(
        entreprise,
        adhesions=[
            *entreprise.adhesions,
            Adhesion(
                debut=a_compter_du,
                motif=MotifChangement.ADHESION,
                numero=numero,
                # ⚠️ La précision porte la déclaration d'éligibilité et sa source :
                # le jour où la liasse la contredira, on saura sur quoi le Centre
                # s'était fondé pour admettre.
                #
                # Le montant est mis en forme **seul** : remplacer les virgules sur
                # toute la phrase abîmerait la source déclarée, qui en contient
                # souvent (« liasse 2025, visée par la DGI »).
                precision=(
                    "Chiffre d'affaires déclaré à l'admission : "
                    f"{_francs(chiffre_affaires_declare)} FCFA, source : "
                    f"{source_du_chiffre.strip()}."
                ),
            ),
        ],
    )


def resilier_l_adhesion(entreprise: Entreprise, au: date) -> Entreprise:
    """Ferme l'adhésion en cours ; le dossier cesse d'être adhérent le jour `au`.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LA RAISON DE LA RÉSILIATION VA AU JOURNAL D'AUDIT, PAS À LA PÉRIODE.**

    Une période porte le motif et la précision de son **ouverture**. Le garde-fou
    d'histoire n'autorise sur une période ouverte qu'une chose : recevoir sa date de
    fin. Réécrire sa précision pour y ajouter la raison de la fin effacerait la
    déclaration faite à l'admission, qui est justement ce qu'un vérificateur
    demandera. La route exige donc le motif au contrôle d'autorisation, qui le
    porte au journal.

    ⚠️ **Convention `[debut, fin[`** : résilier « au 1er juillet » veut dire que le
    30 juin est encore couvert.
    ─────────────────────────────────────────────────────────────────────────────
    """
    ouverte = next((a for a in entreprise.adhesions if a.fin is None), None)
    if ouverte is None:
        raise AdhesionRefusee("aucune adhésion en cours à résilier sur ce dossier.")

    if au <= ouverte.debut:
        raise AdhesionRefusee(
            f"la résiliation au {au:%d/%m/%Y} n'est pas postérieure à la prise d'effet "
            f"de l'adhésion ({ouverte.debut:%d/%m/%Y}). Une adhésion résiliée le jour "
            "même de son effet n'a jamais existé, et l'effacer n'appartient pas à ce "
            "geste."
        )

    traverses = sorted(e.libelle for e in entreprise.exercices if e.clos and e.cloture >= au)
    if traverses:
        raise AdhesionRefusee(
            f"la résiliation prendrait effet pendant ou avant l'exercice clos "
            f"{', '.join(traverses)}. Le Centre a attesté l'adhésion sur cet exercice ; "
            "la retirer après coup contredirait la liasse déposée."
        )

    return transiter(
        entreprise,
        adhesions=[
            transiter(a, fin=au) if a is ouverte else a for a in entreprise.adhesions
        ],
    )
