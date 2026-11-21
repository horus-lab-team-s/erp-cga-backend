"""Préparer la DSF, la faire valider, constater son dépôt (14 octobre 2026).

Voir `domaine/declaration_dsf.py` pour les trois gestes et leur raison d'être.

⚠️ LA STRUCTURE DU CONTENU EST LA NÔTRE. Le modèle Excel « format DGI » et la
spécification JSON de l'API de télédéclaration ne se lisent qu'une fois connecté
au compte du contribuable (guide DGI, février 2024) : le cabinet ne les a pas
encore fournis. Le contenu figé porte donc la liasse telle que la plateforme la
calcule — postes, totaux, passage fiscal, contrôles —, nommée en clair, pour être
reportée sur le portail. C'est le même choix, et pour la même raison, que le
bordereau de la TVA.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from app.contextes.cloture.domaine.declaration_dsf import (
    DeclarationDSF,
    EtatDSF,
    TransitionDSFRefusee,
)
from app.contextes.portefeuille.api import Exercice
from app.contextes.transverse.api import (
    AccuseReception,
    DocumentATransmettre,
    FormatTransmission,
    JournalAudit,
    ModeDepot,
    Portail,
    PortailDeclaratif,
    empreinte_document,
)
from app.partage.horloge import jour_a_douala

__all__ = [
    "DSFNonPreparable",
    "constater_le_depot_de_la_dsf",
    "contenu_de_la_liasse",
    "document_de_la_dsf",
    "preparer_la_dsf",
    "valider_la_dsf",
]


class DSFNonPreparable(RuntimeError):
    """Ce qui empêche de préparer ou de valider : tout, d'un coup, avec quoi faire."""


def contenu_de_la_liasse(liasse: dict) -> str:
    """La liasse, sérialisée de façon déterministe : c'est ce texte qui est haché.

    `sort_keys` et séparateurs sans espace, comme le bordereau de la TVA : deux
    sérialisations du même dossier qui différeraient donneraient deux empreintes,
    et l'accusé ne se rattacherait plus à la version.
    """
    return json.dumps(
        liasse, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )


def preparer_la_dsf(
    liasse: dict,
    *,
    exercice: Exercice,
    existantes: list[DeclarationDSF],
    accuse_existant: AccuseReception | None = None,
    par: str,
    a_l_instant: datetime,
) -> tuple[DeclarationDSF, list[DeclarationDSF]]:
    """Fige la liasse en une nouvelle version, et remplace celles qui n'étaient pas déposées.

    Rend la version et les versions remplacées, à enregistrer ensemble.

    ⚠️ `accuse_existant` : l'accusé DSF de l'exercice au registre du guichet, s'il y en
    a un. Une DSF peut avoir été déposée hors de ce parcours (constat d'avant le 14
    octobre 2026, reprise d'un dossier) : la préparer encore ferait croire qu'elle
    reste à faire, et la déposer serait refusé au dernier geste. On le dit au premier.
    """
    obstacles: list[str] = []
    if not exercice.clos:
        obstacles.append(
            f"l'exercice {exercice.libelle} n'est pas clos. La DSF se prépare sur la balance "
            "définitive : clore l'exercice d'abord (Clôture et DSF)."
        )
    en_echec = [c["libelle"] for c in liasse["controles"] if not c["satisfait"]]
    if en_echec:
        obstacles.append(f"contrôles de la liasse en échec : {' ; '.join(en_echec)}.")
    if liasse["comptes_non_couverts"]:
        obstacles.append(
            "comptes absents du plan de la liasse : "
            f"{', '.join(liasse['comptes_non_couverts'])}. Leur solde n'atteindrait aucun poste."
        )
    deposee = next((d for d in existantes if d.etat is EtatDSF.DEPOSEE), None)
    if deposee is None and accuse_existant is not None:
        obstacles.append(
            f"la DSF {exercice.libelle} est déjà déposée à la DGI (accusé "
            f"{accuse_existant.numero} du {accuse_existant.depose_le:%d/%m/%Y}). La DSF "
            "rectificative n'est pas encore construite : elle se fait sur le portail de la DGI."
        )
    if deposee is not None:
        obstacles.append(
            f"la DSF {exercice.libelle} est déjà déposée (version {deposee.version}, accusé "
            f"{deposee.numero_accuse}). La DSF rectificative n'est pas encore construite : "
            "elle se fait sur le portail de la DGI."
        )
    if obstacles:
        raise DSFNonPreparable(" ".join(obstacles))

    contenu = contenu_de_la_liasse(liasse)
    version = max((d.version for d in existantes), default=0) + 1
    remplacees = [
        d.remplacer() for d in existantes if d.etat in (EtatDSF.PREPAREE, EtatDSF.VALIDEE)
    ]
    dsf = DeclarationDSF(
        entreprise=liasse["entreprise"],
        denomination=liasse["denomination"],
        exercice=exercice.libelle,
        periode_debut=exercice.ouverture,
        periode_fin=exercice.cloture,
        version=version,
        contenu=contenu,
        empreinte=empreinte_document(contenu),
        systeme=liasse["systeme"],
        resultat_comptable=Decimal(str(liasse["resultat_comptable"])),
        resultat_fiscal=Decimal(str(liasse["resultat_fiscal"])),
        preparee_par=par,
        preparee_le=a_l_instant,
    )
    return dsf, remplacees


