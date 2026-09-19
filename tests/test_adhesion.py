"""Admettre un dossier au Centre, et résilier son adhésion.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

L'adhésion est l'objet même d'un centre de gestion agréé, et le portefeuille ne
savait pas l'écrire. Depuis le pas 46, l'abattement CGA dépend de l'adhésion réelle :
une adhésion doit donc pouvoir s'inscrire dans le produit, sous les règles que
l'entité énonce depuis le premier jour.

⚠️ La date d'effet est fiscalement porteuse. D'où l'asymétrie que ces cas gardent :
on n'adhère jamais avant le jour de l'inscription, on peut résilier à une date
passée tant qu'aucun exercice clos n'est traversé.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.contextes.portefeuille.api import (
    PORTEFEUILLE_DEMO,
    Adhesion,
    AdhesionRefusee,
    DepotEntreprisesMemoire,
    MotifChangement,
    inscrire_une_adhesion,
    prochain_numero_d_adhesion,
    resilier_l_adhesion,
)
from app.contextes.referentiel.contrats import Borne
from app.partage.copie import transiter
from app.partage.horloge import maintenant
from tests.conftest import exige_postgresql, ouvrir_une_session

#: Adhérent de 2018 à fin 2020, puis depuis le 1er juillet 2022 ; exercices clos
#: jusqu'en 2025.
DOSSIER = "M093344556677N"
SEUIL = Decimal(100000000)
LE_JOUR = date(2026, 9, 13)


def _hors_centre():
    """Le dossier, sans adhésion en cours : la seconde est résiliée fin 2025."""
    modele = PORTEFEUILLE_DEMO[DOSSIER]
    premiere, seconde = sorted(modele.adhesions, key=lambda a: a.debut)
    return transiter(modele, adhesions=[premiere, transiter(seconde, fin=date(2026, 1, 1))])


def _admettre(entreprise=None, a_compter_du=LE_JOUR, **surcharges):
    arguments = {
        "numero": "ADH-2026-001",
        "inscrite_le": LE_JOUR,
        "chiffre_affaires_declare": Decimal(62000000),
        "source_du_chiffre": "Liasse 2025, visée par la DGI",
        "seuil_adhesion": SEUIL,
        "borne_adhesion": Borne.EXCLUSE,
        "numeros_existants": [],
    }
    return inscrire_une_adhesion(
        entreprise or _hors_centre(), a_compter_du, **{**arguments, **surcharges}
    )


class TestAdmettre:
    def test_l_adhesion_s_ajoute_avec_sa_declaration_d_eligibilite(self):
        avant = _hors_centre()
        apres = _admettre(avant)
        assert len(apres.adhesions) == len(avant.adhesions) + 1
        nouvelle = max(apres.adhesions, key=lambda a: a.debut)
        assert nouvelle.debut == LE_JOUR
        assert nouvelle.fin is None
        assert nouvelle.motif is MotifChangement.ADHESION
        assert nouvelle.numero == "ADH-2026-001"
        # ⚠️ La source garde ses virgules : seul le montant est mis en forme.
        assert "62 000 000 FCFA" in nouvelle.precision
        assert "Liasse 2025, visée par la DGI" in nouvelle.precision
        assert apres.est_adherente_au(LE_JOUR)
        assert not apres.est_adherente_au(LE_JOUR - timedelta(days=1))

    def test_une_date_d_effet_future_est_admise(self):
        apres = _admettre(a_compter_du=date(2027, 1, 1))
        assert apres.est_adherente_au(date(2027, 1, 1))


class TestLesRefusDAdmission:
    def test_on_n_adhere_pas_avant_le_jour_de_l_inscription(self):
        """⚠️ **Le sens qui accorde un avantage ne remonte pas le temps.**

        Antidater d'un seul jour suffit : si ce jour tombe dans l'exercice, il peut
        faire basculer la couverture de l'exercice entier.
        """
        with pytest.raises(AdhesionRefusee, match="avant le jour de son inscription"):
            _admettre(a_compter_du=LE_JOUR - timedelta(days=1))

    def test_la_contre_epreuve_le_jour_meme(self):
        assert _admettre(a_compter_du=LE_JOUR).est_adherente_au(LE_JOUR)

    def test_au_dela_du_seuil_de_l_article_118(self):
        with pytest.raises(AdhesionRefusee, match="CGI art. 118"):
            _admettre(chiffre_affaires_declare=SEUIL + 1)

    def test_le_seuil_lui_meme_reste_admissible(self):
        """« N'excède pas » : 100 000 000 exactement relève encore du Centre."""
        assert _admettre(chiffre_affaires_declare=SEUIL).est_adherente_au(LE_JOUR)

    def test_un_seuil_inconnu_refuse_l_admission(self):
        with pytest.raises(AdhesionRefusee, match="ne peut pas être vérifiée"):
            _admettre(seuil_adhesion=None)

    def test_un_chiffre_sans_source_est_refuse(self):
        with pytest.raises(AdhesionRefusee, match="nommer sa source"):
            _admettre(source_du_chiffre="   ")

    def test_une_entreprise_ne_peut_pas_adherer_avant_d_exister(self):
        """⚠️ **La règle est celle de l'entité, et elle ressort en refus nommé.**

        Le cas n'est pas théorique : une entreprise convertie depuis un dossier de
        création peut porter une date d'immatriculation à venir. L'entité refuse
        l'adhésion antérieure ; le domaine rend sa phrase au lieu d'une erreur
        interne, sans rejouer le contrôle.
        """
        from app.contextes.portefeuille.api import (
            CentreRattachement,
            Entreprise,
            FormeJuridique,
            RegimeFiscal,
            StatutRattachement,
            statut_initial,
        )

        naissance = date(2027, 3, 1)
        a_naitre = Entreprise(
            niu="M099999999999Z",
            denomination="Entreprise à naître",
            forme_juridique=next(iter(FormeJuridique)),
            date_creation=naissance,
            regimes=[statut_initial(RegimeFiscal.IGS, naissance)],
            rattachements=[
                StatutRattachement(
                    debut=naissance,
                    centre=next(iter(CentreRattachement)),
                    motif=MotifChangement.CREATION,
                )
            ],
        )
        with pytest.raises(AdhesionRefusee, match="avant la création"):
            _admettre(a_naitre, a_compter_du=date(2027, 1, 1))
        # La contre-épreuve : le jour de la création, elle adhère.
        assert _admettre(a_naitre, a_compter_du=naissance).est_adherente_au(naissance)

    def test_un_dossier_deja_adherent_ne_readhere_pas(self):
        with pytest.raises(AdhesionRefusee, match="déjà adhérent"):
            _admettre(PORTEFEUILLE_DEMO[DOSSIER])

    def test_une_adhesion_future_deja_inscrite_n_est_pas_chevauchee(self):
        future = _admettre(a_compter_du=date(2027, 1, 1))
        with pytest.raises(AdhesionRefusee, match="commence déjà"):
            _admettre(future, a_compter_du=LE_JOUR, numero="ADH-2026-002")

    def test_un_numero_deja_attribue_est_refuse(self):
        with pytest.raises(AdhesionRefusee, match="déjà attribué"):
            _admettre(numeros_existants=["ADH-2026-001"])


