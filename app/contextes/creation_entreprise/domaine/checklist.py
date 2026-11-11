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

from dataclasses import dataclass

from app.contextes.creation_entreprise.domaine.entites import PieceConstitution
from app.contextes.portefeuille.contrats import FormeJuridique

__all__ = [
    "CAPITAL_EXIGE",
    "PROFESSIONS_REGLEMENTEES",
    "SOCIETES",
    "SituationDuFondateur",
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
# ⚠️ 8 novembre 2026 (réunion du 4 novembre) : le bail ne se signe qu'au nom de la société,
# donc une fois les statuts prêts. Il reste au dossier de constitution, jamais à la demande.
_JUSTIFICATIF_SIEGE = PieceConstitution(
    code="JUSTIFICATIF_SIEGE",
    libelle=(
        "Justificatif du siège, établi une fois les statuts prêts : bail au nom de la "
        "société, titre foncier ou attestation d'hébergement"
    ),
)
_PLAN_LOCALISATION = PieceConstitution(
    code="PLAN_LOCALISATION",
    libelle="Plan de localisation du siège",
    obligatoire=False,
)
# ⚠️ 8 novembre 2026 : le cabinet rédige les statuts à partir de son modèle ; ceux qu'un
# client apporte sont facultatifs (question `statuts_apportes`). Ce qui est attendu ici, ce
# sont les statuts SIGNÉS, quel que soit leur auteur (9 novembre 2026 : le libellé le dit).
_STATUTS = PieceConstitution(
    code="STATUTS",
    libelle=(
        "Statuts signés par tous les associés : rédigés par le cabinet, ou les vôtres "
        "s'ils sont déjà prêts"
    ),
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

# ── L'immatriculation de l'entreprise et du personnel (9 novembre 2026) ─────────
#
# La liste du responsable juridique, dans ses mots. Ce qui concerne CHAQUE employé (copies
# de la CNI ou de l'acte de naissance, téléphone, quartier, adresse, plan, arrondissements
# de naissance des parents) se recueille sur la fiche du salarié, au module social.

_ID_GERANT = PieceConstitution(code="ID_GERANT", libelle="Photocopie de la CNI du gérant")
_COORDONNEES_ENTREPRISE = PieceConstitution(
    code="COORDONNEES_ENTREPRISE",
    libelle="Le numéro de téléphone de l'entreprise et son adresse mail",
)
_LISTE_PERSONNEL = PieceConstitution(code="LISTE_PERSONNEL", libelle="Liste du personnel")
_CACHET_ENTREPRISE = PieceConstitution(
    code="CACHET_ENTREPRISE", libelle="Le cachet de l'entreprise (une empreinte)"
)

#: Ce que toute entreprise ajoute pour son immatriculation et celle de ses employés.
_IMMATRICULATION: tuple[PieceConstitution, ...] = (
    _ID_GERANT,
    _COORDONNEES_ENTREPRISE,
    _LISTE_PERSONNEL,
    _CACHET_ENTREPRISE,
)

# ── Les pièces de situation (8 novembre 2026, réunion du 4 novembre) ──────────
#
# Elles ne dépendent pas de la forme mais de QUI crée et de QUOI : un promoteur étranger,
# une profession à Ordre, une activité minière, une demande faite par un tiers.

_PASSEPORT_FONDATEUR = PieceConstitution(
    code="ID_FONDATEUR",
    libelle="Passeport du fondateur, en cours de validité",
)
_AUTORISATION_ETRANGER = PieceConstitution(
    code="AUTORISATION_EXERCER_ETRANGER",
    libelle="Autorisation ou attestation d'exercer l'activité, pour un promoteur étranger",
)
_PROCURATION = PieceConstitution(
    code="PROCURATION",
    libelle="Procuration signée du promoteur, et pièce d'identité du mandataire",
)
_AUTORISATION_MINIERE = PieceConstitution(
    code="AUTORISATION_MINIERE",
    libelle="Autorisation ou titre minier exigé pour l'activité (pièces à préciser par le cabinet)",
    # ⚠️ Non bloquante tant que le cabinet n'a pas dit lesquelles : un dépôt ne se refuse
    # pas sur une pièce dont personne n'a encore écrit le nom.
    obligatoire=False,
)

#: Les professions à Ordre (ou à Chambre) proposées au questionnaire, et leur libellé.
#: ⚠️ Liste à valider par le cabinet (version 3 du questionnaire de création).
PROFESSIONS_REGLEMENTEES: dict[str, str] = {
    "MEDECIN": "médecin",
    "CHIRURGIEN_DENTISTE": "chirurgien-dentiste",
    "PHARMACIEN": "pharmacien",
    "VETERINAIRE": "vétérinaire",
    "AVOCAT": "avocat",
    "NOTAIRE": "notaire",
    "HUISSIER": "huissier de justice",
    "ARCHITECTE": "architecte",
    "EXPERT_COMPTABLE": "expert-comptable",
    "AUTRE": "la profession",
}


@dataclass(frozen=True)
class SituationDuFondateur:
    """Ce que le questionnaire de création (version 3) dit du fondateur et de l'activité.

    Tout est faux par défaut : un dossier ouvert sans ces réponses garde la checklist
    d'avant, sans pièce de situation.
    """

    etranger: bool = False
    #: Un code de `PROFESSIONS_REGLEMENTEES`, ou `None`.
    profession_reglementee: str | None = None
    activite_miniere: bool = False
    pour_le_compte_d_un_tiers: bool = False

    @classmethod
    def depuis_les_faits(cls, faits: dict) -> SituationDuFondateur:
        """Depuis les réponses de la qualification, telles que l'événement les porte."""

        def vrai(valeur: object) -> bool:
            return valeur is True or str(valeur).strip().lower() in {"true", "oui", "1"}

        profession = str(faits.get("profession_reglementee") or "").strip().upper()
        return cls(
            etranger=str(faits.get("nationalite_promoteur") or "").upper() == "ETRANGERE",
            profession_reglementee=(profession if profession in PROFESSIONS_REGLEMENTEES else None),
            activite_miniere=vrai(faits.get("activite_miniere")),
            pour_le_compte_d_un_tiers=vrai(faits.get("pour_le_compte_d_un_tiers")),
        )


def _pieces_de_situation(situation: SituationDuFondateur) -> tuple[PieceConstitution, ...]:
    pieces: list[PieceConstitution] = []
    if situation.etranger:
        pieces.append(_AUTORISATION_ETRANGER)
    if situation.profession_reglementee:
        pieces.append(
            PieceConstitution(
                code="ATTESTATION_ORDRE",
                libelle=(
                    "Attestation d'inscription à l'Ordre (ou à la Chambre) : "
                    f"{PROFESSIONS_REGLEMENTEES[situation.profession_reglementee]}"
                ),
            )
        )
    if situation.activite_miniere:
        pieces.append(_AUTORISATION_MINIERE)
    if situation.pour_le_compte_d_un_tiers:
        pieces.append(_PROCURATION)
    return tuple(pieces)


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
    FormeJuridique.SA: _SOCLE_SOCIETE + (_DECLARATION_SOUSCRIPTION, _COMMISSAIRE_COMPTES),
    FormeJuridique.SCI: _SOCLE_SOCIETE,
    FormeJuridique.GIE: (_STATUTS, _ID_DIRIGEANTS, _PV_NOMINATION, _REGLEMENT_INTERIEUR),
    FormeJuridique.ASSOCIATION: (_STATUTS, _PV_NOMINATION, _REGLEMENT_INTERIEUR),
}


def checklist_de(
    forme: FormeJuridique, situation: SituationDuFondateur | None = None
) -> tuple[PieceConstitution, ...]:
    """Les pièces à réunir pour constituer une entreprise de cette forme.

    Rend des pièces **non fournies** : c'est un modèle de dossier, pas un état.
    L'appelant y reporte ensuite ce qui a été reçu.

    Une forme inconnue rend le socle plutôt que de lever. C'est délibéré : une
    forme juridique ajoutée au portefeuille et pas encore décrite ici doit
    permettre d'ouvrir un dossier — avec une liste incomplète que le chargé de
    formalités complétera — plutôt que d'interdire de le saisir. Le contraire
    ferait perdre un client pour une case manquante dans une table.

    8 novembre 2026 : la `situation` ajoute ses pièces (étranger, Ordre, mines, procuration),
    et un promoteur étranger fournit son passeport à la place de la pièce d'identité.
    """
    situation = situation or SituationDuFondateur()
    socle = _SOCLE
    if situation.etranger:
        socle = tuple(_PASSEPORT_FONDATEUR if p is _IDENTITE_FONDATEUR else p for p in socle)
    return (
        socle + _PAR_FORME.get(forme, ()) + _pieces_de_situation(situation) + _IMMATRICULATION
    )
