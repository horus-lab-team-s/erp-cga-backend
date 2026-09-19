"""Le rapprochement bancaire (pas 101).

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE, DANS L'ORDRE OÙ L'ERREUR COÛTE LE PLUS CHER

1. **Le signe.** Le relevé est tenu du point de vue de la banque ; tout le reste, du point
   de vue de l'entreprise. Une inversion ferait rapprocher un encaissement d'un règlement.
2. **Le relevé qui ne tombe pas juste** est refusé à l'import : sinon l'écart serait
   cherché dans la comptabilité alors qu'il est dans le fichier.
3. **Aucun écart de montant absorbé**, et jamais deux lignes du relevé sur une écriture.
4. **L'automatique prudent** : une seule correspondance forte, ou rien.
5. **L'état de rapprochement** : sa formule, et la validation qui refuse un écart, une
   ligne inexpliquée, un brouillon ou une écriture modifiée depuis.
6. **Les accès** : lire, préparer, arrêter ; et la pièce demandée qui arrive à qui relance.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import base64
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contextes.comptabilite.adaptateurs.sortant.depots_rapprochements import (
    DepotRapprochementsMemoire,
    vider_les_rapprochements,
)
from app.contextes.comptabilite.adaptateurs.sortant.plan_syscohada import JOURNAUX_CABINET
from app.contextes.comptabilite.adaptateurs.sortant.rapprochement_yaml import (
    charger_les_profils_de_releve,
    charger_les_reglages_du_rapprochement,
)
from app.contextes.comptabilite.api import vider_les_ecritures_en_memoire
from app.contextes.comptabilite.application.rapprochement import (
    abandonner_un_rapprochement,
    importer_un_releve,
    rapproches_par_les_autres,
)
from app.contextes.comptabilite.domaine.entites import Sens
from app.contextes.comptabilite.domaine.rapprochement import (
    ForceDeCorrespondance,
    LigneDeReleve,
    ModeAppariement,
    MouvementComptable,
    NatureJustification,
    ProfilDeReleve,
    RapprochementBancaire,
    RapprochementRefuse,
    ReglagesDuRapprochement,
    ReleveIllisible,
    StatutRapprochement,
    etat_du_rapprochement,
    lire_le_releve,
    proposer_des_correspondances,
)
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.infrastructure.config import configuration
from app.main import creer_application
from app.partage.locataire import etabli
from tests.conftest import exige_postgresql, ouvrir_une_session

BATIMENT = "M081234567890P"
COMPTABLE = "l.fotso@cga-brcg.cm"
COMPTABLE_AUTRE = "c.ndongo@cga-brcg.cm"
CHARGEE_CLIENTELE = "p.moukouri@cga-brcg.cm"
ADHERENT = "jp.nkoa@batimentplus.cm"
INSTANT = datetime(2026, 8, 5, 9, 0)
REGLAGES = ReglagesDuRapprochement()
BQ = next(j for j in JOURNAUX_CABINET if j.code == "BQ")
CA = next(j for j in JOURNAUX_CABINET if j.code == "CA")

PROFIL_DC = ProfilDeReleve(
    code="essai-dc",
    libelle="essai",
    colonne_date=1,
    colonne_libelle=2,
    colonne_debit_banque=3,
    colonne_credit_banque=4,
)
PROFIL_SIGNE = ProfilDeReleve(
    code="essai-signe",
    libelle="essai",
    colonne_date=1,
    colonne_libelle=2,
    colonne_montant=3,
)


@pytest.fixture(autouse=True)
def neuf():
    # ⚠️ Les écritures en mémoire aussi : sans quoi une écriture saisie par un test serait
    # proposée, voire rapprochée automatiquement, dans le suivant.
    reinitialiser_atelier()
    vider_les_rapprochements()
    vider_les_ecritures_en_memoire()
    yield
    vider_les_rapprochements()
    vider_les_ecritures_en_memoire()


@pytest.fixture
def cabinet():
    with etabli("CGA-BRCG"):
        yield


# ── La lecture du relevé ──────────────────────────────────────────────────────


class TestLaLectureDuReleve:
    def test_le_credit_de_la_banque_est_un_encaissement_de_l_entreprise(self):
        contenu = (
            "Date;Libellé;Débit;Crédit\n"
            "03/07/2026;VIR CLIENT KRIBI;;6 400 000\n"
            "18/07/2026;VIR QUINCAILLERIE FACT 0412;2 350 000,00;\n"
        ).encode()
        lignes = lire_le_releve(contenu, PROFIL_DC)
        assert [(l_.rang, l_.sens, l_.montant) for l_ in lignes] == [
            (1, Sens.DEBIT, Decimal(6400000)),
            (2, Sens.CREDIT, Decimal(2350000)),
        ]
        assert [l_.signe for l_ in lignes] == [Decimal(6400000), Decimal(-2350000)]

    def test_un_montant_signe_et_les_parentheses(self):
        contenu = "Date;Libellé;Montant\n01/07/2026;DEPOT;150 000\n02/07/2026;RETRAIT;(18 500)\n"
        lignes = lire_le_releve(contenu.encode(), PROFIL_SIGNE)
        assert [l_.signe for l_ in lignes] == [Decimal(150000), Decimal(-18500)]
        inverse = PROFIL_SIGNE.model_copy(update={"montant_positif_encaisse": False})
        assert [l_.signe for l_ in lire_le_releve(contenu.encode(), inverse)] == [
            Decimal(-150000),
            Decimal(18500),
        ]

    @pytest.mark.parametrize(
        ("ligne", "message"),
        [
            ("32/07/2026;X;;100", "ligne 2 : date « 32/07/2026 » illisible"),
            ("03/07/2026;X", r"ligne 2 : la colonne 3 \(débit\) manque"),
            ("03/07/2026;X;100;100", "ligne 2 : il faut un montant au débit"),
            ("03/07/2026;X;;", "ligne 2 : il faut un montant au débit"),
            ("03/07/2026;X;;100,50", "avec centimes"),
            ("03/07/2026;X;;abc", "ligne 2 : montant illisible"),
        ],
    )
    def test_une_ligne_illisible_fait_echouer_tout_l_import_en_la_nommant(self, ligne, message):
        with pytest.raises(ReleveIllisible, match=message):
            lire_le_releve(f"Date;Libellé;Débit;Crédit\n{ligne}\n".encode(), PROFIL_DC)

    def test_un_fichier_vide_ou_mal_encode_est_refuse(self):
        with pytest.raises(ReleveIllisible, match="aucune opération"):
            lire_le_releve(b"Date;Libelle;Debit;Credit\n\n", PROFIL_DC)
        with pytest.raises(ReleveIllisible, match="encodé"):
            lire_le_releve("Date;Libellé\n".encode("utf-16"), PROFIL_DC)

    def test_un_profil_declare_exactement_une_forme_de_montant(self):
        base = {"code": "x-y", "libelle": "abc", "colonne_date": 1, "colonne_libelle": 2}
        for mauvais in (
            {},
            {"colonne_montant": 3, "colonne_debit_banque": 4, "colonne_credit_banque": 5},
            {"colonne_debit_banque": 3},
        ):
            with pytest.raises(ValidationError, match="déclarer soit"):
                ProfilDeReleve.model_validate({**base, **mauvais})

    def test_le_referentiel_livre_ses_profils_et_ses_reglages(self, tmp_path: Path):
        referentiel = configuration().dossier_referentiel
        assert set(charger_les_profils_de_releve(referentiel)) == {
            "csv-debit-credit",
            "csv-montant-signe",
        }
        assert charger_les_reglages_du_rapprochement(referentiel).source.endswith("reglages.yaml")
        assert charger_les_profils_de_releve(tmp_path) == {}
        assert charger_les_reglages_du_rapprochement(tmp_path).fenetre_jours == 15
        with pytest.raises(ValidationError, match="dépasse"):
            ReglagesDuRapprochement(fenetre_jours=3, fenetre_proche_jours=5)


# ── Les propositions ──────────────────────────────────────────────────────────


def _mouvement(numero, jour, montant, sens=Sens.CREDIT, reference=None, validee=True, ligne=0):
    return MouvementComptable(
        ecriture=f"2026/BQ/{numero:06d}",
        ligne=ligne,
        date=date(2026, 7, jour),
        libelle=f"mouvement {numero}",
        montant=Decimal(montant),
        sens=sens,
        reference=reference,
        validee=validee,
    )


def _ligne(rang, jour, montant, sens=Sens.CREDIT, libelle="VIREMENT"):
    return LigneDeReleve(
        rang=rang, date=date(2026, 7, jour), libelle=libelle, montant=Decimal(montant), sens=sens
    )


class TestLesPropositions:
    def test_le_montant_et_le_sens_sont_une_condition(self):
        ligne = _ligne(1, 18, 2350000)
        mouvements = [
            _mouvement(1, 18, 2349000),
            _mouvement(2, 18, 2350000, sens=Sens.DEBIT),
            _mouvement(3, 1, 2350000),  # 17 jours : hors de la fenêtre de 15
            _mouvement(4, 10, 2350000),
        ]
        propositions = proposer_des_correspondances(ligne, mouvements, set(), REGLAGES)
        assert [p.mouvement.ecriture for p in propositions] == ["2026/BQ/000004"]
        assert propositions[0].motifs == ["montant exact", "date éloignée (8 j)"]

    def test_la_reference_se_retrouve_malgre_la_ponctuation_de_la_banque(self):
        ligne = _ligne(1, 18, 2350000, libelle="VIR QUINCAILLERIE WOURI FACT 0412")
        mouvements = [
            _mouvement(1, 28, 2350000, reference="F-2026-0409"),
            _mouvement(2, 28, 2350000, reference="F-2026-0412"),
        ]
        propositions = proposer_des_correspondances(ligne, mouvements, set(), REGLAGES)
        assert propositions[0].mouvement.reference == "F-2026-0412"
        assert propositions[0].force is ForceDeCorrespondance.FORTE
        assert "référence F-2026-0412 présente dans le libellé" in propositions[0].motifs
        assert propositions[1].force is ForceDeCorrespondance.POSSIBLE

    def test_un_bloc_de_moins_de_trois_chiffres_ne_prouve_rien(self):
        ligne = _ligne(1, 18, 100, libelle="VIR 12 JUILLET")
        [proposition] = proposer_des_correspondances(
            ligne, [_mouvement(1, 28, 100, reference="F-12")], set(), REGLAGES
        )
        assert proposition.force is ForceDeCorrespondance.POSSIBLE

    def test_seul_et_proche_il_est_fort_a_deux_il_ne_l_est_plus(self):
        ligne = _ligne(1, 18, 500)
        seul = proposer_des_correspondances(ligne, [_mouvement(1, 20, 500)], set(), REGLAGES)
        assert seul[0].force is ForceDeCorrespondance.FORTE
        deux = proposer_des_correspondances(
            ligne, [_mouvement(1, 20, 500), _mouvement(2, 17, 500)], set(), REGLAGES
        )
        assert {p.force for p in deux} == {ForceDeCorrespondance.POSSIBLE}
        assert [p.ecart_jours for p in deux] == [1, 2]

    def test_un_mouvement_deja_rapproche_est_a_ecarter_et_ne_rend_pas_l_autre_douteux(self):
        ligne = _ligne(1, 18, 500)
        pris = _mouvement(1, 18, 500)
        libre = _mouvement(2, 19, 500, validee=False)
        propositions = proposer_des_correspondances(
            ligne, [pris, libre], {pris.designation}, REGLAGES
        )
        assert [(p.mouvement.ecriture, p.force) for p in propositions] == [
            ("2026/BQ/000002", ForceDeCorrespondance.FORTE),
            ("2026/BQ/000001", ForceDeCorrespondance.A_ECARTER),
        ]
        assert "écriture encore en brouillon" in propositions[0].motifs
        assert "déjà rapprochée d'une autre ligne" in propositions[1].motifs


# ── Le rapprochement et son état ──────────────────────────────────────────────


def _rapprochement(lignes, solde_initial=0, **autres) -> RapprochementBancaire:
    total = sum((l_.signe for l_ in lignes), Decimal(0))
    return RapprochementBancaire(
        identifiant="RB-BQ-20260731-1",
        dossier=BATIMENT,
        journal="BQ",
        compte="521",
        exercice="2026",
        du=date(2026, 7, 1),
        au=date(2026, 7, 31),
        solde_initial=Decimal(solde_initial),
        solde_final=Decimal(solde_initial) + total,
        lignes=lignes,
        source="saisie",
        importe_par="C-004",
        importe_le=INSTANT,
        **autres,
    )


def _apparier(r, rang, mouvement, deja=None):
    return r.apparier(
        rang,
        mouvement,
        mode=ModeAppariement.MANUEL,
        motifs=[],
        par="C-004",
        le=INSTANT,
        deja_rapproches=deja if deja is not None else r.designations,
    )


class TestLeRapprochement:
    def test_un_releve_qui_ne_tombe_pas_juste_est_refuse(self):
        with pytest.raises(ValidationError, match="ne tombe pas juste"):
            RapprochementBancaire(
                **{
                    **_rapprochement([_ligne(1, 3, 100)]).model_dump(),
                    "solde_final": Decimal(99),
                }
            )

    def test_une_ligne_hors_periode_est_refusee(self):
        ligne = LigneDeReleve(
            rang=1, date=date(2026, 8, 1), libelle="X", montant=Decimal(1), sens=Sens.DEBIT
        )
        with pytest.raises(ValidationError, match="hors de la période"):
            _rapprochement([ligne])

    def test_un_ecart_de_montant_n_est_jamais_absorbe(self):
        r = _rapprochement([_ligne(1, 18, 2350000)])
        with pytest.raises(RapprochementRefuse, match="n'absorbe pas d'écart"):
            _apparier(r, 1, _mouvement(1, 18, 2349000))
        with pytest.raises(RapprochementRefuse, match="n'absorbe pas d'écart"):
            _apparier(r, 1, _mouvement(1, 18, 2350000, sens=Sens.DEBIT))

    def test_jamais_deux_lignes_sur_la_meme_ecriture_ni_deux_ecritures_sur_une_ligne(self):
        r = _rapprochement([_ligne(1, 18, 500), _ligne(2, 18, 500)])
        m = _mouvement(1, 18, 500)
        r = _apparier(r, 1, m)
        with pytest.raises(RapprochementRefuse, match="déjà rapprochée d'une autre ligne"):
            _apparier(r, 2, m)
        with pytest.raises(RapprochementRefuse, match="déjà rapprochée : la dissocier"):
            _apparier(r, 1, _mouvement(2, 18, 500))
        assert _apparier(r.dissocier(1), 2, m).appariement(2) is not None

    def test_rapprocher_efface_la_justification_et_justifier_une_ligne_rapprochee_est_refuse(self):
        r = _rapprochement([_ligne(1, 18, 500)]).justifier(
            1,
            nature=NatureJustification.ECRITURE_A_VENIR,
            motif="Frais du mois, saisis en août.",
            par="C-004",
            le=INSTANT,
        )
        r = _apparier(r, 1, _mouvement(1, 18, 500))
        assert r.justification(1) is None
        with pytest.raises(RapprochementRefuse, match="rien à justifier"):
            r.justifier(
                1,
                nature=NatureJustification.ERREUR_BANCAIRE,
                motif="Erreur de la banque.",
                par="C-004",
                le=INSTANT,
            )

    def test_l_etat_de_rapprochement_explique_les_deux_cotes(self):
        """Relevé : +6 400 000, −2 350 000, −18 500 de frais non saisis.
        Comptabilité : les deux premiers, plus un chèque de −300 000 pas encore encaissé,
        et un à-nouveau de +1 000 000 d'avant la période."""
        frais = _ligne(3, 21, 18500)
        r = _rapprochement(
            [_ligne(1, 3, 6400000, sens=Sens.DEBIT), _ligne(2, 18, 2350000), frais],
            solde_initial=1000000,
        )
        encaissement = _mouvement(2, 3, 6400000, sens=Sens.DEBIT)
        reglement = _mouvement(3, 17, 2350000)
        cheque = _mouvement(4, 29, 300000)
        a_nouveau = MouvementComptable(
            ecriture="2026/AN/000001",
            ligne=0,
            date=date(2026, 1, 1),
            libelle="AN",
            montant=Decimal(1000000),
            sens=Sens.DEBIT,
            validee=True,
        )
        # Un règlement d'août : postérieur au relevé, il n'entre pas dans le solde de juillet.
        aout = MouvementComptable(
            ecriture="2026/BQ/000050",
            ligne=0,
            date=date(2026, 8, 2),
            libelle="août",
            montant=Decimal(777),
            sens=Sens.CREDIT,
            validee=True,
        )
        mouvements = [a_nouveau, encaissement, reglement, cheque, aout]
        r = _apparier(_apparier(r, 1, encaissement), 2, reglement)
        etat = etat_du_rapprochement(r, mouvements)
        assert etat.solde_releve == Decimal(1000000 + 6400000 - 2350000 - 18500)
        assert etat.solde_comptable == Decimal(1000000 + 6400000 - 2350000 - 300000)
        assert etat.releve_non_rapproche == Decimal(-18500)
        assert etat.comptable_non_rapproche == Decimal(-300000)
        assert etat.ecart_inexplique == 0
        assert (etat.rapprochees, etat.a_traiter) == (2, 1)
        # Une écriture oubliée au relevé comme en comptabilité se voit en écart.
        oubli = etat_du_rapprochement(r, [encaissement, reglement, cheque])
        assert oubli.ecart_inexplique == Decimal(1000000)
        # Rapprochée par un autre relevé, le chèque n'est plus « absent du relevé ».
        ailleurs = etat_du_rapprochement(r, mouvements, {cheque.designation})
        assert ailleurs.comptable_non_rapproche == 0

    def _pret(self):
        r = _rapprochement([_ligne(1, 18, 500), _ligne(2, 20, 40)])
        m = _mouvement(1, 18, 500)
        r = _apparier(r, 1, m).justifier(
            2,
            nature=NatureJustification.PIECE_DEMANDEE,
            motif="Aucune pièce pour ces frais.",
            par="C-004",
            le=INSTANT,
        )
        return r, m

    def test_la_validation_refuse_une_ligne_inexpliquee_puis_un_ecart(self):
        r = _rapprochement([_ligne(1, 18, 500)])
        with pytest.raises(RapprochementRefuse, match="ni rapprochée"):
            r.valider(etat_du_rapprochement(r, []), [], par="C-004", le=INSTANT)
        r, m = self._pret()
        # Un mouvement d'avant la période, rapproché nulle part : le solde comptable le
        # compte, aucun des deux côtés ne l'explique.
        juin = MouvementComptable(
            ecriture="2026/BQ/000099",
            ligne=0,
            date=date(2026, 6, 28),
            libelle="oublié",
            montant=Decimal(1000),
            sens=Sens.CREDIT,
            validee=True,
        )
        faux = [m, juin]
        with pytest.raises(RapprochementRefuse, match="écart inexpliqué de 1000"):
            r.valider(etat_du_rapprochement(r, faux), faux, par="C-004", le=INSTANT)

    def test_la_validation_refuse_un_brouillon_puis_une_ecriture_modifiee(self):
        r, m = self._pret()
        brouillon = [m.model_copy(update={"validee": False})]
        etat = etat_du_rapprochement(r, [m])
        with pytest.raises(RapprochementRefuse, match="non validées ou disparues : 2026/BQ/000001"):
            r.valider(etat, brouillon, par="C-004", le=INSTANT)
        with pytest.raises(RapprochementRefuse, match="non validées ou disparues"):
            r.valider(etat, [], par="C-004", le=INSTANT)
        corrigee = [m.model_copy(update={"montant": Decimal(510)})]
        with pytest.raises(
            RapprochementRefuse, match="modifiées depuis le rapprochement : ligne 1"
        ):
            r.valider(etat, corrigee, par="C-004", le=INSTANT)

    def test_valide_l_etat_est_fige_et_plus_aucun_geste_n_est_possible(self):
        r, m = self._pret()
        valide = r.valider(etat_du_rapprochement(r, [m]), [m], par="C-004", le=INSTANT)
        assert valide.statut is StatutRapprochement.VALIDE
        # Une écriture ajoutée après coup ne change pas un état arrêté.
        tard = etat_du_rapprochement(valide, [m, _mouvement(9, 25, 1000)])
        assert tard == valide.etat_valide
        assert tard.solde_comptable == Decimal(-500)
        with pytest.raises(RapprochementRefuse, match="plus aucun geste"):
            valide.dissocier(1)
        with pytest.raises(RapprochementRefuse, match="plus aucun geste"):
            valide.abandonner("Importé par erreur, mauvais mois.")


