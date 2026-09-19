"""Le dépôt PostgreSQL du contexte B."""

from __future__ import annotations

from typing import Any

from app.contextes.portefeuille.adaptateurs.sortant.depot_entreprises_memoire import (
    EntrepriseIntrouvable,
    HistoireAmputee,
)
from app.contextes.portefeuille.adaptateurs.sortant.tables import TableEntreprise
from app.contextes.portefeuille.domaine.entites import Entreprise
from app.contextes.portefeuille.domaine.temporel import ecart_d_histoire
from app.infrastructure.depot_document import DepotDocument

__all__ = ["DepotEntreprisesSql"]


class DepotEntreprisesSql(DepotDocument[Entreprise]):
    """Réalisation de `DepotEntreprises`.

    ⚠️ Le contrôle d'histoire amputée est **conservé** : `enregistrer` refuse un
    dossier qui porterait moins de périodes de régime, de rattachement ou
    d'adhésion que celui déjà en base.

    C'est le garde-fou du contexte B, et il compte plus encore en base qu'en
    mémoire : une écriture partielle venue d'un import ou d'un écran mal câblé
    effacerait une histoire qu'aucune sauvegarde ne distinguerait d'une
    modification légitime.
    """

    _table = TableEntreprise
    _entite = Entreprise

    def _cle(self, entite: Entreprise) -> dict[str, Any]:
        return {"niu": entite.niu}

    def _colonnes(self, entite: Entreprise) -> dict[str, Any]:
        return {
            "denomination": entite.denomination,
            "forme_juridique": entite.forme_juridique.value,
            "date_creation": entite.date_creation,
            "activite": entite.activite,
            "siege": entite.siege,
        }

    def lire(self, niu: str) -> Entreprise:
        trouve = self._premier(self._requete().where(TableEntreprise.niu == niu))
        if trouve is None:
            raise EntrepriseIntrouvable(
                f"aucun dossier au NIU {niu} chez le locataire {self.locataire}"
            )
        return trouve

    def lister(self) -> list[Entreprise]:
        return self._tous(self._requete().order_by(TableEntreprise.denomination))

    def enregistrer(self, entreprise: Entreprise) -> None:
        self._verifier_histoire(entreprise)
        self._poser(entreprise)

    def _verifier_histoire(self, entreprise: Entreprise) -> None:
        """Refuse une mise à jour qui ne prolonge pas l'histoire du dossier.

        La même règle qu'en mémoire, **la même fonction** : voir `ecart_d_histoire`.
        """
        ancien = self._premier(self._requete().where(TableEntreprise.niu == entreprise.niu))
        if ancien is None:
            return
        for quoi, avant, apres in (
            ("régimes", ancien.regimes, entreprise.regimes),
            ("rattachements", ancien.rattachements, entreprise.rattachements),
            ("adhésions", ancien.adhesions, entreprise.adhesions),
        ):
            ecart = ecart_d_histoire(avant, apres, quoi=f"{entreprise.niu} · {quoi}")
            if ecart is not None:
                raise HistoireAmputee(ecart)
