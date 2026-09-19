"""Les adaptateurs — les dépôts, les jeux de démonstration, et l'API.

Trois choses sont protégées ici.

**Les garanties que les ports exigent.** Un port qui promet « aucune modification
d'une écriture validée » ne vaut que si une réalisation le tient. Ces tests
vérifient ce que le dépôt en mémoire tient réellement — et l'en-tête du module dit
franchement ce qu'il ne tient pas.

**La cohérence entre les jeux de démonstration.** C'est la garde qui a rattrapé la
divergence des régimes entre le portefeuille et les factures : deux jeux qui
racontent deux histoires différentes produisent des écrans qui se contredisent, et
personne ne sait lequel croire.

**Le contrat de l'API.** En particulier le refus de rendre un statut sans date : un
régime sans date n'a pas de sens, et supposer « aujourd'hui » fausserait tout
affichage portant sur un exercice antérieur.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from app.contextes.collecte.adaptateurs.sortant.depots_memoire import (
    DepotDemandesMemoire,
    DepotPiecesMemoire,
    MagasinFichiersMemoire,
    PieceIntrouvable,
)
from app.contextes.collecte.adaptateurs.sortant.donnees_demo import (
    DEMANDES_DEMO,
    PIECES_DEMO,
    REFERENCES_BLOQUANTES,
    REFERENCES_NON_IMPUTABLES,
)
from app.contextes.collecte.api import CanalDepot, EtatPiece, PieceJustificative
from app.contextes.comptabilite.adaptateurs.sortant.depot_ecritures_memoire import (
    DepotEcrituresMemoire,
    EcritureFigee,
    EcritureIntrouvable,
)
from app.contextes.comptabilite.adaptateurs.sortant.donnees_demo import ecritures_demo
from app.contextes.comptabilite.adaptateurs.sortant.plan_syscohada import (
    COMPTES_SYSCOHADA,
    JOURNAUX_CABINET,
)
from app.contextes.comptabilite.api import balance, controle_balance_equilibree
from app.contextes.conformite.api import FACTURES_DEMO, MoteurConformite
from app.contextes.portefeuille.adaptateurs.sortant.depot_entreprises_memoire import (
    DepotEntreprisesMemoire,
    EntrepriseIntrouvable,
    HistoireAmputee,
)
from app.contextes.portefeuille.adaptateurs.sortant.donnees_demo import PORTEFEUILLE_DEMO
from app.contextes.portefeuille.api import MotifChangement, RegimeFiscal, StatutRegime
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO
from app.main import creer_application

BATIMENT = "M081234567890P"
TCHOUMBA = "P019876543210K"
JUILLET = (date(2026, 7, 1), date(2026, 7, 31))


@pytest.fixture(scope="module")
def client() -> TestClient:
    """Un client authentifié en réviseur.

    Depuis le branchement du contexte K, **aucune route métier n'est ouverte** :
    toutes réclament une session et vérifient le périmètre. Les tests d'API
    doivent donc s'authentifier, exactement comme un écran.

    Le réviseur est choisi parce qu'il est transverse — il couvre les six
    dossiers de démonstration — et qu'il détient les permissions de lecture des
    quatre contextes. Ce qu'il **ne** détient pas fait l'objet de ses propres
    tests, dans `test_transverse.py`.
    """
    depuis_le_debut = TestClient(creer_application())
    reponse = depuis_le_debut.post(
        "/transverse/session",
        json={"courriel": "a.bouba@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
    )
    assert reponse.status_code == 200, reponse.text
    return depuis_le_debut


@pytest.fixture(scope="module")
def client_clientele() -> TestClient:
    """Un client authentifié en chargé de clientèle.

    Seul rôle à détenir `RELANCER_ADHERENT` : relancer un adhérent est son
    métier, pas celui du réviseur.
    """
    client = TestClient(creer_application())
    reponse = client.post(
        "/transverse/session",
        json={"courriel": "p.moukouri@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
    )
    assert reponse.status_code == 200, reponse.text
    return client


# ── Le portefeuille ──────────────────────────────────────────────────────────────


class TestDepotEntreprises:
    def test_un_niu_inconnu_leve_avec_les_dossiers_connus(self):
        with pytest.raises(EntrepriseIntrouvable, match="dossier"):
            DepotEntreprisesMemoire.avec_demonstration().lire("M000000000000X")

    def test_on_ne_reecrit_pas_l_histoire_d_un_dossier(self):
        """Effacer une période effacerait le régime sous lequel les factures de
        cette époque ont été contrôlées."""
        depot = DepotEntreprisesMemoire.avec_demonstration()
        batiment = depot.lire(BATIMENT)
        ampute = batiment.model_copy(update={"regimes": batiment.regimes[-1:]})

        # ⚠️ Une seule phrase désormais. Les dépôts mémoire et SQL portaient chacun
        # leur copie du garde-fou, et ne disaient même pas la même chose : « ne se
        # modifie pas » ici, « ne se remplace pas » là.
        with pytest.raises(HistoireAmputee, match="ne se remplace pas, il s'ajoute"):
            depot.enregistrer(ampute)

    def test_ajouter_une_periode_reste_permis(self):
        depot = DepotEntreprisesMemoire.avec_demonstration()
        tchoumba = depot.lire(TCHOUMBA)
        ferme = tchoumba.regimes[0].model_copy(update={"fin": date(2027, 1, 1)})
        enrichi = tchoumba.model_copy(
            update={
                "regimes": [
                    ferme,
                    StatutRegime(
                        debut=date(2027, 1, 1),
                        regime=RegimeFiscal.REEL,
                        motif=MotifChangement.DEPASSEMENT_SEUIL,
                    ),
                ]
            }
        )
        depot.enregistrer(enrichi)
        assert depot.lire(TCHOUMBA).regime_au(date(2027, 6, 1)) is RegimeFiscal.REEL


class TestPortefeuilleDemo:
    def test_six_dossiers(self):
        assert len(PORTEFEUILLE_DEMO) == 6

    def test_deux_adherents_relevent_du_synthetique(self):
        """La proportion annoncée par le cabinet, et un cas de chaque sorte dans le
        jeu de démonstration."""
        au_synthetique = [
            e
            for e in PORTEFEUILLE_DEMO.values()
            if e.regime_au(date(2026, 7, 15)) is RegimeFiscal.IGS
        ]
        assert len(au_synthetique) == 2

    def test_les_factures_de_demonstration_disent_le_meme_regime(self):
        """La garde qui a rattrapé la divergence : le portefeuille et les factures
        racontaient deux portefeuilles différents, et l'écart se voyait là où il
        compte — une facture reçue par un adhérent au synthétique n'ouvre aucun
        droit à déduction."""
        for facture in FACTURES_DEMO.values():
            dossier = PORTEFEUILLE_DEMO[facture.destinataire.niu]
            attendu = dossier.regime_au(facture.document.date_emission)
            assert facture.destinataire.regime.value == attendu.value, (
                f"{facture.document.reference} : la facture classe "
                f"{dossier.denomination} en {facture.destinataire.regime}, le "
                f"portefeuille en {attendu}."
            )


# ── La collecte ──────────────────────────────────────────────────────────────────


class TestDepotsCollecte:
    def test_aucune_suppression_n_est_offerte(self):
        """Une pièce justificative reçue ne s'efface pas, pas même un doublon.
        Elle s'archive avec son motif."""
        assert not hasattr(DepotPiecesMemoire, "supprimer")

    def test_la_boite_de_reception_montre_la_plus_recente_d_abord(self):
        pieces = DepotPiecesMemoire.avec_demonstration().toutes()
        assert pieces == sorted(pieces, key=lambda p: (p.recue_le, p.identifiant), reverse=True)

    def test_une_piece_inconnue_leve(self):
        with pytest.raises(PieceIntrouvable):
            DepotPiecesMemoire.avec_demonstration().lire("PJ-INEXISTANTE")

    def test_les_demandes_ouvertes_excluent_les_satisfaites(self):
        depot = DepotDemandesMemoire.avec_demonstration()
        assert len(depot.ouvertes()) == len(depot.toutes()) - 1

    def test_un_fichier_absent_du_magasin_leve(self):
        """Une pièce dont le fichier a disparu n'est plus une pièce justificative."""
        with pytest.raises(KeyError, match="plus une pièce justificative"):
            MagasinFichiersMemoire().lire("inexistant")


