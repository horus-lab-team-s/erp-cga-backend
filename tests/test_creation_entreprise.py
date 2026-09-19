"""Contexte I · Création d'entreprise.

Ce que ces tests protègent, dans l'ordre où un défaut coûte le plus cher :

1. **La conversion** — c'est le seul geste qui fait naître une entreprise au
   portefeuille, et une conversion fautive produit un contribuable dont les
   obligations commencent au mauvais jour. Personne ne s'en aperçoit avant la
   première pénalité de retard.
2. **Le tunnel** — un dossier déposé au guichet sans être constitué revient, et
   il revient trois jours plus tard.
3. **La checklist** — une pièce oubliée, c'est un aller-retour au CFCE.
4. **Le diagnostic** — et surtout le fait qu'un chiffre non validé *informe*
   sans *interdire*.

Aucun test ne monte de base : les cas d'usage reçoivent leur date et leur dépôt.
C'est ce qui les rend lisibles, et c'est aussi ce qui a permis, ailleurs sur ce
projet, de découvrir qu'un cas d'usage lisant l'horloge murale casse la suite à
minuit.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.contextes.creation_entreprise.api import (
    PIPELINE_DEMO,
    ConversionImpossible,
    DepotDossiersCreationMemoire,
    DossierCreationIntrouvable,
    EtapeCreation,
    Fondateur,
    TransitionInterdite,
    abandonner,
    avancer,
    checklist_de,
    convertir,
    depasse_le_delai_annonce,
    diagnostiquer,
    enregistrer_identifiant,
    etapes_ouvertes_depuis,
    fournir_piece,
    ouvrir_dossier,
)
from app.contextes.portefeuille.api import (
    CentreRattachement,
    FormeJuridique,
    RegimeFiscal,
)
from app.contextes.referentiel.api import DepotParametresYaml, ServiceParametres
from app.infrastructure.config import configuration

JOUR = date(2026, 8, 17)


@pytest.fixture(scope="module")
def parametres() -> ServiceParametres:
    return ServiceParametres.depuis_depot(
        DepotParametresYaml(configuration().dossier_referentiel / "parametres.yaml")
    )


def _fondateur() -> Fondateur:
    return Fondateur(
        nom="MOMHA", prenom="Estelle", courriel="e@exemple.cm", telephone="+237699000000"
    )


def _dossier(
    forme: FormeJuridique = FormeJuridique.SARL,
    capital: Decimal | None = Decimal("1000000"),
    a_la_date: date = JOUR,
):
    return ouvrir_dossier(
        reference="CR-TEST-01",
        fondateur=_fondateur(),
        denomination_souhaitee="ALPHA SARL",
        forme_juridique=forme,
        activite="Commerce",
        siege="Douala",
        capital=capital,
        a_la_date=a_la_date,
    )


def _tout_fournir(dossier, le: date = JOUR):
    for piece in dossier.pieces:
        dossier = fournir_piece(dossier, piece.code, le)
    return dossier


def _mener_jusqu_a_livraison(dossier, *, rccm_le: date, niu_le: date):
    """Conduit un dossier neuf jusqu'à l'étape de livraison.

    Les tests de conversion ont tous besoin de ce préambule ; l'écrire une fois
    évite qu'ils divergent sur l'ordre des étapes, qui est justement ce que le
    tunnel garantit.
    """
    dossier = _tout_fournir(dossier)
    dossier = avancer(dossier, EtapeCreation.CONSTITUTION, JOUR)
    dossier = avancer(dossier, EtapeCreation.DEPOT_CFCE, JOUR)
    dossier = avancer(dossier, EtapeCreation.SUIVI_IMMATRICULATION, JOUR)
    dossier = enregistrer_identifiant(
        dossier,
        rccm=("RC/DLA/2026/B/0001", rccm_le),
        niu=("M100000000001X", niu_le),
    )
    return avancer(dossier, EtapeCreation.LIVRAISON, JOUR)


# ══ La checklist ══════════════════════════════════════════════════════════════
class TestChecklist:
    def test_l_entreprise_individuelle_n_a_ni_statuts_ni_associes(self):
        """C'est ce qui en fait le produit d'appel : le dossier le plus court."""
        codes = {p.code for p in checklist_de(FormeJuridique.ETS)}
        assert "STATUTS" not in codes
        assert "LISTE_ASSOCIES" not in codes
        assert "ID_FONDATEUR" in codes

    def test_la_sa_seule_exige_un_commissaire_aux_comptes(self):
        """C'est ce qui explique l'écart de prix avec la SARL.

        Le fondateur doit le découvrir sur le devis, pas au guichet.
        """
        avec = {
            forme
            for forme in FormeJuridique
            if "COMMISSAIRE_COMPTES" in {p.code for p in checklist_de(forme)}
        }
        assert avec == {FormeJuridique.SA}

    def test_la_sarlu_n_exige_ni_liste_d_associes_ni_pv_de_nomination(self):
        """Un associé unique se nomme lui-même dans les statuts."""
        codes = {p.code for p in checklist_de(FormeJuridique.SARLU)}
        assert "LISTE_ASSOCIES" not in codes
        assert "PV_NOMINATION" not in codes
        assert "STATUTS" in codes

    def test_une_forme_non_repertoriee_rend_le_socle_plutot_que_de_lever(self):
        """Une case manquante dans une table ne doit pas faire perdre un client."""
        codes = {p.code for p in checklist_de(FormeJuridique.ASSOCIATION)}
        assert "ID_FONDATEUR" in codes

    def test_la_checklist_est_figee_a_l_ouverture(self):
        """Un dossier instruit sous une liste reste lisible sous cette liste.

        La recalculer à l'affichage ferait apparaître, du jour au lendemain, une
        pièce jamais demandée au fondateur.
        """
        dossier = _dossier()
        assert dossier.pieces == checklist_de(FormeJuridique.SARL)


# ══ Le tunnel ═════════════════════════════════════════════════════════════════
class TestTunnel:
    def test_on_n_avance_que_d_un_cran(self):
        dossier = _dossier()
        ouvertes = etapes_ouvertes_depuis(EtapeCreation.QUALIFICATION)
        assert ouvertes == {EtapeCreation.CONSTITUTION, EtapeCreation.ABANDONNE}
        with pytest.raises(TransitionInterdite, match="n'est pas atteignable"):
            avancer(dossier, EtapeCreation.DEPOT_CFCE, JOUR)

    def test_on_ne_revient_pas_en_arriere(self):
        """Un dossier déposé qui reviendrait en constitution laisserait croire
        qu'il n'a jamais été déposé — et le délai du guichet serait perdu."""
        dossier = avancer(_tout_fournir(_dossier()), EtapeCreation.CONSTITUTION, JOUR)
        dossier = avancer(dossier, EtapeCreation.DEPOT_CFCE, JOUR)
        with pytest.raises(TransitionInterdite):
            avancer(dossier, EtapeCreation.CONSTITUTION, JOUR)

    def test_un_dossier_incomplet_ne_part_pas_au_guichet(self):
        """Le test qui vaut le plus cher : un dossier incomplet revient du CFCE."""
        dossier = avancer(_dossier(), EtapeCreation.CONSTITUTION, JOUR)
        with pytest.raises(TransitionInterdite, match="ne peut pas être déposé"):
            avancer(dossier, EtapeCreation.DEPOT_CFCE, JOUR)

    def test_le_refus_de_depot_nomme_les_pieces_manquantes(self):
        """Un refus qui ne dit pas quoi faire oblige à rouvrir le dossier."""
        dossier = avancer(_dossier(), EtapeCreation.CONSTITUTION, JOUR)
        with pytest.raises(TransitionInterdite) as refus:
            avancer(dossier, EtapeCreation.DEPOT_CFCE, JOUR)
        assert "Statuts signés par tous les associés" in str(refus.value)

    def test_on_ne_livre_pas_sans_rccm_ni_niu(self):
        dossier = _tout_fournir(_dossier())
        dossier = avancer(dossier, EtapeCreation.CONSTITUTION, JOUR)
        dossier = avancer(dossier, EtapeCreation.DEPOT_CFCE, JOUR)
        dossier = avancer(dossier, EtapeCreation.SUIVI_IMMATRICULATION, JOUR)
        with pytest.raises(TransitionInterdite, match="rien à livrer"):
            avancer(dossier, EtapeCreation.LIVRAISON, JOUR)

    def test_un_etat_terminal_n_ouvre_plus_rien(self):
        assert etapes_ouvertes_depuis(EtapeCreation.CONVERTI) == frozenset()
        assert etapes_ouvertes_depuis(EtapeCreation.ABANDONNE) == frozenset()

    def test_chaque_franchissement_laisse_un_jalon_date(self):
        """L'étape courante dit où on en est ; les jalons disent depuis quand."""
        dossier = avancer(_tout_fournir(_dossier()), EtapeCreation.CONSTITUTION, JOUR)
        assert dossier.depuis_le(EtapeCreation.CONSTITUTION) == JOUR
        assert dossier.depuis_le(EtapeCreation.DEPOT_CFCE) is None

    def test_l_abandon_exige_un_motif(self):
        """« Trop cher », « parti chez un concurrent » et « projet reporté »
        appellent trois réponses commerciales différentes."""
        with pytest.raises(TransitionInterdite, match="motif"):
            abandonner(_dossier(), "   ", JOUR)

    def test_avancer_refuse_l_abandon_et_renvoie_a_la_bonne_fonction(self):
        with pytest.raises(TransitionInterdite, match="`abandonner`"):
            avancer(_dossier(), EtapeCreation.ABANDONNE, JOUR)

    def test_on_n_abandonne_pas_deux_fois(self):
        clos = abandonner(_dossier(), "financement refusé", JOUR)
        with pytest.raises(TransitionInterdite, match="déjà clos"):
            abandonner(clos, "encore", JOUR)


