# Règles de conformité de facture

Un fichier YAML par règle. Format identique à celui de la future table `regle_conformite`.

## Convention capitale

> **`predicat` exprime la CONFORMITÉ, pas la violation.**
>
> Prédicat **vrai** ⇒ la règle est respectée, aucun constat.
> Prédicat **faux** ⇒ un constat est émis, avec la sévérité et la conséquence déclarées.

C'est la convention du document de cadrage § 4.2. Elle est contre-intuitive : chaque règle la
rappelle en commentaire.

## Champs

| Champ | Obligatoire | Rôle |
|---|---|---|
| `code` | oui | Identifiant stable, affiché sur le constat |
| `libelle` | oui | Énoncé de ce que la règle **exige** |
| `categorie` | oui | Une des huit catégories du catalogue |
| `version` | oui | Version de rédaction dans le référentiel |
| `applicable_du` / `applicable_au` | oui / non | Validité **légale** de la règle. `null` = en vigueur |
| `severite` | oui | `BLOQUANT`, `MAJEUR`, `AVERTISSEMENT`, `INFORMATION` |
| `statut` | oui | `A_VALIDER`, `VALIDE`, `DESACTIVEE` |
| `fondement` | oui | Texte et source. **Sans lui, la règle est refusée au chargement** |
| `portee` | non | Filtre : type de document, régimes, exclusions — voir ci-dessous |
| `predicat` | oui | JSONLogic. Vrai = conforme |
| `consequence` | non | Déclarative. Aucun effet de bord |
| `message` | oui | Ce que lit le comptable |
| `remediation` | oui | Ce qu'il doit faire |

`version` et `applicable_du` sont deux choses différentes : une règle rédigée en 2026 peut
porter sur du droit en vigueur depuis 2019.

## Opérateurs de prédicat autorisés

Sous-ensemble explicite de JSONLogic, plus un opérateur `regex` maison. Toute autre clé est
refusée au chargement. Voir `Backend_erp_cga/app/contexts/conformite/jsonlogic.py`.

```
var  missing  if
==  !=  ===  !==  >  >=  <  <=
and  or  !  !!
+  -  *  /  %
in  cat  substr
some  none  all  map  filter
regex          → regex(chaine, motif) : le motif accepte le préfixe (?i)
param          → référence au référentiel, résolue AVANT évaluation
```

## La portée d'une règle

Quatre filtres, appliqués **avant** évaluation. Une règle hors portée n'est ni évaluée, ni
comptée dans le total des règles appliquées.

| Clé | Porte sur | Exemple |
|---|---|---|
| `type_document` | La nature du document | `[FACTURE_ACHAT]` |
| `regimes_emetteur` | Le régime du **fournisseur** | Une règle de TVA ne vise pas un émetteur non assujetti |
| `regimes_destinataire` | Le régime de l'**adhérent** | `[REEL]` — la déductibilité n'a pas d'objet au synthétique |
| `exclusions` | Cas particuliers nommés | `[FOURNISSEUR_ETRANGER]` |

⚠️ **Ne pas confondre les deux régimes.** C'est celui du destinataire qui commande la
déductibilité. Une règle de TVA appliquée à un adhérent qui ne récupère jamais la taxe
annonce un préjudice inexistant, et s'use jusqu'à être ignorée. Voir
`Docs/architecture/03-moteur-conformite.md` § 6.

## Référence à un paramètre

`{ param: SEUIL_ESPECES_DEDUCTIBILITE_TVA }` est remplacé, **avant** évaluation, par la
valeur en vigueur à la date de l'opération. Le rapport conserve la valeur employée, sa date
d'effet et son statut de validation.

Un paramètre inexistant, ou sans version applicable à la date, fait échouer la règle avec un
message explicite — jamais silencieusement.

## Conséquence fiscale

Déclarative : la règle *décrit*, un service en aval *applique*.

| Clé | Effet |
|---|---|
| `tva_deductible: false` | La TVA de la facture est rejetée |
| `charge_deductible: false` | La charge est réintégrée au résultat fiscal |
| `poste_reintegration` | Poste du tableau de passage où atterrit la réintégration |
| `rectification_requise: true` | Une facture rectificative doit être demandée |
| `verification_requise: true` | Contrôle humain avant comptabilisation |

L'enjeu chiffré en découle : TVA seule rejetée → montant de TVA ; charge seule → montant HT ;
les deux → TTC ; aucune → non chiffré.

## Règles présentes

| Code | Gravité | Objet | Catégorie |
|---|---|---|---|
| `FAC-ID-003` | Bloquant | NIU du fournisseur présent, valide et actif | 1 · Identification émetteur |
| `FAC-ACH-007` | Majeur | Règlement en espèces au-delà du seuil légal | 7 · Déductibilité |
| `FAC-CAL-002` | Majeur | Somme des lignes égale au total hors taxes | 5 · Cohérence arithmétique |
| `FAC-DOC-011` | Avertissement | Désignation de la prestation précise | 4 · Contenu des lignes |
| `FAC-VRA-005` | Avertissement | Absence de doublon possible | 8 · Vraisemblance |

Ce sont les cinq règles du § 13.6 du dossier de design. Le catalogue cible en compte quinze à
vingt — voir `Docs/architecture/03-moteur-conformite.md` § 7.

## Ajouter une règle

1. Écrire le YAML ici.
2. Écrire son test — **cas passant et cas échouant** — dans
   `Backend_erp_cga/tests/test_regles.py`.
3. `pytest` doit passer, y compris `test_integrite_referentiel.py` qui vérifie que le
   fondement est renseigné et que tous les paramètres référencés existent.
