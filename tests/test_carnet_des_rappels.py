"""Le carnet des rappels : ce qu'un humain voit, et ce qu'il ne peut pas faire deux fois.

⚠️ **Sans cet écran, le repli par appel ne serait pas un repli mais un silence** : la
relance serait comptée comme remise, et personne n'appellerait. Le plancher du plan
de contact deviendrait un trou.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.contextes.souscription.adaptateurs.sortant.depots_sql import DepotRappelsSql
from app.contextes.souscription.domaine.rappels import RappelAPasser, RappelDejaFait
from tests.conftest import exige_postgresql

T0 = datetime(2026, 9, 10, 9, 0)


def _rappel(identifiant="rap-1", dossier="dos-1", cree_le=T0) -> RappelAPasser:
    return RappelAPasser(
        identifiant=identifiant,
        dossier=dossier,
        motif="rappeler au sujet de la proforma PRO-2026-0001 (relance de rang 2)",
        cree_le=cree_le,
    )


class TestLeDomaine:
    def test_un_rappel_neuf_attend(self):
        assert _rappel().en_attente

    def test_clore_demande_de_se_nommer(self):
        """Un carnet où l'on peut clore sans nom ne dit plus qui a parlé au client,
        et c'est la question qu'on pose quand un client rappelle en disant « on m'a
        déjà répondu »."""
        with pytest.raises(ValueError, match="se nommer"):
            _rappel().fait("   ", T0)

    def test_un_rappel_clos_refuse_un_second_appel(self):
        """⚠️ Rendre `self` ferait qu'un second collaborateur croirait avoir pris le
        contact alors que le premier l'avait pris : deux appels au même client, à
        quelques minutes, sur le même sujet."""
        clos = _rappel().fait("awono", T0)
        with pytest.raises(RappelDejaFait, match="déjà clos par awono"):
            clos.fait("mballa", T0 + timedelta(minutes=5))

    def test_le_nom_et_la_date_vont_ensemble(self):
        """Un rappel clos sans nom ne dit plus qui a parlé ; un nom sans date ne dit
        pas quand."""
        with pytest.raises(ValueError, match="vont"):
            RappelAPasser(
                identifiant="r", dossier="d", motif="m", cree_le=T0, fait_par="awono"
            )

    def test_le_motif_est_obligatoire(self):
        """Une liste de références de dossier obligerait le responsable à rouvrir
        chaque dossier pour comprendre ce qu'on attend de lui. Il cesserait de la
        lire, et le trou reviendrait par un autre chemin."""
        with pytest.raises(ValueError):
            RappelAPasser(identifiant="r", dossier="d", motif="", cree_le=T0)


@exige_postgresql
class TestLaPersistance:
    def test_en_attente_ignore_les_rappels_clos(self, session_sql):
        depot = DepotRappelsSql(session_sql, "CGA-BRCG")
        depot.enregistrer(_rappel("rap-1"))
        depot.enregistrer(_rappel("rap-2").fait("awono", T0))
        session_sql.flush()

        assert [r.identifiant for r in depot.en_attente()] == ["rap-1"]

    def test_l_ordre_est_du_plus_ancien_au_plus_recent(self, session_sql):
        """C'est le client qui attend depuis le plus longtemps qu'on rappelle en
        premier."""
        depot = DepotRappelsSql(session_sql, "CGA-BRCG")
        depot.enregistrer(_rappel("rap-recent", cree_le=T0 + timedelta(days=2)))
        depot.enregistrer(_rappel("rap-ancien", cree_le=T0))
        session_sql.flush()

        assert [r.identifiant for r in depot.en_attente()] == ["rap-ancien", "rap-recent"]

    def test_deux_cabinets_portent_le_meme_identifiant(self, session_sql):
        """⚠️ **Le piège, pour la troisième fois de ce chantier.**

        L'identifiant d'un rappel dérive de celui de l'événement, qui dérive du
        numéro de proforma, lequel est **séquentiel par cabinet**.
        `rap-rel-PRO-2026-0001-2` existe donc chez chaque cabinet qui relance sa
        première proforma au rang 2.

        Une clé sur le seul identifiant ferait que le premier à relancer empêcherait
        tous les autres d'inscrire leur rappel, et le refus citerait une contrainte
        de clé primaire sans rapport apparent avec le cloisonnement.

        *La règle générale se lit maintenant : toute clé dérivée d'une numérotation
        par cabinet doit porter le locataire.*
        """
        commun = "rap-rel-PRO-2026-0001-2"
        DepotRappelsSql(session_sql, "CGA-BRCG").enregistrer(_rappel(commun))
        DepotRappelsSql(session_sql, "CGA-AUTRE").enregistrer(_rappel(commun))
        session_sql.flush()

        assert len(DepotRappelsSql(session_sql, "CGA-BRCG").en_attente()) == 1
        assert len(DepotRappelsSql(session_sql, "CGA-AUTRE").en_attente()) == 1

    def test_par_dossier_rend_aussi_les_clos(self, session_sql):
        """La question posée en rouvrant un dossier est « qu'a-t-on déjà confié »,
        et un rappel déjà passé y répond autant qu'un rappel en attente."""
        depot = DepotRappelsSql(session_sql, "CGA-BRCG")
        depot.enregistrer(_rappel("rap-1"))
        depot.enregistrer(_rappel("rap-2").fait("awono", T0))
        session_sql.flush()

        assert len(depot.par_dossier("dos-1")) == 2
