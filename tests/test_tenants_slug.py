"""Le slug d'un tenant : ce qu'on accepte, ce qu'on refuse, ce qu'on propose.

Premier pas du socle multi-tenant. Purement du domaine : aucune base, aucun réseau, aucune
horloge. C'est ce qui permet d'écrire les vingt cas limites qui suivent sans montage.

Ils méritent d'être écrits parce qu'un slug est **inréattribuable** : une fois donné, il
appartient à ce tenant pour toujours. Une erreur de validation ne se corrige pas en
changeant une ligne, elle laisse une adresse gelée.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.contextes.tenants.adaptateurs.sortant.noms_reserves import charger_les_noms_reserves
from app.contextes.tenants.domaine.slug import (
    LONGUEUR_MAXIMALE,
    LONGUEUR_MINIMALE,
    MotifRejet,
    SlugInvalide,
    normaliser,
    proposer,
    valider,
)

RACINE_TENANTS = Path(__file__).resolve().parents[2] / "Docs" / "referentiel" / "tenants"


@pytest.fixture(scope="session")
def reserves() -> frozenset[str]:
    return charger_les_noms_reserves(RACINE_TENANTS)


class TestNormalisation:
    """Ce que devient une saisie libre."""

    @pytest.mark.parametrize(
        ("saisi", "attendu"),
        [
            ("Station Service Bonaberi", "station-service-bonaberi"),
            ("SOCIETE GENERALE", "societe-generale"),
            ("  espaces  autour  ", "espaces-autour"),
            ("deja-correct", "deja-correct"),
            ("Multiples   espaces", "multiples-espaces"),
            ("Ponctuation, et. points!", "ponctuation-et-points"),
            ("chiffres 2026 dedans", "chiffres-2026-dedans"),
        ],
    )
    def test_une_saisie_devient_un_nom_d_hote(self, saisi: str, attendu: str):
        assert normaliser(saisi) == attendu

    @pytest.mark.parametrize(
        ("saisi", "attendu"),
        [
            ("Société Générale", "societe-generale"),
            ("Bonabéri", "bonaberi"),
            ("Café Crème", "cafe-creme"),
            ("Ndjaména", "ndjamena"),
        ],
    )
    def test_les_accents_sont_depliés_et_non_supprimes(self, saisi: str, attendu: str):
        """« Société » donne `societe` et non `socit`.

        C'est la différence entre un nom qu'un client reconnaît et un nom qu'il refuse.
        """
        assert normaliser(saisi) == attendu

    def test_les_tirets_ne_s_accumulent_pas_aux_bords(self):
        assert normaliser("--- Encadré ---") == "encadre"

    def test_les_tirets_internes_se_rassemblent(self):
        assert normaliser("S.A.R.L.  Batiment + Plus") == "s-a-r-l-batiment-plus"

    def test_une_saisie_sans_lettre_ni_chiffre_donne_le_vide(self):
        assert normaliser("!!! ???") == ""


class TestValidation:
    """Les six motifs de refus, chacun avec sa raison d'être."""

    def test_un_slug_ordinaire_passe(self, reserves):
        valider("station-bonaberi", reserves)

    def test_le_vide_est_refuse(self):
        with pytest.raises(SlugInvalide) as echec:
            valider("")
        assert echec.value.motif is MotifRejet.VIDE

    def test_trop_court_est_refuse(self):
        with pytest.raises(SlugInvalide) as echec:
            valider("ab")
        assert echec.value.motif is MotifRejet.TROP_COURT

    def test_la_longueur_minimale_exacte_passe(self):
        """Les bornes se testent à la valeur exacte : c'est là qu'on se trompe."""
        valider("a" * LONGUEUR_MINIMALE)

    def test_trop_long_est_refuse(self):
        with pytest.raises(SlugInvalide) as echec:
            valider("a" * (LONGUEUR_MAXIMALE + 1))
        assert echec.value.motif is MotifRejet.TROP_LONG

    def test_la_longueur_maximale_exacte_passe(self):
        valider("a" * LONGUEUR_MAXIMALE)

    @pytest.mark.parametrize(
        "invalide",
        ["-debut", "fin-", "MAJUSCULE", "avec espace", "point.dedans", "accent-é", "sous_ligne"],
    )
    def test_une_forme_qui_n_est_pas_un_nom_d_hote_est_refusee(self, invalide: str):
        """Un nom d'hôte invalide se comporte de façon intermittente : certains résolveurs
        l'acceptent, d'autres non. Le défaut est introuvable parce qu'il ne se reproduit
        pas chez le développeur."""
        with pytest.raises(SlugInvalide) as echec:
            valider(invalide)
        assert echec.value.motif is MotifRejet.FORME_INVALIDE

    def test_le_prefixe_international_est_refuse(self):
        with pytest.raises(SlugInvalide) as echec:
            valider("xn--exemple")
        assert echec.value.motif is MotifRejet.PREFIXE_INTERNATIONAL

    def test_un_nom_reserve_est_refuse(self, reserves):
        with pytest.raises(SlugInvalide) as echec:
            valider("api", reserves)
        assert echec.value.motif is MotifRejet.RESERVE

    def test_la_reservation_est_insensible_a_la_casse(self):
        """Réserver « API » doit interdire « api ». L'unicité en base suit la même règle."""
        with pytest.raises(SlugInvalide) as echec:
            valider("api", {"API"})
        assert echec.value.motif is MotifRejet.RESERVE

    def test_sans_liste_aucun_nom_n_est_reserve(self):
        """Le défaut est vide, jamais une liste devinée : un domaine qui réserverait des
        noms sans qu'on le lui demande refuserait des slugs pour une raison invisible."""
        valider("api")

    def test_le_motif_accompagne_toujours_le_message(self):
        """L'interface de souscription doit dire au client ce qu'il faut corriger, ce qui
        demande de savoir de quel cas il s'agit."""
        with pytest.raises(SlugInvalide) as echec:
            valider("ab")
        assert echec.value.motif is MotifRejet.TROP_COURT
        assert str(echec.value)


