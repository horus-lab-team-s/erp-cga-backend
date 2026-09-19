"""Clore un exercice, et rouvrir le suivant sur ses soldes.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Sans la clôture, la plateforme ne survit pas à sa deuxième année. L'entité
`Exercice` portait un attribut `clos` depuis le premier jour, et **rien dans le
produit ne le posait jamais** : une capacité sans appelant, comme la veille des
dossiers avant le pas 28 et la permission `CLOTURER_EXERCICE` avant celui-ci.

Fermer ne suffit pas. Un exercice fermé dont les soldes ne passent pas au suivant
laisse une entreprise qui recommence chaque janvier avec une caisse vide, aucun
fournisseur à payer et un capital disparu.

⚠️ **LA DISTINCTION QUE CES CAS GARDENT** tient en deux lignes :

    classes 1 à 5   comptes de situation   reportés, solde pour solde
    classes 6 à 8   comptes de gestion     remis à zéro, jamais reportés

Reporter un compte de gestion doublerait les charges de l'exercice suivant, et le
bénéfice imposable serait faux dès le premier jour, d'un montant que personne ne
saurait retrouver.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.contextes.cloture.domaine.a_nouveau import (
    COMPTE_RESULTAT_NET,
    MotifEmpechement,
    lignes_d_a_nouveau,
    obstacles_a_la_cloture,
)
from app.contextes.comptabilite.api import (
    COMPTES_SYSCOHADA,
    EcritureComptable,
    EtatEcriture,
    LigneEcriture,
    Sens,
    balance,
)
from app.partage.horloge import horloge_figee
from tests.conftest import exige_postgresql, ouvrir_une_session

PLAN = list(COMPTES_SYSCOHADA)
LE_JOUR = date(2026, 6, 30)

#: ⚠️ **Le réviseur, et non le comptable.** `CLOTURER_EXERCICE` n'appartient qu'à
#: lui et à la direction : arrêter les comptes engage le centre au-delà de la
#: saisie, c'est la signature que l'administration lira.
REVISEUR = "a.bouba@cga-brcg.cm"
#: Un comptable au portefeuille duquel ce dossier appartient, pour éprouver qu'il
#: peut saisir sans pouvoir clore.
COMPTABLE = "l.fotso@cga-brcg.cm"
DOSSIER = "M081234567890P"

#: ⚠️ Un motif de la longueur qu'exige la route. L'écrire une fois ici évite
#: qu'un cas le raccourcisse par inadvertance et mesure la validation au lieu de
#: la clôture.
MOTIF = (
    "Comptes arrêtés après revue du dossier et rapprochement des soldes bancaires."
)


def _clore(client, *, appliquer: bool, exercice: str = "2026", motif: str = MOTIF):
    reponse = client.post(
        f"/cloture/dossiers/{DOSSIER}/exercices/{exercice}/cloture"
        f"?appliquer={str(appliquer).lower()}",
        json={"motif": motif},
    )
    assert reponse.status_code == 200, reponse.text
    return reponse.json()


def _exercice(client, libelle: str):
    """L'exercice tel que le portefeuille le rend, ou `None` s'il n'existe pas."""
    reponse = client.get(f"/portefeuille/entreprises/{DOSSIER}")
    assert reponse.status_code == 200, reponse.text
    return next(
        (e for e in reponse.json()["exercices"] if e["libelle"] == libelle), None
    )


def _balance(client, exercice: str):
    reponse = client.get(
        f"/comptabilite/dossiers/{DOSSIER}/balance?exercice={exercice}"
    )
    assert reponse.status_code == 200, reponse.text
    return reponse.json()


def _ecriture(*lignes, numero=1, journal="OD", etat=EtatEcriture.VALIDEE):
    """Une écriture équilibrée, à partir de couples compte/montant signé.

    Un montant positif est au débit, négatif au crédit. La convention est locale
    au fichier et rend les cas lisibles d'un coup d'œil.
    """
    return EcritureComptable(
        journal=journal,
        exercice="2026",
        numero=numero,
        date_operation=LE_JOUR,
        libelle="Opération d'essai",
        piece_justificative=f"PJ-{numero:04d}",
        lignes=[
            LigneEcriture(
                compte=compte,
                libelle=compte,
                sens=Sens.DEBIT if montant > 0 else Sens.CREDIT,
                montant=abs(Decimal(montant)),
            )
            for compte, montant in lignes
        ],
        etat=etat,
        validee_par="a.bouba" if etat is EtatEcriture.VALIDEE else None,
        validee_le=(
            datetime(2026, 7, 1, 9, 0) if etat is EtatEcriture.VALIDEE else None
        ),
    )


#: Un exercice minuscule mais complet : un achat à crédit, une vente encaissée.
#: Charges 6 000 000, produits 9 500 000, donc un bénéfice de 3 500 000.
EXERCICE_COMPLET = [
    _ecriture(("602", 6000000), ("401", -6000000), numero=1),
    _ecriture(("521", 9500000), ("701", -9500000), numero=2),
]


