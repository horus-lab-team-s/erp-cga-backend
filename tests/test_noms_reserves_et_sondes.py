"""Les noms que la plateforme se garde, et les sondes des services qui en manquaient (pas 121).

─────────────────────────────────────────────────────────────────────────────────
LE DÉFAUT QUE CE FICHIER GARDE FERMÉ

`domaine.slug.valider(slug, reserves=())` porte un second paramètre facultatif. L'`api`
du contexte N réexportait cette fonction telle quelle, et les deux seuls appelants —
l'ouverture d'un tenant après paiement et sa confirmation manuelle — l'appelaient avec un
seul argument. Résultat : `Docs/referentiel/tenants/noms-reserves.yaml` n'était lu **nulle
part**, et « api », « www » ou « admin » étaient attribuables à un client. Les requêtes de
la plateforme seraient alors parties chez lui.

Le paramètre facultatif n'est pas supprimé du domaine — il doit rester vérifiable sans
fichier — mais la seule porte ouverte aux autres contextes va chercher la liste
elle-même. C'est la différence entre corriger deux appels et supprimer la façon de se
tromper.

CE QUE CE FICHIER COUVRE ENSUITE

Les sondes prises au pas 121 : la Collecte (son magasin de fichiers), le Pilotage (ses
sept réglages), les Tenants (les noms réservés) et le Référentiel élargi aux barèmes. Et
la propriété qui les rend possibles : une sonde n'est pas une porte d'entrée, et le
registre ne la compte pas comme telle.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.contextes.collecte.adaptateurs.entrant.sonde import sonde_de_la_collecte
from app.contextes.pilotage.adaptateurs.entrant.sonde import REGLAGES, sonde_du_pilotage
from app.contextes.referentiel.adaptateurs.entrant.sonde import sonde_du_referentiel
from app.contextes.tenants.adaptateurs.entrant.sonde import sonde_des_tenants
from app.contextes.tenants.api import (
    NomsReservesIndisponibles,
    SlugInvalide,
    noms_reserves,
    valider_le_slug,
)
from app.contextes.tenants.domaine.slug import MotifRejet
from app.contextes.tenants.domaine.slug import valider as valider_la_forme
from app.infrastructure.config import configuration
from app.registre.etat import EtatDeConstruction, EtatDExecution, construction_de
from app.registre.services import service


@pytest.fixture
def referentiel_a_soi(tmp_path, monkeypatch):
    """Un référentiel vide et jetable, que chaque cas garnit de ce qu'il éprouve.

    ⚠️ `configuration` est mémoïsée : sans les deux vidages, le cas suivant lirait encore
    le dossier de celui-ci, et l'ordre des tests déciderait du résultat.
    """
    monkeypatch.setenv("CGA_DOSSIER_REFERENTIEL", str(tmp_path))
    configuration.cache_clear()
    yield tmp_path
    configuration.cache_clear()


# ── Les noms réservés ─────────────────────────────────────────────────────────


class TestLesNomsReserves:
    def test_le_fichier_livre_garde_les_noms_de_la_plateforme(self):
        reserves = noms_reserves()
        assert {"api", "www", "admin"} <= reserves, "le fichier du dépôt les déclare"

    @pytest.mark.parametrize("slug", ["api", "www", "admin"])
    def test_la_porte_publique_refuse_un_nom_reserve(self, slug: str):
        """Le défaut d'origine : ces trois-là passaient."""
        with pytest.raises(SlugInvalide) as refus:
            valider_le_slug(slug)
        assert refus.value.motif is MotifRejet.RESERVE

    def test_le_domaine_seul_les_accepte_encore_et_c_est_voulu(self):
        """Il valide une **forme**, et doit rester vérifiable sans fichier.

        Ce cas dit pourquoi la correction ne s'est pas faite dans le domaine : s'il
        refusait « api » tout seul, la liste serait revenue dans le code, et la faire
        grandir demanderait une livraison.
        """
        valider_la_forme("api")  # ne lève pas

    def test_un_slug_ordinaire_passe_toujours(self):
        valider_le_slug("station-bonaberi")

    def test_sans_liste_on_refuse_au_lieu_d_attribuer(self, referentiel_a_soi):
        """⚠️ Le choix n'est pas symétrique : un slug attribué est inréattribuable."""
        with pytest.raises(NomsReservesIndisponibles):
            valider_le_slug("station-bonaberi")

    def test_la_casse_du_fichier_n_est_pas_une_donnee(self, referentiel_a_soi):
        """⚠️ Vérifié **sur ce que rend l'adaptateur**, et non sur le refus final.

        Le domaine remet les réserves en minuscules avant de comparer : un test qui ne
        regarde que le refus passe donc même si l'adaptateur ne fait rien. La promesse
        est ici celle de l'adaptateur — « rendus en minuscules ici plutôt qu'à la
        comparaison » — et c'est elle qu'on éprouve.
        """
        (referentiel_a_soi / "tenants").mkdir()
        (referentiel_a_soi / "tenants" / "noms-reserves.yaml").write_text(
            "noms:\n  - API\n  - '  Www  '\n", encoding="utf-8"
        )
        assert noms_reserves() == frozenset({"api", "www"})
        for slug in ("api", "www"):
            with pytest.raises(SlugInvalide):
                valider_le_slug(slug)


