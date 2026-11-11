"""Les échéances au Code général des impôts 2026 (15 octobre 2026).

Lues dans l'édition officielle du CGI au 1er janvier 2026 (DGI), elles remplacent ce que
le catalogue des obligations portait comme inconnues ou comme constantes.

⚠️ CE QUI EST GARDÉ
* la DSF se dépose à une date civile qui dépend du centre : 15 mars (DGE), 15 avril
  (CIME), 15 mai (CDI) l'année qui suit la clôture (art. 18, C 44 (3)) ;
* l'IGS est libératoire de la patente (art. C 38) : un dossier à l'IGS ne la doit pas ;
* la déclaration annuelle de l'IGS est due le 15 mars de l'année QUI SUIT (art. C 41).
"""

from __future__ import annotations

from datetime import date

import pytest

from app.contextes.obligations.adaptateurs.sortant.catalogue_obligations import (
    CATALOGUE_OBLIGATIONS,
)
from app.contextes.obligations.domaine.echeances import calculer_echeance
from app.contextes.portefeuille.contrats import CentreRattachement, RegimeFiscal

TYPES = {t.code: t for t in CATALOGUE_OBLIGATIONS}


@pytest.mark.parametrize(
    ("centre", "attendue"),
    [
        (CentreRattachement.DGE, date(2026, 3, 15)),
        (CentreRattachement.CIME, date(2026, 4, 15)),
        (CentreRattachement.CDI, date(2026, 5, 15)),
    ],
)
def test_la_dsf_se_depose_a_la_date_de_son_centre(centre, attendue):
    echeance = calculer_echeance(
        TYPES["DSF"],
        fin_de_periode=date(2025, 12, 31),
        centre=centre,
        cloture_exercice=date(2025, 12, 31),
    )

    assert echeance == attendue


def test_l_igs_libere_de_la_patente():
    patente = TYPES["PATENTE"]

    assert not patente.concerne(RegimeFiscal.IGS, assujettie_tva=False, a_des_salaries=True)
    assert patente.concerne(RegimeFiscal.REEL, assujettie_tva=True, a_des_salaries=False)


def test_la_declaration_annuelle_de_l_igs_tombe_l_annee_suivante():
    declaration = TYPES["IGS_DECLARATION_ANNUELLE"]

    assert declaration.concerne(RegimeFiscal.IGS, assujettie_tva=False, a_des_salaries=False)
    assert not declaration.concerne(RegimeFiscal.REEL, assujettie_tva=True, a_des_salaries=False)
    echeance = calculer_echeance(
        declaration,
        fin_de_periode=date(2025, 12, 31),
        centre=CentreRattachement.CDI,
        debut_de_periode=date(2025, 1, 1),
    )
    assert echeance == date(2026, 3, 15)


# ── L'IGS : barème et réduction pour l'adhérent d'un CGA (CGI art. C 40) ─────────

from decimal import Decimal  # noqa: E402

from app.contextes.obligations.domaine.igs import estimer_l_igs  # noqa: E402
from tests.conftest import exige_postgresql, ouvrir_une_session  # noqa: E402

D = Decimal


def _classe(ca: Decimal) -> Decimal:
    """Les classes 7, 8 et 10 du barème C 40 (1), assez pour ces cas."""
    if ca >= 30_000_000:
        return D(2_000_000)
    if ca >= 10_000_000:
        return D(500_000)
    return D(300_000)


def _estimer(ca, *, adherent):
    return estimer_l_igs(
        D(ca),
        classe=_classe,
        adherent_cga=adherent,
        reduction_pourcent=D(50),
        seuil_comptabilite=D(10_000_000),
    )


class TestLEstimationDeLIgs:
    def test_l_adherent_tenu_a_comptabilite_paie_moitie(self):
        e = _estimer(38_500_000, adherent=True)

        assert (e.plein_tarif, e.annuel, e.trimestriel) == (D(2_000_000), D(1_000_000), D(250_000))
        assert e.reduction_cga

    def test_sans_adhesion_le_plein_tarif(self):
        e = _estimer(38_500_000, adherent=False)

        assert e.annuel == D(2_000_000) and not e.reduction_cga

    def test_sous_dix_millions_pas_de_reduction(self):
        """C 40 (2) : la réduction est réservée à l'assujetti TENU à une comptabilité,
        donc à partir de 10 M (C 44 (1)), même s'il adhère."""
        e = _estimer(8_000_000, adherent=True)

        assert e.annuel == D(300_000) and not e.reduction_cga


@exige_postgresql
class TestLEcheancierAfficheLIgs:
    def test_nguema_voit_son_igs_reduit_de_moitie(self, plateforme):
        """NGUEMA : IGS, adhérent depuis 2024, 38,5 M en 2025 (exercice repris).
        En 2026, classe 10 (2 000 000), réduite de moitié : 250 000 par trimestre."""
        client = plateforme
        ouvrir_une_session(client, "c.ndongo@cga-brcg.cm")

        lignes = client.get(
            "/obligations/dossiers/P027788990011M/echeancier",
            params={"exercice": "2026", "a_la_date": "2026-10-15"},
        ).json()

        igs = [x["obligation"] for x in lignes if x["obligation"]["code_obligation"] == "IGS"]
        assert len(igs) == 4, lignes
        assert {D(o["montant_estime"]) for o in igs} == {D(250_000)}
        # Ce que l'adhérent lit : l'économie que lui vaut son adhésion au centre.
        assert "1 000 000 FCFA d'économie" in igs[0]["explication_estimation"]
        assert "2 000 000 FCFA au plein tarif" in igs[0]["explication_estimation"]
        assert not [x for x in lignes if x["obligation"]["code_obligation"] == "PATENTE"]


# ── La DSF d'un dossier à l'IGS : due dès 10 M seulement (CGI art. C 44) ─────────


class TestLaDispenseDeDsf:
    def test_une_dispense_retire_l_obligation_et_elle_seule(self):
        from app.contextes.obligations.api import generer_echeancier
        from app.contextes.portefeuille.adaptateurs.sortant.donnees_demo import (
            PORTEFEUILLE_DEMO,
        )

        dossier = PORTEFEUILLE_DEMO["P027788990011M"]
        (exercice,) = [e for e in dossier.exercices if e.libelle == "2025"]

        def generer(dispense):
            return {
                o.code_obligation
                for o in generer_echeancier(
                    dossier,
                    CATALOGUE_OBLIGATIONS,
                    exercice,
                    emploie_sur=lambda d, f: False,
                    accuse_de=lambda cle: None,
                    dispense=dispense,
                )
            }

        avec = generer(None)
        sans = generer(lambda t, d, f: t.code == "DSF")
        assert "DSF" in avec and avec - sans == {"DSF"}


@exige_postgresql
class TestLaDsfDeNguema:
    def test_a_l_igs_au_dela_de_dix_millions_la_dsf_reste_due(self, plateforme):
        """NGUEMA, à l'IGS, a réalisé 38,5 M en 2025 : il tient une comptabilité au SMT
        et sa DSF est due (C 44 (3)), le 15 mai, date des centres de fiscalité locale."""
        client = plateforme
        ouvrir_une_session(client, "c.ndongo@cga-brcg.cm")

        lignes = client.get(
            "/obligations/dossiers/P027788990011M/echeancier",
            params={"exercice": "2025", "a_la_date": "2026-10-15"},
        ).json()

        (dsf,) = [x["obligation"] for x in lignes if x["obligation"]["code_obligation"] == "DSF"]
        assert dsf["echeance"] == "2026-05-15"
