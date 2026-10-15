"""L'échéance de l'adhérent suit les fonds qu'il a confiés au cabinet (14 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI UN ABONNÉ

Sous mandat de paiement, l'adhérent remet au cabinet les fonds de sa TVA ou de sa
CNPS ; le cabinet reverse et porte la quittance. Ces gestes vivent à la
Souscription (la caisse, le mobile money), l'échéance aux Obligations, et le
graphe ne laisse ni l'une lire l'autre. Les deux faits traversent par la boîte
d'envoi :

* `FondsDeTiersReçus`    → l'échéance passe à FONDS_RECUS (« le cabinet a reçu vos
                            fonds et règle l'administration ») ;
* `FondsDeTiersReversés` → la quittance de l'administration devient la preuve de
                            l'échéance, validée par celui qui l'a portée : PAYÉE.

⚠️ LE MÊME JOURNAL QUE LES QUITTANCES DE L'ADHÉRENT. La seconde étape écrit
exactement ce qu'écrivent l'envoi d'une quittance puis son verdict : la carte de
l'adhérent, l'écran du cabinet et le dépôt de la déclaration la lisent sans rien
savoir du mandat.

⚠️ REJOUABLE : le relais garantit « au moins une fois ». Une entrée déjà écrite
pour le même appel ne s'écrit pas deux fois.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.orchestration.boite_d_envoi import EvenementSortant
from app.partage.horloge import maintenant

__all__ = ["abonne_fonds_recus", "abonne_fonds_reverses"]


def _ecrire(
    evenement: EvenementSortant,
    entrees: list[tuple[str, str, dict]],
    unite: Any,
    *,
    si_nouveau: Any = None,
) -> None:
    """`si_nouveau(boutique)` : appelé une fois, dans la même unité, si au moins une entrée
    était nouvelle. C'est ce qui garde le courriel de l'adhérent d'un double envoi quand le
    relais rejoue l'événement (15 octobre 2026)."""
    from app.contextes.obligations.adaptateurs.entrant.routes_espace_adherent import OBJET_PREUVE
    from app.contextes.transverse.api import unite_de_travail as unite_de_la_plateforme
    from app.infrastructure.config import configuration
    from app.partage.locataire import etabli

    niu = evenement.charge["niu"]
    locataire = configuration().locataire_par_defaut
    with etabli(locataire), (unite or unite_de_la_plateforme)(locataire) as boutique:
        deja = {
            (e.action, e.apres.get("appel"))
            for e in boutique.journal.lister(objet_type=OBJET_PREUVE, objet_id=niu)
        }
        nouvelles = 0
        for action, acteur, apres in entrees:
            if (action, evenement.cle) in deja:
                continue
            nouvelles += 1
            boutique.journal.ajouter(
                horodatage=maintenant(),
                acteur=acteur,
                action=action,
                objet_type=OBJET_PREUVE,
                objet_id=niu,
                apres={**apres, "appel": evenement.cle},
            )
        if nouvelles and si_nouveau is not None:
            si_nouveau(boutique)


def noter_les_fonds_recus(evenement: EvenementSortant, *, unite: Any = None) -> None:
    from app.contextes.obligations.adaptateurs.entrant.routes_espace_adherent import ACTION_FONDS

    c = evenement.charge
    _ecrire(
        evenement,
        [
            (
                ACTION_FONDS,
                "systeme",
                {
                    "obligation": c["code_obligation"],
                    "periode_debut": c["periode_debut"],
                    "montant": c["montant"],
                    "canal": c.get("canal", ""),
                },
            )
        ],
        unite,
        si_nouveau=lambda boutique: _prevenir_des_fonds(boutique, c),
    )


def _prevenir_des_fonds(boutique: Any, c: dict) -> None:
    """15 octobre 2026 : l'adhérent apprend que le cabinet a reçu ses fonds."""
    from datetime import date

    from app.contextes.obligations.domaine.echeances_adherent import libelle_de_periode
    from app.contextes.portefeuille.api import DepotEntreprisesSql, entreprises_en_memoire
    from app.contextes.transverse.api import prevenir_les_adherents, session_de_travail
    from app.infrastructure.config import configuration
    from app.partage.formats import ESPACE_FINE, montant
    from app.partage.locataire import courant

    session = session_de_travail()
    dossiers = (
        entreprises_en_memoire() if session is None else DepotEntreprisesSql(session, courant())
    )
    try:
        denomination = dossiers.lire(c["niu"]).denomination
    except Exception:  # noqa: BLE001 — un avis ne doit jamais faire échouer l'abonné
        denomination = c["niu"]
    prevenir_les_adherents(
        boutique,
        c["niu"],
        "adherent.fonds_recus",
        {
            "obligation": c.get("libelle") or c["code_obligation"],
            "periode": libelle_de_periode(
                date.fromisoformat(c["periode_debut"]), date.fromisoformat(c["periode_fin"])
            ),
            "montant": montant(Decimal(c["montant"])).replace(ESPACE_FINE, " "),
            "denomination": denomination,
            "lien": f"{configuration().adresse_publique_site.rstrip('/')}/fr/mon-espace/echeances",
        },
        jour=maintenant().date(),
    )


def noter_le_reversement(evenement: EvenementSortant, *, unite: Any = None) -> None:
    from datetime import date

    from app.contextes.obligations.adaptateurs.entrant.routes_espace_adherent import (
        ACTION_RECUE,
        ACTION_VERDICT,
    )
    from app.contextes.obligations.domaine.echeances_adherent import libelle_de_periode

    c = evenement.charge
    periode = libelle_de_periode(
        date.fromisoformat(c["periode_debut"]), date.fromisoformat(c["periode_fin"])
    )
    commun = {
        "obligation": c["code_obligation"],
        "periode_debut": c["periode_debut"],
        "piece": c["quittance"],
    }
    _ecrire(
        evenement,
        [
            (
                ACTION_RECUE,
                c.get("reverse_par", "systeme"),
                {
                    **commun,
                    "dossier": c["niu"],
                    "libelle": c["libelle"],
                    "periode": periode,
                    "periode_fin": c["periode_fin"],
                    "par": "le cabinet, sous mandat de paiement",
                },
            ),
            (ACTION_VERDICT, c.get("reverse_par", "systeme"), {**commun, "verdict": "VALIDEE"}),
        ],
        unite,
    )


def abonne_fonds_recus(unite: Any = None):
    """La fabrique inscrite au relais."""
    return lambda evenement: noter_les_fonds_recus(evenement, unite=unite)


def abonne_fonds_reverses(unite: Any = None):
    """La fabrique inscrite au relais."""
    return lambda evenement: noter_le_reversement(evenement, unite=unite)
