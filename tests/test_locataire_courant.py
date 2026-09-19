"""Le locataire de la requête en cours, et le refus qui protège.

Troisième pas du socle multi-tenant. Le mécanisme de cloisonnement existait déjà et
fonctionnait ; il n'avait jamais eu qu'un seul locataire, écrit en constante et importé
dans quinze modules. Ce pas le rend dynamique : établi une fois au bord, lu partout
ailleurs.

Le test qui compte est le dernier de ce fichier : lire des données sans avoir dit pour qui
doit **lever**, jamais servir des lignes arbitraires.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.partage.locataire import (
    LocataireNonEtabli,
    courant,
    courant_ou_none,
    etabli,
    sous_mandat,
)


class TestEtablirEtLire:
    def test_le_locataire_etabli_se_lit(self):
        with etabli("CGA-BRCG"):
            assert courant() == "CGA-BRCG"

    def test_il_est_restitue_a_la_sortie_du_bloc(self):
        with etabli("CGA-BRCG"):
            pass
        assert courant_ou_none() is None

    def test_l_imbrication_rend_le_locataire_exterieur(self):
        """Un travail interne mené au nom d'un autre locataire doit rendre le locataire
        extérieur en sortant, pas l'effacer. D'où la restitution par jeton."""
        with etabli("CENTRE"):
            with etabli("ENTREPRISE"):
                assert courant() == "ENTREPRISE"
            assert courant() == "CENTRE"

    def test_le_bloc_rend_le_locataire_etabli(self):
        with etabli("CGA-BRCG") as locataire:
            assert locataire == "CGA-BRCG"

    def test_il_est_restitue_meme_en_cas_d_erreur(self):
        with pytest.raises(RuntimeError), etabli("CGA-BRCG"):
            raise RuntimeError("incident au milieu du traitement")
        assert courant_ou_none() is None

    @pytest.mark.parametrize("vide", ["", "   ", "\t"])
    def test_un_locataire_vide_n_etablit_rien(self, vide: str):
        with pytest.raises(ValueError, match="n'établit rien"):
            with etabli(vide):
                pass


class TestLeRefusQuiProtege:
    """La décision qui porte tout le module."""

    def test_lire_sans_avoir_etabli_leve(self):
        """Un défaut servirait silencieusement les données d'un locataire à un autre, et
        rien ne le signalerait : la requête n'échoue pas, elle rend les lignes du mauvais
        client. Personne ne s'en aperçoit avant qu'un adhérent ne reconnaisse le nom d'un
        concurrent sur son écran."""
        with pytest.raises(LocataireNonEtabli):
            courant()

    def test_le_message_dit_quoi_faire(self):
        with pytest.raises(LocataireNonEtabli) as echec:
            courant()
        assert "etabli" in str(echec.value)

    def test_la_lecture_tolerante_rend_none_sans_lever(self):
        """Réservée à ce qui doit fonctionner hors requête — un journal, une sonde de
        santé. Jamais pour lire des données."""
        assert courant_ou_none() is None


class TestIsolationEntreExecutions:
    """Ce que la variable de contexte garantit, et qu'un attribut de module ne
    garantirait pas."""

    def test_deux_fils_ne_se_marchent_pas_dessus(self):
        depart = threading.Barrier(2)
        vus: dict[str, str] = {}

        def travailler(nom: str) -> None:
            with etabli(nom):
                depart.wait(timeout=5)
                vus[nom] = courant()

        with ThreadPoolExecutor(max_workers=2) as bassin:
            list(bassin.map(travailler, ["CENTRE-A", "CENTRE-B"]))

        assert vus == {"CENTRE-A": "CENTRE-A", "CENTRE-B": "CENTRE-B"}

    def test_un_fil_qui_n_a_rien_etabli_ne_herite_de_rien(self):
        """Une tâche de fond ne doit pas hériter du locataire de la dernière requête
        servie : elle doit lever."""
        echecs: list[type[BaseException]] = []

        def travailler_sans_locataire() -> None:
            try:
                courant()
            except LocataireNonEtabli as exc:
                echecs.append(type(exc))

        with etabli("CGA-BRCG"):
            fil = threading.Thread(target=travailler_sans_locataire)
            fil.start()
            fil.join(timeout=5)

        assert echecs == [LocataireNonEtabli]


class TestLaValeurEstUneConfiguration:
    """Le locataire par défaut est une valeur de déploiement, pas une constante de code."""

    def test_il_vient_de_la_configuration(self):
        from app.infrastructure.config import configuration

        assert configuration().locataire_par_defaut

    def test_aucun_depot_sql_ne_constante_le_locataire(self):
        """Le contrôle qui empêche la régression. Un dépôt SQL qui reprendrait la
        constante servirait le même client à tout le monde, sans que rien ne le signale.
        """
        from pathlib import Path

        racine = Path(__file__).resolve().parents[1] / "app" / "contextes"
        fautifs = [
            str(fichier.relative_to(racine.parent.parent))
            for fichier in racine.rglob("routes_http.py")
            if "Sql(session, LOCATAIRE_PAR_DEFAUT" in fichier.read_text(encoding="utf-8")
        ]
        assert not fautifs, (
            f"ces routes constantent le locataire d'un dépôt SQL : {fautifs}. "
            "Employer `app.partage.locataire.courant()`, établi au bord par l'intergiciel."
        )


class TestLeMandatVoyageAvecLaRequete:
    """Le locataire dit dans quelles données on travaille, le mandat à quel titre.

    Les confondre rendrait indistinguables « le comptable de la PME » et « le comptable
    du centre agissant pour elle » — précisément la question qu'un litige pose.
    """

    def test_agir_chez_soi_ne_porte_aucun_mandat(self):
        """`None` signifie « chez soi », pas « on ne sait pas »."""
        with etabli("brcg"):
            assert sous_mandat() is None

    def test_agir_ailleurs_porte_le_mandat(self):
        with etabli("station-bonaberi", mandat="mdt-001"):
            assert courant() == "station-bonaberi"
            assert sous_mandat() == "mdt-001"

    def test_il_est_restitue_comme_le_locataire(self):
        with etabli("station-bonaberi", mandat="mdt-001"):
            pass
        assert sous_mandat() is None

    def test_l_imbrication_restitue_le_mandat_exterieur(self):
        """Un travail interne mené chez soi, au milieu d'un travail sous mandat, ne doit
        pas faire croire que la suite s'exerce sans délégation."""
        with etabli("station-bonaberi", mandat="mdt-001"):
            with etabli("brcg"):
                assert sous_mandat() is None
            assert sous_mandat() == "mdt-001"

    def test_il_est_restitue_meme_en_cas_d_erreur(self):
        with pytest.raises(RuntimeError), etabli("station", mandat="mdt-001"):
            raise RuntimeError("incident")
        assert sous_mandat() is None
