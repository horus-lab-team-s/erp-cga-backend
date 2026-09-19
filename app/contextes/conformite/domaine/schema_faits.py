"""Les faits qu'une facture expose au moteur d'évaluation.

Ce module est l'**adaptateur** entre un objet métier typé, `FactureAControler`, et le
noyau qui ne connaît que des faits. Il ne contient aucune règle : il déclare ce sur quoi
les règles ont le droit de porter.

Pourquoi le déclarer plutôt que de laisser le moteur découvrir les champs : une règle
qui interroge `montants.total_htt` au lieu de `montants.total_ht` ne provoque aucune
erreur visible. Le fait manquant vaut absent, la comparaison rend faux, et un constat
part sur chaque pièce contrôlée. Confronter les règles à ce schéma au chargement
transforme une faute d'un caractère en refus immédiat, avec le nom du fait suggéré.

⚠️ **Ce schéma reste ici, et n'ira pas au référentiel.** Il décrit ce que
`FactureAControler` expose ; les deux bougent ensemble, et les séparer ouvrirait une
dérive qui ne se verrait qu'à l'évaluation suivante. Les règles, elles, vivent au
référentiel parce qu'elles varient sans le code. Voir `app.moteur.faits.SchemaDeFaits`.
"""

from __future__ import annotations

from app.contextes.conformite.domaine.entites import (
    ModeReglement,
    RegimeEmetteur,
    TypeDocument,
)
from app.moteur.faits import Fait, SchemaDeFaits, TypeFait

__all__ = ["SCHEMA_FACTURE"]


def _partie(prefixe: str, qui: str) -> tuple[Fait, ...]:
    """Les faits d'une partie à l'opération.

    Émetteur et destinataire portent les mêmes champs mais ne commandent pas les mêmes
    règles : le régime de l'émetteur dit s'il facture la taxe, celui du destinataire
    dit s'il peut la récupérer. Les déclarer par une fonction évite qu'ils divergent
    par recopie.
    """
    return (
        Fait(
            code=f"{prefixe}.denomination",
            type=TypeFait.TEXTE,
            libelle=f"Dénomination {qui}",
        ),
        Fait(
            code=f"{prefixe}.niu",
            type=TypeFait.TEXTE,
            libelle=f"Identifiant fiscal {qui}",
        ),
        Fait(
            code=f"{prefixe}.niu_actif",
            type=TypeFait.BOOLEEN,
            libelle=f"Identifiant fiscal {qui} actif",
        ),
        Fait(code=f"{prefixe}.rccm", type=TypeFait.TEXTE, libelle=f"Immatriculation {qui}"),
        Fait(
            code=f"{prefixe}.regime",
            type=TypeFait.ENUM,
            libelle=f"Régime fiscal {qui}",
            valeurs=tuple(membre.value for membre in RegimeEmetteur),
        ),
        Fait(
            code=f"{prefixe}.etranger",
            type=TypeFait.BOOLEEN,
            libelle=f"{qui.capitalize()} établi hors du pays",
        ),
    )


SCHEMA_FACTURE = SchemaDeFaits(
    domaine="CONFORMITE_FACTURE",
    faits=(
        # ── Le document ────────────────────────────────────────────────────────
        Fait(
            code="document.type",
            type=TypeFait.ENUM,
            libelle="Nature du document",
            valeurs=tuple(membre.value for membre in TypeDocument),
        ),
        Fait(code="document.reference", type=TypeFait.TEXTE, libelle="Référence du document"),
        Fait(code="document.date_emission", type=TypeFait.DATE, libelle="Date d'émission"),
        Fait(code="document.devise", type=TypeFait.TEXTE, libelle="Devise"),
        # ── Les parties ────────────────────────────────────────────────────────
        *_partie("emetteur", "de l'émetteur"),
        *_partie("destinataire", "du destinataire"),
        # ── Les montants ───────────────────────────────────────────────────────
        Fait(
            code="montants.total_ht",
            type=TypeFait.DECIMAL,
            libelle="Total hors taxes",
            unite="FCFA",
        ),
        Fait(
            code="montants.total_tva",
            type=TypeFait.DECIMAL,
            libelle="Total de la taxe",
            unite="FCFA",
        ),
        Fait(
            code="montants.total_ttc",
            type=TypeFait.DECIMAL,
            libelle="Total toutes taxes comprises",
            unite="FCFA",
        ),
        Fait(
            code="montants.somme_lignes_ht",
            type=TypeFait.DECIMAL,
            libelle="Somme des lignes hors taxes, recalculée",
            unite="FCFA",
        ),
        # ── Le règlement ───────────────────────────────────────────────────────
        Fait(
            code="reglement.mode",
            type=TypeFait.ENUM,
            libelle="Mode de règlement",
            valeurs=tuple(membre.value for membre in ModeReglement),
        ),
        Fait(code="reglement.date_reglement", type=TypeFait.DATE, libelle="Date du règlement"),
        # ── Le détail ──────────────────────────────────────────────────────────
        Fait(code="lignes", type=TypeFait.LISTE, libelle="Lignes de détail"),
        Fait(code="lignes[].designation", type=TypeFait.TEXTE, libelle="Désignation de la ligne"),
        Fait(code="lignes[].quantite", type=TypeFait.DECIMAL, libelle="Quantité"),
        Fait(
            code="lignes[].prix_unitaire_ht",
            type=TypeFait.DECIMAL,
            libelle="Prix unitaire hors taxes",
            unite="FCFA",
        ),
        Fait(
            code="lignes[].montant_ht",
            type=TypeFait.DECIMAL,
            libelle="Montant de la ligne hors taxes",
            unite="FCFA",
        ),
        Fait(
            code="lignes[].taux_tva",
            type=TypeFait.DECIMAL,
            libelle="Taux de taxe de la ligne",
            unite="%",
        ),
        # ── Ce que la facture ne porte pas ─────────────────────────────────────
        # Ces faits viennent d'ailleurs — la collecte pour les doublons, le
        # portefeuille pour l'état de l'exercice. Ils sont dans le schéma parce
        # qu'une règle a le droit de s'en servir, et hors de la facture parce
        # qu'elle ne les connaît pas.
        Fait(
            code="contexte.doublons_potentiels",
            type=TypeFait.ENTIER,
            libelle="Pièces semblables déjà déposées",
            unite="pièces",
        ),
        Fait(
            code="contexte.exercice_clos",
            type=TypeFait.BOOLEEN,
            libelle="Exercice de rattachement clôturé",
        ),
    ),
)
