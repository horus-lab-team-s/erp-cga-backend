"""Le filet : repêcher les encaissements dont la notification ne nous est jamais parvenue.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI UN FILET, PUISQUE LE PRESTATAIRE NOTIFIE

Parce qu'il ne notifie pas toujours. L'adresse de rappel est injoignable pendant
un redéploiement, une notification se perd, l'opérateur intermédiaire n'a jamais
transmis. Dans tous ces cas l'abonné a bel et bien été débité, et **rien ne
bouge de notre côté**.

C'est le scénario qui coûte le plus cher : l'argent est parti, le service n'est
pas ouvert, le client attend. Au bout d'un moment il repaie — et c'est là que le
double-encaissement apparaît, non pas par un défaut de notre code, mais par notre
silence.

La réconciliation renverse la charge : **c'est nous qui appelons**. Le
prestataire ne signant pas ses notifications entrantes, l'appel sortant vers une
adresse connue est de toute façon la seule source de vérité — ce qui fait de ce
filet autre chose qu'une rustine.

TROIS ISSUES PAR PAIEMENT EXAMINÉ

Le prestataire dit **réussi** → on encaisse et l'on active, exactement comme si la
notification était arrivée : c'est `appliquer_evenement` qui est appelée, la même
fonction, pour que les deux chemins ne puissent pas diverger.

Le prestataire dit **refusé** → on rejette.

Le prestataire **ne sait pas** → on ne fait rien, et l'on réessaiera. Ne rien
faire est ici la bonne réponse : inventer un statut serait pire que d'attendre.

LA PÉREMPTION EST TRAITÉE AVANT L'INTERROGATION

Un paiement de plus de vingt-quatre heures est périmé sans qu'on interroge : on
n'a rien à gagner à demander le statut d'une opération que l'abonné a abandonnée
la veille, et l'on encombrerait le prestataire d'appels inutiles à chaque passage.

UN DÉLAI MINIMAL AVANT LE PREMIER EXAMEN

Cinq minutes. Interroger une opération engagée il y a dix secondes ne rend rien
d'utile — l'abonné n'a pas encore saisi son code — et fait payer un appel réseau
pour rien. Le filet sert les cas où la notification **n'est pas venue**, pas ceux
où elle n'est **pas encore** venue.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, computed_field

from app.contextes.souscription.application.encaissement import (
    ResultatEncaissement,
    SuiteDuPaiement,
    appliquer_evenement,
)
from app.contextes.souscription.domaine.paiements import (
    NaturePaiement,
    StrategieRapprochement,
)
from app.contextes.souscription.domaine.ports import (
    DepotPaiements,
    FournisseurPaiement,
)
from app.contextes.transverse.api import JournalAudit

__all__ = ["DELAI_MINIMAL", "RapportReconciliation", "reconcilier"]

#: Cinq minutes — voir l'en-tête.
DELAI_MINIMAL = timedelta(minutes=5)


class RapportReconciliation(BaseModel):
    """Ce qu'un passage du filet a produit."""

    model_config = ConfigDict(frozen=True)

    examines: int
    valides: int
    rejetes: int
    perimes: int
    #: Le prestataire n'a pas su répondre. Reverront au prochain passage.
    indetermines: int
    resultats: list[ResultatEncaissement] = []

    @computed_field
    @property
    def repeches(self) -> int:
        """Les encaissements que la notification n'avait pas apportés.

        C'est le chiffre qui dit si le filet sert à quelque chose. S'il reste
        durablement à zéro, tant mieux ; s'il monte, c'est que l'adresse de rappel
        a un problème, et il faut le savoir.
        """
        return self.valides + self.rejetes

    @property
    def resume(self) -> str:
        """Ce que l'ordonnanceur inscrit au compte rendu du passage.

        ⚠️ `repeches` y figure en premier : c'est le seul nombre qui dit si le
        filet a servi, et c'est celui qu'un exploitant cherche quand il se demande
        si l'adresse de rappel fonctionne encore.
        """
        return (
            f"{self.repeches} repêché(s) sur {self.examines} examiné(s) — "
            f"{self.valides} validé(s), {self.rejetes} rejeté(s), "
            f"{self.perimes} périmé(s), {self.indetermines} indéterminé(s)"
        )


