"""L'en-tête et le pied des documents, lus dans le référentiel (9 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
« Sur les proformas web, il faut aussi joindre l'en-tête et le pied de page. » La page web
de la proforma et le PDF lisent la même source : `cabinet/identite_des_documents.yaml`.

⚠️ CE QUI EST GARDÉ
* le fichier livré imprime exactement ce que le PDF imprimait (aucune régression) ;
* le PDF imprime ce que dit le fichier, en-tête et pied ;
* un fichier absent ne bloque pas le document ; un fichier mal formé lève ;
* la consultation publique de la proforma rend l'en-tête et le pied.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.contextes.souscription.adaptateurs.sortant import document_proforma
from app.contextes.souscription.adaptateurs.sortant.identite_des_documents import (
    _PAR_DEFAUT,
    IdentiteDesDocuments,
    identite_des_documents,
)
from tests.conftest import exige_postgresql
from tests.test_document_de_la_proforma import _composition, _emettre, _texte_du_pdf


def test_le_fichier_livre_imprime_ce_que_le_document_imprimait():
    assert identite_des_documents() == _PAR_DEFAUT


def test_sans_fichier_les_valeurs_d_origine_et_mal_forme_leve(tmp_path):
    assert identite_des_documents(tmp_path) == _PAR_DEFAUT
    (tmp_path / "cabinet").mkdir()
    (tmp_path / "cabinet" / "identite_des_documents.yaml").write_text(
        "raison_sociale: X\ntelephone_invente: 1\n", encoding="utf-8"
    )
    with pytest.raises(ValidationError):
        identite_des_documents(tmp_path)


def test_le_pdf_imprime_ce_que_dit_le_referentiel(monkeypatch):
    autre = IdentiteDesDocuments(
        raison_sociale="CABINET DE RECETTE",
        activite="Une activite de recette",
        site="www.recette.cm",
        courriel="recette@recette.cm",
        pied=("Ligne de pied de recette", "Agrement de recette"),
    )
    monkeypatch.setattr(document_proforma, "identite_des_documents", lambda: autre)
    texte = _texte_du_pdf(
        document_proforma.rendre_le_document(
            _composition(),
            numero="PRO-2026-0008",
            version=1,
            emise_le=datetime(2026, 10, 14, 10, 30),
            client="MBARGA COUTURE SARL",
        )
    )
    for attendu in ("CABINET DE RECETTE", "www.recette.cm", "Ligne de pied de recette"):
        assert attendu in texte
    assert "CGA BROAD RANGE CONSULTING" not in texte


@exige_postgresql
def test_la_consultation_rend_l_en_tete_et_le_pied(plateforme):
    corps = _emettre(plateforme).json()
    lue = plateforme.get(
        f"/acquisition/proformas/{corps['numero']}/consultation",
        params={
            "version": corps["version"],
            "expire_le": corps["expire_le"],
            "sceau": corps["lien_acceptation"],
        },
    )
    assert lue.status_code == 200, lue.text
    cabinet = lue.json()["cabinet"]
    assert cabinet["raison_sociale"] == "CGA BROAD RANGE CONSULTING"
    assert any("Agrément" in ligne for ligne in cabinet["pied"])
    # Émise avec un modèle : le PDF existe, la page propose de le lire et de le télécharger.
    assert lue.json()["document_disponible"] is True
