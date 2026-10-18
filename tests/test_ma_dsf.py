"""Ma DSF : la déclaration annuelle vue de l'adhérent, et la relecture par l'agent
(18 octobre 2026, point 24).

⚠️ CE QUI EST GARDÉ
* l'adhérent voit ses exercices clos, l'état de chaque DSF et les pièces, sans chiffre ni nom ;
* il verse une pièce à fournir, et remplace la sienne ; jamais celle que le cabinet a traitée ;
* une DSF déposée ne reçoit plus rien ; sa copie se télécharge, visas au nom du cabinet ;
* un autre adhérent n'y accède pas (404 : on ne lui dit pas que le dossier existe) ;
* sans clé, l'agent le dit et rien ne bloque ; avec une réponse, il propose sans chiffre.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.contextes.cloture.adaptateurs.sortant.document_dsf import rendre_la_dsf
from app.contextes.cloture.adaptateurs.sortant.relecture_dsf_claude import RelectureDeLaDsf
from tests.conftest import exige_postgresql, ouvrir_une_session
from tests.test_dsf import _deposee, _texte, _valide

PDF = b"%PDF-1.4\n% attestation de visa\n%%EOF\n"
BATIMENT = "M081234567890P"
ADHERENT = "jp.nkoa@batimentplus.cm"
AUTRE_ADHERENT = "mc.essomba@lacolombe.cm"
REVISEUR = "a.bouba@cga-brcg.cm"
MA_DSF = f"/cloture/dossiers/{BATIMENT}/ma-dsf"


class TestLaCopieDuClient:
    def test_les_visas_portent_le_cabinet_et_non_les_collaborateurs(self):
        deposee = _deposee(_valide())
        interne = _texte(rendre_la_dsf(deposee))
        client = _texte(rendre_la_dsf(deposee, pour_le_client=True))

        assert "a.bouba" in interne and "l.fotso" in interne
        assert "a.bouba" not in client and "l.fotso" not in client
        assert "le cabinet" in client and "DGI-2026-DSF-001" in client


class _ClientFactice:
    """Le client de l'API, réduit à ce que `demander_en_json` appelle."""

    def __init__(self, reponse: str) -> None:
        bloc = SimpleNamespace(type="text", text=reponse)
        messages = SimpleNamespace(
            create=lambda **_: SimpleNamespace(content=[bloc], stop_reason="end_turn")
        )
        self.beta = SimpleNamespace(messages=messages)


class TestLAgent:
    def test_il_rend_ses_remarques_et_ecarte_les_vides(self):
        agent = RelectureDeLaDsf(
            cle_api="x",
            modele="m",
            client=_ClientFactice(
                '{"remarques": [{"sujet": "NACAM", "constat": "Aucune activité codée.",'
                ' "suggestion": "Renseigner le code."}, {"sujet": "", "constat": "",'
                ' "suggestion": ""}]}'
            ),
        )
        (remarque,) = agent.relire("Forme juridique : SARL")
        assert remarque.sujet == "NACAM" and "codée" in remarque.constat

    def test_une_reponse_illisible_rend_la_main(self):
        agent = RelectureDeLaDsf(cle_api="x", modele="m", client=_ClientFactice("pas du json"))
        assert agent.relire("Forme juridique : SARL") is None


def _ranger_la_dsf_2025(dsf) -> None:
    from app.contextes.cloture.adaptateurs.sortant.depots_dsf import DepotDSFSql
    from app.contextes.transverse.api import unite_de_travail
    from app.partage.locataire import etabli

    with etabli("CGA-BRCG"), unite_de_travail("CGA-BRCG") as boutique:
        DepotDSFSql(boutique.session, "CGA-BRCG").enregistrer(dsf)


