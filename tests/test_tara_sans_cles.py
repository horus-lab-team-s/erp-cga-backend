"""Sans clés Tara, un environnement exposé refuse le paiement mobile money et le dit.

⚠️ CONSTATÉ À L'AUDIT AVANT LA MISE EN PRODUCTION (20 octobre 2026) : sans clés, le
fournisseur rendait « accepté » avec une référence factice, et le paiement restait « en
attente » sans que personne ne sache pourquoi.
"""

from __future__ import annotations

from decimal import Decimal

from app.contextes.souscription.adaptateurs.sortant import fournisseur_tara
from app.contextes.souscription.adaptateurs.sortant.fournisseur_tara import FournisseurTara


def _initier(fournisseur: FournisseurTara):
    return fournisseur.initier(
        montant=Decimal(90000), telephone="677123456", cle_idempotence="H-1", libelle="Honoraires"
    )


def _sans_cles() -> FournisseurTara:
    return FournisseurTara(
        cle_api="", identifiant_marchand_tara="", adresse_publique="https://api.cga-brcg.cm"
    )


def test_expose_sans_cles_le_paiement_est_refuse_avec_un_motif(monkeypatch):
    monkeypatch.setattr(fournisseur_tara, "_environnement_expose", lambda: True)
    initiation = _initier(_sans_cles())
    assert initiation.accepte is False
    assert initiation.reference_externe is None
    assert "guichet" in (initiation.message or "")


def test_sur_un_poste_de_travail_la_simulation_reste(monkeypatch):
    monkeypatch.setattr(fournisseur_tara, "_environnement_expose", lambda: False)
    initiation = _initier(_sans_cles())
    assert initiation.accepte is True
    assert (initiation.reference_externe or "").startswith("SIMULE-")
