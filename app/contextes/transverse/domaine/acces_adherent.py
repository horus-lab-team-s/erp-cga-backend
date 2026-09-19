"""Rendre l'accès à un adhérent, par son chargé de clientèle (pas 116).

─────────────────────────────────────────────────────────────────────────────────
D'OÙ VIENT CE MODULE

Maquette « Espace adhérent CGA », vue A (« Accès au compte »), note 5 : « Perte de téléphone :
réinitialisation par le chargé de clientèle, jamais en autonomie : le dossier contient des documents
fiscaux. » Le produit ouvre l'espace par courriel et mot de passe ; l'accès par téléphone (code reçu
par SMS ou WhatsApp) attend un compte d'envoi (question Q29). Ce qui manquait, et qui ne dépend de
personne :

    l'adhérent n'a jamais reçu, ou a perdu, son      rien : le lien d'activation part au
    lien d'activation (A-003, « l'état le plus       paiement, une fois ; personne au cabinet
    fréquent en production »)                        ne pouvait le renvoyer
    l'adhérent a oublié son mot de passe et appelle  le cabinet lui disait d'utiliser « mot de
    le cabinet                                       passe oublié », sans rien pouvoir faire

⚠️ CE QUI PROTÈGE CE GESTE

L'en-tête de `jetons.py` le dit déjà : un geste de renvoi trop facile donne « l'habitude de renvoyer
des liens sans vérifier qui demande ». D'où trois garde-fous :

1. **Le lien part à l'adresse déjà au dossier, jamais à une autre.** L'appelant qui dit « j'ai
   changé d'adresse, envoyez-le sur celle-ci » est exactement le scénario d'usurpation. Changer
   d'adresse est un signalement (pas 114), instruit à part.
2. **La vérification est écrite** : comment le chargé de clientèle s'est assuré de parler au bon
   interlocuteur (« rappelé au numéro du dossier », « venu au cabinet avec sa CNI »). Elle va au
   journal, avec son nom.
3. **Un nombre borné de renvois par jour** pour un même compte (référentiel) : un chargé de clientèle
   harcelé au téléphone ne multiplie pas les liens valides.

Le type du lien se déduit de l'état du compte, jamais de la requête : un compte en attente reçoit
un lien d'**activation** (7 jours), un compte actif un lien de **réinitialisation** (2 heures), un
compte suspendu **rien** (sa levée est un acte d'administration, pas de relation client).
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.contextes.transverse.domaine.identites import EtatCompte
from app.contextes.transverse.domaine.jetons import TypeJeton

__all__ = ["RenvoiRefuse", "ReglagesDuRenvoi", "renvois_du_jour", "type_de_lien"]


class RenvoiRefuse(ValueError):
    """Le renvoi contredit l'état du compte ou la limite du jour."""


class ReglagesDuRenvoi(BaseModel):
    """`Docs/referentiel/acces/adherents.yaml`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    renvois_par_jour: int = Field(default=2, ge=1, le=10)
    #: La longueur minimale de la vérification écrite : « ok » ne dit pas comment on a vérifié.
    verification_minimum: int = Field(default=15, ge=10, le=200)
    source: str = "valeurs par défaut"


def type_de_lien(etat: EtatCompte) -> TypeJeton:
    if etat is EtatCompte.EN_ATTENTE_ACTIVATION:
        return TypeJeton.ACTIVATION
    if etat is EtatCompte.ACTIF:
        return TypeJeton.REINITIALISATION
    raise RenvoiRefuse(
        "ce compte est suspendu : aucun lien ne lui est envoyé. Sa levée est un acte "
        "d'administration, avec son motif."
    )


def renvois_du_jour(dates_des_renvois: list[date], jour: date) -> int:
    return sum(1 for d in dates_des_renvois if d == jour)
