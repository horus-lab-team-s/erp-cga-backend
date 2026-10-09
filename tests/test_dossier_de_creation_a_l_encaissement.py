"""Une création payée ouvre son dossier de formalité, toute seule.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE (27 septembre 2026)

Le site vend « votre SARL immatriculée, statuts et attestations compris ». En
jouant ce parcours pour la première fois de bout en bout, on a constaté qu'une
création réglée n'ouvrait **qu'un accès à l'ERP** : le dossier qui mène au RCCM et
au NIU n'existait que si un collaborateur pensait à l'ouvrir à la main, depuis un
autre écran, sans que rien ne le lui rappelle.

Le client avait payé ; le travail n'avait pas commencé, et rien ne le signalait.

Ces cas tiennent les quatre bords de l'abonné qui referme cette boucle :

  · une création payée ouvre le dossier, avec les faits de la qualification ;
  · une adhésion payée n'en ouvre aucun — elle n'a rien à immatriculer ;
  · l'événement rejoué n'ouvre pas un second dossier pour un seul client payé ;
  · un fait fondateur manquant n'ouvre **rien**, et se dit. Un dossier ouvert au
    nom de « Société à nommer » circulerait jusqu'au greffe.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.contextes.creation_entreprise.adaptateurs.entrant.abonne_de_souscription import (
    _reference_du_dossier,
    ouvrir_le_dossier_de_creation,
)
from app.contextes.creation_entreprise.api import DossierCreationIntrouvable
from app.orchestration.boite_d_envoi import EvenementSortant

DOSSIER = "dos-essai-creation"

FAITS = {
    "denomination_souhaitee": "SYLVIE COUTURE SARL",
    "forme_juridique": "SARL",
    "capital_social": "1000000",
    "activite": "confection et vente de vêtements",
    "siege": "Bonabéri, Douala",
}


def _encaissement(**remplacements) -> EvenementSortant:
    charge = {
        "service": "CREATION",
        "tenant": "t-essai",
        "slug": "sylvie-couture",
        "proforma": "PRO-0001",
        "version_proforma": 1,
        "reference_externe": "TARA-123",
        "faits_fondateurs": dict(FAITS),
        "courriel_titulaire": "sylvie.mbida@exemple.cm",
        "nom_titulaire": "Sylvie MBIDA",
        "telephone_titulaire": "+237699112233",
    }
    charge.update(remplacements)
    return EvenementSortant(
        identifiant="ev-essai",
        nom="PaiementEncaissé",
        cle=DOSSIER,
        charge=charge,
    )


@pytest.fixture
def magasin():
    """Un dépôt mémoire NEUF à chaque cas.

    ⚠️ `_depot_memoire` est mémoïsé — c'est voulu, l'application veut un seul
    dépôt —, si bien que les cas se passaient le même état : le dossier ouvert
    par le premier faisait échouer le second, qui vérifiait justement qu'aucun
    dossier n'était ouvert. Le cache est donc vidé autour de chaque cas.
    """
    from app.contextes.creation_entreprise.adaptateurs.entrant.routes_http import (
        _depot_memoire,
        depot,
    )

    _depot_memoire.cache_clear()
    try:
        yield depot()
    finally:
        _depot_memoire.cache_clear()


def test_une_creation_payee_ouvre_son_dossier(magasin):
    ouvrir_le_dossier_de_creation(_encaissement())

    dossier = magasin.lire(_reference_du_dossier(DOSSIER))
    assert dossier.denomination_souhaitee == "SYLVIE COUTURE SARL"
    assert dossier.forme_juridique.value == "SARL"
    assert dossier.siege == "Bonabéri, Douala"
    assert dossier.capital == Decimal("1000000")
    assert dossier.fondateur.courriel == "sylvie.mbida@exemple.cm"
    assert dossier.fondateur.telephone == "+237699112233"
    # ⚠️ PERSONNE N'A CLIQUÉ, ET LE DOSSIER DOIT LE DIRE. Six mois plus tard,
    # « qui a ouvert ce dossier ? » doit avoir une réponse, et « le système, à
    # l'encaissement » en est une.
    assert any(jalon.par == "système:encaissement" for jalon in dossier.jalons)


def test_la_reference_vient_du_dossier_commercial(magasin):
    """⚠️ C'est ce qui rend l'abonné rejouable, et c'est lisible par surcroît."""
    ouvrir_le_dossier_de_creation(_encaissement())
    assert _reference_du_dossier(DOSSIER) == f"CRE-{DOSSIER}"
    magasin.lire(f"CRE-{DOSSIER}")


