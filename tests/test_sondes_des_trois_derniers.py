"""Les trois contextes qui étaient `SANS_SONDE`, et ce que leurs sondes voient.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Jusqu'au pas 135, trois services sur quatorze se déclaraient `SANS_SONDE` : la
Clôture, la Création d'entreprise et le Social. C'était une décision écrite, et
son motif était bon : « leur inventer une sonde qui relit la ressource d'un autre
créerait deux vérités sur une même question ».

Elle a été révisée pour une raison précise : **ces sondes ne posent pas la
question du Référentiel.** La sienne demande « le fichier se charge-t-il, et
porte-t-il quelque chose ». Les leurs demandent « porte-t-il les valeurs NOMMÉES
sans lesquelles je ne produis plus rien ». Un référentiel de soixante paramètres
auquel manque `CNPS_PLAFOND_MENSUEL` répond oui à la première et non à la
seconde.

⚠️ CE QUE CES CAS MESURENT VRAIMENT

Une sonde qui rend toujours `None` est verte dans tous les cas, y compris sur une
installation cassée : c'est la sonde complaisante que le pas 12 a supprimée.
Prouver qu'elle est verte ici ne prouverait donc rien.

Chaque sonde est donc éprouvée **deux fois** : sur le référentiel réel, où elle
doit se taire, et sur un référentiel amputé de la seule valeur qui la concerne,
où elle doit parler ET nommer ce qui manque.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import pytest

from app.contextes.cloture.adaptateurs.entrant.sonde import sonde_de_la_cloture
from app.contextes.creation_entreprise.adaptateurs.entrant.sonde import (
    sonde_de_la_creation_d_entreprise,
)
from app.contextes.social.adaptateurs.entrant.sonde import sonde_du_social
from app.registre.etat import EtatDExecution


@pytest.fixture
def sans_le_parametre(monkeypatch):
    """Ampute le référentiel commun d'un code, et le rend tel quel pour le reste.

    ⚠️ **On retire une ligne, on ne vide pas le fichier.** Un référentiel vide
    ferait parler n'importe quelle sonde, y compris une sonde complaisante qui
    lèverait sur le premier accès venu. Retirer exactement le code surveillé est
    la seule façon de prouver que c'est CELUI-LÀ qu'elle regarde.
    """
    import app.contextes.referentiel.api as referentiel

    reels = referentiel.parametres_communs()

    def amputer(code: str) -> None:
        restants = tuple(p for p in reels if p.code != code)
        assert len(restants) < len(reels), (
            f"{code} n'est pas au référentiel commun : ce cas ne prouverait rien, "
            "puisqu'il n'y a rien à retirer"
        )
        monkeypatch.setattr(referentiel, "parametres_communs", lambda: restants)

    return amputer


# ── Création d'entreprise (I) ────────────────────────────────────────────────


class TestLaSondeDeLaCreation:
    def test_elle_se_tait_sur_le_referentiel_reel(self):
        assert sonde_de_la_creation_d_entreprise() is None

    @pytest.mark.parametrize(
        "code", ["CAPITAL_MINIMUM_SARL", "CAPITAL_MINIMUM_SA"]
    )
    def test_un_capital_minimum_absent_est_une_panne(self, code, sans_le_parametre):
        """⚠️ Le défaut que cette sonde existe pour rendre visible.

        `_constater_le_capital` ne lève pas : il rend un constat **non bloquant**.
        C'est le bon choix au guichet — une ligne absente d'un YAML ne doit pas
        empêcher d'instruire tous les dossiers de SARL. Le revers est que le
        capital n'est alors plus comparé au minimum légal, en silence, et que le
        dossier part au greffe.
        """
        sans_le_parametre(code)
        verdict = sonde_de_la_creation_d_entreprise()
        assert verdict is not None, f"{code} retiré, et la sonde ne dit rien"
        assert verdict.etat is EtatDExecution.EN_PANNE
        assert code in (verdict.motif or ""), "le motif n'envoie pas au bon endroit"

    def test_une_forme_sans_pieces_est_une_panne(self, monkeypatch):
        """Une forme sans checklist rend un dossier « complet » dès son ouverture.

        Aucune pièce manquante, donc rien qui bloque, donc déposable au CFCE avec
        un dossier vide. C'est le pire des états : il a l'apparence du succès.
        """
        import app.contextes.creation_entreprise.domaine.checklist as checklist

        monkeypatch.setattr(checklist, "checklist_de", lambda _forme: ())
        verdict = sonde_de_la_creation_d_entreprise()
        assert verdict is not None and verdict.etat is EtatDExecution.EN_PANNE
        assert "aucune pièce" in (verdict.motif or "")


# ── Social (G) ───────────────────────────────────────────────────────────────


class TestLaSondeDuSocial:
    def test_elle_se_tait_sur_le_referentiel_reel(self):
        assert sonde_du_social() is None

    @pytest.mark.parametrize(
        "code",
        [
            "CNPS_PLAFOND_MENSUEL",
            "CNPS_PVID_TAUX_SALARIE",
            "CNPS_PVID_TAUX_EMPLOYEUR",
            "CNPS_PRESTATIONS_FAMILIALES_TAUX",
            "CFC_TAUX_SALARIE",
            "CFC_TAUX_EMPLOYEUR",
        ],
    )
    def test_un_code_de_paie_absent_est_une_panne(self, code, sans_le_parametre):
        sans_le_parametre(code)
        verdict = sonde_du_social()
        assert verdict is not None, f"{code} retiré, et la sonde ne dit rien"
        assert verdict.etat is EtatDExecution.EN_PANNE
        assert code in (verdict.motif or "")
        # ⚠️ Le motif dit ce qui est bloqué, pas seulement ce qui manque. Un
        # exploitant qui lit « CFC_TAUX_SALARIE introuvable » cherche ; un qui lit
        # « aucun bulletin ne sortira ce mois-ci » agit.
        assert "bulletin" in (verdict.motif or "")

    def test_les_trois_groupes_de_risque_sont_surveilles(self, sans_le_parametre):
        """⚠️ Et pas seulement celui du dossier de démonstration.

        Le taux d'accidents du travail dépend du groupe de l'employeur. Ne
        vérifier que le groupe A laisserait tomber la paie du premier client
        classé en C, et la sonde resterait verte pendant ce temps.
        """
        from app.contextes.social.api import CODE_ACCIDENTS_TRAVAIL, GroupeRisque

        for groupe in GroupeRisque:
            code = CODE_ACCIDENTS_TRAVAIL[groupe]
            sans_le_parametre(code)
            verdict = sonde_du_social()
            assert verdict is not None, f"{code} retiré, et la sonde ne dit rien"
            assert code in (verdict.motif or "")


# ── Clôture (H) ──────────────────────────────────────────────────────────────


class TestLaSondeDeLaCloture:
    def test_elle_se_tait_sur_le_referentiel_reel(self):
        assert sonde_de_la_cloture() is None

    def test_un_abattement_absent_est_une_panne(self, sans_le_parametre):
        """L'abattement CGA est la seule chose que l'adhésion achète.

        `_abattement_cga` ne lève pas : il rend `None` et une phrase jointe au
        dossier. L'explication vit donc DANS le dossier, lue une fois par la
        personne qui traite ce dossier-là. Si le taux manque, chaque adhérent paie
        l'impôt plein, chacun avec sa petite phrase, et la plateforme se déclare
        en bonne santé.
        """
        sans_le_parametre("ABATTEMENT_CGA_BENEFICE")
        verdict = sonde_de_la_cloture()
        assert verdict is not None and verdict.etat is EtatDExecution.EN_PANNE
        assert "ABATTEMENT_CGA_BENEFICE" in (verdict.motif or "")

    def test_un_plafond_absent_est_suspect_et_dit_dans_quel_sens(
        self, sans_le_parametre
    ):
        """⚠️ L'erreur est ici dans l'AUTRE sens, et c'est ce qui la rend sournoise.

        `_abattement_cga` se rabat sur `plafond = 0` et ne plafonne que si le
        plafond est strictement positif. Un plafond absent **supprime donc le
        plafonnement** : l'abattement s'applique en entier.

        Le code confond deux états que le référentiel ne distingue pas : « la loi
        ne plafonne pas » et « personne n'a écrit la ligne ». Suspect, et non
        panne, parce que l'explication innocente existe.
        """
        sans_le_parametre("ABATTEMENT_CGA_PLAFOND")
        verdict = sonde_de_la_cloture()
        assert verdict is not None and verdict.etat is EtatDExecution.SUSPECT
        assert "SANS plafond" in (verdict.motif or "")

    def test_le_seuil_du_systeme_normal_n_alarme_pas(self, sans_le_parametre):
        """⚠️ La contre-épreuve : une sonde qui parle pour rien n'est plus lue.

        `SEUIL_SYSTEME_NORMAL` absent rend le Système Normal, qui est le régime de
        droit commun — « le retenir par défaut ne fait courir aucun risque de
        rejet », dit `_systeme_dsf`. Une liasse plus détaillée que nécessaire est
        acceptée au guichet.
        """
        sans_le_parametre("SEUIL_SYSTEME_NORMAL")
        assert sonde_de_la_cloture() is None
