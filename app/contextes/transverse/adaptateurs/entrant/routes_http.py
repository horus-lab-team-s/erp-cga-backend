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
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, computed_field

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
    AccesRefuse,
    CodeInvalide,
    Compte,
    CompteIntrouvable,
    CourrielDejaPris,
    EntreeAudit,
    FermetureRefusee,
    Habilitation,
    HabilitationIntrouvable,
    IdentifiantsRefuses,
    JetonInvalide,
    JournalAltere,
    MotDePasseRefuse,
    MotifHabilitation,
    MotifRevocation,
    Permission,
    PreuveDeBoiteRequise,
    ReinitialisationRefusee,
    RetablissementRefuse,
    Role,
    SecondFacteurAbsent,
    SecondFacteurDejaActif,
    SuspensionRefusee,
    affecter_dossier,
    confirmer_enrolement,
    definir_mot_de_passe,
    dossiers_accessibles,
    emettre_jeton,
    engendrer_secret_totp,
    enroler_second_facteur,
    fermer_habilitation,
    fermer_session,
    habilitations_actives,
    inviter_collaborateur,
    ouvrir_session,
    reaffecter_dossier,
    reinitialiser_second_facteur,
    renforcer_session,
    resoudre_acces,
    retablir_compte,
    revoquer_les_sessions,
    roles_au,
    suspendre_compte,
    uri_provisionnement,
    verifier_chaine,
)
from app.contextes.transverse.contrats import TypeJeton
from app.contextes.transverse.domaine.mandats import Mandat, MotifMandat
from app.contextes.transverse.domaine.notifications import Notification, notifications_pour
from app.contextes.transverse.domaine.roles import libelle_role
from app.infrastructure.config import configuration
from app.partage.erreurs import message_lisible
from app.partage.formats import moment_long
from app.partage.horloge import maintenant
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


