"""Contexte E · Comptabilité SYSCOHADA.

Compte, Journal, EcritureComptable, LigneEcriture, AttributFiscal. La balance et
le grand livre sont des projections calculées, jamais des entités persistées :
une comptabilité n'a qu'une source, le journal.

Quatre invariants, tous vérifiés par le modèle :

1. Équilibre débit/crédit **au niveau de l'écriture**, jamais de la ligne.
2. Numérotation chronologique continue par journal et par exercice ; un trou est
   détecté par `projections.sequences_incompletes`.
3. Aucune modification après validation — contre-passation motivée uniquement,
   et aucune suppression nulle part.
4. Toute écriture validée porte sa pièce justificative et le nom de qui l'a
   validée : le Centre engage sa responsabilité sur ce qu'il présente.

Ce contexte ne calcule **aucune** conséquence fiscale : il reçoit du contexte
D · Conformité des constats déjà produits et les pose sur les lignes concernées,
sous forme d'`AttributFiscal`. C'est le maillon qui manquait à la chaîne
« constat → attribut fiscal → TVA du mois → réintégration de la liasse ».

Voir Docs/architecture/01-contextes-bornes.md, 04-modele-donnees.md, et le manuel
Docs/manuel/ pour les mécanismes métier sous-jacents.
"""
