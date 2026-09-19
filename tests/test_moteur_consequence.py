"""La conséquence typée, et la valorisation comme port.

Le pas 4 de la généralisation : `ConsequenceFiscale` cesse d'être *la* conséquence pour
devenir *une* conséquence. Ces tests vérifient les deux moitiés du contrat — que le noyau
sait décrire les quatre natures sans rien connaître d'un domaine, et que le domaine fiscal
sait chiffrer les siennes sans que le noyau s'en mêle.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contextes.conformite.domaine.entites import (
    ConsequenceFiscale,
    Document,
    FactureAControler,
    LigneFacture,
    Montants,
    Partie,
    RegimeEmetteur,
)
from app.contextes.conformite.domaine.valorisation import valoriser_fiscalement
from app.moteur.consequence import (
    Consequence,
    TypeConsequence,
    Valorisation,
    enjeu_declare,
    sans_enjeu,
)

D = Decimal


def _faits() -> dict:
    """Les faits d'une facture, tels que le moteur les passe à une valorisation."""
    return FactureAControler(
        document=Document(reference="F-VAL-0001", date_emission=date(2026, 7, 15)),
        emetteur=Partie(niu="M053311224455R", niu_actif=True, regime=RegimeEmetteur.REEL),
        destinataire=Partie(niu="M081234567890P", regime=RegimeEmetteur.REEL),
        montants=Montants(total_ht=D(100_000), total_tva=D(19_250), total_ttc=D(119_250)),
        lignes=[LigneFacture(designation="Ciment", montant_ht=D(100_000))],
    ).faits()


class TestConsequenceDuNoyau:
    """Le noyau décrit quatre natures et n'en connaît aucun métier."""

    def test_les_quatre_natures_existent(self):
        assert {membre.value for membre in TypeConsequence} == {
            "MONTANT",
            "POINTS",
            "NIVEAU",
            "AJUSTEMENT",
        }

    def test_une_consequence_de_points_porte_sa_valeur(self):
        consequence = Consequence(
            type=TypeConsequence.POINTS,
            libelle="Volume de pièces élevé",
            unite="points",
            valeur_numerique=D(18),
        )
        assert enjeu_declare(consequence, {}) == D(18)

    def test_une_consequence_de_niveau_porte_sa_classification(self):
        consequence = Consequence(
            type=TypeConsequence.NIVEAU,
            libelle="Séparation des tâches non respectée",
            valeur_nominale="ELEVE",
        )
        assert consequence.valeur_nominale == "ELEVE"
        # Un niveau ne se chiffre pas : c'est l'agrégateur qui retiendra le plus élevé.
        assert enjeu_declare(consequence, {}) is None

    def test_un_niveau_sans_classification_est_refuse(self):
        with pytest.raises(ValidationError, match="valeur nominale"):
            Consequence(type=TypeConsequence.NIVEAU, libelle="Sans valeur")

    def test_des_points_sans_valeur_sont_refuses(self):
        with pytest.raises(ValidationError, match="valeur numérique"):
            Consequence(type=TypeConsequence.POINTS, libelle="Sans valeur")

    def test_un_montant_ne_declare_pas_sa_valeur(self):
        """Un MONTANT se calcule à partir des faits : le déclarer serait le figer."""
        consequence = Consequence(type=TypeConsequence.MONTANT, libelle="Taxe non déductible")
        assert consequence.valeur_numerique is None

    def test_la_valorisation_par_defaut_ne_chiffre_rien(self):
        """Mieux vaut un constat sans montant qu'un montant inventé."""
        consequence = Consequence(type=TypeConsequence.MONTANT)
        assert sans_enjeu(consequence, _faits()) is None

    @pytest.mark.parametrize(
        "valorisation", [sans_enjeu, enjeu_declare, valoriser_fiscalement]
    )
    def test_toute_valorisation_honore_le_meme_contrat(self, valorisation: Valorisation):
        """Le port se vérifie par son comportement, pas par `isinstance`.

        `Valorisation` n'a que `__call__` : un contrôle de type ne dirait que « c'est
        appelable », ce que n'importe quelle fonction satisfait. Ce qui compte est
        qu'elle accepte le couple attendu et rende un nombre ou rien.
        """
        resultat = valorisation(ConsequenceFiscale(tva_deductible=False), _faits())
        assert resultat is None or isinstance(resultat, Decimal)


