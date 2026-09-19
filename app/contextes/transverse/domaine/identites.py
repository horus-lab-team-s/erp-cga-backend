"""Le compte : une identité, et rien d'autre.

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE COMPTE NE PORTE PAS

**Ni rôle, ni portée.** Un compte ne « possède » pas le rôle de comptable : il en
est titulaire depuis une date, et peut cesser de l'être. C'est le même patron que
le statut daté du contexte B, appliqué ici pour la même raison — et l'enjeu est
plus grand encore.

La question qu'un contrôle pose n'est jamais « qui est comptable ? » mais **« qui
était habilité le 12 mars, jour où cette déclaration a été déposée ? »**. Un rôle
stocké en colonne ne sait répondre qu'à la première. Le jour où un réviseur quitte
le cabinet et qu'on supprime son rôle, toutes les déclarations qu'il a déposées
apparaîtraient rétroactivement comme déposées par quelqu'un qui n'y était pas
habilité. Le dossier serait à refaire, et il serait faux.

Les habilitations vivent donc dans `habilitations.py`, datées, jamais supprimées.

**Ni mot de passe.** Le compte porte une **empreinte** opaque. Le domaine ne sait
pas si elle vient d'Argon2, de bcrypt ou d'autre chose, et c'est délibéré : les
paramètres d'un algorithme de dérivation se règlent en fonction du matériel du
serveur, ce qui est une préoccupation d'infrastructure. Le jour où l'on augmente
le coût mémoire d'Argon2, aucune entité ne change.

UNE EMPREINTE À `None` N'EST PAS UN COMPTE SANS MOT DE PASSE

C'est un compte **créé mais jamais activé** — typiquement celui d'un adhérent dont
le paiement vient d'être validé et à qui le lien de définition a été envoyé. Il
existe, il est nommé, il est rattaché à son dossier, et il ne peut pas encore
ouvrir de session. Représenter cet état par un mot de passe vide ou par une valeur
sentinelle exposerait à ce qu'une comparaison malheureuse le laisse entrer.

L'ADRESSE ÉLECTRONIQUE EST L'IDENTIFIANT DE CONNEXION, PAS LA CLÉ

Elle change : un adhérent quitte son fournisseur, une collaboratrice se marie. La
clé du compte est un identifiant technique stable, et le courriel est un attribut
modifiable, unique à un instant donné. L'inverse — le courriel comme clé primaire —
rend tout changement d'adresse impossible sans casser le journal d'audit qui y
renvoie.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

__all__ = [
    "SEUIL_TENTATIVES",
    "Compte",
    "CompteInactif",
    "EtatCompte",
]

#: Contrôle volontairement grossier. Valider une adresse selon la RFC 5322 est un
#: piège classique : l'expression exacte fait des centaines de caractères, rejette
#: des adresses valides, et ne prouve toujours pas que la boîte existe. La seule
#: preuve qui compte est que le lien d'activation arrive — et le parcours de
#: souscription la fournit gratuitement. On se contente donc d'écarter les fautes
#: de frappe évidentes.
_ADRESSE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

#: Nombre d'échecs consécutifs au-delà duquel le compte se verrouille.
#:
#: Cinq, et non trois : à trois, un utilisateur légitime qui hésite entre deux de
#: ses mots de passe se retrouve dehors, appelle le cabinet, et l'on prend
#: l'habitude de déverrouiller sans vérifier qui appelle — ce qui coûte plus cher
#: en sécurité que les deux essais supplémentaires n'en font gagner.
SEUIL_TENTATIVES = 5


class EtatCompte(StrEnum):
    """Trois états, et la distinction entre les deux derniers compte.

    `EN_ATTENTE_ACTIVATION` — le compte existe, le lien est parti, personne n'a
    encore défini de mot de passe.

    `ACTIF` — le compte est utilisable.

    `SUSPENDU` — un administrateur l'a coupé. **Le compte n'est jamais supprimé**,
    car son identifiant figure dans le journal d'audit et dans les écritures qu'il
    a validées ; le supprimer rendrait cet historique illisible. Un collaborateur
    qui part est suspendu, et ses habilitations sont fermées à sa date de départ.
    """

    EN_ATTENTE_ACTIVATION = "EN_ATTENTE_ACTIVATION"
    ACTIF = "ACTIF"
    SUSPENDU = "SUSPENDU"


class CompteInactif(RuntimeError):
    """Le compte existe mais ne peut pas ouvrir de session.

    ⚠️ Ce message ne doit **jamais** être relayé tel quel à celui qui tente de se
    connecter : il apprendrait que l'adresse existe. Voir
    `application/authentification.py`, qui le convertit en réponse indifférenciée.
    """


class Compte(BaseModel):
    """Une personne qui peut se connecter.

    Immuable : chaque changement produit un nouvel exemplaire. C'est ce qui permet
    au journal d'audit d'enregistrer un « avant » et un « après » qui ne bougent
    plus sous ses pieds.
    """

    model_config = ConfigDict(frozen=True)

    #: Clé technique stable. Ne change jamais, même si l'adresse change.
    identifiant: str = Field(min_length=1)

    courriel: str = Field(min_length=3)
    nom: str = Field(min_length=1)
    prenom: str = Field(min_length=1)

    #: Le locataire au sens multi-tenant : le cabinet. Le CGA est le tenant, ses
    #: adhérents sont des sous-entités — voir 05-securite-multitenant.md § 1.
    locataire: str = Field(min_length=1)

    #: Opaque au domaine. `None` = compte jamais activé, voir l'en-tête.
    #:
    #: ⚠️ `exclude=True` : **jamais sérialisée**. Le défaut a existé — la route
    #: publique de définition du mot de passe rendait le compte, empreinte
    #: comprise, à un appelant non authentifié. Une empreinte Argon2 livrée est
    #: de la matière à casser hors ligne, tranquillement, sans limite de
    #: tentatives et sans que rien ne l'enregistre.
    #:
    #: L'exclusion est portée par le **champ**, et non par chaque route : une
    #: exclusion à écrire route par route est une exclusion qu'on oubliera à la
    #: prochaine.
    empreinte_mot_de_passe: str | None = Field(default=None, exclude=True)

    etat: EtatCompte = EtatCompte.EN_ATTENTE_ACTIVATION
    cree_le: datetime

    #: Numéro de téléphone au format camerounais, pour le second facteur à venir
    #: et pour les relances par message. Facultatif : un adhérent peut n'avoir
    #: qu'une adresse électronique.
    telephone: str | None = None

    #: Secret du second facteur, en base 32. `None` = non enrôlé.
    #:
    #: ⚠️ Stocké **en clair**, et ce n'est pas un oubli : contrairement à un mot
    #: de passe, il doit être relu pour recalculer le code attendu. Il devra être
    #: chiffré au repos en base, avec une clé qui n'y figure pas. Voir l'en-tête
    #: de `second_facteur.py`.
    #:
    #: ⚠️ `exclude=True`, pour la même raison que l'empreinte : le rendre une
    #: seule fois dans une réponse suffirait à ce que quiconque la lit puisse
    #: engendrer les codes du porteur pour toujours. Il n'est montré qu'à
    #: l'enrôlement, dans une réponse construite pour cela, et jamais depuis
    #: l'entité.
    secret_totp: str | None = Field(default=None, exclude=True)

    tentatives_echouees: int = Field(default=0, ge=0)
    verrouille_jusqu_a: datetime | None = None
    derniere_connexion: datetime | None = None

    @field_validator("courriel", mode="before")
    @classmethod
    def _normaliser(cls, valeur: object) -> object:
        """Minuscules et espaces retirés, puis contrôle de forme.

        Sans cette normalisation, `Jean@Cga.cm` et `jean@cga.cm` seraient deux
        comptes distincts, et le second créé rendrait le premier inaccessible sans
        que personne ne comprenne pourquoi.
        """
        if not isinstance(valeur, str):
            return valeur
        adresse = valeur.strip().lower()
        if not _ADRESSE.match(adresse):
            raise ValueError(
                f"« {valeur} » n'a pas la forme d'une adresse électronique. "
                "Une arobase et un point dans le domaine sont attendus."
            )
        return adresse

    # ── Ce que le compte sait dire de lui-même ──────────────────────────────

    @computed_field
    @property
    def active(self) -> bool:
        return self.etat == EtatCompte.ACTIF and self.empreinte_mot_de_passe is not None

    @computed_field
    @property
    def second_facteur_actif(self) -> bool:
        """Sérialisé : c'est ce champ qui décide si l'écran propose l'enrôlement
        ou la saisie d'un code. Le secret lui-même n'apparaît jamais dans une
        réponse — seul son existence est publiée."""
        return self.secret_totp is not None

    @computed_field
    @property
    def nom_complet(self) -> str:
        return f"{self.prenom} {self.nom}"

    def verrouille(self, a_l_instant: datetime) -> bool:
        """Le verrou est une **date de fin**, pas un drapeau.

        Un booléen `verrouille` exigerait une tâche de fond pour le remettre à
        faux, et le compte resterait bloqué si cette tâche tombait. Une date se
        périme toute seule.
        """
        return self.verrouille_jusqu_a is not None and a_l_instant < self.verrouille_jusqu_a

    def exiger_ouverture(self, a_l_instant: datetime) -> None:
        """Lève `CompteInactif` si la session ne peut pas s'ouvrir."""
        if self.etat == EtatCompte.SUSPENDU:
            raise CompteInactif(f"compte {self.identifiant} suspendu")
        if self.empreinte_mot_de_passe is None:
            raise CompteInactif(
                f"compte {self.identifiant} jamais activé — le lien de définition du "
                "mot de passe n'a pas été suivi"
            )
        if self.verrouille(a_l_instant):
            raise CompteInactif(
                f"compte {self.identifiant} verrouillé jusqu'au {self.verrouille_jusqu_a}"
            )

    # ── Transitions ─────────────────────────────────────────────────────────

    def avec_empreinte(self, empreinte: str, *, a_l_instant: datetime) -> Compte:
        """Définit ou remplace le mot de passe, et active le compte.

        Remet les compteurs à zéro : quelqu'un qui vient de prouver, par un lien
        reçu sur sa boîte, qu'il contrôle l'adresse, n'a pas à subir le verrou
        provoqué par les tentatives de quelqu'un d'autre.
        """
        etat = EtatCompte.ACTIF if self.etat != EtatCompte.SUSPENDU else self.etat
        return self.model_copy(
            update={
                "empreinte_mot_de_passe": empreinte,
                "etat": etat,
                "tentatives_echouees": 0,
                "verrouille_jusqu_a": None,
                "derniere_connexion": self.derniere_connexion or a_l_instant,
            }
        )

    def apres_echec(self, a_l_instant: datetime, duree_verrou: object) -> Compte:
        """Incrémente le compteur, et verrouille au seuil.

        `duree_verrou` est un `timedelta` — typé `object` pour éviter d'importer
        `timedelta` dans une signature que seul l'appelant renseigne.
        """
        tentatives = self.tentatives_echouees + 1
        verrou = a_l_instant + duree_verrou if tentatives >= SEUIL_TENTATIVES else None  # type: ignore[operator]
        return self.model_copy(
            update={"tentatives_echouees": tentatives, "verrouille_jusqu_a": verrou}
        )

    def apres_succes(self, a_l_instant: datetime) -> Compte:
        return self.model_copy(
            update={
                "tentatives_echouees": 0,
                "verrouille_jusqu_a": None,
                "derniere_connexion": a_l_instant,
            }
        )

    def avec_second_facteur(self, secret: str) -> Compte:
        """Enrôle ou réenrôle le second facteur.

        Le réenrôlement écrase l'ancien secret : un téléphone perdu se remplace,
        et conserver l'ancien laisserait vivre un facteur que son porteur ne
        contrôle plus.
        """
        return self.model_copy(update={"secret_totp": secret})

    def sans_second_facteur(self) -> Compte:
        """Retire le second facteur. Réservé à la réinitialisation par un tiers (pas 60)."""
        return self.model_copy(update={"secret_totp": None})

    def suspendre(self) -> Compte:
        return self.model_copy(update={"etat": EtatCompte.SUSPENDU})

    def retablir(self) -> Compte:
        etat = (
            EtatCompte.ACTIF
            if self.empreinte_mot_de_passe is not None
            else EtatCompte.EN_ATTENTE_ACTIVATION
        )
        return self.model_copy(update={"etat": etat})
