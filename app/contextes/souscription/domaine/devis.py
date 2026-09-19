"""Le devis : ce qui a été promis, à quel prix, et jusqu'à quand.

─────────────────────────────────────────────────────────────────────────────────
UN DEVIS FIGE LE BARÈME DU JOUR OÙ IL A ÉTÉ ÉTABLI

Ses lignes portent des montants, pas des renvois au catalogue. C'est la
différence entre un devis et une page de tarifs : la page affiche le prix
d'aujourd'hui, le devis engage sur celui d'hier.

Sans cette copie, un devis reçu le 20 mars et ouvert le 2 avril afficherait le
barème d'avril. Le client verrait un autre chiffre que celui qu'on lui a annoncé,
et le cabinet n'aurait aucun moyen de savoir lequel il avait promis. C'est
exactement le défaut que signale déjà la page d'estimation du site : « un lien
ouvert plus tard affiche des montants » différents.

LA VALIDITÉ EST UNE DATE, ET ELLE EST COURTE

Trente jours. Un devis sans date de fin est une promesse perpétuelle : quelqu'un
le ressort deux ans plus tard et réclame le prix d'alors. Trente jours laissent le
temps de décider et bornent l'engagement.

Passée cette date, le devis n'est pas supprimé — il devient **caduc**. La nuance
compte : il reste consultable, et le prospect qui revient voit ce qu'on lui avait
proposé plutôt qu'une page introuvable.

CE QUE LE DEVIS N'EST PAS

Ce n'est pas une facture. Il n'a aucune valeur comptable, ne porte pas de TVA
collectée, et n'entre dans aucune écriture. La facture d'honoraires du cabinet
relève du contexte K, qui la soumettra aux mêmes règles de conformité que celles
qu'il contrôle chez ses adhérents.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.contextes.souscription.domaine.offre import NatureService, Periodicite
from app.partage.telephone import normaliser_telephone

__all__ = [
    "DUREE_VALIDITE",
    "Devis",
    "EtatDevis",
    "LigneDevis",
    "Prospect",
]

#: Trente jours — voir l'en-tête.
DUREE_VALIDITE = timedelta(days=30)


class EtatDevis(StrEnum):
    EMIS = "EMIS"
    #: Une souscription a été engagée à partir de ce devis.
    ENGAGE = "ENGAGE"
    #: Passé sa date de validité sans avoir été engagé.
    CADUC = "CADUC"
    ABANDONNE = "ABANDONNE"


class Prospect(BaseModel):
    """Celui qui demande, avant d'être un adhérent.

    Il n'est **pas** une `Entreprise` du contexte B, et ce n'en est pas une
    version dégradée : il n'a pas encore de dossier, parfois pas encore de NIU,
    et il peut ne jamais en avoir si le devis n'aboutit pas. Créer l'entreprise
    au moment du devis remplirait le portefeuille de dossiers qui n'existent pas.
    """

    model_config = ConfigDict(frozen=True)

    nom: str = Field(min_length=1)
    prenom: str = Field(min_length=1)
    courriel: str = Field(min_length=3)

    #: Obligatoire : c'est le numéro qui paiera, et celui sur lequel se fera le
    #: rapprochement si l'identifiant du paiement se perd.
    telephone: str = Field(min_length=9)

    denomination: str | None = None

    #: Le NIU de l'entreprise, quand elle en a un. `None` pour une création ou
    #: pour une entreprise qui n'a pas encore été immatriculée.
    niu: str | None = None

    #: Déclaré par le prospect, non vérifié. Sert à choisir la formule, et rien
    #: d'autre : le régime réel se constate sur pièces, pas sur déclaration.
    chiffre_affaires_declare: Decimal | None = Field(default=None, ge=0)

    @field_validator("telephone", mode="before")
    @classmethod
    def _normaliser(cls, valeur: object) -> object:
        return normaliser_telephone(valeur) if isinstance(valeur, str) else valeur

    @field_validator("courriel", mode="before")
    @classmethod
    def _minuscules(cls, valeur: object) -> object:
        return valeur.strip().lower() if isinstance(valeur, str) else valeur

    @computed_field
    @property
    def nom_complet(self) -> str:
        return f"{self.prenom} {self.nom}"


class LigneDevis(BaseModel):
    """Une prestation chiffrée, au prix du jour de l'établissement."""

    model_config = ConfigDict(frozen=True)

    service: str = Field(min_length=1)
    libelle: str = Field(min_length=1)
    #: `None` = sur étude. La ligne figure au devis, elle n'entre pas au total.
    montant: Decimal | None = Field(default=None, ge=0)
    periodicite: Periodicite
    nature: NatureService
    formule: str | None = None
    precision: str | None = None

    @computed_field
    @property
    def chiffree(self) -> bool:
        return self.montant is not None


