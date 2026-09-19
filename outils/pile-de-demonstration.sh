#!/usr/bin/env bash
# La pile de démonstration, montée d'une commande et dans un état connu.
#
# ─────────────────────────────────────────────────────────────────────────────
# POURQUOI CET OUTIL EXISTE
#
# Monter la pile demandait sept commandes dans le bon ordre, et l'une d'elles
# échoue silencieusement si la précédente n'a pas fini. Une démonstration qui
# commence par un serveur mal réveillé se lit comme un produit cassé, alors que
# c'est l'opérateur qui a été trop vite.
#
# Surtout : `amorcer()` REFUSE de rejouer sur une base qui contient déjà des
# comptes. C'est délibéré — un réamorçage écraserait des mots de passe réels par
# ceux de la démonstration, et personne ne s'en apercevrait avant qu'un
# collaborateur ne se retrouve dehors. La seule façon de repartir d'un jeu propre
# est donc de RECRÉER la base, et c'est ce que fait `neuve`.
#
# ⚠️ CE QUE `neuve` DÉTRUIT, ET CE QU'IL NE PEUT PAS DÉTRUIRE
#
# Il supprime la base `cga_dev` de l'instance locale, port 55432. Il refuse de
# s'exécuter contre toute autre adresse : la garde est explicite plus bas, parce
# qu'une commande de remise à zéro qui accepterait une URL de production est une
# perte de données qui attend son jour.
#
#     outils/pile-de-demonstration.sh neuve      base recréée, jeu de démo versé
#     outils/pile-de-demonstration.sh demarrer   monte ce qui manque, ne détruit rien
#     outils/pile-de-demonstration.sh etat       ce qui tourne, et sur quoi
#     outils/pile-de-demonstration.sh arreter    coupe API et front, garde la base
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACK="$RACINE/Backend_erp_cga"
FRONT="$RACINE/Frontend_erp_cga"

PGBIN="${PGBIN:-/usr/lib/postgresql/16/bin}"
PGPORT="${PGPORT:-55432}"
BASE="${BASE:-cga_dev}"
URL="postgresql+psycopg://cga@127.0.0.1:$PGPORT/$BASE"
PORT_API="${PORT_API:-8010}"
PORT_FRONT="${PORT_FRONT:-3011}"
JOURNAUX="${JOURNAUX:-/tmp/cga-demo}"

dire() { printf '\033[1m%s\033[0m\n' "$*"; }
detail() { printf '  %s\n' "$*"; }

pid_du_port() {
  # ⚠️ Le `|| true` final n'est pas de la superstition. Avec `pipefail`, un
  # `grep` qui ne trouve rien fait rendre 1 à tout le tuyau, et une affectation
  # « pid="$(pid_du_port …)" » hérite de ce statut : sous `set -e`, chercher un
  # port libre suffisait à interrompre le script, sans message et juste après
  # l'étape précédente. Un port libre est une réponse, pas une erreur.
  ss -ltnp 2>/dev/null | grep ":$1 " | grep -oP 'pid=\K[0-9]+' | head -1 || true
}

couper() {
  # ⚠️ Un `if` et non « test && { … } ». Sous `set -e`, une liste dont le test
  # échoue rend un statut non nul et arrête le script sans le moindre message :
  # couper un port déjà libre suffisait à interrompre toute la séquence.
  local pid; pid="$(pid_du_port "$1")"
  if [ -n "$pid" ]; then
    kill "$pid" 2>/dev/null || true
    sleep 2
    detail "port $1 libéré (pid $pid)"
  fi
  return 0
}

attendre() {
  # Attend qu'une adresse réponde. Sans cela, l'étape suivante frappe un serveur
  # qui n'écoute pas encore et conclut à une panne.
  local url="$1" essais="${2:-30}"
  for _ in $(seq 1 "$essais"); do
    curl -sf -o /dev/null "$url" && return 0
    sleep 1
  done
  return 1
}

postgres() {
  if ! ss -ltn 2>/dev/null | grep -q ":$PGPORT "; then
    dire "PostgreSQL local"
    bash "$BACK/outils/postgres-local.sh" start >/dev/null
    sleep 2
  fi
  detail "PostgreSQL écoute sur 127.0.0.1:$PGPORT"
}

