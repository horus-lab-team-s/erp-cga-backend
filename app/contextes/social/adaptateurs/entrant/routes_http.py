"""API du contexte G · Social et paie.

Le fichier du personnel, les bulletins, et la déclaration mensuelle.

Permissions. La paie relève de `LIRE_COMPTABILITE` en lecture — elle porte des
rémunérations nominatives, et le rôle qui lit les comptes d'un dossier est celui
qui les lit — et de `SAISIR_ECRITURE` en écriture, parce qu'embaucher engage la
charge de personnel comme une écriture engage un compte.

⚠️ **Aucune route ne rend un bulletin stocké** : chacune le calcule. La date de
lecture du référentiel est le dernier jour de la période demandée, jamais le jour
de l'appel. Une paie de mars refaite en décembre emploie les taux de mars.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.referentiel.api import (
    DepotBaremesYaml,
    ServiceBaremes,
    ServiceParametres,
    service_parametres,
)
from app.contextes.social.api import (
    AvantageNature,
    Bulletin,
    Contrat,
    ContratEnChevauchement,
    ContratIntrouvable,
    DepotContrats,
    DepotSalaries,
    GroupeRisque,
    MatriculeDejaAttribue,
    ParametrePaieAbsent,
    Periode,
    Salarie,
    SalarieIntrouvable,
    TypeContrat,
    calculer_bulletin,
    clore_un_contrat,
    depots_du_social,
    etablir_la_declaration,
    inscrire_un_salarie,
    ouvrir_un_contrat,
)
from app.contextes.transverse.api import (
    AccesRequis,
    Permission,
    atelier,
    exiger_dossier_pour_l_un_de,
)
from app.infrastructure.config import configuration
from app.partage.erreurs import message_lisible
from app.partage.horloge import aujourd_hui, maintenant

routeur = APIRouter(prefix="/social", tags=["Social et paie"])


def depots() -> tuple[DepotSalaries, DepotContrats]:
    """Les dépôts en vigueur. Le choix appartient au contexte : voir `magasins.py`."""
    return depots_du_social()


def parametres() -> ServiceParametres:
    # ⚠️ Pas 95 : le référentiel **du cabinet**, par le point de montage unique, et non plus
    # le fichier commun mémoïsé ici. Voir `service_parametres` dans `referentiel/api.py`.
    return service_parametres()


@lru_cache
def baremes() -> ServiceBaremes:
    return ServiceBaremes.depuis_depot(
        DepotBaremesYaml(configuration().dossier_referentiel / "baremes.yaml")
    )


# ── Ce que les écrans lisent ─────────────────────────────────────────────────
class LigneSalarie(BaseModel):
    """Un salarié et son contrat en vigueur, résolus à une date."""

    matricule: str
    nom: str
    prenom: str
    matricule_cnps: str | None
    poste: str | None
    type_contrat: TypeContrat | None
    depuis: date | None
    salaire_base: Decimal | None
    #: Vrai si aucun contrat ne couvre la date demandée — sorti, ou pas encore entré.
    sans_contrat: bool


class SyntheseDeclaration(BaseModel):
    """Le DIPE d'un mois, tel que l'écran l'affiche."""

    entreprise: str
    periode: str
    effectif: int
    masse_salariale_brute: Decimal
    retenues_salariales: Decimal
    charges_patronales: Decimal
    total_a_verser: Decimal
    cotisations_cnps: Decimal
    a_deposer_avant: date
    en_retard: bool
    repose_sur_des_valeurs_non_validees: bool
    mouvements: list[dict]
    #: Le groupe de risque appliqué, et s'il est supposé faute d'être au dossier.
    groupe_risque: GroupeRisque = GroupeRisque.A
    groupe_risque_suppose: bool = False
    bulletins: list[Bulletin]


class DemandeSalarie(BaseModel):
    matricule: str = Field(min_length=1)
    nom: str = Field(min_length=1)
    prenom: str = Field(min_length=1)
    matricule_cnps: str | None = None
    date_naissance: date | None = None
    enfants_a_charge: int = Field(default=0, ge=0)


