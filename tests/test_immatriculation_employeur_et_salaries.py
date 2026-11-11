"""L'immatriculation de l'entreprise et de ses employés (9 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
La liste du responsable juridique du cabinet : « les documents dont nous avons besoin pour
votre immatriculation et celles de vos employés ». Elle suit la création : la société a
alors son cachet, son téléphone, son adresse.

⚠️ CE QUI EST GARDÉ
* le dossier de création porte ces pièces au moment IMMATRICULATION ; elles ne retiennent
  pas le dépôt du dossier de constitution ;
* chaque salarié porte ce que son immatriculation demande (téléphone, quartier, adresse
  mail, arrondissements de naissance des parents) ; la liste du personnel dit ce qui manque,
  et cela se complète après l'embauche ;
* les pièces de chaque employé se lisent telles que le cabinet les a écrites.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from app.contextes.creation_entreprise.api import checklist_de
from app.contextes.portefeuille.contrats import FormeJuridique
from tests.conftest import exige_postgresql, ouvrir_une_session

REVISEUR = "a.bouba@cga-brcg.cm"
SANS_PERSONNEL = "P019876543210K"


def test_les_pieces_d_immatriculation_suivent_la_creation():
    pieces = {p.code: p for p in checklist_de(FormeJuridique.ETS)}
    for code in ("ID_GERANT", "COORDONNEES_ENTREPRISE", "LISTE_PERSONNEL", "CACHET_ENTREPRISE"):
        assert pieces[code].moment == "IMMATRICULATION", code
    assert pieces["LISTE_PERSONNEL"].libelle == "Liste du personnel"


def test_elles_ne_retiennent_pas_le_depot_de_la_constitution():
    from datetime import date

    from app.contextes.creation_entreprise.api import DossierCreation

    champs = DossierCreation.model_fields
    assert "pieces" in champs
    pieces = tuple(
        p
        if p.moment == "IMMATRICULATION"
        else p.model_copy(update={"fournie_le": date(2026, 11, 9)})
        for p in checklist_de(FormeJuridique.ETS)
    )
    # Toutes les pièces de la constitution reçues, aucune de l'immatriculation : le dossier
    # peut partir au guichet unique, l'immatriculation attend son heure.
    dossier = DossierCreation.model_construct(pieces=pieces)
    assert DossierCreation.pieces_manquantes.fget(dossier) == ()
    assert len(DossierCreation.pieces_d_immatriculation_manquantes.fget(dossier)) == 4


@exige_postgresql
class TestLeSalarie:
    def test_ce_qui_manque_se_lit_puis_se_complete(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, REVISEUR)
        cree = client.post(
            f"/social/dossiers/{SANS_PERSONNEL}/salaries",
            json={
                "matricule": "SAL-IMM-001",
                "nom": "Mbarga",
                "prenom": "Aline",
                "telephone": "677001122",
                "quartier": "Bonamoussadi",
            },
        )
        assert cree.status_code == 201, cree.text
        (ligne,) = [
            s
            for s in client.get(
                f"/social/dossiers/{SANS_PERSONNEL}/salaries?a_la_date=2026-11-09"
            ).json()
            if s["matricule"] == "SAL-IMM-001"
        ]
        assert "téléphone" not in ligne["a_completer"]
        assert "arrondissement de naissance du père" in ligne["a_completer"]

        complete = client.put(
            f"/social/dossiers/{SANS_PERSONNEL}/salaries/SAL-IMM-001/coordonnees",
            json={
                "date_naissance": "1994-03-02",
                "courriel": "aline@exemple.cm",
                "arrondissement_naissance_pere": "Douala 3e",
                "arrondissement_naissance_mere": "Édéa 1er",
            },
        )
        assert complete.status_code == 200, complete.text
        assert complete.json()["quartier"] == "Bonamoussadi"  # inchangé
        (ligne,) = [
            s
            for s in client.get(
                f"/social/dossiers/{SANS_PERSONNEL}/salaries?a_la_date=2026-11-09"
            ).json()
            if s["matricule"] == "SAL-IMM-001"
        ]
        assert ligne["a_completer"] == []

    def test_les_pieces_de_l_employe(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, REVISEUR)
        pieces = client.get("/social/pieces-de-l-employe").json()
        assert pieces[0].startswith("Une copie certifiée non timbrée de la CNI")
        assert "Le plan de localisation de l'employé" in pieces