class TestLaNumerotation:
    def test_le_numero_suit_la_sequence_de_l_annee_sur_tout_le_portefeuille(self):
        """Le portefeuille de démonstration porte ADH-2022-014 et ADH-2022-019 sur
        deux dossiers différents : la séquence est celle du registre, pas du dossier."""
        dossiers = list(PORTEFEUILLE_DEMO.values())
        assert prochain_numero_d_adhesion(dossiers, 2022) == "ADH-2022-020"
        assert prochain_numero_d_adhesion(dossiers, 2026) == "ADH-2026-001"

    def test_un_numero_d_une_autre_forme_n_entre_pas_dans_la_sequence(self):
        dossier = transiter(
            _hors_centre(),
            adhesions=[
                Adhesion(debut=date(2018, 1, 1), fin=date(2019, 1, 1),
                         motif=MotifChangement.ADHESION, numero="ANCIEN-2026-999"),
            ],
        )
        assert prochain_numero_d_adhesion([dossier], 2026) == "ADH-2026-001"


class TestResilier:
    def test_resilier_ferme_l_adhesion_en_cours_et_rien_d_autre(self):
        avant = PORTEFEUILLE_DEMO[DOSSIER]
        apres = resilier_l_adhesion(avant, date(2026, 10, 1))
        ouverte_avant = next(a for a in avant.adhesions if a.fin is None)
        fermee = next(a for a in apres.adhesions if a.debut == ouverte_avant.debut)
        assert fermee.fin == date(2026, 10, 1)
        assert transiter(fermee, fin=None) == ouverte_avant
        # `[debut, fin[` : le 30 septembre est encore couvert.
        assert apres.est_adherente_au(date(2026, 9, 30))
        assert not apres.est_adherente_au(date(2026, 10, 1))

    def test_une_date_passee_est_admise_hors_exercice_clos(self):
        """⚠️ L'asymétrie : le sens qui retire un avantage peut remonter le temps."""
        apres = resilier_l_adhesion(PORTEFEUILLE_DEMO[DOSSIER], date(2026, 2, 1))
        assert not apres.est_adherente_au(date(2026, 2, 1))

    def test_une_resiliation_qui_traverse_un_exercice_clos_est_refusee(self):
        with pytest.raises(AdhesionRefusee, match="exercice clos 2025"):
            resilier_l_adhesion(PORTEFEUILLE_DEMO[DOSSIER], date(2025, 12, 31))

    def test_la_contre_epreuve_au_lendemain_de_la_cloture(self):
        apres = resilier_l_adhesion(PORTEFEUILLE_DEMO[DOSSIER], date(2026, 1, 1))
        assert not apres.est_adherente_au(date(2026, 1, 1))

    def test_rien_a_resilier(self):
        with pytest.raises(AdhesionRefusee, match="aucune adhésion en cours"):
            resilier_l_adhesion(_hors_centre(), date(2026, 10, 1))

    def test_une_resiliation_le_jour_de_la_prise_d_effet_est_refusee(self):
        with pytest.raises(AdhesionRefusee, match="n'est pas postérieure"):
            resilier_l_adhesion(PORTEFEUILLE_DEMO[DOSSIER], date(2022, 7, 1))

    def test_l_histoire_reste_un_prolongement(self):
        """Le garde-fou du pas 45 accepte la fermeture, et elle seule."""
        depot = DepotEntreprisesMemoire.avec_demonstration()
        depot.enregistrer(resilier_l_adhesion(depot.lire(DOSSIER), date(2026, 10, 1)))
        assert not depot.lire(DOSSIER).est_adherente_au(date(2026, 10, 1))


