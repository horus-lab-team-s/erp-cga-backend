"""L'échange d'écritures avec le logiciel du client. **Un moteur, pas un pilote.**

─────────────────────────────────────────────────────────────────────────────────
POURQUOI UN PROFIL DÉCLARÉ, ET NON UNE CLASSE PAR LOGICIEL

Un centre de gestion ne remplace pas le logiciel de ses adhérents : il s'y branche.
Chaque adhérent arrive avec le sien — Sage, un tableur, un progiciel régional, celui
que son expert-comptable impose — et la plateforme doit s'adapter au système final,
jamais l'inverse.

Écrire `ExportateurSage`, puis `ExportateurCegid`, puis `ExportateurOdoo` conduit au
même endroit à chaque fois : trois classes qui font la même chose à trois virgules
près, qui divergent au premier correctif, et un quatrième logiciel qui demande un
déploiement.

⚠️ **Le format est donc une donnée, pas du code.** Un profil est un fichier du
référentiel : séparateur, encodage, format de date, ordre des colonnes. Brancher un
logiciel de plus, c'est écrire un fichier — pas modifier ce module.

C'est la même discipline que la grille d'affectation, le plan de relance et les
délais de veille. *Ce qui varie d'un client à l'autre n'appartient pas au code.*

CE QUE LE PIVOT TRANSPORTE

`EcritureComptable` **est** le pivot : il porte le journal, l'exercice, le numéro,
la date, le libellé, la pièce, la référence externe, et ses lignes avec compte,
sens, montant, tiers et lettrage. Rien à inventer.

⚠️ **Une ligne de fichier par ligne d'écriture**, l'en-tête répété. C'est la forme
qu'attendent tous les importeurs comptables connus, et elle n'est pas négociable :
un format à deux niveaux — une ligne d'en-tête puis ses lignes — se lit mal par un
tableur et se refuse par la plupart des progiciels.

TROIS PIÈGES QUI COÛTENT UN FICHIER ENTIER

**L'encodage.** Beaucoup d'importeurs francophones attendent `cp1252`, pas
`utf-8`. Une facture « Quincaillerie du Wouri — société » importée avec le mauvais
encodage donne des caractères illisibles dans **tous** les libellés, et le client
découvre le problème après avoir importé. L'encodage est donc au profil, déclaré,
jamais supposé.

**Le séparateur dans un libellé.** Un libellé qui contient le séparateur décale
toutes les colonnes suivantes, et l'importeur rejette la ligne — ou pire, l'accepte
en mettant le montant dans le champ tiers. Voir `_assainir`.

**Le séparateur de milliers.** `1 234 567,89` est lu comme `1` par un importeur qui
coupe au premier caractère non numérique. Aucun montant n'en porte jamais ici.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.contextes.comptabilite.domaine.entites import (
    EcritureComptable,
    EtatEcriture,
    LigneEcriture,
    Sens,
)

__all__ = [
    "CHAMPS_DISPONIBLES",
    "CHAMPS_INDISPENSABLES_A_LA_LECTURE",
    "Anomalie",
    "Colonne",
    "EchangeRefuse",
    "FormeDuSens",
    "LotLu",
    "ProfilDEchange",
    "lire",
    "rendre",
]

#: Sans ces champs, un fichier ne se relit pas. Voir `lire` pour le détail de
#: chacun : ce sont ceux qui portent l'**identité** d'une écriture et le
#: **mouvement** d'une ligne, et aucun ne se devine.
#:
#: ⚠️ `exercice` n'y figure pas : on importe toujours *dans* un exercice nommé
#: par l'appelant, et un fichier qui en porterait un autre serait une erreur de
#: l'exploitant, pas une donnée à reprendre.
CHAMPS_INDISPENSABLES_A_LA_LECTURE: frozenset[str] = frozenset(
    {"numero", "date_operation", "compte"}
)


class EchangeRefuse(ValueError):
    """L'export ne peut pas se faire, et le message dit pourquoi."""


class FormeDuSens(StrEnum):
    """Comment le logiciel destinataire exprime le débit et le crédit.

    Les deux formes existent dans la nature, et aucune ne se déduit de l'autre :
    un fichier à colonnes séparées porte un montant vide côté opposé, un fichier à
    colonne unique porte un marqueur.
    """

    #: Deux colonnes, `debit` et `credit` ; l'une porte le montant, l'autre est vide.
    COLONNES_SEPAREES = "COLONNES_SEPAREES"
    #: Une colonne de montant, une colonne de sens portant un marqueur — « D »/« C ».
    COLONNE_UNIQUE = "COLONNE_UNIQUE"


#: Le vocabulaire d'un profil. ⚠️ **Une liste close, et c'est le point.**
#:
#: Un profil qui nommerait un champ inconnu produirait une colonne vide sans que
#: rien ne le dise, et le client importerait un fichier amputé en croyant qu'il
#: est complet. Le chargeur refuse donc un champ hors de cette liste.
CHAMPS_DISPONIBLES: frozenset[str] = frozenset(
    {
        "journal",
        "exercice",
        "numero",
        "date_operation",
        "libelle_ecriture",
        "piece_justificative",
        "reference_externe",
        "compte",
        "libelle_ligne",
        "sens",
        "montant",
        "debit",
        "credit",
        "tiers",
        "lettrage",
    }
)