# ── L'import et l'automatique ─────────────────────────────────────────────────


def _importer(depot, lignes, mouvements, journal=BQ, du=1, au=31, solde_initial=0):
    total = sum((l_.signe for l_ in lignes), Decimal(0))
    return importer_un_releve(
        dossier=BATIMENT,
        journal=journal,
        exercice="2026",
        du=date(2026, 7, du),
        au=date(2026, 7, au),
        solde_initial=Decimal(solde_initial),
        solde_final=Decimal(solde_initial) + total,
        lignes=lignes,
        source="saisie",
        mouvements=mouvements,
        reglages=REGLAGES,
        par="C-004",
        le=INSTANT,
        depot=depot,
    )


@pytest.mark.usefixtures("cabinet")
class TestLImport:
    def test_la_caisse_ne_se_rapproche_pas_d_un_releve(self):
        with pytest.raises(RapprochementRefuse, match="pas un journal de banque"):
            _importer(DepotRapprochementsMemoire(), [_ligne(1, 3, 100)], [], journal=CA)

    def test_l_automatique_n_apparie_que_l_unique_correspondance_forte(self):
        lignes = [
            _ligne(1, 18, 2350000, libelle="VIR WOURI FACT 0412"),
            _ligne(2, 10, 250000),
            _ligne(3, 10, 250000),
            _ligne(4, 21, 18500),
        ]
        mouvements = [
            _mouvement(1, 17, 2350000, reference="F-2026-0412"),
            _mouvement(2, 10, 250000),
            _mouvement(3, 10, 250000),
        ]
        r = _importer(DepotRapprochementsMemoire(), lignes, mouvements)
        assert [(a.rang, a.ecriture, a.mode) for a in r.appariements] == [
            (1, "2026/BQ/000001", ModeAppariement.AUTOMATIQUE)
        ]
        assert r.appariements[0].motifs[:2] == [
            "montant exact",
            "référence F-2026-0412 présente dans le libellé",
        ]

    def test_deux_correspondances_fortes_restent_au_comptable(self):
        """Deux écritures portent la même référence (une facture saisie deux fois) : l'une
        des deux est une erreur, et l'automatique ne choisit pas laquelle."""
        mouvements = [
            _mouvement(1, 17, 900, reference="F-2026-0412"),
            _mouvement(2, 19, 900, reference="F-2026-0412"),
        ]
        r = _importer(
            DepotRapprochementsMemoire(), [_ligne(1, 18, 900, libelle="FACT 0412")], mouvements
        )
        assert r.appariements == []

    def test_l_automatique_ne_prend_pas_deux_fois_la_meme_ecriture(self):
        lignes = [_ligne(1, 10, 900, libelle="REF 4455"), _ligne(2, 11, 900, libelle="REF 4455")]
        r = _importer(
            DepotRapprochementsMemoire(), lignes, [_mouvement(1, 10, 900, reference="F-4455")]
        )
        assert [a.rang for a in r.appariements] == [1]

    def test_une_periode_qui_chevauche_est_refusee_sauf_si_l_autre_est_abandonne(self):
        depot = DepotRapprochementsMemoire()
        m = _mouvement(1, 20, 700, reference="F-7788")
        premier = _importer(depot, [_ligne(1, 20, 700, libelle="F 7788")], [m])
        assert premier.appariements
        with pytest.raises(RapprochementRefuse, match="chevauche le relevé du 01/07/2026"):
            _importer(depot, [_ligne(1, 20, 700)], [m], du=15, au=31)
        abandonner_un_rapprochement(
            dossier=BATIMENT,
            identifiant=premier.identifiant,
            motif="Relevé d'un autre compte, importé par erreur.",
            depot=depot,
        )
        second = _importer(depot, [_ligne(1, 20, 700, libelle="F 7788")], [m], du=15, au=31)
        assert second.identifiant == "RB-BQ-20260731-2"
        # L'abandonné ne retient plus son écriture : elle est de nouveau rapprochée.
        assert second.appariements
        assert rapproches_par_les_autres(depot, second) == set()

    def test_un_releve_incoherent_parle_en_clair(self):
        with pytest.raises(RapprochementRefuse, match="^le relevé ne tombe pas juste"):
            importer_un_releve(
                dossier=BATIMENT,
                journal=BQ,
                exercice="2026",
                du=date(2026, 7, 1),
                au=date(2026, 7, 31),
                solde_initial=Decimal(0),
                solde_final=Decimal(1),
                lignes=[_ligne(1, 3, 100)],
                source="saisie",
                mouvements=[],
                reglages=REGLAGES,
                par="C-004",
                le=INSTANT,
                depot=DepotRapprochementsMemoire(),
            )

    def test_un_autre_cabinet_ne_voit_pas_le_releve(self):
        depot = DepotRapprochementsMemoire()
        _importer(depot, [_ligne(1, 3, 100)], [])
        with etabli("AUTRE-CABINET"):
            assert depot.du_dossier(BATIMENT) == []
            assert _importer(depot, [_ligne(1, 3, 100)], []).identifiant == "RB-BQ-20260731-1"


