"""La sonde du contexte K : les secrets sont-ils protégeables ?

⚠️ **C'est le service dont l'arrêt se voit le plus vite** : sans lui, plus personne
n'entre. Onze services en dépendent.
"""

from __future__ import annotations

from app.infrastructure.config import configuration
from app.registre.etat import Verdict

__all__ = ["sonde_du_transverse"]


def sonde_du_transverse() -> Verdict | None:
    """Les secrets sont-ils protégeables, et la passerelle a-t-elle de quoi router ?

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LE RÉPERTOIRE DES TENANTS EST VÉRIFIÉ ICI, ET NON DANS LE CONTEXTE N.**

    Il y était, et le test d'architecture l'a refusé : le contexte des Tenants
    appartient au socle et ne dépend de rien, pas même du Transverse. Il avait
    raison, et pas pour une raison de forme.

    Le répertoire est celui de la **passerelle**, garni au démarrage et consulté à
    chaque requête pour résoudre un nom d'hôte. Il vit ici, avec l'intergiciel qui
    le lit. Un répertoire vide se manifeste par des `404` sur tous les sous-domaines,
    ce qui est un symptôme du Transverse.

    Le contexte N décide qu'un tenant existe ; le Transverse décide qu'un nom d'hôte
    répond. Ce sont deux questions, et la sonde suit la seconde.
    ─────────────────────────────────────────────────────────────────────────────
    """
    verdict = _la_cle_de_chiffrement()
    return verdict if verdict is not None else _le_repertoire_des_tenants()


def _la_cle_de_chiffrement() -> Verdict | None:
    """La clé est-elle là quand elle doit l'être ?

    ⚠️ **Vérifiée en production seulement**, parce que la production refuse déjà de
    démarrer sans elle : cette sonde n'y ajoute rien et la double, ce qui est
    volontaire — un réglage retiré à chaud après le démarrage se verrait ici.

    Hors production, l'absence est un mode de travail admis, et alarmer dessus
    apprendrait aux développeurs à ignorer le registre.
    """
    config = configuration()
    if config.en_production and not config.cle_chiffrement:
        return Verdict.panne(
            "aucune clé de chiffrement : les secrets TOTP seraient écrits en clair, "
            "et une fuite de base livrerait tous les seconds facteurs du cabinet"
        )
    return None


def _le_repertoire_des_tenants() -> Verdict | None:
    """Y a-t-il de quoi résoudre un sous-domaine ?

    ⚠️ **SUSPECT, ET NON EN PANNE.** C'est le cas qui a fait naître ce niveau.

    Un répertoire vide trahit un garnissage échoué neuf fois sur dix, et tout
    sous-domaine rend alors 404. Mais il est parfaitement normal sur une installation
    neuve, où aucun tenant n'existe encore.

    Rendu `EN_PANNE`, ce constat marquait **treize services** comme ayant un appui
    tombé sur une base vierge. Une sonde qui crie au loup finit ignorée, et le jour
    où elle a raison personne ne regarde.
    """
    from app.contextes.transverse.adaptateurs.entrant.dependances import (
        repertoire_des_tenants,
    )

    if len(repertoire_des_tenants()) == 0:
        return Verdict.suspect(
            "aucun tenant au répertoire de la passerelle : soit l'installation est "
            "neuve, soit le garnissage a échoué au démarrage et tout sous-domaine "
            "rend 404"
        )
    return None
