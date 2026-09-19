"""Le chiffrage : un intervalle, jamais un prix.

Cinquième domaine à tourner sur `app/moteur/`, et rien ne lui a été ajouté.

Quatre familles de tests, et la première est celle qui compte :

* **la commutativité.** Un tarif qui change parce qu'un fiscaliste a renommé un
  fichier est indéfendable. Les ajustements en proportion portent tous sur la
  base, et le test le vérifie en mélangeant les règles ;
* **la séparation honoraires / débours**, parce que négocier ne peut pas porter
  sur l'argent d'un tiers ;
* **la traçabilité**, parce qu'une proposition sans version de barème est un
  montant qu'on ne saura pas réexpliquer ;
* **la datation**, parce qu'un devis se lit au barème de son jour.
"""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contextes.referentiel.contrats import Fondement, StatutValidation
from app.contextes.souscription.adaptateurs.sortant.grille_tarifaire import (
    charger_les_baremes,
    charger_les_regles_de_tarification,
    valider_contre,
)
from app.contextes.souscription.adaptateurs.sortant.questionnaires import (
    RepertoireDeQuestionnaires,
)
from app.contextes.souscription.domaine.tarification import (
    UNITE,
    UNITE_TAUX,
    VALORISER_L_AJUSTEMENT,
    Bareme,
    BaremeIntrouvable,
    Debours,
    Proposition,
    RegleDeTarification,
    bareme_pour,
    chiffrer,
    schema_de_tarification,
)
from app.infrastructure.config import RACINE_DEPOT
from app.moteur.faits import ErreurSchema

LE_JOUR = date(2026, 9, 10)
REFERENTIEL = RACINE_DEPOT / "Docs" / "referentiel"
FONDEMENT = Fondement(texte="Barème d'essai", source="tests")


def _bareme(**surcharges) -> Bareme:
    defauts = {
        "service": "essai",
        "version": "1",
        "base": Decimal("100000"),
        "applicable_du": date(2026, 1, 1),
        "baisse_maximale": Decimal("0.20"),
        "hausse_maximale": Decimal("0.50"),
        "fondement": FONDEMENT,
    }
    return Bareme(**{**defauts, **surcharges})


def _regle(code: str, predicat: dict, **surcharges) -> RegleDeTarification:
    defauts = {
        "code": code,
        "libelle": f"Critère {code}",
        "applicable_du": date(2026, 1, 1),
        "predicat": predicat,
        "montant": Decimal("10000"),
        "fondement": FONDEMENT,
    }
    return RegleDeTarification(**{**defauts, **surcharges})


#: Un prédicat toujours faux : le critère est donc toujours atteint.
TOUJOURS_ATTEINT = {"==": [1, 2]}


# ── La commutativité ──────────────────────────────────────────────────────────


