"""Contexte E · Comptabilité SYSCOHADA.

Journal, EcritureComptable, LigneEcriture, Compte, Balance, GrandLivre, Lettrage,
RapprochementBancaire, Amortissement, Provision.

Invariants : équilibre débit/crédit au niveau de l'écriture ; numérotation
chronologique continue par journal et par exercice ; AUCUNE modification après
validation, contre-passation motivée uniquement.

À FAIRE — voir Docs/architecture/01-contextes-bornes.md et
Docs/architecture/06-phases-et-sequencement.md.
"""
