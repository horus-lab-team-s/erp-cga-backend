"""Le dépôt d'une demande, du formulaire au dossier.

Ces tests montent le cas d'usage sur le dépôt en mémoire. Ils vérifient ce que
le domaine seul ne peut pas dire : que le rattachement choisit le bon fil, qu'il
n'en choisit aucun quand il ne faut pas, et que le résultat est le même quel que
soit l'ordre dans lequel la base rend ses lignes.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.contextes.souscription.adaptateurs.sortant.depots_memoire import (
    DepotDossiersMemoire,
    DossierIntrouvable,
)
from app.contextes.souscription.application.acquisition import deposer_une_demande
from app.contextes.souscription.domaine.demande_de_contact import (
    Canal,
    Consentement,
    DemandeDeContact,
)
from app.contextes.souscription.domaine.dossier_commercial import EtatDossier

LE_JOUR = datetime(2026, 9, 9, 10, 0)


def _demande(identifiant: str, **surcharges) -> DemandeDeContact:
    defauts = {
        "identifiant": identifiant,
        "deposee_le": LE_JOUR,
        "nom": "Abena Ndzana",
        "telephone": "699112233",
        "service_souhaite": "creation-sarl",
        "canal_prefere": Canal.WHATSAPP,
        "consentement": Consentement(
            accorde=True,
            recueilli_le=LE_JOUR,
            version_du_texte="consentement-whatsapp-v1",
        ),
    }
    return DemandeDeContact(**{**defauts, **surcharges})


@pytest.fixture
def depot() -> DepotDossiersMemoire:
    return DepotDossiersMemoire()


class TestPremierDepot:
    def test_il_ouvre_un_dossier_depose(self, depot):
        resultat = deposer_une_demande(_demande("dc-1"), depot, reference="dos-1")
        assert resultat.rattachee is False
        assert resultat.dossier.etat is EtatDossier.DEPOSEE
        assert resultat.dossier.responsable is None

    def test_le_dossier_est_relisible(self, depot):
        deposer_une_demande(_demande("dc-1"), depot, reference="dos-1")
        assert depot.lire("dos-1").demande.identifiant == "dc-1"

    def test_un_dossier_inconnu_leve(self, depot):
        with pytest.raises(DossierIntrouvable):
            depot.lire("dos-jamais-vu")

    def test_le_depot_n_affecte_personne(self, depot):
        """L'affectation est l'étape 2 : la fondre ici ferait perdre sa demande
        à un visiteur parce qu'un annuaire est lent."""
        resultat = deposer_une_demande(_demande("dc-1"), depot, reference="dos-1")
        assert resultat.dossier.affecte_le is None


class TestRattachement:
    def test_un_second_depot_rejoint_le_fil(self, depot):
        deposer_une_demande(_demande("dc-1"), depot, reference="dos-1")
        second = _demande(
            "dc-2",
            deposee_le=LE_JOUR + timedelta(hours=2),
            message="j'oubliais : j'ai déjà un NIU",
        )
        resultat = deposer_une_demande(second, depot, reference="dos-2")

        assert resultat.rattachee is True
        assert resultat.dossier.reference == "dos-1"
        assert len(depot.lister()) == 1

    def test_le_message_du_second_depot_n_est_pas_perdu(self, depot):
        """C'est souvent la précision que le visiteur revenait ajouter."""
        deposer_une_demande(_demande("dc-1"), depot, reference="dos-1")
        deposer_une_demande(
            _demande(
                "dc-2",
                deposee_le=LE_JOUR + timedelta(hours=2),
                message="j'ai déjà un NIU",
            ),
            depot,
            reference="dos-2",
        )
        dossier = depot.lire("dos-1")
        assert dossier.derniere_demande.message == "j'ai déjà un NIU"
        assert dossier.demande.message is None

    def test_le_rattachement_ne_fait_pas_repartir_la_veille(self, depot):
        """Sinon un robot échapperait indéfiniment à l'alerte en soumettant une
        fois par heure."""
        deposer_une_demande(_demande("dc-1"), depot, reference="dos-1")
        deposer_une_demande(
            _demande("dc-2", deposee_le=LE_JOUR + timedelta(hours=5)),
            depot,
            reference="dos-2",
        )
        assert depot.lire("dos-1").depuis_le == LE_JOUR

    def test_un_troisieme_depot_rejoint_aussi(self, depot):
        """Le doublon se mesure contre **toutes** les demandes du dossier :
        comparer à la seule demande d'origine ferait sortir de la fenêtre un fil
        pourtant vivant, au troisième envoi d'un visiteur insistant."""
        deposer_une_demande(_demande("dc-1"), depot, reference="dos-1")
        for rang, heures in ((2, 20), (3, 38)):
            resultat = deposer_une_demande(
                _demande(f"dc-{rang}", deposee_le=LE_JOUR + timedelta(hours=heures)),
                depot,
                reference=f"dos-{rang}",
            )
            assert resultat.rattachee is True, f"dépôt {rang} détaché"
        assert len(depot.lire("dos-1").toutes_les_demandes) == 3

    def test_hors_fenetre_un_nouveau_dossier_s_ouvre(self, depot):
        deposer_une_demande(_demande("dc-1"), depot, reference="dos-1")
        resultat = deposer_une_demande(
            _demande("dc-2", deposee_le=LE_JOUR + timedelta(hours=30)),
            depot,
            reference="dos-2",
        )
        assert resultat.rattachee is False
        assert len(depot.lister()) == 2

    def test_un_autre_numero_ouvre_son_dossier(self, depot):
        deposer_une_demande(_demande("dc-1"), depot, reference="dos-1")
        resultat = deposer_une_demande(
            _demande(
                "dc-2",
                telephone="677889900",
                deposee_le=LE_JOUR + timedelta(minutes=1),
            ),
            depot,
            reference="dos-2",
        )
        assert resultat.rattachee is False


