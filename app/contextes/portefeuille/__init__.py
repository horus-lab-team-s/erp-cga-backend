"""Contexte B · Portefeuille adhérents.

Entreprise, Exercice, Adhesion, MandatDeclaratif, Dirigeant, Associe, Tiers.

RegimeFiscal et RattachementFiscal sont des **statuts datés**, jamais des
attributs : début, fin, motif du changement. Toute lecture se fait à une date, et
il n'existe volontairement aucune façon de demander « le régime courant » sans
préciser laquelle.

C'est le même principe que pour les paramètres du référentiel, et pour la même
raison : une facture de 2024 se contrôle avec le régime de 2024. Traiter le
régime comme une colonne produirait un rapport faux, et faux en silence.

Trois règles métier y vivent :

1. le **franchissement de seuil**, avec alerte anticipée avant le passage — le
   constat après coup arrive toujours trop tard ;
2. la **période probatoire** avant tout retour au régime inférieur ;
3. l'**exercice**, sans présomption d'année civile ni de douze mois : les
   exercices décalés et les premiers exercices longs sont la règle, pas l'exception.

Ce contexte ne dépend d'aucun autre contexte métier — seulement du socle. C'est
une feuille que presque tous les autres lisent.

Voir Docs/architecture/01-contextes-bornes.md, 04-modele-donnees.md, et le manuel
Docs/manuel/ § 1.4 pour les mécanismes sous-jacents.
"""
