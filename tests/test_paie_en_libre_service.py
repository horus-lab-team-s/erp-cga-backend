"""L'adhérent tient la paie de SON entreprise, et d'aucune autre.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER PROTÈGE

Les routes de paie exigeaient `LIRE_COMPTABILITE`, que le rôle adhérent ne détient
pas. Le refus était délibéré : un bulletin porte des rémunérations nominatives.
Mais il fermait aussi le libre service, que le cabinet veut offrir — l'entreprise
qui tient sa paie elle-même, au tarif d'une prestation, sans déléguer.

La permission `TENIR_SA_PAIE` ouvre cela, et **elle seule** : elle n'ouvre ni la
balance, ni le grand livre, ni le journal.

⚠️ C'EST LE CLOISONNEMENT QU'ON TOUCHE, DONC C'EST LUI QU'ON ÉPROUVE.

Une permission nouvelle sur un rôle client est l'endroit du produit où une erreur
coûte le plus cher : elle ne se voit pas à l'écran, elle se découvre le jour où un
adhérent lit les salaires d'un concurrent. Les cas ci-dessous ne vérifient donc
pas seulement que le libre service marche ; ils vérifient d'abord qu'il **ne
déborde pas**.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from tests.conftest import exige_postgresql, ouvrir_une_session

pytestmark = exige_postgresql

#: Titulaire de SARL BATIMENT PLUS, et de rien d'autre.
ADHERENT = "jp.nkoa@batimentplus.cm"
SON_DOSSIER = "M081234567890P"

#: Titulaire de BOULANGERIE LA COLOMBE. Son dossier est, pour le premier, un
#: dossier qui n'existe pas.
AUTRE_DOSSIER = "M071122334455J"

COMPTABLE = "l.fotso@cga-brcg.cm"


class TestLAdherentTientSaPaie:
    def test_il_lit_le_fichier_du_personnel_de_son_entreprise(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, ADHERENT)

        reponse = client.get(
            f"/social/dossiers/{SON_DOSSIER}/salaries", params={"a_la_date": "2026-10-10"}
        )
        assert reponse.status_code == 200, reponse.text
        assert isinstance(reponse.json(), list)

    def test_il_lit_la_declaration_mensuelle_de_son_entreprise(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, ADHERENT)

        reponse = client.get(
            f"/social/dossiers/{SON_DOSSIER}/declaration/2026/8",
            params={"a_la_date": "2026-10-10"},
        )
        assert reponse.status_code in (200, 404), reponse.text
        # 404 veut dire « pas de paie ce mois-là », ce qui est une réponse ;
        # 403 voudrait dire que la permission n'ouvre rien, et serait le défaut.
        assert reponse.status_code != 403, "la permission n'ouvre pas la déclaration"


class TestEtIlNeVoitAucuneAutre:
    """⚠️ LES CAS QUI COMPTENT VRAIMENT.

    Sans eux, les deux précédents prouveraient seulement qu'on a ouvert une porte,
    pas qu'on l'a ouverte au bon endroit.
    """

    def test_la_paie_d_un_autre_dossier_n_existe_pas_pour_lui(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, ADHERENT)

        reponse = client.get(
            f"/social/dossiers/{AUTRE_DOSSIER}/salaries", params={"a_la_date": "2026-10-10"}
        )
        # ⚠️ 404 et NON 403. Un 403 dirait « ce dossier existe, et il ne vous
        # regarde pas », ce qui apprend à un adhérent quels NIU le cabinet suit,
        # c'est-à-dire la liste de ses clients.
        assert reponse.status_code == 404, reponse.text
        assert AUTRE_DOSSIER not in reponse.text or "accessible" in reponse.text

    def test_il_n_inscrit_pas_un_salarie_chez_un_autre(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, ADHERENT)

        reponse = client.post(
            f"/social/dossiers/{AUTRE_DOSSIER}/salaries",
            json={"matricule": "X-999", "nom": "ESSAI", "prenom": "Intrusion"},
        )
        assert reponse.status_code == 404, reponse.text

    def test_la_permission_n_ouvre_pas_les_livres(self, plateforme):
        """⚠️ LA PORTE EST ÉTROITE, ET C'EST TOUT SON INTÉRÊT.

        Si `TENIR_SA_PAIE` ouvrait la comptabilité, on aurait donné au client la
        balance et le journal de son dossier pour qu'il voie ses bulletins. Ce
        cas-ci est la contre-épreuve de la permission elle-même.
        """
        client = plateforme
        ouvrir_une_session(client, ADHERENT)

        balance = client.get(
            f"/comptabilite/dossiers/{SON_DOSSIER}/balance", params={"exercice": "2026"}
        )
        assert balance.status_code == 403, (
            "la paie en libre service a ouvert la comptabilité du dossier : "
            f"{balance.status_code} {balance.text[:200]}"
        )


class TestLeCabinetGardeSaVoie:
    """La route sert deux rôles sans permission commune, et les deux passent."""

    def test_le_comptable_lit_toujours_la_paie_de_ses_dossiers(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)

        reponse = client.get(
            f"/social/dossiers/{SON_DOSSIER}/salaries", params={"a_la_date": "2026-10-10"}
        )
        assert reponse.status_code == 200, reponse.text
