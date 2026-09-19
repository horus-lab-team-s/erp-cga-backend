"""Tenir le journal : enregistrer, valider, contre-passer.

─────────────────────────────────────────────────────────────────────────────────
CE MODULE EST LA PREMIÈRE ÉCRITURE DU PRODUIT

Jusqu'ici, tout le contexte E **lisait**. La balance, le grand livre, la santé
d'avant dépôt se calculaient sur des écritures amenées par le jeu de démonstration.
Un cabinet ne pouvait donc pas produire : il pouvait consulter une comptabilité
que personne n'avait moyen de tenir.

CE QUE LE DOMAINE GARANTIT DÉJÀ, ET QU'ON NE REFAIT PAS ICI

`EcritureComptable` refuse une écriture déséquilibrée, une validation qui ne nomme
personne, une contre-passation sans motif, et interdit de modifier une écriture
validée. Ces invariants sont **dans l'entité**, où ils tiennent quel que soit
l'appelant. Ce module ne les répète pas ; il ajoute les vérifications qu'une
écriture seule ne peut pas faire, parce qu'elles portent sur son environnement :

    le journal existe-t-il           le domaine ne connaît pas la liste
    les comptes existent-ils         le domaine ne valide que la forme du numéro
    l'exercice est-il ouvert         l'exercice appartient au portefeuille
    la date tombe-t-elle dedans      idem

Sans ces quatre contrôles, on saisit sur un journal inventé, un compte qui
n'existe pas au plan, ou dans un exercice déjà déposé à la DGI. Les trois se
rattrapent, mais tard, et la troisième se rattrape devant un vérificateur.

⚠️ CE QUI N'EST DÉLIBÉRÉMENT PAS CONTRÔLÉ : LA SÉPARATION DES TÂCHES

Un contrôle interne orthodoxe exigerait que le valideur ne soit pas le saisisseur.
Ce n'est **pas** imposé, et le choix se défend : dans un cabinet où un seul
comptable tient un dossier, l'imposer rendrait la validation impossible et l'on
contournerait en partageant un compte, ce qui détruirait la piste d'audit qu'on
cherchait à protéger. La règle des quatre yeux est une politique de cabinet, pas
une loi ; elle appartient au référentiel, pas au code. La question est ouverte
dans `Docs/architecture/09-questions-ouvertes.md`.

Ce qui est garanti, en revanche, c'est que la validation **nomme** son auteur et
l'horodate : le contrôle a posteriori reste possible, même sans séparation.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.contextes.comptabilite.domaine.cloture_mensuelle import (
    PeriodeVerrouillee,
    periode_verrouillee_au,
)
from app.contextes.comptabilite.domaine.entites import (
    Compte,
    EcritureComptable,
    EtatEcriture,
    Journal,
    LigneEcriture,
    TypeEcriture,
)
from app.contextes.comptabilite.domaine.ports import DepotEcritures
from app.contextes.portefeuille.contrats import Exercice
from app.partage.copie import transiter
from app.partage.erreurs import message_lisible

__all__ = [
    "ApercuDeContrepassation",
    "apercevoir_la_contrepassation",
    "BrouillonEcriture",
    "CompteInconnu",
    "ContrepassationAntidatee",
    "ContrepassationEnDouble",
    "CorrectionDeBrouillon",
    "CorrectionRefusee",
    "DateHorsExercice",
    "ExerciceClos",
    "JournalInconnu",
    "MoisVerrouille",
    "SaisieRefusee",
    "contrepasser_une_ecriture",
    "corriger_un_brouillon",
    "enregistrer_une_ecriture",
    "valider_une_ecriture",
    "verifier_l_environnement",
]


class SaisieRefusee(ValueError):
    """Racine des refus de saisie.

    Une racine commune plutôt que quatre exceptions sans lien : l'adaptateur HTTP
    les traduit toutes en un même 409, et un appelant qui veut distinguer le motif
    lit le type précis. Sans racine, chaque nouveau contrôle obligerait à retoucher
    la route, et l'on finirait par en oublier un — le refus deviendrait alors un
    500, c'est-à-dire un défaut du logiciel là où il n'y a qu'une saisie fautive.
    """


class JournalInconnu(SaisieRefusee):
    """Le code de journal n'est pas ouvert pour ce dossier."""


