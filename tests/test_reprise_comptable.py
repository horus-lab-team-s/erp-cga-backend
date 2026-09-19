"""La reprise : relire un fichier venu du logiciel du client.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Un adhérent n'arrive jamais vierge. Il arrive avec dix mois d'écritures dans le
logiciel de son ancien comptable, et la première chose que le centre doit savoir
faire est de les reprendre. Sans cela, la plateforme ne se vend qu'à des
entreprises qui se créent.

⚠️ **L'import n'est pas le miroir de l'export, et le croire coûte cher.** À
l'export, les données sont les nôtres : le pivot les garantit. À l'import, rien
n'est garanti. Le fichier vient d'un système que nous n'avons pas écrit, exporté
par quelqu'un que nous ne connaissons pas, souvent repris à la main dans un
tableur entre les deux.

Ces cas gardent les trois règles qui en découlent :

* **rien n'entre à moitié**, car un lot partiel est un grand livre déséquilibré ;
* **toutes les anomalies d'un coup**, car un fichier de reprise se corrige une
  fois et non quarante ;
* **ce qui entre est un brouillon**, car une écriture venue d'ailleurs n'a été
  validée par personne au centre.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.contextes.comptabilite.adaptateurs.sortant.profils_echange import (
    charger_les_profils,
)
from app.contextes.comptabilite.domaine.echange import (
    CHAMPS_INDISPENSABLES_A_LA_LECTURE,
    Colonne,
    EchangeRefuse,
    FormeDuSens,
    ProfilDEchange,
    lire,
    rendre,
)
from app.contextes.comptabilite.domaine.entites import (
    EcritureComptable,
    EtatEcriture,
    LigneEcriture,
    Sens,
)
from app.infrastructure.config import RACINE_DEPOT
from tests.conftest import exige_postgresql, ouvrir_une_session

REFERENTIEL = RACINE_DEPOT / "Docs" / "referentiel" / "echange"


def _profil_lisible(**surcharges) -> ProfilDEchange:
    """Un profil complet pour la lecture, à colonne de sens unique.

    ⚠️ Les colonnes portent les trois champs indispensables **et** de quoi
    regrouper : sans `journal`, l'appelant devrait en nommer un, ce qui est un
    autre cas.
    """
    defauts = {
        "code": "essai",
        "libelle": "Profil d'essai lisible",
        "encodage": "utf-8",
        "fin_de_ligne": "\n",
        "decimale": ",",
        "format_date": "%d/%m/%Y",
        "entete": True,
        "forme_du_sens": FormeDuSens.COLONNE_UNIQUE,
        "colonnes": (
            Colonne(champ="journal"),
            Colonne(champ="numero"),
            Colonne(champ="date_operation"),
            Colonne(champ="libelle_ecriture"),
            Colonne(champ="compte"),
            Colonne(champ="libelle_ligne"),
            Colonne(champ="sens"),
            Colonne(champ="montant"),
        ),
    }
    return ProfilDEchange(**{**defauts, **surcharges})


#: Un lot d'essai équilibré, en clair, tel qu'un tableur le rendrait.
EN_TETE = "journal;numero;date_operation;libelle_ecriture;compte;libelle_ligne;sens;montant"
LIGNE_DEBIT = "AC;12;02/07/2026;Achat de semences;602;Semences;D;2840000,00"
LIGNE_CREDIT = "AC;12;02/07/2026;Achat de semences;401;Fournisseur;C;2840000,00"


def _fichier(*lignes: str, entete: str = EN_TETE) -> bytes:
    return ("\n".join((entete, *lignes)) + "\n").encode("utf-8")


class TestLAllerRetour:
    """⚠️ **La seule preuve qui compte, et elle emploie les profils réels.**

    Un cas qui lirait un fichier forgé à la main prouverait que le lecteur lit ce
    que le cas a écrit. L'aller-retour prouve que le centre sait relire **ce qu'il
    envoie vraiment à ses clients**, avec leurs profils, leurs encodages et leurs
    largeurs fixes.
    """

    @pytest.mark.parametrize("code", sorted(charger_les_profils(REFERENTIEL)))
    def test_ce_que_nous_ecrivons_nous_savons_le_relire(self, code):
        profil = charger_les_profils(REFERENTIEL)[code]
        origine = EcritureComptable(
            journal="OD",
            exercice="2026",
            numero=7,
            date_operation=date(2026, 7, 15),
            libelle="Quincaillerie du Wouri",
            piece_justificative="PJ-A1",
            reference_externe="F-2026-0413",
            etat=EtatEcriture.VALIDEE,
            validee_par="a.bouba",
            validee_le=datetime(2026, 7, 16, 9, 0),
            lignes=[
                LigneEcriture(
                    compte="612",
                    libelle="Transport",
                    sens=Sens.DEBIT,
                    montant=Decimal("1200000"),
                ),
                LigneEcriture(
                    compte="4451",
                    libelle="TVA",
                    sens=Sens.DEBIT,
                    montant=Decimal("231000"),
                ),
                LigneEcriture(
                    compte="401",
                    libelle="Fournisseur",
                    sens=Sens.CREDIT,
                    montant=Decimal("1431000"),
                    tiers="F-WOURI",
                ),
            ],
        )

        texte = rendre([origine], profil)
        lot = lire(texte.encode(profil.encodage), profil, exercice="2026")

        assert lot.exploitable, [a.motif for a in lot.anomalies]
        assert lot.lignes_lues == 3
        (relue,) = lot.ecritures

        # ⚠️ Ce que le fichier porte **doit** revenir à l'identique. Un profil qui
        # n'exporte pas un champ ne peut pas le rendre, et ce n'est pas un défaut :
        # c'est le format du client qui ne le transporte pas.
        assert relue.journal == origine.journal
        assert relue.exercice == "2026"
        assert relue.numero == origine.numero
        assert relue.date_operation == origine.date_operation
        assert [x.compte for x in relue.lignes] == ["612", "4451", "401"]
        assert [x.sens for x in relue.lignes] == [Sens.DEBIT, Sens.DEBIT, Sens.CREDIT]
        assert [x.montant for x in relue.lignes] == [
            Decimal("1200000.00"),
            Decimal("231000.00"),
            Decimal("1431000.00"),
        ]

        # ⚠️ **Les comptes à largeur fixe reviennent dépadés.** Le profil Sage
        # écrit « 00000612 » ; le rendre tel quel créerait un compte inconnu du
        # plan, donc un import accepté et une balance fausse par compte.
        assert all(not x.compte.startswith("0") for x in relue.lignes)

    def test_ce_qui_entre_est_toujours_un_brouillon(self):
        """⚠️ **La symétrie de l'export**, et la règle la plus importante du module.

        « On n'exporte que du validé, on n'importe que du brouillon. » Faire entrer
        une écriture validée ferait engager la responsabilité du cabinet sur le
        travail d'un autre, sans qu'aucun collaborateur ne l'ait lue.
        """
        lot = lire(_fichier(LIGNE_DEBIT, LIGNE_CREDIT), _profil_lisible(), exercice="2026")
        assert lot.exploitable, [a.motif for a in lot.anomalies]
        (ecriture,) = lot.ecritures
        assert ecriture.etat is EtatEcriture.BROUILLON
        assert ecriture.validee_par is None
        assert ecriture.validee_le is None

    def test_l_exercice_vient_de_l_appel_et_non_du_fichier(self):
        """On importe **dans** un exercice nommé par l'exploitant.

        Un fichier qui en porterait un autre serait une erreur de l'exploitant,
        pas une donnée à reprendre : c'est lui qui sait dans quel exercice il
        veut reprendre, le fichier ne fait que proposer des mouvements.
        """
        lot = lire(_fichier(LIGNE_DEBIT, LIGNE_CREDIT), _profil_lisible(), exercice="2025")
        (ecriture,) = lot.ecritures
        assert ecriture.exercice == "2025"
        assert "exercice" not in CHAMPS_INDISPENSABLES_A_LA_LECTURE


class TestLEncodageQuiMentEnSilence:
    """⚠️ **Le piège le plus coûteux du module**, parce qu'il ne se signale pas.

    Un fichier `cp1252` lu en `utf-8` casse sur le premier accent : c'est le bon
    cas, l'erreur est franche. L'autre sens ne casse jamais : presque tout octet a
    un sens en `cp1252`, donc un fichier UTF-8 s'y décode sans la moindre erreur et
    rend un texte parfaitement valide et parfaitement faux.

    Le client importe, et découvre « SociÃ©tÃ© » dans chaque libellé de son grand
    livre. Définitivement : une fois entrés, les libellés abîmés ne se rattrapent
    plus.
    """

    def test_un_fichier_utf8_lu_en_cp1252_est_refuse_et_non_accepte(self):
        profil = _profil_lisible(encodage="cp1252")
        accentue = LIGNE_DEBIT.replace("Achat de semences", "Achat de société")
        octets = ("\n".join((EN_TETE, accentue, LIGNE_CREDIT)) + "\n").encode("utf-8")

        # ⚠️ La contre-épreuve du cas : sans elle, on ne saurait pas que le piège
        # existe vraiment. cp1252 **accepte** ces octets sans broncher.
        assert octets.decode("cp1252"), "cp1252 refuse : le piège aurait disparu"
        assert "Ã©" in octets.decode("cp1252")

        with pytest.raises(EchangeRefuse) as refus:
            lire(octets, profil, exercice="2026")
        assert "charabia" in str(refus.value)
        assert "UTF-8" in str(refus.value)

    def test_un_fichier_cp1252_lu_en_utf8_est_refuse_franchement(self):
        profil = _profil_lisible(encodage="utf-8")
        accentue = LIGNE_DEBIT.replace("Achat de semences", "Achat de société")
        octets = ("\n".join((EN_TETE, accentue, LIGNE_CREDIT)) + "\n").encode("cp1252")

        with pytest.raises(EchangeRefuse) as refus:
            lire(octets, profil, exercice="2026")
        assert "ne se décode pas" in str(refus.value)
        assert "utf-8" in str(refus.value)

    def test_la_marque_d_ordre_d_octets_d_un_tableur_ne_gene_pas(self):
        """Un tableur enregistre en « UTF-8 avec BOM », trois octets invisibles.

        Sans retrait, le premier intitulé de colonne les porte, et quand le profil
        n'a pas d'en-tête, c'est le premier champ de la première ligne.
        """
        profil = _profil_lisible(encodage="utf-8")
        octets = b"\xef\xbb\xbf" + _fichier(LIGNE_DEBIT, LIGNE_CREDIT)
        lot = lire(octets, profil, exercice="2026")
        assert lot.exploitable, [a.motif for a in lot.anomalies]


class TestCeQueLeProfilDoitPorterPourRelire:
    """Écrire et relire ne demandent pas la même chose au profil.

    ⚠️ **Un seul profil sert dans les deux sens**, et c'est l'essentiel : un profil
    de lecture séparé divergerait de son jumeau d'écriture au premier correctif.
    Mais ce qu'un profil n'écrit pas, il ne peut pas le relire, et il vaut mieux
    le dire au chargement qu'au milieu d'un fichier de quatre mille lignes.
    """

    @pytest.mark.parametrize("manquant", sorted(CHAMPS_INDISPENSABLES_A_LA_LECTURE))
    def test_un_profil_ampute_refuse_de_relire_et_dit_quoi(self, manquant):
        complet = _profil_lisible()
        ampute = complet.model_copy(
            update={"colonnes": tuple(c for c in complet.colonnes if c.champ != manquant)}
        )
        with pytest.raises(EchangeRefuse) as refus:
            lire(_fichier(LIGNE_DEBIT, LIGNE_CREDIT), ampute, exercice="2026")
        assert manquant in str(refus.value)
        assert "ne sait pas relire" in str(refus.value)

    def test_sans_colonne_journal_l_appelant_en_nomme_un(self):
        """Beaucoup de formats n'ont qu'un journal par fichier, et ne le portent pas.

        Refuser ces fichiers écarterait la moitié des reprises réelles ; les
        accepter sans journal produirait des écritures introuvables au grand livre.
        L'appelant tranche, parce que lui sait dans quel journal il importe.
        """
        complet = _profil_lisible()
        sans_journal = complet.model_copy(
            update={"colonnes": tuple(c for c in complet.colonnes if c.champ != "journal")}
        )
        entete = EN_TETE.replace("journal;", "")
        lignes = [x.split(";", 1)[1] for x in (LIGNE_DEBIT, LIGNE_CREDIT)]
        octets = _fichier(*lignes, entete=entete)

        sans_rien = lire(octets, sans_journal, exercice="2026")
        assert not sans_rien.exploitable
        assert "aucun journal" in sans_rien.anomalies[0].motif

        avec = lire(octets, sans_journal, exercice="2026", journal_par_defaut="AC")
        assert avec.exploitable, [a.motif for a in avec.anomalies]
        assert avec.ecritures[0].journal == "AC"


class TestLesMontantsQuArriveNtVraiment:
    """Le fichier ne vient pas toujours d'un logiciel : il passe par un tableur.

    ⚠️ Nous n'écrivons **jamais** de séparateur de milliers, et nous en recevons
    tout le temps. Les refuser rendrait l'outil inutilisable pour la moitié des
    reprises réelles.
    """

    @pytest.mark.parametrize(
        "ecrit, attendu",
        [
            ("2840000,00", Decimal("2840000.00")),
            # ⚠️ L'espace ordinaire d'un tableur anglophone en français.
            ("2 840 000,00", Decimal("2840000.00")),
            # ⚠️ **L'espace insécable**, celle qu'insère un tableur francophone.
            # Elle est invisible : un exploitant qui compare à l'œil ne voit
            # aucune différence avec la précédente.
            ("2 840 000,00", Decimal("2840000.00")),
            # ⚠️ L'espace fine insécable, celle des versions récentes.
            ("2 840 000,00", Decimal("2840000.00")),
            ("2840000", Decimal("2840000")),
        ],
    )
    def test_les_separateurs_de_milliers_sont_admis(self, ecrit, attendu):
        lot = lire(
            _fichier(
                LIGNE_DEBIT.replace("2840000,00", ecrit),
                LIGNE_CREDIT.replace("2840000,00", ecrit),
            ),
            _profil_lisible(),
            exercice="2026",
        )
        assert lot.exploitable, [a.motif for a in lot.anomalies]
        assert lot.ecritures[0].lignes[0].montant == attendu

    @pytest.mark.parametrize(
        "ecrit, trace",
        [
            # ⚠️ **On ne devine pas la décimale.** `1,234.56` lu à la française
            # donnerait « 1.234.56 », refusé ; lu à l'anglaise, 1234,56. Les deux
            # lectures sont plausibles, et se tromper met un facteur mille dans
            # une balance sans que rien ne le signale.
            ("1,234.56", "illisible"),
            ("deux millions", "illisible"),
            ("", "illisible"),
            # ⚠️ Ces deux-là sont refusés **par le pivot**, pas par le lecteur.
            # Le lecteur rejouait la règle ; une mutation a montré que la
            # supprimer ne cassait rien, parce que l'entité la tenait derrière.
            # Le message reste celui du pivot, traduit, avec le rang du fichier.
            ("-2840000,00", "strictement positif"),
            ("0,00", "strictement positif"),
        ],
    )
    def test_ce_qui_n_est_pas_un_mouvement_est_refuse(self, ecrit, trace):
        lot = lire(
            _fichier(LIGNE_DEBIT.replace("2840000,00", ecrit), LIGNE_CREDIT),
            _profil_lisible(),
            exercice="2026",
        )
        assert not lot.exploitable
        assert trace in lot.anomalies[0].motif, lot.anomalies[0].motif
        # ⚠️ Le numéro de ligne compte l'en-tête, parce que c'est ce que montre le
        # tableur de l'exploitant. Un décalage d'une unité le fait chercher au
        # mauvais endroit, et il conclut que l'outil se trompe.
        assert lot.anomalies[0].ligne == 2

    def test_un_montant_negatif_n_est_pas_un_sens(self):
        """Le sens a sa propre colonne.

        Accepter `-2840000` au débit comme un crédit paraît serviable et produit
        une balance juste au total, fausse par compte : le mouvement est du bon
        côté de l'équilibre et du mauvais côté du grand livre.
        """
        lot = lire(
            _fichier(LIGNE_DEBIT.replace("2840000,00", "-2840000,00"), LIGNE_CREDIT),
            _profil_lisible(),
            exercice="2026",
        )
        assert not lot.exploitable
        assert "jamais le signe" in lot.anomalies[0].motif
        # ⚠️ Et c'est bien le pivot qui parle, pas le lecteur : un second contrôle
        # dans le lecteur divergerait du premier au prochain correctif.
        assert "règles comptables du système" in lot.anomalies[0].motif


class TestLesDeuxFormesDuSens:
    """⚠️ Elles ne se devinent pas l'une de l'autre, et c'est pourquoi on les déclare.

    Lire un fichier à colonnes séparées comme un fichier à colonne unique donne
    **toutes les lignes au débit**, et la balance est alors fausse du double.
    """

    def test_colonnes_separees_une_seule_porte_le_montant(self):
        profil = _profil_lisible(
            forme_du_sens=FormeDuSens.COLONNES_SEPAREES,
            colonnes=(
                Colonne(champ="journal"),
                Colonne(champ="numero"),
                Colonne(champ="date_operation"),
                Colonne(champ="compte"),
                Colonne(champ="libelle_ligne"),
                Colonne(champ="debit"),
                Colonne(champ="credit"),
            ),
        )
        entete = "journal;numero;date_operation;compte;libelle_ligne;debit;credit"
        lot = lire(
            _fichier(
                "AC;12;02/07/2026;602;Semences;2840000,00;",
                "AC;12;02/07/2026;401;Fournisseur;;2840000,00",
                entete=entete,
            ),
            profil,
            exercice="2026",
        )
        assert lot.exploitable, [a.motif for a in lot.anomalies]
        assert [x.sens for x in lot.ecritures[0].lignes] == [Sens.DEBIT, Sens.CREDIT]

    @pytest.mark.parametrize(
        "ligne, trace",
        [
            ("AC;12;02/07/2026;602;Semences;2840000,00;2840000,00", "tous deux"),
            ("AC;12;02/07/2026;602;Semences;;", "ni débit ni crédit"),
        ],
    )
    def test_une_ligne_porte_un_mouvement_et_un_seul(self, ligne, trace):
        profil = _profil_lisible(
            forme_du_sens=FormeDuSens.COLONNES_SEPAREES,
            colonnes=(
                Colonne(champ="journal"),
                Colonne(champ="numero"),
                Colonne(champ="date_operation"),
                Colonne(champ="compte"),
                Colonne(champ="libelle_ligne"),
                Colonne(champ="debit"),
                Colonne(champ="credit"),
            ),
        )
        entete = "journal;numero;date_operation;compte;libelle_ligne;debit;credit"
        lot = lire(_fichier(ligne, entete=entete), profil, exercice="2026")
        assert not lot.exploitable
        assert trace in lot.anomalies[0].motif

    def test_un_marqueur_de_sens_inconnu_est_nomme(self):
        lot = lire(
            _fichier(LIGNE_DEBIT.replace(";D;", ";Débit;"), LIGNE_CREDIT),
            _profil_lisible(),
            exercice="2026",
        )
        assert not lot.exploitable
        assert "« Débit » inconnu" in lot.anomalies[0].motif
        assert "« D »" in lot.anomalies[0].motif


#: ⚠️ Les trois espaces du cas des milliers doivent être **trois caractères
#: distincts**. Copiées-collées, elles se ressemblent à l'écran, et le cas
#: passerait en ne mesurant qu'une seule d'entre elles.
ESPACES_DE_MILLIERS = (" ", " ", " ")


def test_les_trois_espaces_du_cas_precedent_sont_bien_distinctes():
    assert len(set(ESPACES_DE_MILLIERS)) == 3
    source = (RACINE_DEPOT / "Backend_erp_cga" / "tests" / "test_reprise_comptable.py").read_text()
    for espace in ESPACES_DE_MILLIERS:
        assert f"2{espace}840{espace}000,00" in source, (
            f"l'espace {hex(ord(espace))} a disparu du cas des milliers : "
            "il ne mesure plus que les autres."
        )


class TestLesDatesEtLesComptes:
    def test_une_date_au_mauvais_format_est_refusee(self):
        """⚠️ Une date au format américain se lit **sans erreur** jusqu'au treizième
        jour du mois, et le 07/02 devient le 2 juillet au lieu du 7 février.

        C'est pourquoi le format est déclaré au profil et jamais deviné : douze
        jours sur trente et un, la mauvaise lecture est silencieuse.
        """
        lot = lire(
            _fichier(
                LIGNE_DEBIT.replace("02/07/2026", "2026-07-02"),
                LIGNE_CREDIT,
            ),
            _profil_lisible(),
            exercice="2026",
        )
        assert not lot.exploitable
        assert "illisible au format" in lot.anomalies[0].motif
        assert "%d/%m/%Y" in lot.anomalies[0].motif

    def test_un_compte_absent_du_plan_est_refuse_quand_le_plan_est_connu(self):
        """Accepter produirait une balance juste au total et fausse par compte.

        L'écart ne se verrait qu'aux états financiers, des mois plus tard, et
        personne ne le relierait à l'import.
        """
        lot = lire(
            _fichier(LIGNE_DEBIT, LIGNE_CREDIT),
            _profil_lisible(),
            exercice="2026",
            comptes_connus=frozenset({"401", "411"}),
        )
        assert not lot.exploitable
        assert "« 602 » est absent du plan" in lot.anomalies[0].motif

    def test_sans_plan_fourni_aucun_compte_n_est_refuse(self):
        """⚠️ La contre-épreuve du cas précédent.

        Sans elle, on ne saurait pas si c'est le plan qui refuse ou le lecteur qui
        refuse tout. Le plan est facultatif parce qu'un appelant qui ne l'a pas
        sous la main doit quand même pouvoir relire un fichier.
        """
        lot = lire(_fichier(LIGNE_DEBIT, LIGNE_CREDIT), _profil_lisible(), exercice="2026")
        assert lot.exploitable, [a.motif for a in lot.anomalies]

    def test_le_depadding_ne_s_applique_que_si_le_profil_le_declare(self):
        """⚠️ **On ne retire que ce que le profil déclare avoir mis.**

        Retirer les zéros de tête par flair mutilerait un plan comptable qui les
        emploie vraiment. Le profil dit « largeur 8, complété de zéros à gauche »,
        donc la lecture les retire ; il ne dit rien, donc elle n'y touche pas.
        """
        colonnes_larges = tuple(
            Colonne(champ="compte", largeur=8) if c.champ == "compte" else c
            for c in _profil_lisible().colonnes
        )
        avec = lire(
            _fichier(
                LIGNE_DEBIT.replace(";602;", ";00000602;"),
                LIGNE_CREDIT.replace(";401;", ";00000401;"),
            ),
            _profil_lisible(colonnes=colonnes_larges),
            exercice="2026",
        )
        assert [x.compte for x in avec.ecritures[0].lignes] == ["602", "401"]

        # ⚠️ **La contre-épreuve**, et elle enseigne davantage que le cas.
        # Sans la déclaration de largeur, le compte reste « 00000602 », et le
        # pivot le refuse : il interdit qu'un compte commence par zéro. Le refus
        # sort en **anomalie nommée avec son rang**, jamais en trace de
        # validation : c'est ici, et uniquement ici, que des données étrangères
        # rencontrent le domaine.
        sans = lire(
            _fichier(
                LIGNE_DEBIT.replace(";602;", ";00000602;"),
                LIGNE_CREDIT.replace(";401;", ";00000401;"),
            ),
            _profil_lisible(),
            exercice="2026",
        )
        assert not sans.exploitable
        assert [a.ligne for a in sans.anomalies] == [2, 3]
        assert "règles comptables du système" in sans.anomalies[0].motif
        assert "00000602" in sans.anomalies[0].motif
        # ⚠️ Le message parle français et ne renvoie nulle part : un exploitant
        # comptable à qui l'on montre « string_pattern_mismatch » conclut que
        # l'outil est cassé, pas son fichier.
        assert "pydantic" not in sans.anomalies[0].motif.lower()
        assert "https://" not in sans.anomalies[0].motif


class TestCeQuiRompLAssemblage:
    def test_deux_ecritures_sous_un_meme_numero_sont_refusees(self):
        """Le cas arrive vraiment, quand deux exercices sont exportés d'un bloc.

        Il faudrait choisir une des deux dates, et ce choix n'appartient pas au
        code : le fichier est ambigu, l'exploitant tranche.
        """
        lot = lire(
            _fichier(LIGNE_DEBIT, LIGNE_CREDIT.replace("02/07/2026", "02/07/2025")),
            _profil_lisible(),
            exercice="2026",
        )
        assert not lot.exploitable
        assert "sous un même numéro" in lot.anomalies[0].motif
        assert "02/07/2026" in lot.anomalies[0].motif
        assert "02/07/2025" in lot.anomalies[0].motif

    def test_une_ecriture_desequilibree_nomme_ses_lignes_de_fichier(self):
        """⚠️ Sans les rangs, l'exploitant cherche dans quatre mille lignes.

        L'équilibre est revérifié ici plutôt que laissé à l'entité : le pivot le
        refuse bien à la construction, mais l'erreur de validation serait illisible
        au milieu d'un rapport d'import.
        """
        lot = lire(
            _fichier(LIGNE_DEBIT, LIGNE_CREDIT.replace("2840000,00", "2800000,00")),
            _profil_lisible(),
            exercice="2026",
        )
        assert not lot.exploitable
        (anomalie,) = lot.anomalies
        assert "ne s'équilibre pas" in anomalie.motif
        assert "écart 40000" in anomalie.motif
        assert "Lignes 2 à 3" in anomalie.motif

    def test_le_mauvais_nombre_de_colonnes_est_nomme_avec_son_extrait(self):
        """Un séparateur dans un libellé, ou un fichier qui n'est pas celui du profil.

        L'extrait évite de rouvrir le fichier : l'exploitant voit tout de suite que
        la colonne montant contient une date.
        """
        lot = lire(
            _fichier("AC;12;02/07/2026;Achat;602", LIGNE_CREDIT),
            _profil_lisible(),
            exercice="2026",
        )
        assert not lot.exploitable
        assert "5 colonne(s) au lieu de 8" in lot.anomalies[0].motif
        assert lot.anomalies[0].extrait.startswith("AC;12;")


class TestLesTroisReglesDuModule:
    """Les trois phrases qui tiennent tout le module, chacune avec son cas."""

    def test_rien_n_entre_a_moitie(self):
        """⚠️ **Règle 1.** Un lot partiellement importé est un grand livre
        déséquilibré, et personne ne sait de combien.

        Le fichier ci-dessous porte une écriture parfaitement bonne et une
        mauvaise. Aucune des deux ne sort.
        """
        bonne_debit = LIGNE_DEBIT.replace(";12;", ";13;")
        bonne_credit = LIGNE_CREDIT.replace(";12;", ";13;")
        lot = lire(
            _fichier(
                LIGNE_DEBIT.replace("2840000,00", "zéro"),
                LIGNE_CREDIT,
                bonne_debit,
                bonne_credit,
            ),
            _profil_lisible(),
            exercice="2026",
        )
        assert not lot.exploitable
        assert lot.ecritures == (), "une écriture est sortie d'un lot en échec"
        # ⚠️ Le compte de lignes est rendu **même en échec** : c'est le premier
        # chiffre que regarde un exploitant pour savoir si le fichier qu'il a
        # choisi est bien celui qu'il croit.
        assert lot.lignes_lues == 4

    def test_toutes_les_anomalies_d_un_coup(self):
        """⚠️ **Règle 2.** Un fichier de reprise se corrige une fois, pas quarante.

        Rendre la première erreur seule condamne l'exploitant à autant
        d'allers-retours qu'il y a d'erreurs. Il abandonnera avant, et saisira à
        la main.
        """
        lot = lire(
            _fichier(
                LIGNE_DEBIT.replace("2840000,00", "zéro"),
                LIGNE_CREDIT.replace("02/07/2026", "2026-07-02"),
                LIGNE_DEBIT.replace(";12;", ";14;").replace(";D;", ";X;"),
            ),
            _profil_lisible(),
            exercice="2026",
        )
        rangs = [a.ligne for a in lot.anomalies]
        assert rangs == [2, 3, 4], [a.motif for a in lot.anomalies]
        motifs = " ".join(a.motif for a in lot.anomalies)
        assert "illisible" in motifs
        assert "format" in motifs
        assert "inconnu" in motifs


class TestLaRepriseParLaRoute:
    """La reprise en vraie grandeur : PostgreSQL, HTTP, comptes réels.

    ⚠️ Les cas de domaine plus haut mesurent le lecteur. Ils ne disent rien de ce
    que la **route** en fait : quelle permission elle exige, ce qu'elle écrit, et
    surtout si le mode contrôle contrôle vraiment. C'est exactement la leçon de la
    recette de production, qui a trouvé trois assertions creuses derrière cinq cas
    verts.
    """

    #: ⚠️ Seule cette classe exige une vraie base. Marquer le fichier entier
    #: ferait taire les trente-six cas de domaine sur une machine sans
    #: PostgreSQL, alors qu'ils n'en ont aucun besoin et portent l'essentiel du
    #: sens de ce module.
    pytestmark = exige_postgresql

    def test_le_mode_controle_n_ecrit_rien_et_dit_ce_qui_entrerait(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)
        avant = _combien_d_ecritures(client)

        rapport = _reprendre(client, _lot_de_reprise(), appliquer=False)
        assert rapport["applique"] is False
        assert rapport["anomalies"] == [], rapport["anomalies"]
        assert rapport["ecritures_lues"] == 2
        assert rapport["lignes_lues"] == 4
        # ⚠️ Aucune clé : le numéro se tire au moment d'écrire, et rien n'a été
        # écrit. Rendre des clés en mode contrôle promettrait des numéros que la
        # reprise réelle n'attribuerait pas.
        assert rapport["cles_enregistrees"] == []

        assert _combien_d_ecritures(client) == avant, (
            "le mode contrôle a écrit : ce n'est plus un contrôle."
        )

    def test_appliquer_ecrit_les_ecritures_en_brouillon_et_numerotees_par_nous(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)
        avant = _combien_d_ecritures(client)

        rapport = _reprendre(client, _lot_de_reprise(), appliquer=True)
        assert rapport["applique"] is True, rapport["anomalies"]
        assert len(rapport["cles_enregistrees"]) == 2
        assert _combien_d_ecritures(client) == avant + 2

        for cle in rapport["cles_enregistrees"]:
            exercice, journal, numero = cle.split("/")
            lue = client.get(
                f"/comptabilite/dossiers/{DOSSIER}/ecritures/{exercice}/{journal}/{int(numero)}"
            )
            assert lue.status_code == 200, lue.text
            corps = lue.json()
            # ⚠️ **Brouillon.** Une écriture venue d'ailleurs n'a été validée par
            # personne au centre. La faire entrer validée ferait engager la
            # responsabilité du cabinet sur le travail d'un autre.
            assert corps["etat"] == "BROUILLON"
            assert corps["validee_par"] is None
            # ⚠️ **Le numéro du fichier n'est pas devenu le nôtre.** Le fichier
            # porte 501 et 502 ; le registre a numéroté à sa suite.
            assert corps["numero"] not in (501, 502)
            # ⚠️ Et le numéro d'origine survit, sinon le comptable perd le lien
            # avec le classeur de l'adhérent.
            assert "(origine)" in corps["reference_externe"]

    def test_une_anomalie_quelconque_empeche_tout_le_lot_d_entrer(self, plateforme):
        """⚠️ **Règle 1, mesurée là où elle compte : en base.**

        Le lot ci-dessous porte une écriture parfaitement bonne et une qui
        s'appuie sur un compte absent du plan. Aucune des deux n'entre.
        """
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)
        avant = _combien_d_ecritures(client)

        abime = _lot_de_reprise().replace(";602;", ";699;", 1)
        rapport = _reprendre(client, abime, appliquer=True)

        assert rapport["applique"] is False
        assert rapport["cles_enregistrees"] == []
        assert any("699" in a["motif"] for a in rapport["anomalies"]), rapport
        assert _combien_d_ecritures(client) == avant, (
            "la bonne écriture du lot est entrée : le lot n'est plus atomique."
        )

    def test_le_compte_inconnu_designe_sa_ligne_de_fichier_et_non_son_ecriture(self, plateforme):
        """⚠️ **Pourquoi le plan est passé au lecteur, alors que la saisie le contrôle.**

        Le contrôle de saisie refuserait aussi ce compte, et le lot n'entrerait pas
        davantage. Mais il ne connaît que des écritures : il dirait « l'écriture
        OD/501 porte un compte absent du plan », et l'exploitant chercherait parmi
        les quatre lignes de cette écriture — sur un fichier réel, parmi les
        vingt.

        Le lecteur, lui, tient le rang de chaque ligne. Le compte fautif est ici
        posé sur la **seconde** ligne de la première écriture : l'anomalie doit
        désigner la ligne 3 du fichier, et non la ligne 2 où l'écriture commence.

        Sans ce cas, retirer le plan du lecteur ne cassait rien, et la précision
        se serait perdue au premier remaniement.
        """
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)

        # Le compte de contrepartie de la première écriture seulement.
        lot = _lot_de_reprise().replace(";401;Fournisseur", ";499;Fournisseur", 1)
        rapport = _reprendre(client, lot, appliquer=True)

        assert rapport["applique"] is False
        anomalies = [a for a in rapport["anomalies"] if "499" in a["motif"]]
        assert anomalies, rapport["anomalies"]
        assert anomalies[0]["ligne"] == 3, (
            f"l'anomalie désigne la ligne {anomalies[0]['ligne']} : c'est le début "
            "de l'écriture, pas la ligne fautive. Le plan n'atteint plus le lecteur."
        )
        # ⚠️ Et l'extrait porte la ligne elle-même : l'exploitant voit le compte
        # sans rouvrir son fichier.
        assert "499" in anomalies[0]["extrait"]

    def test_la_reprise_passe_par_la_meme_serrure_que_la_saisie(self, plateforme):
        """⚠️ **Ce que le lecteur ne peut pas voir, et que la saisie refuse.**

        Le lecteur de fichier connaît le plan comptable, parce qu'on le lui passe,
        et il refuse donc un compte inconnu avec son rang de ligne. Il ne connaît
        **ni les journaux du cabinet, ni les bornes de l'exercice** : ce sont des
        faits d'environnement, et c'est le contrôle de la saisie qui les porte.

        Sans ce cas, supprimer l'appel au contrôle d'environnement ne cassait rien
        — une mutation l'a montré — et l'import serait devenu une porte dérobée :
        un journal inventé ou une écriture datée d'un exercice déjà déposé à la
        DGI entrerait par le fichier là où le formulaire la refuse.

        *Une porte dérobée n'est pas une porte dérobée parce qu'on la cache :
        c'est une porte dérobée parce qu'elle n'a pas la même serrure.*
        """
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)
        avant = _combien_d_ecritures(client)

        # ⚠️ Un journal que le cabinet n'a pas. Le lecteur l'accepte sans broncher :
        # rien dans un fichier ne dit qu'un journal existe.
        journal_invente = _lot_de_reprise().replace(f";{JOURNAL};", ";ZZ;")
        rapport = _reprendre(client, journal_invente, appliquer=True)
        assert rapport["applique"] is False
        assert any("ZZ" in a["motif"] for a in rapport["anomalies"]), rapport
        assert _combien_d_ecritures(client) == avant

        # ⚠️ **Les deux écritures sont refusées, chacune à sa ligne de fichier.**
        # La première commence ligne 2, la seconde ligne 4. Citer « 1 » et « 2 »,
        # c'est-à-dire le rang de l'écriture dans le lot, enverrait l'exploitant
        # au mauvais endroit de son tableur — et le champ est documenté comme un
        # rang de fichier.
        assert [a["ligne"] for a in rapport["anomalies"]] == [2, 4], rapport["anomalies"]

        # ⚠️ Une date hors de l'exercice repris. Elle est parfaitement lisible, au
        # bon format, et pourtant elle ne peut pas entrer : l'exercice 2026 ne
        # contient pas le 12 mars 2024.
        hors_bornes = _lot_de_reprise().replace("2026-03-12", "2024-03-12")
        rapport = _reprendre(client, hors_bornes, appliquer=True)
        assert rapport["applique"] is False
        assert rapport["anomalies"], rapport
        assert _combien_d_ecritures(client) == avant

    def test_l_adherent_ne_reprend_pas_la_comptabilite_du_cabinet(self, plateforme):
        """La reprise **produit des écritures**, elle exige donc le droit de saisie.

        Un adhérent dépose des justificatifs ; il ne tient pas sa comptabilité.
        Le mode contrôle est concerné au même titre, car le rapport montre le
        contenu comptable du fichier.
        """
        client = plateforme
        ouvrir_une_session(client, ADHERENT)
        for applique in (False, True):
            reponse = _reprendre(client, _lot_de_reprise(), appliquer=applique, brut=True)
            assert reponse.status_code in (403, 404), reponse.text

    def test_un_profil_inconnu_nomme_ceux_qui_existent(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)
        reponse = _reprendre(client, _lot_de_reprise(), profil="sage-ligne-cent", brut=True)
        assert reponse.status_code == 404, reponse.text
        assert "sage-ligne100" in reponse.text

    def test_un_fichier_illisible_repond_409_et_non_200(self, plateforme):
        """Le fichier ne se lit pas **du tout** : ce n'est pas un résultat.

        Un `200` avec des anomalies dit « votre contenu est refusé ligne par
        ligne ». Un fichier en UTF-8 annoncé en cp1252 ne dit pas cela : rien n'a
        pu être lu, et l'exploitant doit corriger son export, pas ses écritures.
        """
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)
        utf8 = _lot_de_reprise().replace("Achat de semences", "Achat de société")
        reponse = _reprendre(client, utf8, profil="sage-ligne100", brut=True, encodage="utf-8")
        assert reponse.status_code == 409, reponse.text


# ═════════════════════════════════════════════════════════════════════════════
# L'attirail des cas HTTP. Il vient après les cas qu'il sert : ce qui compte se
# lit en premier, et Python résout ces noms à l'appel.
# ═════════════════════════════════════════════════════════════════════════════

ADHERENT = "jp.nkoa@batimentplus.cm"
COMPTABLE = "a.bouba@cga-brcg.cm"
DOSSIER = "M081234567890P"
EXERCICE = "2026"

#: ⚠️ Le journal des opérations diverses, et **pas** celui des achats. Les cas de
#: ce fichier laissent des brouillons derrière eux : les poser sur un journal
#: partagé ferait échouer l'export d'un autre fichier, qui refuse les brouillons.
JOURNAL = "OD"


class TestUneRepriseNeDoublePasLExercice:
    """Pas 85 : appliquer deux fois le même fichier doublait l'exercice, sans retour.

    ⚠️ Essai avant correction, sur les données de démonstration : le journal des achats
    est passé de 2 à 4 puis 6 écritures, chaque application répondant « appliqué ». Un
    brouillon ne se supprime pas (Q23) : un double clic suffisait à fausser un exercice
    pour de bon. Et un fichier sans écriture « s'appliquait » sans rien écrire.
    """

    pytestmark = exige_postgresql

    def test_le_meme_fichier_ne_s_applique_pas_deux_fois(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)
        assert _reprendre(client, _lot_de_reprise(), appliquer=True)["applique"] is True
        apres_une = _combien_d_ecritures(client)

        second = _reprendre(client, _lot_de_reprise(), appliquer=True)
        assert second["applique"] is False
        assert second["recevable"] is False
        assert [a["ligne"] for a in second["anomalies"]] == [2, 4]
        assert all("existe déjà" in a["motif"] for a in second["anomalies"])
        assert _combien_d_ecritures(client) == apres_une, (
            "la seconde application a doublé le journal"
        )

    def test_le_controle_le_dit_avant_d_appliquer(self, plateforme):
        """Le doublon doit se voir dans le rapport de contrôle, pas seulement au refus."""
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)
        _reprendre(client, _lot_de_reprise(), appliquer=True)
        controle = _reprendre(client, _lot_de_reprise(), appliquer=False)
        assert controle["recevable"] is False
        assert len(controle["anomalies"]) == 2

    def test_reimporter_l_export_du_dossier_ne_le_double_pas(self, plateforme):
        """L'autre erreur de fichier : reprendre dans un dossier son propre export."""
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)
        _reprendre(client, _lot_de_reprise(), appliquer=True)
        valides_ou_non = _combien_d_ecritures(client)
        # L'export refuse les brouillons : le fichier est fabriqué comme l'export le serait,
        # à partir des écritures telles que le registre les rend.
        ecritures = client.get(
            f"/comptabilite/dossiers/{DOSSIER}/ecritures?exercice={EXERCICE}&journal={JOURNAL}"
        ).json()
        lignes = [
            f"{EXERCICE};{e['journal']};{e['numero']};{e['date_operation']};Libellé reformaté;;"
            f"{e['reference_externe'] or ''};{ligne['compte']};x;"
            f"{ligne['sens'][0]};{ligne['montant']};;"
            for e in ecritures
            for ligne in e["lignes"]
        ]
        rapport = _reprendre(client, "\n".join((EN_TETE_PIVOT, *lignes)) + "\n", appliquer=True)
        assert rapport["applique"] is False
        assert len(rapport["anomalies"]) == len(ecritures)
        assert _combien_d_ecritures(client) == valides_ou_non

    def test_deux_fois_la_meme_ecriture_dans_le_fichier(self, plateforme):
        """Même journal, date, référence et lignes sous deux numéros d'origine.

        ⚠️ Sans référence, deux écritures de numéros d'origine différents ne sont **pas**
        des doublons : le numéro d'origine devient leur référence, et il les distingue.
        C'est voulu, et le cas le montre avec une référence explicite.
        """
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)
        lot = _lot_de_reprise().strip().split("\n")
        avec_reference = [
            ligne.replace("Reprise antérieure;;", "Reprise antérieure;;F-77") for ligne in lot[1:3]
        ]
        copie = [ligne.replace(";501;", ";777;") for ligne in avec_reference]
        rapport = _reprendre(
            client, "\n".join((lot[0], *avec_reference, *copie)) + "\n", appliquer=True
        )
        assert rapport["applique"] is False
        assert [
            a["motif"].endswith("deux fois dans le fichier.") for a in rapport["anomalies"]
        ] == [True]

        sans_reference = [ligne.replace(";501;", ";777;") for ligne in lot[1:3]]
        distinct = _reprendre(
            client, "\n".join((*lot[:3], *sans_reference)) + "\n", appliquer=False
        )
        assert distinct["anomalies"] == [], (
            "deux numéros d'origine distincts ont été pris pour un doublon"
        )

    def test_un_fichier_sans_ecriture_ne_s_applique_pas(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)
        avant = _combien_d_ecritures(client)
        rapport = _reprendre(client, EN_TETE_PIVOT + "\n", appliquer=True)
        assert rapport["applique"] is False
        assert rapport["recevable"] is False
        assert "aucune écriture" in rapport["anomalies"][0]["motif"]
        assert _combien_d_ecritures(client) == avant

    def test_un_fichier_sain_est_dit_recevable(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)
        assert _reprendre(client, _lot_de_reprise(), appliquer=False)["recevable"] is True


