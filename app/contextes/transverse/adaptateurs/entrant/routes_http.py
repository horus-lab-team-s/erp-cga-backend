"""API du contexte K · Transverse — identité, habilitations, journal d'audit.

─────────────────────────────────────────────────────────────────────────────────
LA ROUTE NE DÉCIDE PAS DES DROITS

Elle traduit, elle ne juge pas. Chaque cas d'usage vérifie lui-même la permission
qu'il exige — voir l'en-tête de `application/administration.py`. Ce que ces routes
ajoutent est la conversion d'un refus métier en code HTTP, et le choix de ce qui
est dit ou tu.

TROIS ROUTES RÉPONDENT VOLONTAIREMENT LA MÊME CHOSE À TOUT LE MONDE

`POST /session` sur des identifiants faux, `POST /mot-de-passe/oubli` sur une
adresse inconnue, `POST /mot-de-passe/definition` sur un lien périmé : la réponse
ne dit jamais si le compte existe. C'est ce qui empêche de dresser, par essais
successifs, la liste des collaborateurs et des adhérents du cabinet — laquelle a
une valeur commerciale propre.

L'oubli de mot de passe rend donc **202 dans tous les cas**, y compris quand rien
n'a été envoyé.
─────────────────────────────────────────────────────────────────────────────────

⚠️ 16 octobre 2026 : les comptes et habilitations (`routes_comptes.py`), le second
facteur (`routes_second_facteur.py`) et les mandats (`routes_mandats.py`) ont leur module,
sous les mêmes chemins.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.transverse.adaptateurs.entrant.dependances import (
    NOM_TEMOIN,
    AccesRequis,
    atelier,
    exiger,
    session_de_travail,
    unite_de_travail,
)
from app.contextes.transverse.adaptateurs.entrant.jeton import composer as composer_le_jeton
from app.contextes.transverse.adaptateurs.sortant.abonnements_yaml import (
    charger_les_abonnements,
)
from app.contextes.transverse.adaptateurs.sortant.lectures_des_notifications import (
    depot_des_lectures,
)
from app.contextes.transverse.api import (
    DUREE_SESSION,
    PERMISSIONS_PAR_ROLE,
    ROLES_A_PORTEE_OBLIGATOIRE,
    Acces,
    Compte,
    EntreeAudit,
    IdentifiantsRefuses,
    JetonInvalide,
    JournalAltere,
    MotDePasseRefuse,
    MotifRevocation,
    Permission,
    Role,
    definir_mot_de_passe,
    emettre_jeton,
    fermer_session,
    ouvrir_session,
    resoudre_acces,
    revoquer_les_sessions,
    verifier_chaine,
)
from app.contextes.transverse.contrats import TypeJeton
from app.contextes.transverse.domaine.notifications import Notification, notifications_pour
from app.infrastructure.config import configuration
from app.partage.horloge import jour_a_douala, maintenant
from app.partage.locataire import courant
from app.partage.recherche import (
    ReponseDeRecherche,
    RequeteTropCourte,
    ResultatDeRecherche,
    charger_les_reglages_de_recherche,
    pertinence,
    verifier_la_requete,
)
from app.partage.recherche import (
    # ⚠️ Renommée à l'import : la collecte a déjà une route `classer` (classer une
    # demande sans suite), et l'homonyme importée l'avait masquée. Même nom partout.
    classer as classer_les_resultats,
)

routeur = APIRouter(prefix="/transverse", tags=["Identité et habilitations"])


def nouvel_identifiant(prefixe: str) -> str:
    return f"{prefixe}-{uuid.uuid4().hex[:12]}"


# ── Catalogue ────────────────────────────────────────────────────────────────


class FicheRole(BaseModel):
    role: Role
    permissions: list[Permission]
    portee_obligatoire: bool


@routeur.get(
    "/roles",
    summary="Les rôles du cabinet et ce que chacun permet",
    description=(
        "Publié sans authentification : ce sont les règles du système, pas des "
        "données. Les connaître n'aide personne à entrer, et les cacher empêcherait "
        "un adhérent de comprendre pourquoi une action lui est refusée."
    ),
)
def lire_roles() -> list[FicheRole]:
    return [
        FicheRole(
            role=role,
            permissions=sorted(permissions, key=lambda p: p.value),
            portee_obligatoire=role in ROLES_A_PORTEE_OBLIGATOIRE,
        )
        for role, permissions in sorted(PERMISSIONS_PAR_ROLE.items(), key=lambda i: i[0].value)
    ]


# ── Session ──────────────────────────────────────────────────────────────────


class DemandeConnexion(BaseModel):
    courriel: str = Field(min_length=3)
    mot_de_passe: str = Field(min_length=1)


@routeur.post(
    "/session",
    summary="Ouvrir une session",
    description=(
        "Pose un témoin `HttpOnly`. Le même identifiant est rendu dans le corps pour "
        "l'outillage en ligne de commande, qui le présente en `Authorization: Bearer`. "
        "**Toute erreur d'identifiants rend le même 401**, quelle qu'en soit la cause."
    ),
    responses={401: {"description": "Identifiants refusés — cause non précisée"}},
)
def connexion(demande: DemandeConnexion, requete: Request, reponse: Response) -> Acces:
    boutique = atelier()
    # `client.host` est l'adresse vue par le serveur. Derrière un répartiteur, ce
    # sera celle du répartiteur : il faudra lire `X-Forwarded-For`, et cela suppose
    # de faire confiance à l'en-tête, donc de savoir qui est devant. Tant que ce
    # n'est pas décidé, on enregistre ce que l'on voit réellement.
    requete_ip = requete.client.host if requete.client else None
    instant = maintenant()
    try:
        session, compte = ouvrir_session(
            demande.courriel,
            demande.mot_de_passe,
            identifiant_session=nouvel_identifiant("S"),
            comptes=boutique.comptes,
            sessions=boutique.sessions,
            empreintes=boutique.empreintes,
            journal=boutique.journal,
            a_l_instant=instant,
            adresse_ip=requete_ip,
        )
    except IdentifiantsRefuses as refus:
        raise HTTPException(status_code=401, detail=str(refus)) from refus

    reponse.set_cookie(
        NOM_TEMOIN,
        # ⚠️ Le témoin porte le locataire où la session s'ouvre, et le bord le
        # confronte à celui du domaine à chaque requête. Voir `adaptateurs/entrant/
        # jeton.py` : sans cela, un témoin ouvert sur un sous-domaine était accepté
        # sur un autre, et la requête servie dans le périmètre du second.
        #
        # ⚠️ Le corps de la réponse, lui, rend toujours l'identifiant nu : c'est ce
        # qui identifie la session partout ailleurs, et ce que l'outillage présente
        # en `Authorization: Bearer`. Y mettre le témoin composé obligerait chaque
        # appelant à le découper pour retrouver l'identifiant.
        composer_le_jeton(courant(), session.identifiant),
        httponly=True,
        samesite="lax",
        max_age=int(DUREE_SESSION.total_seconds()),
        # ⚠️ DÉDUIT DE L'ADRESSE PUBLIQUE, et non écrit en dur. Ici se trouvait
        # `secure=False` et un commentaire disant de le passer à `True` en
        # production : personne ne l'aurait fait, et le témoin de session serait
        # parti en clair. Voir `Configuration.temoin_securise`.
        secure=configuration().temoin_securise,
    )
    return resoudre_acces(
        compte,
        session,
        boutique.habilitations.pour_compte(compte.identifiant),
        jour_a_douala(instant),
    )


@routeur.delete("/session", summary="Se déconnecter", status_code=204)
def deconnexion(acces: AccesRequis, reponse: Response) -> None:
    """Ferme la session **là où elle vit**, qui n'est pas toujours là où l'on est.

    ⚠️ **Sous mandat, la session appartient au mandataire.** Un comptable du cabinet qui
    travaille sur le sous-domaine d'une entreprise et clique sur « se déconnecter » ferait
    fermer sa session dans le périmètre de l'entreprise, où elle n'existe pas. Le geste
    rendait alors `204` sans rien faire, et l'utilisateur restait connecté en croyant le
    contraire : `fermer_session` est volontairement silencieux sur une session absente,
    parce qu'une déconnexion ne doit pas échouer sur une session déjà expirée.

    Constaté en écrivant le premier cas de test du mandat exercé, et corrigé ici.
    """
    instant = maintenant()
    chez_soi = acces.locataire == courant()
    if chez_soi:
        boutique = atelier()
        fermer_session(
            acces.session,
            sessions=boutique.sessions,
            journal=boutique.journal,
            a_l_instant=instant,
        )
    else:
        with unite_de_travail(acces.locataire) as chez_le_mandataire:
            fermer_session(
                acces.session,
                sessions=chez_le_mandataire.sessions,
                journal=chez_le_mandataire.journal,
                a_l_instant=instant,
            )
    reponse.delete_cookie(NOM_TEMOIN)


@routeur.get(
    "/moi",
    summary="Ce que la session courante permet",
    description=(
        "C'est cette route que le front interroge au chargement. Elle rend les rôles, "
        "les permissions et **la liste des dossiers accessibles** — de quoi construire "
        "un menu sans réinventer les règles côté navigateur. `dossiers` à `null` "
        "signifie « tout le portefeuille », ce qui ne se confond pas avec une liste vide."
    ),
)
def lire_moi(acces: AccesRequis) -> Acces:
    return acces


# ── Mot de passe ─────────────────────────────────────────────────────────────


class DemandeDefinition(BaseModel):
    secret: str = Field(min_length=1, description="Le jeton reçu dans le lien")
    mot_de_passe: str = Field(min_length=1)


@routeur.post(
    "/mot-de-passe/definition",
    summary="Définir son mot de passe à partir d'un lien",
    description=(
        "Point d'arrivée du parcours de souscription : le paiement validé fait partir "
        "un lien, ce lien mène ici. Le lien est à usage unique.\n\n"
        "Le mot de passe est contrôlé **avant** que le lien ne soit consommé : une "
        "faute de frappe ne doit pas détruire le lien."
    ),
    responses={
        400: {"description": "Mot de passe refusé — le motif est explicite"},
        410: {"description": "Lien inconnu, expiré ou déjà utilisé"},
    },
)
def definir(demande: DemandeDefinition) -> Compte:
    boutique = atelier()
    try:
        return definir_mot_de_passe(
            demande.secret,
            demande.mot_de_passe,
            comptes=boutique.comptes,
            jetons=boutique.jetons,
            sessions=boutique.sessions,
            empreintes=boutique.empreintes,
            journal=boutique.journal,
            a_l_instant=maintenant(),
        )
    except MotDePasseRefuse as refus:
        # Ce message-ci se montre : la personne choisit son mot de passe, elle est
        # légitime, et un refus sans explication la fait essayer au hasard.
        raise HTTPException(status_code=400, detail=str(refus)) from refus
    except JetonInvalide as refus:
        raise HTTPException(
            status_code=410,
            detail=(
                "Ce lien n'est plus valable. En demander un nouveau depuis la page de connexion."
            ),
        ) from refus


class DemandeOubli(BaseModel):
    courriel: str = Field(min_length=3)


class AccuseDOubli(BaseModel):
    """La même phrase, qu'un compte existe ou non (pas 78 : typé, plus un dictionnaire).

    ⚠️ Le modèle ne porte rien d'autre que la phrase : une réponse qui dirait si
    l'adresse est connue permettrait d'énumérer les comptes du cabinet.
    """

    message: str


@routeur.post(
    "/mot-de-passe/oubli",
    summary="Demander un lien de réinitialisation",
    status_code=202,
    description=(
        "**Rend 202 dans tous les cas**, y compris sur une adresse inconnue. Répondre "
        "404 sur une adresse absente offrirait un oracle d'énumération : on essaie une "
        "liste, on note ce qui répond, et l'on obtient les clients du cabinet."
    ),
)
def oubli(demande: DemandeOubli) -> AccuseDOubli:
    boutique = atelier()
    compte = boutique.comptes.par_courriel(demande.courriel)
    if compte is not None and compte.etat.value != "SUSPENDU":
        _, secret = emettre_jeton(
            compte,
            TypeJeton.REINITIALISATION,
            identifiant_jeton=nouvel_identifiant("J"),
            jetons=boutique.jetons,
            journal=boutique.journal,
            a_l_instant=maintenant(),
            emis_par="systeme",
        )
        boutique.notifications.envoyer(
            "compte.reinitialisation",
            destinataire=compte.courriel,
            contexte={
                "prenom": compte.appellation,
                # ⚠️ Un lien, jamais le secret nu. Un message qui affiche
                # « votre code : xY7… » apprend à l'adhérent qu'un secret se
                # recopie, et c'est exactement le geste qu'un hameçonnage lui
                # demandera ensuite. Le service de notification préfixe les
                # liens relatifs de l'adresse publique du site.
                "lien": f"/reinitialisation?jeton={secret}",
            },
        )
    return AccuseDOubli(
        message=(
            "Si un compte correspond à cette adresse, un lien vient d'être envoyé. "
            "Il est valable deux heures."
        )
    )


# ── Journal d'audit ──────────────────────────────────────────────────────────


@routeur.get(
    "/audit",
    summary="Le journal d'audit, dans l'ordre des rangs",
    description=(
        "Jamais trié autrement : c'est la séquence qui porte la garantie de "
        "non-altération, et un journal rendu par date ne se vérifie plus."
    ),
)
def lire_audit(
    acces: AccesRequis,
    acteur: str | None = Query(None),
    objet_type: str | None = Query(None),
    objet_id: str | None = Query(None),
) -> list[EntreeAudit]:
    exiger(acces, Permission.LIRE_AUDIT)
    return atelier().journal.lister(acteur=acteur, objet_type=objet_type, objet_id=objet_id)


class Verification(BaseModel):
    """Le résultat de la relecture de la chaîne."""

    entrees: int
    intacte: bool
    #: Renseigné seulement si la chaîne est rompue : dit **où**, pas seulement que.
    rupture: str | None = None


@routeur.get(
    "/audit/verification",
    summary="Vérifier la chaîne de hachage",
    description=(
        "Relit le journal du début et signale le rang exact du premier défaut. Les "
        "entrées qui précèdent restent vérifiées.\n\n"
        "⚠️ Le chaînage détecte l'altération ponctuelle et la corruption. Il ne protège "
        "pas de quelqu'un qui, disposant du code et de la base, recalculerait toute la "
        "chaîne. Il faudrait pour cela ancrer périodiquement l'empreinte de tête sur un "
        "support que le cabinet ne contrôle pas — ce n'est pas fait."
    ),
)
def verifier_audit(acces: AccesRequis) -> Verification:
    exiger(acces, Permission.LIRE_AUDIT)
    entrees = atelier().journal.lister()
    try:
        verifier_chaine(entrees)
    except JournalAltere as rupture:
        return Verification(entrees=len(entrees), intacte=False, rupture=str(rupture))
    return Verification(entrees=len(entrees), intacte=True)


# ── Sessions ─────────────────────────────────────────────────────────────────


class SessionsFermees(BaseModel):
    """Combien de sessions la révocation a fermées (pas 78 : typé, plus un dictionnaire)."""

    sessions_fermees: int


@routeur.post(
    "/comptes/{identifiant}/sessions/revocation",
    summary="Fermer toutes les sessions d'un compte",
    description=(
        "C'est ce que la session côté serveur rend possible et qu'un jeton autoportant "
        "interdirait : couper l'accès **maintenant**, et pas à l'expiration du jeton "
        "déjà remis."
    ),
)
def revoquer(acces: AccesRequis, identifiant: str) -> SessionsFermees:
    exiger(acces, Permission.GERER_COMPTES)
    boutique = atelier()
    fermees = revoquer_les_sessions(
        identifiant,
        sessions=boutique.sessions,
        journal=boutique.journal,
        a_l_instant=maintenant(),
        motif=MotifRevocation.REVOCATION_ADMINISTRATIVE,
        par=acces.compte,
    )
    return SessionsFermees(sessions_fermees=fermees)


# ── La boîte aux lettres de recette ─────────────────────────────────────────
#
# ⚠️ CE QUE CETTE ROUTE EXPOSE, ET POURQUOI ELLE NE PEUT PAS EXISTER AILLEURS
#
# Elle rend les **liens d'activation et de réinitialisation en clair**. Ce sont
# des secrets d'usage unique : quiconque les lit prend la main sur les comptes
# correspondants, sans mot de passe et sans laisser d'autre trace qu'une
# connexion parfaitement normale.
#
# Deux verrous, et il faut les deux :
#
#   · `CGA_MODE_DEMONSTRATION` doit être posé — un drapeau qu'on déclare, pas un
#     comportement déduit d'une configuration incomplète ;
#   · le service de notification doit être celui **qui retient** — s'il poste
#     vraiment, il n'y a rien à lire, et la route n'a plus lieu d'être.
#
# La production ne peut de toute façon pas démarrer avec ce drapeau : la
# configuration le refuse. Cette route ne peut donc pas s'y endormir.
#
# POURQUOI ELLE EXISTE
#
# Sans elle, le parcours de souscription est **invérifiable**. Le paiement passe,
# le compte se crée, le lien part dans un tableau en mémoire — et personne ne
# peut aller au bout. C'est le trou par lequel un défaut d'activation arriverait
# intact en production.


class MessageDeRecette(BaseModel):
    """Un courriel retenu, tel qu'il serait parti."""

    code: str
    destinataire: str
    contexte: dict


