"""La DSF d'un exercice : préparer, prévisualiser, valider, constater le dépôt.

─────────────────────────────────────────────────────────────────────────────────
* `GET  /cloture/dossiers/{e}/dsf/{exercice}` : les versions, la plus récente en
  dernier (LIRE_COMPTABILITE) ;
* `POST .../preparation` : fige la liasse en une nouvelle version (PREPARER_DSF) ;
* `GET  .../{version}/document` : l'aperçu PDF, rendu de la version figée ;
* `POST .../{version}/validation` : le second regard (REVISER_DOSSIER, un autre
  que le préparateur) ;
* `POST .../{version}/depot` : l'accusé de la DGI, confronté à l'empreinte
  (DEPOSER_DECLARATION, session renforcée par le second facteur).

Voir `domaine/declaration_dsf.py` et `application/dsf.py`.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.cloture.adaptateurs.entrant.routes_http import (
    _depot_dossiers,
    assembler_la_liasse,
)
from app.contextes.cloture.adaptateurs.sortant.depots_dsf import DepotDSFSql, dsf_en_memoire
from app.contextes.cloture.adaptateurs.sortant.document_dsf import rendre_la_dsf
from app.contextes.cloture.application.dsf import (
    DSFNonPreparable,
    constater_le_depot_de_la_dsf,
    preparer_la_dsf,
    valider_la_dsf,
)
from app.contextes.cloture.domaine.declaration_dsf import (
    DeclarationDSF,
    EtatDSF,
    TransitionDSFRefusee,
)
from app.contextes.portefeuille.api import EntrepriseIntrouvable
from app.contextes.transverse.api import (
    AccesRequis,
    AccuseIncoherent,
    Permission,
    atelier,
    exiger_dossier,
    reference_de_depot,
    session_de_travail,
)
from app.partage.horloge import maintenant
from app.partage.locataire import courant

routeur = APIRouter(prefix="/cloture", tags=["Clôture et DSF"])


def _noms() -> dict[str, str]:
    """Identifiant de compte → nom affiché. Un compte disparu garde son identifiant."""
    return {c.identifiant: c.nom_affiche for c in atelier().comptes.lister()}


def _depot():
    session = session_de_travail()
    return dsf_en_memoire() if session is None else DepotDSFSql(session, courant())


class VersionDSF(BaseModel):
    """Une version, sans son contenu : l'écran lit l'aperçu par le PDF."""

    identifiant: str
    version: int
    etat: EtatDSF
    systeme: str
    resultat_comptable: str
    resultat_fiscal: str
    empreinte: str
    preparee_par: str
    #: Le nom de la personne : l'identifiant du compte reste la trace, le nom se lit.
    preparee_par_nom: str
    preparee_le: datetime
    validee_par: str | None
    validee_par_nom: str | None
    validee_le: datetime | None
    numero_accuse: str | None
    deposee_le: datetime | None
    deposee_par: str | None
    deposee_par_nom: str | None
    piece_accuse: str | None

    @classmethod
    def de(cls, d: DeclarationDSF) -> VersionDSF:
        noms = _noms()
        return cls(
            preparee_par_nom=noms.get(d.preparee_par, d.preparee_par),
            validee_par_nom=noms.get(d.validee_par, d.validee_par) if d.validee_par else None,
            deposee_par_nom=noms.get(d.deposee_par, d.deposee_par) if d.deposee_par else None,
            identifiant=d.identifiant,
            version=d.version,
            etat=d.etat,
            systeme=d.systeme,
            resultat_comptable=str(d.resultat_comptable),
            resultat_fiscal=str(d.resultat_fiscal),
            empreinte=d.empreinte,
            preparee_par=d.preparee_par,
            preparee_le=d.preparee_le,
            validee_par=d.validee_par,
            validee_le=d.validee_le,
            numero_accuse=d.numero_accuse,
            deposee_le=d.deposee_le,
            deposee_par=d.deposee_par,
            piece_accuse=d.piece_accuse,
        )


class DSFDeLExercice(BaseModel):
    entreprise: str
    exercice: str
    versions: list[VersionDSF]


def _versions(entreprise: str, exercice: str) -> list[DeclarationDSF]:
    return _depot().de_l_exercice(entreprise, exercice)


def _version(entreprise: str, exercice: str, version: int) -> DeclarationDSF:
    trouvee = next((d for d in _versions(entreprise, exercice) if d.version == version), None)
    if trouvee is None:
        raise HTTPException(
            status_code=404, detail=f"aucune version {version} de la DSF {exercice}."
        )
    return trouvee


@routeur.get("/dossiers/{entreprise}/dsf/{exercice}", summary="Les versions de la DSF")
def lire_les_versions(acces: AccesRequis, entreprise: str, exercice: str) -> DSFDeLExercice:
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    return DSFDeLExercice(
        entreprise=entreprise,
        exercice=exercice,
        versions=[VersionDSF.de(d) for d in _versions(entreprise, exercice)],
    )


@routeur.post(
    "/dossiers/{entreprise}/dsf/{exercice}/preparation",
    status_code=201,
    summary="Figer la liasse de l'exercice clos en une nouvelle version de la DSF",
    responses={409: {"description": "Exercice ouvert, liasse incohérente, ou DSF déjà déposée"}},
)
def preparer(acces: AccesRequis, entreprise: str, exercice: str) -> VersionDSF:
    exiger_dossier(acces, Permission.PREPARER_DSF, entreprise)
    liasse = assembler_la_liasse(entreprise, exercice)
    try:
        dossier = _depot_dossiers().lire(entreprise)
    except EntrepriseIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence
    (periode,) = [e for e in dossier.exercices if e.libelle == exercice]
    depot = _depot()
    try:
        dsf, remplacees = preparer_la_dsf(
            liasse.model_dump(mode="json"),
            exercice=periode,
            existantes=depot.de_l_exercice(entreprise, exercice),
            accuse_existant=atelier().portail.retrouver(
                reference_de_depot(entreprise, "DSF", periode.ouverture, periode.cloture)
            ),
            par=acces.compte,
            a_l_instant=maintenant(),
        )
    except DSFNonPreparable as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    for ancienne in remplacees:
        depot.enregistrer(ancienne)
    depot.enregistrer(dsf)
    return VersionDSF.de(dsf)


@routeur.get(
    "/dossiers/{entreprise}/dsf/{exercice}/{version}/document",
    summary="L'aperçu PDF de la version, rendu de son contenu figé",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def apercu(acces: AccesRequis, entreprise: str, exercice: str, version: int) -> Response:
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    dsf = _version(entreprise, exercice, version)
    return Response(
        content=rendre_la_dsf(dsf, noms=_noms()),
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'inline; filename="DSF-{entreprise}-{exercice}-v{version}.pdf"'
            )
        },
    )


@routeur.post(
    "/dossiers/{entreprise}/dsf/{exercice}/{version}/validation",
    summary="Valider la version : le second regard, par un autre que le préparateur",
    responses={409: {"description": "Pas préparée, préparée par vous, ou valeur non validée"}},
)
def valider(acces: AccesRequis, entreprise: str, exercice: str, version: int) -> VersionDSF:
    exiger_dossier(acces, Permission.REVISER_DOSSIER, entreprise)
    dsf = _version(entreprise, exercice, version)
    try:
        validee = valider_la_dsf(dsf, par=acces.compte, a_l_instant=maintenant())
    except (DSFNonPreparable, TransitionDSFRefusee) as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    _depot().enregistrer(validee)
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action="cloture.dsf_validee",
        objet_type="dsf",
        objet_id=validee.identifiant,
        apres={"dossier": entreprise, "exercice": exercice, "version": version},
    )
    return VersionDSF.de(validee)


class AccuseDeLaDGI(BaseModel):
    """Ce que le réviseur rapporte du portail. Le guichet et le déposant ne se reçoivent pas."""

    model_config = ConfigDict(extra="forbid")

    numero: str = Field(min_length=1, max_length=120, description="Le numéro de l'accusé DGI")
    depose_le: datetime = Field(description="La date de l'accusé, pas celle de la saisie")
    #: La pièce du dossier qui porte l'accusé (le PDF rendu par le portail).
    piece: str | None = None


@routeur.post(
    "/dossiers/{entreprise}/dsf/{exercice}/{version}/depot",
    summary="Constater le dépôt : l'accusé de la DGI sur la version validée",
    responses={
        403: {"description": "Second facteur requis, ou permission manquante"},
        404: {"description": "Version ou pièce introuvable"},
        409: {"description": "Version non validée, date impossible, ou accusé incohérent"},
    },
)
def deposer(
    acces: AccesRequis, entreprise: str, exercice: str, version: int, accuse: AccuseDeLaDGI
) -> VersionDSF:
    exiger_dossier(acces, Permission.DEPOSER_DECLARATION, entreprise)
    dsf = _version(entreprise, exercice, version)
    if accuse.piece is not None:
        # ⚠️ Une pièce DE CE dossier, comme la quittance d'un paiement sous mandat.
        from app.contextes.collecte.api import DepotPiecesSql, pieces_en_memoire

        session = session_de_travail()
        pieces = pieces_en_memoire() if session is None else DepotPiecesSql(session, courant())
        piece = pieces.par_identifiant(accuse.piece)
        if piece is None or piece.entreprise != entreprise:
            raise HTTPException(status_code=404, detail=f"pièce {accuse.piece} introuvable.")
    try:
        deposee, _ = constater_le_depot_de_la_dsf(
            dsf,
            numero=accuse.numero.strip(),
            depose_le=accuse.depose_le,
            piece=accuse.piece,
            par=acces.compte,
            portail=atelier().portail,
            journal=atelier().journal,
            a_l_instant=maintenant(),
        )
    except (DSFNonPreparable, TransitionDSFRefusee, AccuseIncoherent) as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    _depot().enregistrer(deposee)
    return VersionDSF.de(deposee)


# Le statut « déposée » se lit aussi à l'échéancier : l'accusé consigné au registre du
# guichet fait passer l'obligation DSF à déclarée, sans arête vers les Obligations.
_ = EtatDSF