def test_l_empreinte_ignore_le_libelle_et_l_ecriture_du_montant():
    """Un autre logiciel reformate les libellés et arrondit l'écriture des montants : le
    doublon doit se reconnaître quand même."""
    from app.contextes.comptabilite.application.reprise import empreinte

    def lignes(montant: str, libelle: str) -> list[LigneEcriture]:
        return [
            LigneEcriture(compte="602", libelle=libelle, sens=Sens.DEBIT, montant=Decimal(montant)),
            LigneEcriture(
                compte="401", libelle=libelle, sens=Sens.CREDIT, montant=Decimal(montant)
            ),
        ]

    jour = date(2026, 3, 12)
    assert empreinte("AC", jour, "F-1", lignes("2840000.00", "Achat")) == empreinte(
        "AC", jour, "F-1", list(reversed(lignes("2840000", "ACHAT SEMENCES")))
    )
    assert empreinte("AC", jour, "F-1", lignes("2840000", "x")) != empreinte(
        "AC", jour, "F-2", lignes("2840000", "x")
    )


#: Les colonnes du profil `pivot-csv` du référentiel, dans son ordre exact. Un
#: fichier qui ne les suivrait pas mesurerait le lecteur d'en-tête, pas la reprise.
EN_TETE_PIVOT = (
    "exercice;journal;numero;date_operation;libelle_ecriture;piece_justificative;"
    "reference_externe;compte;libelle_ligne;sens;montant;tiers;lettrage"
)