@routeur.get(
    "/courriels",
    summary="Les courriels retenus (recette)",
    description=(
        "⚠️ **Recette uniquement.** Rend les messages que le mode de développement "
        "retient au lieu de les envoyer, **liens d'activation en clair compris**. "
        "Exige `CGA_MODE_DEMONSTRATION` et un service de notification qui retient. "
        "Rend `404` dans tous les autres cas — un point d'entrée absent se "
        "distingue mal d'un point d'entrée qui refuse, et c'est voulu."
    ),
    responses={404: {"description": "Hors mode démonstration, ou les courriels partent"}},
)
def courriels_retenus(
    code: str | None = Query(None, description="Filtrer sur un code de gabarit"),
) -> list[MessageDeRecette]:
    if not configuration().mode_demonstration:
        raise HTTPException(status_code=404, detail="Not Found")
    service = atelier().notifications
    # `derniers` n'existe que sur la réalisation qui retient. Le test porte sur
    # la capacité, pas sur la classe : une seconde réalisation de développement
    # — un fichier, une base — se brancherait sans toucher à cette route.
    if not hasattr(service, "derniers"):
        raise HTTPException(status_code=404, detail="Not Found")
    return [
        MessageDeRecette(code=m.code, destinataire=m.destinataire, contexte=m.contexte)
        for m in service.derniers(code)
    ]