class CompteInconnu(SaisieRefusee):
    """Un compte de l'écriture n'existe pas au plan.

    Refuser plutôt que créer le compte à la volée. Un plan comptable qui s'enrichit
    par faute de frappe produit des comptes jumeaux — 6011 et 60111 — dont la
    balance ne fait plus la somme, et personne ne s'en aperçoit avant la clôture.
    """


class ExerciceClos(SaisieRefusee):
    """L'exercice est clos : plus rien ne s'y écrit.

    C'est le contrôle qui protège la déclaration déjà déposée. Écrire dans un
    exercice clos rendrait la liasse remise à l'administration fausse **après**
    coup, et le cabinet ne saurait même pas que sa copie a changé.
    """


class DateHorsExercice(SaisieRefusee):
    """La date d'opération tombe hors des bornes de l'exercice.

    ⚠️ Les deux bornes sont **incluses** : un exercice va du 1er janvier au
    31 décembre. C'est l'usage comptable, et il diffère des périodes de statut du
    portefeuille, dont la borne haute est exclue.
    """


class ContrepassationAntidatee(SaisieRefusee):
    """La contre-passation serait datée d'avant l'écriture qu'elle annule (pas 71).

    Annuler une opération avant qu'elle ait eu lieu ferait bouger une balance déjà
    éditée, sur une période où l'opération n'existait pas encore.
    """


class MoisVerrouille(SaisieRefusee):
    """La date tombe dans un mois transmis au réviseur, ou validé par lui (pas 107).

    ─────────────────────────────────────────────────────────────────────────
    POURQUOI CE REFUS EXISTE

    Avant le pas 107, un mois transmis restait ouvert à l'écriture. Le réviseur contrôlait un
    échantillon figé, validait, et le comptable pouvait encore ajouter, corriger ou
    contre-passer dans ce mois : « le mois validé » ne désignait plus le mois qu'on avait
    relu. Le verrou se déduit des revues (voir `domaine/cloture_mensuelle.py`) ; ce refus
    est l'endroit unique où il s'applique, pour les quatre gestes qui écrivent.
    ─────────────────────────────────────────────────────────────────────────
    """


class ContrepassationEnDouble(SaisieRefusee):
    """L'écriture a déjà sa contre-passation (pas 110).

    ⚠️ Avant le pas 110, rien ne l'empêchait. Essai : contre-passer deux fois l'écriture
    2026/AC/000001 produisait les écritures 4 et 5, deux annulations d'une même facture ; une
    fois validées, la charge et la TVA déductible passaient **en négatif** au grand livre et à
    la déclaration du mois. Le double clic, ou deux onglets, suffisaient.
    """


class BrouillonEcriture(BaseModel):
    """Ce qu'un comptable soumet : une écriture sans numéro ni état.

    ─────────────────────────────────────────────────────────────────────────
    POURQUOI UN TYPE À PART, ET NON `EcritureComptable` DIRECTEMENT

    Parce que trois champs n'appartiennent pas à celui qui saisit :

        `numero`      la continuité de la séquence est une propriété du
                      registre entier, jamais d'une écriture isolée
        `etat`        une écriture qui naîtrait validée n'aurait jamais été relue
        `validee_par` se déduit de l'acte de validation, il ne se déclare pas

    Les accepter du client rendrait possible, par une simple requête, d'insérer
    une écriture validée au numéro d'une autre. Ce type-ci ne les porte pas : il
    n'y a donc rien à contrôler, ce qui vaut mieux qu'un contrôle.
    ─────────────────────────────────────────────────────────────────────────
    """

    model_config = ConfigDict(frozen=True)

    journal: str = Field(min_length=1, max_length=8)
    exercice: str = Field(min_length=1)
    date_operation: date
    libelle: str = Field(min_length=1)
    piece_justificative: str | None = None
    reference_externe: str | None = None
    lignes: list[LigneEcriture] = Field(min_length=2)


