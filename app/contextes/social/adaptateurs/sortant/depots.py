"""Les dépôts du contexte G — mémoire et PostgreSQL.

Les quatre réalisations dans un module parce qu'elles doivent rester d'accord sur
une chose : le filtre `a_la_date` d'un contrat. Deux modules séparés le laisseraient
diverger, et l'écart se verrait sur une paie — c'est-à-dire trop tard.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import and_, or_

from app.contextes.social.adaptateurs.sortant.tables import TableContrat, TableSalarie
from app.contextes.social.domaine.entites import Contrat, Salarie
from app.contextes.social.domaine.ports import SalarieIntrouvable
from app.infrastructure.depot_document import DepotDocument

__all__ = [
    "DepotContratsMemoire",
    "DepotContratsSql",
    "DepotSalariesMemoire",
    "DepotSalariesSql",
    "identifiant_de_contrat",
]


def identifiant_de_contrat(contrat: Contrat) -> str:
    """La clé d'un contrat : son salarié et sa date de début.

    Un salarié peut enchaîner plusieurs contrats chez le même employeur — un CDD
    puis un CDI est le cas courant. Prendre le seul matricule comme clé écraserait
    le premier, et la paie du mois précédent se recalculerait au nouveau salaire.
    """
    return f"{contrat.salarie}:{contrat.debut.isoformat()}"


def _en_vigueur(contrat: Contrat, jour: date) -> bool:
    return contrat.couvre(jour)


class DepotSalariesMemoire:
    def __init__(self, salaries: list[Salarie] | None = None) -> None:
        self._salaries: dict[str, Salarie] = {s.matricule: s for s in (salaries or [])}

    def lire(self, matricule: str) -> Salarie:
        try:
            return self._salaries[matricule]
        except KeyError as absence:
            raise SalarieIntrouvable(f"aucun salarié au matricule {matricule}") from absence

    def du_dossier(self, entreprise: str) -> list[Salarie]:
        retenus = [s for s in self._salaries.values() if s.entreprise == entreprise]
        return sorted(retenus, key=lambda s: (s.nom, s.prenom))

    def enregistrer(self, salarie: Salarie) -> None:
        self._salaries[salarie.matricule] = salarie


class DepotContratsMemoire:
    def __init__(self, contrats: list[Contrat] | None = None) -> None:
        self._contrats: dict[str, Contrat] = {
            identifiant_de_contrat(c): c for c in (contrats or [])
        }
        self._dossier: dict[str, str] = {}

    def rattacher(self, matricule: str, entreprise: str) -> None:
        """Associe un salarié à son dossier.

        En mémoire, le contrat ne porte pas le NIU — il porte le matricule. Le
        rattachement est donc tenu à part, là où la réalisation SQL le promeut en
        colonne. C'est la seule asymétrie entre les deux, et elle est ici plutôt
        que dans l'entité pour ne pas alourdir le domaine d'une donnée qui n'est
        qu'un index.
        """
        self._dossier[matricule] = entreprise

    def du_salarie(self, matricule: str) -> list[Contrat]:
        retenus = [c for c in self._contrats.values() if c.salarie == matricule]
        return sorted(retenus, key=lambda c: c.debut)

    def du_dossier(self, entreprise: str, a_la_date: date | None = None) -> list[Contrat]:
        matricules = {m for m, niu in self._dossier.items() if niu == entreprise}
        retenus = [c for c in self._contrats.values() if c.salarie in matricules]
        if a_la_date is not None:
            retenus = [c for c in retenus if _en_vigueur(c, a_la_date)]
        return sorted(retenus, key=lambda c: (c.salarie, c.debut))

    def enregistrer(self, contrat: Contrat) -> None:
        self._contrats[identifiant_de_contrat(contrat)] = contrat


class DepotSalariesSql(DepotDocument[Salarie]):
    """Réalisation PostgreSQL de `DepotSalaries`."""

    _table = TableSalarie
    _entite = Salarie

    def _cle(self, entite: Salarie) -> dict[str, Any]:
        return {"matricule": entite.matricule}

    def _colonnes(self, entite: Salarie) -> dict[str, Any]:
        return {
            "entreprise": entite.entreprise,
            "nom": entite.nom,
            "prenom": entite.prenom,
            "matricule_cnps": entite.matricule_cnps,
        }

    def lire(self, matricule: str) -> Salarie:
        trouve = self._premier(self._requete().where(TableSalarie.matricule == matricule))
        if trouve is None:
            raise SalarieIntrouvable(
                f"aucun salarié au matricule {matricule} chez le locataire {self.locataire}"
            )
        return trouve

    def du_dossier(self, entreprise: str) -> list[Salarie]:
        return self._tous(
            self._requete()
            .where(TableSalarie.entreprise == entreprise)
            .order_by(TableSalarie.nom, TableSalarie.prenom)
        )

    def enregistrer(self, salarie: Salarie) -> None:
        self._poser(salarie)


class DepotContratsSql(DepotDocument[Contrat]):
    """Réalisation PostgreSQL de `DepotContrats`.

    ⚠️ Le NIU du dossier est promu en colonne alors que l'entité ne le porte pas :
    il est repris du salarié à l'écriture. Sans lui, lister les contrats d'un
    dossier demanderait une jointure sur `salarie`, donc une requête que
    `DepotDocument` ne sait pas faire — ou un chargement complet du fichier du
    personnel à chaque paie.
    """

    _table = TableContrat
    _entite = Contrat

    def __init__(self, session, locataire: str, entreprise_par_salarie=None) -> None:
        super().__init__(session, locataire)
        self._entreprises: dict[str, str] = dict(entreprise_par_salarie or {})

    def rattacher(self, matricule: str, entreprise: str) -> None:
        self._entreprises[matricule] = entreprise

    def _cle(self, entite: Contrat) -> dict[str, Any]:
        return {"identifiant": identifiant_de_contrat(entite)}

    def _colonnes(self, entite: Contrat) -> dict[str, Any]:
        entreprise = self._entreprises.get(entite.salarie)
        if entreprise is None:
            raise ValueError(
                f"contrat de {entite.salarie} : dossier employeur inconnu. Enregistrer "
                "le salarié avant son contrat, ou appeler `rattacher`."
            )
        return {
            "salarie": entite.salarie,
            "entreprise": entreprise,
            "debut": entite.debut,
            "fin": entite.fin,
        }

    def du_salarie(self, matricule: str) -> list[Contrat]:
        return self._tous(
            self._requete()
            .where(TableContrat.salarie == matricule)
            .order_by(TableContrat.debut)
        )

    def du_dossier(self, entreprise: str, a_la_date: date | None = None) -> list[Contrat]:
        requete = self._requete().where(TableContrat.entreprise == entreprise)
        if a_la_date is not None:
            # `[debut, fin[` : la borne haute est exclue, ici comme partout.
            requete = requete.where(
                and_(
                    TableContrat.debut <= a_la_date,
                    or_(TableContrat.fin.is_(None), TableContrat.fin > a_la_date),
                )
            )
        return self._tous(requete.order_by(TableContrat.salarie, TableContrat.debut))

    def enregistrer(self, contrat: Contrat) -> None:
        self._poser(contrat)