class DemandeContrat(BaseModel):
    type_contrat: TypeContrat
    debut: date
    fin: date | None = None
    salaire_base: Decimal = Field(ge=0)
    primes: Decimal = Field(default=Decimal(0), ge=0)
    avantages: list[AvantageNature] = Field(default_factory=list)
    poste: str | None = None


def _periode(annee: int, mois: int) -> Periode:
    try:
        return Periode(annee=annee, mois=mois)
    except ValueError as invalide:
        raise HTTPException(status_code=422, detail=message_lisible(invalide)) from invalide


# ── Lectures ─────────────────────────────────────────────────────────────────

#: Qui peut LIRE la paie d'un dossier.
#:
#: ─────────────────────────────────────────────────────────────────────────────
#: DEUX RÔLES, AUCUNE PERMISSION EN COMMUN, ET C'EST VOULU
#:
#: Le collaborateur tient la paie de ses dossiers par `LIRE_COMPTABILITE`, qui
#: lui ouvre aussi la balance et le journal dont il a besoin pour la contrôler.
#:
#: L'adhérent tient la sienne par `TENIR_SA_PAIE`, et rien d'autre : un bulletin
#: porte des rémunérations nominatives, et lui ouvrir la comptabilité du dossier
#: pour qu'il voie sa paie lui ouvrirait au passage tous les livres.
#:
#: ⚠️ Le périmètre reste entier dans les deux cas. `exiger_dossier_pour_l_un_de`
#: applique le même contrôle de dossier quelle que soit la permission qui a
#: ouvert la porte : une paie demandée sur un autre NIU rend 404.
#: ─────────────────────────────────────────────────────────────────────────────
LECTURE_DE_LA_PAIE = (Permission.LIRE_COMPTABILITE, Permission.TENIR_SA_PAIE)

#: Qui peut TENIR la paie : inscrire un salarié, ouvrir et clore un contrat.
#:
#: ⚠️ `SAISIR_ECRITURE` côté cabinet et non `LIRE_COMPTABILITE` : inscrire un
#: salarié engage le dossier, et un chargé de clientèle qui lit les comptes n'a
#: pas à embaucher. L'adhérent, lui, saisit son propre personnel : c'est tout
#: l'objet du libre service.
TENUE_DE_LA_PAIE = (Permission.SAISIR_ECRITURE, Permission.TENIR_SA_PAIE)

@routeur.get("/dossiers/{entreprise}/salaries", summary="Le fichier du personnel")
def lister_salaries(
    acces: AccesRequis,
    entreprise: str,
    a_la_date: date = Query(..., description="Résout le contrat en vigueur à cette date"),
) -> list[LigneSalarie]:
    """Le personnel du dossier, avec le contrat en vigueur à la date demandée.

    ⚠️ Rend **tous** les salariés, y compris ceux sans contrat en cours. Le
    fichier du personnel ne se purge pas : la CNPS peut réclamer un état
    nominatif des années après un départ. Ceux qui sont sortis portent
    `sans_contrat` — l'écran les grise, il ne les efface pas.
    """
    exiger_dossier_pour_l_un_de(acces, LECTURE_DE_LA_PAIE, entreprise)
    depot_salaries, depot_contrats = depots()
    en_cours = {
        c.salarie: c for c in depot_contrats.du_dossier(entreprise, a_la_date=a_la_date)
    }
    lignes = []
    for salarie in depot_salaries.du_dossier(entreprise):
        contrat = en_cours.get(salarie.matricule)
        lignes.append(
            LigneSalarie(
                matricule=salarie.matricule,
                nom=salarie.nom,
                prenom=salarie.prenom,
                matricule_cnps=salarie.matricule_cnps,
                poste=contrat.poste if contrat else None,
                type_contrat=contrat.type_contrat if contrat else None,
                depuis=contrat.debut if contrat else None,
                salaire_base=contrat.salaire_base if contrat else None,
                sans_contrat=contrat is None,
            )
        )
    return lignes