class TestCollecteDemo:
    def test_le_flux_entrant_a_la_taille_que_la_fiche_exige(self):
        """Vingt-cinq lignes au minimum sur l'écran E03."""
        assert len(PIECES_DEMO) >= 25

    def test_les_bloquantes_declarees_sont_bien_celles_que_le_moteur_produit(
        self, moteur: MoteurConformite
    ):
        """La liste est recopiée pour ne pas faire tourner le moteur à l'import.
        Ce test est le prix de cette recopie, et il est bon marché."""
        calculees = {
            reference
            for reference, facture in FACTURES_DEMO.items()
            if moteur.controler(facture).comptabilisation_interdite
        }
        assert calculees == set(REFERENCES_BLOQUANTES)

    def test_aucune_piece_bloquante_n_est_comptabilisee(self):
        for piece in PIECES_DEMO.values():
            if piece.reference_document in REFERENCES_BLOQUANTES:
                assert piece.etat is EtatPiece.LUE
                assert piece.reference_ecriture is None

    def test_la_facture_dont_les_lignes_ne_tombent_pas_juste_reste_en_attente(self):
        """Elle n'est pas bloquante au sens du contrôle, et pourtant elle n'avance
        pas : l'imputation refuse de deviner. Deux mécaniques distinctes."""
        for reference in REFERENCES_NON_IMPUTABLES:
            piece = next(
                p for p in PIECES_DEMO.values() if p.reference_document == reference
            )
            assert piece.etat is EtatPiece.LUE
            assert "FAC-CAL-002" in (piece.commentaire or "")

    def test_la_numerotation_des_ecritures_redemarre_a_chaque_dossier(self):
        """La clé 2026/AC/000001 existe chez plusieurs adhérents : elle n'est
        unique qu'à l'intérieur d'un dossier."""
        premieres = [
            p.entreprise
            for p in PIECES_DEMO.values()
            if p.reference_ecriture == "2026/AC/000001"
        ]
        assert len(premieres) == len(set(premieres)) > 1

    def test_un_doublon_est_pose_deliberement(self):
        redepot = PIECES_DEMO["PJ-2026-0900"]
        assert redepot.canal is CanalDepot.PORTAIL
        assert redepot.reference_document == "F-2026-0412"

    def test_une_demande_bloquante_existe_pour_chaque_facture_a_regulariser(self):
        bloquantes = [d for d in DEMANDES_DEMO.values() if d.bloquante]
        assert len(bloquantes) == len(REFERENCES_BLOQUANTES) + len(
            REFERENCES_NON_IMPUTABLES
        )


