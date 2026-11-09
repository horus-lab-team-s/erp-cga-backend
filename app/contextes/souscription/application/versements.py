"""Recevoir un versement du parcours, et engager le dossier au seuil (9 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
UN SEUL GESTE POUR TROIS PORTES

Le guichet (la caisse constate), le rappel de l'opérateur (le client a payé par
mobile money) et la soumission de la demande (les frais de traitement) aboutissent
tous ici. Trois copies auraient divergé ; c'est la leçon de `solliciter_le_telephone`.

CE QUE FAIT `recevoir_un_versement`

1. calcule la situation du dossier (payé, reste, seuil) ;
2. refuse ce qui ne peut pas être reçu (au-delà du reste, frais en double…) ;
3. enregistre le versement ;
4. si le dossier est ACCEPTÉ et que le seuil est atteint, l'**engage** par le geste
   d'avant ce lot, `encaisser_l_acceptation` : le dossier passe à PAYÉE et le dossier
   de création s'ouvre. Les tranches suivantes s'enregistrent ensuite, jusqu'au solde.

⚠️ PAYÉE VEUT DÉSORMAIS DIRE « ENGAGÉE ». Avec un seuil à 100 % (le défaut), c'est la
même chose qu'avant. Avec 70 %, le dossier est engagé à 70 % et reste à solder : c'est
`SituationDesVersements.solde` qui dit si tout est payé, pas l'état du dossier.

⚠️ LES DOSSIERS PAYÉS AVANT CE LOT n'ont aucun versement enregistré : ils ont été réglés
d'un coup. `situation_du_dossier` les lit comme soldés, sans rien écrire.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from app.contextes.souscription.application.encaissement_du_parcours import (
    BoiteOuDeposer,
    ResultatEncaissementDuParcours,
    encaisser_l_acceptation,
    ouvre_un_locataire,
)
from app.contextes.souscription.domaine.dossier_commercial import (
    DossierCommercial,
    EtatDossier,
)
from app.contextes.souscription.domaine.honoraires import CanalDeReglement
from app.contextes.souscription.domaine.proforma import EtatProforma, Proforma
from app.contextes.souscription.domaine.versements_du_parcours import (
    NatureVersement,
    RegleDesVersements,
    SituationDesVersements,
    VersementDuParcours,
    VersementRefuse,
    situation_des_versements,
    verifier_le_versement,
)

__all__ = [
    "DepotVersements",
    "SuiteDeFraisDeTraitement",
    "ResultatDuVersement",
    "engager_si_le_seuil_est_atteint",
    "proforma_a_regler",
    "recevoir_un_versement",
    "situation_du_dossier",
    "total_a_regler",
]


class DepotVersements(Protocol):
    def pour(self, dossier: str) -> list[VersementDuParcours]: ...
    def enregistrer(self, versement: VersementDuParcours) -> None: ...


def proforma_a_regler(proformas: Iterable[Proforma]) -> Proforma | None:
    """La proposition vivante la plus récente, l'acceptée d'abord (comme la caisse)."""
    vivantes = [
        p for p in proformas if p.etat not in (EtatProforma.REMPLACEE, EtatProforma.ANNULEE)
    ]
    if not vivantes:
        return None
    return max(vivantes, key=lambda p: (p.etat is EtatProforma.ACCEPTEE, p.version))


def total_a_regler(proforma: Proforma | None) -> Decimal | None:
    """Ce que le client règle : le montant arrêté, celui que l'acceptation fige."""
    return None if proforma is None else proforma.tarif.montant


