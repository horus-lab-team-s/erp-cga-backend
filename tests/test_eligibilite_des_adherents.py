"""La revue des adhérents sortis, ou sortant, du champ de l'article 118.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Entre l'admission, qui lit un chiffre déclaré, et la liasse, qui refuse l'abattement
des mois après la clôture, rien ne disait au Centre qu'un adhérent grandissait. Ces
cas gardent la revue qui comble l'intervalle, et les trois choses qui la rendent
lisible : elle ne revoit que les adhérents, elle place le fait avant la prévision,
et elle ne parle pas le vocabulaire du régime fiscal.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.contextes.comptabilite.api import balance
from app.contextes.obligations.application.eligibilite_des_adherents import (
    revoir_l_eligibilite,
)
from app.contextes.portefeuille.api import PORTEFEUILLE_DEMO
from app.contextes.referentiel.contrats import Borne
from app.partage.copie import transiter
from app.partage.horloge import maintenant
from tests.conftest import exige_postgresql, ouvrir_une_session

# ⚠️ Les aides de la surveillance du régime, et non une copie : la vente d'essai et
# les exercices contigus sont les mêmes objets, et deux copies divergeraient.
from tests.test_surveillance_des_seuils import _exercices, _vente

SEUIL = Decimal(100000000)
#: Adhérent depuis 2022, sans interruption.
ADHERENT = "M081234567890P"
#: Un second adhérent, pour le tri.
AUTRE_ADHERENT = "M071122334455J"
LE_JOUR = date(2026, 11, 2)


def _dossier(niu, *, adherent=True):
    modele = PORTEFEUILLE_DEMO[niu]
    champs = {"exercices": _exercices((2025, True), (2026, False))}
    if not adherent:
        champs["adhesions"] = []
    return transiter(modele, **champs)


def _revoir(dossiers, livres, *, a_la_date=LE_JOUR):
    return revoir_l_eligibilite(
        dossiers,
        soldes_de=lambda niu, exercice: livres.get((niu, exercice), []),
        seuil=SEUIL,
        borne=Borne.EXCLUSE,
        a_la_date=a_la_date,
    )


class TestCeQueLaRevueDit:
    def test_un_exercice_clos_au_dela_du_seuil_est_un_fait(self):
        livres = {(ADHERENT, "2025"): balance([_vente(130000000, exercice="2025")])}
        (revue,) = _revoir([_dossier(ADHERENT)], livres)
        assert revue.hors_champ is True
        assert revue.a_surveiller is True
        assert revue.numero_adhesion == "ADH-2022-014"
        assert revue.acquis is not None and revue.acquis.clos is True

    def test_a_la_valeur_meme_du_seuil_l_adherent_reste_dans_le_champ(self):
        """⚠️ « N'excède pas » : la borne du pas 49, mesurée ici au franc près."""
        livres = {(ADHERENT, "2025"): balance([_vente(100000000, exercice="2025")])}
        (revue,) = _revoir([_dossier(ADHERENT)], livres)
        assert revue.hors_champ is False
        (revue_du_franc_suivant,) = _revoir(
            [_dossier(ADHERENT)],
            {(ADHERENT, "2025"): balance([_vente(100000001, exercice="2025")])},
        )
        assert revue_du_franc_suivant.hors_champ is True

    def test_un_exercice_en_cours_deja_au_dela_reste_a_surveiller(self):
        """⚠️ **Le piège de l'alerte anticipée.**

        Le diagnostic ne lève plus d'alerte une fois le seuil franchi. Un adhérent à
        120 % sur son exercice en cours sortirait de la revue au moment précis où il
        faut l'appeler, si la revue ne regardait que l'alerte.
        """
        livres = {(ADHERENT, "2026"): balance([_vente(120000000, exercice="2026")])}
        (revue,) = _revoir([_dossier(ADHERENT)], livres)
        assert revue.en_cours is not None
        assert revue.en_cours.au_dela_du_seuil is True
        assert revue.en_cours.alerte_anticipee is False, "la contre-épreuve du piège"
        assert revue.hors_champ is False
        assert revue.a_surveiller is True

    def test_un_exercice_en_cours_qui_approche_est_a_surveiller(self):
        livres = {(ADHERENT, "2026"): balance([_vente(85000000, exercice="2026")])}
        (revue,) = _revoir([_dossier(ADHERENT)], livres)
        assert revue.en_cours.alerte_anticipee is True
        assert revue.a_surveiller is True

    def test_loin_du_seuil_on_ne_derange_personne(self):
        livres = {(ADHERENT, "2026"): balance([_vente(20000000, exercice="2026")])}
        (revue,) = _revoir([_dossier(ADHERENT)], livres)
        assert revue.a_surveiller is False


class TestCeQueLaRevueNeDitPas:
    def test_un_dossier_hors_du_centre_n_est_pas_revu(self):
        """Un non-adhérent à 300 millions n'appelle aucune démarche du Centre ; le
        faire figurer noierait les adhérents qui en appellent une."""
        livres = {(ADHERENT, "2025"): balance([_vente(300000000, exercice="2025")])}
        assert _revoir([_dossier(ADHERENT, adherent=False)], livres) == []

    def test_la_revue_ne_parle_pas_le_vocabulaire_du_regime(self):
        """⚠️ Un « reclassement requis » dans une revue d'adhésion ferait lire à un
        collaborateur qu'un adhérent doit changer de régime fiscal."""
        livres = {(ADHERENT, "2025"): balance([_vente(130000000, exercice="2025")])}
        (revue,) = _revoir([_dossier(ADHERENT)], livres)
        rendu = str(revue.model_dump(mode="json"))
        assert "reclassement" not in rendu
        assert "regime" not in rendu