@exige_postgresql
class TestParLesRoutes:
    def test_l_adherent_suit_sa_dsf_et_verse_ses_pieces(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, ADHERENT)
        vue = client.get(MA_DSF)
        assert vue.status_code == 200, vue.text
        (dernier, *_) = vue.json()["exercices"]
        assert dernier["exercice"] == "2025" and dernier["etat"] != "DEPOSEE"
        assert "resultat" not in vue.text and "par" not in dernier["pieces"][0]

        versee = client.post(
            f"{MA_DSF}/2025/pieces/1", files={"fichier": ("visa.pdf", PDF, "application/pdf")}
        )
        assert versee.status_code == 200, versee.text
        visa = next(p for p in versee.json()["exercices"][0]["pieces"] if p["numero"] == 1)
        assert visa["etat"] == "JOINTE" and visa["versable"] is True  # il peut la remplacer
        produite = client.post(
            f"{MA_DSF}/2025/pieces/2", files={"fichier": ("b.pdf", PDF, "application/pdf")}
        )
        assert produite.status_code == 409
        assert client.get(f"{MA_DSF}/2025/document").status_code == 404

        # Le cabinet la voit dans sa liste, et la déclare sans objet : elle n'est plus à lui.
        client.cookies.clear()
        ouvrir_une_session(client, REVISEUR)
        jointes = f"/cloture/dossiers/{BATIMENT}/dsf/2025/pieces-jointes"
        cabinet = next(p for p in client.get(jointes).json()["pieces"] if p["numero"] == 1)
        assert cabinet["etat"] == "JOINTE" and cabinet["nom_du_fichier"] == "visa.pdf"
        client.post(f"{jointes}/1/sans-objet", json={"motif": "Aucun visa requis pour ce dossier."})
        client.cookies.clear()
        ouvrir_une_session(client, ADHERENT)
        refus = client.post(
            f"{MA_DSF}/2025/pieces/1", files={"fichier": ("v2.pdf", PDF, "application/pdf")}
        )
        assert refus.status_code == 409 and "cabinet" in refus.text

    def test_deposee_elle_se_telecharge_et_ne_recoit_plus_rien(self, plateforme):
        client = plateforme
        _ranger_la_dsf_2025(_deposee(_valide()))
        ouvrir_une_session(client, ADHERENT)
        (dernier, *_) = client.get(MA_DSF).json()["exercices"]
        assert dernier["etat"] == "DEPOSEE" and dernier["numero_accuse"] == "DGI-2026-DSF-001"
        assert not any(p["versable"] for p in dernier["pieces"])

        copie = client.get(f"{MA_DSF}/2025/document")
        assert copie.status_code == 200 and copie.headers["content-type"] == "application/pdf"
        assert "a.bouba" not in _texte(copie.content)
        tardive = client.post(
            f"{MA_DSF}/2025/pieces/1", files={"fichier": ("visa.pdf", PDF, "application/pdf")}
        )
        assert tardive.status_code == 409

    def test_validee_elle_ne_se_telecharge_pas_encore(self, plateforme):
        client = plateforme
        _ranger_la_dsf_2025(_valide())
        ouvrir_une_session(client, ADHERENT)
        (dernier, *_) = client.get(MA_DSF).json()["exercices"]
        assert dernier["etat"] == "VALIDEE" and dernier["document"] is False
        assert client.get(f"{MA_DSF}/2025/document").status_code == 404

    def test_un_autre_adherent_n_y_accede_pas(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, AUTRE_ADHERENT)
        assert client.get(MA_DSF).status_code == 404
        verse = client.post(
            f"{MA_DSF}/2025/pieces/1", files={"fichier": ("visa.pdf", PDF, "application/pdf")}
        )
        assert verse.status_code == 404

    def test_sans_cle_l_agent_le_dit_et_rien_ne_bloque(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, REVISEUR)
        relue = client.post(f"/cloture/dossiers/{BATIMENT}/dsf/2025/relecture-de-l-agent")
        assert relue.status_code == 200, relue.text
        assert relue.json()["disponible"] is False and "sans lui" in relue.json()["message"]
        client.cookies.clear()
        ouvrir_une_session(client, ADHERENT)
        assert (
            client.post(f"/cloture/dossiers/{BATIMENT}/dsf/2025/relecture-de-l-agent").status_code
            == 403
        )
