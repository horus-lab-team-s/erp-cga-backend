"""La sécurité au niveau des lignes : la ceinture, sous les bretelles de l'ORM.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI UNE SECONDE GARANTIE ALORS QUE LA PREMIÈRE FONCTIONNE

Le filtre ORM de `base_de_donnees.py` est solide : il est greffé sur l'exécution, il
n'est jamais écrit, donc jamais oublié. Il a une faille, et sa propre docstring la
signale : **le SQL textuel y échappe.**

Il n'existe aujourd'hui aucune lecture par SQL textuel sur une table cloisonnée — le
seul `text()` qui les touche est un verrou consultatif. C'est précisément le bon moment
pour poser la garantie : avant qu'une soit écrite, pas après.

La sécurité au niveau des lignes déplace le filtre **dans la base**. Elle s'applique à
toute requête, quelle qu'en soit l'origine : l'ORM, un `text()`, un rapport, une console
d'administration ouverte un dimanche pour dépanner.

LE PIÈGE QUI ANNULE TOUT

**Le propriétaire d'une table contourne ses politiques par défaut.** Une application qui
se connecte avec le rôle propriétaire est donc protégée par une politique qui ne
s'applique jamais à elle — et le test qui la vérifierait, exécuté avec le même rôle,
passerait au vert en ne testant rien.

Deux rôles sont donc nécessaires en exploitation :

    cga_migration   propriétaire des tables, joue Alembic, contourne les politiques
    cga_app         non propriétaire, droits de manipulation seulement, y est soumis

`FORCE ROW LEVEL SECURITY` existe pour soumettre aussi le propriétaire, et c'est
tentant parce que cela évite le second rôle. On ne l'emploie pas : il empêcherait aussi
les migrations et les exports d'administration de voir les données, ce qui déplace le
problème au lieu de le résoudre.

LA VARIABLE DE SESSION EST POSÉE PAR TRANSACTION, JAMAIS PAR CONNEXION

`set_config(..., true)` limite la portée à la transaction en cours. C'est essentiel avec
un bac de connexions : une variable posée pour la connexion survivrait à la requête et
serait héritée par la suivante, servie à un autre client. Le défaut serait intermittent,
dépendrait de la charge, et ne se reproduirait jamais en développement.

⚠️ On emploie `set_config` et non `SET LOCAL` parce que `SET` n'accepte pas de paramètre
lié. Concaténer un nom de locataire dans du SQL serait une injection en attente.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import MetaData, Table

__all__ = [
    "COLONNE_CLOISON",
    "NOM_POLITIQUE",
    "VARIABLE_SESSION",
    "instructions_activation",
    "instructions_activation_pour",
    "instructions_desactivation",
    "tables_cloisonnees",
]

#: La colonne qui porte le locataire. Le mixin `Cloisonne` la pose ; cette constante la
#: nomme pour que la génération du SQL ne la devine pas.
COLONNE_CLOISON = "locataire"

#: Le nom donné à la politique. Un nom unique et parlant, parce qu'il apparaîtra dans les
#: messages d'erreur de PostgreSQL et dans les inspections de schéma.
NOM_POLITIQUE = "cloisonnement_par_locataire"

#: La variable de session lue par la politique. Le point est obligatoire : PostgreSQL
#: n'accepte de variables personnalisées que préfixées.
VARIABLE_SESSION = "app.locataire"


def tables_cloisonnees(metadonnees: MetaData) -> list[Table]:
    """Les tables qui portent la colonne de cloisonnement, par ordre de nom.

    Dérivées des métadonnées plutôt qu'énumérées à la main. Une liste écrite serait
    juste le jour où on l'écrit, et fausse à la table suivante — et la table oubliée
    serait précisément celle qui fuit.
    """
    return sorted(
        (table for table in metadonnees.tables.values() if COLONNE_CLOISON in table.columns),
        key=lambda table: table.name,
    )


def instructions_activation(metadonnees: MetaData) -> Iterator[str]:
    """Le SQL qui active la sécurité au niveau des lignes sur chaque table cloisonnée.

    La politique porte `USING` **et** `WITH CHECK` :

    * `USING` filtre ce qu'on lit et ce qu'on peut modifier ou supprimer ;
    * `WITH CHECK` interdit d'**écrire** une ligne pour un autre locataire.

    Sans le second, une insertion mal formée créerait une ligne au nom d'un autre client,
    que son propriétaire légitime verrait apparaître sans l'avoir demandée. C'est plus
    rare qu'une lecture fautive, et plus difficile à défaire.

    La comparaison emploie `current_setting(..., true)`, dont le second argument dit de
    rendre NULL plutôt que de lever quand la variable n'est pas posée. Une comparaison à
    NULL n'est jamais vraie : **une session qui n'a pas dit son locataire ne voit rien.**
    C'est le bon défaut, et c'est l'inverse de celui qu'on obtiendrait sans ce soin.
    """
    for table in tables_cloisonnees(metadonnees):
        yield from instructions_activation_pour(table.name)


def instructions_activation_pour(nom: str) -> Iterator[str]:
    """Le même SQL, pour une seule table nommée.

    La migration d'origine dérive ses instructions des métadonnées, et couvre donc
    tout ce qui existait le jour où elle a été jouée. **Une table ajoutée ensuite
    n'est pas couverte** : la migration a déjà tourné, et Alembic ne la rejoue pas.

    Chaque migration qui crée une table cloisonnée doit donc poser sa politique
    elle-même, ici en une ligne. La faire recopier le SQL serait l'occasion de le
    recopier de travers, et une politique sans `WITH CHECK` ne se voit pas : elle
    protège les lectures et laisse écrire chez le voisin.
    """
    yield f"ALTER TABLE {nom} ENABLE ROW LEVEL SECURITY"
    yield f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON {nom}"
    yield (
        f"CREATE POLICY {NOM_POLITIQUE} ON {nom}"
        f" USING ({COLONNE_CLOISON} = current_setting('{VARIABLE_SESSION}', true))"
        f" WITH CHECK ({COLONNE_CLOISON} = current_setting('{VARIABLE_SESSION}', true))"
    )


def instructions_desactivation(metadonnees: MetaData) -> Iterator[str]:
    """Le retour en arrière, pour la migration descendante.

    On retire la politique avant de désactiver : l'ordre inverse laisse une politique
    orpheline que la réactivation suivante fera échouer sur un doublon de nom.
    """
    for table in tables_cloisonnees(metadonnees):
        yield f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON {table.name}"
        yield f"ALTER TABLE {table.name} DISABLE ROW LEVEL SECURITY"
