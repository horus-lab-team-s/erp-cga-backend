# Barèmes et règles de tarification

Ce qui fixe le prix d'une prestation, et l'amplitude de négociation autour.

## Deux fichiers de nature différente

`baremes.yaml` porte, par service, la **base** et l'**amplitude** admise. Un
fichier par règle porte les **ajustements**.

La séparation n'est pas cosmétique : la base change quand le centre révise son
offre, les règles changent quand il affine sa lecture des dossiers, et les deux
n'ont ni le même rythme ni le même signataire.

## Le moteur rend un intervalle, jamais un prix

| Valeur | Ce qu'elle représente | Qui elle protège |
|---|---|---|
| Plancher | Coût de revient majoré de la marge minimale | Le centre |
| Référence | Ce que le barème prévoit pour ce profil | La cohérence entre clients |
| Plafond | La limite haute admise | Le client |

Dans l'intervalle, le responsable module librement. Hors de l'intervalle, le
motif devient obligatoire et la validation remonte d'un cran.

## ⚠️ Un ajustement est **soit** un montant, **soit** un taux

Jamais les deux, et jamais aucun. Un fichier qui déclare les deux est refusé au
chargement.

**Un taux s'applique à la base, pas au cumul.** C'est ce qui le rend
commutatif : appliqué au cumul, le résultat dépendrait de l'ordre des règles,
c'est-à-dire de l'alphabet des noms de fichiers. Un tarif qui change parce qu'on
a renommé un fichier est indéfendable.

```yaml
montant: 45000       # quarante-cinq mille francs, en plus
montant: -25000      # une remise : un ajustement comme un autre
taux: 0.15           # quinze pour cent de la base
taux: -0.10          # dix pour cent de moins
```

## La convention de lecture

**VRAI = le cas ordinaire, aucun ajustement. FAUX = le critère est atteint,
l'ajustement s'applique.** C'est la convention des cinq domaines qui tournent sur
ce moteur.

## Les faits disponibles

Ceux du **questionnaire du service**, plus trois que le système calcule :

| Fait | Type | D'où il vient |
|---|---|---|
| `base` | décimal | Le barème du service, à la date |
| `score_charge` | entier | La matrice d'évaluation de charge |
| `service` | texte | La clé du service demandé |

Ajouter une question au questionnaire ouvre donc un fait de plus aux règles de
prix, sans déploiement. C'est voulu.

## Ce qui ne se met pas ici

**Les débours.** Frais de notaire, droits d'enregistrement, frais de guichet : le
centre les avance, il ne les gagne pas. Ils entrent dans la proposition comme des
lignes à part, à leur montant, et **aucune règle ne les ajuste, aucune amplitude
ne les touche**.

Négocier ne peut pas porter sur l'argent d'un tiers.

## Le statut

Comme partout. Les valeurs ci-dessous sont `A_VALIDER` : **le barème réel du
centre n'a pas été transmis**, et celles-ci illustrent le mécanisme sans engager
aucun tarif.
