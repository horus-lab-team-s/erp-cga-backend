"""API du contexte M · Souscription.

─────────────────────────────────────────────────────────────────────────────────
LA MOITIÉ DE CES ROUTES EST PUBLIQUE, ET C'EST NORMAL

Le catalogue, l'établissement d'un devis, sa lecture, l'engagement du paiement :
tout cela s'adresse à quelqu'un qui n'a **pas encore** de compte. Exiger une
session avant de vendre reviendrait à demander à un visiteur de s'inscrire pour
connaître un prix.

Ce qui protège ces routes n'est donc pas l'authentification, c'est la nature de
ce qu'elles font : elles ne lisent rien qui appartienne à quelqu'un d'autre, et
la référence d'un devis est un identifiant long et non devinable.

⚠️ Ce qui manque et qu'il faut nommer : **aucune limitation de débit**. Rien
n'empêche aujourd'hui d'établir dix mille devis, ni de tenter mille engagements.
La limitation appartient à la couche d'entrée — passerelle ou intergiciel —, elle
n'est pas écrite, et l'oublier au déploiement exposerait le prestataire de
paiement à un flot d'initiations.

LA NOTIFICATION DU PRESTATAIRE RÉPOND TOUJOURS 200

Y compris quand elle est illisible, quand le rapprochement échoue, ou quand
l'ouverture d'accès casse. Répondre une erreur ferait renvoyer la notification en
boucle jusqu'à épuisement des tentatives, sans que le vrai problème soit traité.
Voir l'en-tête de `application/encaissement.py`.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from app.contextes.souscription.adaptateurs.sortant.depots_memoire import (
    DepotAppelsDeFondsMemoire,
    DepotContratsDHonorairesMemoire,
    DepotReglementsDHonorairesMemoire,
)
from app.contextes.souscription.adaptateurs.sortant.depots_sql import (
    DepotAppelsDeFondsSql,
    DepotContratsDHonorairesSql,
    DepotDevisSql,
    DepotPaiementsSql,
    DepotReglementsDHonorairesSql,
    DepotSouscriptionsSql,
    DepotTarifsFixesSql,
)
from app.contextes.souscription.api import (
    CatalogueArrete,
    DemandeLigne,
    DepotDevisMemoire,
    DepotPaiementsMemoire,
    DepotServicesMemoire,
    DepotSouscriptionsMemoire,
    DepotTarifsFixesMemoire,
    Devis,
    DevisImpossible,
    DevisIntrouvable,
    EcheanceAbonnement,
    EngagementRefuse,
    FixationRefusee,
    FournisseurTara,
    NaturePaiement,
    NatureService,
    OuvertureAccesTransverse,
    Paiement,
    Prospect,
    RappelEcheance,
    RapportPrelevement,
    RapportReconciliation,
    ResultatEncaissement,
    Service,
    ServiceASuspendre,
    ServiceIntrouvable,
    Souscription,
    SouscriptionIntrouvable,
    SuiteDeSouscription,
    TarifFixe,
    VerificationDIdentite,
    activer_souscription,
    appeler_les_echeances,
    echeancier,
    engager,
    etablir_devis,
    fixer,
    nouvelle_cle_idempotence,
    reconcilier,
    relances_du_jour,
    services_a_suspendre,
    traiter_notification,
)
from app.contextes.transverse.api import (
    AccesRequis,
    Permission,
    atelier,
    exiger,
    session_de_travail,
)
from app.infrastructure.config import configuration
from app.partage.horloge import jour_a_douala, maintenant
from app.partage.locataire import courant

routeur = APIRouter(prefix="/souscription", tags=["Souscription"])


class Comptoir:
    """Les dépôts et services du contexte, réunis.

    Le fournisseur de paiement est construit depuis la configuration : sans clé
    Tara, il passe en mode simulé et n'émet aucun appel réseau — ce qui rend le
    parcours exerçable en développement, y compris dans les tests.
    """

    def __init__(self) -> None:
        config = configuration()
        session = session_de_travail()
        if session is None:
            self.devis = DepotDevisMemoire()
            self.souscriptions = DepotSouscriptionsMemoire()
            self.paiements = DepotPaiementsMemoire()
            self.tarifs_fixes = DepotTarifsFixesMemoire()
            self.contrats_honoraires = DepotContratsDHonorairesMemoire()
            self.reglements_honoraires = DepotReglementsDHonorairesMemoire()
            self.appels_de_fonds = DepotAppelsDeFondsMemoire()
        else:
            self.devis = DepotDevisSql(session, courant())
            self.souscriptions = DepotSouscriptionsSql(session, courant())
            self.paiements = DepotPaiementsSql(session, courant())
            self.tarifs_fixes = DepotTarifsFixesSql(session, courant())
            self.contrats_honoraires = DepotContratsDHonorairesSql(session, courant())
            self.reglements_honoraires = DepotReglementsDHonorairesSql(session, courant())
            self.appels_de_fonds = DepotAppelsDeFondsSql(session, courant())
        # ⚠️ LE JOUR ANNONCÉ EST ARRIVÉ. Ce commentaire disait : « il aura sa
        # table le jour où la direction devra changer un tarif sans
        # redéploiement ». La structure de l'offre reste en code ; les PRIX sont
        # les décisions du gérant, conservées en base et rejouées à chaque
        # lecture. Voir `CatalogueArrete`.
        self.services = CatalogueArrete(DepotServicesMemoire(), self.tarifs_fixes)
        self.fournisseur = FournisseurTara(
            cle_api=config.tara_cle_api,
            identifiant_marchand_tara=config.tara_identifiant_marchand,
            adresse_publique=config.adresse_publique,
        )
        boutique = atelier()
        self.acces = OuvertureAccesTransverse(
            comptes=boutique.comptes,
            habilitations=boutique.habilitations,
            jetons=boutique.jetons,
            journal=boutique.journal,
            notifications=boutique.notifications,
            locataire=boutique.comptes.locataire,
            adresse_publique=config.adresse_publique_site,
        )
        self.journal = boutique.journal

    @property
    def suites(self):
        """La table des suites, par nature de paiement.

        ─────────────────────────────────────────────────────────────────────────
        ⚠️ **Montée ici et nulle part ailleurs.** C'est le seul endroit du contexte
        qui connaît à la fois les souscriptions, les accès, les dossiers et les
        proformas ; c'est donc le seul qui puisse câbler les deux suites.

        Une propriété plutôt qu'un attribut d'`__init__` : la suite du parcours
        d'acquisition construit ses dépôts à la demande, et les monter au
        démarrage conserverait une session de requête déjà fermée.
        ─────────────────────────────────────────────────────────────────────────
        """
        from app.contextes.souscription.adaptateurs.entrant.sources import (
            boite_du_parcours,
            depot_dossiers,
            depot_proformas,
        )
        from app.contextes.souscription.application.encaissement_du_parcours import (
            SuiteDeProforma,
        )
        from app.contextes.souscription.application.fonds_de_tiers import SuiteDeFondsDeTiers
        from app.contextes.souscription.application.reglement_des_honoraires import (
            SuiteDHonoraires,
        )

        return {
            NaturePaiement.SOUSCRIPTION: SuiteDeSouscription(
                souscriptions=self.souscriptions,
                acces=self.acces,
                journal=self.journal,
            ),
            NaturePaiement.PROFORMA: SuiteDeProforma(
                dossiers=depot_dossiers(),
                proformas=depot_proformas(),
                boite=boite_du_parcours(),
                journal=self.journal,
            ),
            # ⚠️ 14 octobre 2026 : les honoraires des adhérents, réglés depuis leur
            # espace. Voir `SuiteDHonoraires`.
            NaturePaiement.HONORAIRES: SuiteDHonoraires(
                reglements=self.reglements_honoraires,
                journal=self.journal,
                boite=boite_du_parcours(),
            ),
            # ⚠️ 14 octobre 2026 : les fonds de tiers (TVA, CNPS sous mandat de paiement).
            NaturePaiement.FONDS_DE_TIERS: SuiteDeFondsDeTiers(
                appels=self.appels_de_fonds,
                boite=boite_du_parcours(),
                journal=self.journal,
            ),
        }


@lru_cache
def _comptoir_memoire() -> Comptoir:
    """⚠️ En mémoire, ceci **est** la persistance : deux instances seraient deux
    bases sans lien."""
    return Comptoir()


def reinitialiser_comptoir() -> None:
    """Repart d'un comptoir mémoire vierge. Destiné aux tests.

    Nommé pour ce qu'il fait, et non `cache_clear` : la mémoïsation a déjà changé
    de place une fois, et les tests qui s'y accrochaient ont cassé.
    """
    _comptoir_memoire.cache_clear()


def comptoir() -> Comptoir:
    """Le comptoir en vigueur — de la requête en SQL, du processus en mémoire.

    En SQL, il est reconstruit à chaque appel parce qu'il porte des dépôts
    adossés à la session de la requête. Le mémoïser conserverait la session d'une
    requête précédente, déjà fermée — une panne qui n'apparaîtrait qu'à la
    seconde requête.
    """
    if session_de_travail() is None:
        return _comptoir_memoire()
    return Comptoir()


def _reference(prefixe: str) -> str:
    """Long et non devinable : la référence d'un devis circule dans un lien, et
    c'est elle seule qui protège le devis d'un tiers curieux."""
    return f"{prefixe}-{uuid.uuid4().hex[:16]}"


# ── Le catalogue ─────────────────────────────────────────────────────────────


@routeur.get(
    "/services",
    summary="L'offre du cabinet, au barème d'une date",
    description=(
        "Public. `a_la_date` est **obligatoire** : un barème se lit à une date ou ne "
        "se lit pas. Rendre « le catalogue courant » ferait afficher le tarif "
        "d'aujourd'hui sur un devis d'il y a trois semaines.\n\n"
        "Le champ `souscriptible_en_ligne` commande l'affichage du bouton « Souscrire » : "
        "le front ne doit pas déduire cette règle d'un montant nul, il finirait par le "
        "lire comme « gratuit »."
    ),
)
def lire_services(
    a_la_date: date = Query(..., description="Date à laquelle résoudre le barème"),
) -> list[Service]:
    return comptoir().services.charger(a_la_date)


# ── Les prix, fixés par le gérant ───────────────────────────────────────────


class DemandeDeFixation(BaseModel):
    """Tel prix, pour telle prestation, à partir de tel jour."""

    service: str = Field(min_length=1)
    formule: str | None = None
    montant: Decimal = Field(gt=0)
    a_partir_du: date
    #: Pourquoi ce prix. Obligatoire : un prix sans motif ne se défend pas trois
    #: ans plus tard devant un client qui demande pourquoi il a changé.
    motif: str = Field(min_length=3)


class LigneDeTarif(BaseModel):
    """Le prix d'une prestation, ou d'une de ses formules, à une date."""

    service: str
    libelle: str
    formule: str | None = None
    libelle_formule: str | None = None
    montant: Decimal | None = None
    #: `A_VALIDER` tant que le gérant n'a rien décidé.
    statut: str
    en_vigueur_depuis: date
    fixe_par: str | None = None
    #: Le nom de celui qui l'a fixé. ⚠️ L'identifiant seul (« C-001 ») ne dit rien
    #: à la direction qui relit l'écran : vu à l'essai dans la console.
    fixe_par_nom: str | None = None
    fixe_le: str | None = None
    #: Faux pour ce qui se chiffre sur étude : le gérant n'y fixe pas de prix
    #: unique, il l'arrête sur la proposition de chaque client.
    fixable: bool


class DecisionDeTarif(TarifFixe):
    """Une décision, avec le nom de son auteur pour l'écran."""

    fixe_par_nom: str | None = None


