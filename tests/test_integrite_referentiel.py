"""Garde-fous sur le référentiel lui-même.

Ces tests ne vérifient pas un comportement : ils empêchent le référentiel de se dégrader
silencieusement. Une règle sans fondement légal, un paramètre référencé mais inexistant,
une valeur légale codée en dur — ce sont les trois façons dont ce produit meurt.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.contextes.conformite.application.moteur_conformite import MoteurConformite
from app.contextes.conformite.domaine.entites import (
    Document,
    FactureAControler,
    LigneFacture,
    Montants,
    Partie,
    Regle,
    Reglement,
)
from app.contextes.conformite.domaine.schema_faits import SCHEMA_FACTURE
from app.contextes.referentiel.application.service_parametres import ServiceParametres
from app.moteur.chemins import chemins_cites
from app.moteur.faits import ErreurSchema

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


class TestLesSeuilsDisentLeurBorne:
    """⚠️ **La borne d'un seuil est une donnée légale, et elle est exigée.**

    Le diagnostic de seuil comparait avec `>=` le seuil de la TVA, que le CGI dit
    « supérieur à » : une entreprise à 50 000 000 exactement était invitée à un
    reclassement que la loi ne lui impose pas. Rien ne pouvait le voir, puisque la
    borne n'était écrite nulle part.

    Le contrôle porte sur **chaque version**, et non sur la seule version en vigueur :
    une version passée sans borne ferait échouer la relecture d'une liasse ancienne,
    des années plus tard.
    """

    PREFIXES = ("SEUIL_", "CAPITAL_MINIMUM_")

    def _seuils(self):
        from app.contextes.referentiel.api import DepotParametresYaml
        from app.infrastructure.config import configuration

        tous = DepotParametresYaml(
            configuration().dossier_referentiel / "parametres.yaml"
        ).charger()
        return [p for p in tous if p.code.startswith(self.PREFIXES)]

    def test_chaque_version_de_seuil_declare_sa_borne(self):
        seuils = self._seuils()
        # ⚠️ La contre-épreuve du balayage : sans elle, un filtre qui ne trouverait
        # rien déclarerait tous les seuils en règle.
        assert len(seuils) >= 8, [p.code for p in seuils]
        sans_borne = [
            f"{p.code} (version du {v.applicable_du})"
            for p in seuils
            for v in p.versions
            if v.borne is None
        ]
        assert not sans_borne, (
            "ces seuils ne disent pas si leur valeur même est atteinte ; renseigner "
            "« borne » d'après la formulation du texte : " + ", ".join(sans_borne)
        )

    @pytest.mark.parametrize(
        "code, formulation, borne",
        [
            ("SEUIL_ASSUJETTISSEMENT_TVA", "supérieur à", "EXCLUSE"),
            ("SEUIL_ADHESION_CGA", "n'excède pas", "EXCLUSE"),
            ("SEUIL_ACTE_NOTARIE_SARL", "n'excède pas", "EXCLUSE"),
            ("SEUIL_ESPECES_DEDUCTIBILITE_TVA", "au moins égale", "INCLUSE"),
            ("SEUIL_COMPTABILITE_OBLIGATOIRE", "dès", "INCLUSE"),
        ],
    )
    def test_la_borne_suit_la_formulation_du_fondement(self, code, formulation, borne):
        """⚠️ **Le texte fait foi, et le cas le relit.**

        La première annotation de l'acte notarié l'avait supposée incluse ; le texte
        dit « n'excède pas ». Ce cas lie la borne à la formulation citée dans le
        fondement : si l'un change sans l'autre, il échoue.
        """
        (parametre,) = [p for p in self._seuils() if p.code == code]
        for version in parametre.versions:
            texte = " ".join(version.fondement.texte.split())
            assert formulation in texte, (code, texte)
            assert version.borne is not None and version.borne.value == borne, (
                code, version.borne
            )


class TestLaComparaisonAUnSeuil:
    def _resolu(self, borne):
        from app.contextes.referentiel.contrats import (
            Fondement,
            ParametreResolu,
            StatutValidation,
            Unite,
        )

        return ParametreResolu(
            code="SEUIL_ESSAI",
            libelle="Seuil d'essai",
            valeur=1000,
            unite=Unite.FCFA,
            applicable_du=date(2026, 1, 1),
            statut=StatutValidation.A_VALIDER,
            fondement=Fondement(texte="texte", source="source"),
            borne=borne,
        )

    def test_la_valeur_meme_selon_la_borne(self):
        from app.contextes.referentiel.contrats import Borne

        assert self._resolu(Borne.INCLUSE).atteint(Decimal(1000)) is True
        assert self._resolu(Borne.EXCLUSE).atteint(Decimal(1000)) is False
        assert self._resolu(Borne.EXCLUSE).atteint(Decimal(1001)) is True
        assert self._resolu(Borne.INCLUSE).atteint(Decimal(999)) is False

    def test_un_seuil_sans_borne_refuse_de_comparer(self):
        """⚠️ Supposer une borne, c'est la choisir à la place de la loi, en silence."""
        from app.contextes.referentiel.contrats import SeuilSansBorne

        with pytest.raises(SeuilSansBorne, match="ne déclare pas sa borne"):
            self._resolu(None).atteint(Decimal(1000))


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
        from app.contextes.conformite.adaptateurs.sortant.donnees_demo import FACTURES_DEMO

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


