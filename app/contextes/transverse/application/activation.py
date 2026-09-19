"""Émettre un lien, puis définir le mot de passe qu'il autorise.

─────────────────────────────────────────────────────────────────────────────────
LA POLITIQUE : UNE LONGUEUR, PAS UNE COMPOSITION

Douze caractères minimum, et aucune règle de composition — ni majuscule imposée,
ni chiffre, ni caractère spécial.

Ce n'est pas du laxisme, c'est le constat de ce que les règles de composition
produisent réellement. Exiger « une majuscule, un chiffre, un caractère spécial »
donne `Douala2026!` chez tout le monde : la majuscule est la première lettre, le
chiffre est l'année, le caractère spécial est le point d'exclamation final. Le
résultat est un mot de passe de onze caractères que n'importe quel dictionnaire
de mutations casse, et que son porteur note sur un papier parce qu'il ne s'en
souvient pas.

`chemise bleue mardi tarif` fait vingt-cinq caractères, se retient, ne se note pas,
et résiste incomparablement mieux. C'est la recommandation de l'état de l'art
depuis le retournement du NIST en 2017, et elle est retenue ici.

Ce qui est refusé, en revanche : les mots de passe qui **contiennent l'identité de
leur porteur** — son nom, son prénom, la partie gauche de son adresse. Ce sont les
premiers essais de quiconque cible une personne précise, et ils échappent à tout
contrôle de longueur.

L'ORDRE DES OPÉRATIONS : ON VALIDE AVANT DE CONSOMMER

Le mot de passe est contrôlé **avant** que le jeton ne soit marqué consommé. Un
adhérent qui saisit un mot de passe trop court verrait sinon son lien à usage
unique détruit par sa propre erreur de frappe, et devrait appeler le cabinet pour
en obtenir un autre. Ce détail décide de la moitié des appels au support.

CHANGER SON MOT DE PASSE FERME SES AUTRES SESSIONS

C'est le geste qu'on fait quand on croit son compte compromis. Le laisser sans
effet sur les sessions déjà ouvertes le viderait de son sens : celui qui les
détient continuerait à travailler.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta

from app.contextes.transverse.domaine.identites import Compte
from app.contextes.transverse.domaine.jetons import (
    JETONS_DE_MOT_DE_PASSE,
    Jeton,
    JetonInvalide,
    TypeJeton,
    empreinte_de,
    engendrer_secret,
)
from app.contextes.transverse.domaine.ports import (
    DepotComptes,
    DepotJetons,
    DepotSessions,
    JournalAudit,
    ServiceEmpreinte,
)
from app.contextes.transverse.domaine.sessions import MotifRevocation

__all__ = [
    "LONGUEUR_MINIMALE",
    "MotDePasseRefuse",
    "controler_mot_de_passe",
    "definir_mot_de_passe",
    "emettre_jeton",
]

#: Douze. Voir l'en-tête pour le raisonnement, et pour ce qui n'est pas exigé.
LONGUEUR_MINIMALE = 12

#: Les suites que l'on retrouve dans tous les corpus de mots de passe divulgués,
#: complétées de celles que produit le contexte local. La liste est courte et
#: assumée comme telle : elle attrape la paresse manifeste, pas les mots de passe
#: faibles en général — c'est la longueur qui s'en charge.
_TROP_COURANTS: frozenset[str] = frozenset(
    {
        "motdepasse",
        "password",
        "azertyuiop",
        "qwertyuiop",
        "123456789012",
        "cameroun2026",
        "douala2026",
        "yaounde2026",
        "administrateur",
        "comptabilite",
    }
)


class MotDePasseRefuse(ValueError):
    """Le mot de passe ne convient pas.

    Ce message-ci, contrairement à celui de l'authentification, **doit** être
    montré : la personne est en train de choisir, elle est légitime, et un refus
    sans explication la conduit à essayer au hasard.
    """


def _sans_accents(texte: str) -> str:
    decompose = unicodedata.normalize("NFKD", texte.lower())
    return "".join(c for c in decompose if not unicodedata.combining(c))


def controler_mot_de_passe(mot_de_passe: str, compte: Compte) -> None:
    """Lève `MotDePasseRefuse` si le mot de passe ne convient pas."""
    if len(mot_de_passe) < LONGUEUR_MINIMALE:
        raise MotDePasseRefuse(
            f"{LONGUEUR_MINIMALE} caractères au minimum, {len(mot_de_passe)} fournis. "
            "Une phrase de quatre mots ordinaires convient parfaitement et se retient "
            "mieux qu'une suite de symboles."
        )

    nu = _sans_accents(mot_de_passe)
    if nu in _TROP_COURANTS:
        raise MotDePasseRefuse(
            "Ce mot de passe figure parmi les plus essayés. Choisir une suite de mots "
            "qui n'a de sens que pour vous."
        )

    partie_gauche = compte.courriel.split("@")[0]
    for element in (compte.nom, compte.prenom, partie_gauche):
        if len(element) >= 3 and _sans_accents(element) in nu:
            raise MotDePasseRefuse(
                f"Le mot de passe contient « {element} ». Nom, prénom et adresse sont "
                "les premiers essais de quiconque vous vise nommément."
            )


def emettre_jeton(
    compte: Compte,
    type: TypeJeton,
    *,
    identifiant_jeton: str,
    jetons: DepotJetons,
    journal: JournalAudit,
    a_l_instant: datetime,
    emis_par: str,
    duree: timedelta | None = None,
) -> tuple[Jeton, str]:
    """Engendre un lien à usage unique et rend **le jeton et son secret en clair**.

    Le secret n'est rendu qu'ici, et n'est stocké nulle part. L'appelant l'insère
    dans le courriel puis le perd. Voir l'en-tête de `jetons.py`.

    ⚠️ Le journal enregistre l'émission, jamais le secret : `expurger` masque la
    clé `secret`, mais on ne la lui donne même pas.
    """
    secret = engendrer_secret()
    jeton = Jeton.emettre(
        identifiant=identifiant_jeton,
        compte=compte.identifiant,
        type=type,
        secret=secret,
        a_l_instant=a_l_instant,
        emis_par=emis_par,
        duree=duree,
    )
    jetons.enregistrer(jeton)
    journal.ajouter(
        horodatage=a_l_instant,
        acteur=emis_par,
        action="jeton.emis",
        objet_type="compte",
        objet_id=compte.identifiant,
        apres={"type": type, "expire_le": jeton.expire_le},
    )
    return jeton, secret


def definir_mot_de_passe(
    secret: str,
    mot_de_passe: str,
    *,
    comptes: DepotComptes,
    jetons: DepotJetons,
    sessions: DepotSessions,
    empreintes: ServiceEmpreinte,
    journal: JournalAudit,
    a_l_instant: datetime,
) -> Compte:
    """Consomme le lien et définit le mot de passe. Active le compte au passage.

    Lève `JetonInvalide` — message unique, voir `jetons.py` — ou
    `MotDePasseRefuse`, dont le message est au contraire destiné à être montré.
    """
    jeton = jetons.par_empreinte(empreinte_de(secret))
    if jeton is None:
        raise JetonInvalide("empreinte inconnue")
    # ⚠️ Le type était ignoré (pas 62). Sans ce contrôle, le lien d'enrôlement du second
    # facteur, reçu par courriel, aurait aussi redéfini le mot de passe du compte.
    if jeton.type not in JETONS_DE_MOT_DE_PASSE:
        raise JetonInvalide(f"jeton {jeton.identifiant} de type {jeton.type} : pas un mot de passe")

    compte = comptes.lire(jeton.compte)

    # Voir l'en-tête : on valide avant de consommer, pour qu'une faute de frappe
    # ne détruise pas le lien.
    controler_mot_de_passe(mot_de_passe, compte)

    jetons.enregistrer(jeton.consommer(a_l_instant))

    active = compte.avec_empreinte(empreintes.deriver(mot_de_passe), a_l_instant=a_l_instant)
    comptes.enregistrer(active)

    # Toutes les sessions tombent — voir l'en-tête.
    fermees = 0
    for session in sessions.pour_compte(compte.identifiant):
        if session.active(a_l_instant):
            sessions.enregistrer(
                session.revoquer(a_l_instant, MotifRevocation.CHANGEMENT_MOT_DE_PASSE)
            )
            fermees += 1

    journal.ajouter(
        horodatage=a_l_instant,
        acteur=compte.identifiant,
        action="compte.mot_de_passe_defini",
        objet_type="compte",
        objet_id=compte.identifiant,
        avant={"etat": compte.etat},
        apres={"etat": active.etat, "sessions_fermees": fermees, "jeton": jeton.type},
    )
    return active
