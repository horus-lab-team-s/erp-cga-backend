"""Clore un exercice : l'acte, et ce qu'il produit.

─────────────────────────────────────────────────────────────────────────────────
TROIS EFFETS, ET ILS SONT INDISSOCIABLES

    1. l'exercice est marqué clos, donc plus rien ne s'y saisit
    2. une écriture d'à-nouveau ouvre l'exercice suivant sur les soldes de
       situation, résultat compris
    3. la trace de l'acte est portée par le journal d'audit, avec son motif

⚠️ **LES DEUX PREMIERS NE PEUVENT PAS SE FAIRE L'UN SANS L'AUTRE**, et l'ordre a
son importance. Marquer clos sans reporter laisse une entreprise qui recommence
avec une caisse vide. Reporter sans marquer clos autorise à continuer de saisir
dans l'exercice fermé, et chaque saisie rend l'à-nouveau faux sans que rien ne le
signale — c'est le pire des trois états possibles, parce qu'il paraît normal.

CE MODULE PASSE PAR LES MÊMES PORTES QUE LA SAISIE

Écrire l'à-nouveau directement dans le dépôt serait plus court et contournerait
les quatre contrôles d'environnement : journal existant, comptes au plan, exercice
ouvert, date dans les bornes. Or l'exercice suivant peut très bien ne pas exister,
ou être lui-même déjà clos, et c'est précisément ce qu'il faut apprendre **avant**
de fermer le précédent.

C'est la règle posée au pas 42 pour la reprise, et elle vaut deux fois ici :
*une porte dérobée n'est pas une porte dérobée parce qu'on la cache, c'est une
porte dérobée parce qu'elle n'a pas la même serrure.*

⚠️ L'À-NOUVEAU EST VALIDÉ, ET PAR CELUI QUI CLÔT

Toute autre écriture naît en brouillon pour que le comptable la relise. Celle-ci
n'est pas saisie : elle est **calculée** à partir d'écritures déjà validées, et il
n'y a rien à y relire que le calcul. La laisser en brouillon aurait une
conséquence précise et absurde : l'exercice suivant s'ouvrirait sur une balance
vide, puisque la balance ne compte que le validé.

Elle est donc validée, et validée au nom de celui qui clôt. *Le Centre engage sa
responsabilité sur ce qu'il présente* : la personne qui ferme un exercice engage
aussi le report qu'elle en tire.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from app.contextes.cloture.domaine.a_nouveau import (
    JOURNAL_DES_A_NOUVEAUX,
    Empechement,
    MotifEmpechement,
    lignes_d_a_nouveau,
    obstacles_a_la_cloture,
)
from app.contextes.comptabilite.api import (
    BrouillonEcriture,
    Compte,
    DepotEcritures,
    Journal,
    SaisieRefusee,
    balance,
    enregistrer_une_ecriture,
    resultat,
    valider_une_ecriture,
)
from app.contextes.portefeuille.api import DepotEntreprises, Entreprise, Exercice
from app.partage.copie import transiter

__all__ = ["ClotureRefusee", "RapportDeCloture", "clore_un_exercice"]


class ClotureRefusee(RuntimeError):
    """La clôture ne peut pas se faire, et le rapport dit pourquoi."""


class RapportDeCloture(BaseModel):
    """Ce que la clôture a fait, ou ce qu'elle ferait.

    ⚠️ **La même forme dans les deux modes**, comme le rapport de reprise. Un
    exploitant qui lit un contrôle doit y voir exactement ce qu'il obtiendra en
    appliquant, sans avoir à comparer deux présentations.
    """

    model_config = ConfigDict(frozen=True)

    exercice: str
    applique: bool
    #: Faux tant qu'un obstacle subsiste. Un écran peut donc griser le bouton sans
    #: interpréter la liste.
    possible: bool
    obstacles: tuple[Empechement, ...] = ()

    resultat_de_l_exercice: str = "0"
    exercice_suivant: str | None = None
    #: La clé de l'écriture d'à-nouveau. Vide en mode contrôle : le numéro se tire
    #: au moment d'écrire, et en promettre un que la clôture réelle n'attribuerait
    #: pas serait pire que de n'en promettre aucun.
    cle_a_nouveau: str | None = None
    lignes_reportees: int = Field(default=0, ge=0)
    #: Vrai quand l'exercice suivant n'existait pas et que la clôture l'ouvre.
    #: Rendu **en mode contrôle aussi** : un réviseur doit savoir qu'il va créer
    #: un exercice, pas seulement en fermer un.
    suivant_ouvert_par_la_cloture: bool = False


def clore_un_exercice(
    niu: str,
    libelle_exercice: str,
    *,
    motif: str,
    entreprises: DepotEntreprises,
    depot: DepotEcritures,
    journaux: list[Journal],
    plan: list[Compte],
    par: str,
    a_l_instant: datetime,
    appliquer: bool,
    cloture_du_suivant: date | None = None,
) -> RapportDeCloture:
    """Ferme l'exercice et rouvre le suivant, ou dit ce qui l'en empêche.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LE MODE CONTRÔLE EST LE DÉFAUT, ET ICI L'ENJEU EST PLUS GRAND QU'AILLEURS.**

    Une reprise mal faite laisse des brouillons, qui ne se suppriment pas (Q23). Une clôture mal
    faite **ferme un exercice**, et le produit ne sait pas le rouvrir : c'est le
    principe d'intangibilité, et il est délibéré. Se tromper d'exercice ou de
    dossier doit donc se voir avant, pas après.

    ⚠️ **LA CLÔTURE OUVRE L'EXERCICE SUIVANT, ET C'EST DÉLIBÉRÉ.**

    L'entité garantit déjà que les exercices se suivent **sans trou ni
    chevauchement**. La date d'ouverture du suivant n'est donc pas un choix : c'est
    le lendemain de la clôture de celui-ci, et rien d'autre n'est admissible.

    Exiger alors que l'exploitant l'ouvre à part créerait le pire état possible :
    un exercice fermé sans successeur, dans lequel plus rien ne se saisit et dont
    les soldes ne sont allés nulle part. Le dossier devient inutilisable, et le
    produit ne sait pas le rouvrir.

    Seule la **fin** du suivant reste un choix, et il est rare : un exercice de
    transition de six mois, un passage à un exercice décalé. `cloture_du_suivant`
    l'exprime ; en son absence, douze mois moins un jour, qui est le cas de la
    quasi-totalité des dossiers.

    ⚠️ Un exercice suivant qui existe **et qui est clos** reste un obstacle : il
    n'y a nulle part où reporter, et ce n'est pas à ce module de rouvrir quoi que
    ce soit.
    ─────────────────────────────────────────────────────────────────────────────
    """
    entreprise = entreprises.lire(niu)
    exercice = _trouver(entreprise, libelle_exercice)
    if exercice is None:
        raise ClotureRefusee(
            f"l'exercice « {libelle_exercice} » n'existe pas au dossier {niu}. "
            f"Exercices connus : {', '.join(e.libelle for e in entreprise.exercices) or 'aucun'}."
        )

    ecritures = depot.lister(libelle_exercice)
    soldes = balance(ecritures)
    obstacles = list(
        obstacles_a_la_cloture(
            ecritures, soldes, plan=plan, deja_clos=exercice.clos
        )
    )

    # ⚠️ Les obstacles de **contexte** viennent après ceux de comptabilité, et
    # jamais à leur place : un exercice suivant manquant n'excuse pas une balance
    # fausse, et le comptable doit voir les deux d'un coup.
    # ⚠️ **UN EXERCICE NE SE CLÔT QU'UNE FOIS TERMINÉ.** (pas 55)
    #
    # Ce contrôle manquait, et rien d'autre ne le remplaçait : un réviseur pouvait
    # clore 2026 le 14 septembre 2026. L'exercice fermé, les opérations de fin
    # septembre à décembre n'avaient plus d'exercice où s'inscrire, puisque le
    # suivant s'ouvre le 1er janvier et refuse leurs dates, et le produit ne sait
    # pas rouvrir un exercice clos. Le défaut s'est vu en préparant le bouton de
    # clôture : l'écran s'ouvre par défaut sur l'exercice en cours, et le premier
    # clic l'aurait fermé.
    #
    # Le jour même de la clôture est refusé aussi : ses opérations ne sont pas
    # encore toutes passées. Le lendemain est le premier jour possible.
    #
    # L'instant est reçu, jamais lu ici : la route le lit sur l'horloge, et les cas
    # le choisissent.
    if a_l_instant.date() <= exercice.cloture:
        obstacles.append(
            Empechement(
                motif=MotifEmpechement.EXERCICE_NON_TERMINE,
                explication=(
                    f"l'exercice {exercice.libelle} se termine le "
                    f"{exercice.cloture:%d/%m/%Y}. Le clore le "
                    f"{a_l_instant.date():%d/%m/%Y} fermerait des opérations qui ne "
                    "sont pas encore passées, et elles n'auraient plus d'exercice où "
                    "s'inscrire : un exercice clos ne se rouvre pas. Première date "
                    f"possible : le {exercice.cloture + timedelta(days=1):%d/%m/%Y}."
                ),
                en_cause=(exercice.libelle,),
            )
        )

    anterieurs = [
        e for e in entreprise.exercices
        if e.ouverture < exercice.ouverture and not e.clos
    ]
    if anterieurs:
        obstacles.append(
            Empechement(
                motif=MotifEmpechement.EXERCICE_ANTERIEUR_OUVERT,
                explication=(
                    "un exercice antérieur est encore ouvert. Le clore d'abord : "
                    "reporter les soldes de celui-ci par-dessus un exercice qui "
                    "n'a pas encore livré les siens produirait un à-nouveau posé "
                    "sur du vide."
                ),
                en_cause=tuple(e.libelle for e in anterieurs),
            )
        )

    suivant = _suivant(entreprise, exercice)
    a_ouvrir = suivant is None
    if suivant is None:
        suivant = _exercice_a_ouvrir(exercice, cloture_du_suivant)
    elif suivant.clos:
        obstacles.append(
            Empechement(
                motif=MotifEmpechement.EXERCICE_SUIVANT_ABSENT,
                explication=(
                    f"l'exercice suivant « {suivant.libelle} » est déjà clos : il "
                    "n'y a nulle part où reporter les soldes. Rouvrir un exercice "
                    "clos n'appartient pas à ce geste."
                ),
                en_cause=(suivant.libelle,),
            )
        )
    elif cloture_du_suivant is not None and cloture_du_suivant != suivant.cloture:
        # ⚠️ **On n'ignore pas en silence une date que l'exploitant a donnée.**
        # L'exercice suivant existe déjà avec ses bornes ; accepter la demande
        # sans la suivre laisserait croire qu'elle a été prise en compte.
        obstacles.append(
            Empechement(
                motif=MotifEmpechement.EXERCICE_SUIVANT_ABSENT,
                explication=(
                    f"l'exercice « {suivant.libelle} » existe déjà et se clôt le "
                    f"{suivant.cloture:%d/%m/%Y}, pas le "
                    f"{cloture_du_suivant:%d/%m/%Y} demandé. Modifier ses bornes "
                    "n'appartient pas à la clôture."
                ),
                en_cause=(suivant.libelle,),
            )
        )

    gain = resultat(soldes)
    lignes = lignes_d_a_nouveau(soldes, plan=plan)

    if obstacles or not appliquer:
        return RapportDeCloture(
            exercice=libelle_exercice,
            applique=False,
            possible=not obstacles,
            obstacles=tuple(obstacles),
            resultat_de_l_exercice=str(gain),
            exercice_suivant=suivant.libelle,
            lignes_reportees=len(lignes),
            suivant_ouvert_par_la_cloture=a_ouvrir,
        )

    if a_ouvrir:
        # ⚠️ **L'exercice s'ajoute en mémoire, il ne se persiste pas ici.**
        #
        # La première version l'enregistrait tout de suite, en croyant que
        # l'à-nouveau le relirait. Il ne le relit pas : l'objet lui est passé
        # directement. Une mutation l'a montré, en supprimant cet enregistrement
        # sans casser aucun cas — la seconde écriture, en fin de geste, portait
        # déjà les deux changements.
        #
        # Et l'ordre retenu est le meilleur des deux : si l'à-nouveau échoue,
        # **rien** n'a été écrit, au lieu d'un exercice neuf sans report.
        entreprise = transiter(
            entreprise, exercices=[*entreprise.exercices, suivant]
        )

    ecrite = _poser_l_a_nouveau(
        lignes,
        origine=libelle_exercice,
        suivant=suivant,
        journaux=journaux,
        plan=plan,
        depot=depot,
        par=par,
        a_l_instant=a_l_instant,
    )

    # ⚠️ **Le marquage vient en dernier**, et l'ordre est ce qui rend l'échec
    # sans conséquence : si l'à-nouveau ne passe pas, l'exercice reste ouvert et
    # le comptable recommence. L'inverse laisserait un exercice fermé sans report,
    # état dont le produit ne sait pas sortir.
    # ⚠️ **Une seule écriture, et elle vient en dernier.** Elle porte l'exercice
    # fermé et, le cas échéant, l'exercice neuf. Si l'à-nouveau avait échoué, on
    # ne serait pas ici : l'exercice resterait ouvert et le comptable
    # recommencerait. L'inverse laisserait un exercice fermé sans report, état
    # dont le produit ne sait pas sortir.
    entreprises.enregistrer(_marquer_clos(entreprise, exercice))
    _ = motif  # porté par le journal d'audit, via la route qui l'exige.

    return RapportDeCloture(
        exercice=libelle_exercice,
        applique=True,
        possible=True,
        resultat_de_l_exercice=str(gain),
        exercice_suivant=suivant.libelle,
        cle_a_nouveau=ecrite,
        lignes_reportees=len(lignes),
        suivant_ouvert_par_la_cloture=a_ouvrir,
    )


def _poser_l_a_nouveau(
    lignes,
    *,
    origine: str,
    suivant: Exercice,
    journaux: list[Journal],
    plan: list[Compte],
    depot: DepotEcritures,
    par: str,
    a_l_instant: datetime,
) -> str:
    """Enregistre puis valide l'écriture de report, par les portes ordinaires."""
    brouillon = BrouillonEcriture(
        journal=JOURNAL_DES_A_NOUVEAUX,
        exercice=suivant.libelle,
        date_operation=suivant.ouverture,
        libelle=f"À-nouveaux de l'exercice {origine}",
        # ⚠️ **UNE ÉCRITURE VALIDÉE PORTE SA PIÈCE, CELLE-CI COMPRISE.**
        #
        # Sa pièce est la **balance de clôture** de l'exercice d'origine. Elle n'est
        # pas un fichier déposé, et elle n'a pas besoin de l'être : l'exercice est
        # clos, donc figé, donc sa balance se reproduit à l'identique le jour où un
        # vérificateur la demande. L'intangibilité est ce qui rend la pièce
        # reproductible.
        #
        # La nommer plutôt que de la laisser vide n'est pas une formalité : le
        # domaine refuse une écriture validée sans pièce, « sans elle, la
        # traçabilité est rompue dès le premier maillon », et ce refus a du sens
        # jusqu'ici.
        piece_justificative=f"BALANCE-{origine}",
        # ⚠️ La référence porte l'exercice d'origine. Sans elle, deux clôtures
        # successives donneraient deux écritures indiscernables dans le journal
        # des à-nouveaux, et rien ne dirait laquelle reprend quoi.
        reference_externe=f"AN-{origine}",
        lignes=list(lignes),
    )
    try:
        ecriture = enregistrer_une_ecriture(
            brouillon,
            journaux=journaux,
            plan=plan,
            exercice=suivant,
            # ⚠️ Pas 107 : aucun verrou mensuel ne s'oppose à l'à-nouveau, et c'est délibéré.
            # Il ne saisit pas une opération du mois : il reporte la balance de clôture, par
            # obligation, dans le journal des à-nouveaux. Le refuser parce que janvier a été
            # transmis au réviseur rendrait impossible la clôture d'un exercice, qui se fait
            # le plus souvent des mois après. Le réviseur retrouve l'écriture, datée du jour
            # d'ouverture, avec sa pièce (la balance de l'exercice clos).
            periodes_verrouillees=(),
            depot=depot,
            par=par,
        )
    except SaisieRefusee as refus:
        raise ClotureRefusee(
            f"l'écriture d'à-nouveau est refusée par les contrôles de saisie : "
            f"{refus}. L'exercice {origine} reste ouvert."
        ) from refus

    valider_une_ecriture(
        ecriture.cle,
        exercice=suivant,
        periodes_verrouillees=(),
        depot=depot,
        par=par,
        le=a_l_instant,
    )
    return ecriture.cle