class EtatDesTarifs(BaseModel):
    a_la_date: date
    lignes: list[LigneDeTarif]
    #: Combien de prix restent à fixer : la question que se pose la direction.
    a_fixer: int
    decisions: list[DecisionDeTarif]


def _etat_des_tarifs(a_la_date: date) -> EtatDesTarifs:
    c = comptoir()
    noms = {compte.identifiant: compte.nom_complet for compte in atelier().comptes.lister()}
    lignes: list[LigneDeTarif] = []
    for s in c.services.charger(a_la_date):
        # Ce qui se chiffre sur étude (la création, le ponctuel) n'a pas de prix
        # unique à fixer : le cabinet l'arrête sur la proposition de chaque
        # client. Tout le reste se fixe, y compris la tranche du réel, qui peut
        # recevoir un prix le jour où le gérant le décide.
        sur_etude = s.nature is NatureService.SUR_ETUDE
        cibles = (
            [(f.code, f.libelle, f.tarifs) for f in s.formules]
            if s.formules
            else [(None, None, s.tarifs)]
        )
        for code, libelle, tarifs in cibles:
            tarif = next((t for t in tarifs if t.couvre(a_la_date)), None)
            if tarif is None:
                continue
            lignes.append(
                LigneDeTarif(
                    service=s.code,
                    libelle=s.libelle,
                    formule=code,
                    libelle_formule=libelle,
                    montant=tarif.montant,
                    statut=tarif.statut.value,
                    en_vigueur_depuis=tarif.du,
                    fixe_par=tarif.fixe_par,
                    fixe_par_nom=noms.get(tarif.fixe_par) if tarif.fixe_par else None,
                    fixe_le=tarif.fixe_le.isoformat() if tarif.fixe_le else None,
                    fixable=not sur_etude,
                )
            )
    return EtatDesTarifs(
        a_la_date=a_la_date,
        lignes=lignes,
        a_fixer=sum(1 for x in lignes if x.fixable and x.statut != "FIXE"),
        decisions=[
            DecisionDeTarif(**d.model_dump(), fixe_par_nom=noms.get(d.fixe_par))
            for d in c.tarifs_fixes.lister()
        ],
    )


