"""La demande de contact : ce qu'un visiteur dépose, et rien de plus.

─────────────────────────────────────────────────────────────────────────────────
CE QU'ELLE N'EST PAS, ET C'EST LE PLUS IMPORTANT

Ce n'est **ni un compte, ni un client, ni un dossier**. C'est une intention, et
elle peut très bien ne mener nulle part. La majorité des demandes n'aboutissent
pas, et c'est normal.

Cette phrase a des conséquences très concrètes :

* aucune `Entreprise` du contexte B n'est créée ici, et aucun NIU n'est réclamé ;
* aucun compte n'est ouvert, aucun mot de passe n'est dérivé, aucun tenant n'est
  provisionné. L'espace du client s'ouvre à l'étape 9, après paiement ;
* une demande sans suite se classe, elle ne se supprime pas.

Créer un compte au dépôt du formulaire produirait des milliers d'espaces vides à
surveiller, à purger et à protéger, sans qu'aucun ne corresponde à un client.

SIX CHAMPS, PAS TRENTE

Le formulaire demande le strict nécessaire pour rappeler quelqu'un : un nom, un
numéro joignable, un courriel facultatif, le service souhaité, un champ libre, et
la case de consentement. Chaque champ supplémentaire est un visiteur de moins qui
va au bout.

Le seul champ réellement obligatoire au sens métier est **le téléphone** : c'est
le canal de rappel, celui du rapprochement de paiement plus tard, et le seul dont
on soit sûr qu'il existe chez un prospect camerounais. Le courriel, lui, est
facultatif et le reste.

LE CONSENTEMENT EST RECUEILLI ICI, PAS AILLEURS

Une case à cocher **non pré-cochée**, parce que la plateforme d'envoi l'exige et
que le droit applicable aussi. Il est modélisé comme un objet à part entière et
non comme un booléen, pour trois raisons qui apparaissent toutes plus tard :

1. il est **daté**, et la date fait foi le jour où quelqu'un conteste ;
2. il porte **la version du texte** qui a été affichée. Le texte évoluera ; un
   consentement recueilli sous l'ancien ne prouve rien sur le nouveau ;
3. il est **révocable**, et une révocation ne s'efface pas : elle s'enregistre.

C'est la réponse D5 du cadrage, mot pour mot : consenti, daté, révocable.

DEUX INVARIANTS, ET ILS SE TIENNENT PAR LA MAIN

Le premier : **on ne peut pas préférer WhatsApp sans y avoir consenti.** Laisser
la combinaison exister produirait des demandes que le responsable croit pouvoir
traiter et qu'il n'a pas le droit d'ouvrir. Le refus est ici, au dépôt, où il se
corrige en cochant une case, plutôt qu'à l'envoi, où il se découvre trois jours
plus tard.

Le second : **on ne peut pas préférer le courriel sans donner de courriel.** Même
raisonnement, autre canal.

POURQUOI L'ORIGINE EST UNE CHAÎNE ET NON UNE ÉNUMÉRATION

« Est-ce que cela varie sans le code ? » Oui. Les sources d'arrivée sont des
campagnes, des partenaires, des liens de parrainage, des salons. Il en naîtra une
par trimestre, décidée par le commercial un vendredi soir. Une énumération
obligerait à déployer pour l'accueillir, et le vendredi soir on écrirait
« AUTRE ».

L'origine est donc une clé libre, normalisée et bornée, dont la liste connue vit
au référentiel. Une origine inconnue n'est **pas** rejetée : perdre une demande
parce que la campagne n'a pas été déclarée serait absurde. Elle est enregistrée
telle quelle, et se retrouve dans le rapport des origines non déclarées.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.partage.copie import transiter
from app.partage.telephone import normaliser_telephone

__all__ = [
    "Canal",
    "ConsentementInvalide",
    "Consentement",
    "DemandeDeContact",
    "FENETRE_ANTI_DOUBLON",
    "ORIGINE_INCONNUE",
    "PreferenceIncoherente",
    "doublon_parmi",
    "normaliser_origine",
]

#: Vingt-quatre heures. Le même visiteur qui soumet deux fois parce que la page a
#: mis du temps à répondre ne doit pas produire deux fils de conversation, ni deux
#: responsables qui le rappellent l'un après l'autre.
#:
#: La valeur est une **valeur par défaut**, pas une constante : `doublon_parmi`
#: accepte la fenêtre en argument, et le paramètre daté du référentiel la fournira.
#: Le centre voudra l'allonger ou la raccourcir après trois mois d'usage réel, et
#: ce changement ne doit demander ni déploiement ni développeur.
FENETRE_ANTI_DOUBLON = timedelta(hours=24)

#: Ce que vaut une origine absente. Une demande arrivée sans provenance connue est
#: une demande directe, pas une demande invalide.
ORIGINE_INCONNUE = "directe"

#: Longueur du champ libre. Assez pour décrire un besoin, trop peu pour servir de
#: dépôt à quiconque trouverait le formulaire amusant.
LONGUEUR_MESSAGE = 2_000

_ORIGINE = re.compile(r"[^a-z0-9]+")


class PreferenceIncoherente(ValueError):
    """Le canal préféré ne peut pas être servi avec ce qui a été fourni."""


class ConsentementInvalide(ValueError):
    """La manipulation demandée n'a pas de sens sur ce consentement."""


