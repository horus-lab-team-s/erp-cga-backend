"""Le socle des dépôts qui conservent une entité entière.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI UNE CLASSE PARTAGÉE, ALORS QUE LES DÉPÔTS DU CONTEXTE K N'EN ONT PAS

Ceux de K traduisent champ par champ, parce que leurs entités portent des données
que la sérialisation exclut délibérément — l'empreinte du mot de passe, le secret
du second facteur. Une écriture générique les perdrait.

Les entités des contextes métier n'ont rien de tel : elles se sérialisent
entièrement et se relisent entièrement. Écrire quatre fois la même boucle de
traduction n'y apporterait rien qu'une occasion de diverger.

⚠️ CE QUE CETTE CLASSE NE FAIT PAS, ET QUI RESTE À CHAQUE DÉPÔT

Elle ne connaît ni les colonnes promues, ni les requêtes métier. Chaque dépôt
déclare comment remplir ses colonnes à partir de l'entité, et écrit ses propres
lectures. C'est voulu : une abstraction qui devinerait les colonnes finirait par
les deviner mal.

LE FILTRE DE LOCATAIRE EST POSÉ ICI **AUSSI**

L'écouteur de session le pose déjà. Le reposer explicitement rend ces dépôts
corrects même construits sur une session ordinaire — c'est le défaut trouvé en
lançant les premiers tests sur PostgreSQL, et la leçon vaut pour tout ce qui
suit.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import Select, select
from sqlalchemy.orm import Session as SessionSql

from app.infrastructure.base_de_donnees import Cloisonne, Document

__all__ = ["DepotDocument", "en_json"]

E = TypeVar("E", bound=BaseModel)


def en_json(entite: BaseModel) -> dict[str, Any]:
    """L'entité, prête pour une colonne JSON.

    `mode="json"` convertit dates, décimaux et énumérations en types que le
    format sait porter. Le second passage par `json.loads` n'est pas de la
    superstition : il garantit que ce qui part en base est exactement ce qui
    reviendra, et fait échouer ici — plutôt qu'au `flush`, loin de l'appelant —
    tout ce qui ne se sérialise pas.
    """
    return json.loads(entite.model_dump_json())


class DepotDocument(Generic[E]):
    """Lecture et écriture d'entités conservées comme documents.

    Les sous-classes déclarent `_table`, `_entite`, et la façon de remplir les
    colonnes promues.
    """

    #: La table SQLAlchemy. Doit hériter de `Cloisonne` et de `Document`.
    _table: type[Cloisonne | Document]
    #: Le modèle Pydantic reconstruit à la lecture.
    _entite: type[E]

    def __init__(self, session: SessionSql, locataire: str) -> None:
        self._session = session
        self.locataire = locataire

    # ── À déclarer par chaque dépôt ─────────────────────────────────────────

    def _cle(self, entite: E) -> dict[str, Any]:
        """Les colonnes qui identifient la ligne — la clé primaire."""
        raise NotImplementedError

    def _colonnes(self, entite: E) -> dict[str, Any]:
        """Les colonnes promues, hors clé. Voir l'en-tête du mixin `Document`."""
        raise NotImplementedError

    # ── Ce que la classe fournit ────────────────────────────────────────────

    def _requete(self) -> Select:
        """Une requête filtrée sur le locataire — voir l'en-tête."""
        return select(self._table).where(self._table.locataire == self.locataire)

    def _lire(self, ligne: Any) -> E:
        return self._entite.model_validate(ligne.donnees)

    def _tous(self, requete: Select) -> list[E]:
        return [self._lire(ligne) for ligne in self._session.scalars(requete).all()]

    def _premier(self, requete: Select) -> E | None:
        ligne = self._session.scalars(requete).first()
        return None if ligne is None else self._lire(ligne)

    def _poser(self, entite: E) -> None:
        """Insère ou remplace, colonnes promues comprises.

        Le document **et** les colonnes sont réécrits ensemble : une colonne
        promue qui ne suivrait pas le document ferait mentir toutes les requêtes
        qui s'appuient dessus, sans que la lecture d'une entité ne le montre
        jamais.
        """
        cle = self._cle(entite)
        # Forme dictionnaire plutôt que tuple : la clé d'écriture en compte
        # cinq, et un tuple mal ordonné chercherait silencieusement la mauvaise
        # ligne.
        ligne = self._session.get(self._table, cle)
        if ligne is None:
            ligne = self._table(**cle)
            self._session.add(ligne)
        elif getattr(ligne, "locataire", None) != self.locataire:
            raise ValueError(
                f"{self._table.__tablename__} {cle} appartient à un autre locataire. "
                "Le filtre de session protège les lectures ; les écritures se "
                "contrôlent à l'entrée."
            )
        ligne.locataire = self.locataire
        for nom, valeur in self._colonnes(entite).items():
            setattr(ligne, nom, valeur)
        ligne.donnees = en_json(entite)
        self._session.flush()
