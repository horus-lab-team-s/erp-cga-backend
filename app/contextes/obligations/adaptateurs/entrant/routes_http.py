"""API du contexte F · Obligations et déclarations.

Deux écrans sont servis ici, et la fiche de conception les nomme tous deux
indispensables : **l'échéancier consolidé du portefeuille**, côté cabinet, et
**« mes échéances »**, côté adhérent. Ce sont les mêmes données, filtrées.

**L'échéancier se calcule à la demande, il ne se stocke pas.** Il découle du profil
du dossier — régime, rattachement, assujettissement, exercice —, tous historisés.
Le persister figerait un calendrier qui deviendrait faux au premier changement de
régime, et personne ne verrait qu'il l'est devenu.

Le retard, de même, se calcule : `en_retard` est une comparaison entre une échéance
et une date, pas une propriété de l'obligation.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.contextes.comptabilite.api import (
    DepotEcritures,
    DepotEcrituresSql,
    balance,
    ecritures_en_memoire,
)
from app.contextes.obligations.adaptateurs.sortant.accuses import accuses_du_portail
from app.contextes.obligations.adaptateurs.sortant.catalogue_obligations import (
    DepotTypesObligationMemoire,
)
from app.contextes.obligations.adaptateurs.sortant.declaration_tva_yaml import (
    charger_le_formulaire_tva,
    charger_les_reglages_du_depot_tva,
)
from app.contextes.obligations.adaptateurs.sortant.effectif import effectif_du_dossier
from app.contextes.obligations.api import (
    DeclarationTVA,
    ObligationInstance,
    Penalite,
    Relance,
    TypeObligation,
    calculer_penalite,
    etablir_declaration_tva,
    generer_echeancier,
    relances_du_jour,
)
from app.contextes.obligations.application.declaration_tva import (
    credit_reporte_au_debut_de,
    mois_declarables_avant,
)
from app.contextes.obligations.application.depot import (
    CompletudeDeLaPeriode,
    DepotImpossible,
    DossierDeDepot,
    RevueDeLaPeriode,
    constater_depot,
    constater_un_depot_hors_tva,
    preparer_depot_tva,
)
from app.contextes.obligations.application.eligibilite_des_adherents import (
    EligibiliteDeLAdherent,
    revoir_l_eligibilite,
)
from app.contextes.obligations.application.formulaire_tva import (
    LigneDeDeclaration,
    lignes_de_la_declaration,
)
from app.contextes.obligations.application.surveillance_des_seuils import (
    SurveillanceDuDossier,
    surveiller_le_portefeuille,
    surveiller_un_dossier,
)
from app.contextes.portefeuille.api import (
    DepotEntreprises,
    DepotEntreprisesSql,
    Entreprise,
    EntrepriseIntrouvable,
    StatutIntrouvable,
    entreprises_en_memoire,
)
from app.contextes.referentiel.api import (
    ParametreResolu,
    ServiceParametres,
    service_parametres,
)
from app.contextes.transverse.api import (
    AccesRequis,
    AccuseIncoherent,
    AccuseReception,
    ModeDepot,
    Permission,
    Portail,
    PortailDeclaratif,
    atelier,
    exiger,
    exiger_dossier,
    restreindre,
    session_de_travail,
)
from app.infrastructure.config import configuration
from app.partage.horloge import maintenant
from app.partage.locataire import courant

routeur = APIRouter(prefix="/obligations", tags=["Obligations"])

#: Le catalogue des types d'obligation. En mémoire **et c'est correct** : ce
#: n'est pas de la donnée de dossier mais du référentiel — la liste des
#: obligations que le droit camerounais prévoit, avec leurs délais. Elle
#: rejoindra le contexte A le jour où le fiscaliste l'éditera.
_catalogue = DepotTypesObligationMemoire()


# ⚠️ Le magasin mémoire du contexte propriétaire, et non une construction locale :
# voir `magasins_memoire.py` de ce contexte, et le pas 52.
_entreprises_memoire = entreprises_en_memoire


def depot_entreprises() -> DepotEntreprises:
    """Le dépôt en vigueur — SQL dans une requête, mémoire sinon.

    ─────────────────────────────────────────────────────────────────────────
    ⚠️ CE CONTEXTE LISAIT **TOUJOURS** LA MÉMOIRE

    Les deux fabriques rendaient le dépôt de démonstration sans consulter la
    session, y compris en mode PostgreSQL. Conséquence : l'échéancier et la
    déclaration de TVA se calculaient sur les dossiers et les écritures de
    démonstration, pendant que la comptabilité réelle vivait en base.

    Le défaut ne se voyait nulle part. Les écrans affichaient des chiffres
    plausibles, le rapprochement avec la balance n'était fait par personne, et
    c'est une déclaration fiscale qui en serait sortie fausse.

    Ce contexte ne persiste rien lui-même — il **lit** le portefeuille et la
    comptabilité pour calculer des échéances. C'est précisément pour cela que
    l'oubli était silencieux : aucune écriture ne manquait, seules les lectures
    portaient sur la mauvaise source.
    ─────────────────────────────────────────────────────────────────────────
    """
    session = session_de_travail()
    if session is None:
        return _entreprises_memoire()
    return DepotEntreprisesSql(session, courant())


def depot_ecritures(entreprise: str) -> DepotEcritures:
    """Les écritures du dossier, depuis la source en vigueur. Voir ci-dessus."""
    session = session_de_travail()
    if session is None:
        return ecritures_en_memoire(entreprise)
    return DepotEcrituresSql(session, courant(), entreprise)


def _entreprise(niu: str) -> Entreprise:
    try:
        return depot_entreprises().lire(niu)
    except EntrepriseIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence


def _exercice(entreprise: Entreprise, libelle: str):
    exercice = next((e for e in entreprise.exercices if e.libelle == libelle), None)
    if exercice is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"exercice « {libelle} » inconnu pour {entreprise.denomination}. "
                f"Connus : {', '.join(e.libelle for e in entreprise.exercices)}"
            ),
        )
    return exercice


def _exercice_contenant(dossier: Entreprise, jour: date, demande: str | None) -> str:
    """L'exercice demandé, ou celui du dossier qui contient ce jour (pas 87).

    ⚠️ Trois routes avaient `exercice="2026"` pour défaut, écrit en dur. Un écran qui ne
    le passait pas lisait les écritures de 2026 pour une période de 2027 : une
    déclaration de TVA de janvier 2027 serait sortie vide, donc « rien à déclarer ».
    """
    if demande is not None:
        return demande
    exercice = next((e for e in dossier.exercices if e.ouverture <= jour <= e.cloture), None)
    if exercice is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"aucun exercice de {dossier.denomination} ne contient le {jour:%d/%m/%Y}. "
                f"Connus : {', '.join(e.libelle for e in dossier.exercices) or 'aucun'}."
            ),
        )
    return exercice.libelle


@routeur.get("/catalogue", summary="Les types d'obligation en vigueur")
def lire_catalogue(
    acces: AccesRequis,
    a_la_date: date = Query(
        ...,
        description=(
            "Une loi de finances peut créer une obligation, en supprimer une, ou "
            "changer un jour limite. ⚠️ La date est acceptée mais pas encore "
            "exploitée : le catalogue n'est pas versionné."
        ),
    ),
) -> list[TypeObligation]:
    exiger(acces, Permission.LIRE_DOSSIER)
    return _catalogue.charger(a_la_date)


class LigneEcheance(BaseModel):
    """Une ligne de l'échéancier, avec le retard déjà calculé."""

    obligation: ObligationInstance
    jours_restants: int

    @computed_field
    @property
    def en_retard(self) -> bool:
        return self.jours_restants < 0 and not self.obligation.deposee