def _lot_de_reprise() -> str:
    """Deux écritures équilibrées, numérotées 501 et 502 par l'autre logiciel.

    ⚠️ La colonne `reference_externe` est **laissée vide** à dessein : c'est ainsi
    qu'on éprouve que le numéro d'origine y est reporté. Un fichier qui en
    porterait une masquerait ce report.
    """
    lignes = []
    for numero, compte_charge, montant in ((501, "602", "2840000"), (502, "604", "915000")):
        # ⚠️ La date est en ISO parce que **le profil pivot le déclare ainsi**.
        # L'écrire au format français passerait par le lecteur de dates sans
        # erreur jusqu'au douzième jour du mois, et le cas ne mesurerait plus la
        # reprise mais la tolérance d'un format.
        commun = f"2026;{JOURNAL};{numero};2026-03-12;Reprise antérieure;;"
        lignes.append(f"{commun};{compte_charge};Charge;D;{montant}.00;;")
        lignes.append(f"{commun};401;Fournisseur;C;{montant}.00;M034455667788W;")
    return "\n".join((EN_TETE_PIVOT, *lignes)) + "\n"


def _reprendre(
    client,
    contenu: str,
    *,
    appliquer: bool = False,
    profil: str = "pivot-csv",
    brut: bool = False,
    encodage: str = "utf-8",
):
    reponse = client.post(
        f"/comptabilite/dossiers/{DOSSIER}/reprise"
        f"?exercice={EXERCICE}&profil={profil}&appliquer={str(appliquer).lower()}",
        files={"fichier": ("reprise.csv", contenu.encode(encodage), "text/csv")},
    )
    if brut:
        return reponse
    assert reponse.status_code == 200, reponse.text
    return reponse.json()


def _combien_d_ecritures(client) -> int:
    reponse = client.get(
        f"/comptabilite/dossiers/{DOSSIER}/ecritures?exercice={EXERCICE}&journal={JOURNAL}"
    )
    assert reponse.status_code == 200, reponse.text
    return len(reponse.json())