@routeur.get(
    "/dossiers/{entreprise}/bulletins/{annee}/{mois}/{matricule}",
    summary="Le bulletin de paie d'un salarié, calculé",
)
def lire_bulletin(
    acces: AccesRequis,
    entreprise: str,
    annee: int,
    mois: int,
    matricule: str,
    groupe_risque: GroupeRisque | None = Query(
        None,
        description=(
            "Pour simuler un autre groupe. Absent : le groupe **notifié par la CNPS** et "
            "enregistré au dossier ; à défaut, A, et le bulletin le signale."
        ),
    ),
) -> Bulletin:
    exiger_dossier_pour_l_un_de(acces, LECTURE_DE_LA_PAIE, entreprise)
    bulletin, _ = _bulletin_du_mois(entreprise, annee, mois, matricule, groupe_risque)
    return bulletin


@routeur.get(
    "/dossiers/{entreprise}/bulletins/{annee}/{mois}/{matricule}/pdf",
    summary="Le bulletin de paie imprimable, du même calcul que l'écran",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def imprimer_le_bulletin(
    acces: AccesRequis,
    entreprise: str,
    annee: int,
    mois: int,
    matricule: str,
    groupe_risque: GroupeRisque | None = Query(None),
) -> Response:
    """15 octobre 2026 : le bulletin se remet au salarié. Même garde, même calcul."""
    exiger_dossier_pour_l_un_de(acces, LECTURE_DE_LA_PAIE, entreprise)
    bulletin, contrat = _bulletin_du_mois(entreprise, annee, mois, matricule, groupe_risque)
    depot_salaries, _ = depots()
    salarie = depot_salaries.lire(matricule)
    from app.contextes.portefeuille.api import EntrepriseIntrouvable
    from app.contextes.social.adaptateurs.sortant.document_bulletin import rendre_le_bulletin

    try:
        employeur = _dossiers_du_portefeuille().lire(entreprise).denomination
    except EntrepriseIntrouvable:
        employeur = entreprise
    octets = rendre_le_bulletin(
        bulletin,
        employeur=employeur,
        niu=entreprise,
        salarie=f"{salarie.prenom} {salarie.nom}",
        matricule_cnps=salarie.matricule_cnps,
        poste=contrat.poste,
    )
    return Response(
        content=octets,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'inline; filename="bulletin-{matricule}-{annee}-{mois:02d}.pdf"'
            )
        },
    )


def _bulletin_du_mois(
    entreprise: str, annee: int, mois: int, matricule: str, groupe_risque: GroupeRisque | None
):
    """Le bulletin du mois et le contrat qui le fonde. Extrait le 15 octobre 2026 pour que
    l'écran et le PDF portent le même calcul ; l'accès reste à l'appelant."""
    periode = _periode(annee, mois)
    _, depot_contrats = depots()
    contrats = [
        c
        for c in depot_contrats.du_dossier(entreprise)
        if c.salarie == matricule and c.couvre_la_periode(periode)
    ]
    if not contrats:
        raise HTTPException(
            status_code=404,
            detail=(
                f"aucun contrat de {matricule} ne couvre {periode.libelle} : "
                "il n'y a pas de bulletin à établir."
            ),
        )
    if len(contrats) > 1:
        # Deux contrats sur un même mois : un CDD qui se transforme en CDI en
        # cours de mois. Le cas est réel et le calcul n'est pas décidé — il
        # faudrait proratiser, et la règle de prorata n'est pas arrêtée.
        raise HTTPException(
            status_code=409,
            detail=(
                f"{matricule} a {len(contrats)} contrats couvrant {periode.libelle}. "
                "La paie d'un mois à cheval sur deux contrats suppose une règle de "
                "prorata que le cabinet n'a pas arrêtée : à traiter à la main."
            ),
        )
    groupe, suppose = _groupe_de_risque(entreprise, groupe_risque)
    bulletin = _calculer(contrats[0], entreprise, periode, groupe)
    if suppose:
        bulletin = bulletin.model_copy(update={"alertes": (*bulletin.alertes, _GROUPE_SUPPOSE)})
    return bulletin, contrats[0]


