"""L'échange avec le logiciel du client : import et export des écritures (16 octobre 2026).

Extrait de `routes_http.py`, qui dépassait 1 600 lignes. Les routes gardent leurs chemins
(`/comptabilite/...`) ; le routeur est monté à côté de celui de la comptabilité.
"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.contextes.comptabilite.adaptateurs.entrant.routes_http import (
    depot,
    depot_des_journaux,
    depot_du_plan,
    exercice_du_portefeuille,
    periodes_verrouillees_du_dossier,
)
from app.contextes.comptabilite.adaptateurs.sortant.profils_echange import (
    charger_les_profils,
)
from app.contextes.comptabilite.application.reprise import (
    RapportDeReprise,
    reprendre_un_lot,
)
from app.contextes.comptabilite.domaine.echange import EchangeRefuse, lire, rendre

# ⚠️ L'arête `comptabilite → conformite` est déclarée au registre des services :
# c'est elle qui autorise cet import, et `test_architecture.py` la garde.
from app.contextes.transverse.api import (
    AccesRequis,
    Permission,
    exiger,
    exiger_dossier,
)
from app.infrastructure.config import configuration

routeur = APIRouter(prefix="/comptabilite", tags=["Comptabilité"])


# ── L'échange avec le logiciel du client ─────────────────────────────────────
#
# ⚠️ **UN CENTRE DE GESTION NE REMPLACE PAS LE LOGICIEL DE SES ADHÉRENTS.**
#
# Il s'y branche. Chaque adhérent arrive avec le sien, et la plateforme doit
# s'adapter au système final, jamais l'inverse. Le format est donc une **donnée**
# du référentiel : brancher un progiciel de plus, c'est déposer un fichier, pas
# modifier ce module.


class ProfilPublie(BaseModel):
    """Un profil d'échange, tel qu'un écran le propose."""

    code: str
    libelle: str
    #: Ce que le centre sait de ce profil, et ce qu'il ne garantit pas. Repris du
    #: référentiel : un exploitant qui choisit doit le lire sans ouvrir le dépôt.
    remarque: str
    colonnes: list[str]
    encodage: str


@routeur.get("/echange/profils", summary="Les formats d'export disponibles")
def lister_les_profils(acces: AccesRequis) -> list[ProfilPublie]:
    """Ce que le référentiel propose, à l'instant.

    ⚠️ Lue au référentiel et non écrite ici : un écran qui recopierait la liste
    proposerait un profil retiré, ou tairait un profil ajouté le matin même.
    """
    exiger(acces, Permission.LIRE_COMPTABILITE)
    return [
        ProfilPublie(
            code=profil.code,
            libelle=profil.libelle,
            remarque=profil.remarque,
            colonnes=[c.intitule or c.champ for c in profil.colonnes],
            encodage=profil.encodage,
        )
        for profil in _profils().values()
    ]


@routeur.get(
    "/dossiers/{entreprise}/export",
    summary="Exporter les écritures vers le logiciel du client",
    responses={
        403: {"description": "Habilitation insuffisante sur ce dossier"},
        404: {"description": "Profil d'échange inconnu"},
        409: {"description": "Le lot contient une écriture non validée"},
    },
)
def exporter(
    acces: AccesRequis,
    entreprise: str,
    exercice: str = Query(description="L'exercice à exporter."),
    profil: str = Query(
        "pivot-csv", description="Le code d'un profil du référentiel."
    ),
    journal: str | None = Query(None, description="Restreint à un journal."),
) -> Response:
    """Le fichier, encodé comme le logiciel destinataire l'attend.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **SEULES LES ÉCRITURES VALIDÉES SORTENT, ET LE REFUS EST FRANC.**

    Un brouillon est une écriture que le centre n'a pas engagée. L'exporter
    pousserait dans le système du client des mouvements que personne n'assume, et
    il les tiendrait pour arrêtés — il déclare avec, puis découvre au contrôle que
    le centre les a corrigés depuis.

    Le lot est donc refusé **en entier**, en nommant les écritures en cause. Un
    export qui filtrerait en silence livrerait un fichier incomplet, et l'écart ne
    se verrait qu'à la balance.

    ⚠️ **LE FICHIER EST RENDU EN OCTETS, PAS EN TEXTE.**

    L'encodage fait partie du format : le rendre en JSON ferait passer le contenu
    par de l'UTF-8, et le client recevrait des libellés abîmés après avoir cru
    télécharger le bon fichier. C'est le piège le plus coûteux de cet échange,
    parce qu'il ne se voit qu'après l'import.

    Les caractères que l'encodage cible ne connaît pas sont **remplacés**, jamais
    fatals : un libellé qui porterait un caractère exotique ne doit pas faire
    perdre le fichier entier d'un exercice.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)

    try:
        choisi = _profils()[profil]
    except KeyError as absent:
        raise HTTPException(
            status_code=404,
            detail=(
                f"aucun profil d'échange « {profil} ». Les profils du référentiel "
                f"sont : {', '.join(sorted(_profils())) or 'aucun'}."
            ),
        ) from absent

    ecritures = depot(entreprise).toutes(exercice)
    if journal is not None:
        ecritures = [e for e in ecritures if e.journal == journal]

    try:
        contenu = rendre(ecritures, choisi)
    except EchangeRefuse as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus

    octets = contenu.encode(choisi.encodage, errors="replace")
    nom = f"{entreprise}-{exercice}{'-' + journal if journal else ''}-{choisi.code}.csv"
    return Response(
        content=octets,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{nom}"',
            # ⚠️ L'encodage est annoncé dans l'en-tête **et** appliqué au contenu.
            # Un navigateur qui devine se trompe une fois sur deux, et le client
            # ouvre un fichier abîmé dans son tableur avant même de l'importer.
            "Content-Type": f"text/csv; charset={choisi.encodage}",
        },
    )