class TestCeQuiPasseEtCeQuiNePasse:
    """⚠️ **La règle la plus structurante de la comptabilité**, et la seule que ce
    module ait besoin de connaître."""

    def test_les_comptes_de_situation_sont_reportes_solde_pour_solde(self):
        lignes = lignes_d_a_nouveau(balance(EXERCICE_COMPLET), plan=PLAN)
        reportes = {x.compte: (x.sens, x.montant) for x in lignes}

        assert reportes["401"] == (Sens.CREDIT, Decimal(6000000)), (
            "la dette fournisseur ne passe pas : l'entreprise rouvrirait sans "
            "personne à payer."
        )
        assert reportes["521"] == (Sens.DEBIT, Decimal(9500000)), (
            "la banque ne passe pas : l'entreprise rouvrirait sans trésorerie."
        )

    def test_les_comptes_de_gestion_ne_sont_jamais_reportes(self):
        """⚠️ Une charge de 2026 ne pèse pas sur 2027 : elle a joué son rôle, elle
        a formé le résultat, et c'est **le résultat** qui passe, pas la charge.

        La reporter doublerait les charges de l'exercice suivant, et rien ne
        distinguerait alors la charge reportée de la charge réelle.
        """
        lignes = lignes_d_a_nouveau(balance(EXERCICE_COMPLET), plan=PLAN)
        classes = {x.compte[0] for x in lignes}
        assert "6" not in classes, [x.compte for x in lignes]
        assert "7" not in classes, [x.compte for x in lignes]

    def test_le_resultat_passe_au_compte_de_capitaux_propres(self):
        """Bénéfice au **crédit**, parce que le résultat enrichit l'entreprise.

        ⚠️ L'inverser ferait boucler le bilan à un signe près, et **le bilan
        boucherait quand même** : l'erreur ne se verrait qu'au compte de résultat,
        c'est-à-dire à la liasse, et des mois plus tard.
        """
        lignes = lignes_d_a_nouveau(balance(EXERCICE_COMPLET), plan=PLAN)
        (resultat_reporte,) = [x for x in lignes if x.compte == COMPTE_RESULTAT_NET]
        assert resultat_reporte.sens is Sens.CREDIT
        assert resultat_reporte.montant == Decimal(3500000)

    def test_une_perte_passe_au_debit_du_meme_compte(self):
        """La contre-épreuve. Sans elle, un signe inversé passerait inaperçu."""
        deficitaire = [
            _ecriture(("602", 9500000), ("401", -9500000), numero=1),
            _ecriture(("521", 6000000), ("701", -6000000), numero=2),
        ]
        lignes = lignes_d_a_nouveau(balance(deficitaire), plan=PLAN)
        (perte,) = [x for x in lignes if x.compte == COMPTE_RESULTAT_NET]
        assert perte.sens is Sens.DEBIT
        assert perte.montant == Decimal(3500000)

    def test_l_a_nouveau_s_equilibre_par_construction(self):
        """⚠️ **Ce n'est pas une vérification ajoutée par prudence.**

        Le contrôle de bouclage dit que la somme des comptes de situation vaut le
        résultat. Formulé autrement : l'écriture de report s'équilibre
        nécessairement, dès lors que la balance de départ est équilibrée. C'est
        la raison pour laquelle l'opération est possible.
        """
        lignes = lignes_d_a_nouveau(balance(EXERCICE_COMPLET), plan=PLAN)
        debit = sum((x.montant for x in lignes if x.au_debit), Decimal(0))
        credit = sum((x.montant for x in lignes if not x.au_debit), Decimal(0))
        assert debit == credit, [(x.compte, x.sens, x.montant) for x in lignes]
        assert debit > 0, "un à-nouveau vide s'équilibre aussi : le cas ne mesure rien"

    def test_un_solde_nul_ne_produit_aucune_ligne(self):
        """Un compte d'attente soldé, une TVA déclarée et payée : rien à reporter.

        ⚠️ Lui écrire une ligne à zéro serait refusé par le pivot, qui exige un
        montant strictement positif, et ce refus serait juste : une ligne à zéro
        est du bruit dans un grand livre qu'on relit à la main pendant dix ans.
        """
        soldes_et_contre_soldes = [
            *EXERCICE_COMPLET,
            _ecriture(("4451", 500000), ("401", -500000), numero=3),
            _ecriture(("401", 500000), ("4451", -500000), numero=4),
        ]
        lignes = lignes_d_a_nouveau(balance(soldes_et_contre_soldes), plan=PLAN)
        assert "4451" not in {x.compte for x in lignes}
        # ⚠️ Et le compte 401, lui, garde son solde : sans cette contre-épreuve,
        # une règle qui supprimerait tous les comptes touchés passerait aussi.
        assert "401" in {x.compte for x in lignes}


