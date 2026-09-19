"""Le cloisonnement s'applique-t-il vraiment ? La question, et sa réponse.

─────────────────────────────────────────────────────────────────────────────────
LE PIÈGE QUI A ÉTÉ NOMMÉ DÈS LE CHANTIER 2, ET QUI EST RESTÉ OUVERT DEPUIS

PostgreSQL applique une politique de sécurité au niveau des lignes à **tout le
monde sauf trois** :

* le **propriétaire** de la table, sauf si elle est déclarée `FORCE ROW LEVEL
  SECURITY` ;
* un rôle portant l'attribut `BYPASSRLS` ;
* un **superutilisateur**, toujours.

Une application qui se connecte avec l'un des trois est donc protégée par une
politique qui ne s'applique jamais à elle. Le cloisonnement repose alors sur le
seul filtre ORM, que le SQL textuel contourne.

⚠️ **ET LE TEST QUI LE VÉRIFIERAIT PASSERAIT AU VERT EN NE TESTANT RIEN**, puisqu'il
s'exécuterait avec le même rôle. C'est ce qui rend ce défaut particulier : il ne
se signale pas, il ne casse rien, et la suite reste verte.

CE QUE CE MODULE FAIT, ET POURQUOI C'EST UN DIAGNOSTIC ET NON UNE CORRECTION

Il **demande à la base** si le rôle courant est soumis aux politiques, et rend un
verdict. Il ne corrige rien : créer un rôle demande des droits que l'application
n'a pas, et ne doit pas avoir.

La correction est une opération d'exploitation, décrite dans
`outils/roles-postgresql.sql`. Ce module est ce qui empêche de l'oublier, en
transformant une ligne de documentation en **fait constaté au démarrage**.

POURQUOI IL NE SUFFIT PAS DE L'ÉCRIRE DANS UN README

Parce qu'un README n'échoue pas. Une installation faite un vendredi soir avec le
rôle `postgres` fonctionne parfaitement, sert des requêtes, passe la recette, et
cloisonne les données de deux cabinets par un seul filtre applicatif. Personne ne
s'en aperçoit avant qu'un adhérent voie la facture d'un autre.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.infrastructure.securite_lignes import NOM_POLITIQUE

__all__ = [
    "DiagnosticDuRole",
    "EtatDuCloisonnement",
    "diagnostiquer",
]


class EtatDuCloisonnement(StrEnum):
    """Ce que la base répond, et ce que cela veut dire pour les données."""

    #: Le rôle courant est soumis aux politiques. C'est l'état attendu.
    APPLIQUE = "APPLIQUE"
    #: Le rôle est superutilisateur. Il contourne tout, sans exception possible.
    CONTOURNE_SUPERUTILISATEUR = "CONTOURNE_SUPERUTILISATEUR"
    #: Le rôle porte `BYPASSRLS`. Attribut explicite, donc décision explicite,
    #: mais rarement voulue pour un rôle applicatif.
    CONTOURNE_BYPASSRLS = "CONTOURNE_BYPASSRLS"
    #: Le rôle possède les tables. C'est le cas le plus fréquent et le plus
    #: silencieux : il arrive quand migrations et application partagent un rôle.
    CONTOURNE_PROPRIETAIRE = "CONTOURNE_PROPRIETAIRE"
    #: Des tables cloisonnées n'ont aucune politique. Le rôle importe peu :
    #: il n'y a rien à appliquer.
    POLITIQUES_ABSENTES = "POLITIQUES_ABSENTES"


class DiagnosticDuRole(BaseModel):
    """Le verdict, et de quoi le comprendre sans rouvrir psql."""

    model_config = ConfigDict(frozen=True)

    etat: EtatDuCloisonnement
    role: str
    #: Les tables cloisonnées que ce rôle possède. Vide dans le cas attendu.
    tables_possedees: tuple[str, ...] = ()
    #: Les tables cloisonnées sans politique. Vide dans le cas attendu.
    tables_sans_politique: tuple[str, ...] = ()

    @property
    def applique(self) -> bool:
        return self.etat is EtatDuCloisonnement.APPLIQUE

    @property
    def explication(self) -> str:
        """Ce qu'on écrit au journal, et ce qui doit suffire à agir.

        Un message qui dit « cloisonnement non appliqué » sans dire pourquoi
        envoie l'exploitant lire le code. Chaque cause a sa phrase et son geste.
        """
        if self.etat is EtatDuCloisonnement.APPLIQUE:
            return f"le rôle « {self.role} » est soumis aux politiques de cloisonnement"
        if self.etat is EtatDuCloisonnement.CONTOURNE_SUPERUTILISATEUR:
            return (
                f"le rôle « {self.role} » est superutilisateur : il contourne toutes "
                "les politiques, sans exception possible. L'application doit se "
                "connecter avec un rôle ordinaire."
            )
        if self.etat is EtatDuCloisonnement.CONTOURNE_BYPASSRLS:
            return (
                f"le rôle « {self.role} » porte l'attribut BYPASSRLS. Le retirer : "
                f"ALTER ROLE {self.role} NOBYPASSRLS."
            )
        if self.etat is EtatDuCloisonnement.CONTOURNE_PROPRIETAIRE:
            return (
                f"le rôle « {self.role} » possède {len(self.tables_possedees)} table(s) "
                "cloisonnée(s) et contourne donc leurs politiques. Voir "
                "outils/roles-postgresql.sql : les migrations et l'application "
                "doivent employer deux rôles distincts."
            )
        return (
            f"{len(self.tables_sans_politique)} table(s) cloisonnée(s) sans politique : "
            f"{', '.join(self.tables_sans_politique[:5])}. Les migrations n'ont pas "
            "toutes été jouées, ou une table a été créée sans poser la sienne."
        )


def diagnostiquer(connexion: Connection) -> DiagnosticDuRole:
    """Demande à la base si le rôle courant est soumis aux politiques.

    ─────────────────────────────────────────────────────────────────────────
    L'ORDRE DES VÉRIFICATIONS VA DU PLUS GRAVE AU PLUS RÉPARABLE

    Un superutilisateur contourne tout, y compris `FORCE ROW LEVEL SECURITY` :
    aucune configuration ne le corrige, seul un changement de rôle le fait. On le
    dit en premier parce que c'est le seul cas où lire la suite ne sert à rien.

    ⚠️ UNE TABLE SANS POLITIQUE COMPTE COMME UN CONTOURNEMENT

    Elle est vérifiée avant la propriété, parce qu'un rôle parfaitement
    configuré sur une table sans politique n'est pas mieux protégé qu'un
    propriétaire. La cause diffère, la conséquence est la même.
    ─────────────────────────────────────────────────────────────────────────
    """
    role = connexion.execute(text("SELECT current_user")).scalar_one()
    attributs = connexion.execute(
        text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    ).one()
    if attributs.rolsuper:
        return DiagnosticDuRole(etat=EtatDuCloisonnement.CONTOURNE_SUPERUTILISATEUR, role=role)
    if attributs.rolbypassrls:
        return DiagnosticDuRole(etat=EtatDuCloisonnement.CONTOURNE_BYPASSRLS, role=role)

    sans_politique = tuple(
        ligne.nom
        for ligne in connexion.execute(
            text(
                """
                SELECT c.relname AS nom
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN information_schema.columns col
                     ON col.table_name = c.relname
                    AND col.table_schema = n.nspname
                    AND col.column_name = 'locataire'
                WHERE c.relkind = 'r'
                  AND n.nspname = current_schema()
                  AND NOT EXISTS (
                        SELECT 1 FROM pg_policy p
                        WHERE p.polrelid = c.oid AND p.polname = :politique
                  )
                ORDER BY c.relname
                """
            ),
            {"politique": NOM_POLITIQUE},
        )
    )
    if sans_politique:
        return DiagnosticDuRole(
            etat=EtatDuCloisonnement.POLITIQUES_ABSENTES,
            role=role,
            tables_sans_politique=sans_politique,
        )

    possedees = tuple(
        ligne.nom
        for ligne in connexion.execute(
            text(
                """
                SELECT c.relname AS nom
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN information_schema.columns col
                     ON col.table_name = c.relname
                    AND col.table_schema = n.nspname
                    AND col.column_name = 'locataire'
                WHERE c.relkind = 'r'
                  AND n.nspname = current_schema()
                  AND pg_get_userbyid(c.relowner) = current_user
                  -- ⚠️ `relforcerowsecurity` ferme le contournement du
                  -- propriétaire. Une table qui le porte n'est donc pas un
                  -- problème, même possédée par le rôle applicatif.
                  AND NOT c.relforcerowsecurity
                ORDER BY c.relname
                """
            )
        )
    )
    if possedees:
        return DiagnosticDuRole(
            etat=EtatDuCloisonnement.CONTOURNE_PROPRIETAIRE,
            role=role,
            tables_possedees=possedees,
        )
    return DiagnosticDuRole(etat=EtatDuCloisonnement.APPLIQUE, role=role)