# ── Les routes ────────────────────────────────────────────────────────────────


def _client(courriel: str) -> TestClient:
    client = TestClient(creer_application())
    reponse = client.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert reponse.status_code == 200, reponse.text
    return client


def _ecriture(client, jour, libelle, compte, sens_banque, montant, reference=None, valider=True):
    contre = "DEBIT" if sens_banque == "CREDIT" else "CREDIT"
    reponse = client.post(
        f"/comptabilite/dossiers/{BATIMENT}/ecritures",
        json={
            "journal": "BQ",
            "exercice": "2026",
            "date_operation": f"2026-07-{jour:02d}",
            "libelle": libelle,
            "reference_externe": reference,
            "piece_justificative": "PJ-2026-0001" if valider else None,
            "lignes": [
                {"compte": "521", "libelle": libelle, "sens": sens_banque, "montant": montant},
                {"compte": compte, "libelle": libelle, "sens": contre, "montant": montant},
            ],
        },
    )
    assert reponse.status_code == 201, reponse.text
    corps = reponse.json()
    if valider:
        cle = f"{corps['exercice']}/{corps['journal']}/{corps['numero']}"
        validee = client.post(f"/comptabilite/dossiers/{BATIMENT}/ecritures/{cle}/validation")
        assert validee.status_code == 200, validee.text
    return corps