class TestCeQuiNAccueillePas:
    def test_un_dossier_paye_n_accueille_pas(self, depot):
        """Un dépôt qui suit un paiement est une nouvelle intention. Le
        rattacher ferait apparaître un client qui vient d'acheter dans la file
        des demandes à traiter."""
        deposer_une_demande(_demande("dc-1"), depot, reference="dos-1")
        dossier = depot.lire("dos-1")
        for geste in (
            lambda d: d.affecter("resp-1", LE_JOUR, motif="m"),
            lambda d: d.premier_contact(LE_JOUR),
            lambda d: d.qualifier(LE_JOUR),
            lambda d: d.chiffrer(LE_JOUR),
            lambda d: d.emettre_la_proforma(LE_JOUR),
            lambda d: d.accepter(LE_JOUR),
            lambda d: d.encaisser(LE_JOUR),
        ):
            dossier = geste(dossier)
        depot.enregistrer(dossier)

        resultat = deposer_une_demande(
            _demande("dc-2", deposee_le=LE_JOUR + timedelta(hours=1)),
            depot,
            reference="dos-2",
        )
        assert resultat.rattachee is False
        assert resultat.dossier.reference == "dos-2"

    def test_un_dossier_classe_n_accueille_pas(self, depot):
        deposer_une_demande(_demande("dc-1"), depot, reference="dos-1")
        depot.enregistrer(
            depot.lire("dos-1").classer_sans_suite(LE_JOUR, motif="hors périmètre")
        )
        resultat = deposer_une_demande(
            _demande("dc-2", deposee_le=LE_JOUR + timedelta(hours=1)),
            depot,
            reference="dos-2",
        )
        assert resultat.rattachee is False


class TestOrdreEtDeterminisme:
    def test_le_fil_vivant_l_emporte_sur_le_fil_ferme(self, depot):
        """Deux dossiers, même numéro, même fenêtre. Le rattachement doit viser
        celui qu'un responsable a sous les yeux."""
        deposer_une_demande(_demande("dc-1"), depot, reference="dos-ferme")
        depot.enregistrer(
            depot.lire("dos-ferme").classer_sans_suite(LE_JOUR, motif="doublon")
        )
        deposer_une_demande(
            _demande("dc-2", deposee_le=LE_JOUR + timedelta(minutes=30)),
            depot,
            reference="dos-vivant",
        )

        resultat = deposer_une_demande(
            _demande("dc-3", deposee_le=LE_JOUR + timedelta(hours=1)),
            depot,
            reference="dos-3",
        )
        assert resultat.dossier.reference == "dos-vivant"

    def test_le_resultat_ne_depend_pas_de_l_ordre_rendu_par_le_depot(self, depot):
        """`doublon_parmi` choisit la plus récente, jamais la première rendue.
        Deux formulaires soumis à la même seconde produisent donc le même
        rattachement quelle que soit la ligne lue en premier."""
        deposer_une_demande(_demande("dc-1"), depot, reference="dos-1")
        deposer_une_demande(
            _demande("dc-2", deposee_le=LE_JOUR + timedelta(hours=30)),
            depot,
            reference="dos-2",
        )

        tardif = _demande("dc-3", deposee_le=LE_JOUR + timedelta(hours=31))
        assert len(depot.par_telephone("+237699112233", depuis=LE_JOUR)) == 2

        droit = deposer_une_demande(tardif, depot, reference="dos-3")
        assert droit.dossier.reference == "dos-2"

        # Le même dépôt, servi dans l'ordre inverse, doit conclure pareil.
        neuf = DepotDossiersMemoire()
        deposer_une_demande(_demande("dc-1"), neuf, reference="dos-1")
        deposer_une_demande(
            _demande("dc-2", deposee_le=LE_JOUR + timedelta(hours=30)),
            neuf,
            reference="dos-2",
        )
        a_l_envers = _DepotInverse(neuf)
        inverse = deposer_une_demande(tardif, a_l_envers, reference="dos-3")
        assert inverse.dossier.reference == "dos-2"