def _facture_temoin() -> FactureAControler:
    """Une facture minimale, employée pour confronter le schéma à ce que le sujet expose.

    Elle porte au moins une ligne : sans détail, le parcours ne verrait pas la collection
    et le test passerait en ignorant la moitié du schéma.
    """
    return FactureAControler(
        document=Document(reference="F-SCHEMA-0001", date_emission=date(2026, 7, 15)),
        emetteur=Partie(denomination="FOURNISSEUR", niu="M053311224455R", niu_actif=True),
        destinataire=Partie(denomination="ADHERENT", niu="M081234567890P"),
        montants=Montants(total_ht=Decimal(1), total_tva=Decimal(0), total_ttc=Decimal(1)),
        reglement=Reglement(),
        lignes=[LigneFacture(designation="Article", montant_ht=Decimal(1))],
    )


class TestFaitsDeclares:
    """Une règle ne cite que des faits que le domaine déclare.

    C'est le garde-fou qui manque le plus souvent, parce que son absence ne se voit pas.
    Une règle qui interroge « montants.total_htt » ne lève aucune erreur : le fait
    manquant vaut absent, la comparaison rend faux, et un constat part sur chaque pièce
    contrôlée. L'erreur est d'un caractère, le préjudice est un adhérent à qui l'on
    reproche une anomalie qui n'existe pas.
    """

    def test_chaque_regle_ne_cite_que_des_faits_declares(self, regles: list[Regle]):
        for regle in regles:
            SCHEMA_FACTURE.valider_predicat(
                chemins_cites(regle.predicat), origine=f"la règle {regle.code}"
            )

    def test_le_schema_couvre_ce_que_le_sujet_expose(self, regles: list[Regle]):
        """Le schéma déclare, le sujet expose : les deux doivent parler des mêmes faits.

        Ce test attrape le cas inverse du précédent — un champ ajouté à la facture et
        oublié au schéma. Sans lui, une règle légitime portant sur le champ neuf serait
        refusée au chargement, et le réflexe serait de désactiver le garde-fou.
        """
        exposes = set()

        def parcourir(valeur, prefixe=""):
            if isinstance(valeur, dict):
                for cle, sous in valeur.items():
                    parcourir(sous, f"{prefixe}{cle}.")
            elif isinstance(valeur, list):
                exposes.add(prefixe.rstrip("."))
            else:
                exposes.add(prefixe.rstrip("."))

        parcourir(_facture_temoin().faits())
        # Les faits d'élément portent le suffixe « [] » : ils ne se déduisent pas d'un
        # parcours, qui ne voit qu'une liste. On les met de côté ici, le test précédent
        # les couvre par les règles qui s'en servent.
        manquants = sorted(exposes - SCHEMA_FACTURE.codes)
        assert not manquants, (
            f"la facture expose des faits que SCHEMA_FACTURE ne déclare pas : {manquants}. "
            "Les déclarer, sinon aucune règle ne pourra légitimement s'en servir."
        )

    def test_un_fait_inconnu_est_refuse_avec_une_suggestion(self):
        """Le garde-fou doit mordre, et son message doit faire gagner du temps."""
        with pytest.raises(ErreurSchema) as echec:
            SCHEMA_FACTURE.valider_predicat(
                chemins_cites({">": [{"var": "montants.total_htt"}, 0]}),
                origine="la règle FAC-TEST-000",
            )
        message = str(echec.value)
        assert "FAC-TEST-000" in message
        assert "montants.total_htt" in message
        assert "montants.total_ht" in message, "le message doit suggérer le fait voisin"


class TestAucuneValeurLegaleEnDur:
    """Principe d'architecture n° 1 : aucune valeur légale en dur dans le code.

    Le contrôle porte sur les modules de domaine, à l'exclusion des données de
    démonstration et des tests, qui manipulent légitimement des montants littéraux.
    """

    DOMAINE = (
        "app/contextes/conformite/application/moteur_conformite.py",
        "app/contextes/conformite/application/resolution_parametres.py",
        "app/moteur/jsonlogic.py",
    )
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