@routeur.get(
    "/dossiers/{entreprise}/echeancier",
    summary="Le calendrier d'un exercice, calculé depuis le profil",
    description=(
        "⚠️ Le profil est réévalué **à la fin de chaque période**, jamais une fois "
        "pour toutes. C'est ce qui rend le franchissement de seuil correct : une "
        "entreprise assujettie à partir de septembre a quatre déclarations de TVA "
        "sur l'exercice, pas douze et pas zéro."
    ),
)
def lire_echeancier(
    acces: AccesRequis,
    entreprise: str,
    exercice: str = Query(...),
    a_la_date: date = Query(..., description="Date de référence pour les retards"),
) -> list[LigneEcheance]:
    """⚠️ La présence de salariés n'est plus un paramètre de requête (pas 56).

    Elle se lit au fichier du personnel, période par période. Le paramètre
    `a_des_salaries`, faux par défaut, n'était envoyé par aucun écran : la CNPS et
    les retenues sur salaires n'apparaissaient jamais. Un appelant qui l'envoie
    encore n'a plus aucun effet.
    """
    exiger_dossier(acces, Permission.LIRE_DOSSIER, entreprise)
    dossier = _entreprise(entreprise)
    instances = generer_echeancier(
        dossier,
        _catalogue.charger(a_la_date),
        _exercice(dossier, exercice),
        emploie_sur=effectif_du_dossier(dossier.niu),
        accuse_de=accuses_du_portail(),
    )
    return [
        LigneEcheance(obligation=o, jours_restants=o.jours_restants(a_la_date))
        for o in instances
    ]


@routeur.get(
    "/relances",
    summary="Les relances d'échéance à émettre aujourd'hui",
    description=(
        "Jalons J-15, J-7, J-2 et J+1. Le J+1 n'est pas une relance de politesse : "
        "c'est le jour où la pénalité commence à courir."
    ),
)
def lister_relances(
    acces: AccesRequis,
    a_la_date: date = Query(...),
) -> list[Relance]:
    """⚠️ PAS 87 : PLUS D'EXERCICE « 2026 » PAR DÉFAUT.

    La route ne relançait que les obligations de l'exercice passé en paramètre, « 2026 »
    par défaut. Essai : au 10/01/2027, **aucune relance**, alors que la TVA de décembre
    2026 arrive à échéance le 15 janvier. Les relances portent désormais sur tout
    exercice ouvert avant la date et clos depuis moins d'un an : les obligations d'un
    exercice se déposent jusqu'à plusieurs mois après sa clôture (DSF, TVA de décembre).
    """
    exiger(acces, Permission.LIRE_DOSSIER)
    obligations: list[ObligationInstance] = []
    dossiers = restreindre(acces, depot_entreprises().lister(), lambda e: e.niu)
    # ⚠️ Lus une fois pour tout le portefeuille, pas une fois par dossier.
    accuse_de = accuses_du_portail()
    for dossier in dossiers:
        for exercice_dossier in dossier.exercices:
            if not (
                exercice_dossier.ouverture <= a_la_date
                and exercice_dossier.cloture >= a_la_date - timedelta(days=366)
            ):
                continue
            obligations += generer_echeancier(
                dossier,
                _catalogue.charger(a_la_date),
                exercice_dossier,
                emploie_sur=effectif_du_dossier(dossier.niu),
                accuse_de=accuse_de,
            )
    return relances_du_jour(obligations, a_la_date)