recreer_la_base() {
  # ⚠️ LA GARDE. Une remise à zéro qui accepterait une adresse quelconque est une
  # perte de données qui attend son jour. On n'accepte que l'instance locale.
  if [ "$PGPORT" != "55432" ]; then
    echo "REFUS : remise à zéro demandée sur le port $PGPORT." >&2
    echo "Cet outil ne détruit que l'instance de démonstration, port 55432." >&2
    exit 2
  fi
  dire "Base $BASE recréée"
  detail "tout son contenu est perdu, c'est le but"
  "$PGBIN/psql" -h 127.0.0.1 -p "$PGPORT" -U cga -d postgres \
    -c "DROP DATABASE IF EXISTS $BASE;" >/dev/null
  "$PGBIN/psql" -h 127.0.0.1 -p "$PGPORT" -U cga -d postgres \
    -c "CREATE DATABASE $BASE OWNER cga;" >/dev/null
}

migrer_et_amorcer() {
  dire "Schéma et jeu de démonstration"
  ( cd "$BACK" \
    && CGA_PERSISTANCE=postgresql CGA_URL_BASE_DE_DONNEES="$URL" \
       python3 -m alembic upgrade head 2>&1 | tail -1 | sed 's/^/  /' )
  ( cd "$BACK" \
    && CGA_PERSISTANCE=postgresql CGA_URL_BASE_DE_DONNEES="$URL" CGA_MODE_DEMONSTRATION=true \
       python3 -c "from app.amorcage import amorcer; print('  ' + str(amorcer()))" )
}

