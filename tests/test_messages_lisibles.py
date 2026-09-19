"""Un refus se dit en français, jamais avec l'enrobage du validateur.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Au pas 51, un essai réel de la vitrine a envoyé un numéro étranger au formulaire de
contact. La route publique `POST /acquisition/demandes` a rendu au visiteur la trace
brute de pydantic : « 1 validation error for DemandeDeContact », des noms de types,
et un lien vers pydantic.dev.

Le défaut était connu : la comptabilité avait écrit sa fonction de déballage, avec un
commentaire qui décrivait cette trace mot pour mot. Elle était privée à ses routes, et
onze autres sites de refus en restaient privés.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import ast
from datetime import datetime

import pytest

from app.infrastructure.config import RACINE_DEPOT
from app.partage.erreurs import message_lisible
from tests.conftest import exige_postgresql

RACINE_APP = RACINE_DEPOT / "Backend_erp_cga" / "app"

#: Les marques de l'enrobage : si l'une d'elles atteint un message, il n'a pas été
#: déballé.
ENROBAGE = ("validation error", "pydantic", "input_value", "[type=")


def _refus_du_numero_etranger():
    from pydantic import ValidationError

    from app.contextes.souscription.domaine.demande_de_contact import (
        Canal,
        Consentement,
        DemandeDeContact,
    )

    try:
        DemandeDeContact(
            identifiant="dc-essai",
            deposee_le=datetime(2026, 9, 14, 9, 0),
            nom="Awa Ngono",
            telephone="+33612345678",
            service_souhaite="adhesion",
            canal_prefere=Canal.APPEL,
            consentement=Consentement(
                accorde=False,
                recueilli_le=datetime(2026, 9, 14, 9, 0),
                version_du_texte="consentement-whatsapp-v1",
            ),
        )
    except ValidationError as refus:
        return refus
    raise AssertionError("le numéro étranger a été accepté : le cas ne mesure rien")


class TestLaFonction:
    def test_une_erreur_de_validation_rend_la_phrase_du_domaine(self):
        refus = _refus_du_numero_etranger()
        # ⚠️ La contre-épreuve : l'enrobage est bien là dans le texte brut.
        assert any(marque in str(refus) for marque in ENROBAGE)

        message = message_lisible(refus)
        assert "numéro camerounais" in message
        assert not any(marque in message for marque in ENROBAGE), message

    def test_une_autre_exception_garde_son_texte(self):
        assert message_lisible(ValueError("journal ZZ inconnu")) == "journal ZZ inconnu"


def _refus_brut_rendus(source: str) -> list[int]:
    """Les lignes où un `except ValueError as x` renvoie `str(x)`."""
    lignes = []
    for noeud in ast.walk(ast.parse(source)):
        if not isinstance(noeud, ast.ExceptHandler) or noeud.name is None:
            continue
        types = noeud.type.elts if isinstance(noeud.type, ast.Tuple) else [noeud.type]
        attrapes = {getattr(t, "id", getattr(t, "attr", "")) for t in types}
        if not attrapes & {"ValueError", "ValidationError"}:
            continue
        for appel in ast.walk(noeud):
            if (
                isinstance(appel, ast.Call)
                and getattr(appel.func, "id", "") == "str"
                and appel.args
                and getattr(appel.args[0], "id", "") == noeud.name
            ):
                lignes.append(appel.lineno)
    return lignes


class TestAucuneRouteNeRendLEnrobage:
    """⚠️ **La classe du défaut, pas les onze sites.**"""

    def test_aucun_refus_de_validation_n_est_rendu_brut(self):
        routes = sorted(RACINE_APP.rglob("adaptateurs/entrant/*.py"))
        # ⚠️ Le balayage doit prouver qu'il balaie.
        assert len(routes) >= 20, [str(r) for r in routes]
        coupables = [
            f"{fichier.relative_to(RACINE_APP)}:{ligne}"
            for fichier in routes
            for ligne in _refus_brut_rendus(fichier.read_text())
        ]
        assert not coupables, (
            "ces refus renvoient `str()` d'une ValueError, qui peut être une "
            "ValidationError pydantic et rendre sa trace brute ; employer "
            "`message_lisible` de app/partage/erreurs.py : " + ", ".join(coupables)
        )

    def test_le_balayage_verrait_le_defaut_s_il_revenait(self):
        fautif = (
            "def route():\n"
            "    try:\n"
            "        pass\n"
            "    except (NumeroInvalide, ValueError) as echec:\n"
            "        raise HTTPException(status_code=422, detail=str(echec))\n"
        )
        assert _refus_brut_rendus(fautif) == [5]


class TestParLaRoutePublique:
    pytestmark = exige_postgresql

    #: ⚠️ Deux numéros **de longueur admise** par le corps de requête, refusés par le
    #: domaine. Un numéro trop court est refusé plus tôt par la validation de requête
    #: de FastAPI, qui rend une structure et non une trace : autre couche, déjà
    #: traitée par `app/lib/api.ts` côté frontend, et que l'action serveur ne peut
    #: pas atteindre puisqu'elle exige huit chiffres.
    @pytest.mark.parametrize("numero", ["+33612345678", "912345678"])
    def test_le_visiteur_lit_une_phrase_et_non_une_trace(self, plateforme, numero):
        reponse = plateforme.post(
            "/acquisition/demandes",
            json={
                "nom": "Awa Ngono",
                "telephone": numero,
                "service_souhaite": "adhesion",
                "canal_prefere": "APPEL",
                "consentement_whatsapp": False,
            },
        )
        assert reponse.status_code == 422, reponse.text
        detail = reponse.json()["detail"]
        assert isinstance(detail, str), detail
        assert not any(marque in detail for marque in ENROBAGE), detail
