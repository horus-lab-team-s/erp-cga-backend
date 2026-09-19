"""L'espace de l'adhérent : ce qu'on attend de lui, ses justificatifs, sa réponse au cabinet (pas 112).

Maquette « Espace adhérent CGA », vues B et C.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE

1. **La réponse de l'adhérent** : un message vide, un « plus tard » sans jour à venir, une date
   sur une réponse qui n'en porte pas, une réponse à une demande close, le double appui.
2. **Les réglages** qui mentiraient : un « message » réglé en réponse toute faite, un bandeau qui
   tairait la date limite, une valeur que l'écran ne connaît pas.
3. **Le statut lu par l'adhérent** : « à corriger » l'emporte sur l'état, le mois du document
   avant celui de la réception, le compte du mois avant filtre.
4. **Le bandeau** : quatre états, et « complet » seulement s'il est vrai.
5. **Les routes** : l'accueil, la réponse (et l'avis au cabinet), les justificatifs, les refus
   (collaborateur qui répondrait à la place, autre dossier), les achats d'un seul mois revu.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contextes.collecte.adaptateurs.sortant.reponses_adherent_yaml import (
    charger_les_reponses_de_l_adherent,
)
from app.contextes.collecte.api import (
    CanalDepot,
    DemandePiece,
    EtatPiece,
    PieceJustificative,
    TypePiece,
    vider_les_magasins_de_la_collecte,
)
from app.contextes.collecte.domaine.demandes import NatureDeReponse, ReponseDeLAdherent
from app.contextes.collecte.domaine.espace_adherent import (
    ReglagesDesReponses,
    StatutPourLAdherent,
    mois_de_la_piece,
    texte_de_la_reponse,
    vue_des_justificatifs,
)
from app.contextes.comptabilite.api import vider_les_ecritures_en_memoire
from app.contextes.pilotage.adaptateurs.sortant.espace_adherent_yaml import (
    charger_les_reglages_de_l_espace_adherent,
)
from app.contextes.pilotage.domaine.mois_de_l_adherent import (
    AttendueDeLAdherent,
    EtatDuMois,
    ReglagesDeLEspaceAdherent,
    TexteDeBandeau,
    achats_du_mois,
    de_mois,
    etat_du_mois,
    rendre_le_bandeau,
)
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.main import creer_application
from app.partage.horloge import horloge_figee

REFERENTIEL = Path(__file__).resolve().parents[2] / "Docs" / "referentiel"
LE_5_AOUT = datetime(2026, 8, 5, 9, 0)


def _demande(**champs) -> DemandePiece:
    valeurs = {
        "identifiant": "DP-1",
        "entreprise": "M081234567890P",
        "type_attendu": TypePiece.RELEVE_BANCAIRE,
        "motif": "Relevé bancaire de juillet",
        "demandee_le": date(2026, 8, 1),
    }
    return DemandePiece(**{**valeurs, **champs})


def _reponse(nature=NatureDeReponse.INTROUVABLE, **champs) -> ReponseDeLAdherent:
    return ReponseDeLAdherent(
        **{"nature": nature, "le": LE_5_AOUT, "par": "jp.nkoa@batimentplus.cm", **champs}
    )


# ── 1. La réponse ─────────────────────────────────────────────────────────────


class TestLaReponse:
    def test_un_message_vide_est_refuse(self):
        with pytest.raises(ValidationError, match="ne peut pas être vide"):
            _reponse(NatureDeReponse.MESSAGE, message="   ")
        assert _reponse(message="  précision  ").message == "précision"

    def test_plus_tard_annonce_un_jour_a_venir(self):
        with pytest.raises(ValidationError, match="annonce un jour"):
            _reponse(NatureDeReponse.PLUS_TARD)
        with pytest.raises(ValidationError, match="annonce un jour"):
            _reponse(NatureDeReponse.PLUS_TARD, annoncee_pour=date(2026, 8, 4))
        assert _reponse(NatureDeReponse.PLUS_TARD, annoncee_pour=date(2026, 8, 5)).annoncee_pour
        with pytest.raises(ValidationError, match="n'annonce pas de date"):
            _reponse(annoncee_pour=date(2026, 8, 12))

    def test_une_demande_close_n_attend_plus_de_reponse(self):
        satisfaite = _demande().satisfaire("PJ-1", date(2026, 8, 2))
        with pytest.raises(ValueError, match="n'attend plus cette pièce"):
            satisfaite.repondre(_reponse())
        with pytest.raises(ValueError, match="avant la demande"):
            _demande(demandee_le=date(2026, 8, 6)).repondre(_reponse())

    def test_le_double_appui_est_refuse_mais_pas_une_autre_reponse(self):
        repondue = _demande().repondre(_reponse())
        with pytest.raises(ValueError, match="déjà été reçue aujourd'hui"):
            repondue.repondre(_reponse())
        autre = repondue.repondre(_reponse(NatureDeReponse.MESSAGE, message="Je cherche encore"))
        lendemain = autre.repondre(_reponse(le=datetime(2026, 8, 6, 8, 0)))
        assert len(lendemain.reponses) == 3
        assert lendemain.derniere_reponse.le.day == 6
        # La même réponse le lendemain, ou une autre précision le même jour, sont de vraies réponses.
        encore = lendemain.repondre(_reponse(le=datetime(2026, 8, 7, 8, 0)))
        precisee = encore.repondre(_reponse(le=datetime(2026, 8, 7, 9, 0), message="chez le fournisseur"))
        assert len(precisee.reponses) == 5
        # Une réponse ne ferme jamais la demande.
        assert lendemain.ouverte


class TestLeCasDUsage:
    def test_la_demande_d_un_autre_dossier_et_la_semaine_du_referentiel(self):
        from app.contextes.collecte.api import DepotDemandesMemoire
        from app.contextes.collecte.application.demandes_de_piece import (
            DemandeIntrouvable,
            repondre_a_une_demande,
        )

        depot = DepotDemandesMemoire()
        depot.enregistrer(_demande())
        valeurs = {
            "nature": NatureDeReponse.PLUS_TARD,
            "message": None,
            "le": LE_5_AOUT,
            "par": "A-001",
            "jours_si_plus_tard": 3,
            "demandes": depot,
        }
        with pytest.raises(DemandeIntrouvable):
            repondre_a_une_demande("DP-1", entreprise="M071122334455J", **valeurs)
        repondue = repondre_a_une_demande("DP-1", entreprise="M081234567890P", **valeurs)
        assert repondue.derniere_reponse.annoncee_pour == date(2026, 8, 8)
        assert depot.par_identifiant("DP-1").reponses == repondue.reponses


# ── 2. Les réglages ───────────────────────────────────────────────────────────


class TestLesReglages:
    def test_les_reponses_absentes_reprennent_leur_libelle(self):
        reglages = ReglagesDesReponses(reponses=[{"nature": "INTROUVABLE", "libelle": "Pas chez moi"}])
        libelles = {r.nature: r.libelle for r in reglages.reponses}
        assert libelles == {
            NatureDeReponse.INTROUVABLE: "Pas chez moi",
            NatureDeReponse.PLUS_TARD: "Je l'aurai la semaine prochaine",
        }

    def test_un_message_n_est_pas_une_reponse_toute_faite(self):
        with pytest.raises(ValidationError, match="champ libre"):
            ReglagesDesReponses(reponses=[{"nature": "MESSAGE", "libelle": "Écrire"}])
        with pytest.raises(ValidationError, match="réglée deux fois"):
            ReglagesDesReponses(
                reponses=[
                    {"nature": "INTROUVABLE", "libelle": "Pas chez moi"},
                    {"nature": "INTROUVABLE", "libelle": "Perdu"},
                ]
            )

    def test_le_texte_lu_par_le_cabinet(self):
        reglages = ReglagesDesReponses()
        plus_tard = _reponse(
            NatureDeReponse.PLUS_TARD, annoncee_pour=date(2026, 8, 12), message="chez le comptable"
        )
        assert (
            texte_de_la_reponse(plus_tard, reglages)
            == "Je l'aurai la semaine prochaine · annoncée pour le 12/08/2026 · « chez le comptable »"
        )

    def test_un_bandeau_qui_tairait_la_date_limite(self):
        with pytest.raises(ValidationError, match="doit citer"):
            ReglagesDeLEspaceAdherent(a_envoyer=TexteDeBandeau(titre="Il manque {pieces}", detail="Vite."))
        with pytest.raises(ValidationError, match="que l'écran ne connaît pas"):
            ReglagesDeLEspaceAdherent(
                complet=TexteDeBandeau(titre="Complet {moi}", detail="Rien à faire.")
            )
        with pytest.raises(ValidationError, match="pas de date limite"):
            ReglagesDeLEspaceAdherent(
                a_envoyer_en_retard=TexteDeBandeau(titre="Il manque {pieces}", detail="Avant le {date_limite}")
            )
        with pytest.raises(ValidationError, match="qui fait l'élision"):
            ReglagesDeLEspaceAdherent(
                complet=TexteDeBandeau(titre="Votre dossier de {mois} est complet", detail="Rien.")
            )
        with pytest.raises(ValidationError, match="débuts de numéro"):
            ReglagesDeLEspaceAdherent(racines_d_achats=("6x",))

    def test_les_fichiers_du_referentiel_se_chargent_et_disent_la_maquette(self):
        reponses = charger_les_reponses_de_l_adherent(REFERENTIEL)
        assert reponses.source == "collecte/reponses_adherent.yaml"
        assert reponses.jours_si_plus_tard == 7
        assert [r.libelle for r in reponses.reponses] == [
            "Je l'aurai la semaine prochaine",
            "Je n'ai pas ce document",
        ]
        espace = charger_les_reglages_de_l_espace_adherent(REFERENTIEL)
        assert espace.source == "pilotage/espace_adherent.yaml"
        # Le fichier redit les valeurs du domaine : un écart serait une décision, pas une copie.
        assert espace.model_dump(exclude={"source"}) == ReglagesDeLEspaceAdherent().model_dump(
            exclude={"source"}
        )
        assert charger_les_reponses_de_l_adherent(Path("/nulle/part")).source == "valeurs par défaut"


# ── 3. Les justificatifs ──────────────────────────────────────────────────────


def _piece(identifiant, *, etat=EtatPiece.RECUE, date_document=None, recue_le=None, emetteur=None):
    recue = recue_le or datetime(2026, 7, 20, 10, 0)
    return PieceJustificative(
        identifiant=identifiant,
        entreprise="M081234567890P",
        canal=CanalDepot.PORTAIL,
        depose_le=recue.date(),
        recue_le=recue,
        etat=etat,
        date_document=date_document,
        emetteur=emetteur,
        nom_fichier=f"{identifiant.lower()}.pdf",
        # Une pièce comptabilisée porte la clé de son écriture (invariant de la collecte).
        reference_ecriture="2026:AC:1" if etat is EtatPiece.COMPTABILISEE else None,
        motif_archivage="Contrat, gardé au dossier" if etat is EtatPiece.ARCHIVEE else None,
        reference_rapprochement="BQ:3" if etat is EtatPiece.RAPPROCHEE else None,
    )


class TestLesJustificatifs:
    def test_le_mois_du_document_avant_celui_de_la_reception(self):
        facture = _piece("P1", date_document=date(2026, 7, 30), recue_le=datetime(2026, 8, 3, 9, 0))
        assert mois_de_la_piece(facture) == "2026-07"
        assert mois_de_la_piece(_piece("P2", recue_le=datetime(2026, 8, 3, 9, 0))) == "2026-08"

    def test_les_statuts_et_a_corriger_l_emporte(self):
        pieces = [
            _piece("P1", etat=EtatPiece.COMPTABILISEE, date_document=date(2026, 7, 2), emetteur="ENEO"),
            _piece("P2", etat=EtatPiece.COMPTABILISEE, date_document=date(2026, 7, 9), emetteur="SABLIÈRE"),
            _piece("P3", etat=EtatPiece.ARCHIVEE, date_document=date(2026, 7, 5)),
            _piece("P4", etat=EtatPiece.RAPPROCHEE, date_document=date(2026, 7, 7), emetteur="CAMTEL"),
            _piece("P5", date_document=date(2026, 5, 7)),
        ]
        rectificative = _demande(
            identifiant="DP-9", piece_a_rectifier="P2", motif="Le NIU du fournisseur manque"
        )
        vue = vue_des_justificatifs(pieces=pieces, demandes_ouvertes=[rectificative], mois="2026-07")
        assert [(l_.identifiant, l_.statut) for l_ in vue.lignes] == [
            ("P2", StatutPourLAdherent.A_CORRIGER),
            ("P4", StatutPourLAdherent.RECU),
            ("P1", StatutPourLAdherent.ENREGISTRE),
            ("P3", StatutPourLAdherent.CLASSE),
        ]
        assert vue.lignes[0].a_corriger == "Le NIU du fournisseur manque"
        assert vue.lignes[0].demande == "DP-9"
        assert vue.mois_disponibles == ["2026-07", "2026-05"]
        assert vue.mois_precedent_avec_pieces == "2026-05"

        filtree = vue_des_justificatifs(
            pieces=pieces,
            demandes_ouvertes=[rectificative],
            mois="2026-07",
            statut=StatutPourLAdherent.ENREGISTRE,
            recherche="eneo",
        )
        assert [l_.identifiant for l_ in filtree.lignes] == ["P1"]
        seulement = vue_des_justificatifs(
            pieces=pieces, demandes_ouvertes=[rectificative], mois="2026-07",
            statut=StatutPourLAdherent.CLASSE,
        )
        assert [l_.identifiant for l_ in seulement.lignes] == ["P3"]
        # Le compte du mois ne change pas quand on filtre.
        assert filtree.total_du_mois == 4 and filtree.par_statut == vue.par_statut
        assert vue.par_statut[StatutPourLAdherent.A_CORRIGER] == 1

    def test_la_recherche_lit_aussi_le_fichier_envoye(self):
        vue = vue_des_justificatifs(
            pieces=[_piece("SCAN-7", date_document=date(2026, 7, 1))],
            demandes_ouvertes=[],
            mois="2026-07",
            recherche="scan-7",
        )
        assert [l_.identifiant for l_ in vue.lignes] == ["SCAN-7"]


# ── 4. Le bandeau ─────────────────────────────────────────────────────────────


def _attendue(demande="DP-1", attendue_pour=date(2026, 8, 12)):
    return AttendueDeLAdherent(
        demande=demande,
        libelle="Relevé",
        type_attendu="RELEVE_BANCAIRE",
        demandee_le=date(2026, 8, 1),
        attendue_pour=attendue_pour,
        en_retard=False,
        bloquante=False,
        a_corriger=False,
    )


class TestLeBandeau:
    def test_quatre_etats_et_ce_qui_est_demande_l_emporte(self):
        assert (
            etat_du_mois(attendues=[_attendue()], attentes_deduites_sans_demande=2, pieces_du_mois=0)
            is EtatDuMois.A_ENVOYER
        )
        assert (
            etat_du_mois(attendues=[], attentes_deduites_sans_demande=1, pieces_du_mois=9)
            is EtatDuMois.EN_VERIFICATION
        )
        assert (
            etat_du_mois(attendues=[], attentes_deduites_sans_demande=0, pieces_du_mois=0)
            is EtatDuMois.AUCUNE_PIECE
        )
        assert (
            etat_du_mois(attendues=[], attentes_deduites_sans_demande=0, pieces_du_mois=1)
            is EtatDuMois.COMPLET
        )

    def test_la_date_limite_est_la_plus_proche(self):
        reglages = ReglagesDeLEspaceAdherent()
        bandeau, limite = rendre_le_bandeau(
            EtatDuMois.A_ENVOYER,
            attendues=[_attendue("DP-2", date(2026, 8, 20)), _attendue("DP-1", date(2026, 8, 12))],
            nom_du_mois="juillet 2026",
            jour=date(2026, 8, 5),
            reglages=reglages,
        )
        assert limite == date(2026, 8, 12)
        assert bandeau.titre == "Il manque 2 justificatifs"
        assert "avant le 12/08/2026" in bandeau.detail

    def test_une_date_passee_ou_absente_ne_se_repete_pas(self):
        reglages = ReglagesDeLEspaceAdherent()
        passe, _ = rendre_le_bandeau(
            EtatDuMois.A_ENVOYER,
            attendues=[_attendue(attendue_pour=date(2026, 8, 1))],
            nom_du_mois="juillet 2026",
            jour=date(2026, 8, 5),
            reglages=reglages,
        )
        assert passe.titre == "Il manque 1 justificatif"
        assert "La date prévue est passée" in passe.detail and "01/08" not in passe.detail
        sans, limite = rendre_le_bandeau(
            EtatDuMois.A_ENVOYER,
            attendues=[_attendue(attendue_pour=None)],
            nom_du_mois="juillet 2026",
            jour=date(2026, 8, 5),
            reglages=reglages,
        )
        assert limite is None and "dès que possible" in sans.detail
        complet, _ = rendre_le_bandeau(
            EtatDuMois.COMPLET, attendues=[], nom_du_mois="juillet 2026", jour=date(2026, 8, 5),
            reglages=reglages,
        )
        assert complet.titre == "Votre dossier de juillet 2026 est complet"
        # L'élision, trouvée à l'essai réel.
        verification, _ = rendre_le_bandeau(
            EtatDuMois.EN_VERIFICATION, attendues=[], nom_du_mois="août 2026", jour=date(2026, 9, 5),
            reglages=reglages,
        )
        assert verification.titre == "Le cabinet vérifie votre mois d'août 2026"
        assert de_mois("octobre 2026") == "d'octobre 2026" and de_mois("mai 2026") == "de mai 2026"

    def test_les_achats_sont_nets_des_avoirs(self):
        assert achats_du_mois(
            [("601100", True, Decimal(1000)), ("601100", False, Decimal(200)), ("401", True, Decimal(5))],
            ("60",),
        ) == Decimal(800)


# ── 5. Les routes ─────────────────────────────────────────────────────────────

BATIMENT = "M081234567890P"
COLOMBE = "M071122334455J"
ADHERENT = "jp.nkoa@batimentplus.cm"
AUTRE_ADHERENT = "mc.essomba@lacolombe.cm"
COMPTABLE = "l.fotso@cga-brcg.cm"
CHARGEE = "p.moukouri@cga-brcg.cm"


@pytest.fixture(autouse=True)
def neuf():
    reinitialiser_atelier()
    vider_les_ecritures_en_memoire()
    vider_les_magasins_de_la_collecte()
    with horloge_figee(LE_5_AOUT):
        yield
    vider_les_ecritures_en_memoire()
    vider_les_magasins_de_la_collecte()


def _client(courriel):
    client = TestClient(creer_application())
    ouverture = client.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert ouverture.status_code == 200, ouverture.text
    return client


class TestLesRoutes:
    def test_l_accueil_dit_ce_qui_manque(self):
        vue = _client(ADHERENT).get(f"/pilotage/dossiers/{BATIMENT}/mon-mois").json()
        assert vue["mois"] == "2026-07" and vue["nom_du_mois"] == "juillet 2026"
        ouvertes = _client(COMPTABLE).get("/collecte/demandes", params={"entreprise": BATIMENT}).json()
        assert vue["bandeau"]["etat"] == "A_ENVOYER"
        assert vue["bandeau"]["titre"] == f"Il manque {len(ouvertes)} justificatifs"
        assert vue["date_limite"] == min(d["attendue_pour"] for d in ouvertes if d["attendue_pour"])
        assert {a["demande"] for a in vue["attendues"]} == {d["identifiant"] for d in ouvertes}
        corriger = next(a for a in vue["attendues"] if a["demande"] == "DP-2026-002")
        assert corriger["a_corriger"] and corriger["piece_a_rectifier"] == "PJ-2026-0013"
        # Le mois n'est pas revu : aucun montant comptable.
        assert vue["achats"] is None and vue["achats_arretes_le"] is None
        assert vue["mois_suivant"] is None and vue["mois_precedent"] == "2026-06"

    def test_hors_de_son_dossier(self):
        adherent = _client(ADHERENT)
        assert adherent.get(f"/pilotage/dossiers/{COLOMBE}/mon-mois").status_code == 404
        assert adherent.get(f"/collecte/dossiers/{COLOMBE}/justificatifs").status_code == 404
        autre = _client(AUTRE_ADHERENT)
        refus = autre.post(
            "/collecte/demandes/DP-2026-002/reponse", json={"nature": "INTROUVABLE"}
        )
        assert refus.status_code == 404

    def test_la_reponse_arrive_au_cabinet(self):
        adherent = _client(ADHERENT)
        reponse = adherent.post(
            "/collecte/demandes/DP-2026-002/reponse",
            json={"nature": "PLUS_TARD", "message": "Le fournisseur la refait"},
        )
        assert reponse.status_code == 201, reponse.text
        derniere = reponse.json()["derniere_reponse"]
        assert derniere["annoncee_pour"] == "2026-08-12"
        assert derniere["par"] == "A-001"  # le compte de Jean-Pierre NKOA
        assert reponse.json()["statut"] == "OUVERTE"

        double = adherent.post(
            "/collecte/demandes/DP-2026-002/reponse",
            json={"nature": "PLUS_TARD", "message": "Le fournisseur la refait"},
        )
        assert double.status_code == 409 and "déjà été reçue" in double.json()["detail"]
        vide = adherent.post("/collecte/demandes/DP-2026-002/reponse", json={"nature": "MESSAGE"})
        assert vide.status_code == 422

        # L'adhérent voit que sa réponse est arrivée.
        vue = adherent.get(f"/pilotage/dossiers/{BATIMENT}/mon-mois").json()
        attendue = next(a for a in vue["attendues"] if a["demande"] == "DP-2026-002")
        texte = "Je l'aurai la semaine prochaine · annoncée pour le 12/08/2026 · « Le fournisseur la refait »"
        assert attendue["reponse"] == texte

        # Le comptable du dossier est prévenu, et la relance le montre.
        comptable = _client(COMPTABLE)
        avis = comptable.get("/transverse/notifications").json()["notifications"]
        recu = next(n for n in avis if n["titre"].startswith("Réponse de l'adhérent"))
        assert texte in recu["texte"] and recu["lien"] == f"/pieces/relancer?dossier={BATIMENT}"
        relance = comptable.get(
            f"/pilotage/dossiers/{BATIMENT}/relance", params={"mois": "2026-07"}
        ).json()
        rectificative = next(a for a in relance["attentes"] if a["demande"] == "DP-2026-002")
        assert rectificative["reponse"] == texte
        assert rectificative["reponse_le"].startswith("2026-08-05T09:00")
        assert rectificative["regle"] and rectificative["regle"] not in rectificative["libelle"]

    def test_un_collaborateur_ne_repond_pas_a_la_place_de_l_adherent(self):
        for courriel in (COMPTABLE, CHARGEE):
            refus = _client(courriel).post(
                "/collecte/demandes/DP-2026-002/reponse", json={"nature": "INTROUVABLE"}
            )
            assert refus.status_code == 403, courriel

    def test_une_demande_classee_n_attend_plus_de_reponse(self):
        assert (
            _client(CHARGEE)
            .post(
                "/collecte/demandes/DP-2026-002/classement",
                json={"motif": "Le fournisseur a cessé son activité"},
            )
            .status_code
            == 200
        )
        refus = _client(ADHERENT).post(
            "/collecte/demandes/DP-2026-002/reponse", json={"nature": "INTROUVABLE"}
        )
        assert refus.status_code == 409 and "n'attend plus" in refus.json()["detail"]

    def test_les_justificatifs_du_mois(self):
        adherent = _client(ADHERENT)
        vue = adherent.get(
            f"/collecte/dossiers/{BATIMENT}/justificatifs", params={"mois": "2026-07"}
        ).json()
        statuts = {l_["identifiant"]: l_["statut"] for l_ in vue["lignes"]}
        assert statuts["PJ-2026-0013"] == "A_CORRIGER"
        assert vue["total_du_mois"] == len(vue["lignes"]) == sum(vue["par_statut"].values())
        assert vue["lignes"][0]["statut"] == "A_CORRIGER"
        filtree = adherent.get(
            f"/collecte/dossiers/{BATIMENT}/justificatifs",
            params={"mois": "2026-07", "statut": "A_CORRIGER", "recherche": "sablière"},
        ).json()
        assert [l_["identifiant"] for l_ in filtree["lignes"]] == ["PJ-2026-0013"]
        assert filtree["par_statut"] == vue["par_statut"]
        # Sans mois : le mois en cours.
        assert adherent.get(f"/collecte/dossiers/{BATIMENT}/justificatifs").json()["mois"] == "2026-08"
        reponses = adherent.get("/collecte/reponses-possibles").json()
        assert [r["nature"] for r in reponses["reponses"]] == ["PLUS_TARD", "INTROUVABLE"]

    def test_les_achats_d_un_mois_revu_seulement(self, monkeypatch):
        from app.contextes.comptabilite.domaine.cloture_mensuelle import PeriodeVerrouillee
        from app.contextes.comptabilite.domaine.revue import StatutRevue
        from app.contextes.pilotage.adaptateurs.entrant import routes_espace_adherent

        verrou = PeriodeVerrouillee(
            du=date(2026, 7, 1),
            au=date(2026, 7, 31),
            revue="RV-1",
            statut=StatutRevue.VALIDEE,
            depuis=datetime(2026, 8, 4, 17, 0),
            par="a.bouba",
        )
        adherent = _client(ADHERENT)
        assert adherent.get(f"/pilotage/dossiers/{BATIMENT}/mon-mois").json()["achats"] is None
        monkeypatch.setattr(
            routes_espace_adherent, "periodes_verrouillees_du_dossier", lambda niu: [verrou]
        )

        def achats():
            vue = adherent.get(f"/pilotage/dossiers/{BATIMENT}/mon-mois").json()
            return Decimal(vue["achats"]), vue["achats_arretes_le"]

        base, arretes = achats()
        assert arretes.startswith("2026-08-04T17:00")

        comptable = _client(COMPTABLE)
        saisie = comptable.post(
            f"/comptabilite/dossiers/{BATIMENT}/ecritures",
            json={
                "journal": "AC",
                "exercice": "2026",
                "date_operation": "2026-07-14",
                "libelle": "Achat de ciment",
                "piece_justificative": "F-CIM-1",
                "lignes": [
                    {"compte": "601", "libelle": "x", "sens": "DEBIT", "montant": "150000"},
                    {"compte": "401", "libelle": "x", "sens": "CREDIT", "montant": "150000"},
                ],
            },
        )
        assert saisie.status_code == 201, saisie.text
        # Un brouillon n'est pas un achat enregistré.
        assert achats()[0] == base
        numero = saisie.json()["numero"]
        assert (
            comptable.post(
                f"/comptabilite/dossiers/{BATIMENT}/ecritures/2026/AC/{numero}/validation"
            ).status_code
            == 200
        )
        assert achats()[0] == base + 150000
        # Juin n'est pas verrouillé par ce verrou-là.
        juin = adherent.get(f"/pilotage/dossiers/{BATIMENT}/mon-mois", params={"mois": "2026-06"}).json()
        assert juin["achats"] is None and juin["mois_suivant"] == "2026-07"
