"""Point d'entrée du backend.

Monolithe modulaire : un package par contexte borné, des frontières nettes, aucun
microservice. Voir Docs/architecture/01-contextes-bornes.md.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.contextes.cloture.adaptateurs.entrant.routes_http import (
    routeur as routeur_cloture,
)
from app.contextes.collecte.adaptateurs.entrant.routes_http import (
    routeur as routeur_collecte,
)
from app.contextes.comptabilite.adaptateurs.entrant.routes_cloture_mensuelle import (
    routeur as routeur_cloture_mensuelle,
)
from app.contextes.comptabilite.adaptateurs.entrant.routes_fiche_ecriture import (
    routeur as routeur_fiche_ecriture,
)
from app.contextes.comptabilite.adaptateurs.entrant.routes_http import (
    routeur as routeur_comptabilite,
)
from app.contextes.comptabilite.adaptateurs.entrant.routes_lettrage import (
    routeur as routeur_lettrage,
)
from app.contextes.comptabilite.adaptateurs.entrant.routes_rapprochement import (
    routeur as routeur_rapprochement,
)
from app.contextes.comptabilite.adaptateurs.entrant.routes_revue import (
    routeur as routeur_revue,
)
from app.contextes.conformite.adaptateurs.entrant.routes_http import (
    routeur as routeur_conformite,
)
from app.contextes.creation_entreprise.adaptateurs.entrant.routes_http import (
    routeur as routeur_creations,
)
from app.contextes.obligations.adaptateurs.entrant.routes_espace_adherent import (
    routeur as routeur_echeances_de_l_adherent,
)
from app.contextes.obligations.adaptateurs.entrant.routes_http import (
    routeur as routeur_obligations,
)
from app.contextes.obligations.adaptateurs.entrant.routes_rappels import (
    routeur as routeur_mes_rappels,
)
from app.contextes.pilotage.adaptateurs.entrant.routes_charge import (
    routeur as routeur_charge,
)
from app.contextes.pilotage.adaptateurs.entrant.routes_espace_adherent import (
    routeur as routeur_accueil_de_l_adherent,
)
from app.contextes.pilotage.adaptateurs.entrant.routes_file_d_anomalies import (
    routeur as routeur_file_d_anomalies,
)
from app.contextes.pilotage.adaptateurs.entrant.routes_http import (
    routeur as routeur_pilotage,
)
from app.contextes.pilotage.adaptateurs.entrant.routes_plan_de_travail import (
    routeur as routeur_plan_de_travail,
)
from app.contextes.pilotage.adaptateurs.entrant.routes_rapport import (
    routeur as routeur_rapport,
)
from app.contextes.pilotage.adaptateurs.entrant.routes_relance import (
    routeur as routeur_relance_des_pieces,
)
from app.contextes.portefeuille.adaptateurs.entrant.routes_acces_adherent import (
    routeur as routeur_acces_adherents,
)
from app.contextes.portefeuille.adaptateurs.entrant.routes_espace_adherent import (
    routeur as routeur_mon_entreprise,
)
from app.contextes.portefeuille.adaptateurs.entrant.routes_http import (
    routeur as routeur_portefeuille,
)
from app.contextes.referentiel.adaptateurs.entrant.routes_http import (
    routeur as routeur_referentiel,
)
from app.contextes.social.adaptateurs.entrant.routes_http import (
    routeur as routeur_social,
)
from app.contextes.souscription.adaptateurs.entrant.routes_acquisition import (
    routeur as routeur_acquisition,
)
from app.contextes.souscription.adaptateurs.entrant.routes_http import (
    routeur as routeur_souscription,
)
from app.contextes.transverse.adaptateurs.entrant.limitation import (
    IntergicielLimitationDebit,
)
from app.contextes.transverse.adaptateurs.entrant.routes_http import (
    routeur as routeur_transverse,
)
from app.contextes.transverse.adaptateurs.entrant.routes_orchestration import (
    routeur as routeur_orchestration,
)
from app.contextes.transverse.adaptateurs.entrant.routes_referentiel import (
    routeur as routeur_decisions_referentiel,
)
from app.contextes.transverse.adaptateurs.entrant.routes_registre import (
    routeur as routeur_registre,
)
from app.contextes.transverse.api import IntergicielUniteDeTravail, garnir_le_repertoire
from app.contextes.vitrine.adaptateurs.entrant.routes_http import (
    routeur as routeur_vitrine,
)
from app.infrastructure.base_de_donnees import moteur
from app.infrastructure.config import configuration
from app.infrastructure.roles import DiagnosticDuRole, diagnostiquer

_journal = logging.getLogger("cga.sante")

DESCRIPTION = """
Plateforme de suivi fiscal et comptable du Centre de Gestion Agréé
**Broad Range Consulting Group** — agrément MINFI/DGI n° 00000048.