class TestLaRouteQuiOuvreUnTenant:
    def test_le_slug_reserve_est_refuse_avant_tout_le_reste(self):
        """422 sur le slug, et non 404 sur la proforma : le refus vient d'abord.

        L'ordre compte au-delà du code de statut : si la proforma était lue en premier,
        un slug réservé accompagné d'un numéro inconnu rendrait 404, et l'appelant
        corrigerait le numéro sans jamais apprendre que son sous-domaine est refusé.
        """
        from fastapi.testclient import TestClient

        from app.contextes.transverse.api import MOT_DE_PASSE_DEMO
        from app.main import creer_application

        client = TestClient(creer_application())
        session = client.post(
            "/transverse/session",
            json={"courriel": "s.onana@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        assert session.status_code == 200, session.text
        reponse = client.post(
            "/acquisition/proformas/PRO-INCONNUE/reglement",
            json={"slug": "api", "reference": "OM-000", "montant": "1"},
        )
        assert reponse.status_code == 422, reponse.text
        assert "réservé" in reponse.text


# ── Les sondes ────────────────────────────────────────────────────────────────


class TestLaSondeDesTenants:
    def test_elle_se_tait_sur_le_referentiel_livre(self):
        assert sonde_des_tenants() is None

    def test_sans_liste_elle_annonce_une_panne_franche(self, referentiel_a_soi):
        verdict = sonde_des_tenants()
        assert verdict is not None and verdict.etat is EtatDExecution.EN_PANNE
        assert "plus aucun tenant ne s'ouvrira" in verdict.motif

    def test_une_liste_vide_est_une_panne_et_dit_pourquoi(self, referentiel_a_soi):
        (referentiel_a_soi / "tenants").mkdir()
        (referentiel_a_soi / "tenants" / "noms-reserves.yaml").write_text(
            "noms: []\n", encoding="utf-8"
        )
        verdict = sonde_des_tenants()
        assert verdict is not None and verdict.etat is EtatDExecution.EN_PANNE
        assert "api" in verdict.motif

    def test_la_sonde_ne_rend_pas_le_service_joignable(self):
        """⚠️ Le contexte N reste `CAS_D_USAGE` : il n'expose toujours aucune route.

        Sans cette propriété, poser une sonde aurait promu le service « en service »,
        c'est-à-dire annoncé une façade sur du vide.
        """
        assert construction_de(service("tenants")) is EtatDeConstruction.CAS_D_USAGE


class TestLaSondeDeLaCollecte:
    def test_elle_se_tait_quand_le_magasin_est_inscriptible(self):
        assert sonde_de_la_collecte() is None

    def test_un_magasin_introuvable_est_une_panne(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CGA_DOSSIER_FICHIERS", str(tmp_path / "absent"))
        configuration.cache_clear()
        try:
            verdict = sonde_de_la_collecte()
        finally:
            configuration.cache_clear()
        assert verdict is not None and verdict.etat is EtatDExecution.EN_PANNE
        assert "croirait avoir remis sa pièce" in verdict.motif

    def test_un_magasin_non_inscriptible_est_une_panne(self, tmp_path, monkeypatch):
        """⚠️ Le cas le plus coûteux : le dossier existe, et l'écriture échoue."""
        interdit = tmp_path / "interdit"
        interdit.mkdir()
        interdit.chmod(0o500)
        monkeypatch.setenv("CGA_DOSSIER_FICHIERS", str(interdit))
        configuration.cache_clear()
        try:
            verdict = sonde_de_la_collecte()
        finally:
            configuration.cache_clear()
            interdit.chmod(0o700)
        assert verdict is not None and verdict.etat is EtatDExecution.EN_PANNE

    def test_des_reponses_illisibles_sont_suspectes_et_non_une_panne(self, tmp_path, monkeypatch):
        """Le cabinet reçoit et contrôle encore : seule la réponse de l'adhérent cesse."""
        monkeypatch.setenv("CGA_DOSSIER_REFERENTIEL", str(tmp_path))
        (tmp_path / "collecte").mkdir()
        (tmp_path / "collecte" / "reponses_adherent.yaml").write_text(
            "reponses: [\n", encoding="utf-8"
        )
        configuration.cache_clear()
        try:
            verdict = sonde_de_la_collecte()
        finally:
            configuration.cache_clear()
        assert verdict is not None and verdict.etat is EtatDExecution.SUSPECT


class TestLaSondeDuPilotage:
    def test_elle_se_tait_sur_le_referentiel_livre(self):
        assert sonde_du_pilotage() is None

    def test_les_sept_reglages_sont_couverts(self):
        """Le compte est écrit ici pour qu'un huitième réglage ajouté sans sonde se voie."""
        fichiers = sorted(
            p.name for p in Path(configuration().dossier_referentiel, "pilotage").glob("*.yaml")
        )
        assert len(REGLAGES) == len(fichiers) == 7, fichiers

    def test_un_reglage_mal_forme_est_suspect_et_nomme_l_ecran(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CGA_DOSSIER_REFERENTIEL", str(tmp_path))
        (tmp_path / "pilotage").mkdir()
        (tmp_path / "pilotage" / "file_d_anomalies.yaml").write_text(
            "gravites_traitees: [INVENTEE]\n", encoding="utf-8"
        )
        configuration.cache_clear()
        try:
            verdict = sonde_du_pilotage()
        finally:
            configuration.cache_clear()
        assert verdict is not None and verdict.etat is EtatDExecution.SUSPECT
        assert "la file d'anomalies" in verdict.motif


class TestLaSondeDuReferentielElargieAuxBaremes:
    def test_elle_se_tait_sur_le_referentiel_livre(self):
        assert sonde_du_referentiel() is None

    def test_l_absence_de_baremes_ne_dit_rien(self, referentiel_a_soi):
        """Un déploiement sans paie n'a aucun barème, et ce n'est pas un défaut.

        Les paramètres, eux, manquent aussi : c'est leur panne qui doit sortir, et non
        une alarme sur les barèmes.
        """
        verdict = sonde_du_referentiel()
        assert verdict is not None and verdict.etat is EtatDExecution.EN_PANNE
        assert "barème" not in verdict.motif

    def test_des_parametres_sains_sans_baremes_ne_disent_rien(self, referentiel_a_soi):
        """Le cas que le précédent ne prouve pas : les paramètres passent, les barèmes
        manquent, et la sonde se tait. Sans lui, retirer le garde-fou sur l'absence du
        fichier ne casserait rien."""
        self._poser_les_parametres(referentiel_a_soi)
        assert not (referentiel_a_soi / "baremes.yaml").exists()
        assert sonde_du_referentiel() is None

    @staticmethod
    def _poser_les_parametres(dossier: Path) -> None:
        source = Path(__file__).resolve().parents[2] / "Docs" / "referentiel"
        (dossier / "parametres.yaml").write_text(
            (source / "parametres.yaml").read_text(encoding="utf-8"), encoding="utf-8"
        )

    def test_un_fichier_de_baremes_qui_n_en_declare_aucun_est_suspect(
        self, referentiel_a_soi
    ):
        """⚠️ Le cas qui distingue « pas de paie » de « la paie a disparu ».

        Le fichier est là, bien formé, et vide de barèmes. C'est le même écran que
        l'absence pour le lecteur du dossier, et l'inverse pour celui qui l'a posé :
        quelqu'un compte dessus.
        """
        self._poser_les_parametres(referentiel_a_soi)
        (referentiel_a_soi / "baremes.yaml").write_text("baremes: []\n", encoding="utf-8")
        verdict = sonde_du_referentiel()
        assert verdict is not None and verdict.etat is EtatDExecution.SUSPECT
        assert "laisse croire le contraire" in verdict.motif

    def test_des_baremes_presents_et_illisibles_sont_suspects(self, referentiel_a_soi):
        self._poser_les_parametres(referentiel_a_soi)
        (referentiel_a_soi / "baremes.yaml").write_text("baremes: [\n", encoding="utf-8")
        verdict = sonde_du_referentiel()
        assert verdict is not None and verdict.etat is EtatDExecution.SUSPECT
        assert "bulletin" in verdict.motif
