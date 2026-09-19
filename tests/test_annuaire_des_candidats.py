"""Qui peut prendre un dossier : ce que l'annuaire monte, et ce qu'il refuse de deviner.

⚠️ **La route de l'affectation exigeait que l'appelant fournisse les candidats**, avec
pour chacun son nombre de dossiers ouverts et sa charge pondérée. Aucune console ne
peut savoir cela sans refaire côté client le travail du serveur : la route était
utilisable en test et inutilisable en service.

Le domaine était éprouvé depuis le pas 4 — cinq critères configurés, une grille, un
classement motivé — et rien ne l'alimentait.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.contextes.souscription.adaptateurs.sortant.annuaire_des_candidats import (
    monter_les_candidatures,
    roles_porteurs,
)
from app.contextes.transverse.adaptateurs.sortant.donnees_demo import depots_demo
from app.contextes.transverse.domaine.roles import (
    PERMISSIONS_PAR_ROLE,
    Permission,
    Role,
)

LE_JOUR = date(2026, 9, 10)


@pytest.fixture
def annuaire():
    comptes, habilitations, *_ = depots_demo()
    return comptes, habilitations


def _candidats(annuaire, charge=None):
    comptes, habilitations = annuaire
    return monter_les_candidatures(
        comptes=comptes,
        habilitations=habilitations,
        charge=charge or {},
        service="creation-sarl",
        a_la_date=LE_JOUR,
    )


class TestQuiEstCandidat:
    def test_les_roles_porteurs_derivent_de_la_permission(self):
        """⚠️ **Dérivé, et non recopié.**

        Un rôle créé demain avec la permission de qualifier entrera dans cette
        liste sans que personne y pense. Une liste écrite à la main l'aurait
        oublié, et le nouveau collaborateur n'aurait jamais reçu de dossier sans
        qu'aucune erreur ne se produise.
        """
        attendus = {
            role
            for role, permissions in PERMISSIONS_PAR_ROLE.items()
            if Permission.QUALIFIER_PROSPECT in permissions
        }
        assert roles_porteurs() == attendus

    def test_l_administrateur_n_est_pas_candidat(self):
        """Il peut **affecter** un dossier, pas en **recevoir** un.

        La distinction est celle du métier : celui qui répartit n'est pas celui qui
        traite. La confondre chargerait l'administrateur de dossiers commerciaux
        qu'il n'a aucune raison de suivre.
        """
        assert Permission.AFFECTER_DOSSIER in PERMISSIONS_PAR_ROLE[Role.ADMINISTRATEUR]
        assert Role.ADMINISTRATEUR not in roles_porteurs()

    def test_un_compte_sans_role_porteur_est_absent(self, annuaire):
        """⚠️ Absent, et non « indisponible ».

        Il n'a jamais eu vocation à prendre ce dossier. Le lister encombrerait les
        empêchements de tous les comptables du cabinet, et le responsable qui
        cherche pourquoi personne n'a été affecté lirait vingt lignes sans rapport.
        """
        comptes, habilitations = annuaire
        porteurs = {c.responsable for c in _candidats(annuaire)}
        tous = {c.identifiant for c in comptes.lister()}

        assert porteurs < tous, "tous les comptes sont devenus candidats"
        assert porteurs, "aucun candidat : l'annuaire ne monte plus rien"

    def test_les_competences_derivent_des_roles_tenus(self, annuaire):
        """Faute d'un référentiel de compétences, et c'est assumé : un chargé de
        formalités *a* la compétence « formalités », et rien ne le dit ailleurs."""
        candidats = {c.responsable: c for c in _candidats(annuaire)}
        porteur = next(c for c in candidats.values() if c.competences)
        assert all(comp in {r.value for r in Role} for comp in porteur.competences)


class TestCeQueLAnnuaireRefuseDeDeviner:
    def test_la_region_est_vide_et_jamais_devinee(self, annuaire):
        """⚠️ **Le défaut que ce module corrige, et il était subtil.**

        ─────────────────────────────────────────────────────────────────────────
        La route passait le **message libre** du prospect comme région. Un message
        du genre « je suis à Bonabéri » aurait fait correspondre une agence par
        coïncidence de mots, et une affectation se serait décidée sur un mot du
        texte libre.

        Le formulaire public ne collecte aucune région : six champs, délibérément.
        Le référentiel l'anticipe — « une région non déclarée pénalise tout le monde
        également, l'effet est nul sur le classement, et c'est voulu : on ne devine
        pas où habite quelqu'un qui n'a rien dit ».

        Le vide est franc ; la coïncidence ne l'est pas.
        ─────────────────────────────────────────────────────────────────────────
        """
        assert {c.region_demande for c in _candidats(annuaire)} == {""}

    def test_l_agence_est_vide_faute_de_donnee(self, annuaire):
        """Un compte ne porte pas d'agence. L'inventer ferait pencher le critère de
        proximité sur une donnée fabriquée."""
        assert {c.agence_responsable for c in _candidats(annuaire)} == {""}


class TestLaCharge:
    def test_elle_vient_du_comptage_reel(self, annuaire):
        candidats = {c.responsable: c for c in _candidats(annuaire, charge={"C-001": 7})}
        assert candidats["C-001"].dossiers_ouverts == 7

    def test_un_responsable_sans_dossier_est_a_zero(self, annuaire):
        """Et non absent : un collaborateur libre est le meilleur candidat, pas un
        candidat manquant."""
        candidats = {c.responsable: c for c in _candidats(annuaire, charge={"C-001": 7})}
        autres = [c for nom, c in candidats.items() if nom != "C-001"]
        assert autres and all(c.dossiers_ouverts == 0 for c in autres)

    def test_la_charge_ponderee_suit_le_nombre_de_dossiers(self, annuaire):
        """⚠️ La matrice de pondération n'existe pas encore.

        La rendre à zéro ferait croire à un cabinet où personne n'est chargé, et le
        critère de charge cesserait de départager quoi que ce soit. Le nombre de
        dossiers ordonne correctement ; il ne prétend rien de plus.
        """
        candidats = {c.responsable: c for c in _candidats(annuaire, charge={"C-001": 7})}
        assert candidats["C-001"].charge_ponderee == 7


class TestUnPorteurSuspendu:
    """⚠️ **Ce cas emploie un annuaire fabriqué, et il le doit.**

    ─────────────────────────────────────────────────────────────────────────────
    Aucun compte **porteur** du jeu de démonstration n'est suspendu : le seul compte
    suspendu n'a pas de rôle qui prenne des dossiers. Le garde de disponibilité ne
    changeait donc rien sur les données réelles, et une mutation le remplaçant par
    `True` a survécu.

    Un cas ne peut mesurer un garde que s'il existe une situation où le garde change
    quelque chose. C'est la cinquième fois que ce chantier le rappelle.
    ─────────────────────────────────────────────────────────────────────────────
    """

    class _Comptes:
        def __init__(self, comptes):
            self._comptes = comptes

        def lister(self):
            return list(self._comptes)

    class _Habilitations:
        def __init__(self, par_compte):
            self._par_compte = par_compte

        def pour_compte(self, compte):
            return list(self._par_compte.get(compte, ()))

    def _annuaire(self, etat):
        from datetime import datetime

        from app.contextes.transverse.domaine.habilitations import (
            Habilitation,
            MotifHabilitation,
        )
        from app.contextes.transverse.domaine.identites import Compte

        compte = Compte(
            identifiant="C-900",
            courriel="porteur@cga-brcg.cm",
            nom="ESSAI",
            prenom="Porteur",
            etat=etat,
            empreinte_mot_de_passe="x",
            cree_le=datetime(2026, 1, 1),
            locataire="CGA-BRCG",
        )
        habilitation = Habilitation(
            identifiant="H-900",
            compte="C-900",
            role=Role.CHARGE_FORMALITES,
            portee=None,
            debut=date(2026, 1, 1),
            accordee_par="C-001",
            motif=MotifHabilitation.RECRUTEMENT,
        )
        return (
            self._Comptes([compte]),
            self._Habilitations({"C-900": [habilitation]}),
        )

    def test_un_porteur_suspendu_est_candidat_mais_indisponible(self):
        """⚠️ **Candidat, et non absent.** La distinction compte.

        `disponible=False` le fait entrer dans le classement puis l'en écarte par
        une règle rédhibitoire, ce qui le fait apparaître dans les empêchements :
        « Porteur ESSAI, compte suspendu ». C'est exactement ce qu'un responsable
        veut lire quand il se demande pourquoi personne n'a été affecté.

        L'écarter en amont laisserait la question sans réponse.
        """
        from app.contextes.transverse.domaine.identites import EtatCompte

        comptes, habilitations = self._annuaire(EtatCompte.SUSPENDU)
        candidats = monter_les_candidatures(
            comptes=comptes,
            habilitations=habilitations,
            charge={},
            service="creation-sarl",
            a_la_date=LE_JOUR,
        )
        assert [c.responsable for c in candidats] == ["C-900"]
        assert candidats[0].disponible is False

    def test_un_porteur_en_attente_d_activation_est_indisponible(self):
        """⚠️ `ACTIF` seul, et non « pas suspendu ».

        Un compte en attente d'activation n'a jamais ouvert de session : lui confier
        un dossier le laisserait sans responsable réel jusqu'à ce que quelqu'un
        s'aperçoive que la personne n'est jamais venue.
        """
        from app.contextes.transverse.domaine.identites import EtatCompte

        comptes, habilitations = self._annuaire(EtatCompte.EN_ATTENTE_ACTIVATION)
        candidats = monter_les_candidatures(
            comptes=comptes,
            habilitations=habilitations,
            charge={},
            service="creation-sarl",
            a_la_date=LE_JOUR,
        )
        assert candidats[0].disponible is False

    def test_un_porteur_actif_est_disponible(self):
        """La contre-épreuve : sans elle, les deux cas passeraient sur un annuaire
        qui déclarerait tout le monde indisponible."""
        from app.contextes.transverse.domaine.identites import EtatCompte

        comptes, habilitations = self._annuaire(EtatCompte.ACTIF)
        candidats = monter_les_candidatures(
            comptes=comptes,
            habilitations=habilitations,
            charge={},
            service="creation-sarl",
            a_la_date=LE_JOUR,
        )
        assert candidats[0].disponible is True
