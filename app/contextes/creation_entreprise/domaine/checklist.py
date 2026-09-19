"""La checklist de constitution, par forme juridique.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI LA CHECKLIST VIT DANS LE DOMAINE ET NON AU RÉFÉRENTIEL

Le référentiel porte ce que la loi de finances change chaque année : taux, seuils,
délais, pénalités. La liste des pièces qu'un greffe exige pour immatriculer une
SARL ne change pas selon la loi de finances — elle change quand le droit des
sociétés est révisé, c'est-à-dire une fois par décennie, et elle change alors pour
tout le monde en même temps.

Y placer la checklist coûterait plus qu'elle ne rapporte : il faudrait porter au
YAML des structures imbriquées — une liste de pièces par forme juridique — là où
le référentiel ne sait porter que des valeurs datées. La complexité irait au
mauvais endroit.

**Ce qui reste au référentiel, c'est le chiffre** : le capital minimum d'une SA.
Un montant, une date d'entrée en vigueur, un fondement — exactement ce que le
référentiel sait faire, et exactement ce qu'une réforme OHADA modifie.

CE QUE CETTE LISTE N'EST PAS

Elle ne prétend pas être exhaustive ni opposable. Le CFCE peut réclamer une pièce
qui n'y figure pas, et cela n'a rien d'anormal : la pièce s'ajoute alors au
dossier. La liste dit ce qu'il faut préparer **avant** de se déplacer, ce qui est
sa seule fonction — éviter l'aller-retour au guichet.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from app.contextes.creation_entreprise.domaine.entites import PieceConstitution
from app.contextes.portefeuille.contrats import FormeJuridique

__all__ = [
    "CAPITAL_EXIGE",
    "SOCIETES",
    "checklist_de",
    "code_du_capital_minimum",
]


#: Les formes qui donnent naissance à une personne morale distincte du fondateur.
#:
#: L'entreprise individuelle (ETS) n'en est pas une : le commerçant *est*
#: l'entreprise. Pas de statuts, pas d'associés, pas de capital — et c'est ce qui
#: rend sa création si rapide, donc si vendable.
SOCIETES: frozenset[FormeJuridique] = frozenset(
    {
        FormeJuridique.SARL,
        FormeJuridique.SARLU,
        FormeJuridique.SAS,
        FormeJuridique.SA,
        FormeJuridique.SCI,
        FormeJuridique.GIE,
    }
)

#: Les formes dont le capital est encadré par un minimum légal, et le code du
#: paramètre qui le porte. Une forme absente a un capital libre.
CAPITAL_EXIGE: dict[FormeJuridique, str] = {
    FormeJuridique.SA: "CAPITAL_MINIMUM_SA",
    FormeJuridique.SARL: "CAPITAL_MINIMUM_SARL",
    FormeJuridique.SARLU: "CAPITAL_MINIMUM_SARL",
}


def code_du_capital_minimum(forme: FormeJuridique) -> str | None:
    """Le code du paramètre de capital minimum, ou `None` si le capital est libre."""
    return CAPITAL_EXIGE.get(forme)


# ── Les pièces, par famille ──────────────────────────────────────────────────
#
# Déclarées une fois, composées ensuite. La duplication d'un libellé entre deux
# formes juridiques finirait par diverger — et l'écart se verrait au guichet, pas
# à la relecture.

_IDENTITE_FONDATEUR = PieceConstitution(
    code="ID_FONDATEUR",
    libelle="Pièce d'identité du fondateur, en cours de validité",
)
_CASIER = PieceConstitution(
    code="CASIER_JUDICIAIRE",
    libelle="Extrait de casier judiciaire (bulletin n° 3) de moins de trois mois",
)
_JUSTIFICATIF_SIEGE = PieceConstitution(
    code="JUSTIFICATIF_SIEGE",
    libelle="Justificatif de domiciliation du siège — bail, titre foncier ou attestation",
)
_PLAN_LOCALISATION = PieceConstitution(
    code="PLAN_LOCALISATION",
    libelle="Plan de localisation du siège",
    obligatoire=False,
)
_STATUTS = PieceConstitution(
    code="STATUTS",
    libelle="Statuts signés par tous les associés",
)
_DECLARATION_SOUSCRIPTION = PieceConstitution(
    code="DECLARATION_SOUSCRIPTION",
    libelle="Déclaration notariée de souscription et de versement du capital",
)
_LISTE_ASSOCIES = PieceConstitution(
    code="LISTE_ASSOCIES",
    libelle="Liste des associés avec leurs apports",
)
_ID_DIRIGEANTS = PieceConstitution(
    code="ID_DIRIGEANTS",
    libelle="Pièces d'identité des dirigeants et des associés",
)
_PV_NOMINATION = PieceConstitution(
    code="PV_NOMINATION",
    libelle="Procès-verbal de nomination des dirigeants",
)
_COMMISSAIRE_COMPTES = PieceConstitution(
    code="COMMISSAIRE_COMPTES",
    libelle="Lettre d'acceptation du commissaire aux comptes",
)
_REGLEMENT_INTERIEUR = PieceConstitution(
    code="REGLEMENT_INTERIEUR",
    libelle="Règlement intérieur du groupement",
)

#: Ce que tout dossier comporte, quelle que soit la forme.
_SOCLE: tuple[PieceConstitution, ...] = (
    _IDENTITE_FONDATEUR,
    _CASIER,
    _JUSTIFICATIF_SIEGE,
    _PLAN_LOCALISATION,
)

#: Ce que toute société ajoute au socle.
_SOCLE_SOCIETE: tuple[PieceConstitution, ...] = (
    _STATUTS,
    _LISTE_ASSOCIES,
    _ID_DIRIGEANTS,
    _PV_NOMINATION,
)

_PAR_FORME: dict[FormeJuridique, tuple[PieceConstitution, ...]] = {
    # L'entreprise individuelle : le socle, et rien d'autre. C'est le dossier le
    # plus léger du catalogue, et c'est ce qui en fait le produit d'appel.
    FormeJuridique.ETS: (),
    FormeJuridique.PERSONNE_PHYSIQUE: (),
    FormeJuridique.SARL: _SOCLE_SOCIETE + (_DECLARATION_SOUSCRIPTION,),
    # La SARL unipersonnelle a des statuts et un capital, mais un seul associé :
    # la liste des associés et le PV de nomination n'ont pas d'objet — l'associé
    # unique se nomme lui-même dans les statuts.
    FormeJuridique.SARLU: (_STATUTS, _ID_DIRIGEANTS, _DECLARATION_SOUSCRIPTION),
    FormeJuridique.SAS: _SOCLE_SOCIETE + (_DECLARATION_SOUSCRIPTION,),
    # La SA est la seule forme à imposer un commissaire aux comptes dès la
    # constitution. C'est ce qui explique l'écart de prix avec la SARL, et le
    # dossier doit le montrer avant que le fondateur ne le découvre.
    FormeJuridique.SA: _SOCLE_SOCIETE
    + (_DECLARATION_SOUSCRIPTION, _COMMISSAIRE_COMPTES),
    FormeJuridique.SCI: _SOCLE_SOCIETE,
    FormeJuridique.GIE: (_STATUTS, _ID_DIRIGEANTS, _PV_NOMINATION, _REGLEMENT_INTERIEUR),
    FormeJuridique.ASSOCIATION: (_STATUTS, _PV_NOMINATION, _REGLEMENT_INTERIEUR),
}


def checklist_de(forme: FormeJuridique) -> tuple[PieceConstitution, ...]:
    """Les pièces à réunir pour constituer une entreprise de cette forme.

    Rend des pièces **non fournies** : c'est un modèle de dossier, pas un état.
    L'appelant y reporte ensuite ce qui a été reçu.

    Une forme inconnue rend le socle plutôt que de lever. C'est délibéré : une
    forme juridique ajoutée au portefeuille et pas encore décrite ici doit
    permettre d'ouvrir un dossier — avec une liste incomplète que le chargé de
    formalités complétera — plutôt que d'interdire de le saisir. Le contraire
    ferait perdre un client pour une case manquante dans une table.
    """
    return _SOCLE + _PAR_FORME.get(forme, ())