def _exercice_a_ouvrir(exercice: Exercice, cloture: date | None) -> Exercice:
    """L'exercice qui suit celui-ci, dont l'ouverture n'est pas un choix.

    ⚠️ **Le libellé vient de l'année d'ouverture**, et il reste faux pour un
    exercice décalé : « 2027 » pour un exercice qui va du 1er juillet 2027 au
    30 juin 2028. C'est la convention déjà employée par le jeu de démonstration et
    par les libellés du portefeuille ; la changer ici seule ferait deux
    conventions au lieu d'une. La question appartient au cabinet, pas au code.
    """
    ouverture = exercice.cloture + timedelta(days=1)
    fin = cloture or (ouverture.replace(year=ouverture.year + 1) - timedelta(days=1))
    return Exercice(libelle=str(ouverture.year), ouverture=ouverture, cloture=fin)


def _trouver(entreprise: Entreprise, libelle: str) -> Exercice | None:
    return next((e for e in entreprise.exercices if e.libelle == libelle), None)


def _suivant(entreprise: Entreprise, exercice: Exercice) -> Exercice | None:
    """Le premier exercice qui commence après celui-ci.

    ⚠️ Cherché par **date d'ouverture**, jamais par le libellé. `Exercice.libelle`
    est une chaîne libre : un dossier repris d'un ancien logiciel arrive avec
    « EX01 » ou « 2026-transition », et un exercice décalé s'appelle souvent
    « 2026-2027 ».

    Aucun jeu de libellés rencontré à ce jour ne met les deux méthodes en
    désaccord, et c'est justement pourquoi l'écart ne se verrait pas : le libellé
    est une **étiquette**, la date est ce qui définit la succession. Le contrat est
    fixé par un cas aux libellés délibérément artificiels.
    """
    posterieurs = sorted(
        (e for e in entreprise.exercices if e.ouverture > exercice.ouverture),
        key=lambda e: e.ouverture,
    )
    return posterieurs[0] if posterieurs else None


def _marquer_clos(entreprise: Entreprise, exercice: Exercice) -> Entreprise:
    """Rend une entreprise dont cet exercice porte `clos`.

    ⚠️ Une copie, jamais une mutation : l'entité est gelée, et c'est ce qui
    garantit qu'aucun autre chemin ne l'a modifiée entre la lecture et l'écriture.
    """
    return transiter(
        entreprise,
        exercices=[
            transiter(e, clos=True) if e.libelle == exercice.libelle else e
            for e in entreprise.exercices
        ],
    )