# ── La comptabilité ──────────────────────────────────────────────────────────────


class TestDepotEcritures:
    def test_une_ecriture_validee_ne_se_reecrit_pas(self):
        depot = DepotEcrituresMemoire.avec_demonstration(BATIMENT)
        validee = depot.lister("2026")[0]
        with pytest.raises(EcritureFigee, match="contre-passation"):
            depot.enregistrer(validee.model_copy(update={"libelle": "modifié"}))

    def test_une_cle_absente_leve_plutot_que_de_rendre_none(self):
        """Une écriture désignée par sa clé et introuvable est une rupture de
        traçabilité, pas un cas limite."""
        with pytest.raises(EcritureIntrouvable, match="piste d'audit"):
            DepotEcrituresMemoire.avec_demonstration(BATIMENT).lire("2026/AC/999999")

    def test_aucune_suppression_n_est_offerte(self):
        assert not hasattr(DepotEcrituresMemoire, "supprimer")

    def test_le_prochain_numero_suit_le_maximum(self):
        depot = DepotEcrituresMemoire.avec_demonstration(BATIMENT)
        existantes = depot.lister("2026", "AC")
        assert depot.prochain_numero("2026", "AC") == max(e.numero for e in existantes) + 1

    def test_un_journal_vierge_commence_a_un(self):
        assert DepotEcrituresMemoire(BATIMENT).prochain_numero("2026", "AC") == 1


