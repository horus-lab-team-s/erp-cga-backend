"""Réalisation en mémoire du port `DepotEntreprises`.

Cible : PostgreSQL. Ce que cette réalisation apporte dès maintenant, c'est la
preuve que le port suffit — voir l'en-tête de `app/partage/depot_memoire.py`.

**Une règle métier est appliquée ici et nulle part ailleurs : on ne réécrit pas
l'histoire d'un dossier.** Enregistrer une entreprise dont les statuts datés
seraient en recul par rapport à ceux déjà connus effacerait le régime sous lequel
une facture ancienne a été contrôlée, et rendrait ce contrôle indéfendable. Le
dépôt refuse donc de perdre des périodes ; il accepte qu'on en ajoute.
"""

from __future__ import annotations

from app.contextes.portefeuille.domaine.entites import Entreprise
from app.contextes.portefeuille.domaine.temporel import ecart_d_histoire
from app.partage.depot_memoire import EntrepotMemoire

__all__ = ["DepotEntreprisesMemoire", "EntrepriseIntrouvable", "HistoireAmputee"]


class EntrepriseIntrouvable(LookupError):
    """Un NIU désigné et absent du portefeuille.

    Le message porte la liste des dossiers connus : sur un portefeuille de
    démonstration c'est utile, et en production la liste sera trop longue pour
    être rendue — d'où la troncature.
    """


class HistoireAmputee(ValueError):
    """L'enregistrement ferait disparaître des périodes déjà connues."""


class DepotEntreprisesMemoire:
    """Les dossiers du portefeuille, en mémoire, pour un locataire."""

    def __init__(self, locataire: str = "CGA-BRCG") -> None:
        self._entrepot: EntrepotMemoire[Entreprise] = EntrepotMemoire(
            locataire, cle=lambda entreprise: entreprise.niu
        )

    @classmethod
    def avec_demonstration(cls, locataire: str = "CGA-BRCG") -> DepotEntreprisesMemoire:
        from app.contextes.portefeuille.adaptateurs.sortant.donnees_demo import (
            PORTEFEUILLE_DEMO,
        )

        depot = cls(locataire)
        depot._entrepot.poser_tout(list(PORTEFEUILLE_DEMO.values()))
        return depot

    # ── Port `DepotEntreprises` ─────────────────────────────────────────────

    def lire(self, niu: str) -> Entreprise:
        entreprise = self._entrepot.prendre(niu)
        if entreprise is None:
            connus = sorted(e.niu for e in self._entrepot)
            apercu = ", ".join(connus[:5]) + ("…" if len(connus) > 5 else "")
            raise EntrepriseIntrouvable(
                f"NIU « {niu} » absent du portefeuille. {len(connus)} dossier(s) "
                f"connu(s) : {apercu}"
            )
        return entreprise

    def lister(self) -> list[Entreprise]:
        return sorted(self._entrepot.tout(), key=lambda e: e.denomination)

    def enregistrer(self, entreprise: Entreprise) -> None:
        ancienne = self._entrepot.prendre(entreprise.niu)
        if ancienne is not None:
            self._verifier_histoire(ancienne, entreprise)
        self._entrepot.poser(entreprise)

    # ── Garde-fou ───────────────────────────────────────────────────────────

    @staticmethod
    def _verifier_histoire(ancienne: Entreprise, nouvelle: Entreprise) -> None:
        """Refuse un enregistrement qui ne prolonge pas l'histoire du dossier.

        ⚠️ La règle vit dans le domaine, `ecart_d_histoire`, et les deux dépôts
        l'appellent. Elle y a été remontée le jour où le portefeuille a reçu sa
        première route d'écriture : chaque dépôt en portait jusque-là sa propre
        copie, qui ne comptait que les périodes. Deux copies d'un garde-fou
        divergent au premier correctif, et c'est la plus laxiste qui fait loi.
        """
        for quoi, avant, apres in (
            ("régimes", ancienne.regimes, nouvelle.regimes),
            ("rattachements", ancienne.rattachements, nouvelle.rattachements),
            ("adhésions", ancienne.adhesions, nouvelle.adhesions),
        ):
            ecart = ecart_d_histoire(avant, apres, quoi=f"{nouvelle.niu} · {quoi}")
            if ecart is not None:
                raise HistoireAmputee(ecart)