_GROUPE_SUPPOSE = (
    "Groupe de risque professionnel non renseigné au dossier : le groupe A (risque faible) "
    "est supposé. Enregistrez le groupe notifié par la CNPS : un groupe B ou C coûte "
    "davantage de cotisation accidents du travail."
)


def _dossiers_du_portefeuille():
    from app.contextes.portefeuille.api import depot_entreprises

    return depot_entreprises()


def _groupe_de_risque(
    entreprise: str, demande: GroupeRisque | None
) -> tuple[GroupeRisque, bool]:
    """Le groupe à appliquer, et s'il est supposé (16 octobre 2026).

    Celui demandé pour une simulation ; sinon celui que la CNPS a notifié, enregistré au
    dossier ; à défaut A, le plus bas, et l'appelant le signale. Supposer sans le dire
    sous-cotiserait un employeur à risque élevé sans que personne ne le voie.
    """
    if demande is not None:
        return demande, False
    from app.contextes.portefeuille.api import EntrepriseIntrouvable

    try:
        notifie = _dossiers_du_portefeuille().lire(entreprise).groupe_risque_cnps
    except EntrepriseIntrouvable:
        notifie = None
    if notifie is None:
        return GroupeRisque.A, True
    return GroupeRisque(notifie), False


@routeur.get(
    "/dossiers/{entreprise}/declaration/{annee}/{mois}",
    summary="La déclaration mensuelle des personnels employés (DIPE)",
)
def lire_declaration(
    acces: AccesRequis,
    entreprise: str,
    annee: int,
    mois: int,
    a_la_date: date = Query(..., description="Date à laquelle juger le retard de dépôt"),
    groupe_risque: GroupeRisque | None = Query(None),
) -> SyntheseDeclaration:
    """Le DIPE du mois, calculé depuis les contrats en vigueur.

    Rend une déclaration **à néant** si le dossier n'a plus de salarié, plutôt
    qu'une erreur : la CNPS attend une déclaration à zéro, pas une absence de
    déclaration.
    """
    exiger_dossier_pour_l_un_de(acces, LECTURE_DE_LA_PAIE, entreprise)
    periode = _periode(annee, mois)
    _, depot_contrats = depots()
    groupe, suppose = _groupe_de_risque(entreprise, groupe_risque)
    try:
        declaration = etablir_la_declaration(
            entreprise,
            periode,
            depot_contrats.du_dossier(entreprise),
            parametres(),
            baremes(),
            groupe_risque=groupe,
        )
    except ParametrePaieAbsent as absence:
        raise HTTPException(status_code=503, detail=str(absence)) from absence

    return SyntheseDeclaration(
        entreprise=entreprise,
        periode=periode.libelle,
        effectif=declaration.effectif,
        masse_salariale_brute=declaration.masse_salariale_brute,
        retenues_salariales=declaration.retenues_salariales,
        charges_patronales=declaration.charges_patronales,
        total_a_verser=declaration.total_a_verser,
        cotisations_cnps=declaration.cotisations_cnps,
        a_deposer_avant=declaration.a_deposer_avant,
        en_retard=declaration.en_retard_au(a_la_date)
        and not _avant_le_suivi(entreprise, declaration.a_deposer_avant),
        repose_sur_des_valeurs_non_validees=(
            declaration.repose_sur_des_valeurs_non_validees
        ),
        mouvements=[
            {"salarie": m.salarie, "sens": m.sens, "survenu_le": m.survenu_le.isoformat()}
            for m in declaration.mouvements
        ],
        bulletins=list(declaration.bulletins),
        groupe_risque=groupe,
        groupe_risque_suppose=suppose,
    )



