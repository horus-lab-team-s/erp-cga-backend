"""La limitation de débit sur les points d'entrée qui coûtent cher.

─────────────────────────────────────────────────────────────────────────────────
LE VERROUILLAGE DE COMPTE NE SUFFIT PAS, ET LA RAISON N'EST PAS CELLE QU'ON CROIT

Le contexte K verrouille déjà un compte après cinq échecs — voir
`authentification.py`. Cela défend **un compte** contre l'essai exhaustif de ses
mots de passe. Trois attaques passent au travers, et la troisième est la plus
sérieuse :

**1 · Le bourrage d'identifiants.** Une fuite ailleurs donne dix mille couples
adresse/mot de passe. L'attaquant en essaie un seul par compte : aucun compte
n'atteint cinq échecs, aucun verrou ne se déclenche, et les comptes qui
réutilisaient leur mot de passe tombent.

**2 · L'énumération des comptes.** La route de mot de passe oublié répond
délibérément la même chose dans tous les cas. Elle reste interrogeable des
milliers de fois, et le **temps de réponse** finit par trahir qui existe.

**3 · L'épuisement du processeur — et c'est le plus grave.** Argon2id est
*conçu* pour être lent : environ cent millisecondes par vérification, exprès.
Sans limite, quelqu'un envoie cent requêtes de connexion par seconde avec des
mots de passe quelconques et sature les cœurs de la machine. Aucun compte n'est
compromis, et plus personne ne peut se connecter — la fonction qui protège les
mots de passe devient l'arme qui coupe le service.

⚠️ CE QUE CETTE RÉALISATION NE FAIT PAS

**Elle compte par processus.** Trois instances derrière un répartiteur donnent
trois fois la limite. C'est un choix assumé : un compteur partagé exigerait
Redis, donc un service de plus à exploiter, à surveiller et à sauvegarder — pour
une plateforme dont le cabinet compte quelques dizaines d'utilisateurs. Le jour
où plusieurs instances tournent vraiment, ce module devient une façade devant un
compteur partagé, et rien d'autre ne bouge.

**Elle fait confiance à l'adresse vue.** Derrière un mandataire inverse,
`uvicorn --proxy-headers` la reconstitue depuis `X-Forwarded-For`. ⚠️ Exposer le
conteneur directement laisserait n'importe qui déclarer son adresse et contourner
la limite en la changeant à chaque requête. La borne du mandataire n'est pas une
commodité de déploiement, c'est ce qui rend ce module honnête.

**Elle ne remplace pas une protection en amont.** Un déni de service distribué
se traite avant d'atteindre l'application. Ceci arrête un poste, pas un réseau.

POURQUOI UNE FENÊTRE GLISSANTE ET NON UN SEAU À JETONS

Le seau se recharge en continu : un attaquant patient trouve le rythme exact qui
le maintient sous le seuil, indéfiniment. La fenêtre glissante répond à la
question qu'on se pose vraiment — « combien de tentatives depuis cette adresse
dans les cinq dernières minutes » —, et c'est aussi celle qu'on reposera en
lisant le journal après coup.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache as _lru_cache
from threading import Lock

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.partage.horloge import maintenant

__all__ = [
    "REGLES_PAR_DEFAUT",
    "IntergicielLimitationDebit",
    "Limiteur",
    "Regle",
    "limiteur_par_defaut",
    "vider_le_limiteur",
]

_journal = logging.getLogger("cga.limitation")


@dataclass(frozen=True)
class Regle:
    """Combien de requêtes, sur quelle durée, pour quel point d'entrée."""

    #: Le chemin exact, préfixe d'application compris.
    chemin: str
    methode: str
    #: Le nombre de requêtes tolérées dans la fenêtre.
    maximum: int
    #: La largeur de la fenêtre, en secondes.
    fenetre: float
    #: Ce que le journal écrira. Sert à retrouver l'incident six mois plus tard.
    motif: str


