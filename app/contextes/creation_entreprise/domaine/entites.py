"""Entités du contexte I · Création d'entreprise.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE CONTEXTE MODÉLISE

Le tunnel qui mène un porteur de projet, qui n'a rien, jusqu'à une entreprise
immatriculée puis adhérente du centre :

    Qualification → Constitution → Dépôt CFCE → Suivi → Livraison → Conversion

C'est le **produit d'appel** du cabinet, et sa nature commerciale commande la
modélisation : un dossier de création n'est pas une entreprise, c'est une
*intention* d'entreprise. Elle n'a ni NIU, ni RCCM, ni exercice, ni régime — rien
de ce dont B · Portefeuille a besoin pour exister. La confondre avec une
entreprise obligerait le portefeuille à porter des dossiers qui ne sont pas des
contribuables, et chaque calcul d'obligation devrait alors se demander « est-ce
une vraie entreprise ? ». C'est cette question qu'on évite en séparant.

LE POINT DE BASCULE

Tout l'intérêt du contexte tient en une transition : le jour où le RCCM **et** le
NIU sont obtenus, le dossier cesse d'être une intention et devient une entreprise.
C'est là — et là seulement — que B · Portefeuille reçoit un dossier, avec son
régime et son rattachement ; F · Obligations en déduit alors le calendrier sans
que personne n'ait à le lui demander.

Avant cette bascule, le dossier vit ici. Après, il n'y vit plus : il est converti,
et le contexte n'en garde que la trace.

CE QU'IL NE MODÉLISE PAS

Ni le devis ni l'encaissement — c'est M · Souscription, qui vend la prestation de
création comme il vend l'adhésion. Ni la conformité des pièces au sens fiscal :
la checklist de constitution emploie **le même moteur** que D · Conformité avec un
autre jeu de règles, elle ne le réimplémente pas.

Aucun montant, aucun délai en dur. Le capital minimum d'une SA et le délai annoncé
du CFCE vivent au référentiel et se lisent à une date : une réforme du droit OHADA
ne doit pas demander de recompiler.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contextes.portefeuille.contrats import FormeJuridique

__all__ = [
    "DossierCreation",
    "EtapeCreation",
    "Fondateur",
    "Immatriculation",
    "Jalon",
    "PieceConstitution",
    "TransitionInterdite",
    "etapes_ouvertes_depuis",
]


class TransitionInterdite(ValueError):
    """Le dossier ne peut pas passer de son étape courante à celle demandée.

    Volontairement une erreur et non un refus silencieux : un dossier déposé au
    CFCE sans que sa constitution soit achevée revient du guichet, et il revient
    trois jours plus tard, quand le porteur a déjà annoncé la date à son banquier.
    Le coût d'un refus au moment du geste est nul ; celui d'un rejet au guichet ne
    l'est pas.
    """


class EtapeCreation(StrEnum):
    """Les étapes du tunnel, dans l'ordre où elles se franchissent.

    ⚠️ L'ordre de déclaration **est** l'ordre métier : `etapes_ouvertes_depuis`
    s'en sert pour dire ce qui suit. Réordonner ces membres change le
    comportement du tunnel — ce n'est pas une énumération décorative.
    """

    #: Le besoin est qualifié : forme juridique retenue, activité, capital envisagé.
    QUALIFICATION = "QUALIFICATION"
    #: Les pièces se rassemblent : statuts, pièces d'identité, justificatif de siège.
    CONSTITUTION = "CONSTITUTION"
    #: Le dossier est entre les mains du guichet unique.
    DEPOT_CFCE = "DEPOT_CFCE"
    #: Les identifiants arrivent un à un — RCCM, puis NIU, puis patente.
    SUIVI_IMMATRICULATION = "SUIVI_IMMATRICULATION"
    #: Les originaux sont remis au fondateur.
    LIVRAISON = "LIVRAISON"
    #: L'entreprise existe au portefeuille : le dossier de création est clos.
    CONVERTI = "CONVERTI"
    #: Fin de parcours sans immatriculation. État terminal, comme CONVERTI.
    ABANDONNE = "ABANDONNE"


#: Les états dont on ne sort pas. Un dossier abandonné qui repart n'existe pas :
#: le porteur revient avec un nouveau projet, donc un nouveau dossier — et le
#: pipeline garde ainsi la trace de l'échec au lieu de la réécrire.
ETATS_TERMINAUX: frozenset[EtapeCreation] = frozenset(
    {EtapeCreation.CONVERTI, EtapeCreation.ABANDONNE}
)

_ORDRE: list[EtapeCreation] = [
    EtapeCreation.QUALIFICATION,
    EtapeCreation.CONSTITUTION,
    EtapeCreation.DEPOT_CFCE,
    EtapeCreation.SUIVI_IMMATRICULATION,
    EtapeCreation.LIVRAISON,
    EtapeCreation.CONVERTI,
]


def etapes_ouvertes_depuis(etape: EtapeCreation) -> frozenset[EtapeCreation]:
    """Les étapes atteignables depuis celle-ci.

    ─────────────────────────────────────────────────────────────────────────
    ON N'AVANCE QUE D'UN CRAN, ET ON PEUT TOUJOURS ABANDONNER

    Un tunnel qui laisserait sauter des étapes ne serait plus un tunnel mais un
    champ libre, et le pipeline cesserait de dire où en sont les dossiers. Le
    saut le plus tentant — « le RCCM est déjà là, passons directement à la
    livraison » — est aussi le plus coûteux : il fait disparaître la trace du
    dépôt, donc du délai tenu ou non par le guichet, donc de ce que le cabinet
    peut opposer au CFCE.

    Le retour en arrière n'est pas ouvert non plus. Un dossier déposé qui
    reviendrait en constitution laisserait croire qu'il n'a jamais été déposé.
    Le geste réel, quand le guichet rejette, est de **redéposer** : la seule
    répétition admise est donc de rester sur place, ce que `avancer` traite à
    part.

    L'abandon, lui, est ouvert depuis n'importe où, parce qu'un porteur de projet
    renonce quand il veut, et qu'un tunnel qui refuse l'abandon accumule des
    dossiers fantômes que personne n'ose fermer.
    ─────────────────────────────────────────────────────────────────────────
    """
    if etape in ETATS_TERMINAUX:
        return frozenset()
    suivante = _ORDRE[_ORDRE.index(etape) + 1]
    return frozenset({suivante, EtapeCreation.ABANDONNE})


class Fondateur(BaseModel):
    """La personne qui porte le projet.

    Pas un `Dirigeant` du portefeuille : tant que la société n'existe pas, il n'y
    a personne à diriger. Le fondateur devient dirigeant à la conversion, et c'est
    une transformation, pas un simple changement de nom.
    """

    model_config = ConfigDict(frozen=True)

    nom: str
    prenom: str
    courriel: str
    telephone: str
    #: Nécessaire à la constitution — le CFCE exige une pièce d'identité valide.
    piece_identite: str | None = None


class Immatriculation(BaseModel):
    """Les identifiants délivrés, chacun avec sa date d'obtention.

    ─────────────────────────────────────────────────────────────────────────
    QUATRE IDENTIFIANTS, DEUX QUI COMMANDENT

    Le RCCM fait exister la société en droit commercial ; le NIU la fait exister
    devant l'administration fiscale. Ce sont les deux seuls dont dépend la
    conversion : sans RCCM il n'y a pas de personne morale, sans NIU il n'y a pas
    de contribuable, et un contexte fiscal ne sait rien faire d'une entreprise
    qui n'est ni l'un ni l'autre.

    La patente et le numéro CNPS suivent souvent de plusieurs semaines. Les
    exiger pour convertir retarderait l'entrée au portefeuille — donc le premier
    calendrier d'obligations — alors que les échéances, elles, ont déjà commencé
    à courir. Ils sont donc suivis, et facultatifs.
    ─────────────────────────────────────────────────────────────────────────
    """

    model_config = ConfigDict(frozen=True)

    rccm: str | None = None
    rccm_obtenu_le: date | None = None
    niu: str | None = None
    niu_obtenu_le: date | None = None
    patente: str | None = None
    patente_obtenue_le: date | None = None
    cnps: str | None = None
    cnps_obtenu_le: date | None = None

    @model_validator(mode="after")
    def _une_date_accompagne_chaque_identifiant(self) -> Immatriculation:
        """Un identifiant sans date d'obtention n'est pas exploitable.

        La date sert à mesurer le délai tenu par le guichet, et c'est le seul
        chiffre que le cabinet peut opposer au CFCE. Un RCCM saisi sans elle
        laisserait un dossier qui paraît complet et un délai qu'on ne peut plus
        reconstituer.
        """
        couples = (
            ("rccm", self.rccm, self.rccm_obtenu_le),
            ("niu", self.niu, self.niu_obtenu_le),
            ("patente", self.patente, self.patente_obtenue_le),
            ("cnps", self.cnps, self.cnps_obtenu_le),
        )
        for nom, valeur, obtenu_le in couples:
            if valeur is not None and obtenu_le is None:
                raise ValueError(
                    f"{nom} renseigné sans date d'obtention : le délai du guichet "
                    "ne serait plus reconstituable."
                )
            if valeur is None and obtenu_le is not None:
                raise ValueError(f"date d'obtention de {nom} sans {nom}.")
        return self

    @property
    def immatriculee(self) -> bool:
        """RCCM **et** NIU obtenus — la condition de la conversion."""
        return self.rccm is not None and self.niu is not None

    @property
    def complete(self) -> bool:
        """Les quatre identifiants sont là. Ne conditionne rien, informe le suivi."""
        return all((self.rccm, self.niu, self.patente, self.cnps))


class PieceConstitution(BaseModel):
    """Une pièce attendue au dossier de constitution.

    Le code est la clé stable ; le libellé s'affiche. Les séparer permet de
    corriger un libellé sans casser les dossiers en cours, ce qui arrive : « CNI
    du gérant » devient « Pièce d'identité du gérant » le jour où un passeport se
    présente.
    """

    model_config = ConfigDict(frozen=True)

    code: str
    libelle: str
    #: Fausse pour les pièces d'usage — utiles au greffe, jamais bloquantes.
    obligatoire: bool = True
    fournie_le: date | None = None
    #: Empreinte du fichier au magasin de C · Collecte, si la pièce y est déposée.
    empreinte: str | None = None

    @property
    def fournie(self) -> bool:
        return self.fournie_le is not None


class Jalon(BaseModel):
    """Le franchissement d'une étape, daté et attribué.

    ─────────────────────────────────────────────────────────────────────────
    POURQUOI CONSERVER LES JALONS PLUTÔT QUE LA SEULE ÉTAPE COURANTE

    L'étape courante répond à « où en est-on ? ». Elle ne répond ni à « depuis
    quand ? », ni à « le guichet a-t-il tenu son délai ? », ni à « qui a déposé ? ».
    Ces trois questions se posent toutes, et aucune ne se reconstitue depuis un
    état courant.

    C'est le même choix que les statuts datés du portefeuille, pour la même
    raison : un état écrasé est une histoire perdue, et les histoires perdues se
    redemandent toujours au pire moment.
    ─────────────────────────────────────────────────────────────────────────
    """

    model_config = ConfigDict(frozen=True)

    etape: EtapeCreation
    survenu_le: date
    #: L'identifiant du compte qui a posé le geste. Le journal d'audit de K en
    #: garde la trace détaillée ; on garde ici de quoi afficher le pipeline.
    par: str | None = None
    commentaire: str | None = None


class DossierCreation(BaseModel):
    """Un dossier de création, de la qualification à la conversion.

    Immuable comme le reste du domaine : chaque geste rend un nouveau dossier.
    C'est ce qui permet de comparer l'avant et l'après dans un test, et
    d'interdire qu'un cas d'usage modifie un dossier qu'un autre tient déjà.
    """

    model_config = ConfigDict(frozen=True)

    reference: str
    fondateur: Fondateur
    #: Nom souhaité, pas dénomination : tant que le greffe n'a pas vérifié sa
    #: disponibilité, c'est un vœu. Il devient dénomination à la conversion.
    denomination_souhaitee: str
    forme_juridique: FormeJuridique
    activite: str
    siege: str
    capital: Decimal | None = None
    ouvert_le: date
    etape: EtapeCreation = EtapeCreation.QUALIFICATION
    pieces: tuple[PieceConstitution, ...] = ()
    immatriculation: Immatriculation = Field(default_factory=Immatriculation)
    jalons: tuple[Jalon, ...] = ()
    #: Renseigné à la conversion : le NIU sous lequel l'entreprise entre au
    #: portefeuille. Sert de lien, et évite de reconvertir deux fois.
    converti_en: str | None = None
    motif_abandon: str | None = None

    @model_validator(mode="after")
    def _un_abandon_porte_son_motif(self) -> DossierCreation:
        """Un dossier perdu sans motif ne s'analyse pas.

        Le pipeline sert à savoir pourquoi les dossiers sortent, pas seulement
        combien. « Trop cher », « parti chez un concurrent » et « projet reporté »
        appellent trois réponses commerciales différentes ; sans motif, les trois
        se ressemblent.
        """
        if self.etape is EtapeCreation.ABANDONNE and not self.motif_abandon:
            raise ValueError("un dossier abandonné porte son motif.")
        return self

    @model_validator(mode="after")
    def _une_conversion_designe_l_entreprise(self) -> DossierCreation:
        if self.etape is EtapeCreation.CONVERTI and not self.converti_en:
            raise ValueError("un dossier converti désigne le NIU de l'entreprise créée.")
        return self

    # ── Lectures ────────────────────────────────────────────────────────────
    @property
    def pieces_manquantes(self) -> tuple[PieceConstitution, ...]:
        """Les pièces obligatoires encore absentes."""
        return tuple(p for p in self.pieces if p.obligatoire and not p.fournie)

    @property
    def constitution_achevee(self) -> bool:
        """Toutes les pièces obligatoires sont au dossier.

        Ne dit rien de leur validité : c'est le moteur de règles qui le dit, et
        il vit en couche application. Ici on compte, on ne juge pas.
        """
        return not self.pieces_manquantes

    @property
    def clos(self) -> bool:
        return self.etape in ETATS_TERMINAUX

    def depuis_le(self, etape: EtapeCreation) -> date | None:
        """La date à laquelle cette étape a été franchie, si elle l'a été."""
        for jalon in self.jalons:
            if jalon.etape is etape:
                return jalon.survenu_le
        return None

    @property
    def immobile_depuis(self) -> date:
        """La date du dernier mouvement — l'ouverture si rien n'a bougé.

        C'est l'indicateur qui fait remonter un dossier dans le pipeline. Un
        dossier de création qui dort est un client qui part.
        """
        return max((j.survenu_le for j in self.jalons), default=self.ouvert_le)
