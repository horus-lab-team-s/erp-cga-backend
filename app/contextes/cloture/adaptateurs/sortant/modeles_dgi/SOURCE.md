# Modèles officiels de la DGI

| Fichier | Source | Téléchargé le | SHA-256 |
|---|---|---|---|
| `DSF_Normal_DGIFORMAT_VERROUILLEVF.xlsx` | https://www.impots.cm/sites/default/files/uploads/DSF_Normal_DGIFORMAT_VERROUILLEVF.xlsx (page « Déclarations statistiques et fiscales » d'impots.cm) | 15 octobre 2026 | `26d8a15b6125df5d744b66cd1d70e6f69f09f30599ed7db36690e0a42cbb75aa` |

Le fichier est **recopié tel quel** : la plateforme n'en modifie que les cellules de montant du bilan et du compte de résultat, ainsi que les lignes d'identification, directement dans le XML (voir `dsf_format_dgi.py`). Tout le reste reste identique, octet pour octet, pour que le portail de télédéclaration le relise comme son propre modèle.

Quand la DGI publie une nouvelle version, on remplace le fichier, puis on met à jour ce tableau. `tests/test_dsf_format_dgi.py` vérifie que chaque code REF attendu est toujours présent.