# ══ Les pièces et les identifiants ════════════════════════════════════════════
class TestPieces:
    def test_une_piece_hors_checklist_est_ajoutee_et_non_refusee(self):
        """Le guichet réclame parfois une pièce que la liste n'a pas prévue."""
        dossier = fournir_piece(_dossier(), "ATTESTATION_SPECIALE", JOUR)
        ajoutee = next(p for p in dossier.pieces if p.code == "ATTESTATION_SPECIALE")
        assert ajoutee.fournie
        assert not ajoutee.obligatoire, (
            "une pièce imprévue ne doit pas bloquer un dépôt : elle est tracée, pas exigée"
        )

    def test_un_identifiant_sans_sa_date_est_refuse(self):
        """Sans la date, le délai tenu par le guichet n'est plus reconstituable."""
        from app.contextes.creation_entreprise.api import Immatriculation

        with pytest.raises(ValueError, match="date d'obtention"):
            Immatriculation(rccm="RC/DLA/2026/B/0001")

    def test_les_identifiants_arrivent_un_par_un(self):
        """Le RCCM et le NIU sont souvent séparés de plusieurs semaines."""
        dossier = enregistrer_identifiant(
            _dossier(), rccm=("RC/DLA/2026/B/0001", date(2026, 7, 2))
        )
        assert not dossier.immatriculation.immatriculee
        dossier = enregistrer_identifiant(dossier, niu=("M100000000001X", date(2026, 8, 1)))
        assert dossier.immatriculation.immatriculee

    def test_la_patente_et_la_cnps_ne_conditionnent_pas_la_conversion(self):
        """Les exiger retarderait l'entrée au portefeuille alors que les
        échéances, elles, ont déjà commencé à courir."""
        dossier = enregistrer_identifiant(
            _dossier(),
            rccm=("RC/DLA/2026/B/0001", date(2026, 7, 2)),
            niu=("M100000000001X", date(2026, 7, 20)),
        )
        assert dossier.immatriculation.immatriculee
        assert not dossier.immatriculation.complete


