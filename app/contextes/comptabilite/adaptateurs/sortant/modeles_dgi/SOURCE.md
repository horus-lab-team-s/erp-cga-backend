# Modèles officiels de la DGI : déclarations annuelles par tiers

| Fichier | Source | Téléchargé le | SHA-256 |
|---|---|---|---|
| `MODELE FOURNISSEURS.xlsx` | page « Formats de la DSF en ligne » d'impots.cm (https://www.impots.cm/fr/actualites/formats-de-la-dsf-en-ligne) | 15 octobre 2026 | `c3d0d3c5a7aa5452d56f3567602f4c89460136d39f8cefe7573a82c588c91acd` |
| `MODELE TIERS.xlsx` | même page | 15 octobre 2026 | `c198c5ee771b87608b8d7db651278b747ddf228bb5b9e540e243f38aadfac5e2` |

Les fichiers sont **recopiés tels quels** : la plateforme n'en réécrit que la feuille (les lignes des tiers et la ligne TOTAL) et la chaîne de calcul, directement dans le XML (voir `declaration_par_tiers_dgi.py`). Le logo et le reste restent identiques, octet pour octet. Le fichier rendu garde le nom du modèle : le portail refuse un fichier renommé.