# ── Le groupe de risque professionnel notifié par la CNPS (16 octobre 2026) ──────


class GroupeDeRisqueDuDossier(BaseModel):
    groupe: GroupeRisque | None
    notifie_le: date | None


class NotificationDuGroupe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groupe: GroupeRisque
    notifie_le: date


@routeur.get(
    "/dossiers/{entreprise}/groupe-risque",
    summary="Le groupe de risque professionnel notifié par la CNPS, s'il est enregistré",
)
def lire_le_groupe_de_risque(acces: AccesRequis, entreprise: str) -> GroupeDeRisqueDuDossier:
    exiger_dossier_pour_l_un_de(acces, LECTURE_DE_LA_PAIE, entreprise)
    dossier = _lire_le_dossier(entreprise)
    return GroupeDeRisqueDuDossier(
        groupe=dossier.groupe_risque_cnps, notifie_le=dossier.groupe_risque_notifie_le
    )


@routeur.put(
    "/dossiers/{entreprise}/groupe-risque",
    summary="Enregistrer le groupe notifié par la CNPS : chaque bulletin et le DIPE le lisent",
)
def enregistrer_le_groupe_de_risque(
    acces: AccesRequis, entreprise: str, notification: NotificationDuGroupe
) -> GroupeDeRisqueDuDossier:
    """Le cabinet, ou l'employeur dans son espace : c'est lui qui reçoit la notification."""
    exiger_dossier_pour_l_un_de(acces, TENUE_DE_LA_PAIE, entreprise)
    if notification.notifie_le > aujourd_hui():
        raise HTTPException(
            status_code=422, detail="la date de notification ne peut pas être à venir."
        )
    dossier = _lire_le_dossier(entreprise)
    _dossiers_du_portefeuille().enregistrer(
        dossier.model_copy(
            update={
                "groupe_risque_cnps": notification.groupe.value,
                "groupe_risque_notifie_le": notification.notifie_le,
            }
        )
    )
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action="social.groupe_risque_notifie",
        objet_type="dossier",
        objet_id=entreprise,
        avant={"groupe": dossier.groupe_risque_cnps},
        apres={"groupe": notification.groupe.value, "notifie_le": str(notification.notifie_le)},
    )
    return GroupeDeRisqueDuDossier(groupe=notification.groupe, notifie_le=notification.notifie_le)


def _avant_le_suivi(entreprise: str, echeance: date) -> bool:
    """La déclaration était due avant que le cabinet ne prenne le dossier : pas son retard.
    Voir `Entreprise.suivi_depuis`."""
    from app.contextes.portefeuille.api import EntrepriseIntrouvable

    try:
        depuis = _dossiers_du_portefeuille().lire(entreprise).suivi_depuis
    except EntrepriseIntrouvable:
        return False
    return depuis is not None and echeance < depuis


def _lire_le_dossier(entreprise: str):
    from app.contextes.portefeuille.api import EntrepriseIntrouvable

    try:
        return _dossiers_du_portefeuille().lire(entreprise)
    except EntrepriseIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence

# ── Écritures ────────────────────────────────────────────────────────────────
@routeur.post("/dossiers/{entreprise}/salaries", summary="Inscrire un salarié", status_code=201)
def inscrire(acces: AccesRequis, entreprise: str, demande: DemandeSalarie) -> Salarie:
    """⚠️ Pas 89 : un matricule déjà attribué est refusé (409). Il écrasait le salarié,
    fût-il d'un autre dossier. Voir `application/personnel.py`."""
    exiger_dossier_pour_l_un_de(acces, TENUE_DE_LA_PAIE, entreprise)
    depot_salaries, depot_contrats = depots()
    salarie = Salarie(
        matricule=demande.matricule,
        nom=demande.nom,
        prenom=demande.prenom,
        entreprise=entreprise,
        matricule_cnps=demande.matricule_cnps,
        date_naissance=demande.date_naissance,
        enfants_a_charge=demande.enfants_a_charge,
    )
    try:
        return inscrire_un_salarie(salarie, salaries=depot_salaries, contrats=depot_contrats)
    except MatriculeDejaAttribue as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus


