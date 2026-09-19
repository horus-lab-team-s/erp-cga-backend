"""Le chiffrement au repos du secret TOTP.

─────────────────────────────────────────────────────────────────────────────────
CE QUI EST VRAIMENT EN JEU

Un secret TOTP doit être **relu** à chaque connexion pour recalculer le code
attendu — le hacher, comme un mot de passe, rendrait le second facteur
inopérant. Il reste donc réversible, et donc exposé par toute fuite de base :
sauvegarde égarée, accès en lecture d'un prestataire, injection SQL.

Qui détient ces secrets génère des codes valides **indéfiniment**, et rien ne le
signale : aucun utilisateur ne voit de différence. Le second facteur devient un
théâtre.

Le test qui compte ici est `test_un_secret_survit_a_un_aller_retour_en_base` :
il ne vérifie pas une fonction de chiffrement — ce serait vérifier la
bibliothèque — mais que le **couplage** au dépôt SQL est correct dans les deux
sens. C'est là que ce genre de branchement se casse : un scellement sans
ouverture correspondante ne se voit qu'à la connexion suivante d'un utilisateur
réel.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import base64
import os
from datetime import datetime

import pytest

from app.contextes.transverse.adaptateurs.sortant.coffre import (
    PREFIXE_V1,
    CoffreAesGcm,
    CoffreTransparent,
    coffre_depuis,
)
from app.contextes.transverse.domaine.identites import Compte, EtatCompte
from app.contextes.transverse.domaine.second_facteur import engendrer_secret_totp

# ⚠️ Sans ce marqueur, les tests qui emploient `session_sql` **échouent** au lieu
# de se sauter quand PostgreSQL est absent. Deux ERROR au bilan de `pytest` se
# lisent comme une chaîne cassée, alors que rien n'est cassé : c'est la base de
# test qui n'est pas montée. Un vert franc ou un rouge franc, jamais d'ambiguïté.
from tests.conftest import exige_postgresql

CLE = base64.b64encode(bytes(range(32))).decode("ascii")
SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"


class TestCoffreAesGcm:
    def test_un_secret_scelle_puis_ouvert_est_identique(self):
        coffre = CoffreAesGcm(base64.b64decode(CLE))
        assert coffre.ouvrir(coffre.sceller(SECRET)) == SECRET

    def test_le_scelle_ne_contient_pas_le_secret(self):
        """La vérification élémentaire, et il faut l'écrire."""
        coffre = CoffreAesGcm(base64.b64decode(CLE))
        scelle = coffre.sceller(SECRET)
        assert SECRET not in scelle
        assert scelle.startswith(PREFIXE_V1)

    def test_deux_scellements_du_meme_secret_different(self):
        """Le nonce doit être neuf à chaque fois.

        Deux scellés identiques trahiraient que deux comptes partagent le même
        secret — et surtout, réemployer un nonce avec la même clé casse GCM
        complètement : ce n'est pas une dégradation, c'est une perte totale de
        confidentialité et d'authenticité.
        """
        coffre = CoffreAesGcm(base64.b64decode(CLE))
        assert coffre.sceller(SECRET) != coffre.sceller(SECRET)

    def test_none_traverse_intact(self):
        """La plupart des comptes n'ont pas de second facteur."""
        coffre = CoffreAesGcm(base64.b64decode(CLE))
        assert coffre.sceller(None) is None
        assert coffre.ouvrir(None) is None

    def test_une_valeur_alteree_est_refusee(self):
        """GCM authentifie — c'est pourquoi ce n'est pas du CBC.

        Sans authentification, une valeur altérée produirait des octets
        quelconques qu'on prendrait pour un secret. Le second facteur
        refuserait alors tout le monde, sans que la cause soit visible nulle
        part.
        """
        coffre = CoffreAesGcm(base64.b64decode(CLE))
        scelle = coffre.sceller(SECRET)
        altere = scelle[:-6] + ("AAAAAA" if not scelle.endswith("AAAAAA") else "BBBBBB")
        with pytest.raises(ValueError, match="indéchiffrable"):
            coffre.ouvrir(altere)

    def test_une_mauvaise_cle_est_refusee_et_ne_rend_pas_none(self):
        """La tentation serait de rendre `None` « pour ne pas casser la
        connexion ». Ce serait désactiver le second facteur d'un compte parce
        que la clé a changé — exactement la protection qu'on croyait avoir."""
        scelle = CoffreAesGcm(base64.b64decode(CLE)).sceller(SECRET)
        autre = CoffreAesGcm(os.urandom(32))
        with pytest.raises(ValueError, match="indéchiffrable"):
            autre.ouvrir(scelle)

    def test_une_valeur_sans_prefixe_est_rendue_telle_quelle(self):
        """Migration d'une base existante, sans script.

        ⚠️ Migration **partielle** et assumée : un compte dont le second facteur
        n'est jamais retouché garde son secret en clair. Voir la migration
        `0b1987f66324`.
        """
        coffre = CoffreAesGcm(base64.b64decode(CLE))
        assert coffre.ouvrir(SECRET) == SECRET

    def test_une_cle_de_mauvaise_taille_est_refusee_a_la_construction(self):
        """Et le message doit dire comment en produire une bonne.

        Celui qui déploie ne doit pas avoir à lire le code pour s'en sortir.
        """
        with pytest.raises(ValueError, match="octets"):
            CoffreAesGcm(os.urandom(16))

    def test_le_scelle_tient_dans_la_colonne(self):
        """La colonne fait 255. Un dépassement se verrait à l'écriture, en
        production, sur le premier compte à activer son second facteur."""
        coffre = CoffreAesGcm(base64.b64decode(CLE))
        assert len(coffre.sceller(engendrer_secret_totp())) <= 255


