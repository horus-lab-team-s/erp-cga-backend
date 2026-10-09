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

### Trois règles de forme, refusées à la soumission

Elles ne figuraient pas ici, et `cga_relance_proforma` en enfreignait une : son
corps finissait sur `{{4}}` seul. Le refus serait arrivé **des semaines après
l'écriture**, dans le vocabulaire de la plateforme, à quelqu'un qui n'avait pas
écrit le texte. Le domaine les vérifie désormais au chargement — voir
`ModeleDeMessage`.

| Règle | Pourquoi |
|---|---|
| Le corps ne **commence** pas par un emplacement | Refusé à la soumission ; et le client lirait un message qui s'ouvre sur une valeur, sans phrase. |
| Le corps ne **se termine** pas par un emplacement | Refusé à la soumission. Faites suivre l'emplacement d'une phrase, comme `cga_envoi_proforma` le fait pour son lien. |
| Deux emplacements ne se **touchent** pas | Refusé, et le client lirait deux valeurs accolées sans séparateur. |

⚠️ **Seules les règles dont on est sûr sont vérifiées.** Celles de catégorie, de
longueur et de bouton ne le sont pas : les deviner ferait refuser des modèles
valides, ce qui coûte plus cher que de les laisser passer.

⚠️ **Un emplacement seul sur sa ligne se lit mal**, même quand la plateforme
l'accepte. Une adresse jetée sans phrase ne dit pas au client ce qu'elle ouvre,
et une valeur vide y laisserait une ligne blanche au milieu du message.
`cga_relance_honoraires` était dans ce cas ; son `{{4}}` enchaîne désormais sur
la phrase suivante, sans qu'aucun mot change. **Plus aucun modèle n'est dans ce
cas**, et ce n'est pas vérifié automatiquement : la règle est nôtre, pas celle
de la plateforme, et l'imposer au chargement ferait refuser un jour un modèle
que la plateforme accepte.

⚠️ **`cga_lien_activation` porte le risque de refus le plus sérieux du lot** :
une adresse libre dans un modèle d'authentification, alors que cette catégorie
est prévue pour un code à usage unique. Son `usage` dit quoi faire si le refus
vient. Ce n'est pas un chemin critique : le lien d'activation part aussi par
courriel, qui ne dépend d'aucune approbation.

## Le coût, pour mémoire

| Ce qu'on envoie | Fenêtre ouverte | Fenêtre fermée |
|---|---|---|
| Message libre | gratuit | **refusé** |
| Modèle utilitaire | gratuit | facturé |
| Modèle d'authentification | facturé | facturé |
| Modèle marketing | facturé | facturé, et hors périmètre |

Facturation **par message** depuis le 1er juillet 2025. Répondre vite est
gratuit ; une relance automatique mal réglée coûte à chaque déclenchement.
