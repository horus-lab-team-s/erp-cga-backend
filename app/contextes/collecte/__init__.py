"""Contexte C · Collecte de pièces.

Le point de friction réel du métier. Tout le reste du système est du calcul, et le
calcul ne se trompe pas deux fois de la même façon ; la collecte, elle, dépend d'un
tiers — l'adhérent —, et c'est là que les dossiers s'enlisent.

Quatre canaux prévus, sans hiérarchie entre eux : portail web, application mobile
avec mode hors ligne, **WhatsApp Business API** — le plus réaliste pour beaucoup de
TPE —, import de relevés bancaires et d'historiques Mobile Money.

`PieceJustificative` avec son cycle de traitement `Reçue → Lue → Rapprochée →
Comptabilisée → Archivée`, `DemandePiece` avec relance tracée, extraction OCR
assortie d'un score de confiance et d'une validation humaine obligatoire sur les
champs dont l'erreur se propage jusqu'à la déclaration.

Le contrôle de conformité se déclenche **à la réception** et non à la saisie : le
seul moment où une facture est corrigeable, c'est tout de suite.

Surfaces publiques : `contrats.py` (entités) et `api.py` (cas d'usage).
Voir Docs/architecture/01-contextes-bornes.md et 10-flux-fonctionnels.md.
"""