class TestLaLigneDuPortefeuille:
    """Les deux dates qui décident du geste proposé à l'écran (pas 57)."""

    def _ligne(self, *adhesions, a_la_date=LE_JOUR):
        from app.contextes.portefeuille.adaptateurs.entrant.routes_http import _resoudre

        modele = PORTEFEUILLE_DEMO[DOSSIER]
        return _resoudre(transiter(modele, adhesions=list(adhesions)), a_la_date)

    def _adhesion(self, debut, fin=None, numero="ADH-X"):
        return Adhesion(debut=debut, fin=fin, motif=MotifChangement.ADHESION, numero=numero)

    def test_une_adhesion_qui_commence_ce_jour_est_en_cours_et_non_a_venir(self):
        """`[debut, fin[` : le jour de la prise d'effet est couvert."""
        ligne = self._ligne(self._adhesion(LE_JOUR))
        assert ligne.adherente is True
        assert ligne.adhesion_a_venir is None

    def test_c_est_la_plus_proche_des_adhesions_a_venir_qui_est_dite(self):
        ligne = self._ligne(
            self._adhesion(date(2018, 1, 1), date(2021, 1, 1), "ADH-A"),
            self._adhesion(LE_JOUR + timedelta(days=10), LE_JOUR + timedelta(days=20), "ADH-B"),
            self._adhesion(LE_JOUR + timedelta(days=30), None, "ADH-C"),
        )
        assert ligne.adherente is False
        assert ligne.adhesion_a_venir == LE_JOUR + timedelta(days=10)


