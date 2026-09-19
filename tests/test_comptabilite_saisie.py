"""Contexte E · La tenue du journal : enregistrer, valider, contre-passer.

Ce que ces tests protègent, dans l'ordre où l'erreur coûte le plus cher :

1. **L'exercice clos reste clos.** Une écriture qui s'y glisse rend fausse, après
   coup, une liasse déjà remise à l'administration, et le cabinet ne sait même pas
   que sa copie a changé.
2. **L'intangibilité.** Une écriture validée ne se modifie plus ; la seule
   correction est la contre-passation, datée du jour où l'on s'aperçoit de
   l'erreur et jamais du jour de l'écriture d'origine.
3. **Le plan comptable ne s'invente pas.** Un compte créé par faute de frappe
   produit des comptes jumeaux dont la balance ne fait plus la somme.
4. **La numérotation ne fait ni trou ni doublon.** C'est la propriété qui rend un
   journal opposable.

⚠️ Une classe `TestRoute` termine le fichier, et ce n'est pas de la redondance :
c'est le seul endroit qui traverse le câblage — permissions, lecture de
l'exercice au portefeuille, traduction des refus en codes HTTP. Le contexte J a
livré une route en HTTP 500 derrière vingt et un tests verts pour avoir cru que
tester le calcul suffisait.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contextes.comptabilite.api import (
    COMPTES_SYSCOHADA,
    JOURNAUX_CABINET,
    BrouillonEcriture,
    CompteInconnu,
    DateHorsExercice,
    DepotEcrituresMemoire,
    EtatEcriture,
    ExerciceClos,
    JournalInconnu,
    LigneEcriture,
    Sens,
    TypeEcriture,
    contrepasser_une_ecriture,
    enregistrer_une_ecriture,
    valider_une_ecriture,
)
from app.contextes.portefeuille.contrats import Exercice

AGRO = "M065544332211L"
JOUR = date(2026, 8, 17)
OUVERT = Exercice(libelle="2026", ouverture=date(2026, 1, 1), cloture=date(2026, 12, 31))
CLOS = Exercice(
    libelle="2025",
    ouverture=date(2025, 1, 1),
    cloture=date(2025, 12, 31),
    clos=True,
)


def _lignes(montant: str = "1000000") -> list[LigneEcriture]:
    """Un achat ordinaire, équilibré, sur deux comptes qui existent au plan."""
    return [
        LigneEcriture(
            compte="601", libelle="Achat de marchandises", sens=Sens.DEBIT, montant=Decimal(montant)
        ),
        LigneEcriture(
            compte="401", libelle="Fournisseur ALPHA", sens=Sens.CREDIT, montant=Decimal(montant)
        ),
    ]


def _brouillon(**remplacements) -> BrouillonEcriture:
    defauts = {
        "journal": "AC",
        "exercice": "2026",
        "date_operation": JOUR,
        "libelle": "Facture ALPHA F-2026-0912",
        "piece_justificative": "PJ-2026-0912",
        "lignes": _lignes(),
    }
    return BrouillonEcriture(**{**defauts, **remplacements})


def _enregistrer(depot, brouillon=None, exercice=OUVERT, par="C-004"):
    return enregistrer_une_ecriture(
        brouillon or _brouillon(),
        journaux=JOURNAUX_CABINET,
        plan=COMPTES_SYSCOHADA,
        exercice=exercice,
        periodes_verrouillees=(),
        depot=depot,
        par=par,
    )


@pytest.fixture
def depot() -> DepotEcrituresMemoire:
    """Un registre vide. La démonstration est chargée ailleurs : ici on compte
    les écritures, et un registre prégarni fausserait la numérotation attendue."""
    return DepotEcrituresMemoire(AGRO)


# ══ L'enregistrement ══════════════════════════════════════════════════════════
class TestEnregistrement:
    def test_une_ecriture_naît_en_brouillon_et_nomme_son_auteur(self, depot):
        """Un logiciel qui validerait à la saisie déplacerait la responsabilité
        vers l'éditeur, alors que c'est le Centre qui engage son agrément."""
        ecriture = _enregistrer(depot)
        assert ecriture.etat is EtatEcriture.BROUILLON
        assert ecriture.saisie_par == "C-004"
        assert ecriture.validee_par is None
        assert ecriture.modifiable

    def test_le_numero_vient_du_registre_et_se_suit(self, depot):
        """La continuité de la séquence est une propriété du registre entier,
        jamais d'une écriture prise isolément."""
        numeros = [_enregistrer(depot).numero for _ in range(3)]
        assert numeros == [1, 2, 3]

    def test_chaque_journal_a_sa_propre_suite(self, depot):
        """Deux journaux qui partageraient une numérotation feraient apparaître
        des trous dans chacun, alors qu'il n'en manque aucun."""
        achat = _enregistrer(depot)
        vente = _enregistrer(
            depot,
            _brouillon(
                journal="VE",
                lignes=[
                    LigneEcriture(
                        compte="411", libelle="Client BETA", sens=Sens.DEBIT, montant=Decimal(500)
                    ),
                    LigneEcriture(
                        compte="701", libelle="Vente", sens=Sens.CREDIT, montant=Decimal(500)
                    ),
                ],
            ),
        )
        assert achat.numero == 1
        assert vente.numero == 1
        assert achat.cle != vente.cle

    def test_l_ecriture_est_relisible_apres_enregistrement(self, depot):
        """Enregistrer sans pouvoir relire ne prouve rien : c'est le tour complet
        qui compte, pas l'appel."""
        ecriture = _enregistrer(depot)
        assert depot.lire(ecriture.cle).libelle == "Facture ALPHA F-2026-0912"