class TestCommutativite:
    """⚠️ **La décision la moins visible du module et la plus importante.**

    Un taux appliqué au cumul rendrait le résultat dépendant de l'ordre des
    règles, c'est-à-dire de l'alphabet des noms de fichiers.
    """

    def test_l_ordre_des_regles_ne_change_pas_le_prix(self):
        regles = [
            _regle("A", TOUJOURS_ATTEINT, montant=None, taux=Decimal("0.10")),
            _regle("B", TOUJOURS_ATTEINT, montant=Decimal("50000")),
            _regle("C", TOUJOURS_ATTEINT, montant=None, taux=Decimal("0.25")),
            _regle("D", TOUJOURS_ATTEINT, montant=Decimal("-15000")),
        ]
        attendu = None
        for graine in range(12):
            melangees = list(regles)
            random.Random(graine).shuffle(melangees)
            p = chiffrer({}, bareme=_bareme(), regles=melangees, a_la_date=LE_JOUR)
            if attendu is None:
                attendu = p.reference
            assert p.reference == attendu, "l'ordre des règles a changé le prix"

    def test_un_taux_porte_sur_la_base_et_non_sur_le_cumul(self):
        """Dix pour cent de cent mille valent dix mille, que la règle soit
        évaluée avant ou après un ajustement de cinquante mille."""
        p = chiffrer(
            {},
            bareme=_bareme(base=Decimal("100000")),
            regles=[
                _regle("FIXE", TOUJOURS_ATTEINT, montant=Decimal("50000")),
                _regle("TAUX", TOUJOURS_ATTEINT, montant=None, taux=Decimal("0.10")),
            ],
            a_la_date=LE_JOUR,
        )
        montants = {ligne.code: ligne.montant for ligne in p.lignes}
        assert montants["TAUX"] == Decimal("10000")
        assert p.reference == Decimal("160000")

    def test_la_nature_de_l_ajustement_est_declaree_par_l_unite(self):
        """⚠️ Et non devinée par la magnitude. La première version supposait
        qu'une valeur entre moins un et un était un taux : cela tenait jusqu'à la
        première remise de cinquante centimes. Une heuristique dans un moteur de
        prix est un défaut en attente."""
        from app.moteur.consequence import Consequence, TypeConsequence

        faits = {"base": Decimal("150000")}
        centimes = Consequence(
            type=TypeConsequence.AJUSTEMENT, libelle="x", unite=UNITE,
            valeur_numerique=Decimal("0.5"),
        )
        quinze_pour_cent = Consequence(
            type=TypeConsequence.AJUSTEMENT, libelle="x", unite=UNITE_TAUX,
            valeur_numerique=Decimal("0.15"),
        )
        assert VALORISER_L_AJUSTEMENT(centimes, faits) == Decimal("0.5")
        assert VALORISER_L_AJUSTEMENT(quinze_pour_cent, faits) == Decimal("22500")


# ── La règle ──────────────────────────────────────────────────────────────────


class TestRegleDeTarification:
    def test_un_ajustement_est_soit_un_montant_soit_un_taux(self):
        """Les deux à la fois rendraient le total dépendant de l'ordre dans
        lequel on les applique ; aucun des deux ne fait rien."""
        with pytest.raises(ValidationError, match="soit"):
            _regle("X", TOUJOURS_ATTEINT, montant=Decimal("1"), taux=Decimal("0.1"))
        with pytest.raises(ValidationError, match="soit"):
            _regle("X", TOUJOURS_ATTEINT, montant=None, taux=None)

    def test_une_remise_est_un_ajustement_comme_un_autre(self):
        """L'écrire ainsi évite un second mécanisme, et un second endroit où se
        tromper de signe."""
        p = chiffrer(
            {}, bareme=_bareme(base=Decimal("100000")),
            regles=[_regle("REMISE", TOUJOURS_ATTEINT, montant=Decimal("-30000"))],
            a_la_date=LE_JOUR,
        )
        assert p.reference == Decimal("70000")

    def test_une_regle_valide_sans_signataire_est_refusee(self):
        with pytest.raises(ValidationError, match="valide_par"):
            _regle("X", TOUJOURS_ATTEINT, statut=StatutValidation.VALIDE)

    def test_une_regle_hors_vigueur_ne_s_applique_pas(self):
        """Un devis se chiffre aux règles de son jour."""
        ancienne = _regle(
            "X", TOUJOURS_ATTEINT,
            applicable_du=date(2025, 1, 1), applicable_au=date(2026, 1, 1),
        )
        p = chiffrer({}, bareme=_bareme(), regles=[ancienne], a_la_date=LE_JOUR)
        assert p.lignes == ()
        assert p.reference == p.base

    def test_une_regle_cassee_est_signalee_et_non_tue(self):
        """Un critère silencieusement absent est indiscernable d'un critère non
        atteint, et le prix serait faux sans que rien ne le dise."""
        p = chiffrer(
            {}, bareme=_bareme(),
            regles=[_regle("CASSEE", {"operateur-inexistant": [1, 2]})],
            a_la_date=LE_JOUR,
        )
        assert p.echecs
        assert "CASSEE" in p.echecs[0]


# ── L'intervalle ──────────────────────────────────────────────────────────────


