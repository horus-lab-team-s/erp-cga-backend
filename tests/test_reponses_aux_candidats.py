"""Le traitement des candidatures et la réponse aux candidats (18 octobre 2026, point 25).

⚠️ CE QUI EST GARDÉ
* l'offre prend une image (type lu dans les octets), servie au site une fois publiée ;
* une décision finale ne se revoit pas sans dire pourquoi ;
* la réponse part d'un texte type rempli, se relit, part par courriel, et reste gardée ;
* un « [à compléter] » oublié est refusé ;
* lire les CV et répondre exige `GERER_RECRUTEMENT` : l'administrateur, qui édite le site,
  ne lit pas les dossiers des candidats.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.contextes.vitrine.domaine.recrutement import (
    Candidature,
    CandidatureRefusee,
    EtatCandidature,
)
from tests.conftest import exige_postgresql, ouvrir_une_session

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
DIRECTRICE = "b.mballa@cga-brcg.cm"
ADMINISTRATEUR = "s.onana@cga-brcg.cm"
LE = datetime(2026, 10, 18, 9)


def _candidature() -> Candidature:
    return Candidature(
        identifiant="cand-1",
        offre="offre-1",
        nom="Ngo Biyong Claire",
        telephone="699887766",
        cv_empreinte="x",
        cv_nom="cv.pdf",
        recue_le=LE,
    )


class TestLaCandidature:
    def test_une_decision_finale_ne_se_revoit_pas_sans_motif(self):
        retenue = _candidature().decider(EtatCandidature.RETENUE, par="C-001", le=LE)
        with pytest.raises(CandidatureRefusee, match="dire pourquoi"):
            retenue.decider(EtatCandidature.NON_RETENUE, par="C-001", le=LE)
        revue = retenue.decider(
            EtatCandidature.NON_RETENUE, par="C-001", le=LE, note="désistement du candidat"
        )
        assert [d.etat for d in revue.decisions] == [
            EtatCandidature.RETENUE,
            EtatCandidature.NON_RETENUE,
        ]
        assert _candidature().appellation == "Ngo"


@exige_postgresql
class TestParLesRoutes:
    SAISIE = {
        "intitule": "Comptable confirmé",
        "lieu": "Douala, Akwa",
        "type_de_contrat": "CDI",
        "description": "Rejoindre l'équipe.",
        "qualifications": "### Profil\n- Licence en comptabilité",
    }

    def _offre_illustree_et_publiee(self, client) -> str:
        ouvrir_une_session(client, DIRECTRICE)
        identifiant = client.post("/vitrine/cabinet/offres", json=self.SAISIE).json()["offre"][
            "identifiant"
        ]
        faux = client.put(
            f"/vitrine/cabinet/offres/{identifiant}/image",
            files={"image": ("page.png", b"<html><script>", "image/png")},
        )
        assert faux.status_code == 415, faux.text
        image = client.put(
            f"/vitrine/cabinet/offres/{identifiant}/image",
            files={"image": ("bureau.png", PNG, "image/png")},
        )
        assert image.status_code == 200, image.text
        assert client.post(f"/vitrine/cabinet/offres/{identifiant}/publication").status_code == 200
        client.cookies.clear()
        return identifiant

    def test_de_l_image_a_la_reponse_envoyee(self, plateforme):
        client = plateforme
        identifiant = self._offre_illustree_et_publiee(client)
        assert client.get(f"/vitrine/offres/{identifiant}").json()["image"] is True
        servie = client.get(f"/vitrine/offres/{identifiant}/image")
        assert servie.status_code == 200 and servie.headers["content-type"] == "image/png"

        client.post(
            f"/vitrine/offres/{identifiant}/candidatures",
            data={
                "nom": "Ngo Biyong Claire",
                "telephone": "699887766",
                "courriel": "claire.ngo@exemple.cm",
                "message": "Motivée.",
            },
            files={"cv": ("cv.pdf", PDF, "application/pdf")},
        )
        ouvrir_une_session(client, DIRECTRICE)
        (candidature,) = client.get(f"/vitrine/cabinet/offres/{identifiant}/candidatures").json()
        cid = candidature["identifiant"]
        assert candidature["etat"] == "RECUE"

        decidee = client.post(
            f"/vitrine/cabinet/candidatures/{cid}/decision",
            json={"decision": "ENTRETIEN", "note": "profil solide en fiscalité"},
        )
        assert decidee.status_code == 200 and decidee.json()["etat"] == "ENTRETIEN"

        proposee = client.get(f"/vitrine/cabinet/candidatures/{cid}/reponse-proposee").json()
        assert proposee["source"] == "MODELE" and "Comptable confirmé" in proposee["corps"]
        assert proposee["corps"].startswith("Bonjour Ngo")
        # Le modèle d'entretien laisse la date à compléter : envoyé tel quel, il est refusé.
        refus = client.post(
            f"/vitrine/cabinet/candidatures/{cid}/reponse",
            json={"objet": proposee["objet"], "corps": proposee["corps"]},
        )
        assert refus.status_code == 422 and "compléter" in refus.text

        corps = proposee["corps"].replace("[à compléter]", "mardi 21 octobre, 10 h")
        envoyee = client.post(
            f"/vitrine/cabinet/candidatures/{cid}/reponse",
            json={"objet": proposee["objet"], "corps": corps},
        )
        assert envoyee.status_code == 200, envoyee.text
        (reponse,) = envoyee.json()["reponses"]
        assert reponse["envoyee"] is True and "mardi 21 octobre" in reponse["corps"]

        from app.contextes.transverse.api import service_de_notification

        (courriel, *_) = service_de_notification().derniers("recrutement.reponse")
        assert courriel.destinataire == "claire.ngo@exemple.cm"

    def test_l_administrateur_n_ouvre_pas_les_dossiers_des_candidats(self, plateforme):
        client = plateforme
        identifiant = self._offre_illustree_et_publiee(client)
        ouvrir_une_session(client, ADMINISTRATEUR)
        refus = client.get(f"/vitrine/cabinet/offres/{identifiant}/candidatures")
        assert refus.status_code == 403, refus.text