demarrer_api() {
  if [ -n "$(pid_du_port "$PORT_API")" ]; then
    detail "API déjà en écoute sur $PORT_API"
    return 0
  fi
  dire "API"
  mkdir -p "$JOURNAUX"
  # ⚠️ `setsid --fork`, et AUCUN travail d'arrière-plan. C'est la seule forme qui
  # rende vraiment la main.
  #
  # `nohup … &` laisse le serveur fils du script : bash supprime le sous-shell
  # quand il ne contient qu'un travail d'arrière-plan, et le script attend ensuite
  # ce fils en sortant. Le symptôme est déroutant, parce que la pile fonctionne :
  # l'API répond, le front répond, et la commande ne se termine jamais. Sous un
  # tuyau, « … | tail », rien ne s'affiche même du tout, puisque le tuyau reste
  # ouvert tant qu'un descendant en tient l'écriture.
  #
  # `setsid --fork` fait le double saut : il se dédouble, le petit-fils est
  # rattaché à init, et `setsid` rend la main aussitôt. Le script n'a donc plus
  # aucun fils à attendre.
  ( cd "$BACK" && CGA_PERSISTANCE=postgresql CGA_URL_BASE_DE_DONNEES="$URL" \
      CGA_MODE_DEMONSTRATION=true \
      CGA_ADRESSE_PUBLIQUE_SITE="http://localhost:$PORT_FRONT" \
      CGA_ORIGINES_CORS="[\"http://localhost:$PORT_FRONT\"]" \
      setsid --fork python3 -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT_API" \
      > "$JOURNAUX/api.log" 2>&1 < /dev/null )
  attendre "http://127.0.0.1:$PORT_API/sante" \
    && detail "http://127.0.0.1:$PORT_API · journal : $JOURNAUX/api.log" \
    || { echo "  l'API n'a pas répondu, voir $JOURNAUX/api.log" >&2; exit 1; }
}

demarrer_front() {
  if [ -n "$(pid_du_port "$PORT_FRONT")" ]; then
    detail "front déjà en écoute sur $PORT_FRONT"
    return 0
  fi
  if [ ! -f "$FRONT/.next/standalone/Frontend_erp_cga/server.js" ]; then
    echo "  build autonome absent. Le produire : (cd Frontend_erp_cga && npx next build)" >&2
    return 1
  fi
  # ⚠️ NEXT NE COPIE PAS LES ACTIFS STATIQUES DANS LE BUILD AUTONOME.
  #
  # `next build` produit `.next/standalone` avec le serveur, mais laisse
  # `.next/static` et `public/` à côté. Sans cette copie, le serveur démarre, les
  # pages se rendent, la navigation fonctionne, et **tout arrive sans feuille de
  # style** : chaque actif répond 404, en silence. Le produit ressemble alors à du
  # HTML brut de 1995, et la première capture d'écran a mis le nez dessus.
  #
  # La copie est refaite à chaque démarrage plutôt que vérifiée : un build
  # postérieur remplace les statiques sans toucher au dossier autonome, et une
  # vérification d'existence laisserait passer des actifs périmés.
  local autonome="$FRONT/.next/standalone/Frontend_erp_cga"
  rm -rf "$autonome/.next/static"
  cp -r "$FRONT/.next/static" "$autonome/.next/static"
  [ -d "$FRONT/public" ] && { rm -rf "$autonome/public"; cp -r "$FRONT/public" "$autonome/public"; }
  dire "Front"
  detail "actifs statiques recopiés dans le build autonome"
  # ⚠️ HOSTNAME=0.0.0.0 est obligatoire. Avec 127.0.0.1, le serveur autonome
  # renvoie une redirection sur chaque page et la recette conclut à un produit
  # cassé alors qu'il fonctionne.
  ( cd "$FRONT/.next/standalone" && API_URL="http://127.0.0.1:$PORT_API" \
      PORT="$PORT_FRONT" HOSTNAME=0.0.0.0 \
      setsid --fork node Frontend_erp_cga/server.js \
      > "$JOURNAUX/front.log" 2>&1 < /dev/null )
  attendre "http://localhost:$PORT_FRONT/" \
    && detail "http://localhost:$PORT_FRONT · journal : $JOURNAUX/front.log" \
    || { echo "  le front n'a pas répondu, voir $JOURNAUX/front.log" >&2; exit 1; }
}

etat() {
  dire "État de la pile"
  for couple in "PostgreSQL:$PGPORT" "API:$PORT_API" "Front:$PORT_FRONT"; do
    local nom="${couple%%:*}" port="${couple##*:}" pid etat_port
    pid="$(pid_du_port "$port")"
    if [ -n "$pid" ]; then etat_port="en écoute (pid $pid)"; else etat_port="arrêté"; fi
    printf '  %-11s %-6s %s\n' "$nom" "$port" "$etat_port"
  done
  if [ -n "$(pid_du_port "$PORT_API")" ]; then
    local n
    n=$(curl -sf "http://127.0.0.1:$PORT_API/sante" | python3 -c \
      'import json,sys; e=json.load(sys.stdin); print("démonstration" if e.get("mode_demonstration") else "normal")' \
      2>/dev/null || echo "?")
    detail "mode : $n"
  fi
}

case "${1:-demarrer}" in
  neuve)
    postgres
    # ⚠️ Le FRONT aussi, pas seulement l'API. Un front laissé debout continue de
    # servir le build sous lequel il a démarré : on corrige, on reconstruit, on
    # relance `neuve`, et l'écran montre encore l'ancien code. Le symptôme est le
    # pire qui soit, parce qu'il fait douter de la correction elle-même.
    couper "$PORT_FRONT"
    couper "$PORT_API"
    recreer_la_base
    migrer_et_amorcer
    demarrer_api
    demarrer_front || true
    echo
    dire "Base neuve. Le limiteur de connexions est à zéro, la séance peut commencer."
    detail "Séance guidée : Docs/seance-de-demonstration.md"
    ;;
  demarrer)
    postgres
    demarrer_api
    demarrer_front || true
    echo
    etat
    ;;
  arreter)
    dire "Arrêt"
    couper "$PORT_FRONT"
    couper "$PORT_API"
    detail "PostgreSQL reste debout : outils/postgres-local.sh stop pour le couper aussi"
    ;;
  etat) etat ;;
  *)
    echo "usage : $0 [neuve|demarrer|arreter|etat]" >&2
    exit 1
    ;;
esac