def verifier_l_environnement(
    brouillon: BrouillonEcriture,
    *,
    journaux: list[Journal],
    plan: list[Compte],
    exercice: Exercice | None,
    periodes_verrouillees: list[PeriodeVerrouillee] | tuple[PeriodeVerrouillee, ...],
) -> None:
    """Les quatre contrôles qu'une écriture seule ne peut pas faire, sans rien écrire.

    ─────────────────────────────────────────────────────────────────────────────
    POURQUOI CETTE FONCTION EST PUBLIQUE, ALORS QU'ELLE NE FAIT RIEN

    Parce qu'un appelant peut avoir besoin de savoir **avant d'écrire**, et qu'il
    n'y a qu'un cas de ce genre : la reprise d'un lot.

    Un lot de reprise fait quatre mille écritures. Les enregistrer une à une et
    s'arrêter à la trois centième laisserait deux cent quatre-vingt-dix-neuf
    écritures en base et un exploitant qui ne sait pas lesquelles. Le grand livre
    serait alors déséquilibré, et personne ne saurait de combien.

    ⚠️ **La reprise contrôle donc tout, puis écrit tout.** Ce n'est pas une
    optimisation : c'est la seule façon de tenir « rien n'entre à moitié » sans
    dépendre du fait qu'une transaction couvre bien l'appel, ce qui est vrai
    aujourd'hui et qu'aucun cas ne garde.

    Extraire ces trois lignes plutôt que les recopier dans la reprise évite le
    défaut qui se paie le plus cher ici : deux contrôles qui divergent, celui de
    la saisie devenant plus strict que celui de l'import, et un lot qui entre par
    une porte ce que l'autre refuse.
    ─────────────────────────────────────────────────────────────────────────────
    """
    _exiger_le_journal(brouillon.journal, journaux)
    _exiger_les_comptes(brouillon.lignes, plan)
    _exiger_un_exercice_ouvert(brouillon, exercice)
    _exiger_un_mois_ouvert(brouillon.date_operation, periodes_verrouillees)


def enregistrer_une_ecriture(
    brouillon: BrouillonEcriture,
    *,
    journaux: list[Journal],
    plan: list[Compte],
    exercice: Exercice | None,
    periodes_verrouillees: list[PeriodeVerrouillee] | tuple[PeriodeVerrouillee, ...],
    depot: DepotEcritures,
    par: str,
) -> EcritureComptable:
    """Enregistre un brouillon, numéroté par le registre.

    L'écriture naît **en brouillon**, et c'est structurant : le comptable relit
    avant d'engager. Un logiciel qui validerait à la saisie déplacerait la
    responsabilité vers l'éditeur, alors que c'est le Centre qui engage son
    agrément.

    ⚠️ `prochain_numero` puis `enregistrer` ne forment pas un acte atomique : deux
    saisies simultanées sur le même journal obtiendraient le même numéro, et la
    seconde échouerait sur la clé primaire. Rien de faux n'est écrit — c'est un
    trou évité, pas un doublon créé — et l'appelant recommence. La limite est
    documentée dans `depots_sql.py` ; sur le volume d'un cabinet, la collision est
    théorique.
    """
    verifier_l_environnement(
        brouillon,
        journaux=journaux,
        plan=plan,
        exercice=exercice,
        periodes_verrouillees=periodes_verrouillees,
    )

    ecriture = EcritureComptable(
        journal=brouillon.journal,
        exercice=brouillon.exercice,
        numero=depot.prochain_numero(brouillon.exercice, brouillon.journal),
        date_operation=brouillon.date_operation,
        libelle=brouillon.libelle,
        piece_justificative=brouillon.piece_justificative,
        reference_externe=brouillon.reference_externe,
        lignes=list(brouillon.lignes),
        etat=EtatEcriture.BROUILLON,
        saisie_par=par,
    )
    depot.enregistrer(ecriture)
    return ecriture