class _DepotInverse:
    """Le même dépôt, dont les lectures rendent les lignes à l'envers.

    Aucune base ne garantit un ordre sans `ORDER BY`, et un cas d'usage qui
    dépendrait de l'ordre reçu se comporterait différemment le jour où le plan
    d'exécution change. Ce faux le prouve plutôt que de l'espérer.
    """

    def __init__(self, vrai) -> None:
        self._vrai = vrai

    def par_telephone(self, telephone, *, depuis):
        return list(reversed(self._vrai.par_telephone(telephone, depuis=depuis)))

    def ouverts(self, *, etat=None):
        return list(reversed(self._vrai.ouverts(etat=etat)))

    def lire(self, reference):
        return self._vrai.lire(reference)

    def enregistrer(self, dossier) -> None:
        self._vrai.enregistrer(dossier)


class TestFenetreConfigurable:
    def test_elle_se_passe_en_argument(self, depot):
        """Le centre voudra l'allonger après trois mois d'usage réel, et ce
        changement ne doit demander ni déploiement ni développeur."""
        deposer_une_demande(_demande("dc-1"), depot, reference="dos-1")
        tardif = _demande("dc-2", deposee_le=LE_JOUR + timedelta(days=3))

        detache = deposer_une_demande(tardif, depot, reference="dos-2")
        assert detache.rattachee is False

        neuf = DepotDossiersMemoire()
        deposer_une_demande(_demande("dc-1"), neuf, reference="dos-1")
        rattache = deposer_une_demande(
            tardif, neuf, reference="dos-2", fenetre=timedelta(days=7)
        )
        assert rattache.rattachee is True


class TestLaLectureEstBornee:
    """Le seul test du lot qui regarde l'appel plutôt que le résultat, et c'est
    délibéré.

    La fenêtre est appliquée **deux fois** : par la requête, qui borne ce qu'on
    lit, et par `doublon_parmi`, qui décide. Élargir la requête ne change donc
    aucun verdict, et aucune assertion sur un résultat ne pourrait s'en
    apercevoir. C'est le mutant qui a survécu à tous les autres tests.

    Ce qui change, c'est le nombre de lignes lues à chaque dépôt de formulaire,
    robots compris. Une borne dont personne ne vérifie qu'elle est posée finit
    par ne plus l'être, et le jour où cela se voit, c'est en production sous
    charge.
    """

    def test_la_requete_ne_remonte_pas_plus_loin_que_la_fenetre(self, depot):
        espion = _DepotEspion(depot)
        demande = _demande("dc-1", deposee_le=LE_JOUR)
        deposer_une_demande(
            demande, espion, reference="dos-1", fenetre=timedelta(hours=24)
        )
        assert espion.depuis == LE_JOUR - timedelta(hours=24)

    def test_elle_suit_la_fenetre_configuree(self, depot):
        espion = _DepotEspion(depot)
        deposer_une_demande(
            _demande("dc-1"), espion, reference="dos-1", fenetre=timedelta(days=7)
        )
        assert espion.depuis == LE_JOUR - timedelta(days=7)


class _DepotEspion:
    """Le vrai dépôt, dont on retient la borne demandée."""

    def __init__(self, vrai) -> None:
        self._vrai = vrai
        self.depuis: datetime | None = None

    def par_telephone(self, telephone, *, depuis):
        self.depuis = depuis
        return self._vrai.par_telephone(telephone, depuis=depuis)

    def ouverts(self, *, etat=None):
        return self._vrai.ouverts(etat=etat)

    def lire(self, reference):
        return self._vrai.lire(reference)

    def enregistrer(self, dossier) -> None:
        self._vrai.enregistrer(dossier)


class TestVeille:
    def test_les_dossiers_ouverts_sortent_du_plus_ancien_au_plus_recent(self, depot):
        """C'est celui qui attend depuis le plus longtemps qu'on traite
        d'abord."""
        for rang, heures in ((1, 0), (2, 3), (3, 1)):
            deposer_une_demande(
                _demande(
                    f"dc-{rang}",
                    telephone=f"69911223{rang}",
                    deposee_le=LE_JOUR + timedelta(hours=heures),
                ),
                depot,
                reference=f"dos-{rang}",
            )
        assert [d.reference for d in depot.ouverts()] == ["dos-1", "dos-3", "dos-2"]

    def test_un_dossier_ferme_ne_figure_plus_dans_la_file(self, depot):
        deposer_une_demande(_demande("dc-1"), depot, reference="dos-1")
        assert len(depot.ouverts()) == 1
        depot.enregistrer(depot.lire("dos-1").classer_sans_suite(LE_JOUR, motif="m"))
        assert depot.ouverts() == []

    def test_la_file_se_filtre_par_etat(self, depot):
        """L'ordonnanceur balaye état par état, avec un délai propre à chacun.
        Charger tout pour n'en garder qu'un dixième ferait grossir le balayage
        avec le portefeuille."""
        for rang in (1, 2):
            deposer_une_demande(
                _demande(f"dc-{rang}", telephone=f"69911223{rang}"),
                depot,
                reference=f"dos-{rang}",
            )
        depot.enregistrer(
            depot.lire("dos-2").affecter("resp-1", LE_JOUR, motif="proximité")
        )
        assert [d.reference for d in depot.ouverts(etat=EtatDossier.DEPOSEE)] == ["dos-1"]
        assert [d.reference for d in depot.ouverts(etat=EtatDossier.AFFECTEE)] == ["dos-2"]