class DeclarationTVAPresentee(DeclarationTVA):
    """La déclaration, et ce que l'écran de préparation montre autour (pas 109)."""

    lignes: list[LigneDeDeclaration]
    completude: CompletudeDeLaPeriode
    revue: RevueDeLaPeriode


def _toutes_les_ecritures(dossier: Entreprise):
    ecritures = []
    for exercice in dossier.exercices:
        ecritures.extend(depot_ecritures(dossier.niu).lister(exercice.libelle))
    return ecritures


def _credit_reporte(dossier: Entreprise, periode_debut: date) -> Decimal:
    """Le crédit laissé par les mois déclarables avant `periode_debut` (pas 109).

    ⚠️ Mois par mois, depuis l'ouverture du premier exercice connu, et seulement les mois où le
    dossier était assujetti : un mois hors assujettissement n'a pas de déclaration, donc ne
    consomme ni ne produit de crédit.
    """
    if not dossier.exercices:
        return Decimal(0)
    periodes = mois_declarables_avant(
        min(e.ouverture for e in dossier.exercices), periode_debut, dossier.assujettie_tva_au
    )
    if not periodes:
        return Decimal(0)
    return credit_reporte_au_debut_de(periodes, _toutes_les_ecritures(dossier))


def _completude(niu: str, debut: date, fin: date) -> CompletudeDeLaPeriode:
    """Les pièces de la période, par la surface publique de la collecte (pas 109).

    Une pièce appartient à la période par la date de son document, sinon par sa réception,
    comme à la clôture mensuelle (pas 107).
    """
    from app.contextes.collecte.api import (
        DepotDemandesSql,
        DepotPiecesSql,
        demandes_en_memoire,
        pieces_en_memoire,
    )

    session = session_de_travail()
    pieces = pieces_en_memoire() if session is None else DepotPiecesSql(session, courant())
    demandes = demandes_en_memoire() if session is None else DepotDemandesSql(session, courant())
    de_la_periode = [
        p
        for p in pieces.du_dossier(niu)
        if debut <= (p.date_document or p.recue_le.date()) <= fin
    ]
    return CompletudeDeLaPeriode(
        pieces_recues=len(de_la_periode),
        pieces_en_souffrance=sorted(
            p.identifiant for p in de_la_periode if p.en_attente_de_traitement
        ),
        pieces_attendues=sorted(
            d.identifiant for d in demandes.ouvertes(niu) if d.demandee_le <= fin
        ),
    )


def _revue_de_la_periode(niu: str, debut: date, fin: date) -> RevueDeLaPeriode:
    """La revue du mois qui couvre toute la période, par la surface publique de la comptabilité."""
    from app.contextes.comptabilite.api import depot_des_revues

    revue = next(
        (r for r in depot_des_revues().du_dossier(niu) if r.du <= debut and fin <= r.au), None
    )
    if revue is None:
        return RevueDeLaPeriode()
    return RevueDeLaPeriode(identifiant=revue.identifiant, statut=revue.statut.value)


@routeur.get(
    "/dossiers/{entreprise}/declaration-tva",
    summary="La déclaration de TVA d'une période",
    description=(
        "La ligne qui fait la valeur du produit : **« TVA rejetée par le contrôle de "
        "conformité »**. Dans une déclaration ordinaire, une TVA rejetée est "
        "invisible — la case porte simplement un chiffre plus faible, et rien "
        "n'explique pourquoi. Ici chaque rejet est isolé, chiffré et justifié pièce "
        "par pièce, avec le code de la règle et le motif en clair."
    ),
)
def lire_declaration_tva(
    acces: AccesRequis,
    entreprise: str,
    periode_debut: date = Query(...),
    periode_fin: date = Query(...),
    exercice: str | None = Query(
        None, description="Défaut : l'exercice qui contient la fin de période (pas 87)."
    ),
) -> DeclarationTVAPresentee:
    """⚠️ Pas 109 : le crédit reporté ne se reçoit plus, il se calcule (voir
    `credit_reporte_au_debut_de`). La réponse porte en plus les lignes du formulaire, avec les
    écritures qui les composent, la complétude des pièces et la revue du mois."""
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    dossier = _entreprise(entreprise)
    exercice = _exercice_contenant(dossier, periode_fin, exercice)
    if not dossier.assujettie_tva_au(periode_fin):
        raise HTTPException(
            status_code=409,
            detail=(
                f"{dossier.denomination} n'est pas assujettie à la TVA au "
                f"{periode_fin} — régime {dossier.regime_au(periode_fin)}. Établir "
                "une déclaration lui ferait réclamer une déduction à laquelle elle "
                "n'a pas droit."
            ),
        )
    ecritures = depot_ecritures(entreprise).lister(exercice)
    declaration = etablir_declaration_tva(
        ecritures,
        entreprise=dossier.denomination,
        periode_debut=periode_debut,
        periode_fin=periode_fin,
        credit_reporte_anterieur=_credit_reporte(dossier, periode_debut),
    )
    return DeclarationTVAPresentee(
        # Les champs calculés se recalculent : les recopier les ferait refuser.
        **declaration.model_dump(
            exclude={"tva_a_payer", "credit_a_reporter", "neant", "cout_de_la_non_conformite"}
        ),
        lignes=lignes_de_la_declaration(
            ecritures,
            periode_debut=periode_debut,
            periode_fin=periode_fin,
            tva_rejetee=declaration.tva_rejetee,
            ecritures_rejetees=[r.ecriture for r in declaration.detail_rejets],
            credit_reporte=declaration.credit_reporte_anterieur,
            formulaire=charger_le_formulaire_tva(configuration().dossier_referentiel),
        ),
        completude=_completude(entreprise, periode_debut, periode_fin),
        revue=_revue_de_la_periode(entreprise, periode_debut, periode_fin),
    )