class Canal(StrEnum):
    """Par où le client souhaite être repris.

    Trois valeurs, et celle-là est bien une énumération : la liste ne varie pas
    sans code, puisque chaque canal suppose un adaptateur qui sait l'emprunter.
    Ajouter un canal, c'est écrire ce qui l'emprunte.
    """

    #: Un message écrit sur WhatsApp. Suppose le consentement, et suppose aussi un
    #: modèle pré-approuvé tant que le client n'a rien écrit.
    WHATSAPP = "WHATSAPP"
    #: Un appel. WhatsApp ou réseau classique, le domaine ne tranche pas : c'est
    #: l'adaptateur qui choisit selon ce qui est joignable.
    APPEL = "APPEL"
    COURRIEL = "COURRIEL"


def normaliser_origine(brut: str | None) -> str:
    """Rend une clé stable à partir de ce que le lien de campagne a transmis.

    `Facebook Ads / Mars` et `facebook-ads-mars` désignent la même campagne. Les
    compter séparément rendrait le rapport des origines inutilisable, ce qui est
    la seule chose qu'on demande à ce champ.
    """
    if brut is None:
        return ORIGINE_INCONNUE
    aplati = _ORIGINE.sub("-", brut.strip().lower()).strip("-")
    return aplati[:60] or ORIGINE_INCONNUE