# ── La recherche globale (pas 93) ────────────────────────────────────────────


@routeur.get(
    "/recherche",
    summary="Chercher un compte du cabinet",
    responses={422: {"description": "Requête trop courte pour les réglages de recherche"}},
)
def chercher_un_compte(
    acces: AccesRequis,
    q: str = Query(min_length=1, max_length=100, description="Nom, prénom, courriel…"),
) -> ReponseDeRecherche:
    """Par identifiant ou courriel (identifiants), par nom et prénom (libellés).

    Réservée à `GERER_COMPTES`, comme la liste des comptes : un annuaire du cabinet
    consultable par tous dirait à un adhérent qui suit son dossier, et comment le
    joindre. La recherche n'ouvre rien que la liste n'ouvrait déjà.
    """
    exiger(acces, Permission.GERER_COMPTES)
    reglages = charger_les_reglages_de_recherche(configuration().dossier_referentiel)
    try:
        verifier_la_requete(q, reglages)
    except RequeteTropCourte as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus
    resultats = []
    for compte in atelier().comptes.lister():
        score = pertinence(
            q,
            identifiants=(compte.identifiant, compte.courriel),
            libelles=(f"{compte.prenom} {compte.nom}", f"{compte.nom} {compte.prenom}"),
            longueur_minimale=reglages.longueur_minimale,
        )
        if score is None:
            continue
        resultats.append(
            ResultatDeRecherche(
                nature="Compte",
                identifiant=compte.identifiant,
                titre=f"{compte.prenom} {compte.nom}",
                detail=f"{compte.courriel} · {compte.etat.value}",
                lien="/comptes",
                pertinence=score,
            )
        )
    return classer_les_resultats("transverse", resultats, reglages)