def reconcilier(
    *,
    fournisseur: FournisseurPaiement,
    paiements: DepotPaiements,
    suites: Mapping[NaturePaiement, SuiteDuPaiement],
    journal: JournalAudit,
    a_l_instant: datetime,
    delai_minimal: timedelta = DELAI_MINIMAL,
) -> RapportReconciliation:
    """Interroge le prestataire sur chaque encaissement resté en attente.

    ⚠️ **La même table de suites que la notification entrante.** Les deux chemins
    doivent produire le même effet : c'est déjà la raison pour laquelle ils
    partagent `appliquer_evenement`, et la partager à moitié serait pire que pas
    du tout — l'un activerait un tenant là où l'autre n'activerait rien.
    """
    valides = rejetes = perimes = indetermines = 0
    resultats: list[ResultatEncaissement] = []
    candidats = paiements.en_attente(depuis=None)
    examines = 0

    for paiement in candidats:
        # Voir l'en-tête : la péremption d'abord, sans appel réseau.
        if paiement.perime(a_l_instant):
            expire = paiement.expirer(a_l_instant)
            paiements.enregistrer(expire)
            # La péremption est un refus comme un autre du point de vue de
            # l'objet réglé : personne n'a payé. C'est donc la suite qui décide
            # ce que cela veut dire pour elle.
            suite_perimee = suites.get(expire.nature)
            if suite_perimee is not None:
                suite_perimee.refuser(
                    expire, expire.motif or "péremption", a_l_instant
                )
            journal.ajouter(
                horodatage=a_l_instant,
                acteur="systeme",
                action="paiement.perime",
                objet_type="paiement",
                objet_id=expire.identifiant,
                apres={"initie_le": expire.initie_le},
            )
            perimes += 1
            continue

        # ⚠️ `a_interroger` et non le seul délai minimal : le filet est désormais
        # jeté toutes les cinq minutes par l'ordonnanceur, et interroger chaque
        # paiement en attente à chaque passage ferait 288 appels au prestataire
        # pour une opération abandonnée. L'espacement double et plafonne.
        if not paiement.a_interroger(a_l_instant, delai_minimal):
            continue

        examines += 1
        paiements.enregistrer(paiement.apres_verification(a_l_instant))

        evenement = fournisseur.interroger_statut(
            cle_idempotence=paiement.cle_idempotence,
            reference_externe=paiement.reference_externe,
        )
        if evenement is None:
            indetermines += 1
            continue

        resultat = appliquer_evenement(
            paiements.lire(paiement.identifiant),
            evenement,
            # Le rapprochement n'a pas eu lieu : on connaissait déjà le paiement,
            # puisque c'est nous qui avons demandé son statut. La stratégie
            # enregistrée est donc celle de l'identifiant, qui est la vérité.
            strategie=StrategieRapprochement.IDENTIFIANT,
            paiements=paiements,
            # ⚠️ La suite est choisie ici comme elle l'est pour une notification
            # entrante : par la nature du paiement. Les deux chemins doivent
            # produire le même effet, faute de quoi l'un activerait là où l'autre
            # rejette — c'est déjà la raison d'être d'`appliquer_evenement`.
            suite=suites[paiement.nature],
            journal=journal,
            a_l_instant=a_l_instant,
        )
        resultats.append(resultat)
        if evenement.reussi:
            valides += 1
        else:
            rejetes += 1

    return RapportReconciliation(
        examines=examines,
        valides=valides,
        rejetes=rejetes,
        perimes=perimes,
        indetermines=indetermines,
        resultats=resultats,
    )
