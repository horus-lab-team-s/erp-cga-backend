"""Les retards du commerce, par responsable (lot 4, 9 novembre 2026), sous `/acquisition`.

─────────────────────────────────────────────────────────────────────────────────
La moitié commerciale de la vue « Retards » de la console. L'autre moitié, comptable et
création, est servie par `/pilotage/retards` : le pilotage ne lit pas la souscription, et
cette route ne lit pas la comptabilité. La console réunit les deux par responsable.

CE QUI EST EN RETARD, ET SELON QUEL DÉLAI (aucun n'est écrit ici)

* une demande restée trop longtemps dans son état : les délais de veille du référentiel
  d'acquisition, ceux de `/acquisition/dossiers/en-souffrance` ;
* une proforma acceptée et impayée au-delà du délai du plan de relance (`impaye_apres`) ;
* un rendez-vous de création passé et pas encore constaté (honoré, manqué, annulé) ;
* des honoraires d'adhérent en retard, selon la règle de dépassement du cabinet.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.souscription.adaptateurs.entrant.sources import (
    depot_dossiers,
    depot_proformas,
)
from app.contextes.souscription.domaine.dossier_commercial import EtatDossier
from app.contextes.souscription.domaine.proforma import EtatProforma
from app.contextes.transverse.api import AccesRequis, Permission, exiger
from app.infrastructure.config import configuration
from app.partage.horloge import a_douala, jour_a_douala, maintenant

__all__ = ["routeur"]

routeur = APIRouter(prefix="/acquisition", tags=["Acquisition · retards"])


class ResponsableDuRetard(BaseModel):
    model_config = ConfigDict(frozen=True)

    compte: str
    nom: str


class RetardDuCommerce(BaseModel):
    """La même forme que `pilotage.Retard` : la console les réunit sans les traduire."""

    model_config = ConfigDict(frozen=True)

    categorie: str
    dossier: str
    denomination: str
    libelle: str
    echeance: date
    jours_de_retard: int = Field(ge=1)
    lien: str
    responsables: tuple[ResponsableDuRetard, ...] = ()


class RetardsDuCommerce(BaseModel):
    a_la_date: date
    retards: list[RetardDuCommerce]


def _jours(echeance: datetime, instant: datetime) -> int:
    return (jour_a_douala(instant) - jour_a_douala(echeance)).days


@routeur.get("/retards", summary="Les retards du commerce, des rendez-vous et des honoraires")
def lire_les_retards_du_commerce(acces: AccesRequis) -> RetardsDuCommerce:
    voit_le_commerce = acces.detient(Permission.LIRE_PROSPECT)
    voit_les_honoraires = acces.detient(Permission.LIRE_PILOTAGE) or acces.detient(
        Permission.ENCAISSER
    )
    if not voit_les_honoraires:
        # Ni la direction ni la caisse : le commerce seul, et le refus nomme sa permission.
        exiger(acces, Permission.LIRE_PROSPECT)
    from app.contextes.souscription.adaptateurs.entrant.routes_acquisition import (
        noms_des_collaborateurs,
        referentiel_de_veille,
    )
    from app.contextes.souscription.adaptateurs.entrant.routes_http import comptoir
    from app.contextes.souscription.adaptateurs.sortant.plan_de_relance import (
        charger_le_plan_de_relance,
    )
    from app.contextes.souscription.domaine.relance import impayees_a_reprendre
    from app.contextes.souscription.domaine.rendez_vous import EtatRendezVous

    instant = maintenant()
    noms = noms_des_collaborateurs()
    dossiers = {d.reference: d for d in depot_dossiers().lister()}

    def responsables(reference: str) -> tuple[ResponsableDuRetard, ...]:
        dossier = dossiers.get(reference)
        if dossier is None or not dossier.responsable:
            return ()
        return (
            ResponsableDuRetard(
                compte=dossier.responsable, nom=noms.get(dossier.responsable, dossier.responsable)
            ),
        )

    retards: list[RetardDuCommerce] = []
    if voit_le_commerce:
        # 1 · Les demandes qui dorment au-delà du délai de leur état.
        delais = referentiel_de_veille()
        for dossier in dossiers.values():
            delai = delais.get(dossier.etat)
            if delai is None or not dossier.ouvert or not dossier.en_souffrance(instant, delais):
                continue
            echeance = dossier.depuis_le + delai
            retards.append(
                RetardDuCommerce(
                    categorie="COMMERCE",
                    dossier=dossier.reference,
                    denomination=dossier.demande.nom,
                    libelle=f"Demande immobile à l'état {dossier.etat.value.lower()}",
                    echeance=jour_a_douala(echeance),
                    jours_de_retard=max(_jours(echeance, instant), 1),
                    lien=f"/acquisition/{dossier.reference}",
                    responsables=responsables(dossier.reference),
                )
            )
        # 2 · Les proformas acceptées et impayées au-delà du délai (pas encore engagées).
        plan = charger_le_plan_de_relance(configuration().dossier_referentiel / "relance")
        for proforma in impayees_a_reprendre(depot_proformas().lister(), plan, instant):
            dossier = dossiers.get(proforma.dossier)
            if dossier is None or dossier.etat is not EtatDossier.ACCEPTEE:
                continue
            echeance = proforma.transmise_le + plan.impaye_apres
            retards.append(
                RetardDuCommerce(
                    categorie="COMMERCE",
                    dossier=dossier.reference,
                    denomination=dossier.demande.nom,
                    libelle=f"Proforma {proforma.numero} acceptée et impayée",
                    echeance=jour_a_douala(echeance),
                    jours_de_retard=max(_jours(echeance, instant), 1),
                    lien=f"/acquisition/{dossier.reference}",
                    responsables=responsables(dossier.reference),
                )
            )
        # 2 bis · Lot 4 (9 novembre 2026) : les proformas transmises et expirées sans
        # acceptation. À réémettre (nouvelle version) ; une pénalité peut être décidée
        # (`routes_penalites`, motif PROFORMA_EXPIREE). La validité est celle imprimée sur la
        # proforma : `validite_lien_acceptation_jours`.
        validite = timedelta(days=configuration().validite_lien_acceptation_jours)
        for proforma in depot_proformas().lister():
            if proforma.etat is not EtatProforma.TRANSMISE:
                continue
            expire_le = proforma.emise_le + validite
            dossier = dossiers.get(proforma.dossier)
            if instant <= expire_le or dossier is None or not dossier.ouvert:
                continue
            retards.append(
                RetardDuCommerce(
                    categorie="COMMERCE",
                    dossier=dossier.reference,
                    denomination=dossier.demande.nom,
                    libelle=(
                        f"Proforma {proforma.numero} expirée sans acceptation : à réémettre"
                    ),
                    echeance=jour_a_douala(expire_le),
                    jours_de_retard=max(_jours(expire_le, instant), 1),
                    lien=f"/acquisition/{dossier.reference}",
                    responsables=responsables(dossier.reference),
                )
            )
        # 3 · Les rendez-vous passés, pas encore constatés.
        maintenant_a_douala = a_douala(instant)
        for rdv in comptoir().rendez_vous.depuis(maintenant_a_douala - timedelta(days=60)):
            if rdv.etat is not EtatRendezVous.PREVU or rdv.fin > maintenant_a_douala:
                continue
            dossier = dossiers.get(rdv.dossier)
            # L'heure d'un rendez-vous est DÉJÀ celle de Douala, sans fuseau : son jour se lit
            # tel quel (et non par `jour_a_douala`, qui ajouterait le décalage une seconde fois).
            jour_du_rendez_vous = date(rdv.debut.year, rdv.debut.month, rdv.debut.day)
            retards.append(
                RetardDuCommerce(
                    categorie="RENDEZ_VOUS",
                    dossier=rdv.dossier,
                    denomination=dossier.demande.nom if dossier else rdv.dossier,
                    libelle=f"Rendez-vous du {rdv.debut:%d/%m à %H h %M} à constater",
                    echeance=jour_du_rendez_vous,
                    jours_de_retard=max((jour_a_douala(instant) - jour_du_rendez_vous).days, 1),
                    lien="/acquisition/rendez-vous",
                    responsables=responsables(rdv.dossier),
                )
            )
    if voit_les_honoraires:
        # 4 · Les honoraires en retard, selon la règle de dépassement du cabinet.
        from app.contextes.souscription.adaptateurs.entrant.routes_reglement import (
            situation_du_dossier,
        )
        from app.contextes.souscription.domaine.situation_de_reglement import EtatDeReglement

        for contrat in comptoir().contrats_honoraires.lister():
            situation = situation_du_dossier(contrat.niu)
            if situation.etat not in (EtatDeReglement.EN_RETARD, EtatDeReglement.BLOQUE):
                continue
            depuis = situation.en_retard_depuis
            retards.append(
                RetardDuCommerce(
                    categorie="REGLEMENT",
                    dossier=contrat.niu,
                    denomination=contrat.denomination,
                    libelle=(
                        f"Honoraires impayés ({situation.du} FCFA)"
                        + (
                            " : traitement suspendu"
                            if situation.etat is EtatDeReglement.BLOQUE
                            else ""
                        )
                    ),
                    echeance=depuis,
                    jours_de_retard=max((jour_a_douala(instant) - depuis).days, 1),
                    lien=f"/portefeuille/{contrat.niu}",
                )
            )
    retards.sort(key=lambda r: (-r.jours_de_retard, r.denomination))
    return RetardsDuCommerce(a_la_date=jour_a_douala(instant), retards=retards)
