"""Une société immatriculée ouvre l'accès de sa fondatrice.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE (28 septembre 2026)

Le cabinet a tranché : une cliente qui achète une prestation est une **adhérente
du cabinet**, pas un locataire de la plateforme. Son compte vit là où vit son
dossier.

Or la portée d'une adhérente est le dossier de SON entreprise. Ouvrir son accès
demande un NIU — et quand elle paie sa création, son entreprise n'existe pas.
Son accès ne peut donc s'ouvrir qu'à la **conversion**, quand la société entre au
portefeuille.

Avant ce chantier, elle recevait un lien d'activation au paiement. Ce lien menait
à un compte créé dans un **autre locataire** que son dossier : même activé, il
ouvrait sur un espace vide. Elle a maintenant un seul accès, au bon moment, sur
le bon dossier.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.contextes.transverse.adaptateurs.entrant.abonne_acces_adherent import (
    ouvrir_l_acces_de_la_fondatrice,
)
from app.infrastructure.config import configuration
from app.orchestration.boite_d_envoi import EvenementSortant

NIU = "M091122334455X"
COURRIEL = "sylvie.mbida@exemple.cm"


@pytest.fixture
def cabinet():
    """Les dépôts du CABINET, en mémoire, et le notifieur qui va avec.

    ⚠️ **Une fabrique d'unité de travail, pas un atelier.** L'abonné tourne hors
    requête et ouvre la sienne : c'est précisément ce que ce montage éprouve.
    """
    from app.contextes.transverse.adaptateurs.sortant.depots_memoire import (
        DepotComptesMemoire,
        DepotHabilitationsMemoire,
        DepotJetonsMemoire,
        JournalAuditMemoire,
    )

    locataire = configuration().locataire_par_defaut
    boutique = SimpleNamespace(
        comptes=DepotComptesMemoire(locataire),
        habilitations=DepotHabilitationsMemoire(locataire),
        jetons=DepotJetonsMemoire(locataire),
        journal=JournalAuditMemoire(locataire),
    )
    postes: list[dict] = []

    class Notifieur:
        def envoyer(self, gabarit, *, destinataire, contexte):
            postes.append(
                {"gabarit": gabarit, "destinataire": destinataire, "contexte": contexte}
            )
            return True

    @contextmanager
    def unite(_locataire: str | None = None):
        yield boutique

    return SimpleNamespace(
        boutique=boutique, unite=unite, notifieur=Notifieur(), postes=postes
    )


def _entreprise_creee(**remplacements) -> EvenementSortant:
    charge = {
        "niu": NIU,
        "denomination": "SYLVIE COUTURE SARL",
        "courriel_fondateur": COURRIEL,
        "nom_fondateur": "MBIDA",
        "prenom_fondateur": "Sylvie",
        "telephone_fondateur": "+237699112233",
    }
    charge.update(remplacements)
    return EvenementSortant(
        identifiant="ent-1", nom="EntrepriseCréée", cle="CRE-dos-1", charge=charge
    )


def _ouvrir(cabinet, evenement=None):
    ouvrir_l_acces_de_la_fondatrice(
        evenement or _entreprise_creee(),
        unite_de_travail=cabinet.unite,
        notifier=cabinet.notifieur,
    )


def test_la_fondatrice_recoit_un_compte_et_un_lien(cabinet):
    """⚠️ Le compte ET le lien : un compte sans lien laisse la cliente dehors."""
    _ouvrir(cabinet)

    compte = cabinet.boutique.comptes.par_courriel(COURRIEL)
    assert compte is not None, "aucun compte ouvert pour la fondatrice"
    assert compte.nom == "MBIDA"
    assert cabinet.postes, "aucun courriel : elle n'a pas de chemin vers son espace"
    assert cabinet.postes[-1]["destinataire"] == COURRIEL
    assert cabinet.postes[-1]["contexte"]["lien"]


def test_son_compte_vit_chez_le_cabinet(cabinet):
    """⚠️ LE POINT DE TOUT LE CHANTIER.

    Son compte naissait dans un locataire à elle, `tnt-couture-…`, pendant que
    son entreprise entrait au portefeuille du cabinet. Deux locataires, et un
    espace vide.
    """
    _ouvrir(cabinet)

    compte = cabinet.boutique.comptes.par_courriel(COURRIEL)
    assert compte.locataire == configuration().locataire_par_defaut


def test_sa_portee_est_le_dossier_de_SON_entreprise(cabinet):
    """⚠️ « Une souscription ne peut pas fabriquer un réviseur » : le rôle est
    ADHERENT, et la portée le seul NIU qui vient de naître."""
    _ouvrir(cabinet)

    compte = cabinet.boutique.comptes.par_courriel(COURRIEL)
    portees = cabinet.boutique.habilitations.pour_compte(compte.identifiant)
    assert portees, "aucune habilitation : elle verrait un espace vide"
    assert any(NIU in str(h) for h in portees), portees


def test_l_evenement_rejoue_n_ouvre_pas_un_second_compte(cabinet):
    """⚠️ Le relais garantit « au moins une fois ». Deux liens d'activation, et
    le premier est invalidé par le second : la cliente clique sur le mauvais."""
    _ouvrir(cabinet)
    premier = cabinet.boutique.comptes.par_courriel(COURRIEL)
    partis = len(cabinet.postes)

    _ouvrir(cabinet)

    assert cabinet.boutique.comptes.par_courriel(COURRIEL).identifiant == premier.identifiant
    assert len(cabinet.postes) == partis, "un second lien d'activation est parti"


@pytest.mark.parametrize("manquant", ["courriel_fondateur", "niu"])
def test_sans_adresse_ni_niu_on_n_invente_rien(cabinet, manquant, caplog):
    """⚠️ On ne fabrique pas d'identifiant pour quelqu'un qu'on ne peut pas
    prévenir, ni de portée sur une entreprise qu'on ne sait pas nommer."""
    with caplog.at_level("WARNING"):
        _ouvrir(cabinet, _entreprise_creee(**{manquant: ""}))

    assert cabinet.boutique.comptes.par_courriel(COURRIEL) is None
    assert not cabinet.postes
    assert "À ouvrir à la main" in caplog.text


