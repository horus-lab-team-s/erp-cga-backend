# Questionnaires de qualification

Ce que le responsable demande au client pendant l'échange, et qui devient les
**faits** sur lesquels le moteur chiffre la prestation.

## Un fichier par service

Le nom du fichier est la clé du service au catalogue : `creation-sarl.yaml`
qualifie une demande de `creation-sarl`. Un service sans fichier ne peut pas être
qualifié, et le chargement le dit franchement plutôt que de proposer un
questionnaire par défaut qui poserait les mauvaises questions.

## Pourquoi ces fichiers existent

> « La liste des questions, leur ordre, leur caractère obligatoire et leur type
> dépendent du service demandé et changent avec l'offre. **Ajouter une question
> ne doit pas demander un déploiement.** »

Le questionnaire est donc de la configuration, alors que les schémas de faits des
trois autres domaines du moteur sont du code. Ce n'est pas une exception
arbitraire : **un schéma vit là où vit son sujet.** Le sujet d'une facture est un
objet de code ; le sujet d'une qualification est ce formulaire. Voir l'en-tête de
`app/moteur/faits.py`, qui porte la règle générale.

## La version, et pourquoi elle n'est pas décorative

Chaque qualification conserve la version du questionnaire employé, comme une
proposition tarifaire conserve la version du barème.

**Changer les questions sans changer la version rendrait illisible tout ce qui a
été qualifié avant.** Six mois plus tard, personne ne saurait sur quelles
questions un dossier a été rempli, ni pourquoi le montant proposé était celui-là.

Règle : toute modification de `questions` s'accompagne d'un incrément de
`version`.

## Les types

Ceux du moteur, sans traduction : `BOOLEEN`, `ENTIER`, `DECIMAL`, `TEXTE`,
`DATE`, `ENUM`, `LISTE`.

Un `ENUM` **doit** déclarer ses valeurs, et une réponse hors liste est refusée à
la saisie. Sans cela, chaque règle qui compare à cette liste échouerait sans que
rien ne le dise.

## Ce qui ne se met pas ici

Le champ libre. Il existe sur la qualification, il sert aux nuances, et **il
n'entre dans aucun calcul**. Ce qui mérite d'entrer dans un calcul mérite une
question, avec un type.

## Le rang

Plus petit = posé plus tôt. L'ordre compte : un questionnaire qui demande le
capital social avant la forme juridique se fait interrompre par le client.