@routeur.post(
    "/dossiers/{entreprise}/salaries/{matricule}/contrats",
    summary="Ouvrir un contrat de travail",
    status_code=201,
)
def engager(
    acces: AccesRequis, entreprise: str, matricule: str, demande: DemandeContrat
) -> Contrat:
    """Ouvre un contrat.

    ⚠️ Un contrat en vigueur ne se **modifie** pas quand les conditions changent :
    on le ferme et on en ouvre un nouveau. Écraser le salaire effacerait
    l'histoire, et la paie de mars se recalculerait avec le salaire d'avril.
    """
    exiger_dossier_pour_l_un_de(acces, TENUE_DE_LA_PAIE, entreprise)
    depot_salaries, depot_contrats = depots()
    try:
        contrat = Contrat(
            salarie=matricule,
            type_contrat=demande.type_contrat,
            debut=demande.debut,
            fin=demande.fin,
            salaire_base=demande.salaire_base,
            primes=demande.primes,
            avantages=tuple(demande.avantages),
            poste=demande.poste,
        )
    except ValueError as invalide:
        # Pas 89 : une fin antérieure au début rendait 500.
        raise HTTPException(status_code=422, detail=message_lisible(invalide)) from invalide
    # ⚠️ Pas 89 : plus de `rattacher` ici. Il déplaçait vers ce dossier le salarié d'un
    # autre. Le salarié est rattaché une fois, à son inscription.
    try:
        return ouvrir_un_contrat(
            contrat, entreprise=entreprise, salaries=depot_salaries, contrats=depot_contrats
        )
    except SalarieIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence
    except ContratEnChevauchement as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus


class DemandeCloture(BaseModel):
    """La date de fin d'un contrat, exclue : le dernier jour travaillé est la veille."""

    model_config = ConfigDict(extra="forbid")

    le: date


@routeur.post(
    "/dossiers/{entreprise}/salaries/{matricule}/contrats/cloture",
    summary="Clore le contrat en vigueur",
    responses={404: {"description": "Salarié inconnu dans ce dossier, ou aucun contrat à clore"}},
)
def clore(
    acces: AccesRequis, entreprise: str, matricule: str, demande: DemandeCloture
) -> Contrat:
    """Le geste qu'annonçait « on le ferme et on en ouvre un nouveau » (pas 89).

    Sans lui, changer un salaire ou passer de CDD en CDI était impossible depuis que les
    contrats ne se chevauchent plus. La fin est exclue : clore le 01/10 laisse septembre
    au contrat clos, et le nouveau contrat commence le 01/10.
    """
    exiger_dossier_pour_l_un_de(acces, TENUE_DE_LA_PAIE, entreprise)
    depot_salaries, depot_contrats = depots()
    try:
        return clore_un_contrat(
            matricule,
            entreprise=entreprise,
            le=demande.le,
            salaries=depot_salaries,
            contrats=depot_contrats,
        )
    except (SalarieIntrouvable, ContratIntrouvable) as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence


def _calculer(
    contrat: Contrat, entreprise: str, periode: Periode, groupe: GroupeRisque
) -> Bulletin:
    """Calcule, ou refuse en 503 si le référentiel est incomplet.

    503 et non 500 : le service est temporairement hors d'état de répondre parce
    qu'une donnée de configuration manque, non parce que le code a échoué. La
    distinction compte pour l'exploitant, qui saura qu'il faut compléter un
    fichier et non déboguer.
    """
    try:
        return calculer_bulletin(
            contrat, entreprise, periode, parametres(), baremes(), groupe_risque=groupe
        )
    except ParametrePaieAbsent as absence:
        raise HTTPException(status_code=503, detail=str(absence)) from absence


__all__ = ["routeur"]