class CorrectionRefusee(ValueError):
    """Ce brouillon ne se corrige pas : il est validé, ou c'est une contre-passation (pas 72)."""


class CorrectionDeBrouillon(BaseModel):
    """Ce qu'une correction peut changer d'un brouillon : son contenu, pas son identité.

    ⚠️ **Ni journal, ni exercice, ni numéro.** Ils forment la clé de l'écriture, et
    la clé est ce que la séquence protège. Changer de journal reviendrait à retirer
    un numéro d'un journal pour en prendre un dans un autre : un trou d'un côté, un
    saut de l'autre. Une écriture passée au mauvais journal se corrige autrement.

    ⚠️ `extra="forbid"` : un `numero`, un `etat` ou un `saisie_par` glissé dans la
    requête est refusé, comme pour toute requête de ce projet.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    date_operation: date
    libelle: str = Field(min_length=1)
    piece_justificative: str | None = None
    reference_externe: str | None = None
    lignes: list[LigneEcriture] = Field(min_length=2)


def corriger_un_brouillon(
    cle: str,
    correction: CorrectionDeBrouillon,
    *,
    journaux: list[Journal],
    plan: list[Compte],
    exercice: Exercice | None,
    periodes_verrouillees: list[PeriodeVerrouillee] | tuple[PeriodeVerrouillee, ...],
    depot: DepotEcritures,
    par: str,
) -> EcritureComptable:
    """Réécrit le contenu d'un brouillon, à son numéro (pas 72).

    ─────────────────────────────────────────────────────────────────────────
    ⚠️ POURQUOI CE CAS D'USAGE EXISTE

    Tout le code le promettait, rien ne le tenait :

        l'écran, après chaque saisie   « reste modifiable tant qu'elle n'est pas validée »
        l'entité                       `modifiable` vrai pour un brouillon, lu par personne
        la contre-passation, refusée   « un brouillon se corrige ou se supprime »
        la clôture, bloquée            « chacune est à valider ou à supprimer »

    Aucune route ne corrigeait, aucune ne supprimait. Essai sur PostgreSQL : un
    brouillon saisi **sans pièce justificative** ne se validait pas (une écriture
    validée porte sa pièce), ne se contre-passait pas (ce n'est pas une écriture
    validée), et bloquait la clôture de l'exercice **pour toujours**. Un oubli de
    saisie, qu'aucun champ de l'écran n'empêchait, fermait l'exercice à la clôture.

    ⚠️ LE NUMÉRO RESTE, ET C'EST POUR ÇA QU'ON CORRIGE PLUTÔT QU'ON SUPPRIME

    Supprimer laisserait un trou dans la séquence, et la clôture refuse aussi les
    trous. Corriger réécrit le contenu à la même place. Le dépôt refuse déjà
    d'écraser une écriture validée : la correction ne peut pas atteindre ce que la
    validation a engagé, même par un appel concurrent.

    ⚠️ CE QUI N'EST PAS CORRIGEABLE

    - **une écriture validée** : elle se contre-passe ;
    - **une contre-passation en brouillon** : ses lignes sont l'inverse exact de
      l'écriture annulée. Les réécrire en ferait une écriture ordinaire qui se dit
      contre-passation. Elle se valide telle quelle, ou l'on corrige l'origine autrement.

    La correction repasse **tous** les contrôles de la saisie (journal, comptes,
    exercice ouvert, date dans les bornes), et `saisie_par` devient l'auteur de la
    correction : c'est lui qui a écrit le contenu que la validation engagera.

    Une question reste ouverte (Q23) : un brouillon qui n'aurait jamais dû exister,
    ou quatre mille brouillons d'une reprise erronée, n'ont toujours pas d'issue
    autre que corriger, valider puis contre-passer.
    ─────────────────────────────────────────────────────────────────────────
    """
    ecriture = depot.lire(cle)
    if ecriture.etat is not EtatEcriture.BROUILLON:
        raise CorrectionRefusee(
            f"écriture {ecriture.cle} validée : elle ne se corrige plus. La contre-passer, "
            "puis saisir l'écriture juste."
        )
    if ecriture.type is TypeEcriture.CONTREPASSATION:
        raise CorrectionRefusee(
            f"écriture {ecriture.cle} : une contre-passation reproduit à l'inverse "
            f"l'écriture {ecriture.ecriture_contrepassee}, ligne pour ligne. La réécrire "
            "en ferait une écriture ordinaire qui se dit contre-passation."
        )
    # ⚠️ La date **d'origine** compte aussi : déplacer vers août un brouillon de juillet
    # retirerait une écriture d'un mois verrouillé, ce qui le change autant qu'en ajouter une.
    _exiger_un_mois_ouvert(ecriture.date_operation, periodes_verrouillees)
    brouillon = BrouillonEcriture(
        journal=ecriture.journal,
        exercice=ecriture.exercice,
        **correction.model_dump(),
    )
    verifier_l_environnement(
        brouillon,
        journaux=journaux,
        plan=plan,
        exercice=exercice,
        periodes_verrouillees=periodes_verrouillees,
    )
    corrigee = transiter(
        ecriture,
        date_operation=correction.date_operation,
        libelle=correction.libelle,
        piece_justificative=correction.piece_justificative,
        reference_externe=correction.reference_externe,
        lignes=list(correction.lignes),
        saisie_par=par,
    )
    depot.enregistrer(corrigee)
    return corrigee


def valider_une_ecriture(
    cle: str,
    *,
    exercice: Exercice | None,
    periodes_verrouillees: list[PeriodeVerrouillee] | tuple[PeriodeVerrouillee, ...],
    depot: DepotEcritures,
    par: str,
    le: datetime,
) -> EcritureComptable:
    """Engage l'écriture. Après quoi elle est immuable.

    La validation ne revérifie ni l'équilibre ni les comptes : ils ont été
    contrôlés à l'enregistrement, et le dépôt refuse d'écraser une écriture
    validée. Les revérifier ici donnerait l'illusion d'une garantie qui ne
    porterait que sur le chemin nominal.

    ─────────────────────────────────────────────────────────────────────────
    ⚠️ MAIS ELLE REVÉRIFIE L'EXERCICE (pas 71), ET CE N'EST PAS LA MÊME CHOSE

    L'équilibre d'une écriture ne change pas entre sa saisie et sa validation.
    **L'état de son exercice, si** : un brouillon saisi en novembre peut attendre,
    et l'exercice se clore entre-temps. La clôture refuse les brouillons qui
    subsistent, mais un brouillon pouvait naître après elle, par la
    contre-passation qui ne passait par aucun contrôle. Validé, il modifiait la
    balance d'un exercice clos, donc la liasse déjà remise.

    `exercice` est exigé, sans défaut : l'appelant le lit au portefeuille. `None`
    refuse, comme à la saisie.
    ─────────────────────────────────────────────────────────────────────────
    """
    ecriture = depot.lire(cle)
    _exiger_l_exercice(ecriture.exercice, ecriture.date_operation, exercice)
    _exiger_un_mois_ouvert(ecriture.date_operation, periodes_verrouillees)
    validee = ecriture.valider(par=par, le=le)
    depot.enregistrer(validee)
    return validee


def contrepasser_une_ecriture(
    cle: str,
    *,
    motif: str,
    jour: date,
    exercice: Exercice | None,
    periodes_verrouillees: list[PeriodeVerrouillee] | tuple[PeriodeVerrouillee, ...],
    depot: DepotEcritures,
    par: str,
) -> EcritureComptable:
    """Fabrique et enregistre l'écriture inverse.

    ⚠️ Elle porte la date du jour où l'on s'aperçoit de l'erreur, **jamais** celle
    de l'écriture d'origine. On ne retouche pas le passé : c'est le principe
    d'intangibilité, et c'est ce qui rend une piste d'audit défendable devant un
    vérificateur. Une contre-passation antidatée ferait bouger une balance déjà
    éditée, et le cabinet ne pourrait plus expliquer d'où vient l'écart.

    La contre-passation naît elle-même en brouillon : annuler est un acte
    comptable ordinaire, qui se relit comme un autre.

    ─────────────────────────────────────────────────────────────────────────
    ⚠️ LE COMMENTAIRE CI-DESSUS N'ÉTAIT TENU PAR AUCUNE LIGNE (pas 71)

    La date venait de la requête et n'était comparée à rien. Essai sur PostgreSQL,
    avant correction :

        l'exercice 2026 clos, contre-passer une écriture de 2026
            → brouillon **dans 2026**, daté du 15/01/2027, hors de ses bornes
            → validé : **la balance de l'exercice clos change**
        date demandée : 01/01/2020
            → acceptée, six ans avant l'écriture qu'elle annule

    La contre-passation passe désormais les contrôles de la saisie, plus un :

        exercice connu et ouvert            ExerciceClos
        date dans les bornes de l'exercice  DateHorsExercice
        date au plus tôt celle de l'origine ContrepassationAntidatee

    Le même jour que l'origine reste admis : c'est le jour du constat quand
    l'erreur se voit à la relecture. Ce qui est refusé, c'est d'annuler avant.

    ⚠️ Une erreur d'un exercice **clos** ne se contre-passe plus dans cet exercice.
    Elle se corrige dans l'exercice ouvert, par une écriture ordinaire et motivée :
    c'est l'usage, et c'est ce qui laisse la liasse remise conforme à sa copie.
    ─────────────────────────────────────────────────────────────────────────
    """
    origine = depot.lire(cle)
    _controler_la_contrepassation(origine, jour, exercice, periodes_verrouillees, depot)
    inverse = origine.contrepasser(
        numero=depot.prochain_numero(origine.exercice, origine.journal),
        date_operation=jour,
        motif=motif,
        saisie_par=par,
    )
    depot.enregistrer(inverse)
    return inverse


def _controler_la_contrepassation(
    origine: EcritureComptable,
    jour: date,
    exercice: Exercice | None,
    periodes_verrouillees: list[PeriodeVerrouillee] | tuple[PeriodeVerrouillee, ...],
    depot: DepotEcritures,
) -> None:
    """Les contrôles de la contre-passation, **un seul corps** pour le geste et son aperçu
    (pas 110) : un aperçu qui accepterait ce que le geste refuse serait pire que pas d'aperçu."""
    _exiger_l_exercice(origine.exercice, jour, exercice, contre_passation=True)
    # ⚠️ Seule la date de la contre-passation compte : l'écriture d'origine peut être dans un
    # mois verrouillé, et c'est justement ainsi qu'on la corrige (maquette, vue G).
    _exiger_un_mois_ouvert(jour, periodes_verrouillees, contre_passation=True)
    existante = next(
        (e for e in depot.lister(origine.exercice) if e.ecriture_contrepassee == origine.cle),
        None,
    )
    if existante is not None:
        etat = "en brouillon, à valider" if existante.etat is EtatEcriture.BROUILLON else "validée"
        raise ContrepassationEnDouble(
            f"l'écriture {origine.cle} est déjà contre-passée par {existante.cle} ({etat}). "
            "Une seconde contre-passation l'annulerait deux fois."
        )
    if jour < origine.date_operation:
        raise ContrepassationAntidatee(
            f"la contre-passation serait datée du {jour:%d/%m/%Y}, avant l'écriture "
            f"{origine.cle} du {origine.date_operation:%d/%m/%Y} qu'elle annule. Dater "
            "du jour où l'erreur est constatée."
        )