class TestParLaRoute:
    """⚠️ Les dates sont calculées depuis le jour réel : la règle d'antidate se
    mesure contre l'horloge que la route lit, pas contre une date figée du cas."""

    pytestmark = exige_postgresql

    REVISEUR = "a.bouba@cga-brcg.cm"
    #: ⚠️ Un comptable **actif** et le dossier **qu'il tient**. Le seul titulaire du
    #: dossier intermittent est le compte suspendu du jeu de démonstration : l'employer
    #: aurait mesuré le refus de session, pas le refus de permission.
    COMPTABLE = "l.fotso@cga-brcg.cm"
    DOSSIER_DU_COMPTABLE = "M081234567890P"
    JUSTIFICATION = "Lettre de résiliation reçue du gérant, datée et signée ce mois-ci."

    def _aujourd_hui(self):
        return maintenant().date()

    def _resilier(self, client, au):
        return client.post(
            f"/portefeuille/entreprises/{DOSSIER}/adhesions/resiliation",
            json={"au": au.isoformat(), "justification": self.JUSTIFICATION},
        )

    def _admettre(self, client, a_compter_du, **surcharges):
        corps = {
            "a_compter_du": a_compter_du.isoformat(),
            "chiffre_affaires_declare": "62000000",
            "source_du_chiffre": "Liasse de l'exercice précédent",
            "justification": "Demande d'adhésion signée, pièces d'éligibilité reçues.",
        }
        return client.post(
            f"/portefeuille/entreprises/{DOSSIER}/adhesions", json={**corps, **surcharges}
        )

    def _adhesions(self, client):
        return client.get(f"/portefeuille/entreprises/{DOSSIER}").json()["adhesions"]

    def test_resilier_puis_readmettre_numerote_par_le_registre(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, self.REVISEUR)
        jour = self._aujourd_hui() + timedelta(days=10)

        resiliee = self._resilier(client, jour)
        assert resiliee.status_code == 200, resiliee.text
        assert resiliee.json()["adherente"] is False

        admise = self._admettre(client, jour)
        assert admise.status_code == 201, admise.text
        assert admise.json()["adherente"] is True

        derniere = max(self._adhesions(client), key=lambda a: a["debut"])
        assert derniere["numero"] == f"ADH-{jour.year}-001"
        assert "62 000 000 FCFA" in derniere["precision"]

    def test_la_liste_dit_la_fin_inscrite_et_l_adhesion_a_venir(self, plateforme):
        """⚠️ **Ce que l'écran doit savoir pour ne proposer que les gestes admis.** (pas 57)

        Une adhésion résiliée pour dans dix jours est encore « en cours » aujourd'hui ;
        une adhésion admise pour dans dix jours n'est pas encore « en cours ». Sans ces
        deux dates, l'écran proposait de résilier la première une seconde fois, et
        d'admettre la seconde à nouveau.
        """
        client = plateforme
        ouvrir_une_session(client, self.REVISEUR)
        aujourd_hui = self._aujourd_hui()
        jour = aujourd_hui + timedelta(days=10)

        def ligne():
            reponse = client.get(f"/portefeuille/entreprises?a_la_date={aujourd_hui}")
            assert reponse.status_code == 200, reponse.text
            (trouvee,) = [d for d in reponse.json() if d["niu"] == DOSSIER]
            return trouvee

        avant = ligne()
        assert avant["adherente"] is True
        assert avant["adhesion_jusqu_au"] is None, "la contre-épreuve : rien n'est résilié"
        assert avant["adhesion_a_venir"] is None

        assert self._resilier(client, jour).status_code == 200
        resiliee = ligne()
        assert resiliee["adherente"] is True, "résiliée pour dans dix jours, encore en cours"
        assert resiliee["adhesion_jusqu_au"] == jour.isoformat()

        assert self._admettre(client, jour).status_code == 201
        assert ligne()["adhesion_a_venir"] == jour.isoformat()

    def test_une_admission_antidatee_repond_409(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, self.REVISEUR)
        hier = self._aujourd_hui() - timedelta(days=1)
        assert self._resilier(client, self._aujourd_hui() - timedelta(days=5)).status_code == 200
        reponse = self._admettre(client, hier)
        assert reponse.status_code == 409, reponse.text
        assert "avant le jour de son inscription" in reponse.text

    def test_au_dela_du_seuil_repond_409_et_n_ecrit_rien(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, self.REVISEUR)
        jour = self._aujourd_hui() + timedelta(days=10)
        assert self._resilier(client, jour).status_code == 200
        avant = len(self._adhesions(client))
        reponse = self._admettre(client, jour, chiffre_affaires_declare="150000000")
        assert reponse.status_code == 409, reponse.text
        assert "CGI art. 118" in reponse.text
        assert len(self._adhesions(client)) == avant

    def test_le_comptable_du_dossier_ne_resilie_pas(self, plateforme):
        """Il tient le dossier et y saisit ; retirer à l'entreprise les avantages de
        l'adhésion engage le Centre au-delà de la saisie."""
        client = plateforme
        ouvrir_une_session(client, self.COMPTABLE)
        reponse = client.post(
            f"/portefeuille/entreprises/{self.DOSSIER_DU_COMPTABLE}/adhesions/resiliation",
            json={
                "au": (self._aujourd_hui() + timedelta(days=10)).isoformat(),
                "justification": self.JUSTIFICATION,
            },
        )
        assert reponse.status_code == 403, reponse.text
        assert "INSCRIRE_STATUT" in reponse.text

    @pytest.mark.parametrize(
        "surcharge",
        [
            # ⚠️ Le numéro s'attribue, il ne se reçoit pas.
            {"numero": "ADH-2026-999"},
            {"justification": "RAS"},
            {"chiffre_affaires_declare": "-1"},
        ],
    )
    def test_la_demande_ne_porte_que_ce_qui_se_decide(self, plateforme, surcharge):
        client = plateforme
        ouvrir_une_session(client, self.REVISEUR)
        reponse = self._admettre(client, self._aujourd_hui(), **surcharge)
        assert reponse.status_code == 422, reponse.text
