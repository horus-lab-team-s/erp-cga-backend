"""Les comptes, leurs habilitations et l'affectation des dossiers.

Extrait de `routes_http.py` le 16 octobre 2026. Les routes gardent leurs chemins ; le
routeur est monté à côté du sien.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.contextes.transverse.adaptateurs.entrant.dependances import (
    AccesRequis,
    atelier,
    exiger,
    exiger_un_geste_fort,
)
from app.contextes.transverse.adaptateurs.entrant.routes_http import (
    nouvel_identifiant,
)
from app.contextes.transverse.api import (
    AccesRefuse,
    CodeRole,
    Compte,
    CompteIntrouvable,
    CourrielDejaPris,
    FermetureRefusee,
    Habilitation,
    HabilitationIntrouvable,
    MotifHabilitation,
    Permission,
    RetablissementRefuse,
    SuspensionRefusee,
    affecter_dossier,
    dossiers_accessibles,
    fermer_habilitation,
    habilitations_actives,
    inviter_collaborateur,
    matrice_en_vigueur,
    reaffecter_dossier,
    retablir_compte,
    roles_au,
    suspendre_compte,
)
from app.contextes.transverse.domaine.roles import libelle_role
from app.partage.erreurs import message_lisible
from app.partage.formats import moment_long
from app.partage.horloge import maintenant

routeur = APIRouter(prefix="/transverse", tags=["Identité et habilitations"])


# ── Comptes ──────────────────────────────────────────────────────────────────


class LigneCompte(BaseModel):
    """Un compte, avec ses rôles résolus à la date demandée."""

    compte: Compte
    roles: list[CodeRole]
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
                roles=sorted(roles_au(habilitations, a_la_date), key=str),
                dossiers=None if dossiers is None else sorted(dossiers),
                habilitations=habilitations_actives(habilitations, a_la_date),
            )
        )
    return lignes


class DemandeInvitation(BaseModel):
    courriel: str = Field(min_length=3)
    nom: str = Field(min_length=1)
    prenom: str = Field(min_length=1)
    #: 18 octobre 2026 : un rôle livré, ou un rôle créé par le cabinet.
    role: CodeRole
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
    exiger_un_geste_fort(acces, Permission.GERER_COMPTES)
    if str(demande.role) not in matrice_en_vigueur():
        raise HTTPException(status_code=422, detail=f"le cabinet n'a pas de rôle {demande.role}.")
    boutique = atelier()
    try:
        compte, jeton, secret = inviter_collaborateur(
            par=acces,
            identifiant_compte=nouvel_identifiant("C"),
            identifiant_habilitation=nouvel_identifiant("H"),
            identifiant_jeton=nouvel_identifiant("J"),
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
            "prenom": compte.appellation,
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
    exiger_un_geste_fort(acces, Permission.GERER_COMPTES)
    boutique = atelier()
    try:
        compte = suspendre_compte(
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
    except SuspensionRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    # ⚠️ Le titulaire est prévenu (pas 69). Le gabarit existait, « au catalogue, pas encore
    # appelé » : un collaborateur dont les sessions tombaient ne savait pas pourquoi. Le
    # motif ne part pas dans le courriel ; il est au journal, et le message renvoie au cabinet.
    boutique.notifications.envoyer(
        "compte.suspendu", destinataire=compte.courriel, contexte={"prenom": compte.appellation}
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
    exiger_un_geste_fort(acces, Permission.GERER_COMPTES)
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
        "compte.retabli", destinataire=compte.courriel, contexte={"prenom": compte.appellation}
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
            identifiant_successeur=nouvel_identifiant("H"),
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
            successeur_de=nouvel_identifiant("H"),
            successeur_vers=nouvel_identifiant("H"),
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
    exiger_un_geste_fort(acces, Permission.GERER_COMPTES)
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