class ApercuDeContrepassation(BaseModel):
    """Ce que la contre-passation produirait, **sans rien écrire** (pas 110).

    Maquette « Parcours comptable », vue E : les lignes inversées se montrent **avant** de créer
    la contre-passation, « non modifiables », et un refus se dit avant l'envoi plutôt qu'après.
    """

    model_config = ConfigDict(frozen=True)

    origine: str
    date_operation: date
    #: L'écriture inverse telle qu'elle serait enregistrée, au numéro pressenti (pas réservé).
    inverse: EcritureComptable | None
    #: Pourquoi la contre-passation serait refusée, dans les mots du geste.
    refus: str | None


def apercevoir_la_contrepassation(
    cle: str,
    *,
    jour: date,
    exercice: Exercice | None,
    periodes_verrouillees: list[PeriodeVerrouillee] | tuple[PeriodeVerrouillee, ...],
    depot: DepotEcritures,
    par: str,
) -> ApercuDeContrepassation:
    """L'aperçu passe **les mêmes contrôles** que `contrepasser_une_ecriture`, et le même
    constructeur d'inverse. Le numéro est pressenti, jamais réservé : réserver ferait un trou
    si le comptable renonce."""
    origine = depot.lire(cle)
    try:
        _controler_la_contrepassation(origine, jour, exercice, periodes_verrouillees, depot)
        inverse = origine.contrepasser(
            numero=depot.prochain_numero(origine.exercice, origine.journal),
            date_operation=jour,
            motif="aperçu, motif à saisir",
            saisie_par=par,
        )
    except ValueError as refus:
        return ApercuDeContrepassation(
            origine=origine.cle, date_operation=jour, inverse=None, refus=message_lisible(refus)
        )
    return ApercuDeContrepassation(
        origine=origine.cle, date_operation=jour, inverse=inverse, refus=None
    )