#: Les points d'entrée protégés, et pourquoi chacun l'est.
#:
#: ⚠️ Les seuils sont larges à dessein. Une limite serrée bloque d'abord les
#: usages légitimes — un cabinet dont trois collaborateurs partagent une même
#: sortie Internet se présente sous une seule adresse, et une agence entière
#: peut n'en avoir qu'une. Le but n'est pas d'arrêter la troisième tentative
#: d'un comptable distrait : c'est d'arrêter la trois-centième d'un automate.
REGLES_PAR_DEFAUT: tuple[Regle, ...] = (
    Regle(
        chemin="/transverse/session",
        methode="POST",
        maximum=30,
        fenetre=300,
        motif="connexion",
    ),
    Regle(
        chemin="/transverse/mot-de-passe/oubli",
        methode="POST",
        maximum=10,
        fenetre=3600,
        motif="mot de passe oublié",
    ),
    Regle(
        chemin="/transverse/mot-de-passe/definition",
        methode="POST",
        # Chaque appel essaie un jeton. Sans limite, l'espace des jetons se
        # balaie — ils sont longs, mais « long » n'est pas « inépuisable » face
        # à une machine qui ne dort pas.
        maximum=20,
        fenetre=3600,
        motif="définition du mot de passe",
    ),
    Regle(
        chemin="/transverse/session/renforcement",
        methode="POST",
        # Un code TOTP ne fait que six chiffres : un million de possibilités,
        # et la fenêtre de validité en accepte plusieurs. Sans limite, il se
        # devine en quelques heures — le second facteur ne vaut alors plus rien.
        maximum=15,
        fenetre=900,
        motif="second facteur",
    ),
    Regle(
        chemin="/souscription/devis",
        methode="POST",
        # Pas une question de sécurité mais de propreté : sans borne, un
        # automate remplit la base de devis fantômes, et le pilotage commercial
        # devient illisible.
        maximum=20,
        fenetre=3600,
        motif="création de devis",
    ),
)


@dataclass
class Limiteur:
    """Le compteur à fenêtre glissante, par règle et par adresse."""

    regles: tuple[Regle, ...] = REGLES_PAR_DEFAUT
    _horodatages: dict[tuple[str, str], deque[float]] = field(
        default_factory=dict, repr=False
    )
    # ⚠️ Uvicorn exécute les routes synchrones dans un réservoir de fils. Deux
    # requêtes simultanées manipuleraient la même file sans ce verrou, et
    # `deque` n'est atomique que pour ses opérations élémentaires — pas pour la
    # séquence « purger, compter, ajouter ».
    _verrou: Lock = field(default_factory=Lock, repr=False)

    def regle_pour(self, methode: str, chemin: str) -> Regle | None:
        for regle in self.regles:
            if regle.methode == methode and regle.chemin == chemin:
                return regle
        return None

    def autorise(self, regle: Regle, adresse: str) -> bool:
        """Enregistre la tentative et dit si elle passe.

        ⚠️ Une requête refusée **n'est pas comptée**. Sinon un attaquant qui
        continue de marteler maintiendrait la fenêtre pleine indéfiniment, et
        l'adresse resterait bloquée bien après la fin de son attaque — y compris
        pour l'utilisateur légitime qui la partage.
        """
        instant = maintenant().timestamp()
        cle = (regle.chemin, adresse)
        with self._verrou:
            passages = self._horodatages.setdefault(cle, deque())
            limite = instant - regle.fenetre
            while passages and passages[0] < limite:
                passages.popleft()
            if len(passages) >= regle.maximum:
                return False
            passages.append(instant)
            return True

    def purger(self) -> int:
        """Oublie les adresses dont la fenêtre est vide. Rend le nombre oublié.

        ⚠️ Sans cela, le dictionnaire garde une entrée par adresse vue depuis le
        démarrage — une fuite lente, invisible en test et visible au bout de
        quelques mois d'exploitation. Appelée à l'occasion des requêtes, sans
        tâche de fond : une minuterie de plus pour ranger un dictionnaire ne se
        justifie pas.
        """
        instant = maintenant().timestamp()
        with self._verrou:
            vides = [
                cle
                for cle, passages in self._horodatages.items()
                if not passages or passages[-1] < instant - 3600
            ]
            for cle in vides:
                del self._horodatages[cle]
            return len(vides)

    def vider(self) -> None:
        """Repart de zéro. Destinée aux tests."""
        with self._verrou:
            self._horodatages.clear()