# ══ Les quatre contrôles d'environnement ══════════════════════════════════════
class TestControlesEnvironnement:
    def test_un_journal_inconnu_est_refuse_et_les_journaux_ouverts_sont_nommes(self, depot):
        """Un refus qui ne dit pas ce qui était possible oblige à deviner."""
        with pytest.raises(JournalInconnu, match="ZZ"):
            _enregistrer(depot, _brouillon(journal="ZZ"))

    def test_un_compte_absent_du_plan_est_refuse(self, depot):
        """Un plan comptable qui s'enrichit par faute de frappe produit des
        comptes jumeaux dont la balance ne fait plus la somme."""
        with pytest.raises(CompteInconnu, match="60199"):
            _enregistrer(
                depot,
                _brouillon(
                    lignes=[
                        LigneEcriture(
                            compte="60199", libelle="Achat", sens=Sens.DEBIT, montant=Decimal(10)
                        ),
                        LigneEcriture(
                            compte="401", libelle="Frs", sens=Sens.CREDIT, montant=Decimal(10)
                        ),
                    ]
                ),
            )

    def test_la_racine_ne_suffit_pas(self, depot):
        """LE TEST QUI TIENT LA RÈGLE. Un compte 601 ouvert n'autorise pas 6011 :
        un plan qui s'étend par tolérance devient un plan que personne n'a arrêté.
        """
        with pytest.raises(CompteInconnu):
            _enregistrer(
                depot,
                _brouillon(
                    lignes=[
                        LigneEcriture(
                            compte="6011", libelle="Achat", sens=Sens.DEBIT, montant=Decimal(10)
                        ),
                        LigneEcriture(
                            compte="401", libelle="Frs", sens=Sens.CREDIT, montant=Decimal(10)
                        ),
                    ]
                ),
            )

    def test_un_exercice_clos_refuse_toute_ecriture(self, depot):
        """Écrire dans un exercice clos rend fausse, après coup, une liasse déjà
        remise à l'administration."""
        with pytest.raises(ExerciceClos, match="clos"):
            _enregistrer(depot, _brouillon(exercice="2025"), exercice=CLOS)

    def test_un_exercice_inconnu_refuse_plutôt_que_de_laisser_passer(self, depot):
        """Des écritures rattachées à un exercice qu'aucune liasse ne ramasse
        existeraient sans apparaître nulle part : la pire forme de perte."""
        with pytest.raises(ExerciceClos, match="inconnu"):
            _enregistrer(depot, _brouillon(exercice="2099"), exercice=None)

    def test_une_date_hors_bornes_est_refusee(self, depot):
        with pytest.raises(DateHorsExercice, match="hors de l'exercice"):
            _enregistrer(depot, _brouillon(date_operation=date(2027, 1, 2)))

    @pytest.mark.parametrize("jour", [date(2026, 1, 1), date(2026, 12, 31)])
    def test_les_deux_bornes_de_l_exercice_sont_incluses(self, depot, jour):
        """⚠️ À la différence des périodes de statut du portefeuille, dont la
        borne haute est exclue : un exercice va du 1er janvier au 31 décembre."""
        assert _enregistrer(depot, _brouillon(date_operation=jour)).numero == 1


