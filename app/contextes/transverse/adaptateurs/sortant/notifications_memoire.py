"""L'envoi de courriels, retenu en mémoire au lieu de partir.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CET ADAPTATEUR EST, ET CE QU'IL N'EST PAS

Il réalise `ServiceNotification` en **conservant** les messages au lieu de les
émettre. Rien ne part, et rien ne se perd : chaque envoi demandé reste lisible,
ce qui permet aux tests de vérifier qu'un lien d'activation a bien été produit —
sans jamais l'envoyer à une vraie adresse pendant une exécution de tests.

Ce n'est donc pas un bouchon vide. C'est la même chose qu'un `console.EmailBackend`
côté Django, et c'est le mode de développement recommandé par le module
`mail+paiement/` du dépôt.

LA CIBLE EST DÉJÀ ÉCRITE, ET ELLE A LA MÊME FORME

Le module `mail+paiement/mail/` expose `send_template(code, to=…, context=…)` :
un code de gabarit, un destinataire, un contexte. Le port `ServiceNotification` a
été dessiné sur cette signature exactement, pour que le branchement soit une
substitution d'adaptateur et rien d'autre — pas une réécriture des appelants.

Ce qu'il restera à faire ce jour-là : reprendre les gabarits sous les codes
listés ici, remplacer le logo, et fournir `BREVO_API_KEY`.

POURQUOI `envoyer` NE LÈVE JAMAIS

Un courriel qui échoue ne doit annuler ni la création du compte, ni le paiement
qui l'a déclenchée. Le pire scénario n'est pas « le message n'est pas parti » —
il se rejoue —, c'est « le paiement a été encaissé et le compte n'existe pas ».
L'échec se journalise donc et se rejoue ; il n'interrompt rien.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

__all__ = ["CODES_MESSAGES", "MessageRetenu", "ServiceNotificationMemoire"]

#: Les codes de gabarit attendus par le contexte K. Ils suivent la convention
#: `objet.evenement` du module `mail/`, pour que la reprise soit littérale.
CODES_MESSAGES: dict[str, str] = {
    "compte.invitation": "Invitation d'un collaborateur — lien valable 14 jours",
    "compte.activation": "Bienvenue, définissez votre mot de passe — lien 7 jours",
    # Pas 140 : entre son règlement et son immatriculation, elle n'avait rien.
    "creation.dossier_ouvert": (
        "Dossier de formalité ouvert, avec le lien de suivi qui n'expire pas"
    ),
    # 14 octobre 2026 : le compte client s'ouvre au paiement de la création.
    "creation.dossier_ouvert_avec_compte": (
        "Dossier de formalité ouvert, compte client créé : choisir son mot de passe"
    ),
    # 15 octobre 2026 : ce que le cabinet a fait pour l'adhérent, sans qu'il se connecte.
    "adherent.quittance_verifiee": "Quittance vérifiée : l'échéance est payée",
    "adherent.quittance_refusee": "Quittance à renvoyer, avec le motif",
    "adherent.fonds_recus": "Fonds reçus par le cabinet, qui règle l'administration",
    "adherent.honoraires_regles": "Honoraires réglés : le reçu est dans l'espace",
    # 18 octobre 2026 (point 25) : la réponse relue du cabinet à un candidat.
    "recrutement.reponse": "Réponse du cabinet à un candidat",
    # 18 octobre 2026 (point 22) : la prestation ponctuelle, prise en charge puis livrée.
    "mission.ouverte": "Prestation ponctuelle prise en charge",
    "mission.livree": "Prestation ponctuelle livrée",
    "compte.adhesion_ajoutee": (
        "Votre société est immatriculée, l'espace adhérent s'ajoute à votre compte"
    ),
    # Pas 138 : elle engageait son entreprise et ne recevait rien.
    "proforma.acceptation": "Accusé d'acceptation d'une proposition commerciale",
    # Pas 134 : entre le dépôt du formulaire et le premier appel, la cliente
    # n'avait aucune trace écrite de son geste.
    "demande.accuse_de_depot": "Accusé de réception d'une demande de contact",
    # Pas 133 : une fondatrice n'a pas souscrit, elle a fait immatriculer sa
    # société — et son accès s'ouvre le jour du NIU, pas celui du paiement.
    "compte.activation_creation": (
        "Votre société est immatriculée, définissez votre mot de passe — lien 7 jours"
    ),
    "compte.reinitialisation": "Réinitialisation du mot de passe — lien 2 heures",
    "compte.mot_de_passe_change": "Confirmation d'un changement de mot de passe",
    "compte.suspendu": "Votre accès a été suspendu",
    # Pas 91 : la levée de suspension prévient le titulaire.
    "compte.retabli": "Votre accès est rétabli",
    "compte.second_facteur_confirmation": (
        "Confirmer l'enrôlement du second facteur — lien 30 minutes"
    ),
    "compte.second_facteur_enrole": "Un second facteur a été enrôlé sur votre compte",
    "compte.second_facteur_reinitialise": "Votre second facteur a été réinitialisé",
    "relance.proforma": "Relance d'une proposition transmise et sans réponse",
    # La proposition elle-même, envoyée au client dès que le responsable l'a émise.
    "proforma.envoi": "Votre proposition est prête, avec le lien pour la consulter",
    # 14 octobre 2026 : la même, avec le détail des rubriques et le document joint.
    "proforma.envoi.detaillee": (
        "Votre proposition, son détail rubrique par rubrique, et le document joint"
    ),
    "piece.rectificative_demandee": "Le cabinet demande une facture rectificative",
    # Pas 111 : la relance des pièces qui manquent au mois, composée par le comptable.
    "piece.relance": "Les pièces qui manquent pour terminer votre mois",
    "compte.acces_renvoye": "Lien d'accès renvoyé par le chargé de clientèle",
    "echeance.rappel": "Rappel d'échéance, selon les réglages de l'adhérent",
}


class MessageRetenu(BaseModel):
    """Un envoi demandé, conservé au lieu d'être émis."""

    model_config = ConfigDict(frozen=True)

    code: str
    destinataire: str
    contexte: dict[str, Any]


class ServiceNotificationMemoire:
    """Réalisation de `ServiceNotification`. Ne poste rien."""

    def __init__(self) -> None:
        self.messages: list[MessageRetenu] = []

    def envoyer(
        self, code: str, *, destinataire: str, contexte: dict[str, Any]
    ) -> bool:
        self.messages.append(
            MessageRetenu(code=code, destinataire=destinataire, contexte=dict(contexte))
        )
        return True

    def derniers(self, code: str | None = None) -> list[MessageRetenu]:
        """Les messages retenus, du plus récent au plus ancien."""
        retenus = [m for m in self.messages if code is None or m.code == code]
        return list(reversed(retenus))

    def vider(self) -> None:
        self.messages.clear()
