"""La fiche signalétique de la DSF : le client la renseigne, le cabinet la valide (15 octobre 2026).

⚠️ CE QUI EST GARDÉ
* rien ne se soumet incomplet, et le refus dit ce qui manque dans les mots du client ;
* une fiche soumise ne se modifie pas ; une fiche validée qui change revient au brouillon ;
* seul le cabinet valide ; un renvoi porte son motif ;
* un adhérent n'écrit pas la fiche d'un autre dossier ;
* chaque geste est au journal d'audit.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.contextes.portefeuille.domaine.fiche_signaletique import (
    ActiviteDeclaree,
    ControleDeLEntite,
    EtatFiche,
    FicheRefusee,
    FicheSignaletique,
)
from tests.conftest import exige_postgresql, ouvrir_une_session

LE = datetime(2026, 10, 15, 10, 0)
COMPLETE = {
    "telephone": "+237 233 42 18 90",
    "ville": "Douala",
    "adresse_geographique": "Immeuble Ekwalla, rue Joss, Bonanjo",
    "controle": "PRIVE_NATIONAL",
    "signataire_nom": "NKOA Jean-Pierre",
    "signataire_qualite": "Gérant",
    "banque": "Afriland First Bank",
    "numero_de_compte": "10005-00001-01234567890-12",
    "activites": [
        {
            "designation": "Construction de bâtiments",
            "code_nacam": "4100",
            "chiffre_affaires_ht": "0",
        }
    ],
}


class TestLeCycle:
    def _complete(self) -> FicheSignaletique:
        return FicheSignaletique().modifier(
            {
                **{k: v for k, v in COMPLETE.items() if k not in ("activites", "controle")},
                "controle": ControleDeLEntite.PRIVE_NATIONAL,
                "activites": (ActiviteDeclaree(designation="Construction", code_nacam="4100"),),
            },
            par="A-001",
            a_l_instant=LE,
        )

    def test_une_fiche_vide_dit_tout_ce_qui_manque(self):
        manque = FicheSignaletique().manquants()

        assert "le numéro de téléphone de l'entreprise" in manque
        assert "au moins une activité, avec son code NACAM" in manque
        with pytest.raises(FicheRefusee, match="il manque encore"):
            FicheSignaletique().soumettre(a_l_instant=LE)

    def test_soumise_elle_ne_se_modifie_pas_puis_validee_elle_revient_au_brouillon(self):
        soumise = self._complete().soumettre(a_l_instant=LE)
        with pytest.raises(FicheRefusee):
            soumise.modifier({"ville": "Yaoundé"}, par="A-001", a_l_instant=LE)

        validee = soumise.valider(par="C-003", a_l_instant=LE)
        assert validee.etat is EtatFiche.VALIDEE
        reprise = validee.modifier({"ville": "Yaoundé"}, par="A-001", a_l_instant=LE)
        assert reprise.etat is EtatFiche.BROUILLON and reprise.validee_par is None

    def test_ce_qui_est_saisi_est_valide(self):
        """Une activité reçue en dictionnaire devient une activité, ou est refusée."""
        fiche = FicheSignaletique().modifier(
            {"activites": [{"designation": "Négoce de ciment", "code_nacam": "4673"}]},
            par="A-001",
            a_l_instant=LE,
        )
        assert isinstance(fiche.activites[0], ActiviteDeclaree)
        with pytest.raises(ValueError):
            FicheSignaletique().modifier(
                {"activites": [{"designation": "x", "code_nacam": ""}]}, par="A-001", a_l_instant=LE
            )

    def test_un_renvoi_porte_son_motif(self):
        soumise = self._complete().soumettre(a_l_instant=LE)
        with pytest.raises(FicheRefusee):
            soumise.renvoyer(motif="non")

        renvoyee = soumise.renvoyer(motif="Le code NACAM 4100 ne correspond pas à votre activité.")
        assert renvoyee.etat is EtatFiche.BROUILLON and "NACAM" in renvoyee.motif_du_renvoi


BATIMENT = "M081234567890P"
URL = f"/portefeuille/entreprises/{BATIMENT}/fiche-signaletique"
ADHERENT = "jp.nkoa@batimentplus.cm"
AUTRE_ADHERENT = "mc.essomba@lacolombe.cm"
REVISEUR = "a.bouba@cga-brcg.cm"


@exige_postgresql
class TestParLesRoutes:
    def test_le_client_renseigne_soumet_et_le_cabinet_valide(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, ADHERENT)

        vue = client.get(URL).json()
        assert vue["fiche"]["etat"] == "BROUILLON" and vue["manquants"]
        assert vue["denomination"] == "SARL BATIMENT PLUS"

        incomplet = client.put(URL, json={"telephone": "+237 233 42 18 90"})
        assert incomplet.status_code == 200, incomplet.text
        refus = client.post(f"{URL}/soumission")
        assert refus.status_code == 409 and "il manque encore" in refus.text

        assert client.put(URL, json=COMPLETE).status_code == 200
        soumise = client.post(f"{URL}/soumission")
        assert soumise.status_code == 200, soumise.text
        assert soumise.json()["fiche"]["etat"] == "SOUMISE"
        assert client.post(f"{URL}/validation").status_code == 403  # le client ne valide pas

        client.cookies.clear()
        ouvrir_une_session(client, REVISEUR)
        validee = client.post(f"{URL}/validation")
        assert validee.status_code == 200, validee.text
        assert validee.json()["fiche"]["etat"] == "VALIDEE"

        journal = client.get("/transverse/audit", params={"objet_type": "fiche_dsf"}).json()
        actions = {e["action"] for e in journal}
        assert {"portefeuille.fiche_dsf_soumise", "portefeuille.fiche_dsf_validee"} <= actions

    def test_le_cabinet_renvoie_avec_un_motif(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, ADHERENT)
        client.put(URL, json=COMPLETE)
        client.post(f"{URL}/soumission")
        client.cookies.clear()
        ouvrir_une_session(client, REVISEUR)

        renvoi = client.post(f"{URL}/renvoi", json={"motif": "Le numéro de compte est incomplet."})

        assert renvoi.status_code == 200, renvoi.text
        assert renvoi.json()["fiche"]["motif_du_renvoi"] == "Le numéro de compte est incomplet."

    def test_un_adherent_n_ecrit_pas_la_fiche_d_un_autre_dossier(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, AUTRE_ADHERENT)

        assert client.put(URL, json=COMPLETE).status_code in (403, 404)
        assert client.get(URL).status_code in (403, 404)