class TestProposition:
    """Ce que la plateforme suggère, et comment elle évite les collisions connues."""

    def test_une_raison_sociale_donne_un_slug_valide(self, reserves):
        propose = proposer("Station Service Bonaberi", reserves)
        assert propose == "station-service-bonaberi"
        valider(propose, reserves)

    def test_un_nom_deja_pris_est_suffixe(self, reserves):
        assert proposer("Station Service", reserves, {"station-service"}) == "station-service-2"

    def test_le_suffixe_commence_a_deux(self, reserves):
        """Personne n'appelle son entreprise « Machin 1 »."""
        assert proposer("Boulangerie", reserves, {"boulangerie"}) == "boulangerie-2"

    def test_le_suffixe_progresse_jusqu_a_trouver(self, reserves):
        pris = {"boulangerie", "boulangerie-2", "boulangerie-3"}
        assert proposer("Boulangerie", reserves, pris) == "boulangerie-4"

    def test_un_nom_reserve_est_suffixe_plutot_que_refuse(self, reserves):
        """La proposition n'échoue pas : elle propose autre chose, et le client tranche."""
        propose = proposer("API", reserves)
        assert propose == "api-2"
        valider(propose, reserves)

    def test_un_nom_trop_long_est_coupe(self, reserves):
        propose = proposer("Entreprise " * 20, reserves)
        assert len(propose) <= LONGUEUR_MAXIMALE
        valider(propose, reserves)

    def test_la_coupe_ne_laisse_pas_de_tiret_final(self, reserves):
        """Couper au caractère près peut tomber sur un tiret, qui rendrait le nom invalide."""
        propose = proposer("a" * 39 + " suite", reserves)
        assert not propose.endswith("-")
        valider(propose, reserves)

    def test_un_nom_long_et_pris_reste_dans_la_limite(self, reserves):
        base = proposer("Entreprise " * 20, reserves)
        propose = proposer("Entreprise " * 20, reserves, {base})
        assert len(propose) <= LONGUEUR_MAXIMALE
        assert propose.endswith("-2")
        valider(propose, reserves)

    def test_un_nom_trop_court_n_est_pas_invente(self):
        """« SA » donne « sa ». Inventer « sa-entreprise » produirait une adresse que
        personne n'a demandée : on laisse la validation refuser et le client choisir."""
        assert proposer("SA") == "sa"
        with pytest.raises(SlugInvalide):
            valider(proposer("SA"))


class TestListeReservee:
    """Le principe de configuration appliqué aux noms réservés."""

    def test_elle_vient_du_referentiel_et_non_du_code(self, reserves):
        assert {"www", "api", "admin", "mail"} <= reserves

    def test_elle_est_entierement_en_minuscules(self, reserves):
        assert all(nom == nom.lower() for nom in reserves)

    def test_le_fichier_porte_son_fondement(self):
        import yaml

        donnees = yaml.safe_load(
            (RACINE_TENANTS / "noms-reserves.yaml").read_text(encoding="utf-8")
        )
        assert donnees["fondement"]["texte"].strip()
        assert donnees["fondement"]["source"].strip()
        assert donnees["statut"] == "A_VALIDER"

    def test_aucun_nom_reserve_n_est_ecrit_dans_le_domaine(self):
        """Le contrôle qui empêche la régression : si quelqu'un remet une liste en dur
        dans le domaine, ce test le dit."""
        source = (
            Path(__file__).resolve().parents[1]
            / "app" / "contextes" / "tenants" / "domaine" / "slug.py"
        ).read_text(encoding="utf-8")
        for interdit in ('"www"', '"admin"', '"console"'):
            assert interdit not in source, (
                f"{interdit} est revenu dans le code. Les noms réservés vivent dans "
                "Docs/referentiel/tenants/noms-reserves.yaml."
            )
