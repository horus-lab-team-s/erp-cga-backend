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
#     outils/pile-de-demonstration.sh arreter    coupe API et fronts, garde la base
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

BACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RACINE="$(cd "$BACK/.." && pwd)"

# ⚠️ LES INTERFACES NE SONT PLUS DANS CE DÉPÔT.
#
# Depuis la scission, la console et la vitrine sont deux dépôts voisins. Ce
# script les cherche à côté du dépôt du serveur, et accepte d'être détrompé par
# CGA_RACINE_CONSOLE et CGA_RACINE_VITRINE — c'est la même convention que les
# trois outils de couverture, pour qu'il n'y ait qu'un réglage à retenir.
#
# La console est obligatoire : sans elle, il n'y a rien à démontrer. La vitrine
# est facultative et le script le dit au lieu d'échouer, parce qu'une démonstration
# du produit comptable n'a pas toujours besoin du site public à côté.
FRONT="${CGA_RACINE_CONSOLE:-$RACINE/erp-cga-console}"
[ -d "$FRONT" ] || FRONT="$RACINE/Frontend_erp_cga"
VITRINE="${CGA_RACINE_VITRINE:-$RACINE/erp-cga-vitrine}"

PGBIN="${PGBIN:-/usr/lib/postgresql/16/bin}"
PGPORT="${PGPORT:-55432}"
BASE="${BASE:-cga_dev}"
URL="postgresql+psycopg://cga@127.0.0.1:$PGPORT/$BASE"
PORT_API="${PORT_API:-8010}"
PORT_FRONT="${PORT_FRONT:-3011}"
PORT_VITRINE="${PORT_VITRINE:-3012}"
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
      CGA_ORIGINES_CORS="[\"http://localhost:$PORT_FRONT\",\"http://localhost:$PORT_VITRINE\"]" \
      setsid --fork python3 -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT_API" \
      > "$JOURNAUX/api.log" 2>&1 < /dev/null )
  attendre "http://127.0.0.1:$PORT_API/sante" \
    && detail "http://127.0.0.1:$PORT_API · journal : $JOURNAUX/api.log" \
    || { echo "  l'API n'a pas répondu, voir $JOURNAUX/api.log" >&2; exit 1; }
}

dossier_autonome() {
  # ⚠️ NEXT NE MET PAS `server.js` TOUJOURS AU MÊME ENDROIT.
  #
  # Il le pose à la racine de `.next/standalone` quand le projet est seul, et
  # dans un sous-dossier portant le nom du projet quand une racine de traçage
  # est détectée au-dessus (c'était le cas dans le monodépôt : le fichier
  # s'appelait `.next/standalone/Frontend_erp_cga/server.js`). Après la scission,
  # les deux formes existent selon la machine et selon ce qui traîne au-dessus
  # du dépôt. Chercher les deux coûte deux lignes ; coder l'une des deux en dur
  # coûte une démonstration qui refuse de démarrer sans dire pourquoi.
  local racine="$1"
  local candidat
  for candidat in "$racine/.next/standalone" "$racine/.next/standalone/$(basename "$racine")"; do
    [ -f "$candidat/server.js" ] && { printf '%s' "$candidat"; return 0; }
  done
  return 1
}

demarrer_un_front() {
  # $1 nom affiché · $2 racine du dépôt · $3 port · $4 « facultatif » ou vide
  local nom="$1" racine="$2" port="$3" facultatif="${4:-}"
  local journal="$JOURNAUX/$(printf '%s' "$nom" | tr 'A-Z ' 'a-z-').log"

  if [ -n "$(pid_du_port "$port")" ]; then
    detail "$nom déjà en écoute sur $port"
    return 0
  fi
  if [ ! -d "$racine" ]; then
    if [ -n "$facultatif" ]; then
      detail "$nom : dépôt absent de $racine, étape sautée"
      return 0
    fi
    echo "  dépôt du $nom introuvable : $racine" >&2
    echo "  le désigner par CGA_RACINE_CONSOLE, ou le cloner à côté du serveur." >&2
    return 1
  fi

  local autonome
  if ! autonome="$(dossier_autonome "$racine")"; then
    if [ -n "$facultatif" ]; then
      detail "$nom : build autonome absent, étape sautée"
      return 0
    fi
    echo "  build autonome absent. Le produire : (cd $racine && npx next build)" >&2
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
  rm -rf "$autonome/.next/static"
  cp -r "$racine/.next/static" "$autonome/.next/static"
  [ -d "$racine/public" ] && { rm -rf "$autonome/public"; cp -r "$racine/public" "$autonome/public"; }
  dire "$nom"
  detail "actifs statiques recopiés dans le build autonome"
  # ⚠️ HOSTNAME=0.0.0.0 est obligatoire. Avec 127.0.0.1, le serveur autonome
  # renvoie une redirection sur chaque page et la recette conclut à un produit
  # cassé alors qu'il fonctionne.
  ( cd "$autonome" && API_URL="http://127.0.0.1:$PORT_API" \
      PORT="$port" HOSTNAME=0.0.0.0 \
      setsid --fork node server.js \
      > "$journal" 2>&1 < /dev/null )
  attendre "http://localhost:$port/" \
    && detail "http://localhost:$port · journal : $journal" \
    || { echo "  le $nom n'a pas répondu, voir $journal" >&2; exit 1; }
}

demarrer_front() {
  demarrer_un_front "front" "$FRONT" "$PORT_FRONT"
  demarrer_un_front "vitrine" "$VITRINE" "$PORT_VITRINE" facultatif
}

etat() {
  dire "État de la pile"
  for couple in "PostgreSQL:$PGPORT" "API:$PORT_API" "Front:$PORT_FRONT" "Vitrine:$PORT_VITRINE"; do
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
    couper "$PORT_VITRINE"
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
    couper "$PORT_VITRINE"
    couper "$PORT_API"
    detail "PostgreSQL reste debout : outils/postgres-local.sh stop pour le couper aussi"
    ;;
  etat) etat ;;
  *)
    echo "usage : $0 [neuve|demarrer|arreter|etat]" >&2
    exit 1
    ;;
esac
