"""Contexte C · Collecte — la pièce, le doublon, et la valeur qu'un humain retient.

Trois choses sont protégées ici, et ce sont les trois que la collecte apporte au
reste du système.

**Le cycle de vie est un cycle de traitement, pas de qualité.** Une pièce non
conforme se comptabilise comme les autres ; c'est la déclaration qui en tire les
conséquences. Confondre les deux rendrait impossible le cas le plus banal du
métier : une charge parfaitement comptabilisée et fiscalement réintégrée.

**Le doublon.** Aucune règle du contexte D ne peut le voir — le moteur contrôle une
facture, jamais un ensemble. Sans ce contrôle, la même facture arrivée par WhatsApp
puis par le portail fait déduire la TVA deux fois, et c'est la première chose qu'un
vérificateur retrouve.

**Une valeur lue par une machine n'est pas une valeur.** Le dernier test du fichier
déroule la chaîne entière, du dépôt WhatsApp jusqu'à l'écriture validée, en passant
par le refus explicite d'employer un montant que personne n'a retenu.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from app.contextes.collecte.api import (
    SEUIL_ACCEPTATION_AUTOMATIQUE,
    CanalDepot,
    DemandePiece,
    DepotRefuse,
    EtatPiece,
    ExtractionOCR,
    NiveauSuspicion,
    PieceJustificative,
    StatutDemande,
    TransitionRefusee,
    TypePiece,
    ValeurExtraite,
    ValeurNonRetenue,
    controler_a_reception,
    detecter_doublons,
    empreinte,
    evaluer_completude,
    identifier,
    receptionner,
    relances_du_jour,
)

NIU = "M081234567890P"
RECU_LE = datetime(2026, 7, 14, 8, 30)


def _piece(
    identifiant: str = "PJ-001",
    *,
    entreprise: str = NIU,
    canal: CanalDepot = CanalDepot.WHATSAPP,
    depose_le: date = date(2026, 7, 12),
    recue_le: datetime = RECU_LE,
    **details,
) -> PieceJustificative:
    return PieceJustificative(
        identifiant=identifiant,
        entreprise=entreprise,
        canal=canal,
        depose_le=depose_le,
        recue_le=recue_le,
        **details,
    )


def _identifiee(identifiant: str = "PJ-001", **details) -> PieceJustificative:
    base = {
        "type": TypePiece.FACTURE_ACHAT,
        "reference_document": "F-2026-0412",
        "date_document": date(2026, 7, 12),
        "montant_ttc": D("2350000"),
        "emetteur": "QUINCAILLERIE DU WOURI",
    }
    return _piece(identifiant, **{**base, **details})


# ── La pièce et son cycle de vie ─────────────────────────────────────────────────


class TestPieceJustificative:
    def test_une_piece_ne_peut_pas_arriver_avant_d_avoir_ete_envoyee(self):
        with pytest.raises(ValidationError, match="antérieure au dépôt déclaré"):
            _piece(depose_le=date(2026, 7, 20), recue_le=datetime(2026, 7, 14, 8, 30))

    def test_le_delai_de_transmission_mesure_l_ecart_entre_les_deux_dates(self):
        """Le mode hors ligne rend cet écart signifiant : une pièce photographiée
        sans réseau et synchronisée deux jours plus tard n'est pas un retard de
        l'adhérent, et une seule date le ferait disparaître."""
        assert _piece().jours_de_transmission() == 2

    def test_un_depot_en_ligne_ne_montre_aucun_delai_de_transmission(self):
        piece = _piece(
            canal=CanalDepot.PORTAIL,
            depose_le=date(2026, 7, 14),
            recue_le=datetime(2026, 7, 14, 8, 30),
        )
        assert piece.jours_de_transmission() == 0

    def test_le_retard_de_remise_oppose_la_date_du_document_a_celle_de_reception(self):
        """C'est la mesure qu'on oppose à l'adhérent qui reproche au cabinet un
        dépôt tardif : une facture de mars remise en juin ne pouvait pas figurer
        dans la déclaration de mars."""
        piece = _identifiee(
            date_document=date(2026, 3, 8),
            depose_le=date(2026, 6, 19),
            recue_le=datetime(2026, 6, 20, 9, 0),
        )
        assert piece.retard_de_remise() == 104

    def test_le_retard_de_remise_est_inconnu_tant_que_la_piece_ne_l_est_pas(self):
        assert _piece().retard_de_remise() is None

    def test_une_piece_non_identifiee_ne_peut_pas_etre_marquee_lue(self):
        with pytest.raises(TransitionRefusee, match="lecture impossible"):
            _piece().marquer_lue()

    def test_le_message_de_refus_enumere_ce_qui_manque(self):
        with pytest.raises(TransitionRefusee) as erreur:
            _piece(type=TypePiece.FACTURE_ACHAT).marquer_lue()
        message = str(erreur.value)
        assert "reference_document" in message
        assert "montant_ttc" in message
        assert "type" not in message.split("il manque")[1]

    def test_une_piece_identifiee_passe_a_lue(self):
        assert _identifiee().marquer_lue().etat is EtatPiece.LUE

    def test_on_ne_revient_pas_en_arriere_dans_le_cycle(self):
        lue = _identifiee().marquer_lue()
        with pytest.raises(TransitionRefusee, match="on ne revient pas"):
            lue.avancer(EtatPiece.RECUE)

    def test_une_piece_comptabilisee_porte_la_cle_de_son_ecriture(self):
        with pytest.raises(ValidationError, match="piste d'audit"):
            _identifiee(etat=EtatPiece.COMPTABILISEE)

    def test_une_piece_rapprochee_porte_la_reference_du_mouvement(self):
        with pytest.raises(ValidationError, match="référence du mouvement"):
            _identifiee(
                etat=EtatPiece.RAPPROCHEE, reference_rapprochement=None
            )

    def test_archiver_une_piece_jamais_comptabilisee_exige_un_motif(self):
        with pytest.raises(ValidationError, match="exige un motif"):
            _identifiee(etat=EtatPiece.ARCHIVEE)

    def test_une_piece_comptabilisee_s_archive_sans_motif(self):
        """Son écriture explique déjà tout : exiger un motif ici serait une
        formalité vide, et une formalité vide se remplit par « RAS »."""
        piece = _identifiee().marquer_lue().comptabiliser("2026/AC/000012")
        assert piece.archiver().etat is EtatPiece.ARCHIVEE

    def test_un_contrat_ne_se_comptabilise_pas(self):
        """Un contrat de bail justifie des écritures, il n'en est pas une."""
        piece = _identifiee(type=TypePiece.CONTRAT).marquer_lue()
        with pytest.raises(TransitionRefusee, match="ne produit pas"):
            piece.comptabiliser("2026/OD/000001")

    def test_une_piece_traitee_sort_du_travail_a_faire(self):
        piece = _identifiee().marquer_lue().comptabiliser("2026/AC/000012")
        assert piece.traitee
        assert not piece.en_attente_de_traitement

    def test_l_anciennete_se_compte_depuis_la_reception(self):
        """C'est l'indicateur d'enlisement : ni la date du document, ni celle du
        dépôt déclaré, mais le temps passé chez le cabinet."""
        assert _piece().anciennete(date(2026, 8, 14)) == 31