class TestIntervalle:
    def test_les_trois_valeurs_encadrent_la_reference(self):
        p = chiffrer({}, bareme=_bareme(base=Decimal("200000")), regles=[], a_la_date=LE_JOUR)
        assert p.reference == Decimal("200000")
        assert p.plancher == Decimal("160000")
        assert p.plafond == Decimal("300000")

    def test_l_amplitude_vient_du_bareme_et_non_du_code(self):
        """Une amplitude en dur serait la valeur commerciale codée dans le
        moteur que ce projet cherche à éviter."""
        serre = _bareme(baisse_maximale=Decimal("0.05"), hausse_maximale=Decimal("0.10"))
        p = chiffrer({}, bareme=serre, regles=[], a_la_date=LE_JOUR)
        assert p.plancher == Decimal("95000")
        assert p.plafond == Decimal("110000")

    def test_un_intervalle_incoherent_est_refuse_a_la_construction(self):
        """Un plancher au dessus de la référence ferait refuser le montant que le
        système recommande lui-même."""
        with pytest.raises(ValidationError, match="intervalle incohérent"):
            Proposition(
                service="essai", version_bareme="1", a_la_date=LE_JOUR,
                base=Decimal(100), plancher=Decimal(200),
                reference=Decimal(100), plafond=Decimal(300),
            )

    def test_le_montant_dans_l_intervalle_passe_sans_motif(self):
        p = chiffrer({}, bareme=_bareme(), regles=[], a_la_date=LE_JOUR)
        assert p.dans_l_intervalle(Decimal("90000")) is True
        assert p.dans_l_intervalle(Decimal("79999")) is False
        assert p.dans_l_intervalle(Decimal("150001")) is False


# ── Les débours ───────────────────────────────────────────────────────────────


class TestUnFaitSansReponse:
    """⚠️ **Une information inconnue ne vaut ni remise ni majoration.** (pas 66)

    Une question facultative sans réponse est absente des faits, et le moteur la lit
    `None`. Avec la convention « faux = déclenchement », `statuts_apportes == false`
    rendait faux sur une question sans réponse : la remise des statuts s'appliquait à
    qui n'avait rien dit.
    """

    REMISE = {"==": [{"var": "statuts_apportes"}, False]}

    def _chiffrer(self, faits, regles):
        return chiffrer(faits, bareme=_bareme(), regles=regles, a_la_date=LE_JOUR)

    def test_sans_reponse_l_ajustement_n_est_pas_applique_et_c_est_dit(self):
        remise = _regle("TAR-STA", self.REMISE, montant=Decimal("-50000"))
        proposition = self._chiffrer({}, [remise])
        assert proposition.lignes == ()
        assert proposition.reference == Decimal("100000")
        assert any("TAR-STA" in e and "statuts_apportes" in e for e in proposition.echecs), (
            proposition.echecs
        )

    def test_avec_la_reponse_la_convention_joue_dans_les_deux_sens(self):
        """La contre-épreuve : la règle n'a pas été éteinte."""
        regle = [_regle("TAR-STA", self.REMISE, montant=Decimal("-50000"))]
        apportes = self._chiffrer({"statuts_apportes": True}, regle)
        assert [ligne.code for ligne in apportes.lignes] == ["TAR-STA"]
        non_apportes = self._chiffrer({"statuts_apportes": False}, regle)
        assert non_apportes.lignes == ()
        assert not non_apportes.echecs

    def test_une_regle_qui_traite_l_absence_reste_evaluee(self):
        """`missing` dit que l'auteur a pensé au cas : on ne l'écarte pas à sa place."""
        prudente = _regle("TAR-X", {"!": [{"missing": ["pages_statuts"]}]}, montant=Decimal("5000"))
        proposition = self._chiffrer({}, [prudente])
        assert [ligne.code for ligne in proposition.lignes] == ["TAR-X"]
        assert not proposition.echecs


