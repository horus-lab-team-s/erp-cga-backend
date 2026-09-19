"""Voir venir le franchissement de seuil, plutôt que le constater.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Le domaine du portefeuille portait `diagnostiquer_seuil` avec une docstring qui
dit exactement ce qu'il faut en faire, et **personne ne l'appelait**. La fonction
attend un chiffre d'affaires en argument, et aucun code n'en calculait un.

Le scénario que ces cas protègent est écrit dans cette docstring : une entreprise
passe au réel en septembre, son comptable continue de facturer sans TVA jusqu'en
décembre, et au contrôle l'administration extrait la taxe du prix perçu.
**L'entreprise doit alors une TVA qu'elle n'a jamais encaissée**, majorée des
pénalités.

⚠️ Deux mesures, deux sens, et les confondre coûte cher :

    l'exercice clos      un FAIT      le reclassement est dû
    l'exercice en cours  une VEILLE   le chiffre est partiel
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app.contextes.comptabilite.api import (
    EcritureComptable,
    EtatEcriture,
    LigneEcriture,
    Sens,
    balance,
    chiffre_affaires,
)
from app.contextes.obligations.application.surveillance_des_seuils import (
    surveiller_le_portefeuille,
    surveiller_un_dossier,
)
from app.contextes.portefeuille.api import (
    PORTEFEUILLE_DEMO,
    Exercice,
    MotifChangement,
    RegimeFiscal,
    StatutRegime,
)
from app.contextes.referentiel.contrats import Borne
from app.partage.copie import transiter
from tests.conftest import exige_postgresql, ouvrir_une_session

SEUIL = Decimal(50000000)

#: ⚠️ Un dossier **encore au régime de l'IGS** en 2026, et c'est ce qui compte :
#: le reclassement n'est requis que si le régime n'a pas déjà suivi. Le premier
#: dossier de démonstration est passé au réel en 2023, et tous les cas y auraient
#: montré « rien à signaler » en donnant l'impression de mesurer quelque chose.
DOSSIER = "P019876543210K"
#: Un dossier déjà au réel, pour la contre-épreuve.
DOSSIER_AU_REEL = "M081234567890P"


def _vente(montant, *, compte="701", numero=1, exercice="2026", sens=Sens.CREDIT):
    """Une vente équilibrée par la banque, validée."""
    autre = Sens.DEBIT if sens is Sens.CREDIT else Sens.CREDIT
    return EcritureComptable(
        journal="VE",
        exercice=exercice,
        numero=numero,
        date_operation=date(int(exercice), 6, 30),
        libelle="Vente",
        piece_justificative=f"PJ-{numero:04d}",
        lignes=[
            LigneEcriture(compte=compte, libelle="Vente", sens=sens, montant=Decimal(montant)),
            LigneEcriture(compte="521", libelle="Banque", sens=autre, montant=Decimal(montant)),
        ],
        etat=EtatEcriture.VALIDEE,
        validee_par="a.bouba",
        validee_le=datetime(int(exercice), 7, 1, 9, 0),
    )


class TestLeChiffreDAffairesNEstPasLaClasse7:
    """⚠️ **La distinction qui évite un reclassement indu.**

    La classe 7 porte tous les produits, y compris les intérêts de prêts reçus et
    les transferts de charges. Les compter gonflerait le chiffre d'affaires d'une
    entreprise qui place sa trésorerie ou refacture des frais, et déclencherait un
    passage au réel qu'elle ne doit pas : numéro de TVA, facturation avec taxe,
    déclarations dont elle n'était pas redevable.
    """

    def test_seuls_les_comptes_de_vente_comptent(self):
        livres = [
            _vente(30000000, compte="701", numero=1),
            _vente(5000000, compte="706", numero=2),
            # ⚠️ Ceux-ci sont en classe 7 et ne sont **pas** du chiffre d'affaires.
            _vente(9000000, compte="771", numero=3),
            _vente(4000000, compte="781", numero=4),
        ]
        assert chiffre_affaires(balance(livres)) == Decimal(35000000)

    def test_un_avoir_diminue_le_chiffre_d_affaires(self):
        """⚠️ Prendre le seul total au crédit donnerait le chiffre brut d'une
        entreprise qui annule la moitié de ses ventes."""
        livres = [
            _vente(40000000, numero=1),
            _vente(8000000, numero=2, sens=Sens.DEBIT),
        ]
        assert chiffre_affaires(balance(livres)) == Decimal(32000000)

    def test_un_brouillon_ne_compte_pas(self):
        """La balance ne retient que le validé, et le chiffre en hérite.

        Un devis saisi et non validé ne doit pas faire franchir un seuil.
        """
        brouillon = transiter(
            _vente(60000000, numero=9),
            etat=EtatEcriture.BROUILLON,
            validee_par=None,
            validee_le=None,
        )
        assert chiffre_affaires(balance([brouillon])) == 0


def _dossier(*, exercices, niu=None, regimes=None):
    """Un dossier de démonstration dont on refait l'histoire d'exercices.

    ⚠️ Bâti sur un dossier réel plutôt qu'inventé : une entreprise forgée
    passerait à côté des invariants que l'entité impose — continuité des régimes,
    contiguïté des exercices — et le cas mesurerait un objet impossible.
    """
    modele = PORTEFEUILLE_DEMO[niu or DOSSIER]
    champs = {"exercices": exercices}
    if regimes is not None:
        champs["regimes"] = regimes
    return transiter(modele, **champs)


def _exercices(*specs):
    return [
        Exercice(
            libelle=str(annee),
            ouverture=date(annee, 1, 1),
            cloture=date(annee, 12, 31),
            clos=clos,
        )
        for annee, clos in specs
    ]


class TestLesDeuxMesures:
    """⚠️ **Un fait et une veille**, et les confondre coûte cher."""

    def test_l_exercice_clos_rend_un_fait_le_reclassement_est_du(self):
        dossier = _dossier(exercices=_exercices((2025, True), (2026, False)))
        livres = {"2025": balance([_vente(52000000, exercice="2025")]), "2026": []}

        vue = surveiller_un_dossier(
            dossier,
            soldes_de=lambda exercice: livres[exercice],
            seuil=SEUIL,
            borne=Borne.EXCLUSE,
            a_la_date=date(2026, 3, 15),
        )
        assert vue.acquis is not None
        assert vue.acquis.exercice == "2025"
        assert vue.acquis.clos is True
        # ⚠️ L'exercice est derrière nous : la part écoulée vaut 1, et le
        # diagnostic se lit comme un fait.
        assert vue.acquis.part_ecoulee == 1
        assert vue.acquis.diagnostic.franchi is True
        assert vue.reclassement_du is True
        assert vue.a_surveiller is True

    def test_l_exercice_en_cours_rend_une_veille_avec_sa_part_ecoulee(self):
        """⚠️ **Le module n'extrapole pas.**

        60 % du seuil à mi-exercice « annonce » 120 % en fin d'année, et le dire
        serait inventer un nombre : une école réalise son chiffre en septembre, un
        négociant de matériaux en saison sèche. Le rapport porte le chiffre réel
        **et** la part écoulée, pour que celui qui lit juge lui-même.
        """
        dossier = _dossier(exercices=_exercices((2025, True), (2026, False)))
        livres = {"2025": [], "2026": balance([_vente(30000000, exercice="2026")])}

        vue = surveiller_un_dossier(
            dossier,
            soldes_de=lambda exercice: livres[exercice],
            seuil=SEUIL,
            borne=Borne.EXCLUSE,
            a_la_date=date(2026, 7, 1),
        )
        assert vue.en_cours is not None
        assert vue.en_cours.clos is False
        assert vue.en_cours.diagnostic.chiffre_affaires == Decimal(30000000)
        assert vue.en_cours.diagnostic.franchi is False
        # Du 1er janvier au 1er juillet : 182 jours sur 365.
        assert Decimal("0.49") < vue.en_cours.part_ecoulee < Decimal("0.51"), (
            vue.en_cours.part_ecoulee
        )
        # ⚠️ Et surtout : **aucune projection n'est rendue.** Le taux d'approche
        # est celui du chiffre réel, 60 %, et non les 120 % qu'une extrapolation
        # linéaire annoncerait.
        assert vue.en_cours.diagnostic.taux_d_approche == Decimal("0.6000")

    def test_un_exercice_termine_mais_non_clos_n_est_pas_un_fait(self):
        """⚠️ **Clos, et non pas simplement terminé.**

        Un exercice dont la date de clôture est passée mais que le cabinet n'a pas
        arrêté porte encore des écritures à venir. Le présenter comme un fait ferait
        réclamer un reclassement sur un chiffre qui va bouger.
        """
        dossier = _dossier(exercices=_exercices((2025, False), (2026, False)))
        livres = {"2025": balance([_vente(52000000, exercice="2025")]), "2026": []}

        vue = surveiller_un_dossier(
            dossier,
            soldes_de=lambda exercice: livres[exercice],
            seuil=SEUIL,
            borne=Borne.EXCLUSE,
            a_la_date=date(2026, 3, 15),
        )
        assert vue.acquis is None
        assert vue.reclassement_du is False

    def test_la_part_ecoulee_est_bornee_des_deux_cotes(self):
        """Sans bornes, la mesure déborderait et le modèle la refuserait.

        ⚠️ Un exercice clos depuis longtemps rendrait une part supérieure à 1, et
        la surveillance échouerait sur les dossiers les plus anciens, c'est-à-dire
        précisément ceux qu'un cabinet suit depuis le plus longtemps.
        """
        dossier = _dossier(
            exercices=_exercices((2023, True), (2024, True), (2025, True), (2026, False))
        )
        vue = surveiller_un_dossier(
            dossier,
            soldes_de=lambda _: [],
            seuil=SEUIL,
            borne=Borne.EXCLUSE,
            a_la_date=date(2026, 12, 31),
        )
        assert vue.acquis is not None
        assert vue.acquis.exercice == "2025", (
            "le dernier exercice clos n'est pas le plus récent des clos"
        )
        assert vue.acquis.part_ecoulee == 1

        # ⚠️ Et l'autre borne : un exercice observé **avant** son ouverture.
        vue_anticipee = surveiller_un_dossier(
            dossier,
            soldes_de=lambda _: [],
            seuil=SEUIL,
            borne=Borne.EXCLUSE,
            a_la_date=date(2026, 1, 1),
        )
        assert vue_anticipee.en_cours is not None
        assert 0 < vue_anticipee.en_cours.part_ecoulee < Decimal("0.01")

    def test_un_dossier_deja_au_reel_n_a_plus_rien_a_signaler(self):
        """⚠️ La contre-épreuve, et elle porte le sens de la surveillance.

        Franchir le seuil n'est un problème que tant que le régime n'a pas suivi.
        Une fois le reclassement inscrit, le dossier doit sortir de la liste :
        sinon le collaborateur apprend à ignorer une alerte permanente, et il
        ignorera aussi la vraie.
        """
        # ⚠️ Le dossier de démonstration **réellement** passé au réel, plutôt
        # qu'une histoire de régimes refabriquée : l'entité refuse un régime
        # antérieur à la création, et la refabriquer aurait mesuré un objet que
        # le domaine n'accepte pas.
        dossier = _dossier(
            niu=DOSSIER_AU_REEL, exercices=_exercices((2025, True), (2026, False))
        )
        assert dossier.regime_au(date(2026, 3, 15)) is RegimeFiscal.REEL
        livres = {"2025": balance([_vente(52000000, exercice="2025")]), "2026": []}

        vue = surveiller_un_dossier(
            dossier,
            soldes_de=lambda exercice: livres[exercice],
            seuil=SEUIL,
            borne=Borne.EXCLUSE,
            a_la_date=date(2026, 3, 15),
        )
        assert vue.acquis is not None
        assert vue.acquis.diagnostic.franchi is True
        assert vue.reclassement_du is False, (
            "le régime a suivi, et le dossier reste pourtant signalé : le "
            "collaborateur apprendra à ignorer cette alerte."
        )
        assert vue.a_surveiller is False


class TestLaDateDeLectureDuRegime:
    """⚠️ **Le régime est lu à la date d'observation, pas à celle de l'exercice.**

    L'écart ne se voit que dans une fenêtre précise : un exercice clos franchi, et
    un reclassement inscrit **après** cette clôture. C'est exactement la situation
    d'un cabinet qui fait son travail — il a vu le franchissement et l'a
    régularisé au 1er janvier.

    Lire le régime à la clôture de l'exercice mesuré maintiendrait l'alerte pour
    toujours sur un dossier régularisé. Le collaborateur apprendrait à l'ignorer,
    et il ignorerait aussi la vraie.

    Aucun dossier de démonstration ne change de régime dans cette fenêtre : le
    cas le fabrique, et c'est la seule façon de mesurer la règle.
    """

    def _regularise_au_premier_janvier(self):
        modele = PORTEFEUILLE_DEMO[DOSSIER]
        return transiter(
            modele,
            exercices=_exercices((2025, True), (2026, False)),
            regimes=[
                StatutRegime(
                    debut=modele.date_creation,
                    fin=date(2026, 1, 1),
                    regime=RegimeFiscal.IGS,
                    motif=MotifChangement.CREATION,
                ),
                StatutRegime(
                    debut=date(2026, 1, 1),
                    regime=RegimeFiscal.REEL,
                    motif=MotifChangement.DEPASSEMENT_SEUIL,
                ),
            ],
        )

    def test_un_reclassement_posterieur_a_la_cloture_eteint_l_alerte(self):
        dossier = self._regularise_au_premier_janvier()
        livres = {"2025": balance([_vente(61000000, exercice="2025")]), "2026": []}

        vue = surveiller_un_dossier(
            dossier,
            soldes_de=lambda exercice: livres[exercice],
            seuil=SEUIL,
            borne=Borne.EXCLUSE,
            a_la_date=date(2026, 3, 15),
        )
        assert vue.acquis is not None
        assert vue.acquis.diagnostic.franchi is True
        assert vue.acquis.diagnostic.regime_actuel is RegimeFiscal.REEL, (
            "le régime est lu à la clôture de l'exercice mesuré : l'alerte "
            "survivra à la régularisation, et elle survivra pour toujours."
        )
        assert vue.reclassement_du is False
        assert vue.a_surveiller is False

    def test_un_reclassement_inscrit_a_effet_futur_eteint_deja_l_alerte(self):
        """⚠️ **Ce cas remplace une contre-épreuve devenue fausse.**

        Au pas 44, il affirmait que le même dossier, observé le 31 décembre, devait
        alerter. Il confondait « avant la date d'effet » et « avant l'inscription » :
        le dossier porte déjà son passage au réel au 1er janvier. Le domaine ne
        connaît que la date d'effet d'un statut, jamais le jour où on l'a saisi.

        Le cabinet qui voit le franchissement en novembre et l'inscrit aussitôt ne
        doit pas rester alerté jusqu'au 1er janvier : deux mois d'alerte sur un
        dossier réglé, et le collaborateur apprend à l'ignorer.
        """
        dossier = self._regularise_au_premier_janvier()
        livres = {"2025": balance([_vente(61000000, exercice="2025")]), "2026": []}

        vue = surveiller_un_dossier(
            dossier,
            soldes_de=lambda exercice: livres[exercice],
            seuil=SEUIL,
            borne=Borne.EXCLUSE,
            a_la_date=date(2025, 12, 31),
        )
        assert vue.acquis is not None
        # Le fait demeure : ce jour-là, le dossier relève encore de l'IGS.
        assert vue.acquis.diagnostic.regime_actuel is RegimeFiscal.IGS
        assert vue.reclassement_inscrit_au == date(2026, 1, 1)
        assert vue.reclassement_du is False
        assert vue.a_surveiller is False

    def test_un_statut_futur_a_l_igs_n_eteint_pas_l_alerte(self):
        """⚠️ **Seul un passage au réel répond à un franchissement.**

        La route d'inscription refuse d'inscrire le régime déjà en vigueur, mais
        l'entité, elle, admet deux périodes à l'IGS qui se suivent : c'est la forme
        que prend un portefeuille repris d'un autre logiciel, qui découpe l'histoire
        à chaque changement de centre. Un tel statut futur n'est pas un
        reclassement, et il ne doit pas faire taire l'alerte.
        """
        modele = PORTEFEUILLE_DEMO[DOSSIER]
        dossier = transiter(
            modele,
            exercices=_exercices((2025, True), (2026, False)),
            regimes=[
                StatutRegime(
                    debut=modele.date_creation,
                    fin=date(2027, 1, 1),
                    regime=RegimeFiscal.IGS,
                    motif=MotifChangement.CREATION,
                ),
                StatutRegime(
                    debut=date(2027, 1, 1),
                    regime=RegimeFiscal.IGS,
                    motif=MotifChangement.DECISION_ADMINISTRATION,
                ),
            ],
        )
        livres = {"2025": balance([_vente(61000000, exercice="2025")]), "2026": []}
        vue = surveiller_un_dossier(
            dossier,
            soldes_de=lambda exercice: livres[exercice],
            seuil=SEUIL,
            borne=Borne.EXCLUSE,
            a_la_date=date(2026, 11, 2),
        )
        assert vue.reclassement_inscrit_au is None
        assert vue.a_surveiller is True

    def test_la_contre_epreuve_sans_rien_d_inscrit(self):
        """⚠️ Le même chiffre, le même jour, sans reclassement inscrit : l'alerte.

        Sans ce cas, une surveillance qui ne signalerait jamais rien passerait le
        précédent.
        """
        dossier = _dossier(exercices=_exercices((2025, True), (2026, False)))
        livres = {"2025": balance([_vente(61000000, exercice="2025")]), "2026": []}

        vue = surveiller_un_dossier(
            dossier,
            soldes_de=lambda exercice: livres[exercice],
            seuil=SEUIL,
            borne=Borne.EXCLUSE,
            a_la_date=date(2025, 12, 31),
        )
        assert vue.reclassement_inscrit_au is None
        assert vue.reclassement_du is True
        assert vue.a_surveiller is True


class TestLaRevueDuPortefeuille:
    """⚠️ **L'ordre est le produit.**

    Un tableau de cent dossiers rangés par NIU ne se lit pas : le collaborateur le
    parcourt une fois, puis plus jamais. C'est la même leçon que la matrice
    d'affectation, où un critère de charge sans effet concentrait tous les
    dossiers sur une seule personne sans que rien ne le signale.
    """

    #: Les deux dossiers de démonstration encore à l'IGS, et un déjà au réel.
    #: ⚠️ Des dossiers réels plutôt qu'inventés : le NIU sert de clé partout, et
    #: deux entreprises forgées sous le même NIU se confondraient dans le tri sans
    #: que le cas le dise.
    EXERCICES = ((2025, True), (2026, False))

    def _trois_dossiers(self):
        return (
            _dossier(niu="P019876543210K", exercices=_exercices(*self.EXERCICES)),
            _dossier(niu="P027788990011M", exercices=_exercices(*self.EXERCICES)),
            _dossier(niu=DOSSIER_AU_REEL, exercices=_exercices(*self.EXERCICES)),
        )

    def test_les_dossiers_a_surveiller_viennent_en_tete(self):
        franchie, approche, tranquille = self._trois_dossiers()
        livres = {
            # Franchi sur l'exercice clos, et toujours à l'IGS : fait acquis.
            (franchie.niu, "2025"): balance([_vente(61000000, exercice="2025")]),
            (franchie.niu, "2026"): [],
            # 84 % du seuil sur l'exercice en cours : alerte anticipée.
            (approche.niu, "2025"): [],
            (approche.niu, "2026"): balance([_vente(42000000, exercice="2026")]),
            # Loin du seuil : rien à dire.
            (tranquille.niu, "2025"): balance([_vente(4000000, exercice="2025")]),
            (tranquille.niu, "2026"): [],
        }
        vues = surveiller_le_portefeuille(
            [tranquille, approche, franchie],
            soldes_de=lambda niu, exercice: livres[(niu, exercice)],
            seuil=SEUIL,
            borne=Borne.EXCLUSE,
            a_la_date=date(2026, 11, 2),
        )
        assert [v.niu for v in vues] == [franchie.niu, approche.niu, tranquille.niu], (
            "la revue ne trie pas par urgence : le collaborateur la lira une fois, "
            "puis plus jamais."
        )
        assert [v.a_surveiller for v in vues] == [True, True, False]
        assert vues[0].reclassement_du is True
        assert vues[1].en_cours is not None
        assert vues[1].en_cours.diagnostic.alerte_anticipee is True

    def test_le_fait_passe_devant_la_prevision_meme_avec_un_taux_inferieur(self):
        """⚠️ **L'arbitrage du tri, mesuré là où il se joue.**

        Un dossier qui vient de dépasser le seuil sur son exercice **en cours**
        affiche 120 % ; un dossier franchi l'an dernier n'affiche que 101 %. Trier
        par le taux mettrait le premier en tête.

        C'est pourtant le second qui presse : son franchissement est **acquis**,
        son exercice est clos, et l'administration peut déjà lui réclamer la TVA
        qu'il n'a pas facturée. Le premier a encore son exercice pour se préparer.
        """
        franchie, en_hausse, _ = self._trois_dossiers()
        livres = {
            (en_hausse.niu, "2025"): [],
            (en_hausse.niu, "2026"): balance([_vente(60000000, exercice="2026")]),
            (franchie.niu, "2025"): balance([_vente(50500000, exercice="2025")]),
            (franchie.niu, "2026"): [],
        }
        vues = surveiller_le_portefeuille(
            [en_hausse, franchie],
            soldes_de=lambda niu, exercice: livres[(niu, exercice)],
            seuil=SEUIL,
            borne=Borne.EXCLUSE,
            a_la_date=date(2026, 11, 2),
        )
        assert vues[0].niu == franchie.niu, [
            (v.niu, v.reclassement_du) for v in vues
        ]
        assert vues[0].reclassement_du is True
        assert vues[1].reclassement_du is False

        # ⚠️ **La contre-épreuve du tri.** Sans elle, ce cas passerait aussi avec
        # un tri par le seul taux : il faut que le dossier relégué au second rang
        # affiche vraiment le taux le plus fort.
        assert vues[1].en_cours is not None
        assert vues[0].acquis is not None
        assert (
            vues[1].en_cours.diagnostic.taux_d_approche
            > vues[0].acquis.diagnostic.taux_d_approche
        ), "les deux taux ne se croisent pas : le cas ne mesure plus l'arbitrage"


class TestParLaRoute:
    """⚠️ **Le chiffre est lu dans les livres, jamais déclaré.**

    C'est ce qui sépare cette route d'un formulaire. Un chiffre saisi à la main
    serait celui que l'adhérent croit réaliser ; celui-ci est celui que sa
    comptabilité porte, et c'est le seul que l'administration retiendra.

    ⚠️ **Ces cas fabriquent la vente par les routes réelles**, saisie puis
    validation. Aucun dossier de démonstration ne porte le moindre chiffre
    d'affaires : le constater a évité d'écrire une recette qui aurait comparé deux
    zéros en paraissant mesurer quelque chose.
    """

    pytestmark = exige_postgresql

    #: Le réviseur voit tout le cabinet et peut saisir. Un comptable au
    #: portefeuille disjoint sert de contre-épreuve de cloisonnement.
    REVISEUR = "a.bouba@cga-brcg.cm"
    COMPTABLE_D_UN_AUTRE_PORTEFEUILLE = "l.fotso@cga-brcg.cm"
    ADHERENT = "jp.nkoa@batimentplus.cm"

    #: 84 % du seuil : assez pour l'alerte anticipée, pas assez pour le franchir.
    VENTE = 42000000

    def _vendre(self, client, niu, montant=VENTE, numero_piece="PJ-VE-0001"):
        saisie = client.post(
            f"/comptabilite/dossiers/{niu}/ecritures",
            json={
                "journal": "VE",
                "exercice": "2026",
                "date_operation": "2026-06-30",
                "libelle": "Vente de l'exercice",
                "piece_justificative": numero_piece,
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
        validee = client.post(
            f"/comptabilite/dossiers/{niu}/ecritures/2026/VE/{numero}/validation",
            json={},
        )
        assert validee.status_code == 200, validee.text

    def test_le_chiffre_rendu_est_celui_des_livres(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, self.REVISEUR)
        self._vendre(client, DOSSIER)

        vue = client.get(
            f"/obligations/dossiers/{DOSSIER}/seuil-de-regime?a_la_date=2026-11-02"
        )
        assert vue.status_code == 200, vue.text
        corps = vue.json()

        # ⚠️ Le seuil vient du référentiel, résolu à la date. Le figer dans le
        # cas ferait passer un test qui ne mesure plus le référentiel.
        assert Decimal(corps["seuil"]) == SEUIL

        assert corps["en_cours"] is not None
        diagnostic = corps["en_cours"]["diagnostic"]
        assert Decimal(diagnostic["chiffre_affaires"]) == Decimal(self.VENTE)
        assert diagnostic["franchi"] is False
        assert diagnostic["alerte_anticipee"] is True
        assert corps["a_surveiller"] is True
        assert corps["reclassement_du"] is False

        # ⚠️ Et le chiffre est bien celui de la balance, pas un nombre parallèle.
        balance_reelle = client.get(
            f"/comptabilite/dossiers/{DOSSIER}/balance?exercice=2026"
        ).json()
        attendu = sum(
            Decimal(ligne["total_credit"]) - Decimal(ligne["total_debit"])
            for ligne in balance_reelle
            if ligne["compte"].startswith("70")
        )
        assert Decimal(diagnostic["chiffre_affaires"]) == attendu
        assert attendu > 0

    def test_un_produit_financier_ne_fait_pas_franchir_le_seuil(self, plateforme):
        """⚠️ **La distinction qui évite un reclassement indu, mesurée par HTTP.**

        Une entreprise qui place sa trésorerie voit ses comptes de classe 7
        grossir sans que son chiffre d'affaires bouge. Compter la classe entière
        lui ferait prendre un numéro de TVA qu'elle ne doit pas.
        """
        client = plateforme
        ouvrir_une_session(client, self.REVISEUR)
        self._vendre(client, DOSSIER)

        interets = client.post(
            f"/comptabilite/dossiers/{DOSSIER}/ecritures",
            json={
                "journal": "OD",
                "exercice": "2026",
                "date_operation": "2026-09-30",
                "libelle": "Intérêts de placement",
                "piece_justificative": "PJ-INT-0001",
                "lignes": [
                    {"compte": "521", "libelle": "Banque", "sens": "DEBIT",
                     "montant": "20000000"},
                    {"compte": "771", "libelle": "Intérêts", "sens": "CREDIT",
                     "montant": "20000000"},
                ],
            },
        )
        assert interets.status_code == 201, interets.text
        numero = interets.json()["numero"]
        assert client.post(
            f"/comptabilite/dossiers/{DOSSIER}/ecritures/2026/OD/{numero}/validation",
            json={},
        ).status_code == 200

        corps = client.get(
            f"/obligations/dossiers/{DOSSIER}/seuil-de-regime?a_la_date=2026-11-02"
        ).json()
        diagnostic = corps["en_cours"]["diagnostic"]
        assert Decimal(diagnostic["chiffre_affaires"]) == Decimal(self.VENTE), (
            "les intérêts sont entrés dans le chiffre d'affaires : le dossier sera "
            "reclassé au réel sans le devoir."
        )
        assert diagnostic["franchi"] is False

    def test_la_revue_ne_rend_que_ce_qui_demande_un_regard(self, plateforme):
        """⚠️ Le filtre est actif par défaut, et c'est un choix.

        Une revue qui rend cent dossiers dont trois méritent un regard se lit une
        fois, puis plus jamais.
        """
        client = plateforme
        ouvrir_une_session(client, self.REVISEUR)
        self._vendre(client, DOSSIER)

        filtree = client.get("/obligations/seuils-de-regime?a_la_date=2026-11-02")
        assert filtree.status_code == 200, filtree.text
        entiere = client.get(
            "/obligations/seuils-de-regime"
            "?a_la_date=2026-11-02&a_surveiller_seulement=false"
        )
        assert entiere.status_code == 200, entiere.text

        # ⚠️ **La liste filtrée doit être non vide.** Une première version de ce
        # cas affirmait que tous les dossiers rendus étaient à surveiller, et il
        # passait sur une liste vide : l'assertion ne mesurait rien.
        assert filtree.json(), "aucun dossier à surveiller : le cas ne mesure rien"
        assert len(entiere.json()) > len(filtree.json())
        assert all(v["a_surveiller"] is True for v in filtree.json())
        assert DOSSIER in [v["niu"] for v in filtree.json()]

    def test_la_revue_est_triee_par_urgence(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, self.REVISEUR)
        # Un dossier franchi sur l'exercice en cours, un autre simplement proche.
        self._vendre(client, DOSSIER, montant=42000000)
        self._vendre(client, "P027788990011M", montant=61000000, numero_piece="PJ-VE-0002")

        vues = client.get(
            "/obligations/seuils-de-regime?a_la_date=2026-11-02"
        ).json()
        assert len(vues) >= 2, vues
        approches = [
            Decimal(v["en_cours"]["diagnostic"]["taux_d_approche"]) for v in vues
        ]
        assert approches == sorted(approches, reverse=True), approches

    def test_l_adherent_ne_revoit_pas_le_portefeuille_du_cabinet(self, plateforme):
        """La revue nomme les clients du cabinet, un par un, avec leur chiffre
        d'affaires. C'est le document le plus sensible que ce contexte produise."""
        client = plateforme
        ouvrir_une_session(client, self.ADHERENT)
        reponse = client.get("/obligations/seuils-de-regime?a_la_date=2026-11-02")
        assert reponse.status_code in (403, 404), reponse.text

    def test_la_revue_ne_montre_que_le_portefeuille_du_collaborateur(self, plateforme):
        """⚠️ **La portée s'applique avant le calcul, et le cas le mesure.**

        Le réviseur voit tout le cabinet : une revue qui ignorerait la portée lui
        rendrait exactement la même chose, et le défaut passerait inaperçu. Il
        faut donc un collaborateur au portefeuille **restreint**.

        Ce que la revue rend est la liste nominative des clients du cabinet avec
        leur chiffre d'affaires. Un comptable qui la verrait entière emporterait le
        portefeuille de ses collègues en partant.
        """
        client = plateforme
        ouvrir_une_session(client, self.COMPTABLE_D_UN_AUTRE_PORTEFEUILLE)
        sienne = client.get(
            "/obligations/seuils-de-regime"
            "?a_la_date=2026-11-02&a_surveiller_seulement=false"
        )
        assert sienne.status_code == 200, sienne.text
        vus = {v["niu"] for v in sienne.json()}

        ouvrir_une_session(client, self.REVISEUR)
        tout = client.get(
            "/obligations/seuils-de-regime"
            "?a_la_date=2026-11-02&a_surveiller_seulement=false"
        ).json()
        tous = {v["niu"] for v in tout}

        assert vus < tous, (
            f"le comptable voit {len(vus)} dossiers sur {len(tous)} : la portée ne "
            "s'applique pas à la revue."
        )
        assert DOSSIER not in vus, (
            "un dossier hors de son portefeuille figure dans sa revue."
        )
        assert DOSSIER in tous

    def test_un_dossier_hors_perimetre_repond_404_et_non_403(self, plateforme):
        """⚠️ Du point de vue de qui n'y a pas accès, un dossier hors périmètre est
        un dossier qui n'existe pas.

        Un `403` dirait « ce dossier existe, et il ne vous regarde pas » : un
        collaborateur apprendrait par essais successifs quels NIU le cabinet suit,
        c'est-à-dire la liste de ses clients.
        """
        client = plateforme
        ouvrir_une_session(client, self.COMPTABLE_D_UN_AUTRE_PORTEFEUILLE)
        # ⚠️ Il détient bien la permission : sans cela le refus serait un 403 de
        # permission, et le cas ne mesurerait pas le cloisonnement.
        sien = client.get(
            f"/obligations/dossiers/{DOSSIER_AU_REEL}/seuil-de-regime"
            "?a_la_date=2026-11-02"
        )
        assert sien.status_code == 200, sien.text

        autre = client.get(
            f"/obligations/dossiers/{DOSSIER}/seuil-de-regime?a_la_date=2026-11-02"
        )
        assert autre.status_code == 404, autre.text