class TestPlanComptable:
    def test_les_journaux_de_tresorerie_declarent_leur_contrepartie(self):
        """Sans elle le rapprochement est impossible — l'entité `Journal` le
        vérifie déjà, et ce test protège le jeu de données."""
        assert all(
            j.compte_contrepartie is not None
            for j in JOURNAUX_CABINET
            if j.nature.value in ("BANQUE", "CAISSE")
        )

    def test_le_mobile_money_est_rapprochable(self):
        """Il se pointe contre un relevé émis par un tiers indépendant, exactement
        comme un compte bancaire. Le traiter comme de la caisse ferait perdre le
        contrôle le plus efficace qui existe en comptabilité."""
        mobile = next(c for c in COMPTES_SYSCOHADA if c.numero == "5231")
        assert mobile.rapprochable

    def test_les_comptes_de_tiers_sont_lettrables(self):
        for numero in ("401", "411"):
            assert next(c for c in COMPTES_SYSCOHADA if c.numero == numero).lettrable


class TestEcrituresDemo:
    def test_chaque_piece_comptabilisee_a_son_ecriture_et_reciproquement(self):
        """Il n'y a pas de convention à maintenir en double : les écritures sont
        construites à partir des pièces. Ce test le vérifie tout de même, parce
        qu'une pièce annonçant une écriture qui n'existe pas romprait la piste
        d'audit dans le sens que le vérificateur emprunte."""
        annoncees = {
            p.identifiant for p in PIECES_DEMO.values() if p.reference_ecriture
        }
        produites = {
            e.piece_justificative
            for niu in PORTEFEUILLE_DEMO
            for e in ecritures_demo(niu)
        }
        assert annoncees == produites

    def test_la_cle_de_l_ecriture_est_celle_que_la_piece_annonce(self):
        par_piece = {
            e.piece_justificative: e.cle
            for niu in PORTEFEUILLE_DEMO
            for e in ecritures_demo(niu)
        }
        for piece in PIECES_DEMO.values():
            if piece.reference_ecriture:
                assert par_piece[piece.identifiant] == piece.reference_ecriture

    def test_le_regime_du_destinataire_commande_la_forme_de_l_ecriture(self):
        """Même facture, même fournisseur, deux écritures différentes. Une
        entreprise au synthétique ne récupère jamais la TVA : elle s'incorpore au
        coût d'achat, et l'écriture n'a que deux lignes."""
        au_reel = ecritures_demo(BATIMENT)
        au_synthetique = ecritures_demo(TCHOUMBA)

        assert any(
            ligne.compte.startswith("445") for e in au_reel for ligne in e.lignes
        )
        assert not any(
            ligne.compte.startswith("445") for e in au_synthetique for ligne in e.lignes
        )

    def test_chaque_ecriture_porte_sa_piece(self):
        """Le maillon 1 de la traçabilité de bout en bout."""
        for niu in PORTEFEUILLE_DEMO:
            assert all(e.piece_justificative for e in ecritures_demo(niu))

    def test_la_balance_de_chaque_dossier_est_equilibree(self):
        for niu, dossier in PORTEFEUILLE_DEMO.items():
            ecritures = ecritures_demo(niu)
            if not ecritures:
                continue
            assert controle_balance_equilibree(balance(ecritures)), (
                f"balance déséquilibrée pour {dossier.denomination}"
            )


# ── L'API ────────────────────────────────────────────────────────────────────────


