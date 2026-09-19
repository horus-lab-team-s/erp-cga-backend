#!/usr/bin/env bash
# Une instance PostgreSQL **à nous**, sans toucher au serveur du poste.
#
# ─────────────────────────────────────────────────────────────────────────────
# POURQUOI PAS LE SERVEUR DU SYSTÈME
#
# Créer un rôle sur le serveur du poste demande `sudo`, donc une décision qui
# n'appartient pas au projet : on modifierait la configuration d'une machine
# partagée pour faire tourner des tests. Cette instance-ci vit dans un
# répertoire temporaire, écoute sur un port dédié, et se jette.
#
# Elle sert au développement et aux tests. **La production n'a rien à voir**
# avec ce fichier : elle emploie une base administrée, dont l'adresse arrive par
# `CGA_URL_BASE_DE_DONNEES`.
#
# ⚠️ `--auth=trust` : aucun mot de passe. Acceptable parce que l'instance
# n'écoute que sur 127.0.0.1, sur un port non standard, et ne contient que des
# données de démonstration. Inacceptable partout ailleurs.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

PGBIN="${PGBIN:-/usr/lib/postgresql/16/bin}"
PGDATA="${PGDATA:-${TMPDIR:-/tmp}/cga-pgdata}"
PGPORT="${PGPORT:-55432}"
# Le répertoire de socket doit être **court** : au-delà de 107 octets,
# PostgreSQL refuse de démarrer, et le message ne dit pas que c'est la longueur.
PGSOCK="${PGSOCK:-/tmp/cga-pg}"

case "${1:-start}" in
  start)
    mkdir -p "$PGSOCK"
    if [ ! -d "$PGDATA/base" ]; then
      "$PGBIN/initdb" -D "$PGDATA" -U cga --auth=trust --encoding=UTF8 --locale=C >/dev/null
    fi
    "$PGBIN/pg_ctl" -D "$PGDATA" -l "$PGDATA/postgres.log" \
      -o "-p $PGPORT -k $PGSOCK -c listen_addresses=127.0.0.1" start
    sleep 1
    for base in cga_dev cga_test; do
      "$PGBIN/psql" -h 127.0.0.1 -p "$PGPORT" -U cga -d postgres \
        -tAc "select 1 from pg_database where datname='$base'" | grep -q 1 \
        || "$PGBIN/psql" -h 127.0.0.1 -p "$PGPORT" -U cga -d postgres \
             -c "CREATE DATABASE $base OWNER cga;" >/dev/null
    done
    echo "export CGA_URL_BASE_DE_DONNEES=postgresql+psycopg://cga@127.0.0.1:$PGPORT/cga_dev"
    echo "export CGA_URL_BASE_DE_DONNEES_TEST=postgresql+psycopg://cga@127.0.0.1:$PGPORT/cga_test"
    ;;
  stop)
    "$PGBIN/pg_ctl" -D "$PGDATA" stop
    ;;
  *)
    echo "usage : $0 [start|stop]" >&2
    exit 1
    ;;
esac