class TestConsequenceFiscale:
    """Le domaine fiscal spécialise la conséquence du noyau, il ne la remplace pas."""

    def test_elle_est_une_consequence_du_noyau(self):
        assert issubclass(ConsequenceFiscale, Consequence)

    def test_son_type_est_toujours_un_montant(self):
        assert ConsequenceFiscale().type is TypeConsequence.MONTANT

    def test_elle_reste_constructible_sans_argument(self):
        """Les règles qui ne déclarent pas de conséquence s'appuient sur ce défaut."""
        assert ConsequenceFiscale().tva_deductible is None


class TestValorisationFiscale:
    """Le barème de chiffrage du § 4 de `03-moteur-conformite.md`, cas par cas."""

    def test_taxe_seule_rejetee_vaut_le_montant_de_la_taxe(self):
        consequence = ConsequenceFiscale(tva_deductible=False)
        assert valoriser_fiscalement(consequence, _faits()) == D(19_250)

    def test_charge_seule_rejetee_vaut_le_montant_hors_taxes(self):
        consequence = ConsequenceFiscale(charge_deductible=False)
        assert valoriser_fiscalement(consequence, _faits()) == D(100_000)

    def test_les_deux_rejetees_valent_le_total(self):
        consequence = ConsequenceFiscale(tva_deductible=False, charge_deductible=False)
        assert valoriser_fiscalement(consequence, _faits()) == D(119_250)

    def test_aucun_rejet_ne_vaut_aucun_enjeu(self):
        """Le cas le plus important. Afficher zéro laisserait croire à un enjeu nul là
        où il n'y a pas d'enjeu du tout, et un réviseur en déduirait qu'il peut passer."""
        consequence = ConsequenceFiscale(rectification_requise=True)
        assert valoriser_fiscalement(consequence, _faits()) is None

    def test_une_deductibilite_confirmee_ne_vaut_aucun_enjeu(self):
        """`True` vaut « déductible », pas « rejeté ». Seul `False` rejette."""
        consequence = ConsequenceFiscale(tva_deductible=True, charge_deductible=True)
        assert valoriser_fiscalement(consequence, _faits()) is None

    def test_une_consequence_d_un_autre_domaine_n_est_pas_chiffree(self):
        """On rend None plutôt que de chiffrer au hasard une conséquence qu'on ne
        reconnaît pas."""
        etrangere = Consequence(type=TypeConsequence.POINTS, valeur_numerique=D(5))
        assert valoriser_fiscalement(etrangere, _faits()) is None


class TestValorisationInjectable:
    """La preuve que le pas 4 a atteint son but.

    Si le moteur accepte une autre façon de chiffrer sans qu'on le modifie, alors le
    chiffrage a bien quitté le noyau. Tant que ce test n'existe pas, l'injection est une
    intention et non une propriété.
    """

    def test_le_moteur_emploie_la_valorisation_qu_on_lui_donne(self, regles, parametres):
        from app.contextes.conformite.application.moteur_conformite import MoteurConformite

        def chiffrer_a_plat(consequence, faits):
            """Valorisation d'un domaine imaginaire : tout constat vaut un point."""
            return D(1)

        facture = FactureAControler(
            document=Document(reference="F-INJ-0001", date_emission=date(2026, 7, 15)),
            emetteur=Partie(niu="M053311224455R", niu_actif=False, regime=RegimeEmetteur.REEL),
            destinataire=Partie(niu="M081234567890P", regime=RegimeEmetteur.REEL),
            montants=Montants(total_ht=D(100_000), total_tva=D(19_250), total_ttc=D(119_250)),
            lignes=[LigneFacture(designation="Ciment", montant_ht=D(100_000))],
        )

        par_defaut = MoteurConformite(regles, parametres).controler(facture)
        injecte = MoteurConformite(regles, parametres, chiffrer_a_plat).controler(facture)

        assert par_defaut.constats, "la facture doit produire au moins un constat"
        assert [c.code_regle for c in injecte.constats] == [
            c.code_regle for c in par_defaut.constats
        ], "changer le chiffrage ne change pas quelles règles se déclenchent"
        assert all(c.enjeu == D(1) for c in injecte.constats), (
            "le moteur doit employer la valorisation fournie, pas la sienne"
        )