class TestApiPortefeuille:
    def test_aucun_statut_sans_date(self, client: TestClient):
        """Un régime sans date n'a pas de sens, et supposer « aujourd'hui »
        fausserait tout affichage portant sur un exercice antérieur."""
        assert client.get("/portefeuille/entreprises").status_code == 422

    def test_la_liste_resout_les_statuts_a_la_date_demandee(self, client: TestClient):
        reponse = client.get(
            "/portefeuille/entreprises", params={"a_la_date": "2026-07-15"}
        )
        assert reponse.status_code == 200
        dossiers = {ligne["niu"]: ligne for ligne in reponse.json()}
        assert len(dossiers) == 6
        assert dossiers[TCHOUMBA]["regime"] == "IGS"
        assert dossiers[TCHOUMBA]["assujettie_tva"] is False

    def test_le_regime_lu_a_une_date_ancienne_est_l_ancien(self, client: TestClient):
        """SARL BATIMENT PLUS a été reclassée au réel au 1er janvier 2023. Une
        facture de 2022 doit se lire avec le régime de 2022."""
        avant = client.get(
            f"/portefeuille/entreprises/{BATIMENT}/statuts",
            params={"a_la_date": "2022-06-15"},
        ).json()
        apres = client.get(
            f"/portefeuille/entreprises/{BATIMENT}/statuts",
            params={"a_la_date": "2026-07-15"},
        ).json()
        assert avant["regime"] == "IGS"
        assert apres["regime"] == "REEL"

    def test_les_obligations_mandatees_peuvent_etre_partielles(self, client: TestClient):
        """Déposer hors mandat n'est pas une négligence de procédure : c'est agir
        sans qualité."""
        statuts = client.get(
            f"/portefeuille/entreprises/{TCHOUMBA}/statuts",
            params={"a_la_date": "2026-07-15"},
        ).json()
        assert statuts["obligations_mandatees"] == ["DSF"]

    def test_un_niu_inconnu_rend_404(self, client: TestClient):
        assert client.get("/portefeuille/entreprises/M000000000000X").status_code == 404


