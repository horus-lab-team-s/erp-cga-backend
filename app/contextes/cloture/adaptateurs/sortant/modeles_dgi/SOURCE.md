# Modèles officiels de la DGI

| Fichier | Source | Téléchargé le | SHA-256 |
|---|---|---|---|
| `DSF_Normal_DGIFORMAT_VERROUILLEVF.xlsx` | https://www.impots.cm/sites/default/files/uploads/DSF_Normal_DGIFORMAT_VERROUILLEVF.xlsx (page « Déclarations statistiques et fiscales » d'impots.cm) | 15 octobre 2026 | `26d8a15b6125df5d744b66cd1d70e6f69f09f30599ed7db36690e0a42cbb75aa` |
| `DSF_SMT_VEROUILLE-09-03-2021.xlsx` | https://www.impots.cm/sites/default/files/2021-03/DSF_SMT_VEROUILLE-09-03-2021.xlsx (page « Formats de la DSF en ligne » d'impots.cm) | 10 novembre 2026 | `ae9715e11e629ba3212f3a9215a8875d51d64488f4457bd7e06a44e085ba8204` |

Le fichier est **recopié tel quel** : la plateforme n'en modifie que les cellules de montant du bilan et du compte de résultat, ainsi que les lignes d'identification, directement dans le XML (voir `dsf_format_dgi.py`). Tout le reste reste identique, octet pour octet, pour que le portail de télédéclaration le relise comme son propre modèle.

Quand la DGI publie une nouvelle version, on remplace le fichier, puis on met à jour ce tableau. `tests/test_dsf_format_dgi.py` vérifie que chaque code REF attendu est toujours présent.

⚠️ **Le modèle SMT n'est pas encore rempli par la plateforme** (10 novembre 2026). Il est
conservé ici pour que l'export soit codé sur le fichier officiel, et non sur une copie
reconstituée. La règle de passage des livres au modèle (bilan simplifié, compte de résultat
de trésorerie) est proposée dans `Docs/dsf-smt-regle-de-passage.md` et attend la validation
de l'expert-comptable du cabinet avant d'être codée.