# ── Les notifications (pas 94) ───────────────────────────────────────────────
#
# Lues au journal d'audit à travers les abonnements du référentiel : voir l'en-tête de
# `domaine/notifications.py`. Aucune route n'en écrit ; seule la position de lecture
# s'enregistre.

#: Au plus, par lecture. La cloche n'est pas le journal d'audit : au-delà, l'écran
#: renvoie à la fenêtre du réglage, et les plus anciennes restent au journal.
NOTIFICATIONS_PAR_LECTURE = 50


class MesNotifications(BaseModel):
    non_lues: int
    notifications: list[Notification]
    #: Le fichier d'abonnements lu, ou « aucun fichier » : dit pourquoi rien n'arrive.
    source: str
    fenetre_jours: int


class DemandeDeLecture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Le rang de la notification la plus récente affichée : tout ce qui la précède est lu.
    jusqu_au_rang: int = Field(ge=1)


class PositionDeLecture(BaseModel):
    lu_jusqu_au_rang: int


@routeur.get("/notifications", summary="Mes notifications")
def lire_mes_notifications(acces: AccesRequis) -> MesNotifications:
    """Les entrées du journal qui concernent **la session qui lit**, dans la fenêtre du réglage.

    Aucune permission exigée au-delà de la session : chacun lit les siennes, et ce sont
    les abonnements qui décident de ce qu'elles contiennent (permission, périmètre,
    compte nommé). Le motif d'une entrée n'est jamais lu.
    """
    politique = charger_les_abonnements(configuration().dossier_referentiel)
    depuis = maintenant() - timedelta(days=politique.fenetre_jours)
    session = session_de_travail()
    position = depot_des_lectures(session).lu_jusqu_au_rang(acces.compte)
    toutes = notifications_pour(
        atelier().journal.lister(depuis=depuis),
        lecteur=acces,
        permissions=acces.permissions,
        politique=politique,
        lu_jusqu_au_rang=position,
    )
    return MesNotifications(
        non_lues=sum(1 for n in toutes if not n.lue),
        notifications=toutes[:NOTIFICATIONS_PAR_LECTURE],
        source=politique.source,
        fenetre_jours=politique.fenetre_jours,
    )


@routeur.post("/notifications/lecture", summary="Marquer mes notifications comme lues")
def marquer_mes_notifications_lues(
    acces: AccesRequis, demande: DemandeDeLecture
) -> PositionDeLecture:
    """Tout ce qui précède `jusqu_au_rang`, compris, devient lu. **Jamais en arrière.**

    ⚠️ Bornée à la tête du journal : un rang demandé au-delà marquerait lues, d'avance,
    des notifications qui n'existent pas encore, et la personne ne les verrait jamais.
    """
    tete = atelier().journal.tete()
    rang = min(demande.jusqu_au_rang, tete.rang if tete is not None else 0)
    if rang < 1:
        return PositionDeLecture(lu_jusqu_au_rang=0)
    retenu = depot_des_lectures(session_de_travail()).avancer(acces.compte, rang)
    return PositionDeLecture(lu_jusqu_au_rang=retenu)

