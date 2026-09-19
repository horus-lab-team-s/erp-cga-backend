"""Verser le jeu de démonstration dans une base vierge.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI ICI ET NON DANS `app/infrastructure/`

Parce qu'il connaît **tous** les contextes, et que le garde-fou d'architecture
l'a refusé là-bas : « le cercle externe est appelé par le métier, il ne l'appelle
pas ». Un amorçage est une affaire de **composition**, au même titre que
`app/main.py` — c'est le seul niveau qui a le droit de tout connaître.

CE N'EST PAS UN OUTIL DE PRODUCTION

Il pose des comptes dont le mot de passe est écrit en clair dans le dépôt. La
seule raison pour laquelle c'est acceptable est que ces comptes n'ont d'intérêt
que sur une base de démonstration.

⚠️ Il **refuse** de s'exécuter sur une base qui contient déjà des comptes. Un
amorçage rejoué écraserait des mots de passe réels par ceux de la démonstration,
et rien ne le signalerait avant qu'un collaborateur ne se retrouve dehors.

L'ORDRE N'EST PAS INDIFFÉRENT

Les comptes avant les habilitations, qui les référencent par clé étrangère. Les
dossiers avant les pièces, les pièces avant les écritures — non par contrainte de
base, mais parce que c'est l'ordre du métier, et qu'un amorçage qui suit l'ordre
du métier se relit.

CE QUI N'EST PAS AMORCÉ, ET POURQUOI

Le **journal d'audit** reste vierge. Fabriquer un historique produirait une
chaîne de hachage qui n'atteste de rien — c'est déjà la règle du jeu en mémoire,
et elle vaut plus encore en base, où l'on pourrait croire cet historique réel.

Les **catalogues** — plan SYSCOHADA, journaux, catalogue d'obligations, offre
commerciale — restent en code. Ce sont des données de configuration, identiques
pour tous les locataires ; leur donner une table ne servirait qu'à devoir les y
remettre à chaque nouvelle base.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from app.contextes.collecte.adaptateurs.sortant.depots_sql import (
    DepotDemandesSql,
    DepotPiecesSql,
)
from app.contextes.collecte.api import DEMANDES_DEMO, PIECES_DEMO
from app.contextes.comptabilite.adaptateurs.sortant.depots_sql import (
    DepotEcrituresSql,
    DepotPlanImputationSql,
)
from app.contextes.comptabilite.api import PLAN_IMPUTATION_DEMO, ecritures_demo
from app.contextes.creation_entreprise.api import (
    PIPELINE_DEMO,
    DepotDossiersCreationSql,
)
from app.contextes.portefeuille.adaptateurs.sortant.depot_entreprises_sql import (
    DepotEntreprisesSql,
)
from app.contextes.portefeuille.api import PORTEFEUILLE_DEMO
from app.contextes.social.adaptateurs.sortant.depots import (
    DepotContratsSql,
    DepotSalariesSql,
)
from app.contextes.social.api import CONTRATS_DEMO, RATTACHEMENTS_DEMO, SALARIES_DEMO
from app.contextes.transverse.api import (
    COMPTES_DEMO,
    HABILITATIONS_DEMO,
    LOCATAIRE_PAR_DEFAUT,
    unite_de_travail,
)

__all__ = ["AmorcageRefuse", "amorcer"]


class AmorcageRefuse(RuntimeError):
    """La base n'est pas vierge — voir l'en-tête."""


def amorcer(locataire: str = LOCATAIRE_PAR_DEFAUT, *, forcer: bool = False) -> dict[str, int]:
    """Verse le jeu de démonstration. Rend le compte de ce qui a été posé.

    `forcer` n'existe que pour les tests, qui repartent d'une base qu'ils ont
    eux-mêmes créée. Il n'a pas d'équivalent en ligne de commande, et c'est
    délibéré : la seule façon de réamorcer une base est de la recréer.
    """
    with unite_de_travail(locataire) as boutique:
        if not forcer and boutique.comptes.lister():
            raise AmorcageRefuse(
                "cette base contient déjà des comptes. Un amorçage rejoué écraserait "
                "des mots de passe réels par ceux de la démonstration, et personne ne "
                "s'en apercevrait avant qu'un collaborateur ne se retrouve dehors."
            )
        for compte in COMPTES_DEMO:
            boutique.comptes.enregistrer(compte)
        for habilitation in HABILITATIONS_DEMO:
            boutique.habilitations.enregistrer(habilitation)
        session = boutique.session

        dossiers = DepotEntreprisesSql(session, locataire)
        for entreprise in PORTEFEUILLE_DEMO.values():
            dossiers.enregistrer(entreprise)

        pieces = DepotPiecesSql(session, locataire)
        for piece in PIECES_DEMO.values():
            pieces.enregistrer(piece)
        # Pas 88 : les documents qu'elles annoncent, au magasin. Voir `semer_les_documents_demo`.
        from pathlib import Path

        from app.contextes.collecte.adaptateurs.sortant.donnees_demo import (
            semer_les_documents_demo,
        )
        from app.contextes.collecte.adaptateurs.sortant.magasin_local import MagasinFichiersLocal
        from app.infrastructure.config import configuration

        semer_les_documents_demo(
            MagasinFichiersLocal(Path(configuration().dossier_fichiers), locataire)
        )

        demandes = DepotDemandesSql(session, locataire)
        for demande in DEMANDES_DEMO.values():
            demandes.enregistrer(demande)

        # ⚠️ Le pipeline de création est versé **avant** les écritures pour une
        # raison de lecture, pas de contrainte : l'écran E13 est le premier que
        # le chargé de formalités ouvre, et une base amorcée qui lui montre des
        # colonnes vides se lit comme un produit inachevé.
        creations = DepotDossiersCreationSql(session, locataire)
        for dossier in PIPELINE_DEMO:
            creations.enregistrer(dossier)

        # ⚠️ Le personnel est versé **après** les dossiers : un contrat porte le
        # NIU de son employeur, et l'amorcer avant le portefeuille laisserait des
        # salariés rattachés à des dossiers qui n'existent pas encore. L'ordre
        # n'est pas une contrainte de clé étrangère — il n'y en a pas — mais une
        # discipline de lecture : une base amorcée doit être cohérente à chaque
        # étape, pas seulement à la fin.
        personnel = DepotSalariesSql(session, locataire)
        for salarie in SALARIES_DEMO:
            personnel.enregistrer(salarie)
        engagements = DepotContratsSql(session, locataire, RATTACHEMENTS_DEMO)
        for contrat in CONTRATS_DEMO:
            engagements.enregistrer(contrat)

        imputation = DepotPlanImputationSql(session, locataire, PLAN_IMPUTATION_DEMO)
        ecritures_posees = 0
        for niu in PORTEFEUILLE_DEMO:
            registre = DepotEcrituresSql(session, locataire, niu)
            for ecriture in ecritures_demo(niu):
                registre.enregistrer(ecriture)
                ecritures_posees += 1
            # Tous les dossiers de démonstration suivent le plan général : aucun
            # n'a de règles propres. Les poser quand même documente qu'ils
            # peuvent en avoir.
            imputation.enregistrer_plan(niu, PLAN_IMPUTATION_DEMO)

        return {
            "creations": len(PIPELINE_DEMO),
            "salaries": len(SALARIES_DEMO),
            "contrats": len(CONTRATS_DEMO),
            "comptes": len(COMPTES_DEMO),
            "habilitations": len(HABILITATIONS_DEMO),
            "dossiers": len(PORTEFEUILLE_DEMO),
            "pieces": len(PIECES_DEMO),
            "demandes": len(DEMANDES_DEMO),
            "ecritures": ecritures_posees,
        }