# ── Le doublon ───────────────────────────────────────────────────────────────────


class TestDoublons:
    def test_meme_empreinte_donne_une_certitude(self):
        contenu = b"%PDF-1.4 facture quincaillerie"
        existante = _identifiee("PJ-001", empreinte=empreinte(contenu))
        arrivante = _identifiee("PJ-002", empreinte=empreinte(contenu))

        [suspicion] = detecter_doublons(arrivante, [existante])
        assert suspicion.niveau is NiveauSuspicion.CERTAIN
        assert suspicion.bloquant
        assert suspicion.piece_existante == "PJ-001"

    def test_meme_document_photographie_deux_fois_reste_une_probabilite(self):
        """Le cas fréquent, et celui qu'aucune comparaison d'empreintes ne voit :
        deux photographies du même papier n'ont pas le même fichier."""
        existante = _identifiee("PJ-001", empreinte="aaa" * 21 + "a")
        arrivante = _identifiee("PJ-002", canal=CanalDepot.PORTAIL, empreinte="bbb" * 21 + "b")

        [suspicion] = detecter_doublons(arrivante, [existante])
        assert suspicion.niveau is NiveauSuspicion.PROBABLE
        assert not suspicion.bloquant
        assert suspicion.montant_en_jeu == D("2350000")

    def test_les_trois_indices_sont_exiges_ensemble(self):
        """Deux suffiraient à produire des faux positifs en série : un fournisseur
        émet beaucoup de factures du même montant."""
        existante = _identifiee("PJ-001")
        autre_montant = _identifiee("PJ-002", montant_ttc=D("1200000"))
        assert detecter_doublons(autre_montant, [existante]) == []

    def test_deux_dossiers_differents_ne_se_confrontent_pas(self):
        """Deux clients d'un même fournisseur reçoivent des factures distinctes."""
        existante = _identifiee("PJ-001", entreprise="P019876543210K")
        arrivante = _identifiee("PJ-002")
        assert detecter_doublons(arrivante, [existante]) == []

    def test_une_piece_archivee_reste_dans_le_champ_de_la_comparaison(self):
        """Elle a été traitée : c'est justement pour cela qu'un second exemplaire
        serait un doublon. L'exclure serait la faute que ce module doit empêcher."""
        archivee = (
            _identifiee("PJ-001").marquer_lue().comptabiliser("2026/AC/000012").archiver()
        )
        [suspicion] = detecter_doublons(_identifiee("PJ-002"), [archivee])
        assert suspicion.niveau is NiveauSuspicion.PROBABLE

    def test_l_indice_dit_que_la_piece_a_deja_ete_comptabilisee(self):
        comptabilisee = _identifiee("PJ-001").marquer_lue().comptabiliser("2026/AC/000012")
        [suspicion] = detecter_doublons(_identifiee("PJ-002"), [comptabilisee])
        assert any("2026/AC/000012" in indice for indice in suspicion.indices)

    def test_une_piece_ne_se_confronte_pas_a_elle_meme(self):
        piece = _identifiee("PJ-001")
        assert detecter_doublons(piece, [piece]) == []

    def test_le_certain_passe_devant(self):
        contenu = b"identique"
        stock = [
            _identifiee("PJ-001", empreinte="zzz" * 21 + "z"),
            _identifiee("PJ-002", empreinte=empreinte(contenu)),
        ]
        suspicions = detecter_doublons(_identifiee("PJ-003", empreinte=empreinte(contenu)), stock)
        assert [s.niveau for s in suspicions] == [
            NiveauSuspicion.CERTAIN,
            NiveauSuspicion.PROBABLE,
        ]