# ══ Ce que le domaine refuse, et qu'on ne réécrit pas ═════════════════════════
class TestInvariantsDuDomaine:
    def test_une_ecriture_desequilibree_est_refusee_a_l_enregistrement(self, depot):
        """L'équilibre est vérifié **une seule fois**, dans `EcritureComptable`.

        Le brouillon ne le revérifie pas, et c'est délibéré : deux copies du même
        invariant divergent le jour où l'une est corrigée et pas l'autre. Le
        refus survient donc à l'enregistrement, ce qui est le premier moment où
        l'écriture existe vraiment.
        """
        with pytest.raises(ValidationError, match="déséquilibrée"):
            _enregistrer(
                depot,
                _brouillon(
                    lignes=[
                        LigneEcriture(
                            compte="601", libelle="Achat", sens=Sens.DEBIT, montant=Decimal(100)
                        ),
                        LigneEcriture(
                            compte="401", libelle="Frs", sens=Sens.CREDIT, montant=Decimal(90)
                        ),
                    ]
                ),
            )

    def test_une_ecriture_a_une_seule_ligne_ne_se_construit_pas(self):
        with pytest.raises(ValidationError):
            BrouillonEcriture(
                journal="AC",
                exercice="2026",
                date_operation=JOUR,
                libelle="Unijambiste",
                lignes=[
                    LigneEcriture(
                        compte="601", libelle="Achat", sens=Sens.DEBIT, montant=Decimal(100)
                    )
                ],
            )

    def test_le_brouillon_ne_porte_ni_numero_ni_etat(self):
        """Ce qu'un client ne peut pas envoyer n'a pas besoin d'être contrôlé.

        Accepter `numero` ou `etat` du client permettrait d'insérer, par une
        simple requête, une écriture validée au numéro d'une autre.
        """
        champs = set(BrouillonEcriture.model_fields)
        assert champs.isdisjoint({"numero", "etat", "validee_par", "validee_le", "saisie_par"})


# ══ La validation ═════════════════════════════════════════════════════════════
class TestValidation:
    def test_valider_nomme_son_auteur_et_horodate(self, depot):
        """Sans séparation des tâches imposée, c'est la seule garantie qui reste :
        le contrôle a posteriori doit rester possible."""
        ecriture = _enregistrer(depot)
        instant = datetime(2026, 8, 17, 10, 30)
        validee = valider_une_ecriture(
            ecriture.cle,
            exercice=OUVERT,
            periodes_verrouillees=(),
            depot=depot,
            par="C-003",
            le=instant,
        )
        assert validee.etat is EtatEcriture.VALIDEE
        assert validee.validee_par == "C-003"
        assert validee.validee_le == instant
        assert not validee.modifiable

    def test_valider_deux_fois_est_refuse(self, depot):
        ecriture = _enregistrer(depot)
        valider_une_ecriture(
            ecriture.cle,
            exercice=OUVERT,
            periodes_verrouillees=(),
            depot=depot,
            par="C-003",
            le=datetime(2026, 8, 17),
        )
        with pytest.raises(ValueError, match="déjà validée"):
            valider_une_ecriture(
                ecriture.cle,
                exercice=OUVERT,
                periodes_verrouillees=(),
                depot=depot,
                par="C-003",
                le=datetime(2026, 8, 18),
            )

    def test_une_ecriture_validee_ne_se_reecrit_plus(self, depot):
        """L'intangibilité est portée par le dépôt, pas par une convention
        d'appel : c'est ce qui la rend vraie même pour un appelant distrait."""
        from app.contextes.comptabilite.api import EcritureFigee

        ecriture = _enregistrer(depot)
        valider_une_ecriture(
            ecriture.cle,
            exercice=OUVERT,
            periodes_verrouillees=(),
            depot=depot,
            par="C-003",
            le=datetime(2026, 8, 17),
        )
        with pytest.raises(EcritureFigee):
            depot.enregistrer(ecriture.model_copy(update={"libelle": "Retouche"}))