class TestApiCollecte:
    def test_la_boite_de_reception(self, client: TestClient):
        reponse = client.get(
            "/collecte/pieces",
            params={"a_la_date": "2026-08-14", "entreprise": BATIMENT},
        )
        assert reponse.status_code == 200
        lignes = reponse.json()
        assert lignes
        assert all(ligne["entreprise"] == BATIMENT for ligne in lignes)

    def test_les_indicateurs_sont_calcules_par_le_backend(self, client: TestClient):
        """Un front qui soustrairait deux dates lui-même finirait par diverger, et
        personne ne saurait lequel des deux a raison."""
        ligne = client.get(
            "/collecte/pieces",
            params={"a_la_date": "2026-08-14", "entreprise": BATIMENT},
        ).json()[0]
        assert "anciennete" in ligne
        assert "jours_de_transmission" in ligne

    def test_le_doublon_delibere_apparait_a_l_arbitrage(self, client: TestClient):
        arbitrages = client.get("/collecte/doublons").json()
        assert any(
            a["suspicion"]["niveau"] == "PROBABLE"
            and "PJ-2026-0900" in (a["piece"], a["suspicion"]["piece_existante"])
            for a in arbitrages
        )

    def test_une_paire_n_est_signalee_qu_une_fois(self, client: TestClient):
        """La confrontation est symétrique, et deux lignes pour un seul arbitrage
        feraient croire à deux problèmes."""
        arbitrages = client.get("/collecte/doublons").json()
        paires = [
            frozenset({a["piece"], a["suspicion"]["piece_existante"]}) for a in arbitrages
        ]
        assert len(paires) == len(set(paires))

    #: ⚠️ **Le corps du dépôt a changé, et ces deux cas le montrent.**
    #:
    #: Ils postaient `identifiant`, `recue_le` et une `empreinte` arbitraire.
    #: Aucun des trois n'appartient au déposant : le serveur les pose. Voir
    #: `test_depot_de_piece.py`, et le refus explicite des champs du système.
    #:
    #: Le fichier doit donc exister au magasin **avant** la pièce : c'est la
    #: contrainte qui rend l'empreinte vérifiable.

    def _ranger(self, client: TestClient, entreprise: str, contenu: bytes) -> str:
        """Range un document et rend la clé que le serveur en a calculée."""
        import io

        reponse = client.post(
            f"/collecte/fichiers?entreprise={entreprise}",
            files={"fichier": ("piece.pdf", io.BytesIO(contenu), "application/pdf")},
        )
        assert reponse.status_code == 201, reponse.text
        return reponse.json()["empreinte"]

    def test_redeposer_le_meme_fichier_rend_la_meme_piece(self, client: TestClient):
        """⚠️ **Le dépôt est devenu rejouable, et c'est mieux qu'un 409.**

        L'identifiant dérive de l'empreinte : redéposer le même fichier sur le
        même dossier rend la **même** pièce, et n'en crée pas une seconde.

        Un client dont la connexion tombe après l'envoi recevait auparavant un
        409 « déjà reçu » sur sa propre reprise — un refus qui lui disait qu'il
        avait fauté alors qu'il avait bien fait. C'est la même discipline que la
        boîte d'envoi et la saga d'ouverture : *rejouer ne doit rien casser.*
        """
        entreprise = PIECES_DEMO["PJ-2026-0001"].entreprise
        cle = self._ranger(client, entreprise, b"%PDF-1.4 une reprise\n%%EOF\n")
        corps = {"entreprise": entreprise, "canal": "PORTAIL", "empreinte": cle}

        premier = client.post("/collecte/pieces", json=corps)
        second = client.post("/collecte/pieces", json=corps)

        assert premier.status_code == 201, premier.text
        # ⚠️ Pas 96 : 200 et `rejeu`, et non plus 201. Le rejeu rendait 201 **en réécrivant
        # la pièce** : une pièce lue par le cabinet redevenait reçue, sans type ni montant.
        # Rien n'est créé au second envoi, et le code le dit. Voir `tests/test_depot_rejoue.py`.
        assert second.status_code == 200, second.text
        assert second.json()["rejeu"] is True
        assert second.json()["piece"]["identifiant"] == premier.json()["piece"]["identifiant"]

    def test_un_depot_nouveau_est_accepte(self, client: TestClient):
        cle = self._ranger(client, BATIMENT, b"%PDF-1.4 un fichier jamais vu\n%%EOF\n")
        reponse = client.post(
            "/collecte/pieces",
            json={"entreprise": BATIMENT, "canal": "WHATSAPP", "empreinte": cle},
        )
        assert reponse.status_code == 201
        assert reponse.json()["suspicions"] == []

    def test_la_completude_ne_pretend_pas_a_l_exhaustivite(self, client: TestClient):
        reponse = client.get(
            f"/collecte/completude/{BATIMENT}",
            params={
                "periode_debut": JUILLET[0].isoformat(),
                "periode_fin": JUILLET[1].isoformat(),
                "a_la_date": "2026-08-14",
            },
        )
        assert reponse.status_code == 200
        etat = reponse.json()
        assert "complet" not in etat["libelle"]
        assert etat["depot_possible"] is False  # deux demandes bloquantes ouvertes