class TestReception:
    def test_le_meme_fichier_est_refuse_a_l_entree(self):
        contenu = b"%PDF-1.4 facture quincaillerie"
        existante = _identifiee("PJ-001", empreinte=empreinte(contenu))
        with pytest.raises(DepotRefuse, match="PJ-001"):
            receptionner(_identifiee("PJ-002", empreinte=empreinte(contenu)), [existante])

    def test_le_refus_est_formule_pour_etre_relaye_a_l_adherent(self):
        """« Erreur lors du dépôt » le ferait réessayer indéfiniment."""
        contenu = b"x"
        existante = _identifiee("PJ-001", empreinte=empreinte(contenu))
        with pytest.raises(DepotRefuse) as erreur:
            receptionner(_identifiee("PJ-002", empreinte=empreinte(contenu)), [existante])
        assert "déjà été reçu" in str(erreur.value)

    def test_le_doublon_probable_est_accepte_et_signale(self):
        """Refuser automatiquement ferait perdre une charge déductible le jour où
        un fournisseur réutilise ses numéros d'une année sur l'autre."""
        existante = _identifiee("PJ-001", empreinte="a" * 64)
        resultat = receptionner(_identifiee("PJ-002", empreinte="b" * 64), [existante])

        assert resultat.a_arbitrer
        assert resultat.montant_du_double_emploi == D("2350000")

    def test_une_piece_sans_precedent_entre_sans_reserve(self):
        resultat = receptionner(_identifiee("PJ-002"), [])
        assert not resultat.a_arbitrer
        assert resultat.montant_du_double_emploi == 0


# ── L'extraction, et la validation humaine ───────────────────────────────────────


def _valeur(champ: str, valeur, confiance: str) -> ValeurExtraite:
    return ValeurExtraite(champ=champ, valeur_lue=valeur, confiance=D(confiance))