def _identifiant(prefixe: str) -> str:
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
            identifiant_session=_identifiant("S"),
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
        # ⚠️ `secure=False` pour que le développement en HTTP fonctionne. À passer
        # à `True` dès qu'un domaine est servi en HTTPS, faute de quoi le témoin
        # circulerait en clair sur un réseau partagé.
        secure=False,
    )
    return resoudre_acces(
        compte,
        session,
        boutique.habilitations.pour_compte(compte.identifiant),
        instant.date(),
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
            identifiant_jeton=_identifiant("J"),
            jetons=boutique.jetons,
            journal=boutique.journal,
            a_l_instant=maintenant(),
            emis_par="systeme",
        )
        boutique.notifications.envoyer(
            "compte.reinitialisation",
            destinataire=compte.courriel,
            contexte={
                "prenom": compte.prenom,
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


# ── Comptes ──────────────────────────────────────────────────────────────────


class LigneCompte(BaseModel):
    """Un compte, avec ses rôles résolus à la date demandée."""

    compte: Compte
    roles: list[Role]
    dossiers: list[str] | None
    #: Les habilitations actives à la date, avec leur identifiant (pas 70) : l'écran
    #: d'administration en a besoin pour affecter un dossier ou fermer un rôle. Sans
    #: elles, il connaissait les rôles d'un compte mais pas de quoi agir dessus.
    habilitations: list[Habilitation]

    @computed_field
    @property
    def sans_dossier(self) -> bool:
        """Habilité, mais sur aucun dossier. Ni une erreur, ni un état normal :
        c'est ce que l'écran d'administration doit faire remonter."""
        return self.dossiers is not None and not self.dossiers


@routeur.get("/comptes", summary="Les comptes du cabinet")
def lister_comptes(
    acces: AccesRequis,
    a_la_date: date = Query(..., description="Date à laquelle résoudre les rôles"),
) -> list[LigneCompte]:
    exiger(acces, Permission.GERER_COMPTES)
    boutique = atelier()
    lignes = []
    for compte in boutique.comptes.lister():
        habilitations = boutique.habilitations.pour_compte(compte.identifiant)
        dossiers = dossiers_accessibles(habilitations, a_la_date)
        lignes.append(
            LigneCompte(
                compte=compte,
                roles=sorted(roles_au(habilitations, a_la_date), key=lambda r: r.value),
                dossiers=None if dossiers is None else sorted(dossiers),
                habilitations=habilitations_actives(habilitations, a_la_date),
            )
        )
    return lignes


class DemandeInvitation(BaseModel):
    courriel: str = Field(min_length=3)
    nom: str = Field(min_length=1)
    prenom: str = Field(min_length=1)
    role: Role
    #: `null` = tout le portefeuille. Refusé pour un adhérent ou un inspecteur.
    portee: list[str] | None = None
    depuis: date
    telephone: str | None = None
    precision: str | None = None


class Invitation(BaseModel):
    """Ce que l'administrateur apprend d'une invitation. **Jamais le lien.**

    ⚠️ Ce modèle portait `lien_provisoire`, le secret d'activation en clair, avec ce
    motif : « rendu uniquement parce qu'aucun service d'envoi réel n'est branché ;
    dès que le module d'envoi sera greffé, ce champ doit disparaître ». Le service de
    courriel était branché, et le champ était resté (pas 69).

    Ce n'était pas qu'un secret journalisé par les intermédiaires : **l'administrateur
    qui invite pouvait activer lui-même le compte qu'il crée**, choisir le mot de passe
    et agir sous le nom du collaborateur. Le lien part au collaborateur, par courriel ;
    en démonstration, la boîte de recette le montre.
    """

    compte: Compte
    expire_le: str


@routeur.post(
    "/comptes/invitation",
    summary="Inviter un collaborateur",
    status_code=201,
    responses={409: {"description": "L'adresse est déjà celle d'un compte"}},
)
def inviter(acces: AccesRequis, demande: DemandeInvitation) -> Invitation:
    exiger(acces, Permission.GERER_COMPTES)
    boutique = atelier()
    try:
        compte, jeton, secret = inviter_collaborateur(
            par=acces,
            identifiant_compte=_identifiant("C"),
            identifiant_habilitation=_identifiant("H"),
            identifiant_jeton=_identifiant("J"),
            courriel=demande.courriel,
            nom=demande.nom,
            prenom=demande.prenom,
            role=demande.role,
            portee=None if demande.portee is None else frozenset(demande.portee),
            depuis=demande.depuis,
            comptes=boutique.comptes,
            habilitations=boutique.habilitations,
            jetons=boutique.jetons,
            journal=boutique.journal,
            a_l_instant=maintenant(),
            telephone=demande.telephone,
            precision=demande.precision,
        )
    except CourrielDejaPris as conflit:
        raise HTTPException(status_code=409, detail=str(conflit)) from conflit
    except AccesRefuse as refus:
        raise HTTPException(status_code=403, detail=str(refus)) from refus
    except ValueError as invalide:
        raise HTTPException(status_code=422, detail=message_lisible(invalide)) from invalide

    boutique.notifications.envoyer(
        "compte.invitation",
        destinataire=compte.courriel,
        contexte={
            "prenom": compte.prenom,
            "role": libelle_role(demande.role),
            "lien": f"/activation?jeton={secret}",
            "expire_le": moment_long(jeton.expire_le),
        },
    )
    return Invitation(compte=compte, expire_le=jeton.expire_le.isoformat())


class DemandeSuspension(BaseModel):
    motif: str = Field(min_length=3)


@routeur.post(
    "/comptes/{identifiant}/suspension",
    summary="Suspendre un compte",
    description=(
        "Ferme immédiatement toutes les sessions ouvertes. Le compte n'est **jamais** "
        "supprimé : son identifiant figure dans les écritures qu'il a validées et dans "
        "le journal d'audit."
    ),
)
def suspendre(acces: AccesRequis, identifiant: str, demande: DemandeSuspension) -> Compte:
    exiger(acces, Permission.GERER_COMPTES)
    boutique = atelier()
    try:
        compte = suspendre_compte(
            par=acces,
            identifiant=identifiant,
            motif=demande.motif,
            comptes=boutique.comptes,
            sessions=boutique.sessions,
            journal=boutique.journal,
            a_l_instant=maintenant(),
        )
    except CompteIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence
    except SuspensionRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    # ⚠️ Le titulaire est prévenu (pas 69). Le gabarit existait, « au catalogue, pas encore
    # appelé » : un collaborateur dont les sessions tombaient ne savait pas pourquoi. Le
    # motif ne part pas dans le courriel ; il est au journal, et le message renvoie au cabinet.
    boutique.notifications.envoyer(
        "compte.suspendu", destinataire=compte.courriel, contexte={"prenom": compte.prenom}
    )
    return compte


class DemandeRetablissement(BaseModel):
    """Pourquoi la suspension est levée. Écrit au journal d'audit."""

    model_config = ConfigDict(extra="forbid")

    motif: str = Field(min_length=10, max_length=500)


@routeur.post(
    "/comptes/{identifiant}/retablissement",
    summary="Lever la suspension d'un compte",
    description=(
        "Pas 91 : le cas d'usage existait au domaine, aucune route ne l'exposait. Seul un "
        "compte suspendu se rétablit ; ses habilitations, fermées ou non, ne sont pas "
        "touchées. Les sessions fermées par la suspension ne se rouvrent pas : le titulaire "
        "se reconnecte."
    ),
    responses={
        404: {"description": "Compte inconnu"},
        409: {"description": "Le compte n'est pas suspendu"},
    },
)
def retablir(acces: AccesRequis, identifiant: str, demande: DemandeRetablissement) -> Compte:
    exiger(acces, Permission.GERER_COMPTES)
    boutique = atelier()
    try:
        compte = retablir_compte(
            par=acces,
            identifiant=identifiant,
            motif=demande.motif,
            comptes=boutique.comptes,
            journal=boutique.journal,
            a_l_instant=maintenant(),
        )
    except CompteIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence
    except RetablissementRefuse as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    # Le titulaire est prévenu, comme de la suspension : il sait qu'il peut se reconnecter.
    boutique.notifications.envoyer(
        "compte.retabli", destinataire=compte.courriel, contexte={"prenom": compte.prenom}
    )
    return compte


# ── Habilitations et affectation de dossiers ─────────────────────────────────


@routeur.get(
    "/dossiers/{niu}/acces",
    summary="Qui a accès à ce dossier",
    description=(
        "Inclut les habilitations transverses, qui couvrent ce dossier aussi. Les "
        "omettre laisserait croire qu'un dossier n'est vu de personne alors que la "
        "direction et le réviseur y ont accès."
    ),
)
def lire_acces_dossier(
    acces: AccesRequis,
    niu: str,
    a_la_date: date = Query(...),
) -> list[Habilitation]:
    exiger(acces, Permission.LIRE_DOSSIER, dossier=niu)
    # ⚠️ PAS 86 : LA PORTÉE EST RAMENÉE À CE SEUL DOSSIER.
    #
    # La route rendait chaque habilitation entière, portée comprise. Essai avant
    # correction : l'adhérent de SARL BATIMENT PLUS, qui détient `LIRE_DOSSIER` sur son
    # dossier, lisait la portée du chargé de clientèle, c'est-à-dire **les NIU de six
    # autres clients du cabinet**. Un comptable lisait de même les dossiers confiés à ses
    # collègues. La règle du projet est de ne jamais apprendre à quelqu'un quels dossiers
    # le cabinet suit (voir `exiger_dossier`, 404 plutôt que 403).
    #
    # La question posée est « qui a accès à CE dossier » : la réponse n'a besoin que de
    # ce dossier. Une habilitation transverse garde sa portée nulle, qui ne nomme rien.
    return [
        h if h.portee is None else h.model_copy(update={"portee": frozenset({niu})})
        for h in habilitations_actives(atelier().habilitations.pour_dossier(niu), a_la_date)
    ]


@routeur.post(
    "/habilitations/{identifiant}/dossiers/{niu}",
    summary="Affecter un dossier à une habilitation, à compter d'aujourd'hui",
    description=(
        "L'allocation de portefeuille. `AFFECTER_DOSSIER` est détenue par la direction "
        "et par l'administration — la première décide de la répartition, la seconde "
        "l'exécute. Elle est refusée au comptable et au réviseur : quelqu'un qui "
        "pourrait s'ajouter un dossier n'aurait plus de périmètre du tout.\n\n"
        "⚠️ Rend **l'habilitation active après l'affectation**, dont l'identifiant peut "
        "différer de celui de l'URL : une habilitation qui a déjà couru est relayée "
        "par une successeur ouverte aujourd'hui, pour que l'historique ne dise pas le "
        "dossier confié depuis le recrutement (pas 70). Refusée sur une habilitation "
        "transverse, fermée, d'adhérent ou d'inspecteur, ou qui porte déjà le dossier."
    ),
)
def affecter(acces: AccesRequis, identifiant: str, niu: str) -> Habilitation:
    exiger(acces, Permission.AFFECTER_DOSSIER, dossier=niu)
    boutique = atelier()
    try:
        return affecter_dossier(
            par=acces,
            identifiant_habilitation=identifiant,
            identifiant_successeur=_identifiant("H"),
            niu=niu,
            habilitations=boutique.habilitations,
            journal=boutique.journal,
            a_l_instant=maintenant(),
        )
    except HabilitationIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence
    except ValueError as invalide:
        raise HTTPException(status_code=422, detail=message_lisible(invalide)) from invalide


class DemandeDeReaffectation(BaseModel):
    #: L'habilitation qui porte le dossier aujourd'hui, et celle qui le recevra.
    de: str = Field(min_length=1)
    vers: str = Field(min_length=1)
    motif: str = Field(min_length=1, max_length=500)


class Reaffectation(BaseModel):
    source: Habilitation
    cible: Habilitation


@routeur.post(
    "/dossiers/{niu}/reaffectation",
    summary="Faire passer un dossier d'un collaborateur à un autre, à compter d'aujourd'hui",
    description=(
        "Pas 105 : la réaffectation proposée par la vue charge et production. Retirer au "
        "premier et confier au second **le même jour**, avec un motif : trois faits au journal "
        "(retrait, affectation, réaffectation), annoncés au collaborateur qui cède, à celui qui "
        "reçoit et au chargé de clientèle du dossier. L'adhérent n'est pas prévenu."
    ),
)
def reaffecter(acces: AccesRequis, niu: str, demande: DemandeDeReaffectation) -> Reaffectation:
    exiger(acces, Permission.AFFECTER_DOSSIER, dossier=niu)
    boutique = atelier()
    try:
        source, cible = reaffecter_dossier(
            par=acces,
            niu=niu,
            de=demande.de,
            vers=demande.vers,
            successeur_de=_identifiant("H"),
            successeur_vers=_identifiant("H"),
            motif=demande.motif,
            habilitations=boutique.habilitations,
            journal=boutique.journal,
            a_l_instant=maintenant(),
        )
    except HabilitationIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence
    except ValueError as invalide:
        raise HTTPException(status_code=422, detail=message_lisible(invalide)) from invalide
    return Reaffectation(source=source, cible=cible)


class DemandeFermeture(BaseModel):
    le: date
    motif: MotifHabilitation


@routeur.post(
    "/habilitations/{identifiant}/fermeture",
    summary="Fermer une habilitation à une date",
    description=(
        "Elle n'est pas supprimée : la ligne demeure, et c'est elle qui permettra de "
        "dire dans trois ans qui était habilité le jour d'un dépôt."
    ),
)
def fermer(acces: AccesRequis, identifiant: str, demande: DemandeFermeture) -> Habilitation:
    exiger(acces, Permission.GERER_COMPTES)
    boutique = atelier()
    try:
        return fermer_habilitation(
            par=acces,
            identifiant=identifiant,
            le=demande.le,
            motif=demande.motif,
            habilitations=boutique.habilitations,
            journal=boutique.journal,
            a_l_instant=maintenant(),
        )
    except FermetureRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    except HabilitationIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence
    except ValueError as invalide:
        raise HTTPException(status_code=422, detail=message_lisible(invalide)) from invalide


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
            identifiant_jeton=_identifiant("J"),
            jetons=boutique.jetons,
            journal=boutique.journal,
            a_l_instant=instant,
            emis_par=compte.identifiant,
        )
        boutique.notifications.envoyer(
            "compte.second_facteur_confirmation",
            destinataire=compte.courriel,
            contexte={
                "prenom": compte.prenom,
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
    # Le titulaire est prévenu : un enrôlement fait par un tiers qui connaît le mot
    # de passe se voit ainsi le jour même, et non au premier dépôt suspect.
    boutique.notifications.envoyer(
        "compte.second_facteur_enrole",
        destinataire=compte.courriel,
        contexte={"prenom": compte.prenom},
    )
    return Enrolement(
        secret=secret,
        uri=uri_provisionnement(secret, compte=compte.courriel, emetteur="CGA Broad Range"),
        consigne=(
            "Scanner ce code dans une application d'authentification, puis vérifier "
            "que l'heure du téléphone est à l'heure : un décalage de plus d'une "
            "minute invalide tous les codes."
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
    boutique.notifications.envoyer(
        "compte.second_facteur_enrole",
        destinataire=compte.courriel,
        contexte={"prenom": compte.prenom},
    )
    return Enrolement(
        secret=secret,
        uri=uri_provisionnement(secret, compte=compte.courriel, emetteur="CGA Broad Range"),
        consigne=(
            "Scanner ce code dans une application d'authentification, puis vérifier que "
            "l'heure du téléphone est à l'heure : un décalage de plus d'une minute "
            "invalide tous les codes."
        ),
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
            sessions=boutique.sessions,
            journal=boutique.journal,
            a_l_instant=maintenant(),
        )
    except CompteIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence
    except ReinitialisationRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    boutique.notifications.envoyer(
        "compte.second_facteur_reinitialise",
        destinataire=compte.courriel,
        contexte={"prenom": compte.prenom},
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
        instant.date(),
        instant,
    )


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


# ── Mandats ──────────────────────────────────────────────────────────────────
#
# ⚠️ **CES ROUTES SONT CELLES DU MANDANT, ET DE LUI SEUL.**
#
# Un mandat s'accorde sur ses propres données, et se retire de même. Le mandataire n'a
# aucune route ici : lui en donner une reviendrait à laisser un locataire s'octroyer un
# accès chez un autre, ce qui est exactement ce que le mandat empêche.


class FicheMandat(BaseModel):
    """Un mandat, tel que l'écran du mandant le montre."""

    identifiant: str
    mandataire: str
    roles: list[Role]
    comptes: list[str] | None
    debut: date
    fin: date | None
    motif: MotifMandat
    accorde_par: str
    revoque_le: date | None
    revoque_par: str | None
    precision: str | None
    #: Calculé à la date du jour : c'est la seule question que l'écran pose vraiment.
    en_vigueur: bool


def _fiche(mandat: Mandat, aujourd_hui: date) -> FicheMandat:
    return FicheMandat(
        identifiant=mandat.identifiant,
        mandataire=mandat.mandataire,
        roles=sorted(mandat.roles, key=lambda r: r.value),
        comptes=None if mandat.comptes is None else sorted(mandat.comptes),
        debut=mandat.debut,
        fin=mandat.fin,
        motif=mandat.motif,
        accorde_par=mandat.accorde_par,
        revoque_le=mandat.revoque_le,
        revoque_par=mandat.revoque_par,
        precision=mandat.precision,
        en_vigueur=mandat.en_vigueur(aujourd_hui),
    )


@routeur.get(
    "/mandats",
    summary="Les mandats que ce locataire a accordés",
    description=(
        "Révoqués et expirés compris : un mandat retiré reste une trace, et c'est la "
        "première chose qu'un litige demande."
    ),
)
def lister_les_mandats(acces: AccesRequis) -> list[FicheMandat]:
    exiger(acces, Permission.GERER_COMPTES)
    aujourd_hui = maintenant().date()
    return [_fiche(m, aujourd_hui) for m in atelier().mandats.tous()]


class DemandeDeMandat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mandataire: str = Field(min_length=1, max_length=64)
    roles: list[Role] = Field(min_length=1)
    #: Absent ou nul = tous les comptes du mandataire.
    comptes: list[str] | None = None
    debut: date
    fin: date | None = None
    motif: MotifMandat
    precision: str | None = Field(default=None, max_length=500)


@routeur.post(
    "/mandats",
    status_code=201,
    summary="Accorder un mandat à un autre locataire",
    description=(
        "Le mandat autorise l'**exercice** de rôles déjà tenus dans le périmètre de ce "
        "locataire. Il n'accorde aucun rôle nouveau."
    ),
    responses={422: {"description": "Mandat incohérent — le motif est rendu"}},
)
def accorder_un_mandat(demande: DemandeDeMandat, acces: AccesRequis) -> FicheMandat:
    exiger(acces, Permission.GERER_COMPTES)
    instant = maintenant()
    try:
        mandat = Mandat(
            identifiant=_identifiant("MDT"),
            # ⚠️ Le mandant est le locataire servi, jamais un champ du corps. Le laisser
            # choisir permettrait d'accorder un mandat sur les données d'un autre.
            mandant=courant(),
            mandataire=demande.mandataire,
            roles=frozenset(demande.roles),
            comptes=None if demande.comptes is None else frozenset(demande.comptes),
            debut=demande.debut,
            fin=demande.fin,
            motif=demande.motif,
            accorde_par=acces.nom_complet,
            precision=demande.precision,
        )
    except ValidationError as refus:
        # ⚠️ `message_lisible` et non `str()` : la trace brute d'un validateur rendue à
        # l'appelant expose la forme interne du modèle et ne se lit pas.
        raise HTTPException(status_code=422, detail=message_lisible(refus)) from refus

    boutique = atelier()
    boutique.mandats.enregistrer(mandat)
    boutique.journal.ajouter(
        horodatage=instant,
        acteur=acces.compte,
        action="mandat.accorde",
        objet_type="mandat",
        objet_id=mandat.identifiant,
        apres={
            "mandataire": mandat.mandataire,
            "roles": sorted(r.value for r in mandat.roles),
            "debut": mandat.debut.isoformat(),
            "motif": mandat.motif.value,
        },
    )
    return _fiche(mandat, instant.date())


class DemandeDeRevocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: ⚠️ Exigé, et long : retirer un accès accordé à un tiers se motive, et c'est ce
    #: motif que l'on relit deux ans plus tard.
    motif: str = Field(min_length=10, max_length=500)


@routeur.post(
    "/mandats/{identifiant}/revocation",
    summary="Retirer un mandat avant son terme",
    description=(
        "La révocation porte sa propre date et son auteur. Elle n'écrase pas la fin "
        "prévue : un mandat retiré n'est pas un mandat arrivé à échéance."
    ),
    responses={404: {"description": "Aucun mandat de ce locataire porte cet identifiant"}},
)
def revoquer_un_mandat(
    identifiant: str, demande: DemandeDeRevocation, acces: AccesRequis
) -> FicheMandat:
    exiger(acces, Permission.GERER_COMPTES)
    boutique = atelier()
    mandat = boutique.mandats.lire(identifiant)
    if mandat is None:
        raise HTTPException(status_code=404, detail="Ce mandat n'existe pas.")
    if mandat.revoque_le is not None:
        raise HTTPException(status_code=409, detail="Ce mandat est déjà révoqué.")

    instant = maintenant()
    retire = mandat.model_copy(
        update={"revoque_le": instant.date(), "revoque_par": acces.nom_complet}
    )
    boutique.mandats.enregistrer(retire)
    boutique.journal.ajouter(
        horodatage=instant,
        acteur=acces.compte,
        action="mandat.revoque",
        objet_type="mandat",
        objet_id=retire.identifiant,
        avant={"revoque_le": None},
        apres={"revoque_le": retire.revoque_le.isoformat()},
        motif=demande.motif,
    )
    return _fiche(retire, instant.date())