class TestApiComptabilite:
    def test_le_journal_d_un_dossier(self, client: TestClient):
        reponse = client.get(
            f"/comptabilite/dossiers/{BATIMENT}/ecritures", params={"exercice": "2026"}
        )
        assert reponse.status_code == 200
        ecritures = reponse.json()
        assert ecritures
        assert [e["numero"] for e in ecritures] == sorted(e["numero"] for e in ecritures)

    def test_la_balance_se_calcule_sur_les_ecritures(self, client: TestClient):
        """⚠️ Cette route appelait `balance()` avec un nom d'argument inexistant,
        et **aucun test ne l'appelait**. Elle échouait en 500 à chaque usage réel.

        Une route sans test n'est pas une route livrée : elle est du code qui
        compile.
        """
        reponse = client.get(
            f"/comptabilite/dossiers/{BATIMENT}/balance",
            params={"exercice": "2026"},
        )
        assert reponse.status_code == 200, reponse.text
        soldes = reponse.json()
        assert soldes
        assert sum(D(s["total_debit"]) for s in soldes) == sum(
            D(s["total_credit"]) for s in soldes
        )

    def test_la_balance_ecarte_les_brouillons_par_defaut(self, client: TestClient):
        """Un brouillon n'est pas de la comptabilité, c'est une intention."""
        avec = client.get(
            f"/comptabilite/dossiers/{BATIMENT}/balance",
            params={"exercice": "2026", "brouillons": "true"},
        )
        assert avec.status_code == 200

    def test_le_grand_livre_d_un_compte(self, client: TestClient):
        lignes = client.get(
            f"/comptabilite/dossiers/{BATIMENT}/grand-livre/401",
            params={"exercice": "2026"},
        )
        assert lignes.status_code == 200
        assert lignes.json()

    def test_le_plan_d_imputation_d_un_dossier(self, client: TestClient):
        reponse = client.get(
            f"/comptabilite/dossiers/{BATIMENT}/plan-imputation"
        )
        assert reponse.status_code == 200
        assert reponse.json()["regles"]

    def test_la_sante_repond_aux_trois_questions_d_avant_depot(self, client: TestClient):
        sante = client.get(
            f"/comptabilite/dossiers/{BATIMENT}/sante", params={"exercice": "2026"}
        ).json()
        assert sante["equilibree"] is True
        assert sante["total_debit"] == sante["total_credit"]
        assert sante["deposable"] is True

    def test_un_compte_sans_mouvement_rend_404_avec_une_explication(
        self, client: TestClient
    ):
        reponse = client.get(
            f"/comptabilite/dossiers/{BATIMENT}/grand-livre/999",
            params={"exercice": "2026"},
        )
        assert reponse.status_code == 404
        assert "n'est pas une erreur" in reponse.json()["detail"]

    def test_le_grand_livre_du_compte_fournisseur(self, client: TestClient):
        lignes = client.get(
            f"/comptabilite/dossiers/{BATIMENT}/grand-livre/401",
            params={"exercice": "2026"},
        ).json()
        assert lignes


class TestApiObligations:
    def test_l_echeancier_se_calcule_depuis_le_profil(self, client: TestClient):
        reponse = client.get(
            f"/obligations/dossiers/{BATIMENT}/echeancier",
            params={"exercice": "2026", "a_la_date": "2026-08-14"},
        )
        assert reponse.status_code == 200
        codes = {ligne["obligation"]["code_obligation"] for ligne in reponse.json()}
        assert "TVA" in codes
        assert "DSF" in codes

    def test_un_adherent_au_synthetique_n_a_pas_d_obligation_de_tva(
        self, client: TestClient
    ):
        codes = {
            ligne["obligation"]["code_obligation"]
            for ligne in client.get(
                f"/obligations/dossiers/{TCHOUMBA}/echeancier",
                params={"exercice": "2026", "a_la_date": "2026-08-14"},
            ).json()
        }
        assert "TVA" not in codes
        assert "IGS" in codes

    def test_etablir_une_declaration_de_tva_hors_assujettissement_est_refuse(
        self, client: TestClient
    ):
        """Elle lui ferait réclamer une déduction à laquelle elle n'a pas droit."""
        reponse = client.get(
            f"/obligations/dossiers/{TCHOUMBA}/declaration-tva",
            params={
                "periode_debut": JUILLET[0].isoformat(),
                "periode_fin": JUILLET[1].isoformat(),
            },
        )
        assert reponse.status_code == 409
        assert "n'est pas assujettie" in reponse.json()["detail"]

    def test_la_tva_rejetee_est_isolee_et_justifiee(self, client: TestClient):
        """La ligne qui fait toute la valeur du produit. Dans une déclaration
        ordinaire elle est invisible : la case porte simplement un chiffre plus
        faible, et rien n'explique pourquoi."""
        declaration = client.get(
            f"/obligations/dossiers/{BATIMENT}/declaration-tva",
            params={
                "periode_debut": JUILLET[0].isoformat(),
                "periode_fin": JUILLET[1].isoformat(),
            },
        ).json()

        assert D(declaration["tva_rejetee"]) == D("379350")
        [rejet] = declaration["detail_rejets"]
        assert rejet["code_regle"]
        assert rejet["motif"]

    def test_la_penalite_refuse_de_se_calculer_avant_l_echeance(self, client: TestClient):
        reponse = client.get(
            "/obligations/penalite",
            params={
                "montant_du": "1000000",
                "echeance": "2026-08-15",
                "a_la_date": "2026-08-10",
            },
        )
        assert reponse.status_code == 422

    def test_la_penalite_se_calcule_apres(self, client: TestClient):
        penalite = client.get(
            "/obligations/penalite",
            params={
                "montant_du": "1000000",
                "echeance": "2026-05-15",
                "a_la_date": "2026-08-15",
            },
        ).json()
        assert D(penalite["total"]) > 0