class TestCeQuiInterditDeClore:
    """⚠️ **Tout ce qui empêche, d'un coup, jamais le premier seul.**

    Clore est un acte de fin de mission, souvent conduit sous délai : la
    déclaration statistique et fiscale a une date. Rendre le premier obstacle seul
    obligerait le comptable à revenir autant de fois qu'il y a de causes, et
    chacune demande un travail différent.
    """

    def test_un_exercice_sain_ne_rencontre_aucun_obstacle(self):
        """⚠️ La contre-épreuve de tous les cas qui suivent.

        Sans elle, une fonction qui refuserait tout les ferait tous passer.
        """
        obstacles = obstacles_a_la_cloture(
            EXERCICE_COMPLET, balance(EXERCICE_COMPLET), plan=PLAN, deja_clos=False
        )
        assert obstacles == (), [o.motif for o in obstacles]

    def test_un_brouillon_subsistant_est_nomme(self):
        """⚠️ **L'obstacle le plus fréquent**, et celui que nul autre que le
        comptable ne peut lever.

        Un brouillon au moment de clore est soit une écriture à valider, soit une
        écriture à supprimer. Le laisser produirait une comptabilité dont la
        balance ne dit pas la même chose que le journal : il n'entre pas dans les
        soldes, mais il occupe un numéro, et le vérificateur qui compare les deux
        trouve un trou.
        """
        avec_brouillon = [
            *EXERCICE_COMPLET,
            _ecriture(("602", 10000), ("401", -10000), numero=3,
                      etat=EtatEcriture.BROUILLON),
        ]
        obstacles = obstacles_a_la_cloture(
            avec_brouillon, balance(avec_brouillon), plan=PLAN, deja_clos=False
        )
        motifs = [o.motif for o in obstacles]
        assert MotifEmpechement.BROUILLON_SUBSISTANT in motifs, motifs
        (brouillon,) = [
            o for o in obstacles if o.motif is MotifEmpechement.BROUILLON_SUBSISTANT
        ]
        # ⚠️ Nommer les écritures en cause, sans quoi le comptable les cherche.
        assert brouillon.en_cause, brouillon
        assert all("OD" in cle for cle in brouillon.en_cause)

    def test_un_exercice_deja_clos_refuse_une_seconde_cloture(self):
        """⚠️ Clore deux fois produirait **un second à-nouveau**, et doublerait
        tous les soldes de l'exercice suivant."""
        obstacles = obstacles_a_la_cloture(
            EXERCICE_COMPLET, balance(EXERCICE_COMPLET), plan=PLAN, deja_clos=True
        )
        assert MotifEmpechement.EXERCICE_DEJA_CLOS in [o.motif for o in obstacles]

    def test_un_exercice_vide_ne_se_clot_pas(self):
        """Clore un exercice vide n'apporte rien, et interdirait d'y saisir la
        comptabilité qui reste peut-être à reprendre."""
        obstacles = obstacles_a_la_cloture([], [], plan=PLAN, deja_clos=False)
        assert MotifEmpechement.RIEN_A_CLORE in [o.motif for o in obstacles]

    def test_un_trou_de_numerotation_arrete_la_cloture(self):
        """⚠️ **Le signal d'alerte le plus fort en contrôle fiscal.**

        L'administration le cherche systématiquement. Clore par-dessus reviendrait
        à figer l'anomalie, et le vérificateur la trouverait dans des livres que le
        cabinet a arrêtés et signés.
        """
        troue = [
            EXERCICE_COMPLET[0],
            _ecriture(("521", 9500000), ("701", -9500000), numero=7),
        ]
        obstacles = obstacles_a_la_cloture(
            troue, balance(troue), plan=PLAN, deja_clos=False
        )
        assert MotifEmpechement.SEQUENCE_TROUEE in [o.motif for o in obstacles]

    def test_un_compte_hors_plan_arrete_la_cloture(self):
        """Son solde ne saurait pas où atterrir, et disparaîtrait en silence."""
        soldes = balance(EXERCICE_COMPLET)
        ampute = [c for c in PLAN if c.numero != "701"]
        obstacles = obstacles_a_la_cloture(
            EXERCICE_COMPLET, soldes, plan=ampute, deja_clos=False
        )
        (hors,) = [
            o for o in obstacles if o.motif is MotifEmpechement.COMPTE_HORS_PLAN
        ]
        assert "701" in hors.en_cause

    def test_plusieurs_causes_se_rendent_ensemble(self):
        """⚠️ **La règle du fichier**, et elle se mesure ici.

        Un exercice qui porte à la fois un brouillon et un trou de numérotation
        doit rendre les deux : chacun demande un travail différent, et le
        comptable ne reviendra pas deux fois.
        """
        malade = [
            EXERCICE_COMPLET[0],
            _ecriture(("521", 9500000), ("701", -9500000), numero=7),
            _ecriture(("602", 10000), ("401", -10000), numero=9,
                      etat=EtatEcriture.BROUILLON),
        ]
        motifs = {
            o.motif
            for o in obstacles_a_la_cloture(
                malade, balance(malade), plan=PLAN, deja_clos=True
            )
        }
        assert MotifEmpechement.BROUILLON_SUBSISTANT in motifs
        assert MotifEmpechement.SEQUENCE_TROUEE in motifs
        assert MotifEmpechement.EXERCICE_DEJA_CLOS in motifs
        assert len(motifs) >= 3, motifs

    def test_un_bouclage_rompu_ne_double_pas_une_balance_fausse(self):
        """⚠️ Le bouclage n'a de sens que sur une balance équilibrée.

        Le tester sur une balance fausse rendrait **deux obstacles pour une seule
        cause**, et le comptable chercherait deux corrections là où il n'y en a
        qu'une.
        """
        from app.contextes.comptabilite.api import SoldeCompte

        boiteuse = [
            SoldeCompte(compte="602", total_debit=Decimal(100), total_credit=Decimal(0)),
            SoldeCompte(compte="401", total_debit=Decimal(0), total_credit=Decimal(90)),
        ]
        motifs = [
            o.motif
            for o in obstacles_a_la_cloture(
                EXERCICE_COMPLET, boiteuse, plan=PLAN, deja_clos=False
            )
        ]
        assert MotifEmpechement.BALANCE_DESEQUILIBREE in motifs
        assert MotifEmpechement.BOUCLAGE_ROMPU not in motifs, (
            "deux obstacles pour une seule cause : le comptable cherchera deux "
            "corrections là où il n'y en a qu'une."
        )


