"""La file d'anomalies du réviseur (pas 117), sous `/pilotage`.

─────────────────────────────────────────────────────────────────────────────────
UNE ROUTE

`GET /pilotage/file-d-anomalies` (`CONTROLER_CONFORMITE`) : les constats non écartés de **tout le
périmètre de l'appelant**, ordonnés par gravité puis par enjeu, avec leur ancienneté, qui a
déposé la pièce, et les règles regroupées.

POURQUOI AU PILOTAGE

La file croise trois contextes : les constats (conformité, qui ne lit personne), la pièce et son
déposant (collecte), la raison sociale du dossier (portefeuille). Le pilotage lit tous les contextes
et n'est lu par aucun : c'est sa place, comme pour les pièces manquantes du pas 111.

⚠️ **Un constat déjà écarté n'est plus à décider** : le rapport est arbitré (`rapport_arbitre`), et
ce qui reste est ce qui attend une décision. Les écarts en attente d'un second regard, eux, sont sur
leur propre écran (pas 92) : les remettre dans la file les ferait traiter deux fois.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.contextes.conformite.api import (
    FACTURES_DEMO,
    moteur_par_defaut,
    rapport_arbitre,
)
from app.contextes.pilotage.adaptateurs.entrant.routes_http import _depot_pieces, _sans_echouer
from app.contextes.pilotage.adaptateurs.sortant.file_d_anomalies_yaml import (
    charger_les_reglages_de_la_file,
)
from app.contextes.pilotage.domaine.file_d_anomalies import (
    GroupeDeRegle,
    LigneDAnomalie,
    ReglagesDeLaFile,
    compter_par_gravite,
    grouper_par_regle,
    ordonner_la_file,
)
from app.contextes.portefeuille.api import Entreprise
from app.contextes.transverse.api import (
    AccesRequis,
    Permission,
    exiger,
    restreindre,
)
from app.infrastructure.config import configuration
from app.partage.horloge import maintenant

routeur = APIRouter(prefix="/pilotage", tags=["Pilotage · file d'anomalies"])


def _reglages() -> ReglagesDeLaFile:
    return charger_les_reglages_de_la_file(configuration().dossier_referentiel)


class VueDeLaFile(BaseModel):
    #: Les compteurs de l'en-tête, avant filtre de gravité : « 6 bloquantes, 14 majeures… ».
    par_gravite: dict[str, int]
    lignes: list[LigneDAnomalie]
    groupes: list[GroupeDeRegle]
    seuil_anciennete_jours: int
    #: Les valeurs proposées aux filtres, telles que la file les contient.
    dossiers: list[dict]
    deposants: list[str]


@routeur.get(
    "/file-d-anomalies",
    summary="Les constats à décider, tout le portefeuille, par gravité et par enjeu",
)
def lire_la_file(
    acces: AccesRequis,
    gravite: str | None = Query(None),
    entreprise: str | None = Query(None),
    regle: str | None = Query(None),
    depose_par: str | None = Query(None),
    du: date | None = Query(None, description="Date de pièce, bornes incluses"),
    au: date | None = Query(None),
) -> VueDeLaFile:
    exiger(acces, Permission.CONTROLER_CONFORMITE)
    reglages = _reglages()
    jour = maintenant().date()
    moteur = moteur_par_defaut()

    # La pièce de la collecte, par référence de document : le déposant et la date réelle.
    pieces = {}
    for dossier_niu in {
        f.destinataire.niu for f in FACTURES_DEMO.values() if f.destinataire and f.destinataire.niu
    }:
        for piece in _sans_echouer(lambda niu=dossier_niu: _depot_pieces().du_dossier(niu), []):
            if piece.reference_document:
                pieces[(piece.entreprise, piece.reference_document)] = piece

    denominations = {e.niu: e.denomination for e in _entreprises()}
    lignes: list[LigneDAnomalie] = []
    for facture in restreindre(
        acces,
        FACTURES_DEMO.values(),
        lambda f: f.destinataire.niu if f.destinataire is not None else None,
    ):
        dossier = facture.destinataire.niu if facture.destinataire is not None else None
        if dossier is None:
            continue
        rapport = rapport_arbitre(moteur.controler(facture), dossier)
        piece = pieces.get((dossier, rapport.reference_document))
        date_piece = facture.document.date_emission
        if (du and date_piece < du) or (au and date_piece > au):
            continue
        for constat in rapport.constats:
            if constat.severite.value not in reglages.gravites_traitees:
                continue
            lignes.append(
                LigneDAnomalie(
                    piece=rapport.reference_document,
                    dossier=dossier,
                    denomination=denominations.get(dossier, dossier),
                    date_piece=date_piece,
                    fournisseur=facture.emetteur.denomination if facture.emetteur else None,
                    code_regle=constat.code_regle,
                    libelle_regle=constat.libelle,
                    gravite=constat.severite.value,
                    message=constat.message,
                    enjeu=constat.enjeu if isinstance(constat.enjeu, Decimal) else None,
                    anciennete=(jour - date_piece).days,
                    dort=(jour - date_piece).days > reglages.seuil_anciennete_jours,
                    depose_par=piece.depose_par if piece else None,
                )
            )

    par_gravite = compter_par_gravite(lignes)
    retenues = [
        l_
        for l_ in lignes
        if (gravite is None or l_.gravite == gravite)
        and (entreprise is None or l_.dossier == entreprise)
        and (regle is None or l_.code_regle == regle)
        and (depose_par is None or l_.depose_par == depose_par)
    ]
    return VueDeLaFile(
        par_gravite=par_gravite,
        lignes=ordonner_la_file(retenues),
        groupes=grouper_par_regle(retenues),
        seuil_anciennete_jours=reglages.seuil_anciennete_jours,
        dossiers=sorted(
            ({"niu": l_.dossier, "denomination": l_.denomination} for l_ in lignes),
            key=lambda d: d["denomination"],
        ),
        deposants=sorted({l_.depose_par for l_ in lignes if l_.depose_par}),
    )


def _entreprises() -> list[Entreprise]:
    from app.contextes.pilotage.adaptateurs.entrant.routes_http import _depot_dossiers

    return _sans_echouer(lambda: _depot_dossiers().lister(), [])


__all__ = ["routeur"]