class EtatDeLaPeriode(BaseModel):
    tva_a_payer: Decimal
    credit_a_reporter: Decimal


class ImpactDeLaContrepassation(BaseModel):
    """Ce qu'une contre-passation changerait à la déclaration de TVA de son mois (pas 110)."""

    periode_debut: date
    periode_fin: date
    #: Pourquoi la contre-passation serait refusée : aucun impact à calculer.
    refus: str | None = None
    assujettie: bool = False
    #: L'écriture inverse mouvemente-t-elle de la TVA collectée ou déductible ?
    touche_la_tva: bool = False
    avant: EtatDeLaPeriode | None = None
    apres: EtatDeLaPeriode | None = None
    #: La déclaration du mois est déjà déposée : la corriger demandera une rectificative.
    deja_deposee: bool = False
    message: str


@routeur.get(
    "/dossiers/{entreprise}/impact-contrepassation",
    summary="L'impact d'une contre-passation sur la déclaration de TVA de son mois",
)
def impact_de_la_contrepassation(
    acces: AccesRequis,
    entreprise: str,
    exercice: str = Query(...),
    journal: str = Query(...),
    numero: int = Query(..., ge=1),
    date_operation: date = Query(...),
) -> ImpactDeLaContrepassation:
    """« L'impact sur une déclaration en préparation est annoncé avant validation » (maquette
    « Parcours comptable », vue E, note 3).

    La déclaration du mois de la contre-passation est calculée **deux fois** par le même
    `etablir_declaration_tva` : sur les écritures telles qu'elles sont, puis avec l'écriture
    inverse, comme si elle était validée. Aucun second calcul de TVA : c'est la déclaration
    elle-même qui dit la différence. ⚠️ Seul le mois de la contre-passation est annoncé ; un
    crédit modifié se reporte ensuite sur les mois suivants, et l'annonce le dit.
    """
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    from app.contextes.comptabilite.api import (
        EcritureIntrouvable,
        apercevoir_la_contrepassation,
        periodes_verrouillees_du_dossier,
    )

    dossier = _entreprise(entreprise)
    debut = date_operation.replace(day=1)
    fin = (debut + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    exercice_de_la_date = _exercice_contenant(dossier, date_operation, None)
    ouvert = next((e for e in dossier.exercices if e.libelle == exercice_de_la_date), None)
    try:
        apercu = apercevoir_la_contrepassation(
            f"{exercice}/{journal}/{numero:06d}",
            jour=date_operation,
            exercice=next((e for e in dossier.exercices if e.libelle == exercice), None),
            periodes_verrouillees=periodes_verrouillees_du_dossier(entreprise),
            depot=depot_ecritures(entreprise),
            par=acces.compte,
        )
    except EcritureIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence
    if apercu.inverse is None:
        return ImpactDeLaContrepassation(
            periode_debut=debut, periode_fin=fin, refus=apercu.refus, message=apercu.refus or ""
        )
    inverse = apercu.inverse
    touche = any(l_.compte.startswith(("443", "445")) for l_ in inverse.lignes)
    try:
        assujettie = dossier.assujettie_tva_au(fin)
    except StatutIntrouvable:
        assujettie = False
    if not assujettie or not touche:
        return ImpactDeLaContrepassation(
            periode_debut=debut,
            periode_fin=fin,
            assujettie=assujettie,
            touche_la_tva=touche,
            message=(
                "La contre-passation ne mouvemente pas de TVA : aucune déclaration n'est touchée."
                if assujettie
                else "Le dossier n'est pas assujetti à la TVA ce mois-là : aucune déclaration."
            ),
        )
    ecritures = depot_ecritures(entreprise).lister(ouvert.libelle if ouvert else exercice)
    credit = _credit_reporte(dossier, debut)

    def etat(avec: list) -> EtatDeLaPeriode:
        declaration = etablir_declaration_tva(
            avec,
            entreprise=dossier.denomination,
            periode_debut=debut,
            periode_fin=fin,
            credit_reporte_anterieur=credit,
        )
        return EtatDeLaPeriode(
            tva_a_payer=declaration.tva_a_payer, credit_a_reporter=declaration.credit_a_reporter
        )

    avant = etat(ecritures)
    apres = etat([*ecritures, inverse.valider(par=acces.compte, le=maintenant())])
    obligations = generer_echeancier(
        dossier,
        _catalogue.charger(date_operation),
        _exercice(dossier, ouvert.libelle if ouvert else exercice),
        emploie_sur=effectif_du_dossier(dossier.niu),
        accuse_de=accuses_du_portail(),
    )
    deposee = any(
        o.code_obligation == "TVA" and o.periode_fin == fin and o.deposee for o in obligations
    )
    mois = f"{debut:%m/%Y}"
    message = (
        f"La déclaration de TVA de {mois} est déjà déposée : cette correction demandera une "
        "déclaration rectificative."
        if deposee
        else f"La déclaration de TVA de {mois}, en préparation, sera recalculée."
    ) + " Un crédit modifié se reporte aussi sur les mois suivants."
    return ImpactDeLaContrepassation(
        periode_debut=debut,
        periode_fin=fin,
        assujettie=True,
        touche_la_tva=True,
        avant=avant,
        apres=apres,
        deja_deposee=deposee,
        message=message,
    )


class PenaliteSimulee(Penalite):
    """La pénalité, avec les deux paramètres du référentiel qui l'ont produite (pas 87)."""

    taux_fixe: ParametreResolu
    taux_mensuel: ParametreResolu


@routeur.get(
    "/penalite",
    summary="Simuler la pénalité d'un dépôt tardif",
    description=(
        "⚠️ Le décompte des mois de retard — mois calendaires ou périodes de trente "
        "jours, fraction comptée ou non — n'est pas au référentiel. Le traitement "
        "retenu est le plus défavorable au contribuable, donc le plus prudent pour "
        "le Centre : mieux vaut annoncer une pénalité surestimée qu'une surprise au "
        "paiement. À confirmer par le fiscaliste.\n\n"
        "Les taux sont ceux du référentiel à la date de l'échéance, rendus avec leur "
        "fondement et leur statut ; ils ne se passent plus en paramètre (pas 87)."
    ),
)
def simuler_penalite(
    acces: AccesRequis,
    montant_du: Decimal = Query(..., gt=0, description="Montant de l'impôt dû"),
    echeance: date = Query(...),
    a_la_date: date = Query(..., description="Date du dépôt effectif, ou d'aujourd'hui"),
) -> PenaliteSimulee:
    """⚠️ PAS 87 : LES TAUX VIENNENT DU RÉFÉRENTIEL, PLUS DE LA REQUÊTE.

    La route prenait `taux_fixe` et `taux_mensuel` en paramètres, avec des valeurs par
    défaut écrites ici : 10 % et 1,5 %. Le référentiel porte, **validés**, 25 % (CGI
    art. L95 et L96) et 1,5 % (art. L106). Essai avant correction : pour 1 000 000 FCFA
    dus depuis deux mois, la simulation annonçait 130 000 FCFA de pénalités au lieu de
    280 000, exactement la « surprise au paiement » que l'en-tête dit éviter. Et
    n'importe quel appelant pouvait passer des taux nuls.

    Les taux sont résolus **à la date de l'échéance**, celle où le retard commence : une
    loi de finances qui changerait le taux ensuite ne s'applique pas à un retard déjà
    couru. La réponse dit quels taux ont servi, sur quel texte et à quel statut, pour
    qu'un écran ne présente jamais un chiffre sans sa source.
    """
    exiger(acces, Permission.LIRE_DOSSIER)
    if a_la_date < echeance:
        raise HTTPException(
            status_code=422,
            detail=(
                f"date du {a_la_date}, antérieure à l'échéance du {echeance} : "
                "il n'y a pas de pénalité à calculer."
            ),
        )
    try:
        fixe = parametres_normatifs().resoudre("PENALITE_RETARD_TAUX_FIXE", echeance)
        mensuel = parametres_normatifs().resoudre("PENALITE_RETARD_TAUX_MENSUEL", echeance)
    except LookupError as absence:
        # Refuser plutôt que supposer un taux : c'est ce que la route faisait, et c'était faux.
        raise HTTPException(status_code=409, detail=str(absence)) from absence
    penalite = calculer_penalite(
        montant_du,
        echeance=echeance,
        a_la_date=a_la_date,
        taux_fixe=fixe.valeur_decimale,
        taux_mensuel=mensuel.valeur_decimale,
    )
    return PenaliteSimulee(
        **penalite.model_dump(exclude={"total", "a_regler"}),
        taux_fixe=fixe,
        taux_mensuel=mensuel,
    )


# ── Le dépôt ─────────────────────────────────────────────────────────────────


def portail() -> PortailDeclaratif:
    """Le guichet de la DGI, pris sur l'atelier de la requête.

    ⚠️ Il avait ici son propre registre en mémoire, et le défaut ne s'est vu
    qu'en redémarrant l'application sur PostgreSQL : les accusés étaient
    consignés dans un dictionnaire que la base ne voyait pas. La preuve de dépôt
    — la pièce la plus critique du système — disparaissait au redémarrage,
    pendant que tout le reste persistait.

    Un registre local paraissait innocent parce qu'il fonctionnait : le refus du
    second dépôt passait, les tests étaient verts. C'est le genre de défaut que
    seule l'exécution réelle révèle.
    """
    return atelier().portail


def _dossier_tva(
    entreprise: str,
    periode_debut: date,
    periode_fin: date,
    exercice: str | None,
    a_la_date: date,
) -> tuple[DossierDeDepot, ObligationInstance]:
    """Assemble le dossier de TVA d'une période, contrôles compris."""
    dossier = _entreprise(entreprise)
    exercice = _exercice_contenant(dossier, periode_fin, exercice)
    obligations = generer_echeancier(
        dossier,
        _catalogue.charger(a_la_date),
        _exercice(dossier, exercice),
        emploie_sur=effectif_du_dossier(dossier.niu),
        accuse_de=accuses_du_portail(),
    )
    obligation = next(
        (
            o
            for o in obligations
            if o.code_obligation == "TVA" and o.periode_fin == periode_fin
        ),
        None,
    )
    if obligation is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"aucune obligation de TVA au {periode_fin} pour "
                f"{dossier.denomination}. Vérifier l'échéancier : le régime ou "
                "l'assujettissement de la période peut ne pas en produire."
            ),
        )

    ecritures = depot_ecritures(entreprise).lister(exercice)
    declaration = etablir_declaration_tva(
        ecritures,
        entreprise=dossier.denomination,
        periode_debut=periode_debut,
        periode_fin=periode_fin,
        credit_reporte_anterieur=_credit_reporte(dossier, periode_debut),
    )
    prepare = preparer_depot_tva(
        obligation,
        dossier,
        declaration,
        ecritures,
        portail=portail(),
        a_la_date=a_la_date,
        completude=_completude(entreprise, periode_debut, periode_fin),
        revue=_revue_de_la_periode(entreprise, periode_debut, periode_fin),
        reglages=charger_les_reglages_du_depot_tva(configuration().dossier_referentiel),
        # ⚠️ Le référentiel n'est validé sur aucun texte : la réserve est donc
        # toujours levée. Le jour où le fiscaliste aura confirmé les paramètres,
        # cette valeur viendra de /referentiel/validation.
        referentiel_valide=False,
    )
    return prepare, obligation