RELEVE = (
    "Date;Libellé;Débit;Crédit\n"
    "03/07/2026;VIR CLIENT SOCIETE KRIBI BTP;;6 400 000\n"
    "18/07/2026;VIR QUINCAILLERIE WOURI FACT 0412;2 350 000;\n"
    "21/07/2026;FRAIS TENUE DE COMPTE;18 500;\n"
)


def _importer_par_la_route(client, **autres):
    return client.post(
        f"/comptabilite/dossiers/{BATIMENT}/rapprochements",
        json={
            "journal": "BQ",
            "du": "2026-07-01",
            "au": "2026-07-31",
            "solde_initial": "0",
            "solde_final": str(6400000 - 2350000 - 18500),
            "profil": "csv-debit-credit",
            "fichier_base64": base64.b64encode(RELEVE.encode()).decode(),
            **autres,
        },
    )


def _base(identifiant: str) -> str:
    return f"/comptabilite/dossiers/{BATIMENT}/rapprochements/{identifiant}"


class TestLesRoutes:
    def test_le_parcours_du_comptable_jusqu_a_l_etat_arrete(self):
        comptable = _client(COMPTABLE)
        _ecriture(comptable, 17, "Règlement Wouri", "401", "CREDIT", "2350000", "F-2026-0412")
        _ecriture(comptable, 3, "Encaissement Kribi", "411", "DEBIT", "6400000")
        reponse = _importer_par_la_route(comptable)
        assert reponse.status_code == 201, reponse.text
        vue = reponse.json()
        assert vue["etat"]["automatiques"] == 2
        assert vue["etat"]["a_traiter"] == 1
        assert vue["etat"]["ecart_inexplique"] == "0"
        # Ce qui résiste d'abord.
        assert vue["lignes"][0]["ligne"]["libelle"] == "FRAIS TENUE DE COMPTE"
        identifiant = vue["rapprochement"]["identifiant"]

        refus = comptable.post(f"{_base(identifiant)}/validation")
        assert refus.status_code == 422 and "ni rapprochée" in refus.json()["detail"]
        justifie = comptable.post(
            f"{_base(identifiant)}/lignes/3/justification",
            json={"nature": "ECRITURE_A_VENIR", "motif": "Frais de juillet, saisis en août."},
        )
        assert justifie.status_code == 200, justifie.text
        valide = comptable.post(f"{_base(identifiant)}/validation")
        assert valide.status_code == 200, valide.text
        assert valide.json()["rapprochement"]["statut"] == "VALIDE"
        resumes = comptable.get(f"/comptabilite/dossiers/{BATIMENT}/rapprochements").json()
        assert [(r["identifiant"], r["statut"], r["a_traiter"]) for r in resumes] == [
            (identifiant, "VALIDE", 0)
        ]

    def test_rapprocher_a_la_main_puis_dissocier(self):
        comptable = _client(COMPTABLE)
        _ecriture(comptable, 10, "Premier", "401", "CREDIT", "2350000")
        _ecriture(comptable, 25, "Second", "401", "CREDIT", "2350000")
        identifiant = _importer_par_la_route(comptable).json()["rapprochement"]["identifiant"]
        vue = comptable.get(_base(identifiant)).json()
        ligne = next(l_ for l_ in vue["lignes"] if l_["ligne"]["rang"] == 2)
        assert ligne["appariement"] is None
        assert [p["force"] for p in ligne["propositions"]] == ["POSSIBLE", "POSSIBLE"]
        choisie = ligne["propositions"][1]["mouvement"]
        apparie = comptable.post(
            f"{_base(identifiant)}/appariements",
            json={"rang": 2, "ecriture": choisie["ecriture"], "ligne": choisie["ligne"]},
        )
        assert apparie.status_code == 200, apparie.text
        assert apparie.json()["etat"]["rapprochees"] == 1
        faux = comptable.post(
            f"{_base(identifiant)}/appariements",
            json={"rang": 3, "ecriture": choisie["ecriture"], "ligne": 1},
        )
        assert faux.status_code == 422 and "ne porte pas de ligne 2" in faux.json()["detail"]
        dissocie = comptable.post(f"{_base(identifiant)}/lignes/2/dissociation")
        assert dissocie.json()["etat"]["rapprochees"] == 0

    def test_la_saisie_manuelle_du_releve(self):
        comptable = _client(COMPTABLE)
        reponse = comptable.post(
            f"/comptabilite/dossiers/{BATIMENT}/rapprochements",
            json={
                "journal": "BQ",
                "du": "2026-07-01",
                "au": "2026-07-31",
                "solde_initial": "100000",
                "solde_final": "81500",
                "lignes": [{"date": "2026-07-21", "libelle": "FRAIS", "montant": "-18500"}],
            },
        )
        assert reponse.status_code == 201, reponse.text
        assert reponse.json()["rapprochement"]["source"] == "saisie"
        assert reponse.json()["lignes"][0]["ligne"]["sens"] == "CREDIT"

    @pytest.mark.parametrize(
        ("remplacements", "message"),
        [
            ({"profil": "banque-inconnue"}, "profil « banque-inconnue » inconnu"),
            ({"lignes": []}, "pas les deux"),
            ({"solde_final": "1"}, "ne tombe pas juste"),
            ({"journal": "CA"}, "pas un journal de banque"),
            (
                {"fichier_base64": base64.b64encode(b"Date;L;D;C\n99/99/2026;X;;1\n").decode()},
                "ligne 2 : date",
            ),
        ],
    )
    def test_un_import_refuse_dit_pourquoi(self, remplacements, message):
        reponse = _importer_par_la_route(_client(COMPTABLE), **remplacements)
        assert reponse.status_code == 422
        assert message in reponse.json()["detail"]

    def test_la_piece_demandee_arrive_a_qui_relance_l_adherent(self):
        comptable = _client(COMPTABLE)
        identifiant = _importer_par_la_route(comptable).json()["rapprochement"]["identifiant"]
        comptable.post(
            f"{_base(identifiant)}/lignes/3/justification",
            json={"nature": "PIECE_DEMANDEE", "motif": "Aucun justificatif de ces frais."},
        )
        chargee = _client(CHARGEE_CLIENTELE)
        notifications = chargee.get("/transverse/notifications").json()["notifications"]
        [avis] = [n for n in notifications if n["titre"].startswith("Pièce à demander")]
        assert avis["titre"] == "Pièce à demander : FRAIS TENUE DE COMPTE"
        assert "-18500 FCFA" in avis["texte"]
        assert avis["lien"] == f"/comptabilite/rapprochement/{identifiant}?dossier={BATIMENT}"
        # La chargée de clientèle lit le rapprochement, mais ne le prépare pas.
        assert chargee.get(_base(identifiant)).status_code == 200
        assert _importer_par_la_route(chargee).status_code == 403
        assert chargee.post(f"{_base(identifiant)}/validation").status_code == 403

    def test_hors_perimetre_le_dossier_n_existe_pas_et_l_adherent_n_y_a_pas_acces(self):
        identifiant = _importer_par_la_route(_client(COMPTABLE)).json()["rapprochement"][
            "identifiant"
        ]
        autre = _client(COMPTABLE_AUTRE)
        assert autre.get(_base(identifiant)).status_code == 404
        assert _importer_par_la_route(autre).status_code == 404
        adherent = _client(ADHERENT)
        assert adherent.get(f"/comptabilite/dossiers/{BATIMENT}/rapprochements").status_code == 403

    def test_abandonner_libere_la_periode(self):
        comptable = _client(COMPTABLE)
        identifiant = _importer_par_la_route(comptable).json()["rapprochement"]["identifiant"]
        assert _importer_par_la_route(comptable).status_code == 422
        abandon = comptable.post(
            f"{_base(identifiant)}/abandon", json={"motif": "Relevé du compte Mobile Money."}
        )
        assert abandon.status_code == 200
        assert _importer_par_la_route(comptable).status_code == 201
        assert comptable.get(_base("RB-BQ-INCONNU")).status_code == 404


class TestSurPostgresql:
    pytestmark = exige_postgresql

    def test_importer_justifier_et_relire_d_une_requete_a_l_autre(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)
        reponse = _importer_par_la_route(client)
        assert reponse.status_code == 201, reponse.text
        identifiant = reponse.json()["rapprochement"]["identifiant"]
        client.post(
            f"{_base(identifiant)}/lignes/3/justification",
            json={"nature": "PIECE_DEMANDEE", "motif": "Aucun justificatif de ces frais."},
        )
        relu = client.get(_base(identifiant)).json()
        assert relu["etat"]["justifiees"] == 1
        assert relu["rapprochement"]["lignes"][1]["libelle"] == "VIR QUINCAILLERIE WOURI FACT 0412"
