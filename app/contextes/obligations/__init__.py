"""Contexte F · Obligations et déclarations.

TypeObligation, ObligationInstance, Declaration, Paiement, Penalite.

La date d'échéance est CALCULÉE à partir du type d'obligation, du centre de
rattachement et de la date de clôture — jamais figée. Le moteur ne raisonne jamais
« régime X ⇒ rien à faire » : le caractère libératoire de l'IGS n'efface ni les
retenues à la source, ni les charges sociales.

À FAIRE — voir Docs/architecture/01-contextes-bornes.md et
Docs/architecture/06-phases-et-sequencement.md.
"""
