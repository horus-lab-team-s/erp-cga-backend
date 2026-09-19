"""Une transition d'entité rejoue ses invariants. `model_copy` ne les rejoue pas.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Trois lignes suffisent à montrer le défaut :

    validee = brouillon.model_copy(update={"etat": VALIDEE})
    validee.etat                 # VALIDEE
    validee.piece_justificative  # None

`EcritureComptable` porte pourtant l'invariant : *« une écriture validée porte sa
pièce justificative. Sans elle, la traçabilité est rompue dès le premier
maillon. »* Il n'a pas joué, parce que **`model_copy` ne rejoue aucun
validateur** — c'est écrit dans la documentation de pydantic, et c'est même son
intérêt : la copie est rapide parce qu'elle ne vérifie rien.

⚠️ **Le contrat est l'inverse de celui qu'on lui prête** en le lisant dans du code
métier, où il ressemble à un constructeur.

CE QUE CELA COÛTAIT VRAIMENT

L'invariant ne se réveillait qu'à la **relecture** depuis PostgreSQL, parce que le
dépôt reconstruit l'objet par `model_validate` : des jours plus tard, sur une
donnée déjà écrite, dans une pile d'appel qui ne dit pas d'où elle vient.

Et en persistance mémoire, l'objet n'est jamais reconstruit : **l'invariant
n'existait tout simplement pas.**
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import ast
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.contextes.comptabilite.domaine.entites import (
    EcritureComptable,
    EtatEcriture,
    LigneEcriture,
    Sens,
)
from app.infrastructure.config import RACINE_DEPOT
from app.partage.copie import transiter
from tests.conftest import exige_postgresql, ouvrir_une_session

RACINE_APP = RACINE_DEPOT / "Backend_erp_cga" / "app"


def _sans_piece() -> EcritureComptable:
    return EcritureComptable(
        journal="OD",
        exercice="2026",
        numero=1,
        date_operation=date(2026, 7, 1),
        libelle="Une écriture sans pièce justificative",
        lignes=[
            LigneEcriture(compte="602", libelle="x", sens=Sens.DEBIT, montant=Decimal(1000)),
            LigneEcriture(compte="401", libelle="x", sens=Sens.CREDIT, montant=Decimal(1000)),
        ],
    )


class TestLaTransitionRejoueLInvariant:
    def test_valider_sans_piece_est_refuse(self):
        """⚠️ **Le cas central.** Avant, cette validation passait."""
        with pytest.raises(ValueError, match="pièce justificative"):
            _sans_piece().valider("a.bouba", datetime(2026, 7, 2, 9, 0))

    def test_la_contre_epreuve_avec_piece(self):
        """Sans elle, une fonction qui refuserait tout passerait aussi."""
        avec = transiter(_sans_piece(), piece_justificative="PJ-0001")
        validee = avec.valider("a.bouba", datetime(2026, 7, 2, 9, 0))
        assert validee.etat is EtatEcriture.VALIDEE
        assert validee.validee_par == "a.bouba"

    def test_model_copy_le_laisserait_encore_passer(self):
        """⚠️ **La démonstration du piège, gardée dans le temps.**

        Ce cas n'éprouve pas notre code : il éprouve pydantic, et il existe pour
        que personne ne conclue un jour que `model_copy` était devenu sûr et qu'on
        peut revenir en arrière. Le jour où pydantic changerait d'avis, ce cas
        échouerait, et ce serait une bonne nouvelle à examiner.
        """
        fraudee = _sans_piece().model_copy(update={"etat": EtatEcriture.VALIDEE})
        assert fraudee.etat is EtatEcriture.VALIDEE
        assert fraudee.piece_justificative is None
        assert fraudee.validee_par is None, (
            "une écriture validée par personne : c'est bien ce que model_copy laisse faire"
        )

    def test_un_champ_inconnu_leve_au_lieu_d_etre_ignore(self):
        """`model_copy` accepte `update={"etat_": …}` et rend une copie inchangée.

        Une faute de frappe sur un nom de champ produit alors une transition qui ne
        transite pas, et rien ne le signale : la valeur reste l'ancienne, et le
        bogue se cherche dans la logique métier.
        """
        muette = _sans_piece().model_copy(update={"etat_": EtatEcriture.VALIDEE})
        assert muette.etat is EtatEcriture.BROUILLON

        with pytest.raises(ValueError, match="etat_"):
            transiter(_sans_piece(), etat_=EtatEcriture.VALIDEE)


class TestLaClasseEntiereDuDefaut:
    """⚠️ **Une classe de défaut, pas un cas isolé.**

    Le défaut a été trouvé sur une écriture. Il valait pour **toute** entité qui
    porte un invariant de cohérence et se recopie pour changer d'état : huit
    classes, quinze appels. Toutes ont été converties, et ce contrôle garde la
    conversion.
    """

    def test_aucune_entite_a_invariant_ne_se_recopie_sans_revalider(self):
        coupables: list[str] = []
        gardees: set[str] = set()
        for fichier in sorted(RACINE_APP.rglob("*.py")):
            source = fichier.read_text()
            if "model_validator" not in source:
                continue
            for classe in [
                n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ClassDef)
            ]:
                if not _gardes_apres(classe):
                    continue
                gardees.add(classe.name)
                for appel in _copies_avec_update(classe):
                    coupables.append(
                        f"{fichier.relative_to(RACINE_APP)}:{appel} "
                        f"dans {classe.name}, qui porte {_gardes_apres(classe)}"
                    )

        # ⚠️ **Le balayage doit prouver qu'il balaie.** Une mutation a supprimé
        # sa boucle sans faire échouer le cas : un contrôle qui ne trouve rien
        # peut avoir raison, ou n'avoir rien regardé. On exige donc qu'il ait vu
        # les entités dont on sait qu'elles portent un invariant.
        assert len(gardees) >= 20, (
            f"{len(gardees)} classes à invariant examinées : le balayage ne "
            "parcourt plus le code."
        )
        for attendue in ("EcritureComptable", "PieceJustificative", "Habilitation"):
            assert attendue in gardees, (
                f"{attendue} porte un validateur « after » et n'a pas été "
                "examinée : le balayage a perdu une partie du code."
            )

        assert not coupables, (
            "ces entités changent d'état par `model_copy`, qui ne rejoue aucun "
            "validateur : leurs invariants ne jouent pas au moment où ils comptent. "
            "Employer `transiter` de app/partage/copie.py.\n  "
            + "\n  ".join(coupables)
        )

    def test_le_controle_verrait_le_defaut_s_il_revenait(self):
        """⚠️ **La contre-épreuve du contrôle lui-même.**

        Un balayage qui ne trouve rien peut avoir raison, ou ne rien chercher. On
        lui soumet donc une classe fautive écrite pour l'occasion : s'il ne la voit
        pas, le cas précédent ne prouve rien.
        """
        fautif = ast.parse(
            "class Faux(BaseModel):\n"
            "    @model_validator(mode='after')\n"
            "    def _coherence(self): return self\n"
            "    def muter(self): return self.model_copy(update={'x': 1})\n"
        )
        (classe,) = [n for n in ast.walk(fautif) if isinstance(n, ast.ClassDef)]
        assert _gardes_apres(classe) == ["_coherence"]
        assert _copies_avec_update(classe) == [4]


def _gardes_apres(classe: ast.ClassDef) -> list[str]:
    """Les validateurs `mode="after"` de cette classe, par leur nom."""
    return [
        membre.name
        for membre in classe.body
        if isinstance(membre, ast.FunctionDef)
        and any(
            isinstance(d, ast.Call)
            and getattr(d.func, "id", "") == "model_validator"
            and any(
                k.arg == "mode" and getattr(k.value, "value", "") == "after"
                for k in d.keywords
            )
            for d in membre.decorator_list
        )
    ]


def _copies_avec_update(classe: ast.ClassDef) -> list[int]:
    return [
        n.lineno
        for n in ast.walk(classe)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "model_copy"
        and any(k.arg == "update" for k in n.keywords)
    ]


class TestParLaRoute:
    """⚠️ **Le défaut atteignait-il vraiment un utilisateur ?**

    Un invariant contourné dans le domaine ne compte que si un chemin réel y
    conduit. Celui-ci en avait un, et le plus ordinaire qui soit : la pièce
    justificative est **facultative à la saisie**, et la validation ne la
    réclamait pas.
    """

    pytestmark = exige_postgresql

    def test_valider_une_ecriture_sans_piece_est_refuse_par_la_route(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "l.fotso@cga-brcg.cm")
        dossier = "M081234567890P"

        saisie = client.post(
            f"/comptabilite/dossiers/{dossier}/ecritures",
            json={
                "journal": "OD",
                "exercice": "2026",
                "date_operation": "2026-11-02",
                "libelle": "Saisie sans pièce justificative",
                "lignes": [
                    {"compte": "602", "libelle": "x", "sens": "DEBIT", "montant": "1000"},
                    {"compte": "401", "libelle": "x", "sens": "CREDIT", "montant": "1000"},
                ],
            },
        )
        # ⚠️ La saisie l'accepte, et c'est voulu : un comptable impute souvent
        # avant que la pièce ne lui parvienne.
        assert saisie.status_code == 201, saisie.text
        assert saisie.json()["piece_justificative"] is None
        numero = saisie.json()["numero"]

        validation = client.post(
            f"/comptabilite/dossiers/{dossier}/ecritures/2026/OD/{numero}/validation",
            json={},
        )
        assert validation.status_code == 409, (
            "la validation a réussi sans pièce : l'invariant du domaine ne joue "
            f"toujours pas. Réponse : {validation.text}"
        )
        assert "pièce justificative" in validation.text

        # ⚠️ Et l'écriture reste brouillon : un refus qui laisserait l'état changé
        # serait pire que pas de refus du tout.
        relue = client.get(
            f"/comptabilite/dossiers/{dossier}/ecritures/2026/OD/{numero}"
        )
        assert relue.json()["etat"] == "BROUILLON"