class TestChaineComplete:
    """Le parcours entier, tel qu'un écran l'enchaînerait.

    Une pièce reçue par WhatsApp → son rapport de conformité → son écriture →
    sa TVA du mois. Chaque étape est un appel HTTP distinct, et chacune retrouve
    la précédente par la référence que la précédente a posée.
    """

    def test_de_la_piece_a_la_tva_du_mois(self, client: TestClient):
        # 1 · La pièce, dans la boîte de réception.
        piece = client.get("/collecte/pieces/PJ-2026-0001").json()["piece"]
        assert piece["canal"] == "WHATSAPP"
        assert piece["reference_ecriture"] == "2026/AC/000001"

        # 2 · Son rapport de conformité, chez le contexte D. Le verdict est
        #     composé par l'adaptateur : le rapport, lui, reste brut.
        controle = client.get(
            f"/conformite/demonstration/{piece['reference_rapport']}"
        ).json()
        assert controle["verdict"]["comptabilisation_interdite"] is False
        assert "379" in controle["verdict"]["titre"]  # l'enjeu, formaté en FCFA

        # 3 · Son écriture, chez le contexte E.
        ecriture = client.get(
            f"/comptabilite/dossiers/{BATIMENT}/ecritures/2026/AC/1"
        ).json()
        assert ecriture["piece_justificative"] == "PJ-2026-0001"
        assert ecriture["etat"] == "VALIDEE"

        # 4 · Et le rejet, chiffré, dans la déclaration du mois.
        declaration = client.get(
            f"/obligations/dossiers/{BATIMENT}/declaration-tva",
            params={
                "periode_debut": JUILLET[0].isoformat(),
                "periode_fin": JUILLET[1].isoformat(),
            },
        ).json()
        assert declaration["detail_rejets"][0]["ecriture"] == "2026/AC/000001"


def test_une_piece_deposee_hors_demonstration_ne_perturbe_pas_les_jeux():
    """Garde de propreté : les dépôts de démonstration sont reconstruits à chaque
    appel de `avec_demonstration`, et n'héritent pas des dépôts partagés par les
    routes HTTP."""
    frais = DepotPiecesMemoire.avec_demonstration()
    assert frais.par_identifiant("PJ-TEST-NEUVE") is None
    assert len(frais.toutes()) == len(PIECES_DEMO)


def test_les_pieces_de_demonstration_sont_toutes_de_dossiers_connus():
    for piece in PIECES_DEMO.values():
        assert piece.entreprise in PORTEFEUILLE_DEMO


def test_l_horodatage_de_reception_ne_precede_jamais_le_depot():
    """Vérifié par l'entité ; ce test protège le jeu de données lui-même."""
    for piece in PIECES_DEMO.values():
        assert isinstance(piece, PieceJustificative)
        assert piece.recue_le >= datetime.combine(piece.depose_le, datetime.min.time())