@routeur.get(
    "/dossiers/{entreprise}/depot-tva",
    summary="Préparer le dépôt d'une déclaration de TVA",
    description=(
        "Assemble les chiffres, applique les **contrôles de recevabilité**, et fige un "
        "bordereau avec son empreinte. Ne dépose rien et ne change aucun état : "
        "préparer se refait autant de fois qu'on veut.\n\n"
        "⚠️ **La DGI ne publie aucune interface programmatique.** `depot_automatique` "
        "vaut donc `false` : le bordereau est fait pour être recopié sur le portail, "
        "puis l'accusé revient par la route de constat.\n\n"
        "Ce qui a de la valeur ici n'est pas le transport, c'est le refus de préparer "
        "une déclaration qui serait rejetée ou redressée."
    ),
)
def preparer_depot(
    acces: AccesRequis,
    entreprise: str,
    periode_debut: date = Query(...),
    periode_fin: date = Query(...),
    a_la_date: date = Query(...),
    exercice: str | None = Query(
        None, description="Défaut : l'exercice qui contient la fin de période (pas 87)."
    ),
) -> DossierDeDepot:
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    prepare, _ = _dossier_tva(entreprise, periode_debut, periode_fin, exercice, a_la_date)
    return prepare


class ConstatDepot(BaseModel):
    """Ce que le réviseur rapporte du portail."""

    numero: str = Field(min_length=1, description="Le numéro d'accusé rendu par la DGI")
    depose_le: datetime = Field(
        description=(
            "La date **du dépôt sur le portail**, pas celle de la saisie. C'est elle "
            "que l'administration retient pour dire si le dépôt est dans les délais."
        )
    )
    montant_constate: Decimal | None = Field(default=None, ge=0)
    piece_jointe: str | None = Field(
        default=None,
        description=(
            "Référence du justificatif archivé. ⚠️ Non résolue tant que la GED "
            "n'existe pas : un accusé sans pièce repose sur la seule parole de qui "
            "l'a saisi, et `verifiable` le dit."
        ),
    )
    precision: str | None = None