class TestExtraction:
    def test_un_montant_n_est_jamais_accepte_automatiquement_meme_a_cent_pour_cent(self):
        """Le score dit « j'ai bien lu ces caractères », pas « ce montant est juste ».
        L'erreur, elle, part dans la déclaration de TVA du mois."""
        montant = _valeur("montant_ttc", D("2350000"), "1.00")
        assert montant.validation_obligatoire
        assert not montant.exploitable

    def test_le_niu_non_plus(self):
        assert _valeur("niu_emetteur", "M053311224455R", "1.00").validation_obligatoire

    def test_un_champ_ordinaire_bien_lu_passe_sans_relecture(self):
        libelle = _valeur("reference_document", "F-2026-0412", "0.97")
        assert libelle.confiance >= SEUIL_ACCEPTATION_AUTOMATIQUE
        assert libelle.exploitable
        assert libelle.valeur == "F-2026-0412"

    def test_un_champ_ordinaire_mal_lu_attend_un_humain(self):
        emetteur = _valeur("emetteur", "QUINCAILLERIE DU WOURl", "0.72")
        assert not emetteur.exploitable
        with pytest.raises(ValeurNonRetenue, match="confiance 0.72"):
            _ = emetteur.valeur

    def test_la_valeur_non_retenue_leve_plutot_que_d_avertir(self):
        """Un avertissement se contourne par distraction ; une exception, non."""
        with pytest.raises(ValeurNonRetenue, match="validation obligatoire"):
            _ = _valeur("montant_ttc", D("2350000"), "0.99").valeur

    def test_confirmer_ne_marque_pas_de_correction(self):
        confirmee = _valeur("montant_ttc", D("2350000"), "0.99").confirmer(
            par="Rodrigue BIYA'A", le=datetime(2026, 7, 14, 10, 0)
        )
        assert confirmee.validee
        assert not confirmee.corrigee
        assert confirmee.valeur == D("2350000")

    def test_corriger_est_trace_comme_tel(self):
        """Le compteur des corrections est la seule mesure honnête de la qualité du
        moteur d'extraction. La confiance moyenne, elle, ne mesure que la netteté
        des images reçues."""
        corrigee = _valeur("montant_ttc", D("2530000"), "0.99").retenir(
            D("2350000"), par="Rodrigue BIYA'A", le=datetime(2026, 7, 14, 10, 0)
        )
        assert corrigee.corrigee
        assert corrigee.valeur == D("2350000")

    def test_les_champs_a_relire_posent_les_sensibles_en_premier(self):
        extraction = ExtractionOCR(
            piece="PJ-001",
            moteur="ocr-demo",
            extraite_le=datetime(2026, 7, 14, 9, 0),
            champs={
                "emetteur": _valeur("emetteur", "QUINCAILLERIE", "0.60"),
                "montant_ttc": _valeur("montant_ttc", D("2350000"), "0.99"),
            },
        )
        assert extraction.champs_a_relire == ["montant_ttc", "emetteur"]
        assert not extraction.exploitable

    def test_confirmer_tout_est_offert_trace_et_nominatif(self):
        """Le refuser conduirait à le contourner : un comptable qui doit valider
        quinze champs un par un finira par cliquer quinze fois sans lire."""
        extraction = ExtractionOCR(
            piece="PJ-001",
            moteur="ocr-demo",
            extraite_le=datetime(2026, 7, 14, 9, 0),
            champs={"montant_ttc": _valeur("montant_ttc", D("2350000"), "0.99")},
        ).confirmer_tout(par="Rodrigue BIYA'A", le=datetime(2026, 7, 14, 10, 0))

        assert extraction.exploitable
        assert extraction.champs["montant_ttc"].retenue_par == "Rodrigue BIYA'A"

    def test_un_champ_absent_se_saisit_plutot_qu_il_ne_se_suppose(self):
        extraction = ExtractionOCR(
            piece="PJ-001", moteur="ocr-demo", extraite_le=datetime(2026, 7, 14, 9, 0)
        )
        with pytest.raises(ValeurNonRetenue, match="plutôt que le supposer"):
            extraction.valeur("montant_ttc")


