"""Le registre qui écrit en base **et** rend le sous-domaine joignable tout de suite.

─────────────────────────────────────────────────────────────────────────────────
LE DÉFAUT QUE CE MODULE CORRIGE, ET SA GRAVITÉ

L'ouverture d'un tenant écrivait dans un registre **en mémoire**, y compris en
persistance PostgreSQL. Mesuré : un paiement encaissé, la saga rendait `TERMINEE`, et
la table `tenant` contenait **zéro ligne**.

⚠️ C'est la pire forme de défaut de ce projet : le système **annonçait le succès de
ce qu'il n'avait pas fait**. Le client payait, l'exécution était marquée terminée,
l'événement publié, et le tenant disparaissait au redémarrage suivant. Le
sous-domaine rendait alors 404 sur un abonnement payé.

POURQUOI ÉCRIRE EN BASE NE SUFFIT PAS

La passerelle résout chaque nom d'hôte dans un répertoire **en mémoire**, garni depuis
la table **au démarrage**. Écrire la ligne sans inscrire au répertoire ferait que le
sous-domaine d'un client qui vient de payer ne répondrait qu'au prochain
redéploiement.

Un client qui règle attend que son espace s'ouvre, pas la prochaine livraison.

⚠️ **LES DEUX ÉCRITURES NE SONT PAS SYMÉTRIQUES, ET L'ORDRE COMPTE.**

La base d'abord, le répertoire ensuite. Si la base refuse — un slug déjà pris, une
contrainte violée —, l'exception remonte et le répertoire n'a rien appris : la
passerelle ne servira pas un tenant qui n'existe pas.

L'ordre inverse laisserait un tenant joignable en mémoire et absent de la base, donc
servi jusqu'au redémarrage puis évanoui. C'est exactement le défaut qu'on corrige,
avec une fenêtre plus courte.

CE QUI RESTE À LA CHARGE DE LA TRANSACTION

Le répertoire est inscrit avant la validation de la transaction. Si celle-ci est
annulée après coup, le répertoire garde une entrée que la base n'a pas.

⚠️ La fenêtre est étroite et la conséquence bénigne : le prochain garnissage la
corrige, et entre-temps la passerelle sert un tenant dont les tables sont vides, ce
qui donne un espace sans données plutôt qu'une fuite. Fermer cette fenêtre demanderait
d'accrocher l'inscription à la validation de la session, ce qui lierait le registre au
cycle de vie de SQLAlchemy pour un gain que l'exploitation ne verra pas.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from app.contextes.tenants.domaine.tenant import Tenant

__all__ = ["RegistreDurable"]


class RegistreDurable:
    """Réalise `RegistreDesTenants` en écrivant aux deux endroits qui comptent.

    Un composite plutôt qu'une méthode de plus sur le dépôt SQL : le dépôt ne doit
    rien savoir du répertoire de la passerelle, qui appartient à un autre contexte
    et vit dans un autre processus le jour où les services seront séparés.
    """

    def __init__(self, depot, repertoire, session) -> None:
        self._depot = depot
        self._repertoire = repertoire
        self._session = session

    def par_slug(self, slug: str) -> Tenant | None:
        """Lit **en base**, jamais au répertoire.

        ⚠️ Le répertoire peut être incomplet : il est garni au démarrage et
        n'apprend que ce qui passe par ce registre. Le provisionnement s'en sert
        pour savoir si un slug est déjà pris, et cette question ne souffre pas
        d'à-peu-près : deux tenants sur le même sous-domaine se serviraient l'un
        les données de l'autre.
        """
        return self._depot.par_slug(slug)

    def enregistrer(self, tenant: Tenant) -> None:
        """La base d'abord, le répertoire ensuite. Voir l'en-tête pour l'ordre.

        ─────────────────────────────────────────────────────────────────────────
        ⚠️ **LE VIDAGE EXPLICITE, ET POURQUOI IL EST INDISPENSABLE ICI.**

        Ce projet coupe `autoflush` délibérément : il enverrait des `INSERT` au
        moindre `SELECT` intercalé, rendrait l'ordre des écritures imprévisible, et
        ferait échouer des contraintes loin du code fautif. *On écrit quand on le
        décide.*

        Or la saga d'ouverture **relit ce qu'elle vient d'écrire**, pas après pas :
        elle réserve le slug, puis cherche le tenant par son slug pour créer sa
        ligne, puis le cherche encore pour l'étape suivante. Sans vidage, chaque
        lecture manque l'écriture précédente.

        C'est ce qui s'est produit : la saga s'est arrêtée au deuxième pas sur
        « tenant introuvable au répertoire », alors qu'il venait d'être écrit.

        ⚠️ **Vider n'est pas valider.** La transaction gouverne toujours
        l'atomicité : si le tour échoue plus loin, tout est annulé, y compris ces
        lignes. Le vidage ne fait qu'envoyer les écritures pour que les lectures de
        la même transaction les voient.
        ─────────────────────────────────────────────────────────────────────────
        """
        self._depot.enregistrer(tenant)
        self._session.flush()
        self._repertoire.inscrire(tenant)