# ══ Le diagnostic ═════════════════════════════════════════════════════════════
class TestDiagnostic:
    def test_une_piece_manquante_est_bloquante(self, parametres):
        diagnostic = diagnostiquer(_dossier(), parametres, JOUR)
        assert not diagnostic.deposable

    def test_un_dossier_complet_est_deposable(self, parametres):
        diagnostic = diagnostiquer(_tout_fournir(_dossier()), parametres, JOUR)
        assert diagnostic.deposable

    def test_un_capital_insuffisant_avertit_sans_bloquer_tant_qu_il_est_a_valider(
        self, parametres_non_arretes
    ):
        """LE TEST QUI PORTE LA DISCIPLINE DU PRODUIT.

        Tant qu'un chiffre n'est pas confirmé, refuser un dépôt sur sa foi
        coûterait un client au cabinet, sur une règle dont on n'est pas sûr. Le
        constat informe donc, et n'interdit pas.

        Ce test s'exécute désormais sur `parametres_non_arretes` et non sur le
        référentiel réel : le second a été validé le 18 août 2026, et le
        comportement qu'on décrit ici n'existe plus que pour un paramètre qui,
        lui, ne l'est pas. Voir le test suivant.
        """
        dossier = _tout_fournir(_dossier(capital=Decimal("1000")))
        diagnostic = diagnostiquer(dossier, parametres_non_arretes, JOUR)
        constat = next(c for c in diagnostic.constats if c.code == "CAPITAL_INSUFFISANT")
        assert not constat.bloquant
        assert "non encore validé" in constat.message
        assert diagnostic.deposable, "un chiffre non validé informe, il n'interdit pas"

    def test_le_capital_insuffisant_bloque_une_fois_le_minimum_valide(self, parametres):
        """LE JOUR ANNONCÉ EST ARRIVÉ, ET AUCUNE LIGNE DE CODE N'A CHANGÉ.

        La version précédente de ce fichier portait, dans le test ci-dessus, cette
        phrase : « le jour où le paramètre passera VALIDE, ce même constat
        deviendra bloquant sans qu'une ligne de code change — et ce test devra
        alors être retourné, délibérément ». C'est le 18 août 2026, sur la loi
        camerounaise n° 2016/014 du 14 décembre 2016 qui fixe le capital minimum
        de la SARL à 100 000 FCFA.

        Le mécanisme tient dans `bloquant=valide` : la force d'un constat suit le
        statut du chiffre sur lequel il repose. C'est ce qui rend le référentiel
        utile plutôt que décoratif — une valeur qu'on valide change le
        comportement du produit, et un déploiement n'y est pour rien.
        """
        dossier = _tout_fournir(_dossier(capital=Decimal("1000")))
        diagnostic = diagnostiquer(dossier, parametres, JOUR)
        constat = next(c for c in diagnostic.constats if c.code == "CAPITAL_INSUFFISANT")
        assert constat.bloquant
        assert "non encore validé" not in constat.message
        assert not diagnostic.deposable, (
            "un capital sous le minimum légal confirmé ne se dépose pas : le greffe "
            "le refuserait, et le cabinet aurait facturé une démarche vouée à l'échec"
        )

    def test_le_capital_libre_ne_produit_aucun_constat(self, parametres):
        """Une entreprise individuelle n'a pas de capital : rien à vérifier."""
        dossier = _tout_fournir(_dossier(FormeJuridique.ETS, capital=None))
        codes = {c.code for c in diagnostiquer(dossier, parametres, JOUR).constats}
        assert not {c for c in codes if c.startswith("CAPITAL")}

    def test_un_parametre_absent_n_interrompt_pas_le_diagnostic(self):
        """Lever ici empêcherait d'instruire toute SARL pour une ligne manquante
        dans un fichier YAML — et l'origine serait invisible depuis le guichet."""
        vide = ServiceParametres([])
        diagnostic = diagnostiquer(_tout_fournir(_dossier()), vide, JOUR)
        constat = next(c for c in diagnostic.constats if c.code == "CAPITAL_MINIMUM_INCONNU")
        assert not constat.bloquant
        assert diagnostic.deposable

    def test_le_depassement_du_delai_du_guichet_est_signale(self, parametres):
        """Signalé pour relancer, jamais pour bloquer : le délai du CFCE est
        annoncé, pas opposable."""
        dossier = avancer(_tout_fournir(_dossier()), EtapeCreation.CONSTITUTION, JOUR)
        dossier = avancer(dossier, EtapeCreation.DEPOT_CFCE, date(2026, 8, 1))
        assert depasse_le_delai_annonce(dossier, parametres, date(2026, 8, 17))
        assert not depasse_le_delai_annonce(dossier, parametres, date(2026, 8, 2))

    def test_un_dossier_qui_n_est_pas_au_guichet_n_est_jamais_en_retard(self, parametres):
        assert not depasse_le_delai_annonce(_dossier(), parametres, JOUR)