class TestIdentification:
    def _extraction(self) -> ExtractionOCR:
        return ExtractionOCR(
            piece="PJ-001",
            moteur="ocr-demo",
            extraite_le=datetime(2026, 7, 14, 9, 0),
            champs={
                "reference_document": _valeur("reference_document", "F-2026-0412", "0.97"),
                "date_emission": _valeur("date_emission", date(2026, 7, 12), "0.93"),
                "montant_ttc": _valeur("montant_ttc", D("2350000"), "0.99"),
                "emetteur": _valeur("emetteur", "QUINCAILLERIE DU WOURI", "0.94"),
            },
        )

    def test_sans_validation_du_montant_la_piece_reste_dans_le_travail_a_faire(self):
        with pytest.raises(TransitionRefusee, match="montant_ttc"):
            identifier(_piece(), self._extraction(), type_confirme=TypePiece.FACTURE_ACHAT)

    def test_une_extraction_ne_s_applique_qu_a_sa_propre_piece(self):
        """Un rapprochement de ce genre passerait les données d'un adhérent sur le
        dossier d'un autre."""
        with pytest.raises(ValueError, match="dossier d'un autre"):
            identifier(_piece("PJ-999"), self._extraction())

    def test_apres_validation_la_piece_est_identifiee_et_lue(self):
        extraction = self._extraction().confirmer_tout(
            par="Rodrigue BIYA'A", le=datetime(2026, 7, 14, 10, 0)
        )
        piece = identifier(_piece(), extraction, type_confirme=TypePiece.FACTURE_ACHAT)

        assert piece.etat is EtatPiece.LUE
        assert piece.identifiee
        assert piece.reference_document == "F-2026-0412"
        assert piece.montant_ttc == D("2350000")


# ── Les demandes de pièces ───────────────────────────────────────────────────────


def _demande(identifiant: str = "DP-001", **details) -> DemandePiece:
    base = {
        "entreprise": NIU,
        "type_attendu": TypePiece.FACTURE_ACHAT,
        "motif": "Facture du fournisseur citée sur le relevé bancaire du 12 juillet",
        "demandee_le": date(2026, 7, 20),
    }
    return DemandePiece(identifiant=identifiant, **{**base, **details})


class TestDemandes:
    def test_une_piece_ne_peut_pas_etre_attendue_avant_d_etre_demandee(self):
        with pytest.raises(ValidationError, match="avant d'avoir été demandée"):
            _demande(attendue_pour=date(2026, 7, 10))

    def test_classer_sans_suite_exige_un_motif(self):
        """C'est ce qui distingue une décision d'un abandon."""
        with pytest.raises(ValidationError, match="exige un motif"):
            _demande(statut=StatutDemande.CLASSEE_SANS_SUITE)

    def test_le_retard_se_calcule_et_ne_se_stocke_pas(self):
        demande = _demande(attendue_pour=date(2026, 8, 10))
        assert not demande.en_retard(date(2026, 8, 10))
        assert demande.en_retard(date(2026, 8, 11))

    def test_une_demande_satisfaite_n_est_jamais_en_retard(self):
        demande = _demande(attendue_pour=date(2026, 8, 10)).satisfaire(
            "PJ-042", date(2026, 8, 14)
        )
        assert not demande.en_retard(date(2026, 9, 1))

    def test_les_relances_sont_tracees_avec_leur_canal(self):
        """Une relance non tracée n'a pas eu lieu. Le jour où l'adhérent conteste
        une pénalité de retard, cette liste est la défense du Centre."""
        demande = _demande().relancer(date(2026, 7, 27), CanalDepot.WHATSAPP)
        assert demande.relances == ((date(2026, 7, 27), CanalDepot.WHATSAPP),)

    def test_on_ne_relance_pas_une_demande_satisfaite(self):
        """C'est la première cause d'exaspération d'un adhérent, et elle
        décrédibilise toutes les relances suivantes."""
        demande = _demande().satisfaire("PJ-042", date(2026, 7, 25))
        with pytest.raises(ValueError, match="plus rien à relancer"):
            demande.relancer(date(2026, 7, 27), CanalDepot.WHATSAPP)

    def test_les_relances_du_jour_tombent_sur_les_jalons_et_nulle_part_ailleurs(self):
        demande = _demande()
        assert relances_du_jour([demande], date(2026, 7, 27)) != []  # J+7
        assert relances_du_jour([demande], date(2026, 7, 26)) == []
        assert relances_du_jour([demande], date(2026, 8, 4)) != []  # J+15

    def test_le_canal_prefere_de_l_adherent_l_emporte(self):
        """Relancer par courriel un artisan qui ne consulte que WhatsApp revient à
        ne pas relancer, tout en produisant la trace qui laisse croire qu'on l'a
        fait — c'est pire que de ne rien envoyer."""
        [relance] = relances_du_jour(
            [_demande()],
            date(2026, 7, 27),
            canal_par_entreprise={NIU: CanalDepot.COURRIEL},
        )
        assert relance.canal is CanalDepot.COURRIEL

    def test_a_trente_jours_ce_n_est_plus_un_oubli(self):
        [relance] = relances_du_jour([_demande()], date(2026, 8, 19))
        assert relance.jalon == 30
        assert relance.escalade
        assert _demande().a_escalader(date(2026, 8, 19))