# ══ La contre-passation ═══════════════════════════════════════════════════════
class TestContrepassation:
    def _validee(self, depot):
        ecriture = _enregistrer(depot)
        return valider_une_ecriture(
            ecriture.cle,
            exercice=OUVERT,
            periodes_verrouillees=(),
            depot=depot,
            par="C-003",
            le=datetime(2026, 8, 17),
        )

    def test_l_inverse_annule_l_originale_sens_par_sens(self, depot):
        origine = self._validee(depot)
        inverse = contrepasser_une_ecriture(
            origine.cle, motif="Facture reçue en double", jour=JOUR,
                exercice=OUVERT, periodes_verrouillees=(), depot=depot, par="C-003"
        )
        assert inverse.type is TypeEcriture.CONTREPASSATION
        assert inverse.ecriture_contrepassee == origine.cle
        assert [ligne.sens for ligne in inverse.lignes] == [Sens.CREDIT, Sens.DEBIT]
        assert inverse.montant == origine.montant

    def test_elle_porte_la_date_du_jour_et_jamais_celle_de_l_origine(self, depot):
        """⚠️ LE TEST QUI TIENT L'INTANGIBILITÉ. Antidater ferait bouger une
        balance déjà éditée, et le cabinet ne pourrait plus expliquer l'écart."""
        origine = self._validee(depot)
        constat = date(2026, 11, 4)
        inverse = contrepasser_une_ecriture(
            origine.cle, motif="Erreur d'imputation", jour=constat,
                exercice=OUVERT, periodes_verrouillees=(), depot=depot, par="C-003"
        )
        assert inverse.date_operation == constat
        assert inverse.date_operation != origine.date_operation

    def test_un_brouillon_ne_se_contre_passe_pas(self, depot):
        """Il se corrige ou se supprime avant validation. Contre-passer un
        brouillon laisserait deux écritures là où il n'en fallait aucune."""
        brouillon = _enregistrer(depot)
        with pytest.raises(ValueError, match="on ne contre-passe qu'une écriture validée"):
            contrepasser_une_ecriture(
                brouillon.cle,
                motif="Erreur",
                jour=JOUR,
                exercice=OUVERT,
                periodes_verrouillees=(),
                depot=depot,
                par="C-003",
            )

    def test_un_motif_vide_est_refuse(self, depot):
        """Six mois plus tard, personne ne saurait si l'écriture d'origine était
        fausse ou si elle a été annulée par erreur."""
        origine = self._validee(depot)
        with pytest.raises(ValueError, match="motif"):
            contrepasser_une_ecriture(
                origine.cle,
                motif="   ",
                jour=JOUR,
                exercice=OUVERT,
                periodes_verrouillees=(),
                depot=depot,
                par="C-003",
            )

    def test_la_contre_passation_naît_en_brouillon(self, depot):
        """Annuler est un acte comptable ordinaire, qui se relit comme un autre."""
        origine = self._validee(depot)
        inverse = contrepasser_une_ecriture(
            origine.cle,
            motif="Doublon",
            jour=JOUR,
            exercice=OUVERT,
            periodes_verrouillees=(),
            depot=depot,
            par="C-003",
        )
        assert inverse.etat is EtatEcriture.BROUILLON

    def test_l_originale_demeure_validee(self, depot):
        """On n'efface pas : on ajoute l'inverse. C'est ce qui distingue une
        comptabilité d'un tableur."""
        origine = self._validee(depot)
        contrepasser_une_ecriture(
            origine.cle,
            motif="Doublon",
            jour=JOUR,
            exercice=OUVERT,
            periodes_verrouillees=(),
            depot=depot,
            par="C-003",
        )
        assert depot.lire(origine.cle).etat is EtatEcriture.VALIDEE