class TestLaClotureParLaRoute:
    """L'acte entier : PostgreSQL, HTTP, le dossier de démonstration.

    ⚠️ Les cas de domaine plus haut mesurent le calcul. Ils ne disent rien de ce
    que la **route** en fait : quelle permission elle exige, ce qu'elle écrit, et
    si l'exercice suivant s'ouvre vraiment. C'est la leçon de la recette de
    production, qui a trouvé trois assertions creuses derrière cinq cas verts.
    """

    pytestmark = exige_postgresql

    @pytest.fixture(autouse=True)
    def _au_lendemain_de_l_exercice(self):
        """⚠️ **Ces cas closaient 2026 à la date du jour, et c'était le défaut.**

        Écrits en septembre 2026, ils fermaient un exercice qui courait encore, et
        la route l'acceptait (pas 55). Ils se jouent désormais le 15 janvier 2027,
        la date où un cabinet clôt réellement ; le refus en cours d'exercice a son
        propre cas, plus bas.
        """
        with horloge_figee(datetime(2027, 1, 15, 9, 0)):
            yield

    def test_en_cours_d_exercice_la_route_refuse_et_n_ecrit_rien(self, plateforme):
        """Le défaut du pas 55, par la route : le 14 septembre 2026, rien ne se ferme."""
        client = plateforme
        with horloge_figee(datetime(2026, 9, 14, 10, 0)):
            ouvrir_une_session(client, REVISEUR)
            rapport = _clore(client, appliquer=True)
            assert rapport["applique"] is False
            assert rapport["possible"] is False
            (obstacle,) = [
                o for o in rapport["obstacles"]
                if o["motif"] == MotifEmpechement.EXERCICE_NON_TERMINE.value
            ]
            assert "01/01/2027" in obstacle["explication"], obstacle
            assert _exercice(client, "2026")["clos"] is False
            assert _exercice(client, "2027") is None, "la clôture refusée a ouvert 2027"

    def test_le_mode_controle_ne_ferme_rien_et_annonce_l_ouverture(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, REVISEUR)

        rapport = _clore(client, appliquer=False)
        assert rapport["applique"] is False
        assert rapport["possible"] is True, rapport["obstacles"]
        # ⚠️ Le jeu de démonstration s'arrête à 2026 : l'exercice suivant n'existe
        # pas, et le réviseur doit savoir qu'il va **créer** un exercice, pas
        # seulement en fermer un.
        assert rapport["suivant_ouvert_par_la_cloture"] is True
        assert rapport["exercice_suivant"] == "2027"
        assert rapport["lignes_reportees"] > 0
        assert rapport["cle_a_nouveau"] is None

        assert _exercice(client, "2026")["clos"] is False, (
            "le mode contrôle a fermé l'exercice : ce n'est plus un contrôle."
        )
        assert _exercice(client, "2027") is None

    def test_clore_ferme_ouvre_et_reporte(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, REVISEUR)
        avant = _balance(client, "2026")
        assert avant, "l'exercice de démonstration est vide : le cas ne mesure rien"

        rapport = _clore(client, appliquer=True)
        assert rapport["applique"] is True, rapport["obstacles"]

        # ── 1 · L'exercice est fermé, et plus rien ne s'y saisit ──────────────
        assert _exercice(client, "2026")["clos"] is True
        ouvrir_une_session(client, COMPTABLE)
        refus = client.post(
            f"/comptabilite/dossiers/{DOSSIER}/ecritures",
            json={
                "journal": "OD",
                "exercice": "2026",
                "date_operation": "2026-11-02",
                "libelle": "Une écriture de trop",
                "piece_justificative": "PJ-X",
                "lignes": [
                    {"compte": "602", "libelle": "x", "sens": "DEBIT", "montant": "1000"},
                    {"compte": "401", "libelle": "x", "sens": "CREDIT", "montant": "1000"},
                ],
            },
        )
        assert refus.status_code == 409, refus.text

        # ── 2 · L'exercice suivant existe et il est ouvert ────────────────────
        ouvrir_une_session(client, REVISEUR)
        suivant = _exercice(client, "2027")
        assert suivant is not None, "l'exercice suivant n'a pas été ouvert"
        assert suivant["clos"] is False
        assert suivant["ouverture"] == "2027-01-01"
        assert suivant["cloture"] == "2027-12-31"

        # ── 3 · Les soldes sont passés, et eux seuls ─────────────────────────
        apres = _balance(client, "2027")
        assert apres, "l'exercice suivant s'ouvre vide : l'à-nouveau n'a rien reporté"

        situation_avant = {
            s["compte"]: Decimal(s["total_debit"]) - Decimal(s["total_credit"])
            for s in avant
            if s["compte"][0] in "12345"
        }
        situation_apres = {
            s["compte"]: Decimal(s["total_debit"]) - Decimal(s["total_credit"])
            for s in apres
            if s["compte"] != COMPTE_RESULTAT_NET
        }
        # ⚠️ **Solde pour solde, au franc près.** Une assertion de présence
        # passerait même si tous les montants étaient faux.
        assert situation_apres == {
            c: v for c, v in situation_avant.items() if v != 0
        }, (situation_avant, situation_apres)

        # ⚠️ **Et aucun compte de gestion.** C'est la moitié de la règle, et celle
        # dont l'oubli double les charges de l'exercice suivant.
        assert not [s for s in apres if s["compte"][0] in "678"
                    and s["compte"] != COMPTE_RESULTAT_NET], apres

    def test_clore_deux_fois_est_refuse(self, plateforme):
        """⚠️ Un second à-nouveau doublerait tous les soldes de 2027."""
        client = plateforme
        ouvrir_une_session(client, REVISEUR)
        assert _clore(client, appliquer=True)["applique"] is True

        second = _clore(client, appliquer=True)
        assert second["applique"] is False
        assert second["possible"] is False
        assert MotifEmpechement.EXERCICE_DEJA_CLOS.value in [
            o["motif"] for o in second["obstacles"]
        ]

    def test_le_motif_est_exige_et_ne_peut_pas_etre_vide(self, plateforme):
        """⚠️ **Un motif court est un motif vide.**

        Le seuil n'est pas une contrainte de forme : au-dessous, on écrit « RAS »,
        et un journal d'audit rempli de « RAS » ne sert plus à rien le jour où un
        vérificateur demande pourquoi cet exercice a été arrêté ce jour-là.
        """
        client = plateforme
        ouvrir_une_session(client, REVISEUR)
        court = client.post(
            f"/cloture/dossiers/{DOSSIER}/exercices/2026/cloture?appliquer=false",
            json={"motif": "RAS"},
        )
        assert court.status_code == 422, court.text

        # ⚠️ Et rien d'autre que le motif n'est accepté : un résultat déclaré par
        # l'appelant serait exactement la faute que les trois surfaces ont corrigée
        # ailleurs.
        intrus = client.post(
            f"/cloture/dossiers/{DOSSIER}/exercices/2026/cloture?appliquer=false",
            json={"motif": MOTIF, "resultat_de_l_exercice": "999999999"},
        )
        assert intrus.status_code == 422, intrus.text

    def test_un_comptable_ne_clot_pas_un_exercice(self, plateforme):
        """`CLOTURER_EXERCICE` n'appartient qu'au réviseur et à la direction.

        Arrêter les comptes engage le centre au-delà de la saisie : c'est la
        signature que l'administration lira.
        """
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)
        reponse = client.post(
            f"/cloture/dossiers/{DOSSIER}/exercices/2026/cloture?appliquer=true",
            json={"motif": MOTIF},
        )
        assert reponse.status_code in (403, 404), reponse.text

    def test_un_exercice_inconnu_repond_404_et_nomme_ceux_qui_existent(
        self, plateforme
    ):
        client = plateforme
        ouvrir_une_session(client, REVISEUR)
        reponse = client.post(
            f"/cloture/dossiers/{DOSSIER}/exercices/2019/cloture?appliquer=false",
            json={"motif": MOTIF},
        )
        assert reponse.status_code == 404, reponse.text
        assert "2026" in reponse.text

    def test_un_brouillon_subsistant_arrete_la_cloture_en_base(self, plateforme):
        """⚠️ **L'obstacle mesuré là où il compte.**

        Le cas de domaine prouve que la fonction le détecte. Celui-ci prouve que
        la route le voit vraiment, sur une écriture réellement saisie.
        """
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)
        saisie = client.post(
            f"/comptabilite/dossiers/{DOSSIER}/ecritures",
            json={
                "journal": "OD",
                "exercice": "2026",
                "date_operation": "2026-11-02",
                "libelle": "Un brouillon laissé en plan",
                "piece_justificative": "PJ-BROUILLON",
                "lignes": [
                    {"compte": "602", "libelle": "x", "sens": "DEBIT", "montant": "1000"},
                    {"compte": "401", "libelle": "x", "sens": "CREDIT", "montant": "1000"},
                ],
            },
        )
        assert saisie.status_code == 201, saisie.text

        ouvrir_une_session(client, REVISEUR)
        rapport = _clore(client, appliquer=True)
        assert rapport["applique"] is False
        (obstacle,) = [
            o for o in rapport["obstacles"]
            if o["motif"] == MotifEmpechement.BROUILLON_SUBSISTANT.value
        ]
        assert obstacle["en_cause"], obstacle
        assert _exercice(client, "2026")["clos"] is False