class DepotConstate(BaseModel):
    obligation: ObligationInstance
    accuse: AccuseReception
    reserves_assumees: list[str]


@routeur.post(
    "/dossiers/{entreprise}/depot-tva",
    summary="Constater un dépôt effectué sur le portail",
    description=(
        "Enregistre l'accusé et fait passer l'obligation à **déclarée**. L'accusé est "
        "confronté au dossier préparé : même référence **et même empreinte**. Un "
        "numéro saisi sur la mauvaise ligne, ou obtenu avant une correction des "
        "écritures, est refusé.\n\n"
        "⚠️ Réclame une **session renforcée** par le second facteur — c'est l'une des "
        "actions sensibles du tableau de 05-securite-multitenant.md."
    ),
    responses={
        403: {"description": "Second facteur requis, ou permission manquante"},
        409: {"description": "Dossier non recevable, ou accusé incohérent"},
    },
)
def constater(
    acces: AccesRequis,
    entreprise: str,
    constat: ConstatDepot,
    periode_debut: date = Query(...),
    periode_fin: date = Query(...),
    a_la_date: date = Query(...),
    exercice: str | None = Query(
        None, description="Défaut : l'exercice qui contient la fin de période (pas 87)."
    ),
) -> DepotConstate:
    exiger_dossier(acces, Permission.DEPOSER_DECLARATION, entreprise)
    prepare, obligation = _dossier_tva(entreprise, periode_debut, periode_fin, exercice, a_la_date)
    accuse = AccuseReception(
        numero=constat.numero,
        portail=Portail.DGI_TELEDECLARATION,
        reference_document=prepare.document.reference,
        depose_le=constat.depose_le,
        mode=ModeDepot.MANUEL,
        empreinte_deposee=prepare.document.empreinte,
        depose_par=acces.compte,
        montant_constate=constat.montant_constate,
        piece_jointe=constat.piece_jointe,
        precision=constat.precision,
    )
    try:
        declaree, consigne = constater_depot(
            prepare,
            obligation,
            accuse,
            portail=portail(),
            journal=atelier().journal,
            par=acces.compte,
            a_l_instant=maintenant(),
        )
    except (DepotImpossible, AccuseIncoherent) as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus

    return DepotConstate(
        obligation=declaree,
        accuse=consigne,
        reserves_assumees=[a.code for a in prepare.recevabilite.reserves],
    )


class DemandeDeConstat(BaseModel):
    """Ce que le réviseur rapporte d'un dépôt fait hors de la plateforme.

    ⚠️ `extra="forbid"` : ni le guichet, ni le statut, ni le déposant ne se reçoivent.
    Le guichet vient du catalogue, le déposant de la session.
    """

    model_config = ConfigDict(extra="forbid")

    code_obligation: str = Field(min_length=1)
    periode_debut: date
    periode_fin: date
    numero: str = Field(min_length=1, max_length=120, description="Le numéro d'accusé")
    depose_le: datetime = Field(description="La date du dépôt au guichet, pas celle de la saisie")
    montant_constate: Decimal | None = Field(default=None, ge=0)
    piece_jointe: str | None = None
    precision: str | None = Field(default=None, max_length=500)