# ══ La route, seule à traverser le câblage ════════════════════════════════════
class TestRoute:
    @pytest.fixture(scope="class")
    def application(self):
        from app.contextes.transverse.adaptateurs.entrant.dependances import (
            reinitialiser_atelier,
        )
        from app.main import creer_application

        reinitialiser_atelier()
        return creer_application()

    def _connecte(self, application, courriel: str):
        from fastapi.testclient import TestClient

        from app.contextes.transverse.api import MOT_DE_PASSE_DEMO

        client = TestClient(application)
        reponse = client.post(
            "/transverse/session",
            json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        assert reponse.status_code == 200, reponse.text
        return client

    @pytest.fixture(scope="class")
    def comptable(self, application):
        """L. FOTSO, habilité sur AGRO-NKOLO SA. Il détient les trois permissions
        d'écriture : saisir, valider, contre-passer."""
        return self._connecte(application, "l.fotso@cga-brcg.cm")

    def _corps(self, **remplacements) -> dict:
        defauts = {
            "journal": "AC",
            "exercice": "2026",
            "date_operation": "2026-08-17",
            "libelle": "Facture de recette",
            "piece_justificative": "PJ-RECETTE",
            "lignes": [
                {
                    "compte": "601",
                    "libelle": "Achat de marchandises",
                    "sens": "DEBIT",
                    "montant": "250000",
                },
                {
                    "compte": "401",
                    "libelle": "Fournisseur",
                    "sens": "CREDIT",
                    "montant": "250000",
                },
            ],
        }
        return {**defauts, **remplacements}

    def test_le_tour_complet_saisir_valider(self, comptable):
        """LE TEST QUI MANQUAIT AU CONTEXTE J. Il ne juge aucun chiffre : il exige
        que la route aille au bout, permissions et portefeuille compris."""
        creation = comptable.post(
            f"/comptabilite/dossiers/{AGRO}/ecritures", json=self._corps()
        )
        assert creation.status_code == 201, creation.text
        ecriture = creation.json()
        assert ecriture["etat"] == "BROUILLON"
        assert ecriture["numero"] > 0

        cle = f"{ecriture['exercice']}/{ecriture['journal']}/{ecriture['numero']}"
        validation = comptable.post(f"/comptabilite/dossiers/{AGRO}/ecritures/{cle}/validation")
        assert validation.status_code == 200, validation.text
        assert validation.json()["etat"] == "VALIDEE"
        assert validation.json()["validee_par"]

    def test_un_exercice_inconnu_repond_409_et_non_500(self, comptable):
        """La requête est bien formée : ce sont les faits qui s'y opposent. Un 422
        laisserait chercher la faute dans le mauvais champ, un 500 accuserait le
        logiciel."""
        refus = comptable.post(
            f"/comptabilite/dossiers/{AGRO}/ecritures", json=self._corps(exercice="2099")
        )
        assert refus.status_code == 409, refus.text
        assert "2099" in refus.json()["detail"]

    def test_un_compte_hors_plan_repond_409(self, comptable):
        refus = comptable.post(
            f"/comptabilite/dossiers/{AGRO}/ecritures",
            json=self._corps(
                lignes=[
                    {"compte": "99999", "libelle": "X", "sens": "DEBIT", "montant": "10"},
                    {"compte": "401", "libelle": "Y", "sens": "CREDIT", "montant": "10"},
                ]
            ),
        )
        assert refus.status_code == 409
        assert "99999" in refus.json()["detail"]

    def test_une_ecriture_desequilibree_repond_409_dans_une_phrase_lisible(self, comptable):
        """⚠️ Le message va à l'écran d'un comptable, pas dans un journal de
        serveur. Le `str` brut d'une `ValidationError` y déposerait
        « 1 validation error for EcritureComptable … [type=value_error,
        input_value={…}] » et une adresse web : un message qu'il faut déchiffrer
        est un message qu'on cesse de lire.
        """
        refus = comptable.post(
            f"/comptabilite/dossiers/{AGRO}/ecritures",
            json=self._corps(
                lignes=[
                    {"compte": "601", "libelle": "X", "sens": "DEBIT", "montant": "100"},
                    {"compte": "401", "libelle": "Y", "sens": "CREDIT", "montant": "90"},
                ]
            ),
        )
        assert refus.status_code == 409
        detail = refus.json()["detail"]
        assert "déséquilibrée" in detail
        assert "validation error" not in detail
        assert "pydantic" not in detail
        assert "input_value" not in detail

    def test_l_adherent_ne_saisit_pas_dans_son_propre_journal(self, application):
        """⚠️ LE REFUS QUI PROTÈGE L'AGRÉMENT. Un centre de gestion agréé engage
        son agrément sur les comptes qu'il produit : laisser l'adhérent écrire
        ferait du cabinet le témoin de ses écritures plutôt que leur auteur.
        """
        batiment = "M081234567890P"
        adherent = self._connecte(application, "jp.nkoa@batimentplus.cm")
        refus = adherent.post(
            f"/comptabilite/dossiers/{batiment}/ecritures", json=self._corps()
        )
        assert refus.status_code == 403
        assert "SAISIR_ECRITURE" in str(refus.json()["detail"])

    def test_un_comptable_ne_saisit_pas_hors_de_son_portefeuille(self, application):
        """Le cloisonnement tient aussi à l'écriture, et pas seulement à la
        lecture : c'est là qu'il coûterait le plus cher."""
        voisin = self._connecte(application, "c.ndongo@cga-brcg.cm")
        refus = voisin.post(f"/comptabilite/dossiers/{AGRO}/ecritures", json=self._corps())
        assert refus.status_code in (403, 404)

    def test_valider_une_ecriture_absente_repond_404(self, comptable):
        absente = comptable.post(
            f"/comptabilite/dossiers/{AGRO}/ecritures/2026/AC/999999/validation"
        )
        assert absente.status_code == 404

    def test_la_contre_passation_exige_un_motif(self, comptable):
        creation = comptable.post(
            f"/comptabilite/dossiers/{AGRO}/ecritures", json=self._corps()
        )
        ecriture = creation.json()
        cle = f"{ecriture['exercice']}/{ecriture['journal']}/{ecriture['numero']}"
        comptable.post(f"/comptabilite/dossiers/{AGRO}/ecritures/{cle}/validation")

        # ⚠️ 403 et non 409 : le motif vide est refusé par le **contrôle
        # d'accès**, avant que le domaine ne voie l'acte. `CONTRE_PASSER` figure
        # dans `EXIGE_MOTIF` du socle, et ce refus-là se journalise. Le domaine
        # refuserait lui aussi, mais il n'est jamais atteint : les deux gardes
        # sont posées, et la première suffit.
        sans_motif = comptable.post(
            f"/comptabilite/dossiers/{AGRO}/ecritures/{cle}/contre-passation",
            json={"motif": "  "},
        )
        assert sans_motif.status_code == 403
        assert "CONTRE_PASSER" in str(sans_motif.json()["detail"])

        avec_motif = comptable.post(
            f"/comptabilite/dossiers/{AGRO}/ecritures/{cle}/contre-passation",
            json={"motif": "Facture reçue en double"},
        )
        assert avec_motif.status_code == 201, avec_motif.text
        assert avec_motif.json()["type"] == "CONTREPASSATION"
