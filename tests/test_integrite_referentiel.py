"""Garde-fous sur le référentiel lui-même.

Ces tests ne vérifient pas un comportement : ils empêchent le référentiel de se dégrader
silencieusement. Une règle sans fondement légal, un paramètre référencé mais inexistant,
une valeur légale codée en dur — ce sont les trois façons dont ce produit meurt.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from app.contexts.conformite.modeles import Regle
from app.contexts.conformite.moteur import MoteurConformite
from app.contexts.referentiel.service import ServiceParametres

CONTROLE_LE = date(2026, 7, 15)


def _references_de_parametres(noeud) -> set[str]:
    if isinstance(noeud, list):
        return set().union(*(_references_de_parametres(e) for e in noeud)) if noeud else set()
    if not isinstance(noeud, dict):
        return set()
    if set(noeud) == {"param"}:
        return {noeud["param"]}
    return set().union(*(_references_de_parametres(v) for v in noeud.values())) if noeud else set()


class TestFondementLegal:
    def test_chaque_regle_porte_un_fondement(self, regles: list[Regle]):
        # Sans fondement, on ne sait pas quoi mettre à jour à la loi de finances suivante,
        # ni justifier un rejet auprès d'un adhérent mécontent.
        for regle in regles:
            assert regle.fondement.texte.strip(), regle.code
            assert regle.fondement.source.strip(), regle.code

    def test_chaque_regle_porte_un_message_et_une_remediation(self, regles: list[Regle]):
        # Un constat sans consigne de régularisation laisse le comptable sans issue.
        for regle in regles:
            assert len(regle.message) > 20, regle.code
            assert len(regle.remediation) > 20, regle.code

    def test_chaque_parametre_porte_un_fondement(self, parametres: ServiceParametres):
        for code in parametres.codes:
            resolu = parametres.resoudre(code, CONTROLE_LE)
            assert resolu.fondement.texte.strip(), code
            assert resolu.fondement.source.strip(), code


class TestCoherenceDesReferences:
    def test_tous_les_parametres_references_existent(
        self, regles: list[Regle], parametres: ServiceParametres
    ):
        for regle in regles:
            for code in _references_de_parametres(regle.predicat):
                assert parametres.existe(code), (
                    f"{regle.code} référence le paramètre « {code} », absent du référentiel"
                )

    def test_tous_les_parametres_references_sont_resolubles_a_la_date_des_regles(
        self, regles: list[Regle], parametres: ServiceParametres
    ):
        # Une règle en vigueur depuis 2019 ne peut pas s'appuyer sur un paramètre créé
        # en 2026 : le contrôle d'une facture ancienne échouerait.
        for regle in regles:
            for code in _references_de_parametres(regle.predicat):
                parametres.resoudre(code, regle.applicable_du)

    def test_aucune_regle_en_echec_sur_le_jeu_de_demonstration(self, moteur: MoteurConformite):
        from app.contexts.conformite.donnees_demo import FACTURES_DEMO

        for reference, facture in FACTURES_DEMO.items():
            rapport = moteur.controler(facture)
            assert not rapport.regles_en_echec, (reference, rapport.regles_en_echec)


class TestCouvertureDesTests:
    def test_chaque_regle_possede_une_classe_de_test(self, regles: list[Regle]):
        """Exigence du § 4.5 du cadrage : un test par règle, cas passant et cas échouant."""
        source = (Path(__file__).parent / "test_regles.py").read_text(encoding="utf-8")
        for regle in regles:
            attendu = "class Test" + regle.code.replace("-", "_")
            assert attendu in source, (
                f"{regle.code} n'a pas de tests. Ajouter « {attendu} » dans test_regles.py "
                "avec au moins un cas passant et un cas échouant."
            )


class TestAucuneValeurLegaleEnDur:
    """Principe d'architecture n° 1 : aucune valeur légale en dur dans le code.

    Le contrôle porte sur les modules de domaine, à l'exclusion des données de
    démonstration et des tests, qui manipulent légitimement des montants littéraux.
    """

    DOMAINE = ("app/contexts/conformite/moteur.py", "app/contexts/conformite/resolution.py")
    #: Valeurs qui ne doivent jamais apparaître ailleurs que dans le référentiel.
    INTERDITES = (r"\b19[.,]25\b", r"\b100_?000\b", r"\b500_?000\b", r"\b50_?000_?000\b")

    @pytest.mark.parametrize("chemin_relatif", DOMAINE)
    def test_pas_de_seuil_ni_de_taux_code_en_dur(self, chemin_relatif: str):
        racine = Path(__file__).resolve().parents[1]
        source = (racine / chemin_relatif).read_text(encoding="utf-8")
        for motif in self.INTERDITES:
            trouve = re.search(motif, source)
            assert trouve is None, (
                f"{chemin_relatif} contient la valeur légale « {trouve.group()} » en dur. "
                "Elle doit vivre dans Docs/referentiel/parametres.yaml et être lue à une date."
            )