# ══ La conversion — le cœur du contexte ═══════════════════════════════════════
class TestConversion:
    def test_la_date_de_creation_est_celle_du_rccm_jamais_celle_du_jour(self):
        """LE TEST LE PLUS IMPORTANT DU CONTEXTE.

        C'est le RCCM qui fait naître la personne morale. Convertir trois
        semaines plus tard avec la date du jour ferait commencer les obligations
        fiscales trois semaines trop tard, et le premier acompte serait déclaré
        en retard sans que personne comprenne pourquoi — trois mois après, quand
        la pénalité tombe.
        """
        rccm_le = date(2026, 7, 2)
        dossier = _mener_jusqu_a_livraison(
            _dossier(), rccm_le=rccm_le, niu_le=date(2026, 7, 20)
        )
        _, entreprise = convertir(
            dossier,
            a_la_date=date(2026, 8, 17),
            regime=RegimeFiscal.REEL_SIMPLIFIE
            if hasattr(RegimeFiscal, "REEL_SIMPLIFIE")
            else RegimeFiscal.REEL,
            centre=CentreRattachement.CDI,
        )
        assert entreprise.date_creation == rccm_le
        assert entreprise.regimes[0].debut == rccm_le

    def test_l_adhesion_prend_effet_le_jour_de_la_conversion_pas_du_rccm(self):
        """L'adhésion ouvre l'abattement CGA sur le bénéfice.

        L'antidater accorderait un avantage fiscal sur une période où
        l'entreprise n'était pas adhérente, et exposerait le Centre autant que
        l'adhérent.
        """
        conversion_le = date(2026, 8, 17)
        dossier = _mener_jusqu_a_livraison(
            _dossier(), rccm_le=date(2026, 7, 2), niu_le=date(2026, 7, 20)
        )
        _, entreprise = convertir(
            dossier,
            a_la_date=conversion_le,
            regime=RegimeFiscal.REEL,
            centre=CentreRattachement.CDI,
        )
        assert entreprise.adhesions[0].debut == conversion_le

    def test_sans_adhesion_l_entreprise_entre_quand_meme_au_portefeuille(self):
        """Une création n'oblige pas à adhérer : le cabinet peut créer sans suivre."""
        dossier = _mener_jusqu_a_livraison(
            _dossier(), rccm_le=date(2026, 7, 2), niu_le=date(2026, 7, 20)
        )
        _, entreprise = convertir(
            dossier,
            a_la_date=JOUR,
            regime=RegimeFiscal.REEL,
            centre=CentreRattachement.CDI,
            adherent=False,
        )
        assert entreprise.adhesions == []

    def test_la_conversion_exige_les_deux_identifiants(self):
        """Sans RCCM il n'y a pas de personne morale, sans NIU pas de contribuable."""
        dossier = _tout_fournir(_dossier())
        dossier = avancer(dossier, EtapeCreation.CONSTITUTION, JOUR)
        dossier = avancer(dossier, EtapeCreation.DEPOT_CFCE, JOUR)
        dossier = avancer(dossier, EtapeCreation.SUIVI_IMMATRICULATION, JOUR)
        with pytest.raises(ConversionImpossible):
            convertir(
                dossier, a_la_date=JOUR, regime=RegimeFiscal.REEL, centre=CentreRattachement.CDI
            )

    def test_la_conversion_suit_la_livraison(self):
        dossier = _tout_fournir(_dossier())
        dossier = avancer(dossier, EtapeCreation.CONSTITUTION, JOUR)
        with pytest.raises(ConversionImpossible, match="suit la livraison"):
            convertir(
                dossier, a_la_date=JOUR, regime=RegimeFiscal.REEL, centre=CentreRattachement.CDI
            )

    def test_un_rccm_posterieur_au_jour_de_conversion_est_refuse(self):
        """Une saisie fautive de date produirait une entreprise née demain."""
        dossier = _mener_jusqu_a_livraison(
            _dossier(), rccm_le=date(2026, 9, 1), niu_le=date(2026, 9, 2)
        )
        with pytest.raises(ConversionImpossible, match="postérieur"):
            convertir(
                dossier,
                a_la_date=date(2026, 8, 17),
                regime=RegimeFiscal.REEL,
                centre=CentreRattachement.CDI,
            )

    def test_on_ne_convertit_pas_deux_fois(self):
        dossier = _mener_jusqu_a_livraison(
            _dossier(), rccm_le=date(2026, 7, 2), niu_le=date(2026, 7, 20)
        )
        clos, _ = convertir(
            dossier, a_la_date=JOUR, regime=RegimeFiscal.REEL, centre=CentreRattachement.CDI
        )
        with pytest.raises(ConversionImpossible, match="déjà clos"):
            convertir(
                clos, a_la_date=JOUR, regime=RegimeFiscal.REEL, centre=CentreRattachement.CDI
            )

    def test_le_dossier_clos_designe_l_entreprise_creee(self):
        """Le lien dans les deux sens : sans lui, on reconvertirait au prochain passage."""
        dossier = _mener_jusqu_a_livraison(
            _dossier(), rccm_le=date(2026, 7, 2), niu_le=date(2026, 7, 20)
        )
        clos, entreprise = convertir(
            dossier, a_la_date=JOUR, regime=RegimeFiscal.REEL, centre=CentreRattachement.CDI
        )
        assert clos.etape is EtapeCreation.CONVERTI
        assert clos.converti_en == entreprise.niu

    def test_le_fondateur_devient_dirigeant_avec_la_qualite_de_sa_forme(self):
        """« Gérant » écrit sur une SA se voit — le mot figure au RCCM."""
        dossier = _mener_jusqu_a_livraison(
            _dossier(FormeJuridique.SA, capital=Decimal("15000000")),
            rccm_le=date(2026, 7, 2),
            niu_le=date(2026, 7, 20),
        )
        _, entreprise = convertir(
            dossier, a_la_date=JOUR, regime=RegimeFiscal.REEL, centre=CentreRattachement.CDI
        )
        assert entreprise.dirigeants[0].qualite == "Directeur général"
        assert entreprise.dirigeants[0].nom == "Estelle MOMHA"

    def test_la_conversion_cree_le_premier_exercice(self):
        """LE TEST QUI MANQUAIT, ET CE QUE SON ABSENCE A COÛTÉ.

        La première version n'en créait aucun, au motif que « c'est au
        portefeuille de les porter ». Les quarante-cinq tests du contexte
        passaient. Puis le parcours complet, exécuté contre PostgreSQL, a rendu
        **404 : exercice 2026 inconnu** sur l'échéancier — F calcule les échéances
        *sur un exercice*, et il n'y en avait pas. La promesse du contexte était
        creuse et rien ne le disait.

        La ligne juste passe entre le fait et le calcul : la période couverte par
        les premiers comptes est un fait, écrit dans les statuts ; les échéances
        qui en découlent sont un calcul, refait à chaque lecture.
        """
        dossier = _mener_jusqu_a_livraison(
            _dossier(), rccm_le=date(2026, 7, 2), niu_le=date(2026, 7, 20)
        )
        _, entreprise = convertir(
            dossier, a_la_date=JOUR, regime=RegimeFiscal.REEL, centre=CentreRattachement.CDI
        )
        assert len(entreprise.regimes) == 1
        assert len(entreprise.rattachements) == 1
        exercice = entreprise.exercices[0]
        assert exercice.ouverture == date(2026, 7, 2), "l'exercice s'ouvre au RCCM"
        assert exercice.cloture == date(2026, 12, 31)
        assert exercice.libelle == "2026"
        assert not exercice.clos

    def test_le_premier_exercice_long_se_declare_et_ne_se_devine_pas(self):
        """Une entreprise immatriculée en octobre clôture souvent quinze mois plus
        tard. Le deviner d'après le mois reviendrait à prendre dans le code une
        décision qui appartient aux statuts.

        Le libellé suit l'année de **clôture** : c'est sous elle que la liasse est
        déposée, et c'est donc celle que le comptable cherche.
        """
        dossier = _mener_jusqu_a_livraison(
            _dossier(), rccm_le=date(2026, 10, 12), niu_le=date(2026, 10, 30)
        )
        _, entreprise = convertir(
            dossier,
            a_la_date=date(2026, 11, 3),
            regime=RegimeFiscal.REEL,
            centre=CentreRattachement.CDI,
            premiere_cloture=date(2027, 12, 31),
        )
        exercice = entreprise.exercices[0]
        assert exercice.libelle == "2027"
        assert exercice.est_long

    def test_sans_declaration_le_premier_exercice_clot_au_31_decembre(self):
        dossier = _mener_jusqu_a_livraison(
            _dossier(), rccm_le=date(2026, 10, 12), niu_le=date(2026, 10, 30)
        )
        _, entreprise = convertir(
            dossier,
            a_la_date=date(2026, 11, 3),
            regime=RegimeFiscal.REEL,
            centre=CentreRattachement.CDI,
        )
        assert entreprise.exercices[0].cloture == date(2026, 12, 31)
        assert not entreprise.exercices[0].est_long


