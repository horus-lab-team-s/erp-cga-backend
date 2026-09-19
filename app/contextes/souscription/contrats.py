"""Contrats de domaine du contexte M · Souscription.

**Deux surfaces publiques, pas une.** `contrats.py` n'expose que des entités
pures ; `api.py` expose en plus les cas d'usage.

─────────────────────────────────────────────────────────────────────────────────
QUI LIRA CE CONTEXTE

Le contexte J · Pilotage, pour compter les souscriptions et mesurer le taux de
transformation. Personne d'autre aujourd'hui.

En particulier **pas le portefeuille**. Une souscription n'est pas un dossier :
elle dit qu'un accès a été payé, pas que l'entreprise existe, ni qu'elle a un
régime, ni qu'elle a un exercice. La création du dossier reste un geste du chargé
de clientèle, sur pièces. Verser automatiquement chaque souscription au
portefeuille y ferait entrer des entreprises dont on ne sait rien — et le contexte
B tout entier est construit sur l'idée qu'on ne sait d'une entreprise que ce qu'on
a constaté.

CE CONTEXTE LIT, LUI, LE PORTEFEUILLE

Pour une seule question : ce NIU est-il déjà suivi par le cabinet ? Elle évite de
vendre deux fois le même suivi à la même entreprise.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from app.contextes.souscription.domaine.devis import (
    DUREE_VALIDITE,
    Devis,
    EtatDevis,
    LigneDevis,
    Prospect,
)
from app.contextes.souscription.domaine.offre import (
    Formule,
    NatureService,
    Periodicite,
    Service,
    ServiceIntrouvable,
    Tarif,
    TarifIndisponible,
    service_par_code,
)
from app.contextes.souscription.domaine.paiements import (
    DELAI_EXPIRATION,
    FENETRE_RAPPROCHEMENT,
    EvenementPaiement,
    MontantIncoherent,
    NaturePaiement,
    Paiement,
    StatutPaiement,
    StrategieRapprochement,
    rapprocher,
)
from app.contextes.souscription.domaine.souscriptions import (
    EtatSouscription,
    Souscription,
    TransitionRefusee,
)

__all__ = [
    "DELAI_EXPIRATION",
    "DUREE_VALIDITE",
    "FENETRE_RAPPROCHEMENT",
    "Devis",
    "EtatDevis",
    "EtatSouscription",
    "EvenementPaiement",
    "Formule",
    "LigneDevis",
    "MontantIncoherent",
    "NaturePaiement",
    "NatureService",
    "Paiement",
    "Periodicite",
    "Prospect",
    "Service",
    "ServiceIntrouvable",
    "Souscription",
    "StatutPaiement",
    "StrategieRapprochement",
    "Tarif",
    "TarifIndisponible",
    "TransitionRefusee",
    "rapprocher",
    "service_par_code",
]