⚠️ **Aucune valeur légale de ce système n'a été validée sur le Code Général des Impôts.**
Les paramètres proviennent de sources secondaires et portent le statut `A_VALIDER`.
Consulter `/referentiel/validation` pour l'état exact. Aucun chiffre produit n'est
opposable tant qu'un fiscaliste nommé n'a pas confirmé chaque valeur sur le texte.
"""


@asynccontextmanager
async def _cycle_de_vie(application: FastAPI) -> AsyncIterator[None]:
    """Ce qui se prépare avant la première requête, et se range après la dernière.

    Deux choses. **Garnir le répertoire des tenants** depuis la base. Il doit
    l'être **avant** la première requête, puisque la passerelle le consulte pour résoudre
    chaque nom d'hôte — un répertoire garni à la première requête ferait échouer celle-ci.

    ⚠️ Le garnissage n'échoue jamais le démarrage. Voir `garnir_le_repertoire` : un
    répertoire vide rend 404 sur les sous-domaines, ce qui est franc, là où un refus de
    démarrer priverait aussi la vitrine et la sonde de santé — qui n'ont besoin d'aucun
    tenant.

    Puis **constater si le cloisonnement s'applique vraiment**. Une fois, ici, et non à
    chaque appel de la sonde : la réponse ne change que par un geste d'exploitation
    (`ALTER ROLE`, une migration jouée), jamais entre deux requêtes. ⚠️ La contrepartie
    est nommée : une table ajoutée sans politique pendant que le processus tourne ne se
    verra qu'au redémarrage suivant. C'est acceptable parce qu'une migration se joue
    avec un redémarrage ; ce ne le serait plus le jour où le schéma changerait à chaud.
    """
    garnir_le_repertoire()
    application.state.cloisonnement = _constater_le_cloisonnement()
    arreter = await _demarrer_l_ordonnanceur()
    yield
    if arreter is not None:
        await arreter()


async def _demarrer_l_ordonnanceur() -> Callable[[], Awaitable[None]] | None:
    """Démarre la boucle de fond si la configuration la demande. Rend son arrêteur.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **Elle ne démarre pas par défaut**, et ce n'est pas de la prudence.

    Les deux façons de faire tourner les travaux sont légitimes : une boucle dans
    le processus, ou un ordonnanceur extérieur qui appelle
    `POST /orchestration/ordonnancement`. Une installation qui a choisi la seconde
    verrait ici une seconde source d'appels dont elle ignore l'existence, et
    passerait un temps déraisonnable à comprendre pourquoi ses compteurs doublent.

    Les deux ensemble restent **sûrs** — le verrou consultatif arbitre — mais sûr
    n'est pas la même chose que voulu.

    LE CHOIX D'IMPORTER ICI PLUTÔT QU'EN TÊTE DE MODULE

    L'import est local parce qu'il ne sert que dans ce cas, et surtout parce que
    `routes_orchestration` importe déjà beaucoup : le remonter ferait dépendre le
    démarrage de tout ce qu'il traîne, y compris quand l'ordonnanceur est éteint.
    ─────────────────────────────────────────────────────────────────────────────
    """
    config = configuration()
    if not config.ordonnanceur_en_processus:
        return None
    if not config.en_base:
        # Sans base durable, le compte à rebours de chaque travail repart à zéro au
        # redémarrage, et il n'y a rien à publier de toute façon : la boîte d'envoi
        # en mémoire est vidée par le même processus qui la remplit.
        _journal.info("ordonnanceur en processus : ignoré, la persistance est en mémoire")
        return None

    from app.contextes.transverse.adaptateurs.entrant.routes_orchestration import (
        tour_hors_requete,
    )
    from app.infrastructure.boucle_de_fond import demarrer

    _, arreter = await demarrer(
        tour_hors_requete,
        lambda: float(configuration().cadence_relais_secondes),
    )
    return arreter


def _constater_le_cloisonnement() -> DiagnosticDuRole | None:
    """Demande à la base si les politiques s'appliquent au rôle courant.

    ─────────────────────────────────────────────────────────────────────────────
    POURQUOI CE CONSTAT NE PEUT PAS RESTER UNE LIGNE DE README

    Sans deux rôles PostgreSQL distincts, l'application se connecte avec le
    propriétaire des tables et **contourne toutes les politiques de cloisonnement**.
    Les données de deux cabinets ne sont alors séparées que par le filtre applicatif,
    que le moindre SQL textuel contourne.

    ⚠️ **Rien ne le signale.** Le service répond, la recette passe, la suite de tests
    est verte — elle s'exécute avec le même rôle privilégié, donc elle vérifie un
    cloisonnement qui ne s'applique pas à elle non plus. C'est resté vrai depuis le
    chantier 2 : le présent constat est ce qui rend le défaut visible.

    CE QU'IL FAIT DE SA RÉPONSE : DEUX CAS, ET LA LIGNE QUI LES SÉPARE

    **En production, un contournement constaté refuse le démarrage.** C'est la seule
    exception au principe posé par le garnissage du répertoire (« on démarre quand
    même, la panne se voit au journal »). La raison tient en une phrase : un répertoire
    vide rend 404, ce qui est désagréable mais franc, tandis qu'un cloisonnement
    contourné **sert les données de deux cabinets sans séparation** et ne se voit pas.
    Une panne franche vaut mieux qu'un service qui marche en mélangeant.

    **Une base injoignable ne refuse rien.** La distinction est entre « j'ai demandé et
    la réponse est mauvaise » et « je n'ai pas pu demander ». Confondre les deux
    empêcherait un redémarrage pendant une coupure réseau, c'est-à-dire au pire moment.

    ⚠️ Hors production, aucun refus. La suite de tests s'exécute avec un rôle
    superutilisateur — elle contourne donc les politiques, et c'est assumé : elle a
    besoin d'écrire pour deux locataires afin de vérifier qu'ils ne se voient pas.
    C'est `test_isolation.py`, avec son second rôle non propriétaire, qui vérifie le
    cloisonnement pour de bon.
    ─────────────────────────────────────────────────────────────────────────────
    """
    if not configuration().en_base:
        return None
    try:
        with moteur().connect() as connexion:
            diagnostic = diagnostiquer(connexion)
    except Exception as panne:  # noqa: BLE001 — voir l'en-tête : on démarre quand même
        _journal.error("cloisonnement non constaté : base injoignable (%s)", type(panne).__name__)
        return None
    if diagnostic.applique:
        _journal.info("cloisonnement : %s", diagnostic.explication)
        return diagnostic
    # ⚠️ `error` et non `warning`. Une alerte se range dans le bruit de démarrage ;
    # ceci est une fuite de données entre cabinets qui attend son heure.
    _journal.error("CLOISONNEMENT NON APPLIQUÉ : %s", diagnostic.explication)
    if configuration().en_production:
        raise RuntimeError(
            "Démarrage refusé : le cloisonnement des données ne s'applique pas. "
            + diagnostic.explication
        )
    return diagnostic


def _inscrire_les_sondes() -> None:
    """Chaque service déclare la sienne ; la composition les rassemble.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **Troisième fois que ce sens s'inverse**, après les travaux périodiques et les
    abonnés aux événements, et pour le même motif : le registre vit dans
    `app/registre/`, que le test d'architecture empêche d'importer aucun contexte
    métier.

    La contrainte n'est pas formelle. Un registre qui importerait chaque service pour
    le sonder deviendrait le point par lequel tout se charge, et l'on veut
    précisément pouvoir consulter l'état de la plateforme **quand elle va mal**.

