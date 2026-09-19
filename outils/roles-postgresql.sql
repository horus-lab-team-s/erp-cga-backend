-- ═══════════════════════════════════════════════════════════════════════════════
-- LES DEUX RÔLES POSTGRESQL, SANS LESQUELS LE CLOISONNEMENT NE S'APPLIQUE PAS
-- ═══════════════════════════════════════════════════════════════════════════════
--
-- À JOUER UNE FOIS PAR BASE, AVANT LA PREMIÈRE MIGRATION, AVEC UN SUPERUTILISATEUR.
--
--   psql "$URL_SUPERUTILISATEUR" \
--        -v mot_de_passe_migration="'…'" \
--        -v mot_de_passe_app="'…'" \
--        -f outils/roles-postgresql.sql
--
-- ⚠️ Les deux mots de passe passent par `-v`, jamais en dur ici : ce fichier est en
-- dépôt. Les guillemets simples à l'intérieur des guillemets doubles sont voulus,
-- `psql` substitue le texte littéral et PostgreSQL attend une chaîne SQL.
--
-- ───────────────────────────────────────────────────────────────────────────────
-- POURQUOI DEUX RÔLES ET NON UN SEUL
--
-- PostgreSQL n'applique pas une politique de sécurité au niveau des lignes à trois
-- catégories : le PROPRIÉTAIRE de la table, un rôle portant BYPASSRLS, et un
-- SUPERUTILISATEUR. Une application connectée avec l'un des trois est donc protégée
-- par des politiques qui ne s'appliquent jamais à elle.
--
-- Or les migrations doivent créer les tables, donc les posséder. Si l'application
-- emploie le même rôle que les migrations, elle possède les tables, et le
-- cloisonnement du multi-cabinet repose sur le seul filtre applicatif : le moindre
-- SQL textuel, le moindre rapport, la moindre requête d'administration le contourne.
--
-- D'où la séparation :
--
--   cga_migration  crée, possède, modifie le schéma.  Alembic seul l'emploie.
--   cga_app        lit et écrit les données.  Ne possède rien. Soumis aux politiques.
--
-- ⚠️ CE FICHIER NE PEUT PAS ÊTRE UNE MIGRATION ALEMBIC. Créer un rôle demande des
-- droits qu'Alembic n'a précisément pas dans cette architecture, et un rôle est un
-- objet de l'instance, pas de la base : il survit à un `downgrade`, et deux bases de
-- la même instance le partagent. Une migration décrit un instant de l'histoire d'un
-- schéma ; ceci décrit l'installation d'une instance.
--
-- COMMENT SAVOIR QUE C'EST FAIT
--
-- L'application le constate au démarrage et le publie : `GET /sante` rend
-- `"cloisonnement": "APPLIQUE"`. Tant qu'il rend autre chose, ce fichier n'a pas été
-- joué, ou l'a été à moitié. En production, l'application refuse de démarrer.
-- ───────────────────────────────────────────────────────────────────────────────

\set ON_ERROR_STOP on

-- ─── 1. Le rôle des migrations ────────────────────────────────────────────────
-- Il possède les tables. Il n'a PAS besoin de se connecter depuis l'application :
-- seule la commande de migration l'emploie.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cga_migration') THEN
        CREATE ROLE cga_migration LOGIN;
    END IF;
END
$$;

-- ─── 2. Le rôle de l'application ──────────────────────────────────────────────
-- ⚠️ NOBYPASSRLS et NOSUPERUSER sont écrits explicitement, alors qu'ils sont les
-- valeurs par défaut. Un défaut se change sans bruit ; une ligne écrite se relit et
-- se compare. C'est l'unique attribut qui, retiré par mégarde, annule silencieusement
-- tout le cloisonnement de la plateforme.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cga_app') THEN
        CREATE ROLE cga_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
    ELSE
        ALTER ROLE cga_app NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
    END IF;
END
$$;

ALTER ROLE cga_migration PASSWORD :mot_de_passe_migration;
ALTER ROLE cga_app PASSWORD :mot_de_passe_app;

-- ─── 3. Les droits de cga_app sur ce qui existe déjà ───────────────────────────
GRANT CONNECT ON DATABASE :"DBNAME" TO cga_app, cga_migration;
GRANT USAGE ON SCHEMA public TO cga_app;

-- ⚠️ `CREATE` sur le schéma, pour le rôle des migrations. Cette ligne a manqué à la
-- première rédaction de ce fichier, et la première migration a échoué sur
-- `permission denied for schema public`. Depuis **PostgreSQL 15**, le schéma `public`
-- n'accorde plus `CREATE` au pseudo-rôle `PUBLIC` : un script écrit pour la 14 crée
-- deux rôles parfaitement configurés dont l'un ne peut rien créer.
GRANT USAGE, CREATE ON SCHEMA public TO cga_migration;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO cga_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO cga_app;

-- ⚠️ ET SUR CE QUI N'EXISTE PAS ENCORE. Sans cette clause, chaque migration future
-- créerait une table à laquelle l'application n'aurait aucun accès, et la panne
-- n'apparaîtrait qu'à la première requête après le déploiement. `FOR ROLE
-- cga_migration` est indispensable : les droits par défaut se rattachent au rôle qui
-- crée l'objet, pas à celui qui écrit cette ligne.
ALTER DEFAULT PRIVILEGES FOR ROLE cga_migration IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO cga_app;
ALTER DEFAULT PRIVILEGES FOR ROLE cga_migration IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO cga_app;

-- ─── 4. Si les tables existent déjà et appartiennent à quelqu'un d'autre ───────
-- Cas d'une base installée avant l'introduction des deux rôles. Chaque table
-- cloisonnée doit changer de propriétaire, sans quoi l'ancien propriétaire continue
-- de contourner ses propres politiques.
DO $$
DECLARE
    nom text;
BEGIN
    FOR nom IN
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r'
          AND n.nspname = 'public'
          AND pg_get_userbyid(c.relowner) <> 'cga_migration'
    LOOP
        EXECUTE format('ALTER TABLE public.%I OWNER TO cga_migration', nom);
        RAISE NOTICE 'table % rendue à cga_migration', nom;
    END LOOP;
END
$$;

-- ─── 5. Le constat ─────────────────────────────────────────────────────────────
-- Ce que ce script doit avoir rendu vrai. Une ligne renvoyée ici est un défaut.
SELECT rolname AS "rôle en faute",
       rolsuper AS superutilisateur,
       rolbypassrls AS "contourne les politiques"
FROM pg_roles
WHERE rolname = 'cga_app'
  AND (rolsuper OR rolbypassrls);

SELECT c.relname AS "table cloisonnée sans politique"
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN information_schema.columns col
     ON col.table_name = c.relname
    AND col.table_schema = n.nspname
    AND col.column_name = 'locataire'
WHERE c.relkind = 'r'
  AND n.nspname = 'public'
  AND NOT EXISTS (
        SELECT 1 FROM pg_policy p
        WHERE p.polrelid = c.oid
          AND p.polname = 'cloisonnement_par_locataire'
  );
