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

    @property
    def appellation(self) -> str:
        """Ce qu'on écrit après « Bonjour ». Jamais vide.

        ⚠️ **LA MÊME RÈGLE QUE `Compte.appellation`, ET POUR LA MÊME RAISON.**

        Une demande de contact ne porte qu'UN champ de nom : le prénom d'un
        fondateur est donc souvent vide, et `f"Bonjour {prenom}"` rend
        « Bonjour ». C'est exactement ce qui est parti vers une adhérente le
        28 septembre, suivi d'une demande d'argent.

        ⚠️ **NE COMPOSEZ PAS LA SALUTATION À LA MAIN.** `nom` seul rend le bon
        résultat aujourd'hui, et le jour où le formulaire portera deux champs il
        rendra le mauvais. Un cas de `test_courriel.py` lit le code source et
        refuse toute autre façon de remplir `prenom`.
        """
        return self.prenom or self.nom
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


class CanalDeLaPiece(StrEnum):
    """Par où la pièce est arrivée au cabinet.

    ⚠️ AJOUTÉ LE 14 OCTOBRE 2026, À LA DEMANDE DU CABINET : « les documents
    envoyés par WhatsApp sont versés par le juriste ». Une pièce reçue ne disait
    pas d'où elle venait : six mois plus tard, devant un document illisible, on ne
    savait plus s'il fallait le redemander à la cliente ou le rechercher dans une
    conversation.
    """

    #: Déposée par la cliente, depuis le lien de suivi sans compte.
    LIEN_DE_SUIVI = "LIEN_DE_SUIVI"
    #: Déposée par la cliente, depuis son compte.
    ESPACE_CLIENT = "ESPACE_CLIENT"
    #: Reçue par messagerie, versée par un collaborateur.
    WHATSAPP = "WHATSAPP"
    #: Apportée au cabinet.
    GUICHET = "GUICHET"
    COURRIEL = "COURRIEL"


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
    #: Par où elle est arrivée. `None` pour les pièces reçues avant le 14 octobre 2026.
    canal: CanalDeLaPiece | None = None
    #: Qui l'a versée au dossier : un compte du cabinet, ou « cliente ».
    versee_par: str | None = None

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
        """La date du dernier mouvement, l'ouverture si rien n'a bougé.

        C'est l'indicateur qui fait remonter un dossier dans le pipeline. Un
        dossier de création qui dort est un client qui part.

        ─────────────────────────────────────────────────────────────────────
        ⚠️ **UNE PIÈCE QUI ARRIVE EST UN MOUVEMENT**, et elle ne comptait pas.

        Seuls les jalons étaient lus, c'est-à-dire les franchissements d'étape,
        c'est-à-dire les gestes du cabinet. Depuis que la cliente dépose
        elle-même par son lien scellé, elle peut envoyer cinq documents un
        mardi sans qu'aucun jalon ne bouge : le pipeline continuait d'annoncer
        « immobile depuis trois semaines », et le chargé de formalités relançait
        en premier celle qui venait de répondre.

        **Un indicateur qui ment est pire qu'un indicateur absent** : celui-ci
        décide chaque matin de l'ordre des relances.

        ⚠️ On lit les dates de pièces plutôt que de poser un jalon : un `Jalon`
        est le franchissement d'une ÉTAPE, et en inventer un pour une pièce
        fausserait l'histoire du dossier, que le panneau des jalons affiche tel
        quel.

        ⚠️ La colonne dénormalisée suit toute seule : elle est recalculée de
        cette propriété à chaque écriture, jamais saisie (voir `tables.py`).
        ─────────────────────────────────────────────────────────────────────
        """
        mouvements = [self.dernier_geste_du_cabinet]
        if self.derniere_piece_recue is not None:
            mouvements.append(self.derniere_piece_recue)
        return max(mouvements)

    @property
    def dernier_geste_du_cabinet(self) -> date:
        """Le dernier franchissement d'étape, l'ouverture si rien n'a bougé."""
        return max((j.survenu_le for j in self.jalons), default=self.ouvert_le)

    @property
    def derniere_piece_recue(self) -> date | None:
        """Le dernier dépôt de la cliente, ou `None` si elle n'a rien envoyé."""
        recues = [p.fournie_le for p in self.pieces if p.fournie_le is not None]
        return max(recues, default=None)

    @property
    def attend_le_cabinet(self) -> bool:
        """La cliente a bougé la dernière, et personne ne lui a répondu.

        ─────────────────────────────────────────────────────────────────────
        ⚠️ **CETTE PROPRIÉTÉ RÉPARE UNE CONSÉQUENCE DE `immobile_depuis`.**

        Compter le dépôt d'une cliente comme un mouvement a rendu l'indicateur
        honnête et la priorité fausse. Le pipeline se lit du mouvement le plus
        ancien au plus récent ; un dossier où elle vient d'envoyer cinq
        documents affiche « sans mouvement depuis 1 j » et **tombe en bas de la
        liste**, sous ceux où il ne se passe rien.

        Mesuré : trois dossiers, celui où elle a déposé hier après trois
        semaines de silence du cabinet passe DERNIER, derrière celui où
        personne n'a rien fait.

        Avant la correction il restait en tête, mais pour une mauvaise raison
        et sous une étiquette qui mentait. Les deux comportements étaient
        fautifs, différemment.

        ⚠️ **CE QUI MANQUAIT EST LA QUESTION « QUI A BOUGÉ EN DERNIER ».** Un
        dossier où personne n'agit et un dossier où la cliente attend une
        réponse ne demandent pas le même geste, et le second est le plus
        pressant : c'est exactement le client qui part, que cet indicateur
        existe pour retenir.

        Rien à stocker : les deux dates sont déjà là.

        ⚠️ **COMPARAISON LARGE, ET NON STRICTE. C'EST LE POINT DÉLICAT.**

        Les deux repères sont des DATES, sans heure. Une comparaison stricte
        manquerait le cas le plus courant : dossier ouvert le matin, pièces
        envoyées l'après-midi, puis trois semaines de silence. Les deux dates
        sont égales, et la cliente n'est **jamais** signalée. C'est exactement
        la situation que cette propriété existe pour attraper, et le premier
        jet la laissait passer.

        Le prix de la comparaison large : un dossier où le cabinet franchit une
        étape le jour même du dépôt reste marqué « en attente » tant qu'aucune
        étape n'est franchie un jour ultérieur. Ce n'est pas un mauvais signal :
        un dossier qui n'avance plus après ce jour-là mérite qu'on le regarde.

        ⚠️ Passer à l'horodatage réglerait l'ambiguïté et demanderait de changer
        le type de `fournie_le`, donc les données déjà écrites. On ne le fait
        pas pour une précision dont personne n'a exprimé le besoin ; si un jour
        elle manque, c'est ici qu'il faudra revenir.
        ─────────────────────────────────────────────────────────────────────
        """
        if self.clos or self.derniere_piece_recue is None:
            return False
        return self.derniere_piece_recue >= self.dernier_geste_du_cabinet