ONZE SERVICES SUR QUATORZE, ET LES TROIS AUTRES LE DISENT

    ⚠️ **Cette phrase disait huit, et elle avait vieilli.** Elle affirmait que la
    Collecte, le Pilotage et les Tenants « n'ont aucune configuration propre dont
    l'absence les empêcherait de travailler ». C'était vrai quand elle a été écrite ;
    ces trois services ont pris depuis un magasin de fichiers, sept réglages et une
    liste de noms réservés, sans que personne ne revienne ici. Un commentaire qui
    explique une absence doit être relu le jour où l'absence cesse, et rien ne le
    rappelle : c'est le pas 121 qui l'a constaté, en interrogeant le registre.

    Restent trois services sans sonde, et pour une bonne raison :

    * la **Clôture** et la **Création d'entreprise** n'ont aucune ressource à elles.
      Elles lisent la Comptabilité, le Portefeuille et la base. Leur inventer une sonde
      qui relirait la ressource d'un autre créerait deux vérités sur une même question ;
      le graphe des dépendances dit déjà ce qui tombe avec quoi ;
    * le **Social** lit les barèmes, mais ils appartiennent au Référentiel : c'est son
      dépôt que le Social emprunte par l'`api`. La sonde du Référentiel les couvre depuis
      le pas 121, et une seconde sonde sur le même fichier finirait par diverger.

    ⚠️ Le **répertoire** des Tenants reste vérifié par le Transverse : il appartient à la
    passerelle, et le test d'architecture a refusé qu'il en soit autrement. La sonde du
    contexte N surveille autre chose : les noms que la plateforme se garde. Voir
    `sonde_du_transverse` et `sonde_des_tenants`.

    `SANS_SONDE` existe pour cela, et le registre le distingue de `REPOND`.
    ─────────────────────────────────────────────────────────────────────────────
    """
    from app.contextes.collecte.adaptateurs.entrant.sonde import sonde_de_la_collecte
    from app.contextes.comptabilite.adaptateurs.entrant.sonde import (
        sonde_de_la_comptabilite,
    )
    from app.contextes.conformite.adaptateurs.entrant.sonde import (
        sonde_de_la_conformite,
    )
    from app.contextes.obligations.adaptateurs.entrant.sonde import (
        sonde_des_obligations,
    )
    from app.contextes.pilotage.adaptateurs.entrant.sonde import sonde_du_pilotage
    from app.contextes.portefeuille.adaptateurs.entrant.sonde import (
        sonde_du_portefeuille,
    )
    from app.contextes.referentiel.adaptateurs.entrant.sonde import (
        sonde_du_referentiel,
    )
    from app.contextes.souscription.adaptateurs.entrant.sonde import (
        sonde_de_la_souscription,
    )
    from app.contextes.tenants.adaptateurs.entrant.sonde import sonde_des_tenants
    from app.contextes.transverse.adaptateurs.entrant.sonde import (
        sonde_du_transverse,
    )
    from app.contextes.vitrine.adaptateurs.entrant.sonde import sonde_de_la_vitrine
    from app.registre import inscrire_une_sonde

    inscrire_une_sonde("referentiel", sonde_du_referentiel)
    inscrire_une_sonde("portefeuille", sonde_du_portefeuille)
    inscrire_une_sonde("conformite", sonde_de_la_conformite)
    inscrire_une_sonde("comptabilite", sonde_de_la_comptabilite)
    inscrire_une_sonde("obligations", sonde_des_obligations)
    inscrire_une_sonde("transverse", sonde_du_transverse)
    inscrire_une_sonde("vitrine", sonde_de_la_vitrine)
    inscrire_une_sonde("souscription", sonde_de_la_souscription)
    inscrire_une_sonde("collecte", sonde_de_la_collecte)
    inscrire_une_sonde("pilotage", sonde_du_pilotage)
    inscrire_une_sonde("tenants", sonde_des_tenants)


def _inscrire_les_travaux_periodiques() -> None:
    """Assemble les travaux de l'ordonnanceur, service par service.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **C'EST LE SEUL ENDROIT QUI A LE DROIT DE CONNAÎTRE TOUS LES SERVICES.**

    L'ordonnanceur vit dans le Transverse, et le graphe n'autorise au Transverse que
    le Référentiel et les Tenants. Un ordonnanceur qui importerait la Souscription
    pour faire tourner son balayage serait refusé par le test d'architecture — et il
    l'a été.

    Il avait raison, et pas pour une raison de forme : un ordonnanceur qui importe
    chaque service dont il fait tourner un travail devient un point de couplage
    central. Il faut le modifier pour ajouter un travail, il traîne au démarrage tout
    ce que ces services traînent, et le jour où l'un part vivre ailleurs il faut le
    découdre.

    La composition, elle, assemble : c'est son métier, et elle n'est le domaine de
    personne.

    L'ORDRE D'INSCRIPTION EST L'ORDRE D'EXÉCUTION

    Le relais d'abord, parce qu'il est le chemin vital : c'est lui qui ouvre les
    tenants payés, et un client qui règle attend que son sous-domaine réponde. Le
    faire passer derrière un balayage de centaines de proformas ajouterait la durée
    du parcours au délai d'ouverture.
    ─────────────────────────────────────────────────────────────────────────────
    """
    from app.contextes.souscription.adaptateurs.entrant.abonne_de_relance import (
        abonne_de_relance,
    )
    from app.contextes.souscription.adaptateurs.entrant.travail_de_reconciliation import (
        reconcilier_les_paiements,
        travail_de_reconciliation,
    )
    from app.contextes.souscription.adaptateurs.entrant.travail_de_relance import (
        balayer,
        travail_de_relance,
    )
    from app.contextes.souscription.adaptateurs.entrant.travail_de_reprise import (
        reprendre,
        travail_de_reprise,
    )
    from app.contextes.souscription.adaptateurs.entrant.travail_de_veille import (
        travail_de_veille,
        veiller,
    )
    from app.contextes.souscription.application.balayage_de_relance import NOM_RELANCE
    from app.contextes.transverse.adaptateurs.entrant.routes_orchestration import (
        Atelier,
        executant_du_relais,
        travail_du_relais,
    )
    from app.orchestration.inscription import inscrire_un_abonne, inscrire_un_travail

    inscrire_un_travail(travail_du_relais(), executant_du_relais)
    # ⚠️ La boîte est construite **au moment du tour**, pas ici : elle porte la
    # session de la requête ou du tour, et la mémoriser au démarrage conserverait
    # une session fermée. C'est pourquoi l'exécutant est une fermeture et non un
    # appel direct.
    # ⚠️ **La réconciliation juste après le relais**, et avant tout le reste.
    #
    # C'est le seul travail qui touche à l'argent déjà encaissé : un règlement dont
    # la notification s'est perdue est un client qui a payé et dont l'espace ne
    # s'ouvre pas. Il passe donc avant la relance, qui pourrait sinon réclamer son
    # règlement à quelqu'un qui vient de payer.
    #
    # ⚠️ **Et c'est le seul qui appelle le réseau.** Un prestataire lent retarde le
    # tour entier ; l'ordonnanceur compte les échecs, recule, et abandonne au bout
    # de vingt tentatives.
    inscrire_un_travail(
        travail_de_reconciliation(), lambda: reconcilier_les_paiements(Atelier().boite)
    )

    inscrire_un_travail(travail_de_relance(), lambda: balayer(Atelier().boite))

    # ⚠️ **Et l'abonné qui referme la boucle.** Le balayage dépose un `RelanceDue`,
    # le relais le publie, celui-ci le poste. Sans cette ligne, les trois pièces
    # existent et l'événement part en « publié sans abonné » : compté, jamais remis,
    # et aucun client relancé.
    inscrire_un_abonne(NOM_RELANCE, abonne_de_relance)

    # ⚠️ **La veille des dossiers, et volontairement sans abonné.** Elle dépose des
    # `DossierEnSouffrance` que le relais publiera, et aucun code ne s'y abonne
    # aujourd'hui : l'alerte reste dans la boîte, en « publié sans abonné », et se
    # lit par `GET /acquisition/dossiers/en-souffrance`, qui calcule l'état réel
    # sans dépendre d'elle.
    #
    # C'est assumé plutôt que subi. Brancher tout de suite un abonné supposerait de
    # décider *à qui* l'alerte est remise, par quel canal, et à quelle heure — trois
    # choix qui appartiennent au cabinet et qu'aucun code ne peut prendre à sa
    # place. Le fait est enregistré, durablement et daté, en attendant qui le lira.
    # ⚠️ **La reprise avant la veille, et l'ordre porte un sens.**
    #
    # La reprise remet l'ancienneté du dossier à zéro. Inscrite après, elle
    # agirait sur des dossiers que la veille vient de signaler dans le même tour,
    # et l'alerte partirait pour un dossier déjà repris : un responsable de pôle
    # recevrait une alerte sur un dossier qui n'a plus de problème.
    #
    # Dans cet ordre, un dossier n'atteint les 48 heures de la veille que si la
    # reprise **n'a pas pu aboutir**. L'alerte cesse de dire « personne n'a
    # rappelé » et dit « la machine a essayé de passer la main et n'a pas pu ».
    inscrire_un_travail(travail_de_reprise(), lambda: reprendre(Atelier().boite))
    inscrire_un_travail(travail_de_veille(), lambda: veiller(Atelier().boite))

    # Pas 115 : les rappels d'échéance que les adhérents ont réglés. Après les travaux de la
    # souscription : un rappel à J-7 n'est pas à la minute, et il ne doit pas retarder l'ouverture
    # d'un tenant payé.
    from app.contextes.obligations.adaptateurs.entrant.travail_des_rappels import (
        envoyer_les_rappels,
        travail_des_rappels,
    )

    inscrire_un_travail(travail_des_rappels(), envoyer_les_rappels)


def creer_application() -> FastAPI:
    config = configuration()
    application = FastAPI(
        title=config.nom_application,
        description=DESCRIPTION,
        version="0.1.0",
        lifespan=_cycle_de_vie,
    )
    # L'unité de travail avant CORS : les intergiciels s'appliquent dans l'ordre
    # inverse de leur déclaration, et l'on veut que la transaction enveloppe le
    # traitement, pas l'inverse. Une transaction ouverte à l'extérieur de la
    # gestion d'erreur resterait ouverte sur une exception non rattrapée.
    _inscrire_les_sondes()
    _inscrire_les_travaux_periodiques()
    application.add_middleware(IntergicielUniteDeTravail)
    # La limitation de débit déclarée **après**, donc exécutée **avant** — même
    # règle d'inversion. C'est ce qu'on veut : une requête refusée ne doit ni
    # ouvrir de transaction, ni atteindre la vérification Argon2 dont elle
    # cherche justement à saturer le processeur. Voir `limitation.py`.
    application.add_middleware(IntergicielLimitationDebit)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.origines_cors,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Le socle d'abord — il ne dépend d'aucun métier et tous en dépendent :
    # le transverse dit qui parle et ce qu'il a le droit de faire, le référentiel
    # dit ce que la loi prévoit.
    application.include_router(routeur_transverse)
    # Le registre des services, sous le même préfixe : c'est une surface
    # d'administration de la plateforme, au même titre que le journal d'audit.
    application.include_router(routeur_registre)
    # Pas 95 : les décisions d'un cabinet sur le référentiel. Au transverse, qui sait qui
    # parle ; voir l'en-tête de `routes_referentiel.py`.
    application.include_router(routeur_decisions_referentiel)
    # L'ordre suit ensuite la chaîne de valeur : le portefeuille dit qui est qui,
    # la collecte reçoit, la conformité contrôle, la comptabilité enregistre, les
    # obligations déclarent.
    application.include_router(routeur_referentiel)
    application.include_router(routeur_portefeuille)
    application.include_router(routeur_collecte)
    application.include_router(routeur_conformite)
    application.include_router(routeur_comptabilite)
    # Pas 101 : le rapprochement bancaire, même préfixe, module à part.
    application.include_router(routeur_rapprochement)
    # Pas 102 : la revue d'un mois transmis, même préfixe, module à part.
    application.include_router(routeur_revue)
    application.include_router(routeur_cloture_mensuelle)
    application.include_router(routeur_lettrage)
    application.include_router(routeur_fiche_ecriture)
    application.include_router(routeur_relance_des_pieces)
    application.include_router(routeur_accueil_de_l_adherent)
    application.include_router(routeur_echeances_de_l_adherent)
    application.include_router(routeur_mon_entreprise)
    application.include_router(routeur_mes_rappels)
    application.include_router(routeur_acces_adherents)
    application.include_router(routeur_file_d_anomalies)
    application.include_router(routeur_obligations)
    application.include_router(routeur_creations)
    application.include_router(routeur_social)
    application.include_router(routeur_cloture)
    # J · Pilotage vient en dernier des contextes métier : il les agrège tous.
    application.include_router(routeur_pilotage)
    # Pas 104 : le plan de travail des collaborateurs, qui réutilise les relevés du pilotage.
    application.include_router(routeur_plan_de_travail)
    # Pas 105 : la charge et la production des collaborateurs, et les réaffectations proposées.
    application.include_router(routeur_charge)
    # Pas 106 : le rapport mensuel de la direction, daté, archivé et intègre.
    application.include_router(routeur_rapport)
    # La vitrine publique : contenu éditorial du site, en lecture seule et sans
    # authentification — tout ce qu'elle rend est déjà destiné à être affiché.
    application.include_router(routeur_vitrine)
    # La souscription : elle part du site public et aboutit dans l'ERP. Montée en
    # dernier parce qu'elle est le seul contexte à traverser la frontière entre
    # les deux — un visiteur y entre sans compte et en ressort avec.
    application.include_router(routeur_souscription)
    # Le parcours d'acquisition, en amont de la souscription : il part du
    # formulaire public et s'arrête au dossier qualifié. Monté à part parce qu'il
    # a son propre préfixe et sa propre étiquette, et parce qu'il grandira.
    application.include_router(routeur_acquisition)
    # Le socle d'orchestration, monté en dernier : il n'appartient à aucun
    # contexte métier, et c'est lui qui les relie. Ses routes sont des gestes
    # d'exploitation, jamais du parcours client.
    application.include_router(routeur_orchestration)

    @application.get(
        "/sante",
        tags=["Technique"],
        summary="Vérification de disponibilité",
        responses={503: {"description": "Une dépendance vitale est injoignable"}},
    )
    def sante(requete: Request, reponse: Response) -> dict[str, object]:
        """L'état réel du service, dépendances comprises.

        ─────────────────────────────────────────────────────────────────────
        POURQUOI CETTE ROUTE INTERROGE LA BASE

        Elle répondait « opérationnel » sans rien vérifier. Un orchestrateur
        s'en sert pour décider d'envoyer du trafic : un conteneur dont la base
        est injoignable était déclaré sain, recevait des requêtes, et rendait
        des `500` à des adhérents. Le tableau de bord restait vert pendant ce
        temps — c'est le pire état d'une panne, celui où personne ne cherche.

        `SELECT 1` est délibérément minimal : la sonde s'exécute toutes les
        quelques secondes, et une vérification coûteuse deviendrait elle-même
        une cause de panne sous charge.

        ⚠️ `503` et non `200` avec un état dégradé dans le corps : un
        orchestrateur lit le code, pas le corps.

        POURQUOI ELLE PUBLIE AUSSI L'ÉTAT DU CLOISONNEMENT

        Une sonde qui répond « opérationnel » pendant que les politiques de
        cloisonnement ne s'appliquent pas **ment**, et de la façon la plus coûteuse :
        elle affirme précisément la chose qui est fausse. Le champ `cloisonnement`
        rend le constat de démarrage lisible sans ouvrir psql ni le journal.

        ⚠️ Elle **ne rend pas `503`** pour autant, et c'est un choix. Un cloisonnement
        contourné ne rend pas le service indisponible : le retirer du trafic ne
        protège rien et prive les adhérents. La sanction est ailleurs, et plus dure :
        la production refuse de démarrer. Hors production, l'état se lit ici.
        ─────────────────────────────────────────────────────────────────────
        """
        etat: dict[str, object] = {
            "etat": "operationnel",
            "environnement": config.environnement,
            "persistance": config.persistance,
        }
        # ⚠️ Annoncé, et non tu. Un serveur qui fabrique des encaissements sans
        # argent et retient les courriels ne doit jamais être pris pour une
        # production. Trois façons de s'en apercevoir : ce champ, le bandeau
        # d'avertissement de la boîte aux lettres, et le refus de démarrage en
        # production.
        if config.mode_demonstration:
            etat["mode_demonstration"] = True
        if not config.en_base:
            return etat

        try:
            with moteur().connect() as connexion:
                connexion.execute(text("SELECT 1"))
        except Exception as panne:  # noqa: BLE001 — la sonde ne doit jamais lever.
            _journal.error("Sonde de santé : base injoignable (%s)", type(panne).__name__)
            reponse.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            # ⚠️ Le détail de l'erreur reste au journal. Une sonde est souvent
            # exposée sans authentification, et le message d'un pilote de base
            # nomme l'hôte, le port et l'utilisateur.
            return etat | {"etat": "degrade", "cause": "base_de_donnees"}

        etat["base_de_donnees"] = "joignable"
        # `getattr` plutôt qu'un accès direct : les tests qui montent
        # l'application sans passer par le cycle de vie n'ont pas cet état, et la
        # sonde ne doit jamais lever — c'est sa seule promesse.
        diagnostic: DiagnosticDuRole | None = getattr(requete.app.state, "cloisonnement", None)
        if diagnostic is None:
            etat["cloisonnement"] = "NON_CONSTATE"
        else:
            etat["cloisonnement"] = diagnostic.etat.value
            if not diagnostic.applique:
                etat["cloisonnement_explication"] = diagnostic.explication
        return etat

    return application


app = creer_application()