# ══ Le dépôt ══════════════════════════════════════════════════════════════════
class TestDepotMemoire:
    def test_le_pipeline_se_lit_par_urgence(self):
        """Un dossier qui dort depuis trois semaines passe avant celui d'hier."""
        depot = DepotDossiersCreationMemoire(list(PIPELINE_DEMO))
        dates = [d.immobile_depuis for d in depot.lister()]
        assert dates == sorted(dates)

    def test_le_filtre_par_etape(self):
        depot = DepotDossiersCreationMemoire(list(PIPELINE_DEMO))
        retenus = depot.lister(etape=EtapeCreation.DEPOT_CFCE)
        assert {d.etape for d in retenus} == {EtapeCreation.DEPOT_CFCE}

    def test_le_filtre_des_dossiers_qui_dorment(self):
        """La borne est stricte, et le seuil se déplace comme on l'attend.

        Deux dates plutôt qu'une : un filtre qui rendrait tout, ou rien, passerait
        un test à seuil unique. C'est en déplaçant le seuil qu'on voit s'il
        discrimine — et c'est ce test qui a corrigé mon attendu, pas le code : le
        dossier livré dort lui aussi depuis juin.
        """
        depot = DepotDossiersCreationMemoire(list(PIPELINE_DEMO))
        assert {d.reference for d in depot.lister(immobiles_avant=date(2026, 5, 1))} == {
            "CR-2026-0009"
        }
        assert {d.reference for d in depot.lister(immobiles_avant=date(2026, 7, 1))} == {
            "CR-2026-0009",
            "CR-2026-0015",
        }

    def test_une_reference_inconnue_leve(self):
        depot = DepotDossiersCreationMemoire([])
        with pytest.raises(DossierCreationIntrouvable):
            depot.lire("CR-INEXISTANT")

    def test_une_ecriture_qui_perdrait_un_jalon_est_refusee(self):
        """Un jalon effacé, c'est la date d'un dépôt perdue — donc le délai que
        le guichet a tenu ou non, seul chiffre opposable au CFCE."""
        depot = DepotDossiersCreationMemoire([])
        dossier = avancer(_tout_fournir(_dossier()), EtapeCreation.CONSTITUTION, JOUR)
        depot.enregistrer(dossier)
        ampute = dossier.model_copy(update={"jalons": dossier.jalons[:1]})
        with pytest.raises(ValueError, match="perdrait"):
            depot.enregistrer(ampute)


