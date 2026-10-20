"""Le lien par lequel la fondatrice suit son dossier, sans compte.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE LIEN EXISTE, ET POURQUOI IL N'EST PAS CELUI DE LA PROFORMA

Entre son paiement et son immatriculation — les semaines où elle attend le plus —
la cliente n'avait **rien** : ni dépôt de pièce, ni avancement. Les dix routes du
contexte exigent `SUIVRE_FORMALITE`, une habilitation de collaborateur.

Le canal prévu était WhatsApp. Le cabinet a constaté que cela ne tient pas : on
ne peut écrire librement qu'à l'intérieur de la **fenêtre de vingt-quatre heures**
ouverte par le dernier message du client, et hors de cette fenêtre le seul recours
est un modèle approuvé — dont aucun ne l'est. Une formalité dure des semaines ;
bâtir sa chaîne documentaire sur cette horloge, c'est la remettre à autrui.

⚠️ **AUCUN MOT DE PASSE, ET C'EST LA DÉCISION DU 27 SEPTEMBRE QUI TIENT.**

« Un formulaire à identifiants pour envoyer une photo de sa carte d'identité
ferait abandonner la moitié des dossiers. » Elle vient de payer ; elle n'a pas
envie de créer un compte. Le compte à mot de passe reste ce qu'il est : il arrive
**avec la société**, au NIU, et seulement si elle a pris le suivi.

CE QUI LE DISTINGUE DU LIEN D'ACCEPTATION

`LienDAcceptation` est **à usage unique** : on n'accepte qu'une fois. Celui-ci se
rouvre autant de fois qu'elle le veut, sur plusieurs semaines. Les réemployer
l'un pour l'autre aurait fait d'une signature contractuelle un lien banal, ou de
ce suivi-ci un lien mort au premier clic.

⚠️ **IL N'A PAS DE DATE D'EXPIRATION, ET C'EST RÉFLÉCHI.**

Sa durée de vie est **celle du dossier** : il ouvre tant que la formalité est en
cours, et cesse dès qu'elle est convertie ou abandonnée. Inventer trente ou
quatre-vingt-dix jours aurait fait l'un des deux : l'enfermer dehors au milieu de
sa formalité, ou laisser un lien vivant sur un dossier clos. Le référentiel ne
porte aucun délai de formalité validé ; le fabriquer ici aurait été écrire une
durée que personne n'a décidée.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

# ⚠️ Le sceau vit à `app/partage/`, et non ici : la Collecte doit le vérifier
# aussi, pour accepter le fichier qu'elle dépose. Le graphe des dépendances
# n'autorise pas ces deux contextes à se voir, et une arête ajoutée par
# commodité ne se retire jamais. Voir l'en-tête de `partage/sceau.py`.
from app.partage.sceau import SceauInvalide, sceller
from app.partage.sceau import exiger_le_sceau as _exiger_le_sceau

__all__ = ["LienDeSuiviInvalide", "exiger_le_sceau", "sceau_de_suivi"]

#: Conservé sous le nom du contexte : les appelants disent « le sceau de suivi »,
#: pas « le sceau ». Le mécanisme, lui, est partagé.
LienDeSuiviInvalide = SceauInvalide


def sceau_de_suivi(reference: str, *, secret: str) -> str:
    """Le sceau du dossier, seul élément que la cliente présente."""
    return sceller(reference, secret=secret)


def exiger_le_sceau(reference: str, sceau: str, *, secret: str) -> None:
    """Lève si le sceau ne vaut pas. L'appelant rend 404, jamais 403."""
    _exiger_le_sceau(reference, sceau, secret=secret)