@routeur.post(
    "/dossiers/{entreprise}/depots",
    summary="Constater le dépôt d'une obligation hors TVA",
    description=(
        "Consigne l'accusé d'une obligation déposée hors de la plateforme (CNPS, "
        "retenues sur salaires, IGS, DSF, patente) : l'obligation devient **déclarée** "
        "à l'échéancier, aux relances et au pilotage.\n\n"
        "⚠️ **Aucun bordereau n'est confronté** : l'empreinte porte sur le constat, pas "
        "sur ce que le guichet a reçu. La TVA est refusée ici, elle a son parcours et "
        "ses contrôles. Réclame une **session renforcée** par le second facteur."
    ),
    responses={
        403: {"description": "Second facteur requis, ou permission manquante"},
        404: {"description": "Aucune obligation de ce code sur cette période"},
        409: {"description": "Déjà déclarée, TVA, date impossible, ou accusé incohérent"},
    },
)
def constater_hors_tva(
    acces: AccesRequis, entreprise: str, demande: DemandeDeConstat
) -> DepotConstate:
    """⚠️ L'obligation est retrouvée à l'échéancier, jamais reçue.

    Constater la CNPS d'un dossier sans salariés, ou la TVA trimestrielle d'une
    entreprise mensuelle, consignerait un dépôt d'une obligation qui n'existe pas :
    le `404` le dit, avec les périodes qui existent.
    """
    exiger_dossier(acces, Permission.DEPOSER_DECLARATION, entreprise)
    dossier = _entreprise(entreprise)
    exercice = next(
        (
            e for e in dossier.exercices
            if e.ouverture <= demande.periode_fin <= e.cloture
        ),
        None,
    )
    if exercice is None:
        raise HTTPException(
            status_code=404,
            detail=f"aucun exercice de {dossier.denomination} ne couvre le {demande.periode_fin}.",
        )
    jour = maintenant().date()
    types = _catalogue.charger(jour)
    echeancier = generer_echeancier(
        dossier,
        types,
        exercice,
        emploie_sur=effectif_du_dossier(dossier.niu),
        accuse_de=accuses_du_portail(),
    )
    obligation = next(
        (
            o for o in echeancier
            if o.code_obligation == demande.code_obligation
            and o.periode_debut == demande.periode_debut
            and o.periode_fin == demande.periode_fin
        ),
        None,
    )
    if obligation is None:
        existantes = sorted(
            f"{o.periode_debut:%d/%m/%Y}-{o.periode_fin:%d/%m/%Y}"
            for o in echeancier if o.code_obligation == demande.code_obligation
        )
        raise HTTPException(
            status_code=404,
            detail=(
                f"aucune obligation {demande.code_obligation} du {demande.periode_debut:%d/%m/%Y} "
                f"au {demande.periode_fin:%d/%m/%Y} à l'échéancier de {dossier.denomination}. "
                f"Périodes connues : {', '.join(existantes) or 'aucune'}."
            ),
        )
    (type_obligation,) = [t for t in types if t.code == obligation.code_obligation]
    try:
        declaree, consigne = constater_un_depot_hors_tva(
            obligation,
            type_obligation,
            numero=demande.numero,
            depose_le=demande.depose_le,
            montant_constate=demande.montant_constate,
            piece_jointe=demande.piece_jointe,
            precision=demande.precision,
            portail=portail(),
            journal=atelier().journal,
            par=acces.compte,
            a_l_instant=maintenant(),
        )
    except (DepotImpossible, AccuseIncoherent) as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    return DepotConstate(obligation=declaree, accuse=consigne, reserves_assumees=[])


def parametres_normatifs() -> ServiceParametres:
    """Le référentiel normatif du cabinet.

    ⚠️ Mémoïsé ici, contrairement aux profils d'échange qui sont relus à chaque
    appel, et la différence se justifie : un seuil légal change par loi de
    finances, une fois l'an, et son entrée en vigueur est datée dans le fichier
    lui-même. Une correction de seuil n'a donc jamais besoin d'être vue dans la
    minute, et elle est de toute façon résolue **à une date**.
    """
    # ⚠️ Pas 95 : le référentiel **du cabinet**, par le point de montage unique, et non plus
    # le fichier commun mémoïsé ici. La mémoïsation justifiée plus haut ne tient plus : un
    # cabinet qui valide un seuil doit le voir au calcul suivant. Voir `service_parametres`
    # dans `referentiel/api.py`.
    return service_parametres()


def _seuil_de_regime(a_la_date: date) -> ParametreResolu:
    """Le seuil d'assujettissement en vigueur à cette date.

    ⚠️ **Résolu à la date, jamais « la valeur actuelle ».** Une revue portant sur
    2024 doit comparer au seuil de 2024 : un seuil relevé par la loi de finances
    2026 ferait sinon disparaître rétroactivement des franchissements acquis, et
    le cabinet croirait ses dossiers en règle.
    """
    resolu = parametres_normatifs().resoudre("SEUIL_ASSUJETTISSEMENT_TVA", a_la_date)
    if resolu.borne is None:
        # ⚠️ Refuser plutôt que supposer : c'est au franc près que l'erreur coûte.
        raise HTTPException(
            status_code=409,
            detail="SEUIL_ASSUJETTISSEMENT_TVA ne déclare pas sa borne au référentiel.",
        )
    return resolu