def valider_la_dsf(dsf: DeclarationDSF, *, par: str, a_l_instant: datetime) -> DeclarationDSF:
    """Le second regard. Refuse une version qui repose sur une valeur non validée.

    ⚠️ Un seuil du Système Normal ou un taux d'abattement encore « à valider » au
    référentiel peut changer le système déclaré ou l'impôt : le fiscaliste tranche
    avant que la DSF parte, pas après.
    """
    liasse = json.loads(dsf.contenu)
    reserves = []
    if liasse.get("systeme_non_valide"):
        reserves.append("le seuil du Système Normal")
    if liasse.get("passage_non_valide"):
        reserves.append("une valeur du passage au résultat fiscal")
    if reserves:
        raise DSFNonPreparable(
            f"{dsf.identifiant} repose sur {' et '.join(reserves)} encore à valider au "
            "référentiel. Le fiscaliste les valide, puis on prépare une nouvelle version."
        )
    return dsf.valider(par=par, a_l_instant=a_l_instant)


def document_de_la_dsf(dsf: DeclarationDSF) -> DocumentATransmettre:
    """Ce qui part à la DGI : la version, avec la référence de l'obligation DSF."""
    return DocumentATransmettre(
        portail=Portail.DGI_TELEDECLARATION,
        code_document="DSF",
        entreprise=dsf.entreprise,
        periode_debut=dsf.periode_debut,
        periode_fin=dsf.periode_fin,
        format=FormatTransmission.SAISIE_MANUELLE,
        contenu=dsf.contenu,
    )


def constater_le_depot_de_la_dsf(
    dsf: DeclarationDSF,
    *,
    numero: str,
    depose_le: datetime,
    piece: str | None,
    par: str,
    portail: PortailDeclaratif,
    journal: JournalAudit,
    a_l_instant: datetime,
) -> tuple[DeclarationDSF, AccuseReception]:
    """Consigne l'accusé de la DGI sur la version validée ; l'obligation DSF devient déclarée.

    ⚠️ La date est celle de l'accusé, jamais celle de la saisie : c'est elle que
    l'administration retient pour le retard.
    """
    if depose_le > a_l_instant:
        raise DSFNonPreparable(
            f"dépôt daté du {depose_le:%d/%m/%Y %H:%M}, postérieur à maintenant : on ne "
            "consigne pas un dépôt qui n'a pas eu lieu."
        )
    if jour_a_douala(depose_le) <= dsf.periode_fin:
        raise DSFNonPreparable(
            f"dépôt daté du {depose_le:%d/%m/%Y}, avant la fin de l'exercice "
            f"({dsf.periode_fin:%d/%m/%Y}) : vérifier la date de l'accusé."
        )
    # Le geste du domaine d'abord : une version non validée est refusée avant que
    # l'accusé n'entre au registre, d'où il ne ressortirait pas.
    deposee = dsf.deposer(numero=numero, depose_le=depose_le, par=par, piece=piece)
    document = document_de_la_dsf(dsf)
    consigne = portail.enregistrer_accuse(
        AccuseReception(
            numero=numero,
            portail=Portail.DGI_TELEDECLARATION,
            reference_document=document.reference,
            depose_le=depose_le,
            mode=ModeDepot.MANUEL,
            empreinte_deposee=dsf.empreinte,
            depose_par=par,
            piece_jointe=piece,
            precision=f"DSF {dsf.exercice}, version {dsf.version}",
        ),
        document=document,
    )
    journal.ajouter(
        horodatage=a_l_instant,
        acteur=par,
        action="cloture.dsf_deposee",
        objet_type="dsf",
        objet_id=dsf.identifiant,
        apres={
            "dossier": dsf.entreprise,
            "exercice": dsf.exercice,
            "version": dsf.version,
            "accuse": consigne.numero,
            "depose_le": consigne.depose_le,
            # ⚠️ Pas sous `empreinte` : le journal expurge cette clé (voir le dépôt TVA).
            "sha256_de_la_dsf": dsf.empreinte,
            "validee_par": dsf.validee_par,
        },
    )
    return deposee, consigne


# `TransitionDSFRefusee` remonte telle quelle : la route la rend en 409.
_ = TransitionDSFRefusee