@routeur.get(
    "/tarifs",
    summary="Les prix de l'offre, et ce qui reste à fixer",
    description=(
        "Réservé à la direction (`FIXER_LES_TARIFS`). Chaque prix porte son statut : "
        "`A_VALIDER` tant que le gérant ne l'a pas arrêté, `FIXE` ensuite, avec qui "
        "l'a fixé et quand."
    ),
)
def lire_tarifs(
    acces: AccesRequis, a_la_date: date = Query(...)
) -> EtatDesTarifs:
    exiger(acces, Permission.FIXER_LES_TARIFS)
    return _etat_des_tarifs(a_la_date)


@routeur.post(
    "/tarifs",
    summary="Fixer le prix d'une prestation, à partir d'une date",
    status_code=201,
    description=(
        "Réservé à la direction. **Le prix est une décision humaine** : tant qu'il "
        "n'est pas fixé ici, le tarif du catalogue s'affiche comme indicatif et ne se "
        "règle pas en ligne.\n\n"
        "On ne fixe jamais un prix dans le passé : un devis déjà émis changerait de "
        "prix. Corriger un prix, c'est en fixer un nouveau à partir d'une date."
    ),
    responses={
        404: {"description": "Prestation inconnue"},
        422: {"description": "Date passée, formule absente ou prestation sur étude"},
    },
)
def fixer_un_tarif(acces: AccesRequis, demande: DemandeDeFixation) -> EtatDesTarifs:
    exiger(acces, Permission.FIXER_LES_TARIFS)
    instant = maintenant()
    if demande.a_partir_du < jour_a_douala(instant):
        raise HTTPException(
            status_code=422,
            detail=(
                f"un prix ne se fixe pas dans le passé ({demande.a_partir_du}) : un devis "
                "déjà émis changerait de prix. Choisir aujourd'hui ou une date à venir."
            ),
        )
    c = comptoir()
    fixation = TarifFixe(
        identifiant=_reference("TF"),
        service=demande.service,
        formule=demande.formule,
        montant=demande.montant,
        a_partir_du=demande.a_partir_du,
        motif=demande.motif,
        fixe_par=acces.compte,
        fixe_le=instant,
    )
    try:
        service = next(
            (s for s in c.services.charger(demande.a_partir_du) if s.code == demande.service),
            None,
        )
        if service is None:
            raise ServiceIntrouvable(f"aucune prestation {demande.service} au catalogue")
        # Appliquée à blanc d'abord : une décision que le domaine refuse n'est pas
        # écrite, et le gérant lit pourquoi.
        fixer(service, fixation)
    except ServiceIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec
    except FixationRefusee as echec:
        raise HTTPException(status_code=422, detail=str(echec)) from echec

    c.tarifs_fixes.enregistrer(fixation)
    c.journal.ajouter(
        horodatage=instant,
        acteur=acces.compte,
        action="tarif.fixe",
        objet_type="service",
        objet_id=demande.service,
        apres={
            "formule": demande.formule,
            "montant": str(demande.montant),
            "a_partir_du": demande.a_partir_du.isoformat(),
            "motif": demande.motif,
        },
    )
    return _etat_des_tarifs(jour_a_douala(instant))