class TestChoixDuCoffre:
    def test_sans_cle_le_coffre_est_transparent(self):
        """Le mode de développement. La production ne peut pas l'atteindre :
        `Configuration` refuse de démarrer sans clé."""
        assert isinstance(coffre_depuis(""), CoffreTransparent)
        assert coffre_depuis("").sceller(SECRET) == SECRET

    def test_avec_une_cle_le_coffre_chiffre(self):
        assert isinstance(coffre_depuis(CLE), CoffreAesGcm)

    def test_une_cle_mal_formee_est_refusee_avec_la_marche_a_suivre(self):
        with pytest.raises(ValueError, match="base64"):
            coffre_depuis("ceci n'est pas du base64 !!")


# ── Le couplage au dépôt : le test qui vaut tous les autres ─────────────────

pytest.importorskip("sqlalchemy")


@exige_postgresql
class TestCouplageAuDepot:
    def test_un_secret_survit_a_un_aller_retour_en_base(self, session_sql):
        """Écrit chiffré, relu en clair — et le chiffré est bien en base.

        C'est ici que ce genre de branchement se casse : un scellement sans
        ouverture correspondante ne se voit qu'à la connexion suivante d'un
        utilisateur réel, des semaines plus tard.
        """
        from sqlalchemy import select

        from app.contextes.transverse.adaptateurs.sortant.depots_sql import (
            DepotComptesSql,
        )
        from app.contextes.transverse.adaptateurs.sortant.tables import TableCompte

        depot = DepotComptesSql(
            session_sql, "CGA-BRCG", CoffreAesGcm(base64.b64decode(CLE))
        )
        depot.enregistrer(
            Compte(
                identifiant="C-coffre",
                courriel="coffre@cga-brcg.cm",
                nom="Essai",
                prenom="Coffre",
                locataire="CGA-BRCG",
                etat=EtatCompte.ACTIF,
                cree_le=datetime(2026, 8, 15, 10, 0),
                secret_totp=SECRET,
            )
        )

        # Relu par le dépôt : le secret est utilisable.
        assert depot.lire("C-coffre").secret_totp == SECRET

        # Lu directement en base : il ne l'est pas.
        brut = session_sql.scalars(
            select(TableCompte).where(TableCompte.identifiant == "C-coffre")
        ).one()
        assert brut.secret_totp.startswith(PREFIXE_V1)
        assert SECRET not in brut.secret_totp

    def test_un_depot_sans_coffre_ecrit_en_clair(self, session_sql):
        """Le comportement de développement, explicite plutôt que supposé."""
        from sqlalchemy import select

        from app.contextes.transverse.adaptateurs.sortant.depots_sql import (
            DepotComptesSql,
        )
        from app.contextes.transverse.adaptateurs.sortant.tables import TableCompte

        depot = DepotComptesSql(session_sql, "CGA-BRCG")
        depot.enregistrer(
            Compte(
                identifiant="C-clair",
                courriel="clair@cga-brcg.cm",
                nom="Essai",
                prenom="Clair",
                locataire="CGA-BRCG",
                etat=EtatCompte.ACTIF,
                cree_le=datetime(2026, 8, 15, 10, 0),
                secret_totp=SECRET,
            )
        )
        brut = session_sql.scalars(
            select(TableCompte).where(TableCompte.identifiant == "C-clair")
        ).one()
        assert brut.secret_totp == SECRET