# ── La complétude ────────────────────────────────────────────────────────────────


class TestCompletude:
    def _evaluer(self, pieces, demandes, a_la_date=date(2026, 8, 5)):
        return evaluer_completude(
            NIU,
            pieces,
            demandes,
            periode_debut=date(2026, 7, 1),
            periode_fin=date(2026, 7, 31),
            a_la_date=a_la_date,
        )

    def test_une_demande_bloquante_ouverte_empeche_le_depot(self):
        completude = self._evaluer([], [_demande(bloquante=True)])
        assert not completude.depot_possible
        assert completude.demandes_bloquantes_ouvertes == ["DP-001"]

    def test_dix_demandes_non_bloquantes_n_empechent_rien(self):
        """Elles pèsent sur la qualité du dossier, pas sur la possibilité de
        déclarer."""
        demandes = [_demande(f"DP-{n:03d}") for n in range(1, 11)]
        completude = self._evaluer([], demandes)
        assert completude.depot_possible
        assert completude.demandes_ouvertes == 10

    def test_le_taux_est_indefini_quand_rien_n_a_ete_demande(self):
        """Un taux de 100 % calculé sur zéro attente serait le plus trompeur de
        tous."""
        assert self._evaluer([_identifiee()], []).attentes_satisfaites is None

    def test_le_taux_ne_porte_que_sur_les_attentes_exprimees(self):
        demandes = [
            _demande("DP-001").satisfaire("PJ-001", date(2026, 7, 25)),
            _demande("DP-002"),
        ]
        assert self._evaluer([], demandes).attentes_satisfaites == D("0.50")

    def test_le_libelle_ne_dit_jamais_que_le_dossier_est_complet(self):
        completude = self._evaluer(
            [_identifiee().marquer_lue().comptabiliser("2026/AC/000012")],
            [_demande().satisfaire("PJ-001", date(2026, 7, 25))],
        )
        assert completude.libelle == "Tout ce qui était attendu est arrivé et traité"
        assert "complet" not in completude.libelle

    def test_la_plus_ancienne_en_souffrance_revele_le_dossier_enlise(self):
        """Un dossier peut afficher 100 % de pièces traitées et n'avoir rien reçu
        depuis quatre mois : c'est cet indicateur-là qui le montre."""
        completude = self._evaluer([_identifiee()], [])
        assert completude.pieces_en_souffrance == ["PJ-001"]
        assert completude.plus_ancienne_en_souffrance == 22

    def test_les_pieces_sont_retenues_sur_leur_date_de_reception(self):
        """Une facture de mars reçue en juillet appartient au travail de juillet."""
        completude = self._evaluer([_identifiee(date_document=date(2026, 3, 8))], [])
        assert completude.pieces_recues == 1


# ── La chaîne complète : dépôt WhatsApp → contrôle → écriture ────────────────────


def _moteur():
    from app.contextes.conformite.adaptateurs.sortant.depot_regles_yaml import (
        DepotReglesYaml,
    )
    from app.contextes.conformite.application.moteur_conformite import MoteurConformite
    from app.contextes.referentiel.api import DepotParametresYaml, ServiceParametres
    from app.infrastructure.config import RACINE_DEPOT

    referentiel = RACINE_DEPOT / "Docs" / "referentiel"
    return MoteurConformite(
        regles=DepotReglesYaml(referentiel / "regles").charger(),
        parametres=ServiceParametres.depuis_depot(
            DepotParametresYaml(referentiel / "parametres.yaml")
        ),
    )