# ── Le devis ─────────────────────────────────────────────────────────────────


class DemandeDevis(BaseModel):
    prospect: Prospect
    lignes: list[DemandeLigne] = Field(min_length=1)


@routeur.post(
    "/devis",
    summary="Établir un devis",
    status_code=201,
    description=(
        "Public. Les montants sont **recopiés** du barème du jour, pas référencés : "
        "un devis reçu le 20 mars et ouvert le 2 avril doit afficher le prix de mars.\n\n"
        "Valable trente jours."
    ),
    responses={422: {"description": "Prestation inconnue, ou formule impossible à choisir"}},
)
def creer_devis(demande: DemandeDevis) -> Devis:
    boutique = comptoir()
    instant = maintenant()
    try:
        devis = etablir_devis(
            reference=_reference("DV"),
            prospect=demande.prospect,
            demandes=demande.lignes,
            services=boutique.services.charger(jour_a_douala(instant)),
            a_la_date=jour_a_douala(instant),
        )
    except (DevisImpossible, ServiceIntrouvable) as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus
    boutique.devis.enregistrer(devis)
    return devis


@routeur.get(
    "/devis/{reference}",
    summary="Relire un devis",
    description=(
        "Public : la référence est longue et non devinable, et c'est elle qui protège "
        "le devis. Un devis caduc reste lisible — le prospect qui revient voit ce "
        "qu'on lui avait proposé plutôt qu'une page introuvable."
    ),
)
def lire_devis(reference: str) -> Devis:
    try:
        return comptoir().devis.lire(reference)
    except DevisIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence


# ── L'engagement ─────────────────────────────────────────────────────────────


class DemandeEngagement(BaseModel):
    #: Facultatif : à défaut, celui du prospect. Nécessaire pour les prestations
    #: qui ouvrent un accès à un dossier.
    niu: str | None = None


class Engagement(BaseModel):
    souscription: Souscription
    paiement: Paiement
    message: str


@routeur.post(
    "/devis/{reference}/engagement",
    summary="Régler un devis par paiement mobile",
    status_code=201,
    description=(
        "Le prestataire pousse un menu USSD sur le téléphone du prospect, qui valide "
        "avec son code. La notification arrive ensuite, et c'est elle qui encaisse. "
        "⚠️ Depuis le pas 83, elle n'ouvre plus l'accès à un dossier : le cabinet "
        "vérifie d'abord l'identité (route `/souscriptions/{reference}/activation`).\n\n"
        "Le devis est **fermé** au moment de l'engagement : deux clics sur le bouton "
        "de paiement produiraient sinon deux débits pour la même prestation."
    ),
    responses={
        404: {"description": "Devis inconnu"},
        409: {"description": "Devis déjà engagé, caduc, ou non chiffrable"},
    },
)
def engager_devis(reference: str, demande: DemandeEngagement) -> Engagement:
    boutique = comptoir()
    try:
        souscription, paiement = engager(
            reference,
            reference_souscription=_reference("SO"),
            identifiant_paiement=_reference("PM"),
            cle_idempotence=nouvelle_cle_idempotence(),
            niu=demande.niu,
            devis=boutique.devis,
            souscriptions=boutique.souscriptions,
            paiements=boutique.paiements,
            services=boutique.services,
            fournisseur=boutique.fournisseur,
            journal=boutique.journal,
            a_l_instant=maintenant(),
        )
    except DevisIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence
    except EngagementRefuse as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus

    # ── Recette : le paiement se valide seul ────────────────────────────────
    #
    # ⚠️ Sans cela, le parcours s'arrête ici. Le prospect ne reçoit aucun menu
    # USSD — il n'y a pas de prestataire au bout —, et il faut un appel manuel à
    # la route de simulation pour continuer. Personne ne vérifie un parcours
    # qu'il faut pousser à la main à chaque essai.
    #
    # Ce raccourci n'existe **que** sous `CGA_MODE_DEMONSTRATION`, un drapeau
    # qu'on déclare et que la production refuse au démarrage. Il ne découle pas
    # de l'absence de clé Tara : l'en-tête de `fournisseur_tara.py` explique
    # pourquoi ce serait une faute — « un mode simulé qui validerait
    # automatiquement finirait un jour en production ».
    #
    # La suite du parcours n'est pas court-circuitée : on emprunte exactement le
    # chemin d'une vraie notification, celui que le prestataire déclenchera. Ce
    # qui est simulé, c'est l'appel du prestataire, rien d'autre.
    valide_d_office = configuration().mode_demonstration and not paiement.clos
    if valide_d_office:
        traiter_notification(
            {
                "productId": paiement.cle_idempotence,
                "paymentId": f"DEMONSTRATION-{paiement.identifiant}",
                "status": "SUCCESS",
                "amount": str(paiement.montant),
                "phoneNumber": paiement.telephone,
                "message": "validé d'office — mode démonstration",
            },
            fournisseur=boutique.fournisseur,
            paiements=boutique.paiements,
            suites=boutique.suites,
            journal=boutique.journal,
            a_l_instant=maintenant(),
        )
        # Relus : l'ouverture d'accès a fait évoluer les deux agrégats, et rendre
        # les objets d'avant afficherait « en attente » sur un paiement encaissé.
        paiement = boutique.paiements.lire(paiement.identifiant)
        souscription = boutique.souscriptions.lire(souscription.reference)

    return Engagement(
        souscription=souscription,
        paiement=paiement,
        message=(
            "⚠️ MODE DÉMONSTRATION — le paiement a été validé d'office, aucun "
            "argent n'a été débité. "
            + (
                "Le lien d'activation est parti : il se lit dans la boîte aux lettres "
                "de recette."
                if souscription.activee_le is not None
                # Pas 83 : une prestation qui ouvre un dossier attend la vérification
                # d'identité du cabinet ; ne pas annoncer un lien qui n'est pas parti.
                else "L'accès au dossier s'ouvrira après vérification de l'identité par "
                "le cabinet."
            )
        )
        if valide_d_office
        else (
            "Un message vient d'être envoyé sur votre téléphone : validez-le avec votre "
            "code. "
            # ⚠️ Pas 83 : la phrase promettait « l'accès s'ouvre dès la confirmation »,
            # ce qui n'est plus vrai d'une prestation qui ouvre un dossier.
            + (
                "L'accès à votre dossier s'ouvrira après la confirmation du paiement et "
                "la vérification de votre identité par le cabinet, qui vous contactera."
                if souscription.ouvre_un_acces
                else "La prestation est confirmée dès la validation du paiement."
            )
        )
        if not paiement.clos
        else (paiement.motif or "le paiement n'a pas pu être initié"),
    )


