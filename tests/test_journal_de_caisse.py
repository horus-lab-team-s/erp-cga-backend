"""Le journal de caisse, par axe (18 octobre 2026, point 6).

⚠️ CE QUI EST GARDÉ
* chaque versement est rangé sur son axe, et les fonds de tiers ont leur propre total ;
* la période et les familles visibles filtrent ;
* par les routes : la caisse lit le journal de démonstration ; une famille retirée par
  la matrice disparaît du journal ET du panneau de caisse ; un comptable ne lit rien.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from app.contextes.souscription.domaine.journal_de_caisse import (
    AxeDeVersement,
    FamilleDeVersement,
    LigneDuJournal,
    composer_le_journal,
)
from tests.conftest import enroler_par_le_courriel, exige_postgresql, ouvrir_une_session


def _ligne(
    axe: AxeDeVersement, montant: int, le: datetime, canal: str = "GUICHET"
) -> LigneDuJournal:
    return LigneDuJournal(
        le=le, axe=axe, objet="x", client="c", dossier="d", montant=Decimal(montant), canal=canal
    )


class TestLaComposition:
    def test_totaux_par_axe_et_fonds_de_tiers_a_part(self):
        lignes = [
            _ligne(AxeDeVersement.HONORAIRES, 45000, datetime(2026, 10, 2), "MOBILE_MONEY"),
            _ligne(AxeDeVersement.HONORAIRES, 45000, datetime(2026, 10, 5)),
            _ligne(AxeDeVersement.CREATION, 300000, datetime(2026, 10, 6)),
            _ligne(AxeDeVersement.CNPS, 80000, datetime(2026, 10, 7)),
            _ligne(AxeDeVersement.INSCRIPTION, 50000, datetime(2026, 9, 30)),  # hors période
        ]
        journal = composer_le_journal(
            lignes,
            du=date(2026, 10, 1),
            au=date(2026, 10, 31),
            familles=list(FamilleDeVersement),
            jour_de=lambda instant: instant.date(),
        )
        totaux = {t.axe: t for t in journal.totaux}
        assert totaux[AxeDeVersement.HONORAIRES].montant == Decimal(90000)
        assert totaux[AxeDeVersement.HONORAIRES].dont_mobile_money == Decimal(45000)
        assert journal.total_du_cabinet == Decimal(390000)
        assert journal.total_des_fonds_de_tiers == Decimal(80000)
        assert AxeDeVersement.INSCRIPTION not in totaux
        assert journal.lignes[0].axe is AxeDeVersement.CNPS  # le plus récent d'abord

    def test_une_famille_masquee_n_est_ni_listee_ni_totalisee(self):
        journal = composer_le_journal(
            [_ligne(AxeDeVersement.TVA, 10, datetime(2026, 10, 2))],
            du=date(2026, 10, 1),
            au=date(2026, 10, 31),
            familles=[FamilleDeVersement.COMMERCE, FamilleDeVersement.HONORAIRES],
            jour_de=lambda instant: instant.date(),
        )
        assert journal.lignes == [] and journal.total_des_fonds_de_tiers == 0


CAISSIERE = "n.essama@cga-brcg.cm"
SUPER_ADMIN = "h.kamga@cga-brcg.cm"
COMPTABLE = "l.fotso@cga-brcg.cm"


@exige_postgresql
class TestParLesRoutes:
    def test_la_caisse_lit_les_honoraires_de_la_demonstration(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, CAISSIERE)
        du = (date.today() - timedelta(days=365)).isoformat()

        journal = client.get("/souscription/caisse/journal", params={"du": du})

        assert journal.status_code == 200, journal.text
        corps = journal.json()
        honoraires = next(t for t in corps["totaux"] if t["axe"] == "HONORAIRES")
        assert honoraires["nombre"] > 0
        assert (
            Decimal(honoraires["dont_mobile_money"]) > 0 and Decimal(honoraires["dont_guichet"]) > 0
        )
        export = client.get("/souscription/caisse/journal.xlsx", params={"du": du})
        assert export.status_code == 200 and export.content[:2] == b"PK"
        assert client.get("/souscription/caisse/non-rattaches").status_code == 200

    def test_une_famille_retiree_par_la_matrice_disparait(self, plateforme):
        from app.contextes.transverse.api import code_attendu
        from app.partage.horloge import maintenant

        client = plateforme
        ouvrir_une_session(client, SUPER_ADMIN)
        secret = enroler_par_le_courriel(client)
        renfort = client.post(
            "/transverse/session/renforcement", json={"code": code_attendu(secret, maintenant())}
        )
        assert renfort.status_code == 200, renfort.text
        regle = client.put(
            "/transverse/matrice-des-roles/CAISSE",
            json={
                "permissions": [
                    "ENCAISSER",
                    "VOIR_VERSEMENTS_COMMERCE",
                    "VOIR_VERSEMENTS_FONDS_DE_TIERS",
                ],
                "motif": "la caisse ne suit plus les honoraires",
            },
        )
        assert regle.status_code == 200, regle.text

        client.cookies.clear()
        ouvrir_une_session(client, CAISSIERE)
        journal = client.get(
            "/souscription/caisse/journal",
            params={"du": (date.today() - timedelta(days=365)).isoformat()},
        ).json()
        assert "HONORAIRES" not in journal["familles_visibles"]
        assert all(t["axe"] != "HONORAIRES" for t in journal["totaux"])
        assert client.get("/souscription/honoraires/caisse").status_code == 403

    def test_un_comptable_ne_lit_pas_la_caisse(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)
        assert client.get("/souscription/caisse/journal").status_code == 403