class TestDebours:
    """**Négocier ne peut pas porter sur l'argent d'un tiers.**"""

    def _avec_debours(self):
        return chiffrer(
            {}, bareme=_bareme(base=Decimal("100000")), regles=[], a_la_date=LE_JOUR,
            debours=[
                Debours(code="ENR", libelle="Droits d'enregistrement", montant=Decimal("120000")),
                Debours(code="GRE", libelle="Frais de greffe", montant=Decimal("30000")),
            ],
        )

    def test_ils_n_entrent_pas_dans_l_intervalle(self):
        """Les inclure ferait croire qu'un rabais peut porter sur des droits
        d'enregistrement."""
        p = self._avec_debours()
        assert p.reference == Decimal("100000")
        assert p.plancher == Decimal("80000")
        assert p.plafond == Decimal("150000")

    def test_ils_s_ajoutent_au_total_a_leur_montant(self):
        p = self._avec_debours()
        assert p.total_des_debours == Decimal("150000")
        assert p.total_de_reference == Decimal("250000")

    def test_aucune_regle_ne_les_ajuste(self):
        """Un taux de vingt pour cent porte sur la base, pas sur les débours."""
        p = chiffrer(
            {}, bareme=_bareme(base=Decimal("100000")),
            regles=[_regle("T", TOUJOURS_ATTEINT, montant=None, taux=Decimal("0.20"))],
            a_la_date=LE_JOUR,
            debours=[Debours(code="ENR", libelle="Enregistrement", montant=Decimal("500000"))],
        )
        assert p.lignes[0].montant == Decimal("20000")

    def test_negocier_ne_porte_que_sur_les_honoraires(self):
        p = self._avec_debours()
        assert p.dans_l_intervalle(Decimal("85000")) is True
        assert p.dans_l_intervalle(p.total_de_reference) is False


# ── Le barème ─────────────────────────────────────────────────────────────────


class TestBareme:
    def test_il_se_lit_a_une_date_et_non_au_present(self):
        """Un devis reçu le 20 mars et ouvert le 2 avril doit afficher le barème
        de mars, sinon le client voit un autre chiffre que celui annoncé."""
        ancien = _bareme(
            version="2025", base=Decimal("80000"),
            applicable_du=date(2025, 1, 1), applicable_au=date(2026, 1, 1),
        )
        nouveau = _bareme(version="2026", base=Decimal("100000"))
        baremes = [ancien, nouveau]

        assert bareme_pour(baremes, "essai", date(2025, 6, 1)).version == "2025"
        assert bareme_pour(baremes, "essai", LE_JOUR).version == "2026"

    def test_un_service_sans_bareme_leve(self):
        with pytest.raises(BaremeIntrouvable, match="Services tarifés"):
            bareme_pour([_bareme()], "jamais-vendu", LE_JOUR)

    def test_un_bareme_valide_sans_signataire_est_refuse(self):
        """Sans signataire nommé, le prix n'engage personne."""
        with pytest.raises(ValidationError, match="valide_par"):
            _bareme(statut=StatutValidation.VALIDE)

    def test_la_version_est_recopiee_dans_la_proposition(self):
        """Le piège nommé par le document : sans elle, personne ne peut
        réexpliquer le montant six mois plus tard."""
        p = chiffrer({}, bareme=_bareme(version="2026.3"), regles=[], a_la_date=LE_JOUR)
        assert p.version_bareme == "2026.3"

    def test_une_proposition_sans_version_est_refusee(self):
        with pytest.raises(ValidationError):
            Proposition(
                service="essai", version_bareme="", a_la_date=LE_JOUR,
                base=Decimal(1), plancher=Decimal(1),
                reference=Decimal(1), plafond=Decimal(1),
            )


# ── Le pont avec le questionnaire ─────────────────────────────────────────────