def adresse_de(requete: Request) -> str:
    """L'adresse de l'appelant, telle que le serveur la voit.

    ⚠️ `request.client` est déjà celle reconstituée par `--proxy-headers` quand
    l'application tourne derrière un mandataire. On ne relit donc **pas**
    `X-Forwarded-For` ici : le faire à la main, sans savoir combien de
    mandataires sont traversés, laisserait n'importe qui préfixer l'en-tête et
    choisir son adresse.
    """
    return requete.client.host if requete.client else "inconnue"


class IntergicielLimitationDebit(BaseHTTPMiddleware):
    """Applique les règles avant que la requête n'atteigne la route.

    Placé **avant** l'unité de travail : une requête refusée ne doit pas ouvrir
    de transaction, et surtout pas déclencher la vérification Argon2 dont elle
    cherche précisément à saturer le processeur.
    """

    def __init__(self, application, limiteur: Limiteur | None = None) -> None:
        super().__init__(application)
        # ⚠️ **Le limiteur par défaut est partagé, et joignable.**
        #
        # Il était construit ici, sans que personne puisse l'atteindre. Le
        # compteur survivait donc d'un cas de test au suivant, et la suite
        # entière épuisait le budget de trente connexions par cinq minutes : des
        # cas tombaient au **montage** de leur décor, loin de ce qu'ils
        # vérifient, et seulement quand on les lançait tous ensemble.
        #
        # `Limiteur.vider` portait pourtant déjà la mention « destinée aux
        # tests » : la méthode existait, l'objet était hors d'atteinte.
        self._limiteur = limiteur or limiteur_par_defaut()
        self._depuis_la_purge = 0

    async def dispatch(self, requete: Request, appeler_suivant) -> Response:
        regle = self._limiteur.regle_pour(requete.method, requete.url.path)
        if regle is None:
            return await appeler_suivant(requete)

        self._purger_de_temps_en_temps()
        adresse = adresse_de(requete)
        if self._limiteur.autorise(regle, adresse):
            return await appeler_suivant(requete)

        # Journalisé en `warning` : ce n'est pas une panne, mais c'est le
        # premier signal d'une attaque en cours, et il doit se retrouver.
        _journal.warning(
            "Limite atteinte sur %s (%s) depuis %s", regle.chemin, regle.motif, adresse
        )
        return JSONResponse(
            status_code=429,
            # ⚠️ `Retry-After` en secondes : les clients corrects le respectent,
            # ce qui évite qu'un front un peu insistant ne s'auto-bloque.
            headers={"Retry-After": str(int(regle.fenetre))},
            content={
                "detail": (
                    "Trop de tentatives depuis cette adresse. "
                    f"Réessayez dans {int(regle.fenetre // 60)} minutes."
                )
            },
        )

    def _purger_de_temps_en_temps(self) -> None:
        self._depuis_la_purge += 1
        if self._depuis_la_purge < 1000:
            return
        self._depuis_la_purge = 0
        oubliees = self._limiteur.purger()
        if oubliees:
            _journal.debug("Limitation : %d adresses oubliées", oubliees)


@_lru_cache
def limiteur_par_defaut() -> Limiteur:
    """L'unique limiteur du processus, sauf injection explicite.

    ⚠️ Partagé à dessein : deux instances compteraient séparément, et la limite
    réelle vaudrait le double de celle qui est écrite. En production il n'y a
    qu'un intergiciel, donc la question ne se posait pas — jusqu'au jour où une
    suite de tests monte plusieurs applications.
    """
    return Limiteur()


def vider_le_limiteur() -> None:
    """Remet le compteur à zéro. Destinée aux tests.

    ⚠️ Elle ne remplace pas `Limiteur.vider` : elle la rend **atteignable**. Un
    garde-fou de production qu'aucun décor ne peut réinitialiser fait tomber les
    cas pour une raison qui n'est pas la leur, et l'on finit par désactiver le
    garde-fou plutôt que par le comprendre.
    """
    limiteur_par_defaut().vider()