@routeur.get("/souscriptions/{reference}", summary="Suivre une souscription")
def lire_souscription(reference: str) -> Souscription:
    """Public : la référence est longue et non devinable. C'est la page sur
    laquelle le navigateur revient après le paiement, et elle doit être lisible
    par quelqu'un qui n'a pas encore de compte — puisque son compte n'existe
    justement que si le paiement a abouti."""
    try:
        return comptoir().souscriptions.lire(reference)
    except SouscriptionIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence


# ── La notification du prestataire ───────────────────────────────────────────


@routeur.post(
    "/notification/tara",
    summary="Notification d'encaissement du prestataire",
    description=(
        "**Répond toujours 200**, y compris sur une charge utile illisible — sans quoi "
        "le prestataire la renverrait en boucle.\n\n"
        "⚠️ Tara **ne signe pas** ses notifications : ni HMAC, ni en-tête de signature. "
        "La sécurité repose sur HTTPS, sur le secret de cette adresse, et sur la "
        "vérification de l'identifiant marchand. Un statut « payé » ne se déduit donc "
        "jamais de cet appel seul : c'est le rapprochement qui fait foi, et la "
        "réconciliation par appel sortant qui confirme."
    ),
)
async def notification_tara(requete: Request) -> ResultatEncaissement:
    charge_utile: dict[str, Any]
    try:
        charge_utile = await requete.json()
    except Exception:  # noqa: BLE001 — une charge illisible ne doit pas faire 500
        charge_utile = {}
    boutique = comptoir()

    # ⚠️ L'encaissement écrit en base, et cette route est asynchrone : tenu sur la
    # boucle d'événements, il figeait le serveur le temps de l'écriture. La route
    # reste asynchrone pour `await requete.json()`, qui l'est réellement.
    # Voir `tests/test_boucle_non_bloquee.py`.
    return await run_in_threadpool(
        lambda: traiter_notification(
            charge_utile if isinstance(charge_utile, dict) else {},
            fournisseur=boutique.fournisseur,
            paiements=boutique.paiements,
            suites=boutique.suites,
            journal=boutique.journal,
            a_l_instant=maintenant(),
        )
    )


# ── Exploitation, côté cabinet ───────────────────────────────────────────────