def _profils():
    """Les profils du référentiel, relus à chaque appel.

    ⚠️ Non mémoïsés, contrairement au moteur de conformité : un export se demande
    quelques fois par mois, et un profil corrigé le matin doit servir l'après-midi
    **sans redéploiement**. C'est le sens même de l'avoir mis au référentiel.
    """
    return charger_les_profils(configuration().dossier_referentiel / "echange")


#: ⚠️ Un plafond en octets, et pas en écritures : le contrôle doit porter sur ce
#: qui est reçu, avant d'être compris. Un exercice de PME fait quelques milliers
#: de lignes, soit quelques centaines de kilo-octets ; huit méga-octets laissent
#: dix fois la marge et n'épuisent pas la mémoire d'un conteneur.
TAILLE_MAXIMALE_DE_REPRISE = 8 * 1024 * 1024


@routeur.post(
    "/dossiers/{entreprise}/reprise",
    summary="Reprendre un fichier d'écritures venu du logiciel du client",
    responses={
        403: {"description": "Habilitation insuffisante sur ce dossier"},
        404: {"description": "Profil d'échange inconnu"},
        409: {"description": "Le fichier ne se lit pas avec ce profil"},
        413: {"description": "Fichier trop volumineux"},
    },
)
async def reprendre(
    acces: AccesRequis,
    entreprise: str,
    fichier: UploadFile = File(description="Le fichier exporté par l'autre logiciel."),
    exercice: str = Query(description="L'exercice dans lequel reprendre."),
    profil: str = Query("pivot-csv", description="Le code d'un profil du référentiel."),
    journal: str | None = Query(
        None, description="Le journal, quand le fichier n'en porte pas."
    ),
    appliquer: bool = Query(
        False,
        description=(
            "Faux, le défaut : le rapport dit ce qui entrerait, rien n'est écrit."
        ),
    ),
) -> RapportDeReprise:
    """Relit le fichier avec le profil déclaré, et rend ce qui entrerait.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LE MODE CONTRÔLE EST LE DÉFAUT.**

    Reprendre un exercice entier est irréversible, et pas seulement en pratique :
    les écritures entrent en brouillon, et **aucune route ne supprime un brouillon**.
    Ce texte disait « donc elles se suppriment » ; c'était faux (pas 72). Un brouillon
    se corrige à son numéro, se valide, se contre-passe : quatre mille brouillons
    d'un mauvais fichier n'ont pas d'autre issue, et la question est ouverte (Q23).
    Se tromper de fichier, de dossier ou d'exercice doit se voir **avant**.

    ⚠️ **LA PERMISSION EST CELLE DE LA SAISIE, PAS CELLE DE LA LECTURE.**

    Une reprise produit des écritures. Qu'elles viennent d'un fichier plutôt que
    d'un formulaire ne change rien à ce qu'elles sont, et le mode contrôle exige
    le même droit que le mode application : le rapport de contrôle **montre le
    contenu comptable du fichier**, ce qui n'est pas une lecture publique.

    TROIS STATUTS, TROIS SENS DIFFÉRENTS

    `404` le profil n'existe pas au référentiel, donc la demande désigne un format
    inconnu du centre.

    `409` le fichier ne se lit pas **du tout** avec ce profil : encodage, charabia,
    profil incapable de relire. Ce n'est pas un défaut de la requête, c'est un
    désaccord entre le fichier et le format annoncé.

    `200` avec des anomalies : le fichier se lit, et son contenu est refusé ligne
    par ligne. C'est un résultat, pas une erreur, et il faut qu'un écran puisse
    l'afficher en entier plutôt qu'un message d'échec.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger_dossier(acces, Permission.SAISIR_ECRITURE, entreprise)

    try:
        choisi = _profils()[profil]
    except KeyError as absent:
        raise HTTPException(
            status_code=404,
            detail=(
                f"aucun profil d'échange « {profil} ». Les profils du référentiel "
                f"sont : {', '.join(sorted(_profils())) or 'aucun'}."
            ),
        ) from absent

    octets = await fichier.read()
    if len(octets) > TAILLE_MAXIMALE_DE_REPRISE:
        raise HTTPException(
            status_code=413,
            detail=(
                f"fichier de {len(octets)} octets, au-delà des "
                f"{TAILLE_MAXIMALE_DE_REPRISE} admis. Un exercice de PME tient "
                "largement dedans : vérifier qu'il s'agit bien d'un export "
                "d'écritures et non d'une archive."
            ),
        )

    # ⚠️ TOUT CE QUI SUIT EST BLOQUANT, ET CETTE ROUTE EST ASYNCHRONE.
    #
    # Elle l'est pour une seule raison : `await fichier.read()`. Le reste — relire
    # huit mégaoctets d'écritures, les confronter au plan, les écrire — ne rend
    # jamais la main. Tenu sur la boucle d'événements, il figeait le serveur entier
    # pendant toute la reprise. Voir `tests/test_boucle_non_bloquee.py`.
    return await run_in_threadpool(
        _reprendre_le_lot,
        octets,
        choisi,
        entreprise=entreprise,
        exercice=exercice,
        journal=journal,
        appliquer=appliquer,
        par=acces.compte,
    )


def _reprendre_le_lot(
    octets: bytes,
    choisi,  # noqa: ANN001 — le profil du référentiel, typé par `_profils()`
    *,
    entreprise: str,
    exercice: str,
    journal: str | None,
    appliquer: bool,
    par: str,
) -> RapportDeReprise:
    """La reprise elle-même. **Bloquante, donc jamais sur la boucle.**"""
    plan = depot_du_plan.charger()
    try:
        lot = lire(
            octets,
            choisi,
            exercice=exercice,
            journal_par_defaut=journal,
            # ⚠️ Le plan du dossier est passé au lecteur pour que le compte
            # inconnu sorte **avec son rang de ligne**. Le contrôle d'environnement
            # le refuserait aussi, mais en nommant l'écriture, pas la ligne du
            # fichier : l'exploitant chercherait dans le mauvais outil.
            comptes_connus=frozenset(c.numero for c in plan),
        )
    except EchangeRefuse as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus

    return reprendre_un_lot(
        lot,
        journaux=depot_des_journaux.charger(),
        plan=plan,
        exercice=exercice_du_portefeuille(entreprise, exercice),
        periodes_verrouillees=periodes_verrouillees_du_dossier(entreprise),
        depot=depot(entreprise),
        par=par,
        appliquer=appliquer,
    )