class TestChaineComplete:
    """Du dépôt WhatsApp jusqu'à l'écriture validée, sans rien simuler.

    C'est le parcours E03 → E02 → E10 des maquettes, pris par son commencement :
    la facture n'apparaît plus par magie dans une constante de démonstration, elle
    entre par le canal qu'un artisan de Bonabéri emploiera réellement.
    """

    def test_le_parcours_entier(self):
        from app.contextes.comptabilite.api import (
            EtatEcriture,
            PlanImputation,
            proposer_ecriture_achat,
        )
        from app.contextes.conformite.adaptateurs.sortant.donnees_demo import FACTURES_DEMO

        facture = FACTURES_DEMO["F-2026-0412"]

        # 1 · La pièce arrive par WhatsApp, deux jours après avoir été photographiée.
        brute = _piece("PJ-2026-0731", empreinte=empreinte(b"photo-whatsapp-0412"))
        reception = receptionner(brute, [])
        assert not reception.a_arbitrer

        # 2 · L'OCR lit, mais rien n'est encore une valeur.
        extraction = ExtractionOCR(
            piece="PJ-2026-0731",
            moteur="ocr-demo",
            extraite_le=datetime(2026, 7, 14, 9, 0),
            champs={
                "reference_document": _valeur("reference_document", "F-2026-0412", "0.96"),
                "date_emission": _valeur("date_emission", facture.document.date_emission, "0.93"),
                # Le moteur se trompe d'un chiffre, avec 99 % de confiance.
                "montant_ttc": _valeur("montant_ttc", D("2530000"), "0.99"),
                "emetteur": _valeur("emetteur", facture.emetteur.denomination, "0.95"),
            },
        )
        with pytest.raises(TransitionRefusee):
            identifier(reception.piece, extraction, type_confirme=TypePiece.FACTURE_ACHAT)

        # 3 · Le comptable relit et corrige — c'est là que le montant devient vrai.
        extraction = extraction.retenir(
            "montant_ttc",
            facture.montants.total_ttc,
            par="Rodrigue BIYA'A",
            le=datetime(2026, 7, 14, 10, 0),
        ).confirmer_tout(par="Rodrigue BIYA'A", le=datetime(2026, 7, 14, 10, 0))
        assert extraction.champs["montant_ttc"].corrigee

        lue = identifier(reception.piece, extraction, type_confirme=TypePiece.FACTURE_ACHAT)
        assert lue.etat is EtatPiece.LUE

        # 4 · Le contrôle part tout de suite : c'est le seul moment où la facture
        #     est encore corrigeable par le fournisseur.
        controle = controler_a_reception(lue, facture, _moteur())
        assert controle.correction_a_demander
        assert controle.rapport.enjeu_total == D("379350")
        assert not controle.comptabilisation_interdite
        assert controle.piece.reference_rapport == "F-2026-0412"

        # 5 · L'écriture est proposée, validée, et la pièce lui est rattachée.
        ecriture = proposer_ecriture_achat(
            facture,
            controle.rapport,
            PlanImputation(compte_charge_par_defaut="604"),
            journal="AC",
            exercice="2026",
            numero=12,
        ).valider("Rodrigue BIYA'A", datetime(2026, 7, 14, 11, 0))
        assert ecriture.etat is EtatEcriture.VALIDEE

        comptabilisee = controle.piece.comptabiliser(ecriture.cle)
        assert comptabilisee.reference_ecriture == "2026/AC/000012"
        assert comptabilisee.traitee

        # 6 · Trois jours plus tard, l'adhérent redépose la même facture sur le
        #     portail. Fichier différent, facture identique : sans ce contrôle, la
        #     TVA serait déduite deux fois.
        redepot = _piece(
            "PJ-2026-0774",
            canal=CanalDepot.PORTAIL,
            depose_le=date(2026, 7, 17),
            recue_le=datetime(2026, 7, 17, 14, 0),
            type=TypePiece.FACTURE_ACHAT,
            reference_document="F-2026-0412",
            date_document=facture.document.date_emission,
            montant_ttc=facture.montants.total_ttc,
            emetteur=facture.emetteur.denomination,
            empreinte=empreinte(b"scan-portail-0412"),
        )
        resultat = receptionner(redepot, [comptabilisee])

        assert resultat.a_arbitrer
        assert resultat.montant_du_double_emploi == facture.montants.total_ttc
        [suspicion] = resultat.suspicions
        assert suspicion.niveau is NiveauSuspicion.PROBABLE
        assert any("2026/AC/000012" in indice for indice in suspicion.indices)

        # 7 · Et le même fichier, lui, n'entre même pas.
        with pytest.raises(DepotRefuse):
            receptionner(
                _piece("PJ-2026-0801", empreinte=empreinte(b"photo-whatsapp-0412")),
                [comptabilisee],
            )
