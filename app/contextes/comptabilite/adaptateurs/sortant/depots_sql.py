"""Les dépôts PostgreSQL du contexte E."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.contextes.comptabilite.adaptateurs.sortant.depot_ecritures_memoire import (
    EcritureFigee,
    EcritureIntrouvable,
)
from app.contextes.comptabilite.adaptateurs.sortant.tables import (
    TableEcriture,
    TablePlanImputation,
)
from app.contextes.comptabilite.domaine.entites import EcritureComptable, EtatEcriture
from app.contextes.comptabilite.domaine.imputation import PlanImputation
from app.infrastructure.depot_document import DepotDocument, en_json

__all__ = ["DepotEcrituresSql", "DepotPlanImputationSql"]


class DepotEcrituresSql(DepotDocument[EcritureComptable]):
    """Réalisation de `DepotEcritures`, pour **un dossier**.

    ─────────────────────────────────────────────────────────────────────────
    L'ENTREPRISE EST DANS LA CLÉ, ENFIN

    `2026/AC/000042` n'est unique qu'à l'intérieur d'un dossier — défaut relevé
    au premier branchement des adaptateurs, contourné jusqu'ici par un dépôt en
    mémoire ouvert *pour un dossier*. La clé primaire porte désormais le
    locataire et l'entreprise, et la question posée à l'époque est close.

    Le dépôt reste construit pour un dossier, parce que l'interface du port l'est
    — `lire("2026/AC/000042")` ne dit rien de l'entreprise. C'est le dépôt qui
    complète, et il ne peut plus se tromper.

    TROIS GARANTIES DU PORT, ET CE QUI EN EST TENU

    **Une écriture validée est immuable** — tenue, par le même contrôle qu'en
    mémoire.

    **La numérotation est continue** — tenue par la clé primaire : deux écritures
    ne peuvent pas porter le même numéro.

    **Même en accès concurrent** — ⚠️ partiellement. `prochain_numero` lit le
    maximum et ajoute un ; deux transactions concurrentes obtiendraient le même,
    et la seconde échouerait sur la clé primaire au lieu de prendre le suivant.
    C'est correct — rien de faux n'est écrit — mais l'appelant doit recommencer.
    Une vraie séquence par journal supprimerait ce cas ; sur le volume d'un
    cabinet, la collision est théorique.
    ─────────────────────────────────────────────────────────────────────────
    """

    _table = TableEcriture
    _entite = EcritureComptable

    def __init__(self, session, locataire: str, entreprise: str) -> None:
        super().__init__(session, locataire)
        self.entreprise = entreprise

    def _cle(self, entite: EcritureComptable) -> dict[str, Any]:
        return {
            "locataire": self.locataire,
            "entreprise": self.entreprise,
            "exercice": entite.exercice,
            "journal": entite.journal,
            "numero": entite.numero,
        }

    def _colonnes(self, entite: EcritureComptable) -> dict[str, Any]:
        return {
            "date_operation": entite.date_operation,
            "libelle": entite.libelle,
            "etat": entite.etat.value,
            "type": entite.type.value,
            "piece_justificative": entite.piece_justificative,
        }

    def _du_dossier(self):
        return self._requete().where(TableEcriture.entreprise == self.entreprise)

    def enregistrer(self, ecriture: EcritureComptable) -> None:
        ancienne = self._session.get(self._table, self._cle(ecriture))
        if ancienne is not None and ancienne.etat == EtatEcriture.VALIDEE.value:
            raise EcritureFigee(
                f"{ecriture.cle} : cette écriture est validée et ne se modifie plus. "
                "La corriger se fait par contre-passation, qui laisse sa propre trace "
                "et conserve l'originale."
            )
        self._poser(ecriture)

    def lire(self, cle: str) -> EcritureComptable:
        exercice, journal, numero = cle.split("/")
        trouvee = self._premier(
            self._du_dossier().where(
                TableEcriture.exercice == exercice,
                TableEcriture.journal == journal,
                TableEcriture.numero == int(numero),
            )
        )
        if trouvee is None:
            raise EcritureIntrouvable(
                f"écriture « {cle} » absente du registre de {self.entreprise}. Une "
                "écriture désignée par sa clé et introuvable est une rupture de la "
                "piste d'audit."
            )
        return trouvee

    def lister(self, exercice: str, journal: str | None = None) -> list[EcritureComptable]:
        """Dans l'ordre du numéro par journal — celui du journal papier, et celui
        qu'un vérificateur attend. Trier par date ferait apparaître des sauts de
        numéro qui n'existent pas."""
        requete = (
            self._du_dossier()
            .where(TableEcriture.exercice == exercice)
            .order_by(TableEcriture.journal, TableEcriture.numero)
        )
        if journal is not None:
            requete = requete.where(TableEcriture.journal == journal)
        return self._tous(requete)

    def prochain_numero(self, exercice: str, journal: str) -> int:
        """⚠️ Maximum plus un — voir l'en-tête pour la limite en concurrence."""
        maximum = self._session.scalar(
            select(func.max(TableEcriture.numero)).where(
                TableEcriture.locataire == self.locataire,
                TableEcriture.entreprise == self.entreprise,
                TableEcriture.exercice == exercice,
                TableEcriture.journal == journal,
            )
        )
        return (maximum or 0) + 1

    def toutes(self, exercice: str | None = None) -> list[EcritureComptable]:
        requete = self._du_dossier().order_by(
            TableEcriture.exercice, TableEcriture.journal, TableEcriture.numero
        )
        if exercice is not None:
            requete = requete.where(TableEcriture.exercice == exercice)
        return self._tous(requete)

    def exercices(self) -> list[str]:
        lignes = self._session.scalars(
            select(TableEcriture.exercice)
            .where(
                TableEcriture.locataire == self.locataire,
                TableEcriture.entreprise == self.entreprise,
            )
            .distinct()
            .order_by(TableEcriture.exercice)
        ).all()
        return list(lignes)


class DepotPlanImputationSql(DepotDocument[PlanImputation]):
    """Le plan d'imputation d'un dossier, conservé entier.

    ⚠️ `charger` rend le plan **par défaut** quand le dossier n'en a pas.
    C'est ce que fait déjà la réalisation en mémoire, et il faut le dire : un
    dossier sans plan propre n'est pas un dossier sans imputation possible — il
    suit les règles générales du cabinet. Lever ici bloquerait la comptabilité de
    tout nouvel adhérent.
    """

    _table = TablePlanImputation
    _entite = PlanImputation

    def __init__(self, session, locataire: str, par_defaut: PlanImputation) -> None:
        super().__init__(session, locataire)
        self._par_defaut = par_defaut

    def _cle(self, entite: PlanImputation) -> dict[str, Any]:
        raise NotImplementedError(
            "un plan d'imputation s'identifie par son dossier, que l'entité ne porte "
            "pas. Passer par `enregistrer_plan(entreprise, plan)`."
        )

    def _colonnes(self, entite: PlanImputation) -> dict[str, Any]:
        return {}

    def charger(self, entreprise: str) -> PlanImputation:
        trouve = self._premier(
            self._requete().where(TablePlanImputation.entreprise == entreprise)
        )
        return trouve if trouve is not None else self._par_defaut

    def enregistrer_plan(self, entreprise: str, plan: PlanImputation) -> None:
        cle = {"locataire": self.locataire, "entreprise": entreprise}
        ligne = self._session.get(TablePlanImputation, cle)
        if ligne is None:
            ligne = TablePlanImputation(**cle)
            self._session.add(ligne)
        ligne.donnees = en_json(plan)
        self._session.flush()