class Consentement(BaseModel):
    """L'accord d'être contacté, daté, versionné, révocable.

    Il n'existe pas d'instance représentant « pas encore demandé » : le
    consentement est recueilli au dépôt ou il ne l'est pas, et l'absence se dit
    par un refus explicite plutôt que par un objet à demi rempli.
    """

    model_config = ConfigDict(frozen=True)

    #: Ce que la case cochée vaut. `False` est une réponse valide et se conserve :
    #: elle interdit WhatsApp, elle n'interdit pas de rappeler au téléphone.
    accorde: bool

    #: Quand. Fait foi le jour où quelqu'un conteste avoir donné son accord.
    recueilli_le: datetime

    #: Quelle rédaction a été affichée. Versionnée au référentiel, jamais recopiée
    #: ici : le texte fait plusieurs paragraphes, et le dupliquer par demande
    #: garantirait qu'aucune correction ne se propage.
    version_du_texte: str = Field(min_length=1)

    revoque_le: datetime | None = None
    motif_revocation: str | None = None

    @property
    def vaut_maintenant(self) -> bool:
        """Accordé et non révoqué.

        C'est la seule question que le reste du système a le droit de poser. Lire
        `accorde` directement contournerait la révocation, et c'est exactement
        l'erreur que cet objet existe pour empêcher.
        """
        return self.accorde and self.revoque_le is None

    def revoquer(self, a_l_instant: datetime, motif: str) -> Consentement:
        """Le client se retire. La trace reste.

        Effacer le consentement plutôt que de le marquer révoqué priverait le
        cabinet de la preuve qu'il avait bien été donné pendant la période où il
        a écrit, ce qui est précisément ce qu'on lui demanderait de prouver.

        Rejouable : révoquer ce qui l'est déjà rend l'objet inchangé, avec sa
        date d'origine. Le prestataire de messagerie renvoie ses notifications de
        désabonnement, et la seconde ne doit pas repousser la première.
        """
        if not self.accorde:
            raise ConsentementInvalide(
                "révoquer un consentement qui n'a jamais été accordé. La demande "
                "n'a jamais autorisé WhatsApp : il n'y a rien à retirer, et "
                "enregistrer une révocation laisserait croire le contraire."
            )
        if self.revoque_le is not None:
            return self
        return self.model_copy(
            update={"revoque_le": a_l_instant, "motif_revocation": motif}
        )


class DemandeDeContact(BaseModel):
    """Ce qu'un visiteur dépose depuis la vitrine publique.

    Figée : ce qui a été déposé ne se réécrit pas. Ce que le responsable apprend
    ensuite se range dans la qualification, qui est un autre objet, et la
    distinction est délibérée. Corriger la demande à mesure de l'échange ferait
    disparaître ce que le client avait réellement écrit, qui est parfois la seule
    chose qui explique un malentendu.
    """

    model_config = ConfigDict(frozen=True)

    identifiant: str = Field(min_length=1)
    deposee_le: datetime

    nom: str = Field(min_length=2, max_length=120)

    #: Normalisé à `+237XXXXXXXXX`. Obligatoire, voir l'en-tête.
    telephone: str = Field(min_length=9)

    #: Facultatif, et il le reste. L'exiger coûterait des demandes.
    courriel: str | None = None

    #: La clé d'un service du catalogue, ou ce que le visiteur a choisi dans la
    #: liste. Non résolue ici : le domaine ne lit pas le catalogue, et une clé
    #: devenue obsolète ne doit pas empêcher de déposer.
    service_souhaite: str = Field(min_length=1, max_length=40)

    #: Le champ libre. Borné, voir `LONGUEUR_MESSAGE`.
    message: str | None = Field(default=None, max_length=LONGUEUR_MESSAGE)

    canal_prefere: Canal
    consentement: Consentement

    #: D'où vient le visiteur. Voir l'en-tête : clé libre, jamais rejetée.
    origine: str = ORIGINE_INCONNUE

    @field_validator("telephone", mode="before")
    @classmethod
    def _normaliser_le_numero(cls, valeur: object) -> object:
        return normaliser_telephone(valeur) if isinstance(valeur, str) else valeur

    @field_validator("courriel", mode="before")
    @classmethod
    def _minuscules(cls, valeur: object) -> object:
        if not isinstance(valeur, str):
            return valeur
        propre = valeur.strip().lower()
        return propre or None

    @field_validator("nom", "message", mode="before")
    @classmethod
    def _elaguer(cls, valeur: object) -> object:
        if not isinstance(valeur, str):
            return valeur
        propre = valeur.strip()
        return propre or None

    @field_validator("origine", mode="before")
    @classmethod
    def _normaliser_l_origine(cls, valeur: object) -> object:
        if valeur is None or isinstance(valeur, str):
            return normaliser_origine(valeur)
        return valeur

    @model_validator(mode="after")
    def _le_canal_doit_pouvoir_etre_emprunte(self) -> DemandeDeContact:
        """Les deux invariants de l'en-tête, refusés au dépôt.

        Pydantic enveloppe cette exception dans une `ValidationError` : c'est le
        comportement du cadre, et il n'y a pas lieu de le combattre. Le message
        y reste lisible, et c'est ce qui compte pour celui qui débogue.
        """
        if self.canal_prefere is Canal.WHATSAPP and not self.consentement.vaut_maintenant:
            raise PreferenceIncoherente(
                "canal préféré WhatsApp sans consentement en vigueur. La "
                "plateforme refuserait l'envoi, et le refus n'arriverait qu'au "
                "moment où le responsable croit avoir écrit."
            )
        if self.canal_prefere is Canal.COURRIEL and self.courriel is None:
            raise PreferenceIncoherente(
                "canal préféré courriel sans adresse. La demande serait affectée "
                "à un responsable qui n'aurait aucun moyen de la servir."
            )
        return self

    @property
    def joignable_sur_whatsapp(self) -> bool:
        """Le numéro existe toujours, le consentement peut ne plus valoir.

        Distinguer les deux importe : une révocation ne rend pas la demande
        intraitable, elle la fait basculer sur l'appel ou le courriel.
        """
        return self.consentement.vaut_maintenant

    def sans_consentement(
        self, a_l_instant: datetime, motif: str
    ) -> DemandeDeContact:
        """Le client se retire de WhatsApp. La demande reste, le canal change.

        Le canal préféré retombe sur l'appel, parce que le numéro reste le seul
        moyen dont on soit sûr. Laisser `WHATSAPP` violerait l'invariant du
        modèle, et rendrait l'objet impossible à relire depuis la base.
        """
        consentement = self.consentement.revoquer(a_l_instant, motif)
        canal = (
            Canal.APPEL if self.canal_prefere is Canal.WHATSAPP else self.canal_prefere
        )
        return transiter(self, consentement=consentement, canal_prefere=canal)