class Devis(BaseModel):
    """Une proposition datée, valable trente jours."""

    model_config = ConfigDict(frozen=True)

    reference: str = Field(min_length=1)
    prospect: Prospect
    lignes: list[LigneDevis] = Field(min_length=1)

    etabli_le: date
    valide_jusqu_au: date
    etat: EtatDevis = EtatDevis.EMIS

    #: Renseigné quand une souscription en est issue.
    souscription: str | None = None

    @computed_field
    @property
    def montant_total(self) -> Decimal:
        """Somme des seules lignes chiffrées.

        Une ligne sur étude vaut zéro dans le total et le dit par ailleurs — voir
        `complet`. L'inverse, refuser de totaliser un devis partiellement chiffré,
        priverait le prospect du montant qu'il peut déjà connaître.
        """
        return sum(
            (ligne.montant for ligne in self.lignes if ligne.montant is not None),
            Decimal(0),
        )

    @computed_field
    @property
    def complet(self) -> bool:
        """Toutes les lignes sont chiffrées.

        Sérialisé : c'est ce champ qui commande l'affichage du bouton de paiement.
        Un devis incomplet se lit, se discute, et ne se règle pas en ligne — voir
        l'en-tête de `offre.py`.
        """
        return all(ligne.chiffree for ligne in self.lignes)

    @computed_field
    @property
    def abonnement_mensuel(self) -> Decimal:
        """Ce qui sera dû chaque mois, annoncé séparément."""
        return sum(
            (
                ligne.montant
                for ligne in self.lignes
                if ligne.montant is not None and ligne.nature == NatureService.ABONNEMENT
            ),
            Decimal(0),
        )

    @computed_field
    @property
    def montant_a_regler(self) -> Decimal:
        """Ce qui est encaissé **maintenant**, et la règle mérite d'être lue.

        Les prestations ponctuelles et annuelles sont dues immédiatement.

        Un abonnement, lui, ne l'est que **s'il est seul au devis** : c'est alors
        sa première échéance qui met la prestation en route, et il faut bien
        encaisser quelque chose pour ouvrir l'accès. Adossé à une création
        d'entreprise, il ne gonfle pas le total à régler — c'est exactement ce que
        la page d'estimation annonce en note : « cet abonnement se règle
        mensuellement, il n'entre pas dans le total à régler à la création ».

        Le backend doit dire la même chose que la vitrine. Un client qui lit
        165 000 sur le site et voit 177 500 au moment de payer n'achète pas, et il
        a raison.

        En aucun cas les douze mois ne sont réclamés d'avance : réclamer 150 000 à
        quelqu'un à qui l'on a annoncé 12 500 par mois est la manière la plus
        rapide de perdre une souscription.
        """
        immediat = sum(
            (
                ligne.montant
                for ligne in self.lignes
                if ligne.montant is not None and ligne.nature != NatureService.ABONNEMENT
            ),
            Decimal(0),
        )
        if immediat > 0:
            return immediat
        return self.abonnement_mensuel

    def caduc(self, a_la_date: date) -> bool:
        return a_la_date > self.valide_jusqu_au

    def payable(self, a_la_date: date) -> bool:
        """Trois conditions, et il faut les trois.

        Encore ouvert, encore valide, entièrement chiffré. Un devis engagé qui
        redeviendrait payable produirait un second encaissement pour la même
        prestation.
        """
        return (
            self.etat == EtatDevis.EMIS
            and not self.caduc(a_la_date)
            and self.complet
            and self.montant_a_regler > 0
        )

    def engager(self, souscription: str) -> Devis:
        if self.etat != EtatDevis.EMIS:
            raise ValueError(
                f"devis {self.reference} à l'état {self.etat} : seul un devis émis "
                "s'engage. L'engager de nouveau produirait un second encaissement pour "
                "la même prestation."
            )
        return self.model_copy(update={"etat": EtatDevis.ENGAGE, "souscription": souscription})

    def perimer(self) -> Devis:
        """Rend le devis caduc. Ne le supprime pas — voir l'en-tête."""
        if self.etat != EtatDevis.EMIS:
            return self
        return self.model_copy(update={"etat": EtatDevis.CADUC})
