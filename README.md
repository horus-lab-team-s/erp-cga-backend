# Backend — Plateforme CGA Broad Range Consulting Group

FastAPI, monolithe modulaire. Un package Python par contexte borné.

## Démarrer

```bash
cd Backend_erp_cga
python -m venv .venv
.venv/Scripts/activate          # Windows ;  source .venv/bin/activate sous Unix
pip install -e ".[dev]"

uvicorn app.main:app --reload   # http://127.0.0.1:8000/docs
pytest                          # 103 tests
ruff check .
```

## Ce qui est implémenté

| Contexte | Package | État |
|---|---|---|
| **A · Référentiel normatif** | `app/contexts/referentiel/` | Paramètres légaux datés, lecture à une date, statut de validation |
| **D · Conformité** | `app/contexts/conformite/` | Moteur de règles, rapport, conséquence fiscale chiffrée, verdict E02 |
| B, C, E → K | — | À faire, voir `Docs/architecture/06-phases-et-sequencement.md` |

Le référentiel est aujourd'hui un dossier de fichiers YAML versionnés
(`Docs/referentiel/`). Cible : une table PostgreSQL éditable par le fiscaliste via
l'écran E11. Le service de lecture ne connaît pas l'origine, la bascule n'affectera pas
les appelants.

## Les deux principes que le code fait respecter

### 1 · Aucune valeur légale en dur, aucune lecture sans date

```python
parametres.valeur_numerique("SEUIL_ESPECES_DEDUCTIBILITE_TVA", facture.date_emission)
```

Il n'existe volontairement aucune méthode permettant de lire « la valeur courante » sans
préciser laquelle. Une facture de 2024 se contrôle avec les règles de 2024.

Un paramètre absent, ou sans version couvrant la date, lève une erreur — jamais une
valeur par défaut. `tests/test_integrite_referentiel.py` interdit par ailleurs la
présence des seuils et taux connus dans les modules de domaine.

### 2 · Le prédicat d'une règle exprime la conformité

Prédicat **vrai** ⇒ conforme, aucun constat. Prédicat **faux** ⇒ constat émis.
Contre-intuitif, donc rappelé partout dans le code et dans chaque fichier de règle.

## Le moteur en trois étapes

```
règle YAML
   │  1. résolution   {"param": "SEUIL_…"} → Decimal("500000")
   │                  (la valeur employée est conservée dans le rapport)
   │  2. évaluation   JSONLogic restreint, sans eval, opérateurs sur liste close
   │  3. conséquence  déclarative : la règle décrit, un service en aval applique
   ▼
constat + enjeu chiffré
```

L'enjeu découle de la conséquence : TVA seule rejetée → montant de TVA ; charge seule →
HT ; les deux → TTC ; aucune → non chiffré.

## API

| Route | Objet |
|---|---|
| `GET /sante` | Disponibilité |
| `GET /referentiel/parametres` | Codes disponibles |
| `GET /referentiel/parametres/{code}?a_la_date=` | Résolution datée. **La date est obligatoire** |
| `GET /referentiel/validation?a_la_date=` | Ce qui reste à valider, et si le référentiel est opposable |
| `GET /conformite/regles` | Catalogue des règles |
| `POST /conformite/controler` | Contrôler une facture transmise |
| `GET /conformite/demonstration` | Références du jeu de démonstration (§ 13.5) |
| `GET /conformite/demonstration/{reference}` | Contrôler une facture de démonstration |

Essai rapide :

```bash
curl http://127.0.0.1:8000/conformite/demonstration/F-2026-0414
# → verdict bloquant, 536 625 FCFA, TVA et charge non déductibles
```

## Tests

| Fichier | Ce qu'il protège |
|---|---|
| `test_jsonlogic.py` | L'évaluateur : portée, comparaisons, refus des opérateurs inconnus |
| `test_referentiel.py` | Lecture datée, bornes `[du, au[`, refus de deviner |
| `test_regles.py` | Un cas passant et un cas échouant **par règle**, plus les verdicts du § 13.5 |
| `test_integrite_referentiel.py` | Fondement légal obligatoire, paramètres référencés existants, couverture de test par règle, aucune valeur légale en dur |
| `test_formats.py` | Formats du § 4 : montants, taux, dates |
| `test_api.py` | Contrats HTTP |

Deux tests sont des **rappels volontaires** : ils échoueront le jour où le référentiel
sera validé, pour forcer la mise à jour de la documentation associée. Voir
`test_rien_nest_encore_opposable`.

## Ajouter une règle

1. `Docs/referentiel/regles/<CODE>.yaml`
2. Une classe `Test<CODE_avec_underscores>` dans `tests/test_regles.py`, avec au moins un
   cas passant et un cas échouant
3. `pytest` — `test_integrite_referentiel.py` refuse une règle sans tests, sans fondement
   légal, ou référençant un paramètre inexistant

## Avertissement

Aucune valeur légale de ce système n'a été validée sur le Code Général des Impôts. Toutes
portent le statut `A_VALIDER`. `GET /referentiel/validation` en donne l'état exact.
**Aucun chiffre produit n'est opposable** tant qu'un fiscaliste nommé n'a pas confirmé
chaque valeur sur le texte — voir `Docs/architecture/09-questions-ouvertes.md`.