def doublon_parmi(
    nouvelle: DemandeDeContact,
    existantes: list[DemandeDeContact],
    *,
    fenetre: timedelta = FENETRE_ANTI_DOUBLON,
) -> DemandeDeContact | None:
    """Rend la demande que celle-ci répète, ou `None`.

    ─────────────────────────────────────────────────────────────────────────
    LE CRITÈRE EST LE NUMÉRO, ET LUI SEUL

    Pas le nom, qui s'écrit de quatre façons. Pas le courriel, qui est
    facultatif. Pas le service souhaité, parce qu'un visiteur qui hésite entre
    deux prestations soumet deux fois et n'a pas besoin de deux responsables :
    il a besoin d'un seul, à qui l'on dit qu'il hésite.

    LE RÉSULTAT N'EST PAS UN REJET

    C'est un rapprochement. La nouvelle demande n'est pas jetée : elle se
    rattache au fil déjà ouvert, et le responsable voit qu'elle est arrivée.
    Jeter la seconde ferait disparaître le champ libre, qui est souvent la
    précision que le visiteur revenait ajouter.

    POURQUOI CETTE FONCTION EST ICI ET NON DANS UNE REQUÊTE SQL

    Parce que la règle est du métier et qu'elle changera. Le dépôt sait
    chercher les demandes d'un numéro sur une période ; il ne sait pas ce
    qu'« être un doublon » veut dire, et le jour où le critère s'enrichira,
    c'est ce fichier qui bougera, pas l'adaptateur.
    ─────────────────────────────────────────────────────────────────────────
    """
    candidates = [
        ancienne
        for ancienne in existantes
        if ancienne.identifiant != nouvelle.identifiant
        and ancienne.telephone == nouvelle.telephone
        and timedelta(0) <= nouvelle.deposee_le - ancienne.deposee_le <= fenetre
    ]
    if not candidates:
        return None
    # La plus récente : c'est le fil vivant, celui que le responsable a sous les
    # yeux. Rattacher à la plus ancienne renverrait vers un échange déjà refermé.
    return max(candidates, key=lambda d: d.deposee_le)