class TestSchemaCompose:
    @pytest.fixture(scope="class")
    def questionnaire(self):
        return RepertoireDeQuestionnaires.depuis(REFERENTIEL / "qualification").prendre(
            "creation-sarl"
        )

    def test_il_reprend_les_faits_du_questionnaire(self, questionnaire):
        """Ajouter une question ouvre un fait de plus aux règles de prix, sans
        déploiement. C'est voulu."""
        schema = schema_de_tarification(questionnaire)
        assert questionnaire.schema().codes <= schema.codes

    def test_il_ajoute_les_trois_faits_du_chiffrage(self, questionnaire):
        codes = schema_de_tarification(questionnaire).codes
        assert {"base", "score_charge", "service"} <= codes

    def test_une_regle_qui_cite_un_fait_absent_est_refusee(self, questionnaire):
        """⚠️ Le défaut le plus coûteux du lot : il ne casse rien, il fait perdre
        de l'argent à chaque devis. Un critère qui lit un fait inexistant rend
        toujours faux, donc l'ajustement s'applique sur **tous** les dossiers."""
        fautive = _regle("X", {">": [{"var": "associe"}, 3]})
        with pytest.raises(ErreurSchema, match="associe"):
            valider_contre([fautive], questionnaire)


# ── Le référentiel réel ───────────────────────────────────────────────────────


class TestReferentielReel:
    @pytest.fixture(scope="class")
    def grille(self):
        dossier = REFERENTIEL / "tarification"
        return charger_les_baremes(dossier), charger_les_regles_de_tarification(dossier)

    @pytest.fixture(scope="class")
    def questionnaires(self):
        return RepertoireDeQuestionnaires.depuis(REFERENTIEL / "qualification")

    def test_il_se_charge(self, grille):
        baremes, regles = grille
        assert {b.service for b in baremes} == {"creation-sarl", "tenue-comptable"}
        assert len(regles) >= 7

    def test_chaque_bareme_et_chaque_regle_porte_un_fondement(self, grille):
        baremes, regles = grille
        for element in (*baremes, *regles):
            assert element.fondement.texte.strip()
            assert element.fondement.source.strip()

    def test_aucun_bareme_n_est_encore_valide(self, grille):
        """Le barème réel du centre n'a pas été transmis. Les déclarer validés
        ferait passer pour arrêté un tarif que personne n'a signé."""
        baremes, _ = grille
        assert all(b.statut is StatutValidation.A_VALIDER for b in baremes)

    def test_les_regles_de_creation_citent_des_faits_du_questionnaire(
        self, grille, questionnaires
    ):
        """Le contrôle qui empêche un ajustement de s'appliquer à tous les
        dossiers pour une faute de frappe."""
        _, regles = grille
        creation = questionnaires.prendre("creation-sarl")
        concernees = [
            r for r in regles
            if r.code.startswith(("TAR-ASS", "TAR-CAP", "TAR-NAT", "TAR-REG", "TAR-STA", "TAR-CHG"))
        ]
        valider_contre(concernees, creation)

    def test_un_chiffrage_complet_tient_debout(self, grille):
        baremes, regles = grille
        p = chiffrer(
            {
                "associes": 5,
                "capital_social": Decimal("8000000"),
                "apports_en_nature": True,
                "region_siege": "OUEST",
                "statuts_apportes": True,
            },
            bareme=bareme_pour(baremes, "creation-sarl", LE_JOUR),
            regles=regles,
            a_la_date=LE_JOUR,
            score_charge=59,
        )
        assert p.plancher < p.reference < p.plafond
        assert p.version_bareme == "2026.1"
        assert {ligne.code for ligne in p.lignes} >= {"TAR-ASS-001", "TAR-CHG-001"}
        assert all(ligne.fondement for ligne in p.lignes)

    def test_un_dossier_ordinaire_reste_au_barème(self, grille):
        """Aucun critère atteint : le prix est celui du barème, sans ajustement.
        C'est le cas le plus fréquent, et il doit être le plus simple."""
        baremes, regles = grille
        p = chiffrer(
            {
                "associes": 2,
                "capital_social": Decimal("1000000"),
                "apports_en_nature": False,
                "region_siege": "LITTORAL",
                "statuts_apportes": False,
            },
            bareme=bareme_pour(baremes, "creation-sarl", LE_JOUR),
            regles=regles, a_la_date=LE_JOUR, score_charge=20,
        )
        assert p.lignes == ()
        assert p.reference == p.base
