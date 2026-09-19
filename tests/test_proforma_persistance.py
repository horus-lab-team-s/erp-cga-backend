"""La proforma, adossée à la base.

C'est la table dont la durée de conservation est la plus longue du contexte
commercial : dix ans, et le document doit ressortir à l'identique.

Ces tests vérifient ce que le domaine seul ne peut pas dire :

* que **deux cabinets peuvent porter le même numéro**, parce qu'un numéro de
  proforma est séquentiel par cabinet ;
* que le même cabinet ne le peut pas, et que c'est la **base** qui l'arbitre ;
* que les colonnes promues suivent le document, y compris à travers un
  remplacement de version.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.contextes.souscription.adaptateurs.sortant.depots_memoire import (
    ProformaIntrouvable,
)
from app.contextes.souscription.adaptateurs.sortant.depots_sql import DepotProformasSql
from app.contextes.souscription.domaine.proforma import (
    EtatProforma,
    TarifArrete,
    emettre,
)
from tests.conftest import exige_postgresql

pytestmark = exige_postgresql

T0 = datetime(2026, 9, 10, 10, 0)
CENTRE, AUTRE = "CGA-BRCG", "CGA-AUTRE"


def _tarif(montant: str = "250000") -> TarifArrete:
    return TarifArrete(
        montant=Decimal(montant),
        plancher=Decimal("200000"),
        reference=Decimal("250000"),
        plafond=Decimal("375000"),
        version_bareme="2026.1",
        chiffre_par="awono",
        valide_par="direction",
        arrete_le=T0,
    )


def _proforma(numero: str = "PRO-2026-0001", dossier: str = "dos-1", **surcharges):
    defauts = {
        "numero": numero,
        "dossier": dossier,
        "service": "creation-sarl",
        "tarif": _tarif(),
        "contenu": b"%PDF le document",
        "modele": "creation-sarl",
        "version_modele": "2026.1",
        "a_l_instant": T0,
    }
    return emettre(**{**defauts, **surcharges})


@pytest.fixture
def depot(session_sql) -> DepotProformasSql:
    return DepotProformasSql(session_sql, CENTRE)


class TestAllerRetour:
    def test_une_proforma_ecrite_se_relit_a_l_identique(self, depot):
        origine = _proforma()
        depot.enregistrer(origine)
        assert depot.lire("PRO-2026-0001") == origine

    def test_l_empreinte_survit_a_l_aller_retour(self, depot):
        """Sans elle, on ne peut plus dire si le document a été touché après
        coup, et un document touché après coup ne vaut plus rien."""
        depot.enregistrer(_proforma())
        relue = depot.lire("PRO-2026-0001")
        assert relue.document_intact(b"%PDF le document") is True
        assert relue.document_intact(b"%PDF autre chose") is False

    def test_une_proforma_inconnue_leve(self, depot):
        with pytest.raises(ProformaIntrouvable):
            depot.lire("PRO-2026-9999")


class TestNumerotationParCabinet:
    """⚠️ **Le défaut trouvé en éprouvant la contrainte sur une vraie base.**

    Le rejet venait de la clé primaire, pas de la contrainte prévue pour cela :
    la clé portait le numéro seul, donc deux cabinets n'auraient jamais pu avoir
    tous deux `PRO-2026-0001`. C'est pourtant le cas normal.
    """

    def test_deux_cabinets_portent_le_meme_numero(self, session_sql):
        DepotProformasSql(session_sql, CENTRE).enregistrer(_proforma())
        DepotProformasSql(session_sql, AUTRE).enregistrer(
            _proforma(dossier="dos-9")
        )
        session_sql.flush()

        assert DepotProformasSql(session_sql, CENTRE).lire("PRO-2026-0001").dossier == "dos-1"
        assert DepotProformasSql(session_sql, AUTRE).lire("PRO-2026-0001").dossier == "dos-9"

    def test_le_meme_cabinet_ne_le_peut_pas(self, session_sql):
        """C'est la base qui arbitre deux émissions simultanées, jamais une
        lecture suivie d'une écriture : entre les deux, l'autre a émis."""
        depot = DepotProformasSql(session_sql, CENTRE)
        depot.enregistrer(_proforma())
        session_sql.flush()

        from sqlalchemy import text

        session_sql.expunge_all()
        with pytest.raises(IntegrityError, match="pk_proforma"):
            session_sql.execute(
                text(
                    "INSERT INTO proforma (numero, dossier, etat, version, montant, "
                    "emise_le, locataire, donnees) VALUES "
                    "('PRO-2026-0001','dos-2','EMISE',1,999,now(),:loc, "
                    "CAST('{}' AS json))"
                ),
                {"loc": CENTRE},
            )

    def test_le_dernier_numero_se_lit_par_cabinet(self, session_sql):
        DepotProformasSql(session_sql, CENTRE).enregistrer(_proforma("PRO-2026-0007"))
        DepotProformasSql(session_sql, AUTRE).enregistrer(_proforma("PRO-2026-0042"))
        session_sql.flush()

        assert DepotProformasSql(session_sql, CENTRE).dernier_numero("PRO", 2026) == "PRO-2026-0007"
        assert DepotProformasSql(session_sql, AUTRE).dernier_numero("PRO", 2026) == "PRO-2026-0042"

    def test_une_serie_neuve_rend_none(self, depot):
        assert depot.dernier_numero("PRO", 2030) is None


class TestLesColonnesSuiventLeDocument:
    def test_le_remplacement_ecrit_les_deux_lignes(self, depot):
        """Rendre la seule nouvelle laisserait l'ancienne opposable : **deux
        proformas actives pour le même engagement, à deux montants**."""
        v1 = _proforma()
        depot.enregistrer(v1)
        remplacee, v2 = v1.nouvelle_version(
            _tarif("300000"), numero="PRO-2026-0002",
            contenu=b"%PDF version deux", a_l_instant=T0 + timedelta(days=1),
        )
        depot.enregistrer(remplacee)
        depot.enregistrer(v2)

        assert depot.lire("PRO-2026-0001").etat is EtatProforma.REMPLACEE
        relue = depot.lire("PRO-2026-0002")
        assert relue.version == 2
        assert relue.remplace == "PRO-2026-0001"
        assert relue.tarif.montant == Decimal("300000")

    def test_le_fil_des_versions_se_lit_dans_l_ordre(self, depot):
        """L'ordre dans lequel on le raconte au client."""
        v1 = _proforma()
        depot.enregistrer(v1)
        remplacee, v2 = v1.nouvelle_version(
            _tarif("300000"), numero="PRO-2026-0002",
            contenu=b"v2", a_l_instant=T0 + timedelta(days=1),
        )
        depot.enregistrer(remplacee)
        depot.enregistrer(v2)

        assert [p.numero for p in depot.par_dossier("dos-1")] == [
            "PRO-2026-0001",
            "PRO-2026-0002",
        ]

    def test_a_relancer_ne_rend_que_les_transmises_sans_reponse(self, depot):
        """Le calendrier de relance à 3, 7 et 14 jours. Relancer une proforma
        acceptée harcèlerait un client qui a déjà dit oui."""
        depot.enregistrer(_proforma("PRO-2026-0001").transmise(T0))
        depot.enregistrer(_proforma("PRO-2026-0002", dossier="dos-2"))
        depot.enregistrer(
            _proforma("PRO-2026-0003", dossier="dos-3").transmise(T0).acceptee()
        )

        assert [p.numero for p in depot.a_relancer()] == ["PRO-2026-0001"]

    def test_les_plus_anciennes_se_relancent_d_abord(self, depot):
        for rang, jours in ((1, 10), (2, 2), (3, 5)):
            depot.enregistrer(
                _proforma(f"PRO-2026-000{rang}", dossier=f"dos-{rang}").transmise(
                    T0 + timedelta(days=jours)
                )
            )
        assert [p.numero for p in depot.a_relancer()] == [
            "PRO-2026-0002",
            "PRO-2026-0003",
            "PRO-2026-0001",
        ]


class TestCloisonnement:
    def test_un_cabinet_ne_lit_pas_la_proforma_d_un_autre(self, session_sql):
        DepotProformasSql(session_sql, AUTRE).enregistrer(_proforma("PRO-2026-0500"))
        session_sql.flush()
        with pytest.raises(ProformaIntrouvable):
            DepotProformasSql(session_sql, CENTRE).lire("PRO-2026-0500")

    def test_la_relance_ne_traverse_pas_la_cloison(self, session_sql):
        """Relancer le client d'un autre cabinet serait le pire incident
        commercial que cette plateforme puisse produire."""
        DepotProformasSql(session_sql, AUTRE).enregistrer(
            _proforma("PRO-2026-0600", dossier="dos-eux").transmise(T0)
        )
        session_sql.flush()
        assert DepotProformasSql(session_sql, CENTRE).a_relancer() == []
