"""Le code QR de la clé du second facteur, en SVG (17 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI

L'écran d'association n'affichait que la clé en texte : trente-deux caractères à recopier
à la main dans l'application du téléphone. En recette en ligne, la cliente a affiché sa
clé (journal du 12 novembre) et n'a jamais réussi à l'activer : une faute de frappe dans
la clé rend tous les codes faux, et rien ne le signale. Toutes les applications
d'authentification lisent un code QR : on le montre, la clé en texte reste en secours.

⚠️ AUCUNE DÉPENDANCE NOUVELLE : `reportlab` (déjà là pour les PDF) calcule la matrice ;
le SVG est écrit ici, un seul chemin, quelques kilo-octets. Il ne sort jamais du serveur
autrement que dans la réponse qui porte déjà la clé, une seule fois.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

__all__ = ["code_qr_svg"]

#: La marge blanche exigée autour du code (quatre modules), sans laquelle certains
#: lecteurs ne le trouvent pas.
_MARGE = 4


def code_qr_svg(contenu: str) -> str:
    """Le code QR de `contenu` (une adresse `otpauth://`), en SVG autonome."""
    from reportlab.graphics.barcode.qr import QrCodeWidget

    widget = QrCodeWidget(contenu, barLevel="M")
    widget.draw()
    qr = widget.qr
    n = qr.getModuleCount()
    cote = n + 2 * _MARGE
    traces = [
        f"M{c + _MARGE} {r + _MARGE}h1v1h-1z"
        for r in range(n)
        for c in range(n)
        if qr.isDark(r, c)
    ]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {cote} {cote}" '
        f'shape-rendering="crispEdges"><rect width="{cote}" height="{cote}" fill="#fff"/>'
        f'<path d="{"".join(traces)}" fill="#000"/></svg>'
    )