@routeur.get(
    "/dossiers/{entreprise}/seuil-de-regime",
    summary="Où en est un dossier par rapport au seuil de changement de régime",
)
def lire_le_seuil_d_un_dossier(
    acces: AccesRequis, entreprise: str, a_la_date: date = Query(...)
) -> SurveillanceDuDossier:
    """Le chiffre d'affaires réel du dossier, comparé au seuil légal.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LE CHIFFRE D'AFFAIRES EST LU DANS LES LIVRES, JAMAIS DÉCLARÉ.**

    C'est ce qui sépare cette route d'un formulaire. Un chiffre saisi à la main
    serait celui que l'adhérent croit réaliser ; celui-ci est celui que sa
    comptabilité porte, et c'est le seul que l'administration retiendra.

    ⚠️ **DEUX MESURES, DEUX SENS**, et c'est tout l'objet de la réponse : sur
    l'exercice clos le franchissement est un fait et le reclassement est dû ; sur
    l'exercice en cours c'est une veille, et le chiffre est partiel.

    Le rapport porte la part de l'exercice écoulée pour que le lecteur juge
    lui-même : **rien n'est extrapolé**. Une entreprise saisonnière rend la
    projection linéaire fausse.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    dossier = _lire_le_dossier(entreprise)
    seuil = _seuil_de_regime(a_la_date)
    return surveiller_un_dossier(
        dossier,
        soldes_de=lambda exercice: balance(depot_ecritures(entreprise).toutes(exercice)),
        seuil=seuil.valeur_decimale,
        borne=seuil.borne,
        a_la_date=a_la_date,
    )


@routeur.get(
    "/seuils-de-regime",
    summary="La revue du portefeuille au regard du seuil de régime",
)
def revoir_les_seuils(
    acces: AccesRequis,
    a_la_date: date = Query(...),
    a_surveiller_seulement: bool = Query(
        True, description="Ne rendre que les dossiers qui demandent un regard."
    ),
) -> list[SurveillanceDuDossier]:
    """La revue annuelle des seuils, triée par urgence décroissante.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **C'EST ICI QUE LE CENTRE GAGNE SON AGRÉMENT.**

    Le scénario que cette route existe pour éviter est écrit dans le domaine du
    portefeuille depuis le premier jour : une entreprise passe au réel en
    septembre, son comptable continue de facturer sans TVA jusqu'en décembre, et
    au contrôle l'administration extrait la taxe du prix perçu. L'entreprise doit
    alors une TVA **qu'elle n'a jamais encaissée**, majorée des pénalités.

    ⚠️ **LE FILTRE EST ACTIF PAR DÉFAUT**, et c'est un choix. Une revue qui rend
    cent dossiers dont trois méritent un regard se lit une fois, puis plus jamais.
    Le paramètre existe pour vérifier, pas pour l'usage courant.

    ⚠️ **LA PORTÉE S'APPLIQUE AVANT LE CALCUL.** Un collaborateur ne voit que son
    portefeuille, et le calcul ne porte que sur ce qu'il voit : le faire sur tout
    puis filtrer coûterait une lecture comptable par dossier du cabinet, et
    laisserait fuir le nombre de dossiers par le temps de réponse.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.LIRE_COMPTABILITE)
    dossiers = restreindre(acces, depot_entreprises().lister(), lambda e: e.niu)
    seuil = _seuil_de_regime(a_la_date)
    vues = surveiller_le_portefeuille(
        dossiers,
        soldes_de=lambda niu, exercice: balance(depot_ecritures(niu).toutes(exercice)),
        seuil=seuil.valeur_decimale,
        borne=seuil.borne,
        a_la_date=a_la_date,
    )
    return [v for v in vues if v.a_surveiller] if a_surveiller_seulement else vues


@routeur.get(
    "/eligibilite-des-adherents",
    summary="La revue des adhérents au regard du seuil de l'article 118",
)
def revoir_l_eligibilite_des_adherents(
    acces: AccesRequis,
    a_la_date: date = Query(...),
    a_surveiller_seulement: bool = Query(
        True, description="Ne rendre que les adhérents qui demandent un regard."
    ),
) -> list[EligibiliteDeLAdherent]:
    """Les adhérents dont le chiffre d'affaires sort, ou va sortir, du champ du Centre.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **ENTRE L'ADMISSION ET LA LIASSE, RIEN NE DISAIT QU'UN ADHÉRENT GRANDISSAIT.**

    L'admission lit un chiffre déclaré ; la liasse refuse l'abattement sur les
    livres, des mois après la clôture. Cette revue comble l'intervalle : le Centre
    voit le dépassement venir, et peut prévenir l'adhérent avant que celui-ci
    n'attende un abattement qu'il n'aura pas.

    ⚠️ **ELLE NE RÉSILIE RIEN.** Sortir du champ n'éteint pas l'adhésion de plein
    droit ; la décision appartient au cabinet et passe par sa route.

    ⚠️ **LA BORNE VIENT DU RÉFÉRENTIEL**, avec la valeur : « n'excède pas », donc
    100 000 000 exactement reste dans le champ. Un seuil sans borne est refusé.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.LIRE_COMPTABILITE)
    resolu = parametres_normatifs().resoudre("SEUIL_ADHESION_CGA", a_la_date)
    if resolu.borne is None:
        raise HTTPException(
            status_code=409,
            detail="SEUIL_ADHESION_CGA ne déclare pas sa borne au référentiel.",
        )
    dossiers = restreindre(acces, depot_entreprises().lister(), lambda e: e.niu)
    revues = revoir_l_eligibilite(
        dossiers,
        soldes_de=lambda niu, exercice: balance(depot_ecritures(niu).toutes(exercice)),
        seuil=resolu.valeur_decimale,
        borne=resolu.borne,
        a_la_date=a_la_date,
    )
    return [r for r in revues if r.a_surveiller] if a_surveiller_seulement else revues


def _lire_le_dossier(niu: str) -> Entreprise:
    try:
        return depot_entreprises().lire(niu)
    except EntrepriseIntrouvable as absent:
        raise HTTPException(status_code=404, detail=str(absent)) from absent