def situation_du_dossier(
    dossier: DossierCommercial,
    proforma: Proforma | None,
    versements: Iterable[VersementDuParcours],
    regle: RegleDesVersements,
) -> SituationDesVersements:
    siens = [v for v in versements if v.dossier == dossier.reference]
    total = total_a_regler(proforma)
    if dossier.etat is EtatDossier.PAYEE and total is not None and not siens:
        # Payé sans aucun versement enregistré : c'est un règlement d'avant ce lot, fait
        # d'un coup. Lu comme soldé, sans rien écrire. (Depuis ce lot, un dossier ne
        # passe à PAYÉE que par un versement : il en a toujours au moins un.)
        siens.append(
            VersementDuParcours(
                identifiant=f"historique-{dossier.reference}",
                dossier=dossier.reference,
                nature=NatureVersement.TRANCHE,
                montant=total,
                canal=CanalDeReglement.GUICHET,
                reference_externe=f"avant-les-tranches:{dossier.reference}",
                proforma=proforma.numero if proforma else None,
                constate_le=dossier.payee_le or dossier.depuis_le,
                constate_par="systeme",
            )
        )
    return situation_des_versements(dossier.reference, total, siens, regle)


class ResultatDuVersement(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    versement: VersementDuParcours | None
    situation: SituationDesVersements
    #: Le dossier après le geste (PAYÉE s'il vient d'être engagé).
    dossier: DossierCommercial
    #: Non nul quand ce versement a engagé le dossier.
    engagement: ResultatEncaissementDuParcours | None = None


def engager_si_le_seuil_est_atteint(
    dossier: DossierCommercial,
    proforma: Proforma,
    situation: SituationDesVersements,
    *,
    boite: BoiteOuDeposer,
    a_l_instant: datetime,
    reference_externe: str = "",
    slug: str = "",
) -> ResultatEncaissementDuParcours | None:
    """Le geste d'avant ce lot, au moment où le seuil est atteint. `None` sinon."""
    if dossier.etat is not EtatDossier.ACCEPTEE or not situation.seuil_atteint:
        return None
    adresse = slug or dossier.slug_retenu or ""
    return encaisser_l_acceptation(
        dossier,
        proforma,
        boite=boite,
        slug=adresse,
        tenant=f"tnt-{adresse}" if adresse and ouvre_un_locataire(proforma.service) else "",
        a_l_instant=a_l_instant,
        # ⚠️ Dérivé du dossier : deux chemins, un seul événement, une seule saga.
        identifiant_evenement=f"enc-{dossier.reference}",
        reference_externe=reference_externe,
    )


def recevoir_un_versement(
    *,
    dossier: DossierCommercial,
    proforma: Proforma | None,
    versements: DepotVersements,
    regle: RegleDesVersements,
    nature: NatureVersement,
    montant: Decimal,
    canal: CanalDeReglement,
    reference_externe: str,
    constate_par: str,
    a_l_instant: datetime,
    boite: BoiteOuDeposer,
    slug: str = "",
    notifie: bool = False,
) -> ResultatDuVersement:
    """Voir l'en-tête. Lève `VersementRefuse`, ou `VersementDejaRecu` au rejeu.

    ⚠️ `notifie=True` : L'ARGENT EST DÉJÀ ARRIVÉ (le rappel ou la réconciliation de Tara).
    Rien n'est alors refusé, sauf le rejeu : ce qui ne peut pas être absorbé — au-delà du
    reste, sur une proforma soldée ou un dossier classé — devient un TROP-PERÇU, à restituer
    par la caisse. Le refuser ferait un client débité que rien ne crédite, et qui repaie :
    c'est la « compensation » que l'intégration de paiement doit empêcher.
    """
    if notifie:
        return _recevoir_ce_qui_est_arrive(
            dossier=dossier,
            proforma=proforma,
            versements=versements,
            regle=regle,
            nature=nature,
            montant=montant,
            canal=canal,
            reference_externe=reference_externe,
            constate_par=constate_par,
            a_l_instant=a_l_instant,
            boite=boite,
            slug=slug,
        )
    # ⚠️ Un trop-perçu se rend, même sur un dossier classé : c'est souvent pour ça qu'il existe.
    if dossier.etat is EtatDossier.SANS_SUITE and nature is not NatureVersement.RESTITUTION:
        raise VersementRefuse(f"dossier {dossier.reference} classé sans suite.")
    if nature is NatureVersement.FRAIS_DE_TRAITEMENT:
        attendu = regle.frais_de(dossier.service) if dossier.service else None
        if attendu is None:
            raise VersementRefuse(
                f"aucun frais de traitement n'est fixé pour le service {dossier.service or '?'}."
            )
        if montant != attendu:
            raise VersementRefuse(
                f"les frais de traitement de ce service sont de {attendu} FCFA, pas {montant}."
            )
    elif nature is NatureVersement.RESTITUTION:
        pass  # bornée au trop-perçu en attente par `verifier_le_versement`
    elif nature is NatureVersement.PENALITE_RENDEZ_VOUS:
        # 9 novembre 2026 : ni proforma ni acceptation ; le montant fixé, et lui seul.
        if regle.penalite_rendez_vous is None:
            raise VersementRefuse("aucune pénalité de rendez-vous n'est fixée.")
        if montant != regle.penalite_rendez_vous:
            raise VersementRefuse(
                f"la pénalité est de {regle.penalite_rendez_vous} FCFA, pas {montant}."
            )
    else:
        if proforma is None or proforma.dossier != dossier.reference:
            raise VersementRefuse("aucune proforma de ce dossier à régler.")
        if dossier.etat not in (EtatDossier.ACCEPTEE, EtatDossier.PAYEE):
            raise VersementRefuse(
                f"dossier {dossier.reference} à l'état {dossier.etat} : une tranche se "
                "règle après l'acceptation de la proforma."
            )

    avant = situation_du_dossier(dossier, proforma, versements.pour(dossier.reference), regle)
    verifier_le_versement(avant, nature, montant)

    versement = VersementDuParcours(
        identifiant=f"ver-{uuid.uuid4().hex[:16]}",
        dossier=dossier.reference,
        nature=nature,
        montant=montant,
        canal=canal,
        reference_externe=reference_externe,
        proforma=proforma.numero if proforma and nature is NatureVersement.TRANCHE else None,
        constate_le=a_l_instant,
        constate_par=constate_par,
    )
    versements.enregistrer(versement)
    apres = situation_du_dossier(dossier, proforma, versements.pour(dossier.reference), regle)

    engagement = None
    if proforma is not None:
        engagement = engager_si_le_seuil_est_atteint(
            dossier,
            proforma,
            apres,
            boite=boite,
            a_l_instant=a_l_instant,
            reference_externe=reference_externe,
            slug=slug,
        )
    return ResultatDuVersement(
        versement=versement,
        situation=apres,
        dossier=engagement.dossier if engagement else dossier,
        engagement=engagement,
    )


class SuiteDeFraisDeTraitement:
    """Ce que des frais de traitement réglés par mobile money déclenchent : un versement.

    ⚠️ Comme `SuiteDeProforma`, ELLE NE LÈVE JAMAIS : l'argent est arrivé, un échec de
    la suite est journalisé pour un humain, et l'opérateur n'est pas renvoyé en boucle.
    """

    def __init__(self, *, dossiers, proformas, versements, regle, boite, journal) -> None:
        self._dossiers = dossiers
        self._proformas = proformas
        self._versements = versements
        self._regle = regle
        self._boite = boite
        self._journal = journal

    def encaisser(self, paiement, a_l_instant: datetime):
        from app.contextes.souscription.application.encaissement import Aboutissement
        from app.contextes.souscription.domaine.versements_du_parcours import (
            REGLE_SANS_FRAIS,
            VersementDejaRecu,
        )

        try:
            dossier = self._dossiers.lire(paiement.reference_reglee)
            proforma = proforma_a_regler(self._proformas.par_dossier(dossier.reference))
            resultat = recevoir_un_versement(
                dossier=dossier,
                proforma=proforma,
                versements=self._versements,
                regle=self._regle() or REGLE_SANS_FRAIS,
                nature=NatureVersement.FRAIS_DE_TRAITEMENT,
                montant=paiement.montant,
                canal=CanalDeReglement.MOBILE_MONEY,
                reference_externe=f"paiement:{paiement.identifiant}",
                constate_par="systeme",
                a_l_instant=a_l_instant,
                boite=self._boite,
                notifie=True,
            )
        except VersementDejaRecu:
            return Aboutissement(abouti=True, message="frais déjà comptés ; rejeu sans effet")
        except Exception as echec:  # noqa: BLE001 — voir l'en-tête
            motif = f"{type(echec).__name__}: {echec}"
            self._journal.ajouter(
                horodatage=a_l_instant,
                acteur="systeme",
                action="paiement.frais_non_comptes",
                objet_type="dossier",
                objet_id=paiement.reference_reglee,
                apres={"motif": motif},
            )
            return Aboutissement(abouti=False, message=motif)
        if resultat.engagement is not None and not resultat.engagement.rejeu:
            self._dossiers.enregistrer(resultat.dossier)
        return Aboutissement(abouti=True, message="frais de traitement reçus")

    def refuser(self, paiement, motif: str, a_l_instant: datetime) -> None:
        """Le client n'a pas réglé : rien ne bouge, la demande reste au cabinet."""
        self._journal.ajouter(
            horodatage=a_l_instant,
            acteur="systeme",
            action="paiement.frais_refuses",
            objet_type="dossier",
            objet_id=paiement.reference_reglee,
            apres={"motif": motif},
        )


def _recevoir_ce_qui_est_arrive(
    *,
    dossier: DossierCommercial,
    proforma: Proforma | None,
    versements: DepotVersements,
    regle: RegleDesVersements,
    nature: NatureVersement,
    montant: Decimal,
    canal: CanalDeReglement,
    reference_externe: str,
    constate_par: str,
    a_l_instant: datetime,
    boite: BoiteOuDeposer,
    slug: str,
) -> ResultatDuVersement:
    """L'argent de l'opérateur : tout est compté, ce qui déborde en trop-perçu."""
    avant = situation_du_dossier(dossier, proforma, versements.pour(dossier.reference), regle)
    parts: list[tuple[NatureVersement, Decimal, str]] = []
    if dossier.etat is EtatDossier.SANS_SUITE:
        parts.append((NatureVersement.TROP_PERCU, montant, reference_externe))
    elif nature is NatureVersement.FRAIS_DE_TRAITEMENT:
        # Le montant a été fixé par nous à l'initiation : il vaut, même si la règle a changé.
        parts.append((nature, montant, reference_externe))
    elif nature is NatureVersement.TRANCHE:
        absorbable = (
            proforma is not None
            and proforma.dossier == dossier.reference
            and dossier.etat in (EtatDossier.ACCEPTEE, EtatDossier.PAYEE)
            and avant.reste is not None
            and avant.reste > 0
        )
        if not absorbable:
            parts.append((NatureVersement.TROP_PERCU, montant, reference_externe))
        elif montant > avant.reste:
            parts.append((NatureVersement.TRANCHE, avant.reste, reference_externe))
            parts.append(
                (NatureVersement.TROP_PERCU, montant - avant.reste, f"{reference_externe}:excedent")
            )
        else:
            parts.append((NatureVersement.TRANCHE, montant, reference_externe))
    else:
        parts.append((nature, montant, reference_externe))

    premier: VersementDuParcours | None = None
    for nature_part, montant_part, reference_part in parts:
        versement = VersementDuParcours(
            identifiant=f"ver-{uuid.uuid4().hex[:16]}",
            dossier=dossier.reference,
            nature=nature_part,
            montant=montant_part,
            canal=canal,
            reference_externe=reference_part,
            proforma=(
                proforma.numero if proforma and nature_part is NatureVersement.TRANCHE else None
            ),
            constate_le=a_l_instant,
            constate_par=constate_par,
        )
        versements.enregistrer(versement)
        premier = premier or versement
    apres = situation_du_dossier(dossier, proforma, versements.pour(dossier.reference), regle)
    engagement = None
    if proforma is not None and dossier.etat is not EtatDossier.SANS_SUITE:
        engagement = engager_si_le_seuil_est_atteint(
            dossier,
            proforma,
            apres,
            boite=boite,
            a_l_instant=a_l_instant,
            reference_externe=reference_externe,
            slug=slug,
        )
    return ResultatDuVersement(
        versement=premier,
        situation=apres,
        dossier=engagement.dossier if engagement else dossier,
        engagement=engagement,
    )
