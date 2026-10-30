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
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.cloture.adaptateurs.entrant.routes_http import (
    assembler_la_liasse,
    depot_dossiers,
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
        dossier = depot_dossiers().lire(entreprise)
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


# ── La DSF au format DGI : le modèle officiel rempli (15 octobre 2026) ───────────


class FormatDgi(BaseModel):
    """Ce que l'écran montre avant de proposer le fichier."""

    pret: bool
    obstacles: list[str]
    avertissements: list[str]
    nom_du_fichier: str
    total_bilan: Decimal
    resultat: Decimal
    avec_exercice_precedent: bool


def _format_dgi(entreprise: str, exercice: str):
    from app.contextes.cloture.adaptateurs.entrant.routes_http import depot_ecritures
    from app.contextes.cloture.application.format_dgi import etat_du_format_dgi
    from app.contextes.comptabilite.api import balance

    try:
        dossier = depot_dossiers().lire(entreprise)
    except EntrepriseIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence
    periode = next((e for e in dossier.exercices if e.libelle == exercice), None)
    if periode is None:
        raise HTTPException(status_code=404, detail=f"l'exercice {exercice} n'existe pas.")
    precedent = next((e for e in dossier.exercices if e.cloture < periode.ouverture), None)
    for e in dossier.exercices:
        if e.cloture < periode.ouverture and (precedent is None or e.cloture > precedent.cloture):
            precedent = e

    def soldes(libelle: str) -> dict[str, Decimal]:
        return {s.compte: s.solde for s in balance(depot_ecritures(entreprise).toutes(libelle))}

    etat = etat_du_format_dgi(
        soldes(exercice),
        soldes_precedents=soldes(precedent.libelle) if precedent else None,
        exercice_clos=periode.clos,
        # Le système suit le régime à la clôture (voir `determiner_le_systeme`).
        systeme_normal=dossier.regime_au(periode.cloture) == "REEL",
    )
    return dossier, periode, etat


def _avertissements_de_la_fiche(niu: str) -> list[str]:
    """15 octobre 2026 : la fiche signalétique (R1 à R3) que le client renseigne."""
    dossier = depot_dossiers().lire(niu)
    fiche = dossier.fiche_dsf
    if fiche is None or fiche.etat != "VALIDEE":
        etat = "non renseignée" if fiche is None else f"à l'état {fiche.etat.value.lower()}"
        return [
            f"Fiche signalétique de la DSF {etat} : les fiches R1 à R3 ne reprennent que "
            "l'identité et les dirigeants du dossier, le reste sera à compléter sur le portail. "
            "Le client la renseigne dans son espace, le cabinet la valide."
        ]
    return []


def _identite_dsf(dossier, periode):
    """Ce que les fiches R1 à R3 reprennent. ⚠️ La fiche signalétique n'y entre que
    VALIDÉE : la DSF ne s'établit que sur ce que le cabinet a relu."""
    from app.contextes.cloture.adaptateurs.sortant.dsf_fiches_r import (
        ActiviteR2,
        IdentiteDsf,
        PersonneR3,
    )

    precedent = max(
        (e for e in dossier.exercices if e.cloture < periode.ouverture),
        key=lambda e: e.cloture,
        default=None,
    )
    en_fonction = [
        d for d in dossier.dirigeants if d.jusqu_a is None or d.jusqu_a >= periode.cloture
    ]
    identite = {
        "denomination": dossier.denomination,
        "niu": dossier.niu,
        "forme_juridique": str(dossier.forme_juridique),
        "ouverture": periode.ouverture,
        "cloture": periode.cloture,
        "rccm": dossier.rccm,
        "siege": dossier.siege,
        "dirigeants": tuple(
            PersonneR3(nom=d.nom, qualite=d.qualite, niu=d.niu) for d in en_fonction
        ),
    }
    if precedent is not None:
        identite["cloture_precedente"] = precedent.cloture
        identite["duree_precedente"] = (
            (precedent.cloture.year - precedent.ouverture.year) * 12
            + precedent.cloture.month
            - precedent.ouverture.month
            + 1
        )
    fiche = dossier.fiche_dsf
    if fiche is not None and fiche.etat == "VALIDEE":
        saisie = fiche.model_dump(exclude={"activites", "conseil_d_administration", "controle"})
        identite |= {k: v for k, v in saisie.items() if k in IdentiteDsf.__dataclass_fields__}
        identite["controle"] = fiche.controle.value if fiche.controle else None
        identite["activites"] = tuple(
            ActiviteR2(a.designation, a.code_nacam, a.chiffre_affaires_ht) for a in fiche.activites
        )
        identite["conseil_d_administration"] = tuple(
            PersonneR3(nom=m.nom, prenoms=m.prenoms, qualite=m.qualite, adresse=m.adresse)
            for m in fiche.conseil_d_administration
        )
    return IdentiteDsf(**identite)


@routeur.get(
    "/dossiers/{entreprise}/dsf/{exercice}/format-dgi",
    summary="La DSF au format DGI : prête ou non, et pourquoi",
)
def etat_du_format_dgi_de_l_exercice(
    acces: AccesRequis, entreprise: str, exercice: str
) -> FormatDgi:
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    from app.contextes.cloture.adaptateurs.sortant.dsf_format_dgi import NOM_DU_MODELE

    _, _, etat = _format_dgi(entreprise, exercice)
    return FormatDgi(
        pret=etat.pret,
        obstacles=list(etat.obstacles),
        avertissements=[*etat.etats.avertissements, *_avertissements_de_la_fiche(entreprise)],
        nom_du_fichier=NOM_DU_MODELE,
        total_bilan=etat.etats.passif["DZ"],
        resultat=etat.etats.resultat["XI"],
        avec_exercice_precedent=etat.precedents is not None,
    )


@routeur.get(
    "/dossiers/{entreprise}/dsf/{exercice}/format-dgi/fichier",
    summary="Le modèle Excel officiel de la DGI, bilan et compte de résultat remplis",
    response_class=Response,
    responses={
        200: {"content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}}},
        409: {"description": "Exercice ouvert, compte sans ligne officielle, bilan déséquilibré"},
    },
)
def fichier_au_format_dgi(acces: AccesRequis, entreprise: str, exercice: str) -> Response:
    """⚠️ Le fichier garde le NOM du modèle : le portail refuse un fichier renommé."""
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    from app.contextes.cloture.adaptateurs.sortant.dsf_fiches_r import valeurs_des_fiches_r
    from app.contextes.cloture.adaptateurs.sortant.dsf_format_dgi import (
        NOM_DU_MODELE,
        remplir_la_dsf_normal,
    )

    dossier, periode, etat = _format_dgi(entreprise, exercice)
    if not etat.pret:
        raise HTTPException(status_code=409, detail=" ".join(etat.obstacles))
    octets = remplir_la_dsf_normal(
        etat.etats,
        precedents=etat.precedents,
        denomination=dossier.denomination,
        niu=entreprise,
        cloture=periode.cloture,
        fiches=valeurs_des_fiches_r(_identite_dsf(dossier, periode)),
    )
    return Response(
        content=octets,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{NOM_DU_MODELE}"'},
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


# ── La relecture par l'agent (18 octobre 2026, point 24) ──────────────────────────


class RemarqueLue(BaseModel):
    sujet: str
    constat: str
    suggestion: str


class RelectureDeLAgent(BaseModel):
    #: Faux sans clé, ou quand l'agent n'a pas répondu : la DSF se prépare sans lui.
    disponible: bool
    remarques: list[RemarqueLue]
    message: str


def service_de_relecture():
    """L'agent de relecture, ou `None` sans clé."""
    from app.infrastructure.config import configuration, moteur_ia

    reglages = configuration()
    moteur = moteur_ia(reglages)
    if moteur is None:
        return None
    from app.contextes.cloture.adaptateurs.sortant.relecture_dsf_claude import RelectureDeLaDsf

    return RelectureDeLaDsf(
        cle_api=moteur.cle_api, modele=moteur.modele, fournisseur=moteur.fournisseur
    )


def _dossier_pour_l_agent(entreprise: str, exercice: str) -> str:
    """Ce que l'agent lit : sans NIU, sans dénomination, sans nom (voir son en-tête)."""
    from app.contextes.cloture.adaptateurs.entrant.routes_pieces_dsf import (
        lignes_des_pieces_dsf,
    )

    dossier, periode, etat = _format_dgi(entreprise, exercice)
    fiche = dossier.fiche_dsf
    lignes = [
        f"Forme juridique : {dossier.forme_juridique}",
        f"Activité déclarée au dossier : {dossier.activite or 'non renseignée'}",
        f"Exercice : du {periode.ouverture:%d/%m/%Y} au {periode.cloture:%d/%m/%Y}",
        f"Système : {'normal' if dossier.regime_au(periode.cloture) == 'REEL' else 'SMT'}",
        f"Total du bilan : {etat.etats.passif['DZ']} FCFA ; résultat : "
        f"{etat.etats.resultat['XI']} FCFA",
    ]
    if fiche is None:
        lignes.append("Fiche signalétique : non renseignée.")
    else:
        manquantes = [
            nom
            for nom in (
                "telephone",
                "ville",
                "adresse_geographique",
                "numero_cnps_employeur",
                "signataire_qualite",
                "etats_approuves_en_ag",
                "banque",
                "controle",
            )
            if getattr(fiche, nom) is None
        ]
        lignes.append(f"Fiche signalétique : {fiche.etat.value.lower()}.")
        lignes.append(f"Rubriques non renseignées : {', '.join(manquantes) or 'aucune'}")
        for a in fiche.activites:
            ca = (
                "CA non renseigné"
                if a.chiffre_affaires_ht is None
                else f"CA HT {a.chiffre_affaires_ht}"
            )
            lignes.append(f"Activité : {a.designation} ; code NACAM {a.code_nacam} ; {ca}")
        if not fiche.activites:
            lignes.append("Aucune activité déclarée avec son code NACAM.")
    lignes.append("Pièces à joindre :")
    lignes += [
        f"- {x.piece.libelle} : {x.etat.value}" for x in lignes_des_pieces_dsf(entreprise, exercice)
    ]
    if etat.obstacles:
        lignes.append("Obstacles au format DGI : " + " ; ".join(etat.obstacles))
    if etat.etats.avertissements:
        lignes.append("Avertissements de la liasse : " + " ; ".join(etat.etats.avertissements))
    return "\n".join(lignes)


@routeur.post(
    "/dossiers/{entreprise}/dsf/{exercice}/relecture-de-l-agent",
    summary="Demander à l'agent une relecture du dossier de la DSF (il propose, ne bloque rien)",
)
def demander_une_relecture(acces: AccesRequis, entreprise: str, exercice: str) -> RelectureDeLAgent:
    exiger_dossier(acces, Permission.PREPARER_DSF, entreprise)
    service = service_de_relecture()
    if service is None:
        return RelectureDeLAgent(
            disponible=False,
            remarques=[],
            message="L'agent n'est pas branché sur ce poste : la DSF se prépare sans lui.",
        )
    remarques = service.relire(_dossier_pour_l_agent(entreprise, exercice))
    if remarques is None:
        return RelectureDeLAgent(
            disponible=False,
            remarques=[],
            message="L'agent n'a pas répondu. Réessayez plus tard ; rien n'attend sa réponse.",
        )
    return RelectureDeLAgent(
        disponible=True,
        remarques=[
            RemarqueLue(sujet=r.sujet, constat=r.constat, suggestion=r.suggestion)
            for r in remarques
        ],
        message=(
            "Des propositions à juger : l'agent ne lit que le dossier, pas les pièces."
            if remarques
            else "L'agent n'a rien relevé. Cela ne vaut pas validation."
        ),
    )