class TestLOrdreDesExercices:
    """⚠️ **On ne clôt pas 2026 avant 2025.**

    Le cas n'existait pas, et une mutation l'a montré : supprimer ce contrôle ne
    cassait rien, parce que le jeu de démonstration ferme tous ses exercices
    antérieurs. Rien dans le produit n'oblige pourtant à clore dans l'ordre.

    Reporter les soldes de 2026 alors que 2025 n'a pas livré les siens produirait
    **un à-nouveau posé sur du vide** : 2027 recevrait des soldes amputés de tout
    ce que 2025 n'a pas encore transmis, et l'écart ne se verrait qu'au bilan.
    """

    def _cabinet(self, *, annee_anterieure_close: bool):
        """Une entreprise à deux exercices ouverts, montée à la main.

        ⚠️ Le jeu de démonstration ne peut pas servir ici : il ferme tous ses
        exercices antérieurs, ce qui est précisément la situation que ce cas doit
        écarter.
        """
        from app.contextes.portefeuille.api import (
            PORTEFEUILLE_DEMO,
            DepotEntreprisesMemoire,
            Exercice,
        )
        from app.partage.copie import transiter

        modele = PORTEFEUILLE_DEMO[DOSSIER]
        exercices = [
            e for e in modele.exercices if e.libelle not in {"2025", "2026"}
        ] + [
            Exercice(
                libelle="2025",
                ouverture=date(2025, 1, 1),
                cloture=date(2025, 12, 31),
                clos=annee_anterieure_close,
            ),
            Exercice(
                libelle="2026",
                ouverture=date(2026, 1, 1),
                cloture=date(2026, 12, 31),
                clos=False,
            ),
        ]
        entreprise = transiter(
            modele, exercices=sorted(exercices, key=lambda e: e.ouverture)
        )
        depot = DepotEntreprisesMemoire()
        depot.enregistrer(entreprise)
        return depot

    def _clore(self, entreprises, *, a_l_instant=datetime(2027, 1, 15, 9, 0), appliquer=False):

        from app.contextes.cloture.application.exercice_clos import clore_un_exercice
        from app.contextes.comptabilite.api import (
            JOURNAUX_CABINET,
            DepotEcrituresMemoire,
        )

        # ⚠️ Le dépôt mémoire est nommé par son dossier, puis garni : son
        # constructeur ne prend pas d'écritures. Le lui passer en liste rendait un
        # dépôt vide, et le rapport disait « rien à clore » — un faux négatif que
        # la contre-épreuve a attrapé.
        livres = DepotEcrituresMemoire(DOSSIER)
        for ecriture in EXERCICE_COMPLET:
            livres.enregistrer(ecriture)

        return clore_un_exercice(
            DOSSIER,
            "2026",
            motif=MOTIF,
            entreprises=entreprises,
            depot=livres,
            journaux=list(JOURNAUX_CABINET),
            plan=PLAN,
            par="a.bouba",
            a_l_instant=a_l_instant,
            appliquer=appliquer,
        )

    def test_un_exercice_anterieur_ouvert_arrete_la_cloture(self):
        rapport = self._clore(self._cabinet(annee_anterieure_close=False))
        assert rapport.possible is False
        (obstacle,) = [
            o for o in rapport.obstacles
            if o.motif is MotifEmpechement.EXERCICE_ANTERIEUR_OUVERT
        ]
        assert obstacle.en_cause == ("2025",), obstacle

    def test_la_contre_epreuve_quand_il_est_clos(self):
        """⚠️ Sans elle, un contrôle qui refuserait toujours passerait aussi."""
        rapport = self._clore(self._cabinet(annee_anterieure_close=True))
        assert rapport.possible is True, [o.motif for o in rapport.obstacles]
        assert MotifEmpechement.EXERCICE_ANTERIEUR_OUVERT not in [
            o.motif for o in rapport.obstacles
        ]

    def test_la_succession_est_chronologique_et_non_alphabetique(self):
        """⚠️ **Le libellé d'un exercice est une étiquette, pas un ordre.**

        `Exercice.libelle` est une chaîne libre : un dossier repris d'un ancien
        logiciel arrive avec « EX01 », « 0001 » ou « 2026-transition ». Chercher
        l'exercice suivant en comparant des libellés marcherait tant que ce sont
        des années, et se tromperait sans le dire le jour où ce n'en sont plus.

        Le libellé de ce cas est **délibérément artificiel**, et c'est ce qui le
        rend utile : il fixe le contrat, qui est que la succession se lit sur les
        dates d'ouverture. Aucun jeu de libellés réel du projet ne met les deux
        méthodes en désaccord aujourd'hui ; c'est une raison de l'écrire, pas une
        raison de s'en passer.
        """
        from app.contextes.portefeuille.api import (
            PORTEFEUILLE_DEMO,
            DepotEntreprisesMemoire,
            Exercice,
        )
        from app.partage.copie import transiter

        modele = PORTEFEUILLE_DEMO[DOSSIER]
        exercices = [
            e for e in modele.exercices if e.ouverture.year < 2026
        ] + [
            Exercice(libelle="2026", ouverture=date(2026, 1, 1), cloture=date(2026, 12, 31)),
            # ⚠️ Alphabétiquement **avant** « 2026 », chronologiquement après.
            Exercice(libelle="0001", ouverture=date(2027, 1, 1), cloture=date(2027, 12, 31)),
        ]
        depot = DepotEntreprisesMemoire()
        depot.enregistrer(transiter(modele, exercices=exercices))

        rapport = self._clore(depot)
        assert rapport.exercice_suivant == "0001", (
            f"l'exercice suivant est « {rapport.exercice_suivant} » : la succession "
            "se lit sur les libellés, alors qu'ils ne sont que des étiquettes."
        )
        assert rapport.suivant_ouvert_par_la_cloture is False

    def test_un_exercice_suivant_deja_clos_arrete_la_cloture(self):
        """Il n'y a nulle part où reporter, et rouvrir n'appartient pas à ce geste."""
        from app.contextes.portefeuille.api import (
            PORTEFEUILLE_DEMO,
            DepotEntreprisesMemoire,
            Exercice,
        )
        from app.partage.copie import transiter

        modele = PORTEFEUILLE_DEMO[DOSSIER]
        exercices = [
            e for e in modele.exercices if e.ouverture.year < 2026
        ] + [
            Exercice(libelle="2026", ouverture=date(2026, 1, 1), cloture=date(2026, 12, 31)),
            Exercice(
                libelle="2027",
                ouverture=date(2027, 1, 1),
                cloture=date(2027, 12, 31),
                clos=True,
            ),
        ]
        depot = DepotEntreprisesMemoire()
        depot.enregistrer(transiter(modele, exercices=exercices))

        rapport = self._clore(depot)
        assert rapport.possible is False
        (obstacle,) = [
            o for o in rapport.obstacles
            if o.motif is MotifEmpechement.EXERCICE_SUIVANT_ABSENT
        ]
        assert "déjà clos" in obstacle.explication
        assert obstacle.en_cause == ("2027",)