# ── Les quatre contrôles d'environnement ─────────────────────────────────────
def _exiger_le_journal(code: str, journaux: list[Journal]) -> None:
    if not any(journal.code == code for journal in journaux):
        ouverts = ", ".join(sorted(journal.code for journal in journaux)) or "aucun"
        raise JournalInconnu(
            f"journal « {code} » inconnu pour ce dossier. Journaux ouverts : {ouverts}."
        )


def _exiger_les_comptes(lignes: list[LigneEcriture], plan: list[Compte]) -> None:
    """Chaque compte mouvementé doit exister au plan, à l'identique.

    ⚠️ Pas de tolérance sur la racine : un compte 6011 n'autorise pas à saisir sur
    60119. Un plan qui s'étend par tolérance devient un plan que personne n'a
    arrêté, et la balance cesse de se rapprocher du grand livre.
    """
    ouverts = {compte.numero for compte in plan}
    inconnus = sorted({ligne.compte for ligne in lignes} - ouverts)
    if inconnus:
        raise CompteInconnu(
            "compte(s) absent(s) du plan : " + ", ".join(inconnus) + ". "
            "Ouvrir le compte au plan avant de saisir dessus."
        )


def _exiger_un_exercice_ouvert(brouillon: BrouillonEcriture, exercice: Exercice | None) -> None:
    """L'exercice existe, n'est pas clos, et contient la date d'opération.

    ⚠️ `exercice is None` **refuse** au lieu de laisser passer. Saisir dans un
    exercice que le portefeuille ne connaît pas produirait des écritures qu'aucune
    liasse ne ramasserait : elles existeraient sans jamais apparaître nulle part,
    ce qui est la pire forme de perte — celle qu'on ne voit pas.
    """
    _exiger_l_exercice(brouillon.exercice, brouillon.date_operation, exercice)


