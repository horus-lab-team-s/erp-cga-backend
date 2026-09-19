"""Le noyau d'évaluation, partagé par les contextes qui appliquent des règles.

Un même mécanisme sert quatre usages : contrôler la conformité d'une pièce, chiffrer
une prestation, évaluer la charge d'un dossier, surveiller le fonctionnement interne.
Ce qui change d'un usage à l'autre n'est ni le code ni le mécanisme, mais les faits
que porte le sujet, les règles qu'on lui applique et la façon d'agréger les constats.

Le noyau vit ici plutôt que dans un contexte parce qu'il n'appartient à aucun : le
placer dans « conformité » obligerait la tarification à dépendre de la fiscalité pour
faire une addition pondérée.

⚠️ **Aucun module de ce paquet n'importe un contexte métier.** Il ne connaît ni
facture, ni impôt, ni monnaie : il connaît des faits, des règles et des conséquences.
C'est cette ignorance qui le rend transposable, et le test d'architecture la vérifie.
"""
