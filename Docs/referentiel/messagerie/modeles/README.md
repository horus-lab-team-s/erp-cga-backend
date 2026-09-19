# Modèles de message

Les textes soumis à l'avance à la plateforme d'envoi, seul moyen d'écrire à un
client **hors de la fenêtre de service de vingt-quatre heures**.

## Pourquoi ce dossier est urgent

Un modèle est approuvé en quelques heures à quelques jours. **Un modèle non
approuvé fonctionne dans le bac à sable et échoue en production.** Rien ne
distingue les deux à l'écriture du code, et le défaut ne se découvre qu'au
moment où l'on écrit à un vrai client.

Ils doivent donc être soumis **dès le début du développement**, pas la veille de
la mise en service. La soumission ne dépend d'aucun code : elle se fait dans la
console de la plateforme.

## Ce qui est ici, et ce qui ne l'est pas

Ce dossier porte ce que **le centre décide** : le texte, la catégorie, l'usage,
et l'état d'approbation constaté.

Il ne porte **pas** la fenêtre de vingt-quatre heures, ni la grille tarifaire.
Celles-là sont imposées par la plateforme et vivent dans le code, avec leur
citation. Les mettre ici ferait croire qu'on peut les changer ; quelqu'un
porterait la fenêtre à soixante-douze heures pour se laisser du temps, et les
envois échoueraient sans que rien ne l'explique.

## Le statut, et ce qu'il commande

| Statut | Effet |
|---|---|
| `EN_ATTENTE` | Soumis, pas encore approuvé. **Refusé à l'envoi**, localement. |
| `APPROUVE` | Envoyable. Exige une date d'approbation. |
| `REFUSE` | La plateforme a refusé le texte. À réécrire et resoumettre. |
| `DESACTIVE` | Retiré après approbation. Refusé à l'envoi. |

Tous les modèles ci-dessous sont `EN_ATTENTE` : **aucun n'a encore été soumis**,
puisque le compte de la plateforme n'est pas ouvert. C'est un état honnête, et il
fait échouer les envois localement plutôt qu'en production.

## Les emplacements

Au format de la plateforme : `{{1}}`, `{{2}}`… Le nombre attendu est le **plus
grand numéro cité**, pas le nombre de numéros distincts : un corps qui cite
`{{1}}` et `{{3}}` en attend trois.

## Le coût, pour mémoire

| Ce qu'on envoie | Fenêtre ouverte | Fenêtre fermée |
|---|---|---|
| Message libre | gratuit | **refusé** |
| Modèle utilitaire | gratuit | facturé |
| Modèle d'authentification | facturé | facturé |
| Modèle marketing | facturé | facturé, et hors périmètre |

Facturation **par message** depuis le 1er juillet 2025. Répondre vite est
gratuit ; une relance automatique mal réglée coûte à chaque déclenchement.