def _exiger_l_exercice(
    libelle: str,
    date_operation: date,
    exercice: Exercice | None,
    *,
    contre_passation: bool = False,
) -> None:
    """Le contrôle d'exercice, commun à la saisie, à la validation et à la
    contre-passation (pas 71).

    ⚠️ Un seul corps pour les trois gestes : trois copies divergeraient, et c'est
    précisément la contre-passation, restée sans contrôle, qui ouvrait l'exercice
    clos. `contre_passation` ne change que les phrases, jamais la règle.
    """
    if exercice is None:
        raise ExerciceClos(
            f"exercice « {libelle} » inconnu de ce dossier : "
            "aucune écriture ne peut y être rattachée."
        )
    if exercice.libelle != libelle:
        # Un appelant qui passerait l'exercice d'une autre écriture ferait contrôler
        # la mauvaise chose ; mieux vaut un refus bruyant qu'un contrôle faux.
        raise ValueError(
            f"contrôle d'exercice incohérent : écriture de {libelle}, exercice "
            f"{exercice.libelle} fourni."
        )
    if exercice.clos:
        if contre_passation:
            raise ExerciceClos(
                f"exercice {exercice.libelle} clos : une écriture de cet exercice ne se "
                "contre-passe plus. Corriger dans l'exercice ouvert, par une écriture "
                "ordinaire et motivée : la liasse remise reste ainsi conforme à sa copie."
            )
        raise ExerciceClos(
            f"exercice {exercice.libelle} clos : plus aucune écriture ne s'y ajoute. "
            "Passer par l'exercice suivant, ou rouvrir l'exercice si la clôture était "
            "prématurée."
        )
    if not exercice.ouverture <= date_operation <= exercice.cloture:
        conseil = (
            f" Une erreur d'un exercice encore ouvert se corrige dans cet exercice : "
            f"dater au plus tard du {exercice.cloture:%d/%m/%Y}."
            if contre_passation and date_operation > exercice.cloture
            else " Les deux bornes sont incluses."
        )
        raise DateHorsExercice(
            f"le {date_operation:%d/%m/%Y} tombe hors de l'exercice "
            f"{exercice.libelle} ({exercice.ouverture:%d/%m/%Y} au "
            f"{exercice.cloture:%d/%m/%Y})." + conseil
        )