@routeur.get(
    "/a-activer",
    summary="Les encaissements payés dont l'accès n'a pas été ouvert",
    description=(
        "**La** requête de surveillance. Un client qui a payé et n'a rien reçu doit "
        "apparaître ici sans qu'on ait à le chercher. La plus ancienne d'abord : c'est "
        "celui qui attend depuis le plus longtemps.\n\n"
        "Lisible par la direction (`LIRE_PILOTAGE`) **et** par l'administration des "
        "comptes (`GERER_COMPTES`), qui ouvre l'accès après vérification (pas 83)."
    ),
)
def lister_a_activer(acces: AccesRequis) -> list[Souscription]:
    # ⚠️ Pas 83 : la route était réservée à `LIRE_PILOTAGE`, que seule la direction
    # détient. Or c'est l'administrateur (`GERER_COMPTES`) qui vérifie l'identité et
    # ouvre l'accès : il devait ouvrir des souscriptions qu'il ne pouvait pas lister,
    # et la direction lister des souscriptions qu'elle ne pouvait pas ouvrir. Les deux
    # rôles lisent la liste ; seul l'administrateur l'ouvre.
    if not acces.detient(Permission.GERER_COMPTES):
        exiger(acces, Permission.LIRE_PILOTAGE)
    return comptoir().souscriptions.a_activer()


class DemandeActivation(BaseModel):
    """Comment le cabinet a vérifié que le payeur est l'entreprise (pas 83)."""

    model_config = ConfigDict(extra="forbid")

    verification: str = Field(min_length=20)


@routeur.post(
    "/souscriptions/{reference}/activation",
    summary="Ouvrir l'accès d'une souscription payée, après vérification d'identité",
    description=(
        "Depuis le pas 83, **le paiement n'ouvre plus d'accès à un dossier** : un inconnu "
        "pouvait payer avec le NIU d'une autre entreprise et lire son dossier. Le cabinet "
        "vérifie l'identité, décrit sa vérification, et ouvre l'accès par cette route, "
        "qui nomme son auteur au journal."
    ),
    responses={409: {"description": "L'accès n'a pas pu être ouvert — le motif est rendu"}},
)
def rattraper(acces: AccesRequis, reference: str, demande: DemandeActivation) -> Souscription:
    exiger(acces, Permission.GERER_COMPTES)
    boutique = comptoir()
    try:
        return activer_souscription(
            reference,
            verification=VerificationDIdentite(par=acces.compte, comment=demande.verification),
            souscriptions=boutique.souscriptions,
            acces=boutique.acces,
            journal=boutique.journal,
            a_l_instant=maintenant(),
        )
    except SouscriptionIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence
    except RuntimeError as echec:
        raise HTTPException(status_code=409, detail=str(echec)) from echec


@routeur.post(
    "/reconciliation",
    summary="Repêcher les encaissements dont la notification n'est pas arrivée",
    description=(
        "Interroge le prestataire sur chaque paiement resté en attente. C'est **nous** "
        "qui appelons : la notification entrante n'étant pas signée, l'appel sortant est "
        "de toute façon la seule source de vérité.\n\n"
        "À planifier toutes les cinq à quinze minutes. Le champ `repeches` dit si le "
        "filet sert : durablement à zéro, tant mieux ; s'il monte, l'adresse de rappel a "
        "un problème."
    ),
)
def lancer_reconciliation(acces: AccesRequis) -> RapportReconciliation:
    exiger(acces, Permission.LIRE_PILOTAGE)
    boutique = comptoir()
    return reconcilier(
        fournisseur=boutique.fournisseur,
        paiements=boutique.paiements,
        suites=boutique.suites,
        journal=boutique.journal,
        a_l_instant=maintenant(),
    )


# ── Développement ────────────────────────────────────────────────────────────


class Simulation(BaseModel):
    reussi: bool = True
    montant: Decimal | None = None


@routeur.post(
    "/paiements/{identifiant}/simulation",
    summary="Simuler la notification du prestataire (développement)",
    description=(
        "⚠️ **Refusée dès que des identifiants Tara sont configurés.** C'est la "
        "contrepartie du mode simulé : celui-ci ne valide jamais un paiement tout seul, "
        "il faut le dire explicitement — et cette route disparaît en production plutôt "
        "que de s'y endormir."
    ),
    responses={409: {"description": "Le fournisseur réel est configuré"}},
)
def simuler(identifiant: str, simulation: Simulation) -> ResultatEncaissement:
    boutique = comptoir()
    if not boutique.fournisseur.simule:
        raise HTTPException(
            status_code=409,
            detail=(
                "des identifiants Tara sont configurés : la simulation est refusée. "
                "Un encaissement réel ne se fabrique pas."
            ),
        )
    paiement = boutique.paiements.lire(identifiant)
    return traiter_notification(
        {
            "productId": paiement.cle_idempotence,
            "paymentId": f"SIMULE-{identifiant}",
            "status": "SUCCESS" if simulation.reussi else "FAILED",
            "amount": str(simulation.montant) if simulation.montant is not None else None,
            "phoneNumber": paiement.telephone,
            "message": "notification simulée",
        },
        fournisseur=boutique.fournisseur,
        paiements=boutique.paiements,
        suites=boutique.suites,
        journal=boutique.journal,
        a_l_instant=maintenant(),
    )


