# Backend CGA — image de production.
#
# ⚠️ SE CONSTRUIT DEPUIS LA RACINE DU DÉPÔT, PAS DEPUIS CE DOSSIER :
#
#     docker build -f Backend_erp_cga/Dockerfile -t cga-api .
#
# Parce que l'image a besoin de `Docs/referentiel/` et de `Contenu_vitrine/`,
# qui vivent à la racine. Ce ne sont pas des données de démonstration : le
# référentiel normatif porte les taux, les seuils et les règles, et sans lui
# l'application démarre puis refuse tout contrôle. `COPY` ne sort jamais du
# contexte de construction, donc le contexte est la racine.
#
# ─────────────────────────────────────────────────────────────────────────────
# DEUX ÉTAGES, ET LA RAISON N'EST PAS LA TAILLE
#
# L'étage de construction porte un compilateur et des en-têtes de développement ;
# l'étage final n'en a aucun. Gagner quelques centaines de mégaoctets est
# agréable ; retirer `gcc` d'une image exposée à Internet est ce qui compte —
# une exécution de code arbitraire y trouverait sinon de quoi compiler ce
# qu'elle veut.
#
# LES DÉPENDANCES S'INSTALLENT AVANT LE CODE
#
# Docker met chaque instruction en cache. Copier `pyproject.toml` seul,
# installer, puis copier le code : une correction d'une ligne ne réinstalle
# rien. L'ordre inverse — celui qui vient naturellement — réinstalle tout à
# chaque commit, et rend le déploiement assez lent pour qu'on cesse de déployer
# souvent.
#
# ⚠️ CE QUE CETTE IMAGE NE FAIT PAS
#
# Elle n'applique **pas** les migrations au démarrage. C'est délibéré : avec
# plusieurs instances, chacune lancerait `alembic upgrade` en même temps sur la
# même base. La migration est une étape de déploiement distincte, exécutée une
# fois — voir le service `migration` de `docker-compose.yml`.
# ─────────────────────────────────────────────────────────────────────────────

# ── Étage 1 · construction ──────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS construction

# Un environnement virtuel plutôt qu'une installation système : il se recopie
# d'un seul `COPY` dans l'étage final, sans traîner le gestionnaire de paquets.
ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv "$VIRTUAL_ENV"

WORKDIR /construction
COPY Backend_erp_cga/pyproject.toml Backend_erp_cga/README.md ./
# Le paquet doit exister pour que `pip install .` aboutisse. On ne copie ici
# qu'un squelette : copier le vrai code invaliderait le cache d'installation au
# premier changement d'une ligne.
RUN mkdir -p app && touch app/__init__.py && pip install .

# ── Étage 2 · exécution ─────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS execution

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    # Pas de fichiers `.pyc` : ils ne servent qu'au second démarrage, et un
    # conteneur n'en a pas.
    PYTHONDONTWRITEBYTECODE=1 \
    # Sortie non tamponnée. Sans cela, les journaux d'une application qui vient
    # de planter restent dans un tampon jamais vidé — exactement ceux qu'on
    # cherche.
    PYTHONUNBUFFERED=1

# ⚠️ Un utilisateur sans privilège. Le défaut de Docker est `root`, et une
# évasion de conteneur depuis un processus `root` a des conséquences que la même
# évasion depuis un compte ordinaire n'a pas.
RUN groupadd --system cga && useradd --system --gid cga --home /application cga

COPY --from=construction /opt/venv /opt/venv

WORKDIR /application
COPY --chown=cga:cga Backend_erp_cga/app ./app
COPY --chown=cga:cga Backend_erp_cga/alembic ./alembic
COPY --chown=cga:cga Backend_erp_cga/alembic.ini ./

# Le référentiel normatif et le contenu éditorial ne sont pas du code : ce sont
# des données versionnées à la racine du dépôt. La configuration les cherche par
# défaut trois niveaux au-dessus du module, convention qui n'a pas de sens dans
# une image — d'où ces chemins explicites.
ENV CGA_DOSSIER_REFERENTIEL=/application/donnees/referentiel \
    CGA_DOSSIER_CONTENU_VITRINE=/application/donnees/contenu_vitrine
COPY --chown=cga:cga Docs/referentiel /application/donnees/referentiel
COPY --chown=cga:cga Contenu_vitrine /application/donnees/contenu_vitrine

# ⚠️ Le point de montage des justificatifs est créé **ici**, avec son
# propriétaire. Docker initialise un volume nommé à partir des droits du chemin
# tel qu'il existe dans l'image : sans cette ligne, le volume appartiendrait à
# `root`, et le processus — qui tourne sans privilège — échouerait au premier
# dépôt de fichier. La panne n'apparaîtrait qu'à l'usage, jamais au démarrage.
ENV CGA_DOSSIER_FICHIERS=/application/fichiers
RUN mkdir -p /application/fichiers && chown cga:cga /application/fichiers
VOLUME ["/application/fichiers"]

USER cga
EXPOSE 8000

# La sonde interroge la base — voir `app/main.py`. Une instance dont la base est
# injoignable doit sortir de la rotation, et non répondre des `500`.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/sante', timeout=4).status == 200 else 1)"]

# Un seul processus par conteneur : la mise à l'échelle est l'affaire de
# l'orchestrateur, qui sait ce que porte la machine. Un `--workers 4` en dur
# quadruplerait la mémoire consommée sur un hôte à un cœur.
#
# ⚠️ `--forwarded-allow-ips *` fait confiance à l'en-tête `X-Forwarded-For`.
# Correct **derrière un mandataire inverse** qui la réécrit, et seulement là :
# exposer ce conteneur directement laisserait n'importe qui déclarer son adresse
# — celle que journalise l'audit et que lira la limitation de débit.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
