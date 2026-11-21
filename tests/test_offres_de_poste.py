"""Les offres de poste du cabinet, lues sur le site avant de postuler (16 octobre 2026).

⚠️ CE QUI EST GARDÉ
* le mini-éditeur produit un Markdown réduit (titres, listes, gras, italique) que le
  site reçoit en blocs : aucun HTML n'est interprété, un chevron reste du texte ;
* une offre ne se voit sur le site qu'une fois publiée, et jusqu'à sa date limite
  comprise ; close, elle disparaît ;
* on postule avec un CV PDF (type vérifié sur le contenu), une fois par téléphone ;
* seul `EDITER_VITRINE` (direction, administrateur) rédige, publie et lit les CV.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.contextes.vitrine.domaine.recrutement import (
    EtatOffre,
    OffreDePoste,
    OffreRefusee,
    TypeDeContrat,
)
from app.contextes.vitrine.domaine.texte_riche import en_blocs
from tests.conftest import exige_postgresql, ouvrir_une_session

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


class TestLeTexteRiche:
    def test_titres_listes_et_emphases_deviennent_des_blocs(self):
        blocs = en_blocs(
            "### Profil recherché\n"
            "- Licence en **comptabilité**\n"
            "- Maîtrise du *SYSCOHADA*\n"
            "\n"
            "Une première ligne\n"
            "suivie d'une seconde."
        )
        assert [b.type for b in blocs] == ["intertitre", "liste", "paragraphe"]
        assert blocs[0].texte == "Profil recherché"
        premier = blocs[1].points[0]
        assert [(s.texte, s.gras) for s in premier] == [
            ("Licence en ", False),
            ("comptabilité", True),
        ]
        assert blocs[1].points[1][1].italique is True
        assert blocs[2].segments[0].texte == "Une première ligne suivie d'une seconde."

    def test_aucun_html_n_est_interprete(self):
        (bloc,) = en_blocs("<script>alert(1)</script> et <b>gras</b>")
        assert bloc.type == "paragraphe"
        assert bloc.segments[0].texte == "<script>alert(1)</script> et <b>gras</b>"

    def test_un_texte_vide_ne_rend_rien(self):
        assert en_blocs("") == [] and en_blocs("\n  \n") == []


def _offre(**surcharges) -> OffreDePoste:
    champs = {
        "identifiant": "offre-1",
        "intitule": "Comptable confirmé",
        "lieu": "Douala",
        "type_de_contrat": TypeDeContrat.CDI,
        "qualifications": "- Trois ans d'expérience",
        "creee_le": datetime(2026, 10, 1, 9, 0),
        "creee_par": "C-001",
    }
    return OffreDePoste(**(champs | surcharges))


class TestLeCycle:
    def test_la_date_limite_est_comprise_et_l_offre_close_disparait(self):
        publiee = _offre(date_limite=date(2026, 10, 31)).publier(
            datetime(2026, 10, 16, 9, 0), jour=date(2026, 10, 16)
        )
        assert publiee.ouverte_au(date(2026, 10, 31)) is True
        assert publiee.ouverte_au(date(2026, 11, 1)) is False
        close = publiee.clore(datetime(2026, 10, 20, 9, 0))
        assert close.etat is EtatOffre.CLOSE and close.ouverte_au(date(2026, 10, 20)) is False

    def test_un_brouillon_ne_se_voit_pas_et_une_date_passee_ne_se_publie_pas(self):
        assert _offre().ouverte_au(date(2026, 10, 16)) is False
        with pytest.raises(OffreRefusee):
            _offre(date_limite=date(2026, 10, 15)).publier(
                datetime(2026, 10, 16, 9, 0), jour=date(2026, 10, 16)
            )
        with pytest.raises(OffreRefusee):
            _offre().clore(datetime(2026, 10, 16, 9, 0))


@exige_postgresql
class TestParLesRoutes:
    SAISIE = {
        "intitule": "Comptable confirmé",
        "lieu": "Douala, Akwa",
        "type_de_contrat": "CDI",
        "description": "Rejoindre l'équipe **tenue comptable**.",
        "qualifications": "### Profil\n- Licence en comptabilité\n- **Trois ans** d'expérience",
    }

    def _publiee(self, client) -> str:
        ouvrir_une_session(client, "b.mballa@cga-brcg.cm")
        creee = client.post("/vitrine/cabinet/offres", json=self.SAISIE)
        assert creee.status_code == 201, creee.text
        identifiant = creee.json()["offre"]["identifiant"]
        client.cookies.clear()
        assert client.get(f"/vitrine/offres/{identifiant}").status_code == 404  # brouillon
        ouvrir_une_session(client, "b.mballa@cga-brcg.cm")
        publiee = client.post(f"/vitrine/cabinet/offres/{identifiant}/publication")
        assert publiee.status_code == 200, publiee.text
        client.cookies.clear()
        return identifiant

    def _postuler(self, client, identifiant, *, telephone="699887766", cv=PDF):
        return client.post(
            f"/vitrine/offres/{identifiant}/candidatures",
            data={"nom": "Ngo Biyong Claire", "telephone": telephone, "message": "Motivée."},
            files={"cv": ("cv-claire.pdf", cv, "application/pdf")},
        )

    def test_le_candidat_lit_les_qualifications_puis_postule(self, plateforme):
        client = plateforme
        identifiant = self._publiee(client)

        offres = client.get("/vitrine/offres").json()
        offre = next(o for o in offres if o["identifiant"] == identifiant)
        assert [b["type"] for b in offre["qualifications"]] == ["intertitre", "liste"]
        gras = offre["qualifications"][1]["points"][1][0]
        assert (gras["texte"], gras["gras"]) == ("Trois ans", True)

        assert self._postuler(client, identifiant).status_code == 201
        # Le même téléphone, une seule candidature.
        assert self._postuler(client, identifiant, telephone="699 887 766").status_code == 201
        assert self._postuler(client, identifiant, cv=b"<html>").status_code == 415

        ouvrir_une_session(client, "b.mballa@cga-brcg.cm")
        recues = client.get(f"/vitrine/cabinet/offres/{identifiant}/candidatures").json()
        assert len(recues) == 1 and recues[0]["nom"] == "Ngo Biyong Claire"
        cv = client.get(f"/vitrine/cabinet/candidatures/{recues[0]['identifiant']}/cv")
        assert cv.status_code == 200 and cv.content == PDF
        # Un brouillon n'apparaît pas dans la liste du site.
        brouillon = client.post("/vitrine/cabinet/offres", json=self.SAISIE).json()["offre"]
        client.cookies.clear()
        visibles = {o["identifiant"] for o in client.get("/vitrine/offres").json()}
        assert identifiant in visibles and brouillon["identifiant"] not in visibles
        # Un comptable ne lit pas les CV.
        ouvrir_une_session(client, "l.fotso@cga-brcg.cm")
        url_cv = f"/vitrine/cabinet/candidatures/{recues[0]['identifiant']}/cv"
        assert client.get(url_cv).status_code == 403
        client.cookies.clear()
        ouvrir_une_session(client, "b.mballa@cga-brcg.cm")
        liste = client.get("/vitrine/cabinet/offres").json()
        assert (
            next(o for o in liste if o["offre"]["identifiant"] == identifiant)["candidatures"] == 1
        )

    def test_le_candidat_recoit_un_accuse_de_reception(self, plateforme):
        """⚠️ 19 octobre 2026 (point 25) : rien ne partait avant la réponse du cabinet."""
        from app.contextes.transverse.adaptateurs.entrant.dependances import (
            service_de_notification,
        )

        client = plateforme
        identifiant = self._publiee(client)
        reponse = client.post(
            f"/vitrine/offres/{identifiant}/candidatures",
            data={
                "nom": "Ngo Biyong Claire",
                "telephone": "699112233",
                "courriel": "claire.ngo@exemple.cm",
                "message": "Motivée.",
            },
            files={"cv": ("cv-claire.pdf", PDF, "application/pdf")},
        )
        assert reponse.status_code == 201, reponse.text
        assert "accusé" in reponse.json()["message"]
        (accuse, *_) = service_de_notification().derniers("recrutement.accuse")
        assert accuse.destinataire == "claire.ngo@exemple.cm"
        assert accuse.contexte["poste"] == "Comptable confirmé"

    def test_sans_adresse_aucun_accuse_ne_part(self, plateforme):
        from app.contextes.transverse.adaptateurs.entrant.dependances import (
            service_de_notification,
        )

        client = plateforme
        avant = len(service_de_notification().derniers("recrutement.accuse"))
        assert self._postuler(client, self._publiee(client)).status_code == 201
        assert len(service_de_notification().derniers("recrutement.accuse")) == avant

    def test_close_elle_quitte_le_site_et_ne_recoit_plus_rien(self, plateforme):
        client = plateforme
        identifiant = self._publiee(client)
        ouvrir_une_session(client, "s.onana@cga-brcg.cm")  # administrateur
        assert client.post(f"/vitrine/cabinet/offres/{identifiant}/cloture").status_code == 200
        assert (
            client.put(f"/vitrine/cabinet/offres/{identifiant}", json=self.SAISIE).status_code
            == 409
        )
        client.cookies.clear()
        assert client.get(f"/vitrine/offres/{identifiant}").status_code == 404
        assert self._postuler(client, identifiant).status_code == 404

    def test_seul_qui_edite_la_vitrine_redige_et_lit_les_cv(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "l.fotso@cga-brcg.cm")  # comptable
        assert client.post("/vitrine/cabinet/offres", json=self.SAISIE).status_code == 403
        assert client.get("/vitrine/cabinet/offres").status_code == 403
        client.cookies.clear()
        assert client.get("/vitrine/cabinet/offres").status_code in (401, 403)

    def test_une_date_limite_echue_retire_l_offre_du_site(self, plateforme):
        from app.partage.horloge import aujourd_hui

        client = plateforme
        ouvrir_une_session(client, "b.mballa@cga-brcg.cm")
        hier = (aujourd_hui() - timedelta(days=1)).isoformat()
        creee = client.post("/vitrine/cabinet/offres", json=self.SAISIE | {"date_limite": hier})
        identifiant = creee.json()["offre"]["identifiant"]
        refus = client.post(f"/vitrine/cabinet/offres/{identifiant}/publication")
        assert refus.status_code == 409 and "date limite" in refus.json()["detail"]
