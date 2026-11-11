"""Ce qu'un règlement d'honoraires déclenche : la période passe à « réglée ».

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CETTE SUITE EXISTE (14 octobre 2026)

L'adhérent règle une période d'honoraires par mobile money depuis son espace. La
notification de l'opérateur arrive par la route commune à tous les paiements ;
c'est `paiement.nature` qui la mène ici, comme elle mène une proforma à
l'ouverture du dossier. Ni la notification ni la réconciliation ne savent que
cette suite existe.

Ce qu'elle fait : écrire le règlement de la période, au canal MOBILE_MONEY, avec
la référence de l'opérateur. C'est ce règlement que lisent l'espace de
l'adhérent et la caisse : la ligne passe d'elle-même à « réglée », sans que
personne ne saisisse rien. C'est le rapprochement automatique.

⚠️ ELLE NE LÈVE JAMAIS (voir `SuiteDuPaiement`). Une période déjà réglée par un
autre canal, entre la demande et la notification, est un DOUBLE règlement : il
est journalisé pour que la caisse rembourse ou reporte, l'argent reste encaissé.

⚠️ REJOUABLE : la même notification remise deux fois trouve la période réglée
par CE paiement (même référence) et s'arrête sans rien écrire.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime

from app.contextes.souscription.application.encaissement import Aboutissement
from app.contextes.souscription.domaine.honoraires import (
    CanalDeReglement,
    PeriodeDejaReglee,
    ReglementDHonoraires,
)
from app.orchestration.boite_d_envoi import deposer

__all__ = ["NOM_HONORAIRES_REGLES", "SuiteDHonoraires", "constater_le_reglement"]

#: 15 octobre 2026 : un règlement d'honoraires constaté traverse vers la Comptabilité,
#: qui propose l'écriture dans les livres de l'adhérent. Personne ne la ressaisit.
NOM_HONORAIRES_REGLES = "HonorairesRéglés"


def constater_le_reglement(reglements, boite, reglement: ReglementDHonoraires) -> None:
    """Écrit le règlement, puis le publie. **Le seul chemin**, guichet comme mobile money.

    ⚠️ Lève `PeriodeDejaReglee` avant toute publication : un double règlement ne doit pas
    proposer deux écritures.
    """
    reglements.enregistrer(reglement)
    deposer(
        boite,
        f"hr-{reglement.identifiant}",
        NOM_HONORAIRES_REGLES,
        reglement.identifiant,
        {
            "identifiant": reglement.identifiant,
            "niu": reglement.niu,
            "periode": reglement.periode.isoformat(),
            "montant": str(reglement.montant),
            "canal": reglement.canal.value,
            "reference": reglement.reference_externe,
            "constate_le": reglement.constate_le.isoformat(),
        },
        reglement.constate_le,
    )


class SuiteDHonoraires:
    def __init__(self, *, reglements, journal, boite) -> None:
        self._reglements = reglements
        self._journal = journal
        self._boite = boite

    def encaisser(self, paiement, a_l_instant: datetime) -> Aboutissement:
        niu = paiement.reference_reglee
        try:
            periode = date.fromisoformat(paiement.echeance or "")
        except ValueError:
            return self._echec(paiement, "paiement sans période lisible", a_l_instant)

        reference = paiement.reference_externe or paiement.identifiant
        deja = next((r for r in self._reglements.pour(niu) if r.periode == periode), None)
        if deja is not None:
            if deja.reference_externe == reference:
                return Aboutissement(abouti=True, message="période déjà réglée par ce paiement")
            return self._echec(
                paiement,
                f"DOUBLE RÈGLEMENT : la période du {periode:%d/%m/%Y} était déjà réglée "
                f"({deja.canal.value}, {deja.reference_externe}). L'encaissement est "
                "acquis ; la caisse doit rembourser ou reporter.",
                a_l_instant,
            )
        try:
            constater_le_reglement(
                self._reglements,
                self._boite,
                ReglementDHonoraires(
                    identifiant=f"RH-{paiement.identifiant}",
                    niu=niu,
                    periode=periode,
                    montant=paiement.montant,
                    canal=CanalDeReglement.MOBILE_MONEY,
                    reference_externe=reference,
                    constate_le=a_l_instant,
                    constate_par="systeme",
                ),
            )
        except PeriodeDejaReglee as course:
            return self._echec(paiement, f"DOUBLE RÈGLEMENT : {course}", a_l_instant)
        except Exception as echec:  # noqa: BLE001 — voir l'en-tête : on ne lève pas
            return self._echec(paiement, f"{type(echec).__name__}: {echec}", a_l_instant)
        return Aboutissement(abouti=True, message=f"honoraires du {periode:%d/%m/%Y} réglés")

    def refuser(self, paiement, motif: str, a_l_instant: datetime) -> None:
        """L'adhérent n'a pas réglé : l'échéance reste due, il peut recommencer."""
        self._journal.ajouter(
            horodatage=a_l_instant,
            acteur="systeme",
            action="paiement.honoraires_refuse",
            objet_type="honoraires",
            objet_id=f"{paiement.reference_reglee}/{paiement.echeance}",
            apres={"motif": motif},
        )

    def _echec(self, paiement, motif: str, a_l_instant: datetime) -> Aboutissement:
        self._journal.ajouter(
            horodatage=a_l_instant,
            acteur="systeme",
            action="paiement.honoraires_a_traiter",
            objet_type="honoraires",
            objet_id=f"{paiement.reference_reglee}/{paiement.echeance}",
            apres={"motif": motif, "paiement": paiement.identifiant},
        )
        return Aboutissement(abouti=False, message=motif)