class TestUnExerciceNeSeClotQueTermine(TestLOrdreDesExercices):
    """⚠️ **Le 14 septembre 2026, on ne clôt pas 2026.** (pas 55)

    La clôture l'acceptait. Fermé, l'exercice ne recevait plus les opérations de
    septembre à décembre ; le suivant, ouvert au 1er janvier, refusait leurs dates ;
    et le produit ne sait pas rouvrir un exercice clos. Le dossier perdait trois mois
    et demi, sans recours.

    Hérite du montage de l'ordre des exercices, qui construit un 2026 ouvert après
    un 2025 clos. Les cas hérités se rejouent donc ici aussi, et c'est voulu : ils
    montrent que l'instant choisi par défaut n'a pas changé leur verdict.
    """

    def _motifs(self, instant, *, appliquer=False):
        rapport = self._clore(
            self._cabinet(annee_anterieure_close=True), a_l_instant=instant, appliquer=appliquer
        )
        return rapport, [o.motif for o in rapport.obstacles]

    @pytest.mark.parametrize(
        "instant",
        [datetime(2026, 9, 14, 10, 0), datetime(2026, 12, 31, 23, 59)],
        ids=["en_septembre", "le_dernier_jour_meme"],
    )
    def test_avant_le_lendemain_de_la_cloture_c_est_refuse(self, instant):
        """Le dernier jour aussi : ses opérations ne sont pas toutes passées."""
        rapport, motifs = self._motifs(instant)
        assert rapport.possible is False
        assert MotifEmpechement.EXERCICE_NON_TERMINE in motifs

    def test_le_lendemain_c_est_possible(self):
        """⚠️ La contre-épreuve, à la borne : sans elle, un contrôle qui refuserait
        toujours passerait aussi."""
        rapport, motifs = self._motifs(datetime(2027, 1, 1, 0, 0))
        assert MotifEmpechement.EXERCICE_NON_TERMINE not in motifs
        assert rapport.possible is True, motifs

    def test_appliquer_en_cours_d_exercice_n_ecrit_rien(self):
        entreprises = self._cabinet(annee_anterieure_close=True)
        rapport = self._clore(
            entreprises, a_l_instant=datetime(2026, 9, 14, 10, 0), appliquer=True
        )
        assert rapport.applique is False
        (courant,) = [e for e in entreprises.lire(DOSSIER).exercices if e.libelle == "2026"]
        assert courant.clos is False