def test_le_lien_part_sur_l_adresse_du_SITE(cabinet):
    """⚠️ Et non sur celle de l'API — celle que le prestataire de paiement
    rappelle. Un lien d'activation mène à une page.

    C'est le défaut constaté le 27 septembre sur l'autre port d'ouverture : la
    cliente payait, recevait son courriel, et le lien ne menait nulle part.
    """
    _ouvrir(cabinet)

    lien = cabinet.postes[-1]["contexte"]["lien"]
    assert lien.startswith(configuration().adresse_publique_site.rstrip("/")), lien
    assert "/activation?jeton=" in lien


def test_sans_le_suivi_aucun_acces_n_est_ouvert(cabinet, caplog):
    """⚠️ LA SECONDE CONDITION, ET ELLE VAUT LA PREMIÈRE.

    Le cabinet peut immatriculer une société sans en tenir la comptabilité : la
    cliente repart avec son RCCM et son NIU, et c'est complet. Lui ouvrir un
    espace de suivi qu'elle n'a pas acheté lui ferait attendre un service qui ne
    viendra pas — et le cabinet le découvrirait à la première échéance non
    traitée.
    """
    with caplog.at_level("INFO"):
        _ouvrir(cabinet, _entreprise_creee(adherente=False))

    assert cabinet.boutique.comptes.par_courriel(COURRIEL) is None
    assert not cabinet.postes, "un lien est parti pour un suivi non souscrit"
    assert "sans suivi" in caplog.text


def test_avec_le_suivi_l_acces_s_ouvre(cabinet):
    """La contre-épreuve du cas précédent."""
    _ouvrir(cabinet, _entreprise_creee(adherente=True))

    assert cabinet.boutique.comptes.par_courriel(COURRIEL) is not None
    assert cabinet.postes


def test_un_evenement_d_avant_la_distinction_ouvre_toujours_l_acces(cabinet):
    """⚠️ ABSENT VAUT VRAI : la conversion valait adhésion avant cette clé, et
    il en dort dans la boîte d'envoi. Les ignorer laisserait une adhérente
    payante sans accès, sans que personne sache pourquoi."""
    sans_la_cle = _entreprise_creee()
    charge = dict(sans_la_cle.charge)
    charge.pop("adherente", None)
    _ouvrir(cabinet, sans_la_cle.model_copy(update={"charge": charge}))

    assert cabinet.boutique.comptes.par_courriel(COURRIEL) is not None


def test_une_fondatrice_sans_prenom_obtient_quand_meme_son_acces(cabinet):
    """⚠️ LE DÉFAUT QUI A MIS UN ÉVÉNEMENT EN QUARANTAINE.

    Le formulaire public ne porte qu'UN champ de nom, et la demande de contact
    le conserve entier. Le dossier de formalité fait de même. `Compte.prenom`
    exigeait pourtant un caractère : l'ouverture de l'accès tombait en
    quarantaine après dix tentatives, sur « String should have at least 1
    character ».

    Sa société était immatriculée, au portefeuille, et elle n'avait **aucun**
    chemin vers son espace — sans que rien ne le dise, l'échec vivant dans une
    colonne de la boîte d'envoi.

    ⚠️ Les deux remèdes possibles étaient pires : couper le nom au premier
    espace pour en tirer un prénom — l'étiquette du formulaire dit « Nom et
    prénom », dans cet ordre —, ou recopier le nom dans ce champ, c'est-à-dire
    ranger un faux en base.
    """
    _ouvrir(cabinet, _entreprise_creee(prenom_fondateur=""))

    compte = cabinet.boutique.comptes.par_courriel(COURRIEL)
    assert compte is not None, "aucun accès pour une fondatrice sans prénom"
    assert compte.prenom == ""
    # ⚠️ Et le nom affiché ne porte pas d'espace en tête : elle apparaîtrait en
    # première ligne de toute liste triée, avant les A.
    assert compte.nom_affiche == "MBIDA"
    assert cabinet.postes[-1]["contexte"]["prenom"] == "MBIDA"