class TestLeTri:
    def test_le_fait_passe_devant_la_prevision_meme_a_taux_inferieur(self):
        livres = {
            # Clos, juste au-delà : 101 %.
            (ADHERENT, "2025"): balance([_vente(101000000, exercice="2025")]),
            # En cours, bien au-delà : 150 %, mais une prévision.
            (AUTRE_ADHERENT, "2026"): balance([_vente(150000000, exercice="2026")]),
        }
        revues = _revoir([_dossier(AUTRE_ADHERENT), _dossier(ADHERENT)], livres)
        assert [r.niu for r in revues] == [ADHERENT, AUTRE_ADHERENT]
        # ⚠️ La contre-épreuve du tri : le second affiche bien le taux le plus fort.
        assert revues[1].en_cours.taux_d_approche > revues[0].acquis.taux_d_approche


class TestParLaRoute:
    pytestmark = exige_postgresql

    REVISEUR = "a.bouba@cga-brcg.cm"
    COMPTABLE = "c.ndongo@cga-brcg.cm"  # portefeuille : P019876543210K, P027788990011M
    JUSTIFICATION = "Lettre de résiliation reçue du gérant, datée et signée ce mois-ci."

    def _vendre(self, client, niu, montant):
        saisie = client.post(
            f"/comptabilite/dossiers/{niu}/ecritures",
            json={
                "journal": "VE", "exercice": "2026", "date_operation": "2026-06-30",
                "libelle": "Ventes de l'exercice", "piece_justificative": "PJ-VE-ELIG",
                "lignes": [
                    {"compte": "411", "libelle": "Client", "sens": "DEBIT",
                     "montant": str(montant)},
                    {"compte": "701", "libelle": "Vente", "sens": "CREDIT",
                     "montant": str(montant)},
                ],
            },
        )
        assert saisie.status_code == 201, saisie.text
        numero = saisie.json()["numero"]
        assert client.post(
            f"/comptabilite/dossiers/{niu}/ecritures/2026/VE/{numero}/validation", json={}
        ).status_code == 200

    def _revue(self, client, *, tout=False):
        reponse = client.get(
            "/obligations/eligibilite-des-adherents"
            f"?a_la_date=2026-11-02&a_surveiller_seulement={str(not tout).lower()}"
        )
        assert reponse.status_code == 200, reponse.text
        return reponse.json()

    def test_un_adherent_qui_depasse_apparait_avec_ses_livres(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, self.REVISEUR)
        self._vendre(client, ADHERENT, 120000000)

        revue = self._revue(client)
        (ligne,) = [r for r in revue if r["niu"] == ADHERENT]
        assert Decimal(ligne["en_cours"]["chiffre_affaires"]) == Decimal(120000000)
        assert ligne["en_cours"]["au_dela_du_seuil"] is True
        assert ligne["a_surveiller"] is True
        assert ligne["borne"] == "EXCLUSE"
        assert all(r["a_surveiller"] for r in revue), "le filtre par défaut ne filtre pas"

    def test_a_cent_millions_exactement_la_route_reste_dans_le_champ(self, plateforme):
        """⚠️ **La borne mesurée là où elle est lue : au référentiel, par la route.**

        Le cas de domaine prouve que la revue applique la borne qu'on lui donne. Ce
        cas prouve que la route lui donne celle du référentiel. Une première
        épreuve n'était tuée que par la relecture du champ `borne` rendu, pas par
        le classement lui-même.
        """
        client = plateforme
        ouvrir_une_session(client, self.REVISEUR)
        self._vendre(client, ADHERENT, 100000000)
        (ligne,) = [r for r in self._revue(client, tout=True) if r["niu"] == ADHERENT]
        assert Decimal(ligne["en_cours"]["chiffre_affaires"]) == Decimal(100000000)
        assert ligne["en_cours"]["au_dela_du_seuil"] is False

    def test_un_dossier_resilie_sort_de_la_revue(self, plateforme):
        """La boucle avec le pas 48 : une fois l'adhésion résiliée, le dossier n'est
        plus l'affaire de cette revue."""
        client = plateforme
        ouvrir_une_session(client, self.REVISEUR)
        assert ADHERENT in [r["niu"] for r in self._revue(client, tout=True)]

        resiliation = maintenant().date() - timedelta(days=1)
        assert resiliation.year == 2026, "le cas suppose une date d'observation en 2026"
        reponse = client.post(
            f"/portefeuille/entreprises/{ADHERENT}/adhesions/resiliation",
            json={"au": resiliation.isoformat(), "justification": self.JUSTIFICATION},
        )
        assert reponse.status_code == 200, reponse.text
        assert ADHERENT not in [r["niu"] for r in self._revue(client, tout=True)]

    def test_la_revue_ne_montre_que_le_portefeuille_du_collaborateur(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, self.COMPTABLE)
        siens = {r["niu"] for r in self._revue(client, tout=True)}
        ouvrir_une_session(client, self.REVISEUR)
        tous = {r["niu"] for r in self._revue(client, tout=True)}
        assert siens, "le comptable ne voit aucun adhérent : le cas ne mesure rien"
        assert siens < tous
        assert ADHERENT not in siens

    def test_l_adherent_ne_revoit_pas_les_autres_adherents(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "jp.nkoa@batimentplus.cm")
        reponse = client.get("/obligations/eligibilite-des-adherents?a_la_date=2026-11-02")
        assert reponse.status_code in (403, 404), reponse.text