# ══ Le jeu de démonstration ═══════════════════════════════════════════════════
class TestPipelineDemo:
    def test_chaque_etape_du_tunnel_est_peuplee(self):
        """Un pipeline de démonstration qui laisserait une étape vide cacherait
        exactement la colonne dont l'écran a besoin pour être jugé."""
        peuplees = {d.etape for d in PIPELINE_DEMO}
        assert EtapeCreation.QUALIFICATION in peuplees
        assert EtapeCreation.CONSTITUTION in peuplees
        assert EtapeCreation.DEPOT_CFCE in peuplees
        assert EtapeCreation.SUIVI_IMMATRICULATION in peuplees
        assert EtapeCreation.LIVRAISON in peuplees
        assert EtapeCreation.ABANDONNE in peuplees

    def test_le_dossier_abandonne_porte_son_motif(self):
        abandonne = next(d for d in PIPELINE_DEMO if d.etape is EtapeCreation.ABANDONNE)
        assert abandonne.motif_abandon

    def test_le_dossier_en_livraison_est_convertible(self):
        """Sans lui, la conversion ne serait démontrable sur aucun écran."""
        pret = next(d for d in PIPELINE_DEMO if d.etape is EtapeCreation.LIVRAISON)
        assert pret.immatriculation.immatriculee

    def test_le_dossier_en_suivi_n_a_que_son_rccm(self):
        """La situation la plus fréquente, et celle qui explique la règle : la
        société existe en droit commercial mais n'est pas encore contribuable."""
        suivi = next(
            d for d in PIPELINE_DEMO if d.etape is EtapeCreation.SUIVI_IMMATRICULATION
        )
        assert suivi.immatriculation.rccm is not None
        assert suivi.immatriculation.niu is None
        assert not suivi.immatriculation.immatriculee
