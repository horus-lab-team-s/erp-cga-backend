# La configuration Consul, engendrée depuis le registre

**Rien de ce répertoire ne s'écrit à la main.** La configuration se produit depuis
`app/registre/services.py`, qui est la déclaration unique des quatorze services :

```bash
python -m app.registre.consul \
    --adresse "$ADRESSE_DU_SERVICE" \
    --port 8000 \
    --jeton-de-sonde "$JETON_DE_SONDE" > consul.json
```

## Le sens est unique : le dépôt écrit, Consul applique

⚠️ Une intention corrigée dans l'interface de Consul serait **invisible dans le
dépôt**, absente de la revue, perdue au prochain déploiement, et
`tests/test_architecture.py` continuerait d'affirmer une découpe qui ne serait plus
celle appliquée par le maillage.

C'est exactement le défaut que le registre a corrigé, avec un tour de plus : la
vérité serait alors hors de portée du code.

Quand une dépendance entre services change, on modifie `ARETES_AUTORISEES`, on relit
le test d'architecture, et on régénère. Jamais l'inverse.

## Ce que le fichier contient

| Clé | Ce que c'est | D'où ça vient |
|---|---|---|
| `services` | Un enregistrement par service, avec son contrôle de santé | `SERVICES` |
| `intentions` | Qui a le droit d'appeler qui, appliqué par mTLS | `ARETES_AUTORISEES`, **inversé** et socle développé |
| `mesh` | Le refus par défaut | `politique_par_defaut()` |

⚠️ **Les quatorze services s'enregistrent même dans un seul processus.** Un agent
Consul peut en porter plusieurs ; Consul montre alors la santé service par service
alors que tout vit encore ensemble. Le jour où l'un part vivre ailleurs, seule son
adresse change : l'extraction devient un déménagement, pas une refonte.

## Le jeton de sonde

Les chemins de santé sont protégés, comme tout ce qui décrit l'architecture : la
liste de ce qui tombe avec quoi est une carte des points de rupture.

Consul porte l'en-tête d'autorisation sur ses contrôles. Le jeton vient donc de
l'exploitation, jamais du dépôt. **Sans lui, les contrôles échouent en `401` et
Consul dit les quatorze services critiques** : c'est franc, et cela envoie chercher
le jeton plutôt qu'ailleurs.

## Les trois niveaux

Consul traduit les codes de statut de ses contrôles HTTP en trois niveaux, et un
seul code déclenche l'intermédiaire.

```
2xx  → passing    le service répond, ou n'a pas encore de sonde
429  → warning    signal à regarder, sans certitude de panne
reste → critical  le service ne peut pas travailler
```

Le registre a trois niveaux d'exécution parce qu'un état d'exécution en a
naturellement trois. Consul avait fait le même constat avant nous.
