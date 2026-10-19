"""Le second facteur : enrôlement, vérification, révocation.

Extrait de `routes_http.py` le 16 octobre 2026. Les routes gardent leurs chemins ; le
routeur est monté à côté du sien.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.transverse.adaptateurs.entrant.dependances import (
    AccesRequis,
    atelier,
    exiger,
)
from app.contextes.transverse.adaptateurs.entrant.routes_http import (
    nouvel_identifiant,
)
from app.contextes.transverse.api import (
    Acces,
    AccesRefuse,
    CodeInvalide,
    Compte,
    CompteIntrouvable,
    JetonInvalide,
    Permission,
    PreuveDeBoiteRequise,
    ReinitialisationRefusee,
    SecondFacteurAbsent,
    SecondFacteurDejaActif,
    activer_second_facteur,
    confirmer_enrolement,
    emettre_jeton,
    engendrer_secret_totp,
    enroler_second_facteur,
    reinitialiser_second_facteur,
    renforcer_session,
    resoudre_acces,
    uri_provisionnement,
)
from app.contextes.transverse.contrats import TypeJeton
from app.partage.horloge import jour_a_douala, maintenant

routeur = APIRouter(prefix="/transverse", tags=["Identité et habilitations"])


# ── Second facteur ───────────────────────────────────────────────────────────


class Enrolement(BaseModel):
    """Ce qui est rendu **une seule fois**, à l'enrôlement.

    ⚠️ Le secret et l'adresse de provisionnement n'apparaissent que dans cette
    réponse, jamais dans une lecture ultérieure du compte — l'entité les exclut
    de sa sérialisation. Qui perd son téléphone réenrôle ; on ne lui « rappelle »
    pas son secret.

    ⚠️ **Un premier enrôlement ne rend pas le secret** (pas 62) : `secret` et `uri`
    sont vides, `confirmation_par_courriel` est vrai, et le secret sort de la route
    de confirmation, à qui présente le lien reçu.
    """

    secret: str | None = None
    uri: str | None = None
    confirmation_par_courriel: bool = False
    consigne: str


@routeur.post(
    "/second-facteur",
    summary="Enrôler son second facteur",
    description=(
        "Engendre un secret TOTP et le rattache au compte connecté. La réponse porte "
        "le secret **une seule fois** : à présenter en code-barres, puis à oublier.\n\n"
        "On enrôle pour soi-même et pour personne d'autre : un administrateur qui "
        "enrôlerait le facteur d'un tiers en détiendrait le second facteur, ce qui le "
        "viderait de son sens."
    ),
)
def enroler(acces: AccesRequis) -> Enrolement:
    """⚠️ Un compte déjà enrôlé ne remplace son facteur que sous session renforcée.

    Voir `enroler_second_facteur` : avec le mot de passe seul, cette route écrasait le
    secret et rendait le nouveau, et le second facteur ne protégeait plus de rien.
    """
    boutique = atelier()
    compte = boutique.comptes.lire(acces.compte)
    instant = maintenant()

    if not compte.second_facteur_actif:
        # ⚠️ Le premier enrôlement passe par la boîte aux lettres (pas 62) : aucun
        # secret ne sort ici, un lien part à l'adresse du compte.
        _, lien = emettre_jeton(
            compte,
            TypeJeton.ENROLEMENT,
            identifiant_jeton=nouvel_identifiant("J"),
            jetons=boutique.jetons,
            journal=boutique.journal,
            a_l_instant=instant,
            emis_par=compte.identifiant,
        )
        boutique.notifications.envoyer(
            "compte.second_facteur_confirmation",
            destinataire=compte.courriel,
            contexte={
                "prenom": compte.appellation,
                "lien": f"/second-facteur/confirmation?jeton={lien}",
            },
        )
        return Enrolement(
            confirmation_par_courriel=True,
            consigne=(
                "Un lien vient d'être envoyé à l'adresse de votre compte. Ouvrez-le dans "
                "ce navigateur, où vous êtes connecté : il est valable trente minutes."
            ),
        )

    secret = engendrer_secret_totp()
    try:
        enroler_second_facteur(
            compte,
            secret=secret,
            session_renforcee=acces.facteur_fort,
            comptes=boutique.comptes,
            journal=boutique.journal,
            a_l_instant=instant,
        )
    except SecondFacteurDejaActif as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    # Le titulaire est prévenu à l'ACTIVATION (19 octobre 2026) : c'est elle qui change
    # le facteur. Voir `activer`.
    return Enrolement(
        secret=secret,
        uri=uri_provisionnement(secret, compte=compte.courriel, emetteur="CGA Broad Range"),
        consigne=(
            "Scanner ce code dans une application d'authentification, puis saisir le "
            "premier code qu'elle affiche : la clé ne sert qu'une fois ce code accepté. "
            "Vérifier aussi que l'heure du téléphone est à l'heure."
        ),
    )


class DemandeConfirmationEnrolement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jeton: str = Field(min_length=16, max_length=200)


@routeur.post(
    "/second-facteur/confirmation",
    summary="Confirmer un premier enrôlement par le lien reçu",
    description=(
        "Consomme le lien envoyé à l'adresse du compte et rend le secret, **une seule "
        "fois**. Le lien appartient au compte de la session : présenté par un autre, il "
        "est refusé comme tout lien invalide."
    ),
    responses={
        409: {"description": "Un second facteur est déjà enrôlé"},
        410: {"description": "Lien inconnu, expiré, déjà utilisé, ou d'un autre compte"},
    },
)
def confirmer_l_enrolement(
    acces: AccesRequis, demande: DemandeConfirmationEnrolement
) -> Enrolement:
    boutique = atelier()
    compte = boutique.comptes.lire(acces.compte)
    secret = engendrer_secret_totp()
    try:
        confirmer_enrolement(
            demande.jeton,
            compte=compte,
            secret_totp=secret,
            comptes=boutique.comptes,
            jetons=boutique.jetons,
            journal=boutique.journal,
            a_l_instant=maintenant(),
        )
    except JetonInvalide as refus:
        raise HTTPException(
            status_code=410,
            detail="Ce lien n'est plus valable. Demander un nouvel enrôlement depuis l'écran.",
        ) from refus
    except (SecondFacteurDejaActif, PreuveDeBoiteRequise) as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    return Enrolement(
        secret=secret,
        uri=uri_provisionnement(secret, compte=compte.courriel, emetteur="CGA Broad Range"),
        consigne=(
            "Scanner ce code dans une application d'authentification, puis saisir le "
            "premier code qu'elle affiche : la clé ne sert qu'une fois ce code accepté. "
            "Vérifier aussi que l'heure du téléphone est à l'heure."
        ),
    )


class DemandeActivation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=6, max_length=8)


@routeur.post(
    "/second-facteur/activation",
    summary="Activer la clé affichée par un premier code",
    description=(
        "La clé rendue par l'enrôlement **attend** : elle ne protège ni ne bloque rien "
        "tant qu'un premier code juste ne l'a pas prouvée. Ce code active le second "
        "facteur, remplace l'ancien s'il y en avait un, et renforce la session."
    ),
    responses={
        400: {"description": "Aucune clé en attente"},
        401: {"description": "Code refusé"},
    },
)
def activer(acces: AccesRequis, demande: DemandeActivation) -> Acces:
    boutique = atelier()
    instant = maintenant()
    compte = boutique.comptes.lire(acces.compte)
    session = boutique.sessions.lire(acces.session)
    if session is None:
        raise HTTPException(status_code=401, detail="Session introuvable.")
    try:
        actif, renforcee = activer_second_facteur(
            session,
            compte,
            demande.code.replace(" ", ""),
            sessions=boutique.sessions,
            comptes=boutique.comptes,
            journal=boutique.journal,
            a_l_instant=instant,
        )
    except SecondFacteurAbsent as absence:
        raise HTTPException(status_code=400, detail=str(absence)) from absence
    except CodeInvalide as refus:
        raise HTTPException(status_code=401, detail=str(refus)) from refus
    # Le titulaire est prévenu : un enrôlement fait par un tiers se voit le jour même,
    # et non au premier dépôt suspect.
    boutique.notifications.envoyer(
        "compte.second_facteur_enrole",
        destinataire=actif.courriel,
        contexte={"prenom": actif.appellation},
    )
    return resoudre_acces(
        actif,
        renforcee,
        boutique.habilitations.pour_compte(actif.identifiant),
        jour_a_douala(instant),
        instant,
    )


class DemandeReinitialisation(BaseModel):
    #: Trente caractères : « téléphone perdu » ne suffit pas, le motif dit comment la
    #: perte a été signalée et vérifiée.
    motif: str = Field(min_length=30, max_length=500)


@routeur.post(
    "/comptes/{identifiant}/second-facteur/reinitialisation",
    summary="Réinitialiser le second facteur d'un collaborateur",
    description=(
        "Pour un appareil perdu. Retire le second facteur, **ferme toutes les sessions** "
        "du titulaire et le prévient par courriel. Jamais sur son propre compte, et "
        "n'enrôle rien à la place du titulaire."
    ),
    responses={
        403: {"description": "Habilitation insuffisante"},
        404: {"description": "Compte inconnu"},
        409: {"description": "Son propre compte, ou aucun second facteur enrôlé"},
    },
)
def reinitialiser_le_second_facteur(
    acces: AccesRequis, identifiant: str, demande: DemandeReinitialisation
) -> Compte:
    exiger(acces, Permission.GERER_COMPTES)
    boutique = atelier()
    try:
        compte = reinitialiser_second_facteur(
            par=acces,
            identifiant=identifiant,
            motif=demande.motif,
            comptes=boutique.comptes,
            habilitations=boutique.habilitations,
            sessions=boutique.sessions,
            journal=boutique.journal,
            a_l_instant=maintenant(),
        )
    except CompteIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence
    except AccesRefuse as refus:
        # 19 octobre 2026 : le compte visé tient les droits réservés. Seul le super
        # administrateur, session renforcée, peut le faire.
        raise HTTPException(
            status_code=403,
            detail="Ce compte tient les droits réservés : seul le super administrateur, "
            "second facteur présenté, peut faire ce geste.",
        ) from refus
    except ReinitialisationRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    boutique.notifications.envoyer(
        "compte.second_facteur_reinitialise",
        destinataire=compte.courriel,
        contexte={"prenom": compte.appellation},
    )
    return compte


class DemandeRenforcement(BaseModel):
    code: str = Field(min_length=6, max_length=8)


@routeur.post(
    "/session/renforcement",
    summary="Élever la session par un second facteur",
    description=(
        "Le code n'est pas réclamé à la connexion mais **au moment de l'acte "
        "sensible** : exiger un code dix fois par jour conduit au téléphone posé "
        "déverrouillé à côté du clavier.\n\n"
        "La session reste renforcée quinze minutes — assez pour déposer plusieurs "
        "déclarations à la suite, assez peu pour qu'un poste laissé ouvert ne dépose "
        "pas tout l'après-midi."
    ),
    responses={
        400: {"description": "Aucun second facteur enrôlé"},
        401: {"description": "Code refusé"},
    },
)
def renforcer(acces: AccesRequis, demande: DemandeRenforcement) -> Acces:
    boutique = atelier()
    instant = maintenant()
    compte = boutique.comptes.lire(acces.compte)
    session = boutique.sessions.lire(acces.session)
    if session is None:
        raise HTTPException(status_code=401, detail="Session introuvable.")
    try:
        renforcee = renforcer_session(
            session,
            compte,
            demande.code,
            sessions=boutique.sessions,
            journal=boutique.journal,
            a_l_instant=instant,
        )
    except SecondFacteurAbsent as absence:
        raise HTTPException(status_code=400, detail=str(absence)) from absence
    except CodeInvalide as refus:
        raise HTTPException(status_code=401, detail=str(refus)) from refus

    return resoudre_acces(
        compte,
        renforcee,
        boutique.habilitations.pour_compte(compte.identifiant),
        jour_a_douala(instant),
        instant,
    )


