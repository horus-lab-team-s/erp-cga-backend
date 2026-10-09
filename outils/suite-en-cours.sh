#!/usr/bin/env bash
# Une suite de tests tourne-t-elle sur la base d'essai ?
#
# ─────────────────────────────────────────────────────────────────────────────
# POURQUOI CET OUTIL EXISTE
#
# `pgrep -f "python3 -m pytest"` ment, dans les deux sens. Il trouve sa propre
# ligne de commande quand on l'appelle depuis un shell dont la commande contient
# le motif, et il a répondu « terminée » sur une suite vivante puis « une suite
# tourne » sur une machine au repos. Trois décisions ont été prises sur ces
# réponses le 30 septembre 2026, les trois fausses.
#
# Le seul état qui ne ment pas est le **verrou consultatif** que
# `tests/conftest.py` pose pour toute la durée d'une session, et qui meurt avec
# sa connexion. Il est vrai si et seulement si une suite tient la base.
#
# ⚠️ Sans base joignable, on ne sait pas : on le dit, plutôt que de répondre
# « libre » et laisser lancer une seconde suite.
# ─────────────────────────────────────────────────────────────────────────────
set -u

URL="${CGA_URL_BASE_DE_DONNEES_TEST:-postgresql://cga@127.0.0.1:55432/cga_test}"
# psql ne comprend pas le préfixe SQLAlchemy.
URL="${URL/postgresql+psycopg:/postgresql:}"

verrous=$(psql "$URL" -tAc \
  "select count(*) from pg_locks where locktype='advisory'" 2>/dev/null)

if [ -z "$verrous" ]; then
  echo "INDÉTERMINÉ : base injoignable sur $URL"
  echo "  Ne pas lancer de suite avant de savoir : deux suites sur la même base"
  echo "  se détruisent, chaque cas y recréant le schéma entier."
  exit 2
fi

if [ "$verrous" -gt 0 ]; then
  echo "OCCUPÉE : une suite tient la base ($verrous verrou(s))."
  echo "  Attendre son verdict. Ne rien éditer d'ici là : pytest relit les"
  echo "  fichiers à la collecte, et une édition en cours produit des échecs"
  echo "  qui ne se reproduisent pas isolément."
  exit 1
fi

echo "LIBRE : aucune suite en cours."
exit 0