class Colonne(BaseModel):
    """Une colonne du fichier produit."""

    model_config = ConfigDict(frozen=True)

    champ: str
    #: L'intitulé de la colonne, si le profil demande une ligne d'en-tête. Vide,
    #: c'est le nom du champ qui sert : un profil qui n'a rien à dire de plus n'a
    #: pas à le répéter.
    intitule: str = ""
    #: Largeur fixe, complétée à gauche par des zéros pour les comptes. `0` laisse
    #: la valeur telle quelle.
    #:
    #: ⚠️ Certains progiciels exigent des comptes de longueur fixe — `401` devient
    #: `40100000`. Ne pas compléter produit un import accepté et des comptes
    #: inconnus, ce qui est pire qu'un refus.
    largeur: int = Field(default=0, ge=0, le=40)
    #: Le caractère de remplissage, quand `largeur` est posée.
    remplissage: str = "0"
    #: Complète à droite plutôt qu'à gauche. Vrai pour un libellé, faux pour un
    #: compte.
    a_droite: bool = False


class ProfilDEchange(BaseModel):
    """Comment un logiciel donné veut recevoir des écritures."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1)
    libelle: str = Field(min_length=1)
    #: À quoi ce profil sert, et ce qu'il ne fait pas. Repris tel quel dans la
    #: réponse de l'API : un exploitant qui choisit un profil doit lire ce que le
    #: centre en sait sans ouvrir le référentiel.
    remarque: str = ""

    separateur: str = Field(default=";", min_length=1, max_length=1)
    #: ⚠️ Déclaré, jamais supposé. Voir l'en-tête : un mauvais encodage abîme
    #: **tous** les libellés, et le client s'en aperçoit après l'import.
    encodage: str = "cp1252"
    #: `\r\n` pour les progiciels hérités, `\n` pour les modernes. Le mauvais
    #: choix se voit tout de suite — c'est le seul des trois pièges qui se
    #: signale.
    fin_de_ligne: str = "\r\n"
    decimale: str = Field(default=",", min_length=1, max_length=1)
    format_date: str = "%d/%m/%Y"
    entete: bool = True

    forme_du_sens: FormeDuSens = FormeDuSens.COLONNES_SEPAREES
    marqueur_debit: str = "D"
    marqueur_credit: str = "C"

    colonnes: tuple[Colonne, ...] = Field(min_length=1)


def rendre(
    ecritures: Sequence[EcritureComptable], profil: ProfilDEchange
) -> str:
    """Le contenu du fichier, prêt à être encodé selon le profil.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **SEULES LES ÉCRITURES VALIDÉES SORTENT.**

    Un brouillon est une écriture que le centre n'a pas engagée. L'exporter
    pousserait dans le système du client des mouvements que personne n'assume, et
    le client les prendrait pour des mouvements arrêtés — il tient sa comptabilité
    avec, la déclare, et découvre au contrôle que le centre les a depuis
    corrigés.

    Le refus est **franc et nominatif** : il nomme les écritures en cause. Un
    export qui filtrerait en silence livrerait un fichier incomplet, et l'écart ne
    se verrait qu'à la balance.

    ⚠️ **L'ÉQUILIBRE EST REVÉRIFIÉ.** L'entité le garantit déjà, et on le vérifie
    quand même : un lot déséquilibré est refusé **en entier** par le logiciel
    destinataire, sans dire lequel. Le dire ici coûte une addition et fait gagner
    une demi-journée de recherche.
    ─────────────────────────────────────────────────────────────────────────────
    """
    brouillons = [
        f"{e.journal}/{e.numero}" for e in ecritures if e.etat is not EtatEcriture.VALIDEE
    ]
    if brouillons:
        raise EchangeRefuse(
            f"{len(brouillons)} écriture(s) non validée(s) dans le lot : "
            f"{', '.join(brouillons[:5])}"
            f"{'…' if len(brouillons) > 5 else ''}. "
            "Un brouillon n'est pas engagé par le centre : l'exporter le ferait "
            "prendre pour un mouvement arrêté."
        )

    lignes: list[str] = []
    if profil.entete:
        lignes.append(
            profil.separateur.join(c.intitule or c.champ for c in profil.colonnes)
        )

    for ecriture in ecritures:
        mouvements = ecriture.lignes
        debit = sum((x.montant for x in mouvements if x.au_debit), Decimal(0))
        credit = sum((x.montant for x in mouvements if not x.au_debit), Decimal(0))
        if debit != credit:
            raise EchangeRefuse(
                f"écriture {ecriture.journal}/{ecriture.numero} déséquilibrée à "
                f"l'export : débit {debit}, crédit {credit}. Le logiciel "
                "destinataire refuserait le lot entier sans dire laquelle."
            )
        for ligne in ecriture.lignes:
            lignes.append(
                profil.separateur.join(
                    _rendre_colonne(ecriture, ligne, colonne, profil)
                    for colonne in profil.colonnes
                )
            )

    # ⚠️ La fin de ligne clôt **aussi** la dernière : un fichier dont la dernière
    # ligne n'est pas terminée est tronqué pour la moitié des importeurs, et la
    # dernière écriture disparaît sans message.
    return profil.fin_de_ligne.join(lignes) + profil.fin_de_ligne if lignes else ""


def _rendre_colonne(ecriture, ligne, colonne: Colonne, profil: ProfilDEchange) -> str:
    valeur = _valeur(ecriture, ligne, colonne.champ, profil)
    valeur = _assainir(valeur, profil)
    if colonne.largeur:
        valeur = (
            valeur.ljust(colonne.largeur, colonne.remplissage)
            if colonne.a_droite
            else valeur.rjust(colonne.largeur, colonne.remplissage)
        )[: colonne.largeur]
    return valeur


def _valeur(ecriture, ligne, champ: str, profil: ProfilDEchange) -> str:
    au_debit = ligne.sens is Sens.DEBIT
    match champ:
        case "journal":
            return ecriture.journal
        case "exercice":
            return ecriture.exercice
        case "numero":
            return str(ecriture.numero)
        case "date_operation":
            return ecriture.date_operation.strftime(profil.format_date)
        case "libelle_ecriture":
            return ecriture.libelle
        case "piece_justificative":
            return ecriture.piece_justificative or ""
        case "reference_externe":
            return ecriture.reference_externe or ""
        case "compte":
            return ligne.compte
        case "libelle_ligne":
            return ligne.libelle
        case "sens":
            return profil.marqueur_debit if au_debit else profil.marqueur_credit
        case "montant":
            return _montant(ligne.montant, profil)
        case "debit":
            return _montant(ligne.montant, profil) if au_debit else ""
        case "credit":
            return "" if au_debit else _montant(ligne.montant, profil)
        case "tiers":
            return ligne.tiers or ""
        case "lettrage":
            return ligne.lettrage or ""
    # Inatteignable : le chargeur refuse un champ hors de `CHAMPS_DISPONIBLES`.
    raise EchangeRefuse(f"champ « {champ} » inconnu du pivot d'échange.")


def _montant(montant: Decimal, profil: ProfilDEchange) -> str:
    """Deux décimales, jamais de séparateur de milliers.

    ⚠️ `1 234 567,89` est lu comme `1` par un importeur qui coupe au premier
    caractère non numérique — et il ne le dit pas. La balance importée est alors
    fausse d'un facteur mille, ce qui se voit ; ou d'un centime, ce qui ne se voit
    pas.
    """
    return f"{montant:.2f}".replace(".", profil.decimale)


def _assainir(valeur: str, profil: ProfilDEchange) -> str:
    """Retire du texte ce qui casserait le fichier.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **ON SUBSTITUE, ON N'ENTOURE PAS DE GUILLEMETS.**

    Entourer serait la réponse d'un lecteur CSV moderne. Les importeurs comptables
    hérités, eux, prennent le guillemet pour un caractère du libellé : le client
    voit `"Quincaillerie du Wouri"` avec les guillemets dans son grand livre, sur
    chaque ligne, pour toujours.

    Le séparateur devient une espace, et les fins de ligne aussi : un libellé qui
    contient un retour à la ligne coupe l'écriture en deux, et la seconde moitié
    est rejetée comme une ligne incomplète.

    C'est une perte d'information, et elle est assumée : un libellé comptable
    n'est pas un champ de texte libre, et le séparateur n'y porte aucun sens.
    ─────────────────────────────────────────────────────────────────────────────
    """
    for casseur in (profil.separateur, "\r", "\n", '"'):
        valeur = valeur.replace(casseur, " ")
    return valeur.strip()


# ═════════════════════════════════════════════════════════════════════════════
# LE SENS LECTURE
#
# ⚠️ **CE N'EST PAS LE MIROIR DE L'ÉCRITURE, ET LE CROIRE COÛTE CHER.**
#
# À l'export, les données sont les nôtres : le pivot les garantit, l'équilibre
# est tenu par l'entité, les comptes viennent du plan. Il ne reste qu'à mettre en
# forme, et la seule question est de savoir ce que le destinataire accepte.
#
# À l'import, **rien n'est garanti**. Le fichier vient d'un système que nous
# n'avons pas écrit, exporté par quelqu'un que nous ne connaissons pas, souvent
# repris à la main dans un tableur entre les deux. Il peut être tronqué, mal
# encodé, déséquilibré d'un centime, porter des comptes absents de notre plan, ou
# deux écritures différentes sous le même numéro.
#
# Trois règles en découlent, et elles tiennent tout ce module.
#
# **1 · Rien n'entre à moitié.** Un lot partiellement importé est un grand livre
# déséquilibré, et personne ne sait de combien. Le refus porte sur le lot entier.
#
# **2 · Toutes les anomalies d'un coup.** Un fichier de reprise fait quatre mille
# lignes et se corrige une fois. Rendre la première erreur seule condamne
# l'exploitant à quarante allers-retours ; il abandonnera avant, et saisira à la
# main.
#
# **3 · Ce qui entre est un brouillon.** Une écriture venue d'ailleurs n'a pas été
# validée par le centre. La faire entrer validée ferait engager la responsabilité
# du cabinet sur le travail d'un autre, sans qu'aucun collaborateur ne l'ait lue.
#
# C'est la symétrie exacte de l'export, et elle se retient en une phrase :
# **on n'exporte que du validé, on n'importe que du brouillon.**
# ═════════════════════════════════════════════════════════════════════════════


class Anomalie(BaseModel):
    """Ce qui empêche une ligne de fichier d'entrer, et où la corriger.

    ⚠️ `ligne` est le **numéro dans le fichier**, en comptant l'en-tête s'il y en
    a un, parce que c'est ce que montre le tableur de l'exploitant. Un numéro
    décalé d'une unité fait chercher au mauvais endroit, et l'exploitant conclut
    que l'outil se trompe.
    """

    model_config = ConfigDict(frozen=True)

    ligne: int = Field(ge=1)
    motif: str = Field(min_length=1)
    #: Le contenu brut de la ligne, tronqué. Sans lui, l'exploitant doit rouvrir
    #: le fichier pour comprendre ; avec lui, il voit tout de suite que la
    #: colonne montant contient une date.
    extrait: str = ""


class LotLu(BaseModel):
    """Le résultat d'une lecture : des écritures **ou** des anomalies, jamais les deux.

    Le lot est utilisable si et seulement si `anomalies` est vide. Un appelant qui
    enregistrerait `ecritures` sans regarder `anomalies` ne le pourrait pas : une
    lecture en échec ne rend aucune écriture.
    """

    model_config = ConfigDict(frozen=True)

    ecritures: tuple[EcritureComptable, ...] = ()
    #: La **première ligne du fichier** de chaque écriture, dans le même ordre
    #: qu'`ecritures`.
    #:
    #: ⚠️ Sans elle, un appelant qui refuse une écriture après coup ne peut pas
    #: dire où la corriger. Il mettrait alors un rang d'écriture dans un champ
    #: documenté comme un rang de fichier, et l'exploitant chercherait la ligne 3
    #: de son tableur pour une écriture qui commence à la ligne 47.
    rangs: tuple[int, ...] = ()
    anomalies: tuple[Anomalie, ...] = ()
    #: Nombre de lignes de mouvement lues, en-tête exclu. Rendu même en échec :
    #: c'est le premier chiffre que regarde un exploitant pour savoir si le
    #: fichier qu'il a choisi est bien celui qu'il croit.
    lignes_lues: int = 0

    @property
    def exploitable(self) -> bool:
        return not self.anomalies and bool(self.ecritures)


def lire(
    octets: bytes,
    profil: ProfilDEchange,
    *,
    exercice: str,
    journal_par_defaut: str | None = None,
    comptes_connus: frozenset[str] | None = None,
) -> LotLu:
    """Relit un fichier écrit par le logiciel du client, selon le **même** profil.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **UN SEUL PROFIL POUR LES DEUX SENS, ET C'EST L'ESSENTIEL.**

    Un profil de lecture séparé divergerait de son jumeau d'écriture au premier
    correctif : on corrigerait le séparateur d'un côté, et le fichier relu ne
    serait plus celui qu'on écrit. Le référentiel décrit **un format**, pas deux
    opérations.

    Cela impose une contrainte, et elle est saine : ce qu'un profil ne sait pas
    écrire, il ne sait pas le lire. Un logiciel dont on ne reçoit qu'un format et
    n'envoie rien demande quand même un profil complet.

    CE QUE LA LECTURE DOIT DÉFAIRE

    L'écriture **ajoute** des choses au pivot : elle complète les comptes à
    longueur fixe, elle remplace le point décimal, elle assainit les libellés. La
    lecture doit défaire les deux premières. La troisième est irréversible, et
    c'est assumé : une espace à la place d'un point-virgule ne se retrouve pas.

    ⚠️ Le profil dit ce qu'il a ajouté, donc la lecture sait quoi retirer. Un
    compte déclaré en largeur 8 complété de zéros à gauche rend `00000401` : la
    lecture retire les zéros de tête **parce que le profil dit qu'ils ont été
    mis**, jamais par flair. Sans cette précaution, un plan comptable qui utilise
    vraiment `00000401` serait mutilé à chaque import.

    CE QUI EST REFUSÉ, ET POURQUOI

    * Le fichier ne se décode pas, ou se décode en charabia (voir `_decoder`).
    * Le profil ne porte pas les colonnes indispensables au regroupement.
    * Une ligne n'a pas le bon nombre de colonnes.
    * Un montant n'est pas un nombre, ou est négatif, ou vaut zéro.
    * Une date ne suit pas le format déclaré.
    * Un compte est absent du plan, quand le plan est fourni.
    * Deux lignes portent le même numéro d'écriture et une date ou un libellé
      différents : il faudrait en choisir un, et ce choix n'appartient pas au code.
    * Une écriture ne s'équilibre pas.
    ─────────────────────────────────────────────────────────────────────────────
    """
    manquants = CHAMPS_INDISPENSABLES_A_LA_LECTURE - {c.champ for c in profil.colonnes}
    if manquants:
        raise EchangeRefuse(
            f"le profil « {profil.code} » ne porte pas {sorted(manquants)} : "
            "sans eux, on ne sait ni où finit une écriture ni ce que la ligne "
            "mouvemente. Ce profil sait écrire, il ne sait pas relire."
        )

    texte = _decoder(octets, profil)
    brutes = [ligne for ligne in texte.replace("\r\n", "\n").split("\n") if ligne.strip()]
    decalage = 1 if profil.entete else 0
    if profil.entete and brutes:
        brutes = brutes[1:]

    anomalies: list[Anomalie] = []
    # ⚠️ Un dictionnaire ordonné : les écritures ressortent dans l'ordre du
    # fichier. Un import qui réordonne rend la comparaison avec le fichier source
    # impossible, et c'est la première chose que fait un exploitant qui doute.
    en_cours: dict[tuple[str, int], _Assemblage] = {}

    for rang, brute in enumerate(brutes, start=decalage + 1):
        cellules = brute.split(profil.separateur)
        if len(cellules) != len(profil.colonnes):
            anomalies.append(
                Anomalie(
                    ligne=rang,
                    motif=(
                        f"{len(cellules)} colonne(s) au lieu de "
                        f"{len(profil.colonnes)}. Un séparateur dans un libellé, "
                        "ou un fichier qui n'est pas celui du profil choisi."
                    ),
                    extrait=brute[:120],
                )
            )
            continue

        champs = {
            colonne.champ: _delire(cellules[index], colonne)
            for index, colonne in enumerate(profil.colonnes)
        }
        _assembler(
            champs,
            rang=rang,
            brute=brute,
            profil=profil,
            exercice=exercice,
            journal_par_defaut=journal_par_defaut,
            comptes_connus=comptes_connus,
            en_cours=en_cours,
            anomalies=anomalies,
        )

    ecritures, rangs = _clore(en_cours, anomalies)
    # ⚠️ En échec, aucune écriture ne sort. Voir la règle 1 de l'en-tête : un
    # appelant distrait ne doit pas pouvoir enregistrer la moitié d'un lot.
    return LotLu(
        ecritures=() if anomalies else tuple(ecritures),
        rangs=() if anomalies else tuple(rangs),
        anomalies=tuple(anomalies),
        lignes_lues=len(brutes),
    )


#: Les traces d'un texte UTF-8 relu en cp1252. `Ã©` est un « é », `â€™` une
#: apostrophe typographique, `Ã‰` un « É ».
#:
#: ⚠️ **Le piège vient de ce que cp1252 ne proteste pas.** Presque tout octet y a
#: un sens, donc un fichier UTF-8 s'y décode sans la moindre erreur, et rend un
#: texte parfaitement valide et parfaitement faux. Le client importe, et découvre
#: « SociÃ©tÃ© » dans chaque libellé de son grand livre, définitivement.
_TRACES_DE_CHARABIA: tuple[str, ...] = ("Ã©", "Ã¨", "Ã ", "Ã´", "Ã§", "Ã‰", "â€™", "â€œ", "Ã»")


def _decoder(octets: bytes, profil: ProfilDEchange) -> str:
    """Décode selon le profil, et refuse le silence.

    Deux échecs, très différents l'un de l'autre :

    **Le décodage échoue.** Un fichier `cp1252` lu en `utf-8` casse sur le premier
    accent. C'est le bon cas : l'erreur est franche, immédiate, et le message dit
    quel encodage était attendu.

    **Le décodage réussit et ment.** C'est l'autre sens, et il ne se signale pas
    tout seul. Voir `_TRACES_DE_CHARABIA`.
    """
    try:
        texte = octets.decode(profil.encodage)
    except UnicodeDecodeError as echec:
        raise EchangeRefuse(
            f"le fichier ne se décode pas en « {profil.encodage} », que le profil "
            f"« {profil.libelle} » déclare (position {echec.start}). Le fichier "
            "vient probablement d'un autre logiciel que celui du profil choisi."
        ) from echec

    trouvees = [trace for trace in _TRACES_DE_CHARABIA if trace in texte]
    if trouvees:
        raise EchangeRefuse(
            f"le fichier se décode en « {profil.encodage} » mais donne du "
            f"charabia ({', '.join(trouvees[:3])}) : il est en UTF-8. "
            "L'import est refusé plutôt qu'accepté, car des libellés abîmés ne se "
            "rattrapent plus une fois entrés."
        )

    # ⚠️ La marque d'ordre d'octets d'un tableur. Sans ce retrait, le tout premier
    # intitulé de colonne porte un caractère invisible, et le premier champ de la
    # première ligne aussi quand il n'y a pas d'en-tête.
    return texte.lstrip("﻿")


def _delire(cellule: str, colonne: Colonne) -> str:
    """Retire ce que l'écriture avait ajouté à cette colonne, et rien d'autre.

    ⚠️ **On ne retire que ce que le profil déclare avoir mis.** Retirer les zéros
    de tête d'un compte dont le profil ne dit rien mutilerait un plan comptable
    qui les emploie vraiment.

    La valeur intégralement faite de remplissage rend une chaîne vide, et c'est le
    bon résultat : une colonne de zéros sur huit caractères est une colonne vide
    complétée, pas le compte numéro zéro.

    ⚠️ Le pivot interdit par ailleurs qu'un compte commence par un zéro, ce qui
    rend le dépadding sans danger **dans ce plan-là**. La règle reste énoncée dans
    l'autre sens, parce qu'elle vaut pour toutes les colonnes et que le jour où un
    plan étranger emploiera des zéros de tête, c'est le profil qui devra le dire.
    """
    valeur = cellule.strip()
    if colonne.largeur and colonne.remplissage:
        valeur = (
            valeur.rstrip(colonne.remplissage)
            if colonne.a_droite
            else valeur.lstrip(colonne.remplissage)
        )
    return valeur.strip()


class _Assemblage:
    """Une écriture en cours de construction, et les lignes de fichier qui l'ont faite.

    ⚠️ Elle retient `rangs` pour pouvoir dire **où** se trouve le déséquilibre
    quand il apparaît. Une écriture déséquilibrée signalée sans numéro de ligne
    envoie l'exploitant chercher dans quatre mille lignes.
    """

    __slots__ = (
        "cle",
        "exercice",
        "date",
        "libelle",
        "piece",
        "reference",
        "lignes",
        "rangs",
    )

    def __init__(
        self,
        cle: tuple[str, int],
        exercice: str,
        date: date,
        libelle: str,
        piece: str | None,
        reference: str | None,
    ) -> None:
        self.cle = cle
        self.exercice = exercice
        self.date = date
        self.libelle = libelle
        self.piece = piece
        self.reference = reference
        self.lignes: list[LigneEcriture] = []
        self.rangs: list[int] = []


def _assembler(
    champs: dict[str, str],
    *,
    rang: int,
    brute: str,
    profil: ProfilDEchange,
    exercice: str,
    journal_par_defaut: str | None,
    comptes_connus: frozenset[str] | None,
    en_cours: dict[tuple[str, int], _Assemblage],
    anomalies: list[Anomalie],
) -> None:
    """Range une ligne de fichier dans son écriture, ou dit pourquoi elle n'entre pas.

    ⚠️ **Chaque refus est motivé et rendu, la lecture continue.** S'arrêter à la
    première anomalie condamnerait l'exploitant à autant d'allers-retours qu'il y
    a d'erreurs. Voir la règle 2 de l'en-tête de section.
    """
    def refuser(motif: str) -> None:
        anomalies.append(Anomalie(ligne=rang, motif=motif, extrait=brute[:120]))

    journal = champs.get("journal") or journal_par_defaut
    if not journal:
        refuser(
            "aucun journal : ni la colonne ni l'appel n'en nomment un. "
            "Une écriture sans journal ne se retrouve pas dans le grand livre."
        )
        return

    brut_numero = champs.get("numero", "")
    if not brut_numero.isdigit() or int(brut_numero) <= 0:
        refuser(
            f"numéro d'écriture « {brut_numero} » : un entier strictement positif "
            "est attendu. C'est lui qui dit où finit une écriture et où commence "
            "la suivante."
        )
        return
    numero = int(brut_numero)

    try:
        date_operation = datetime.strptime(
            champs.get("date_operation", ""), profil.format_date
        ).date()
    except ValueError:
        refuser(
            f"date « {champs.get('date_operation', '')} » illisible au format "
            f"« {profil.format_date} » déclaré par le profil. ⚠️ Une date au "
            "format américain se lit sans erreur jusqu'au treizième jour du mois."
        )
        return

    compte = champs.get("compte", "")
    if not compte:
        refuser("aucun compte : la ligne ne mouvemente rien.")
        return
    if comptes_connus is not None and compte not in comptes_connus:
        refuser(
            f"le compte « {compte} » est absent du plan de ce dossier. L'accepter "
            "produirait une balance juste au total et fausse par compte, ce qui "
            "ne se voit qu'aux états financiers."
        )
        return

    montant, sens, refus = _mouvement(champs, profil)
    if refus is not None:
        refuser(refus)
        return

    cle = (journal, numero)
    assemblage = en_cours.get(cle)
    # ⚠️ **Distinguer ce que le fichier porte de ce qu'on lui substitue.**
    #
    # `champs` ne contient que les colonnes que le profil déclare : `.get` rend
    # `None` quand le profil n'a pas de libellé d'écriture, ce qui est le cas de
    # Sage Ligne 100, qui n'en porte qu'au niveau de la ligne.
    #
    # Le repli sur le libellé de ligne est légitime pour **nommer** l'écriture.
    # Il ne l'est pas pour la **contrôler** : comparer un repli à un autre repli
    # fait conclure que deux écritures différentes partagent un numéro, alors que
    # le fichier ne dit rien du tout. C'est ce qu'a révélé le premier aller-retour.
    libelle_declare = champs.get("libelle_ecriture")
    libelle = libelle_declare or champs.get("libelle_ligne") or ""
    piece = champs.get("piece_justificative") or None
    reference = champs.get("reference_externe") or None

    if assemblage is None:
        assemblage = _Assemblage(
            cle, exercice, date_operation, libelle, piece, reference
        )
        en_cours[cle] = assemblage
    else:
        # ⚠️ **Deux écritures sous un même numéro.** Il faudrait en choisir une, et
        # ce choix n'appartient pas au code : le fichier est ambigu, l'exploitant
        # tranche. Le cas arrive vraiment, quand deux exercices sont exportés dans
        # un même fichier sans que la colonne exercice soit reprise.
        if assemblage.date != date_operation:
            refuser(
                f"l'écriture {journal}/{numero} porte déjà la date "
                f"{assemblage.date:%d/%m/%Y} ligne {assemblage.rangs[0]}, et celle-ci "
                f"dit {date_operation:%d/%m/%Y}. Deux écritures différentes sous un "
                "même numéro : le fichier mélange probablement deux exercices."
            )
            return
        if (
            libelle_declare
            and assemblage.libelle
            and libelle_declare != assemblage.libelle
        ):
            refuser(
                f"l'écriture {journal}/{numero} porte déjà le libellé "
                f"« {assemblage.libelle} », et celle-ci dit « {libelle_declare} »."
            )
            return

    # ⚠️ **TOUTE RÈGLE DU PIVOT DOIT RESSORTIR EN ANOMALIE NOMMÉE.**
    #
    # C'est ici, et uniquement ici, que des données étrangères rencontrent le
    # domaine. Le pivot porte des règles que ce module ne rejoue pas : un compte
    # ne commence pas par zéro, un montant a au plus deux décimales, un libellé
    # n'est pas vide. Les rejouer les ferait diverger au premier correctif.
    #
    # Mais laisser remonter l'erreur de validation arrêterait la lecture au
    # premier défaut, sans numéro de ligne, dans un langage que l'exploitant ne
    # lit pas. Elle est donc traduite, et le rapport reste entier.
    try:
        mouvement = LigneEcriture(
            compte=compte,
            libelle=champs.get("libelle_ligne") or libelle,
            sens=sens,
            montant=montant,
            tiers=champs.get("tiers") or None,
            lettrage=champs.get("lettrage") or None,
        )
    except ValidationError as refus_du_pivot:
        refuser(
            "la ligne ne respecte pas les règles comptables du système : "
            + _traduire(refus_du_pivot)
        )
        return

    assemblage.lignes.append(mouvement)
    assemblage.rangs.append(rang)


def _mouvement(
    champs: dict[str, str], profil: ProfilDEchange
) -> tuple[Decimal, Sens, str | None]:
    """Le montant et le sens, quelle que soit la forme déclarée par le profil.

    ⚠️ **Les deux formes ne se devinent pas l'une de l'autre**, et c'est pour cela
    que le profil les déclare. Un fichier à colonnes séparées porte le montant
    d'un côté et du vide de l'autre ; un fichier à colonne unique porte un
    marqueur. Lire l'un comme l'autre donne toutes les lignes au débit.
    """
    if profil.forme_du_sens is FormeDuSens.COLONNES_SEPAREES:
        brut_debit = champs.get("debit", "")
        brut_credit = champs.get("credit", "")
        if bool(brut_debit) == bool(brut_credit):
            return (
                Decimal(0),
                Sens.DEBIT,
                (
                    "débit et crédit tous deux renseignés"
                    if brut_debit
                    else "ni débit ni crédit"
                )
                + " : une ligne comptable porte un mouvement et un seul.",
            )
        sens = Sens.DEBIT if brut_debit else Sens.CREDIT
        brut = brut_debit or brut_credit
    else:
        brut = champs.get("montant", "")
        marqueur = champs.get("sens", "")
        if marqueur == profil.marqueur_debit:
            sens = Sens.DEBIT
        elif marqueur == profil.marqueur_credit:
            sens = Sens.CREDIT
        else:
            return (
                Decimal(0),
                Sens.DEBIT,
                f"sens « {marqueur} » inconnu : le profil attend "
                f"« {profil.marqueur_debit} » ou « {profil.marqueur_credit} ».",
            )

    montant, refus = _lire_montant(brut, profil)
    return montant, sens, refus


def _lire_montant(brut: str, profil: ProfilDEchange) -> tuple[Decimal, str | None]:
    """L'inverse de `_montant`, plus tolérant que lui de deux façons précises.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **NOUS N'ÉCRIVONS JAMAIS DE SÉPARATEUR DE MILLIERS, MAIS NOUS EN RECEVONS.**

    Le fichier ne vient pas toujours d'un logiciel : il passe très souvent par un
    tableur, et un tableur rend `1 234 567,89`. Les refuser rendrait l'outil
    inutilisable pour la moitié des reprises réelles, alors que le retrait des
    espaces est sans ambiguïté : aucune notation comptable n'emploie l'espace
    autrement.

    L'espace insécable est retirée aussi. Elle est **invisible**, et c'est
    exactement ce qu'un tableur francophone insère.

    ⚠️ En revanche, **on ne devine pas la décimale.** Un fichier déclaré à virgule
    qui contient `1,234.56` est refusé, pas réinterprété : les deux lectures
    donnent un montant plausible, et se tromper met un facteur mille dans une
    balance sans que rien ne le signale.
    ─────────────────────────────────────────────────────────────────────────────
    """
    nettoye = brut.replace(" ", "").replace(" ", "").replace(" ", "")
    nettoye = nettoye.replace(profil.decimale, ".")
    try:
        montant = Decimal(nettoye)
    except (ArithmeticError, ValueError):
        return Decimal(0), (
            f"montant « {brut} » illisible. Le profil déclare « {profil.decimale} » "
            "comme séparateur décimal ; un fichier à l'anglo-saxonne est refusé "
            "plutôt que réinterprété."
        )
    # ⚠️ **Le signe et les décimales ne sont PAS contrôlés ici**, et c'est
    # délibéré : le pivot les refuse déjà, dans ses propres mots — « le montant
    # d'une ligne est strictement positif : c'est le sens qui porte la direction,
    # jamais le signe », et « le franc CFA ne comporte pas de décimale ».
    #
    # Ce module rejouait cette règle. Une mutation l'a révélé : la supprimer ne
    # cassait aucun cas, puisque le pivot la tenait derrière. Deux contrôles qui
    # disent la même chose divergent au premier correctif, et c'est alors le plus
    # laxiste des deux qui fait loi sans que personne ne s'en aperçoive.
    #
    # Le refus remonte par `_traduire`, avec le rang du fichier.
    return montant, None


def _clore(
    en_cours: dict[tuple[str, int], _Assemblage], anomalies: list[Anomalie]
) -> tuple[list[EcritureComptable], list[int]]:
    """Transforme les assemblages en écritures, ou signale ce qui les en empêche.

    ⚠️ **L'équilibre est vérifié ici, pas par l'entité.** Le pivot refuse une
    écriture déséquilibrée à la construction, ce qui est juste, mais rendrait une
    erreur de validation illisible au milieu d'un rapport d'import. Le contrôle
    est donc fait avant, pour nommer le journal, le numéro, l'écart **et les
    lignes du fichier** en cause.
    """
    ecritures: list[EcritureComptable] = []
    rangs: list[int] = []
    for assemblage in en_cours.values():
        journal, numero = assemblage.cle
        # ⚠️ Un assemblage vide a été ouvert par une ligne qui a ensuite été
        # refusée. Ses anomalies sont déjà au rapport ; ajouter ici « ne
        # s'équilibre pas : débit 0, crédit 0 » ferait chercher un déséquilibre
        # là où il n'y a qu'une ligne déjà signalée.
        if not assemblage.lignes:
            continue
        debit = sum((x.montant for x in assemblage.lignes if x.au_debit), Decimal(0))
        credit = sum((x.montant for x in assemblage.lignes if not x.au_debit), Decimal(0))
        if debit != credit:
            anomalies.append(
                Anomalie(
                    ligne=assemblage.rangs[0],
                    motif=(
                        f"l'écriture {journal}/{numero} ne s'équilibre pas : débit "
                        f"{debit}, crédit {credit}, écart {abs(debit - credit)}. "
                        f"Lignes {assemblage.rangs[0]} à {assemblage.rangs[-1]} du "
                        "fichier. Une ligne manque, ou le fichier est tronqué."
                    ),
                )
            )
            continue
        try:
            faite = EcritureComptable(
                journal=journal,
                exercice=assemblage.exercice,
                numero=numero,
                date_operation=assemblage.date,
                libelle=assemblage.libelle or f"Reprise {journal}/{numero}",
                piece_justificative=assemblage.piece,
                reference_externe=assemblage.reference,
                lignes=assemblage.lignes,
                etat=EtatEcriture.BROUILLON,
            )
        except ValidationError as refus_du_pivot:
            # Même discipline qu'à la ligne : une règle du pivot que ce module ne
            # rejoue pas doit se lire en français, avec le rang du fichier.
            anomalies.append(
                Anomalie(
                    ligne=assemblage.rangs[0],
                    motif=(
                        f"l'écriture {journal}/{numero} ne respecte pas les règles "
                        f"comptables du système : {_traduire(refus_du_pivot)}"
                    ),
                )
            )
            continue
        ecritures.append(faite)
        rangs.append(assemblage.rangs[0])
    return ecritures, rangs


def _traduire(refus: ValidationError) -> str:
    """Une erreur de validation du pivot, dite en une phrase lisible.

    ⚠️ Le format natif de pydantic nomme des types et des chemins de champs, et
    renvoie vers une adresse web. Un exploitant comptable n'en tirera rien, et le
    conclura que l'outil est cassé plutôt que son fichier.
    """
    morceaux = []
    for erreur in refus.errors():
        champ = ".".join(str(x) for x in erreur["loc"]) or "la ligne"
        valeur = erreur.get("input")
        morceaux.append(f"{champ} = « {valeur} » ({erreur['msg']})")
    return " ; ".join(morceaux[:3])