def _exiger_un_mois_ouvert(
    jour: date,
    periodes_verrouillees: list[PeriodeVerrouillee] | tuple[PeriodeVerrouillee, ...],
    *,
    contre_passation: bool = False,
) -> None:
    """Le contrôle du verrou mensuel (pas 107), commun aux quatre gestes qui écrivent.

    ⚠️ `periodes_verrouillees` est **exigé, sans défaut**, pour la leçon du pas 71 : un
    contrôle dont l'argument a un défaut « rien de verrouillé » est un contrôle qu'un nouvel
    appelant oublie sans que rien ne le signale. Un appelant qui n'a rien à verrouiller le
    dit en passant `()`, et son choix se lit.
    """
    periode = periode_verrouillee_au(jour, periodes_verrouillees)
    if periode is None:
        return
    etat = "validé par le réviseur" if periode.statut.value == "VALIDEE" else "transmis au réviseur"
    conseil = (
        f" Dater la contre-passation après le {periode.au:%d/%m/%Y} : l'écriture d'origine "
        "reste dans son mois, la correction se lit au grand livre du mois ouvert."
        if contre_passation
        else " Passer la correction dans un mois ouvert, par une contre-passation"
        + (
            ", ou demander au réviseur de renvoyer le mois."
            if periode.statut.value == "TRANSMISE"
            else "."
        )
    )
    raise MoisVerrouille(
        f"le {jour:%d/%m/%Y} tombe dans la période du {periode.du:%d/%m/%Y} au "
        f"{periode.au:%d/%m/%Y}, {etat} ({periode.par}, le {periode.depuis:%d/%m/%Y}) : "
        "elle est verrouillée." + conseil
    )