class TestLaFinDeLExerciceSuivant:
    """Douze mois par défaut, autre chose sur demande, jamais en silence.

    ⚠️ L'**ouverture** du suivant n'est pas un choix : l'entité garantit que les
    exercices se suivent sans trou, donc c'est le lendemain. Seule sa **fin** se
    décide, et c'est rare : un exercice de transition de six mois, un passage à un
    exercice décalé.
    """

    def _depot(self, *, avec_2027: bool):
        from app.contextes.portefeuille.api import (
            PORTEFEUILLE_DEMO,
            DepotEntreprisesMemoire,
            Exercice,
        )
        from app.partage.copie import transiter

        modele = PORTEFEUILLE_DEMO[DOSSIER]
        exercices = [e for e in modele.exercices if e.ouverture.year < 2026] + [
            Exercice(libelle="2026", ouverture=date(2026, 1, 1), cloture=date(2026, 12, 31))
        ]
        if avec_2027:
            exercices.append(
                Exercice(
                    libelle="2027",
                    ouverture=date(2027, 1, 1),
                    cloture=date(2027, 12, 31),
                )
            )
        depot = DepotEntreprisesMemoire()
        depot.enregistrer(transiter(modele, exercices=exercices))
        return depot

    def _clore(self, depot, *, cloture_du_suivant=None, appliquer=False):
        from datetime import datetime

        from app.contextes.cloture.application.exercice_clos import clore_un_exercice
        from app.contextes.comptabilite.api import (
            JOURNAUX_CABINET,
            DepotEcrituresMemoire,
        )

        livres = DepotEcrituresMemoire(DOSSIER)
        for ecriture in EXERCICE_COMPLET:
            livres.enregistrer(ecriture)
        return clore_un_exercice(
            DOSSIER,
            "2026",
            motif=MOTIF,
            entreprises=depot,
            depot=livres,
            journaux=list(JOURNAUX_CABINET),
            plan=PLAN,
            par="a.bouba",
            a_l_instant=datetime(2027, 1, 15, 9, 0),
            appliquer=appliquer,
            cloture_du_suivant=cloture_du_suivant,
        )

    def test_par_defaut_douze_mois_moins_un_jour(self):
        depot = self._depot(avec_2027=False)
        rapport = self._clore(depot, appliquer=True)
        assert rapport.applique is True, rapport.obstacles
        (suivant,) = [
            e for e in depot.lire(DOSSIER).exercices if e.libelle == "2027"
        ]
        assert (suivant.ouverture, suivant.cloture) == (
            date(2027, 1, 1),
            date(2027, 12, 31),
        )

    def test_une_fin_demandee_est_suivie(self):
        """Un exercice de transition de six mois, avant un passage au décalé."""
        depot = self._depot(avec_2027=False)
        rapport = self._clore(
            depot, cloture_du_suivant=date(2027, 6, 30), appliquer=True
        )
        assert rapport.applique is True, rapport.obstacles
        (suivant,) = [
            e for e in depot.lire(DOSSIER).exercices if e.libelle == "2027"
        ]
        assert suivant.cloture == date(2027, 6, 30)

    def test_une_fin_demandee_sur_un_exercice_existant_est_refusee(self):
        """⚠️ **On n'ignore pas en silence une date que l'exploitant a donnée.**

        L'exercice suivant existe déjà avec ses bornes. Accepter la demande sans
        la suivre laisserait croire qu'elle a été prise en compte, et le réviseur
        découvrirait des mois plus tard que son exercice de transition dure douze
        mois. Modifier les bornes d'un exercice existant n'appartient pas à la
        clôture ; le refus nomme donc le geste, il ne le fait pas.
        """
        rapport = self._clore(
            self._depot(avec_2027=True), cloture_du_suivant=date(2027, 6, 30)
        )
        assert rapport.possible is False
        (obstacle,) = rapport.obstacles
        assert "existe déjà" in obstacle.explication
        assert "30/06/2027" in obstacle.explication

    def test_la_contre_epreuve_quand_la_date_demandee_coincide(self):
        """La même demande, conforme aux bornes existantes, ne gêne personne."""
        rapport = self._clore(
            self._depot(avec_2027=True), cloture_du_suivant=date(2027, 12, 31)
        )
        assert rapport.possible is True, [o.motif for o in rapport.obstacles]