def test_une_adhesion_payee_n_ouvre_aucun_dossier(magasin):
    """Une adhésion n'a rien à immatriculer : elle ouvre un espace, et c'est tout."""
    ouvrir_le_dossier_de_creation(_encaissement(service="ADHESION"))

    with pytest.raises(DossierCreationIntrouvable):
        magasin.lire(_reference_du_dossier(DOSSIER))


def test_l_evenement_rejoue_n_ouvre_pas_un_second_dossier(magasin):
    """⚠️ Le relais garantit « au moins une fois ». Deux dossiers pour un seul
    client payé se verraient au pipeline, et se factureraient deux fois."""
    ouvrir_le_dossier_de_creation(_encaissement())
    premier = magasin.lire(_reference_du_dossier(DOSSIER))

    ouvrir_le_dossier_de_creation(_encaissement())
    second = magasin.lire(_reference_du_dossier(DOSSIER))

    assert premier.ouvert_le == second.ouvert_le


@pytest.mark.parametrize(
    "manquant", ["denomination_souhaitee", "forme_juridique", "activite", "siege"]
)
def test_un_fait_fondateur_manquant_n_ouvre_rien(magasin, manquant, caplog):
    """⚠️ CE QUI MANQUE N'EST PAS INVENTÉ.

    Un dossier ouvert au nom de « Société à nommer » partirait au greffe. On
    préfère un dossier non ouvert, dit dans le journal d'exploitation, qu'un
    dossier ouvert sur une donnée fabriquée.
    """
    faits = dict(FAITS)
    del faits[manquant]

    with caplog.at_level("WARNING"):
        ouvrir_le_dossier_de_creation(_encaissement(faits_fondateurs=faits))

    with pytest.raises(DossierCreationIntrouvable):
        magasin.lire(_reference_du_dossier(DOSSIER))
    assert manquant in caplog.text


def test_une_forme_juridique_inconnue_n_ouvre_rien(magasin, caplog):
    """Le questionnaire et le portefeuille doivent parler la même langue.

    ⚠️ Ils ne la parlaient pas : le questionnaire proposait
    `SARL_UNIPERSONNELLE` et `ETABLISSEMENT` là où le portefeuille dit `SARLU` et
    `ETS`. Corrigé au référentiel le 27 septembre ; ce cas garde la frontière.
    """
    faits = dict(FAITS, forme_juridique="SARL_UNIPERSONNELLE")

    with caplog.at_level("WARNING"):
        ouvrir_le_dossier_de_creation(_encaissement(faits_fondateurs=faits))

    with pytest.raises(DossierCreationIntrouvable):
        magasin.lire(_reference_du_dossier(DOSSIER))
    assert "SARL_UNIPERSONNELLE" in caplog.text


def test_le_capital_illisible_n_empeche_pas_l_ouverture(magasin):
    """Le capital est facultatif : il se porte plus tard, et une valeur illisible
    ne doit pas retenir un dossier que quatre faits suffisent à ouvrir."""
    ouvrir_le_dossier_de_creation(
        _encaissement(faits_fondateurs=dict(FAITS, capital_social="à confirmer"))
    )

    dossier = magasin.lire(_reference_du_dossier(DOSSIER))
    assert dossier.capital is None
