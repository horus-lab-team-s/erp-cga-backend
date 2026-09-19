"""Le dépôt d'un document auprès d'une administration, et la preuve qu'il a eu lieu.

─────────────────────────────────────────────────────────────────────────────────
⚠️ CE QUE JE NE SAIS PAS, ET QUI DÉTERMINE TOUT LE RESTE

**La DGI ne publie aucune spécification d'interface programmatique.** Le portail
de télédéclaration est un site que l'on remplit à la main. Il n'existe, à ma
connaissance, ni adresse d'API documentée, ni format d'échange publié, ni jeu
d'essai, ni bac à sable.

Inventer une charge utile, des noms de champs et des codes de retour produirait
un adaptateur qui compile, qui se teste contre lui-même, et qui ne fonctionnera
jamais. Ce serait pire que de ne rien écrire : le système paraîtrait branché.

**Ce module ne modélise donc pas l'interface de la DGI. Il modélise le dépôt.**
Ce qui suit est vrai que le dépôt se fasse par formulaire à l'écran ou par appel
réseau — et c'est précisément ce qui permettra de substituer l'un à l'autre sans
toucher au métier.

CE QU'IL FAUT OBTENIR DU CABINET AVANT D'ALLER PLUS LOIN

* Le portail accepte-t-il un **import de fichier** — tableur, XML, texte
  positionnel — ou faut-il tout saisir champ par champ ?
* La DSF se dépose-t-elle en liasse structurée, et sous quel format ?
* Que contient exactement l'accusé de réception ? Numéro, horodatage, empreinte
  du dépôt, montant repris ?
* Existe-t-il un compte de rattachement du CGA permettant de déposer **pour** un
  adhérent, ou faut-il les identifiants de chacun ?

La dernière question est la plus lourde : elle décide si la plateforme peut
déposer, ou seulement préparer.

LE DÉPÔT MANUEL N'EST PAS UN PIS-ALLER, C'EST LE MODE NOMINAL D'AUJOURD'HUI

Le dossier d'architecture l'exige pour toutes les intégrations : « systématiquement
un mode manuel de secours ». Ici, le manuel est le mode principal, et l'automatique
l'exception à venir.

Ce que la plateforme apporte dans ce mode n'est pas mince : elle produit des
chiffres contrôlés, elle refuse de préparer un dossier irrecevable, elle
enregistre l'accusé, elle en conserve l'empreinte, et elle sait dire trois ans
plus tard **ce qui a été déposé et par qui**. Le portail, lui, ne sait rien de
tout cela.

L'ACCUSÉ DE RÉCEPTION EST LA SEULE PREUVE QUI COMPTE

Une obligation n'est pas déposée parce que le système le croit : elle l'est parce
qu'un accusé existe. C'est lui qui porte la date opposable, celle que
l'administration retiendra pour calculer une pénalité de retard.

Il porte aussi **l'empreinte de ce qui a été déposé**. Sans elle, un adhérent qui
conteste trois ans plus tard n'a rien à opposer à « ce n'est pas ce que vous avez
envoyé » — et le cabinet non plus.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field

__all__ = [
    "AccuseReception",
    "DepotRefuseParLePortail",
    "DocumentATransmettre",
    "FormatTransmission",
    "ModeDepot",
    "Portail",
    "empreinte_document",
    "reference_de_depot",
]


class Portail(StrEnum):
    """Les guichets auxquels le cabinet dépose.

    Nommés, et non « externe » : une pénalité de retard DGI et un redressement
    CNPS ne se traitent pas pareil, et l'accusé de l'un ne prouve rien pour
    l'autre.
    """

    DGI_TELEDECLARATION = "DGI_TELEDECLARATION"
    CNPS_DIPE = "CNPS_DIPE"


class ModeDepot(StrEnum):
    """Comment le dépôt a été effectué.

    Enregistré sur l'accusé, et pas seulement sur le portail : le jour où
    l'automatique existera, les deux modes coexisteront — certains dossiers
    passeront par l'interface, d'autres resteront saisis à la main. Savoir lequel
    a servi est la première chose qu'on regarde quand un dépôt manque.
    """

    MANUEL = "MANUEL"
    AUTOMATIQUE = "AUTOMATIQUE"


class FormatTransmission(StrEnum):
    """Sous quelle forme le contenu est remis.

    ⚠️ `XML_ADMINISTRATION` est déclaré et **n'a pas de schéma** : la DGI n'en
    publie pas. Il figure ici pour que le jour où un schéma existe, il ait sa
    place — pas pour laisser croire qu'il en existe un.
    """

    #: Une saisie humaine au clavier sur le portail. Le contenu sert de bordereau.
    SAISIE_MANUELLE = "SAISIE_MANUELLE"
    #: Le format interne du système, lisible et vérifiable.
    JSON_INTERNE = "JSON_INTERNE"
    #: Colonnes séparées, pour un import de tableur — si le portail l'accepte.
    CSV = "CSV"
    #: ⚠️ Sans schéma publié à ce jour.
    XML_ADMINISTRATION = "XML_ADMINISTRATION"


class DepotRefuseParLePortail(RuntimeError):
    """Le guichet a refusé le dépôt.

    Distinct d'une panne réseau : un refus est une réponse, et il faut la
    conserver. Le message du portail est repris tel quel — le reformuler ferait
    perdre le seul élément qui permettra de corriger.
    """


def empreinte_document(contenu: str) -> str:
    """SHA-256 hexadécimal de ce qui est déposé.

    Calculée sur le contenu **exactement tel qu'il part**. Le recalculer plus
    tard sur une version régénérée ne prouverait rien : le barème, les règles ou
    les écritures auront changé, et l'on obtiendrait une empreinte différente
    pour un dépôt pourtant identique.
    """
    return hashlib.sha256(contenu.encode("utf-8")).hexdigest()


class DocumentATransmettre(BaseModel):
    """Ce qui part au guichet, figé.

    L'entité ne sait pas ce qu'est une déclaration de TVA : elle sait qu'un
    document identifié, portant sur un dossier et une période, doit être remis à
    un guichet. C'est ce qui permet à ce module d'appartenir au socle sans lire
    aucun contexte métier.
    """

    model_config = ConfigDict(frozen=True)

    portail: Portail
    #: Le code de l'obligation — « TVA », « DSF », « CNPS ». Opaque ici.
    code_document: str = Field(min_length=1)
    entreprise: str = Field(min_length=1)
    periode_debut: date
    periode_fin: date

    format: FormatTransmission
    #: Le contenu, sérialisé. C'est lui qui est haché, et lui qu'on archive.
    contenu: str = Field(min_length=1)

    #: Ce que l'administration attend en règlement, s'il y a lieu. Repris sur le
    #: bordereau, et confronté à l'accusé quand celui-ci le rend.
    montant_a_payer: Decimal | None = Field(default=None, ge=0)

    @computed_field
    @property
    def empreinte(self) -> str:
        return empreinte_document(self.contenu)

    @computed_field
    @property
    def reference(self) -> str:
        """L'identité du dépôt : un dossier, une obligation, une période.

        C'est cette clé qui empêche de déposer deux fois la même chose. Elle
        n'inclut pas la date de dépôt, et c'est le but : un second dépôt de la
        TVA de juillet doit se heurter au premier, quel que soit le jour.
        """
        return reference_de_depot(
            self.entreprise, self.code_document, self.periode_debut, self.periode_fin
        )


def reference_de_depot(
    entreprise: str, code_document: str, periode_debut: date, periode_fin: date
) -> str:
    """L'identité d'un dépôt, calculable **sans préparer le document**.

    ⚠️ Sortie de `DocumentATransmettre.reference` au pas 58, et non recopiée : les
    Obligations en ont besoin pour dire si une obligation a été déposée, et une
    seconde formule écrite à côté divergerait au premier changement de format. Le
    jour où elle diverge, chaque déclaration déposée redevient « en retard ».
    """
    return f"{entreprise}/{code_document}/{periode_debut:%Y%m%d}-{periode_fin:%Y%m%d}"


class AccuseReception(BaseModel):
    """La preuve du dépôt. Sans elle, rien n'a été déposé.

    Immuable et jamais reconstruite : un accusé recalculé serait un accusé
    fabriqué.
    """

    model_config = ConfigDict(frozen=True)

    #: Le numéro rendu par le guichet. C'est la référence que l'administration
    #: reconnaîtra, et la seule qu'elle reconnaîtra.
    numero: str = Field(min_length=1)
    portail: Portail
    reference_document: str = Field(min_length=1)

    #: La date **opposable**. C'est elle qui décide si le dépôt est dans les
    #: délais, pas la date à laquelle le dossier a été préparé.
    depose_le: datetime
    mode: ModeDepot

    #: L'empreinte de ce qui a été déposé — voir l'en-tête.
    empreinte_deposee: str = Field(min_length=64, max_length=64)

    #: Qui a déposé. `systeme` pour un dépôt automatique, l'identifiant du
    #: réviseur pour un dépôt manuel. Un dépôt sans auteur n'engage personne.
    depose_par: str = Field(min_length=1)

    #: Le montant que le guichet a repris, quand il en rend un.
    montant_constate: Decimal | None = Field(default=None, ge=0)

    #: Référence du justificatif archivé — capture d'écran, courriel, accusé
    #: imprimé. ⚠️ Non résolue tant que la GED du contexte K n'existe pas.
    piece_jointe: str | None = None

    precision: str | None = None

    def concerne(self, document: DocumentATransmettre) -> bool:
        """L'accusé porte-t-il bien sur **ce** document, à l'octet près ?

        Les deux conditions sont exigées. La référence seule laisserait passer un
        accusé obtenu sur une version antérieure du dossier — celle qu'on avait
        préparée avant de corriger une écriture —, et l'on croirait avoir déposé
        les chiffres corrigés.
        """
        return (
            self.reference_document == document.reference
            and self.empreinte_deposee == document.empreinte
        )

    @computed_field
    @property
    def verifiable(self) -> bool:
        """Un justificatif est archivé.

        Sérialisé : un accusé sans pièce jointe reste un accusé, mais il repose
        sur la seule parole de celui qui a saisi le numéro. La différence doit
        être visible à l'écran, pas enfouie.
        """
        return self.piece_jointe is not None