# ── Les abonnements ──────────────────────────────────────────────────────────


@routeur.get(
    "/souscriptions/{reference}/echeancier",
    summary="Les mensualités d'un abonnement",
    description=(
        "**Calculé, jamais stocké** : il découle de la date d'effet, de la périodicité "
        "et de la date de résiliation. Le persister figerait un calendrier qui "
        "deviendrait faux à la première résiliation.\n\n"
        "Les périodes suivent la **date d'effet**, pas le mois civil : une souscription "
        "du 17 produit des périodes du 17 au 16. C'est ce qui évite le prorata — et le "
        "prorata est ce qui rend une première facture incompréhensible."
    ),
)
def lire_echeancier(
    reference: str,
    jusqu_au: date = Query(..., description="Date de résolution des états"),
) -> list[EcheanceAbonnement]:
    boutique = comptoir()
    try:
        souscription = boutique.souscriptions.lire(reference)
    except SouscriptionIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence
    return echeancier(
        souscription,
        boutique.paiements.par_souscription(reference),
        jusqu_au=jusqu_au,
    )


@routeur.post(
    "/prelevements",
    summary="Appeler les mensualités dues aujourd'hui",
    description=(
        "À planifier une fois par jour. Demande le prélèvement de toute échéance dont "
        "la date d'appel est arrivée — cinq jours avant la période, pour laisser le "
        "temps de recommencer avant que le service ne s'interrompe.\n\n"
        "**Idempotent** : rejouer la tâche ne produit pas de second débit. ⚠️ Un "
        "prélèvement refusé n'est **pas** rappelé automatiquement — un menu de paiement "
        "quotidien est vécu comme du harcèlement et peut être facturé à l'adhérent. Le "
        "rattrapage passe par la relance."
    ),
)
def lancer_prelevements(acces: AccesRequis) -> RapportPrelevement:
    exiger(acces, Permission.LIRE_PILOTAGE)
    boutique = comptoir()
    instant = maintenant()
    return appeler_les_echeances(
        souscriptions=boutique.souscriptions,
        paiements=boutique.paiements,
        fournisseur=boutique.fournisseur,
        journal=boutique.journal,
        a_l_instant=instant,
        identifiant=_reference("PM"),
        cle_idempotence=nouvelle_cle_idempotence(),
    )


@routeur.get(
    "/relances",
    summary="Les relances d'impayé à émettre aujourd'hui",
    description=(
        "Jalons J+1, J+7 et J+14. Le premier n'est pas de la pression : **l'adhérent "
        "ignore souvent que le prélèvement a échoué**, parce que l'opérateur ne le lui "
        "dit pas. Le champ `derniere` distingue le jalon qui annonce l'arrêt du service "
        "de ceux qui informent."
    ),
)
def lister_relances_abonnement(
    acces: AccesRequis, a_la_date: date = Query(...)
) -> list[RappelEcheance]:
    exiger(acces, Permission.RELANCER_ADHERENT)
    boutique = comptoir()
    return relances_du_jour(
        boutique.souscriptions.lister(), boutique.paiements, a_la_date
    )


@routeur.get(
    "/services-a-suspendre",
    summary="Les abonnements dont le service doit s'arrêter",
    description=(
        "Au-delà du délai de grâce de quinze jours. ⚠️ **Suspendre n'est pas "
        "séquestrer** : le cabinet cesse de traiter les pièces et de préparer les "
        "déclarations, mais l'adhérent conserve l'accès en lecture à ses documents "
        "comptables — qu'il est légalement tenu de conserver dix ans.\n\n"
        "Cette route rend une **décision**, pas une exécution : le contexte K ne sait "
        "aujourd'hui que suspendre un compte, ce qui couperait tout. Le champ "
        "`consigne` porte la marche à suivre."
    ),
)
def lister_services_a_suspendre(
    acces: AccesRequis, a_la_date: date = Query(...)
) -> list[ServiceASuspendre]:
    exiger(acces, Permission.LIRE_PILOTAGE)
    boutique = comptoir()
    return services_a_suspendre(
        boutique.souscriptions.lister(), boutique.paiements, a_la_date
    )
