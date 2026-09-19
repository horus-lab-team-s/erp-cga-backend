"""Établir un devis à partir du catalogue.

─────────────────────────────────────────────────────────────────────────────────
LE DEVIS EST CALCULÉ UNE FOIS, PUIS FIGÉ

Les lignes reçoivent des montants recopiés du barème du jour, jamais des renvois
au catalogue. Voir l'en-tête de `devis.py` : c'est ce qui distingue un devis d'une
page de tarifs.

LA FORMULE SE CHOISIT SUR UN CHIFFRE D'AFFAIRES DÉCLARÉ, ET RIEN D'AUTRE

Le prospect annonce son chiffre d'affaires ; on lui propose la tranche
correspondante. Cette déclaration n'est pas vérifiée, et ne peut pas l'être — on
ne dispose d'aucune pièce à ce stade.

Ce que cela implique doit être dit clairement au cabinet : **la formule d'un devis
n'établit pas le régime fiscal**. Le régime se constate sur pièces, il relève du
contexte B, et il est historisé. Un adhérent qui a souscrit la formule « impôt
libératoire » et qui se révèle au réel paie la formule qui correspond à sa
situation réelle, après constat — pas celle qu'il avait cochée.

L'ADHÉSION EST UN ABONNEMENT ; LA PREMIÈRE ÉCHÉANCE SEULE EST ENCAISSÉE

Un client à qui l'on annonce 12 500 FCFA par mois et à qui l'on réclame 150 000 à
la souscription ne revient pas. La distinction est portée par
`Devis.montant_a_regler` et `Devis.abonnement_mensuel`, tous deux sérialisés, de
sorte que l'écran ne puisse pas les confondre.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.contextes.souscription.domaine.devis import (
    DUREE_VALIDITE,
    Devis,
    LigneDevis,
    Prospect,
)
from app.contextes.souscription.domaine.offre import (
    NatureService,
    Service,
    service_par_code,
)

__all__ = ["DemandeLigne", "DevisImpossible", "etablir_devis"]


class DevisImpossible(ValueError):
    """Le devis ne peut pas être établi tel que demandé."""


class DemandeLigne(BaseModel):
    """Ce que le prospect coche."""

    model_config = ConfigDict(frozen=True)

    service: str = Field(min_length=1)

    #: Pour les services à formules. À défaut, la tranche est déduite du chiffre
    #: d'affaires déclaré par le prospect.
    formule: str | None = None


def etablir_devis(
    *,
    reference: str,
    prospect: Prospect,
    demandes: Sequence[DemandeLigne],
    services: Sequence[Service],
    a_la_date: date,
) -> Devis:
    """Chiffre les prestations demandées au barème du jour.

    Une ligne dont le service est sur étude figure au devis **sans montant**. On
    ne l'écarte pas : le prospect a demandé cette prestation, elle doit apparaître
    dans ce qu'on lui propose, avec la mention qui explique pourquoi elle n'est pas
    chiffrée.
    """
    if not demandes:
        raise DevisImpossible("aucune prestation demandée : il n'y a rien à chiffrer.")

    lignes: list[LigneDevis] = []
    for demande in demandes:
        service = service_par_code(services, demande.service)
        lignes.append(_chiffrer(service, demande, prospect, a_la_date))

    return Devis(
        reference=reference,
        prospect=prospect,
        lignes=lignes,
        etabli_le=a_la_date,
        valide_jusqu_au=a_la_date + DUREE_VALIDITE,
    )


def _chiffrer(
    service: Service,
    demande: DemandeLigne,
    prospect: Prospect,
    a_la_date: date,
) -> LigneDevis:
    if service.nature == NatureService.SUR_ETUDE:
        return LigneDevis(
            service=service.code,
            libelle=service.libelle,
            montant=None,
            periodicite=service.periodicite,
            nature=service.nature,
            precision=(
                "Chiffré après examen du dossier. Les frais officiels dépendent de la "
                "forme juridique, du capital et du greffe compétent ; ils relèvent du "
                "référentiel normatif et ne sont pas encore saisis."
            ),
        )

    if not service.formules:
        tarif = service.tarif_au(a_la_date)
        return LigneDevis(
            service=service.code,
            libelle=service.libelle,
            montant=tarif.montant,
            periodicite=service.periodicite,
            nature=service.nature,
            precision=service.unite,
        )

    formule = _formule(service, demande, prospect)
    tarif = formule.tarif_au(a_la_date)
    return LigneDevis(
        service=service.code,
        libelle=f"{service.libelle} — {formule.libelle}",
        montant=tarif.montant,
        periodicite=service.periodicite,
        nature=service.nature,
        formule=formule.code,
        precision=(
            service.unite
            if tarif.montant is not None
            else "Sur devis : au-delà de ce seuil, l'entreprise sort du régime du "
            "centre agréé et la prestation change de nature."
        ),
    )


def _formule(service: Service, demande: DemandeLigne, prospect: Prospect):
    """La formule explicitement demandée, ou celle que le chiffre d'affaires commande."""
    if demande.formule is not None:
        trouvee = next((f for f in service.formules if f.code == demande.formule), None)
        if trouvee is None:
            raise DevisImpossible(
                f"formule « {demande.formule} » inconnue pour le service "
                f"{service.code}. Formules : "
                f"{', '.join(f.code for f in service.formules)}"
            )
        return trouvee

    if prospect.chiffre_affaires_declare is None:
        raise DevisImpossible(
            f"le service {service.code} est décliné par tranche de chiffre d'affaires. "
            "Indiquer un chiffre d'affaires, ou choisir une formule explicitement."
        )
    return service.formule_pour(Decimal(prospect.chiffre_affaires_declare))
