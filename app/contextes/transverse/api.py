"""Surface publique du contexte K · Transverse — entités **et** cas d'usage.

Réservée aux couches `application` et `adaptateurs` des autres contextes. Une
couche `domaine` n'a droit qu'à `contrats.py` : importer cette surface depuis une
entité tirerait la couche application par la bande, et le cercle 1 dépendrait du
cercle 2.

─────────────────────────────────────────────────────────────────────────────────
CE QUE LES AUTRES CONTEXTES UTILISERONT, EN PRATIQUE

Presque uniquement deux choses : `Acces`, déjà résolu, et `Permission`, pour dire
ce que leur acte exige.

    def valider(ecriture, *, par: Acces) -> EcritureComptable:
        par.exiger(Permission.VALIDER_ECRITURE, dossier=ecriture.entreprise)

Les comptes, les sessions, les jetons et les dépôts restent l'affaire de K. Un
contexte métier qui lirait `DepotComptes` pour retrouver le nom d'un valideur
ferait entrer l'identité dans la comptabilité, et l'on ne saurait plus lequel des
deux contextes détient la vérité sur les personnes.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from app.contextes.transverse.adaptateurs.entrant.dependances import (
    NOM_TEMOIN,
    AccesRequis,
    Atelier,
    AtelierMemoire,
    AtelierSql,
    IntergicielUniteDeTravail,
    acces_courant,
    acces_optionnel,
    atelier,
    exiger,
    exiger_dossier,
    garnir_le_repertoire,
    reinitialiser_atelier,
    repertoire_des_tenants,
    restreindre,
    service_de_notification,
    session_de_travail,
    unite_de_travail,
)
from app.contextes.transverse.adaptateurs.entrant.routes_orchestration import (
    boite_d_envoi,
)
from app.contextes.transverse.adaptateurs.sortant.depots_memoire import (
    LOCATAIRE_PAR_DEFAUT,
    CompteIntrouvable,
    CourrielEnDouble,
    DepotComptesMemoire,
    DepotHabilitationsMemoire,
    DepotJetonsMemoire,
    DepotSessionsMemoire,
    HabilitationIntrouvable,
    JournalAuditMemoire,
)
from app.contextes.transverse.adaptateurs.sortant.donnees_demo import (
    COMPTES_DEMO,
    HABILITATIONS_DEMO,
    MOT_DE_PASSE_DEMO,
    NIU_DEMO,
    depots_demo,
)
from app.contextes.transverse.adaptateurs.sortant.empreinte_argon2 import (
    ServiceEmpreinteArgon2,
)
from app.contextes.transverse.adaptateurs.sortant.lectures_des_notifications import (
    depot_des_lectures,
)
from app.contextes.transverse.adaptateurs.sortant.notifications_memoire import (
    CODES_MESSAGES,
    MessageRetenu,
    ServiceNotificationMemoire,
)
from app.contextes.transverse.adaptateurs.sortant.portail_manuel import (
    AccuseIncoherent,
    DepotManuelRequis,
    PortailManuel,
)
from app.contextes.transverse.application.activation import (
    LONGUEUR_MINIMALE,
    MotDePasseRefuse,
    controler_mot_de_passe,
    definir_mot_de_passe,
    emettre_jeton,
)
from app.contextes.transverse.application.administration import (
    SYSTEME,
    CourrielDejaPris,
    FermetureRefusee,
    ReaffectationRefusee,
    ReinitialisationRefusee,
    RetablissementRefuse,
    SuspensionRefusee,
    accorder,
    affecter_dossier,
    fermer_habilitation,
    inviter_collaborateur,
    ouvrir_acces_adherent,
    reaffecter_dossier,
    reinitialiser_second_facteur,
    retablir_compte,
    suspendre_compte,
)
from app.contextes.transverse.application.authentification import (
    DUREE_VERROU,
    IdentifiantsRefuses,
    PreuveDeBoiteRequise,
    SecondFacteurAbsent,
    SecondFacteurDejaActif,
    confirmer_enrolement,
    enroler_second_facteur,
    fermer_session,
    ouvrir_session,
    renforcer_session,
    revoquer_les_sessions,
    verifier_session,
)
from app.contextes.transverse.application.autorisation import (
    Acces,
    AccesRefuse,
    MotifRequis,
    SecondFacteurRequis,
    resoudre_acces,
)
from app.contextes.transverse.contrats import (
    DUREE_RENFORCEMENT,
    DUREE_SESSION,
    EXIGE_MFA,
    EXIGE_MOTIF,
    GENESE,
    PERMISSIONS_PAR_ROLE,
    ROLES_A_PORTEE_OBLIGATOIRE,
    ROLES_INTERNES,
    AccuseReception,
    CodeInvalide,
    Compte,
    CompteInactif,
    DepotRefuseParLePortail,
    DocumentATransmettre,
    EntreeAudit,
    EtatCompte,
    FormatTransmission,
    Habilitation,
    Jeton,
    JetonInvalide,
    JournalAltere,
    ModeDepot,
    MotifHabilitation,
    MotifRevocation,
    Permission,
    Portail,
    Role,
    Session,
    SessionInvalide,
    TypeJeton,
    code_attendu,
    dossiers_accessibles,
    empreinte_de,
    empreinte_document,
    engendrer_secret_totp,
    habilitations_actives,
    permissions_au,
    permissions_de,
    reference_de_depot,
    roles_au,
    uri_provisionnement,
    verifier_chaine,
    verifier_code,
)
from app.contextes.transverse.domaine.ports import (
    DepotComptes,
    DepotHabilitations,
    DepotJetons,
    DepotSessions,
    JournalAudit,
    PortailDeclaratif,
    ServiceEmpreinte,
    ServiceNotification,
)

from app.contextes.transverse.domaine.acces_adherent import (
    ReglagesDuRenvoi,
    RenvoiRefuse,
    renvois_du_jour,
    type_de_lien,
)

__all__ = [
    "FermetureRefusee",
    "RetablissementRefuse",
    "SuspensionRefusee",
    "PreuveDeBoiteRequise",
    "confirmer_enrolement",
    "reinitialiser_second_facteur",
    "SecondFacteurDejaActif",
    "ReinitialisationRefusee",
    "garnir_le_repertoire",
    "CODES_MESSAGES",
    "DUREE_RENFORCEMENT",
    "NOM_TEMOIN",
    "COMPTES_DEMO",
    "DUREE_SESSION",
    "DUREE_VERROU",
    "EXIGE_MFA",
    "EXIGE_MOTIF",
    "GENESE",
    "HABILITATIONS_DEMO",
    "LOCATAIRE_PAR_DEFAUT",
    "LONGUEUR_MINIMALE",
    "MOT_DE_PASSE_DEMO",
    "NIU_DEMO",
    "PERMISSIONS_PAR_ROLE",
    "ROLES_A_PORTEE_OBLIGATOIRE",
    "ROLES_INTERNES",
    "SYSTEME",
    "Acces",
    "AccesRefuse",
    "AccesRequis",
    "AccuseIncoherent",
    "reference_de_depot",
    "AccuseReception",
    "Atelier",
    "AtelierMemoire",
    "AtelierSql",
    "CodeInvalide",
    "Compte",
    "CompteInactif",
    "CompteIntrouvable",
    "CourrielDejaPris",
    "CourrielEnDouble",
    "DepotComptes",
    "DepotComptesMemoire",
    "DepotHabilitations",
    "DepotHabilitationsMemoire",
    "DepotJetons",
    "DepotJetonsMemoire",
    "DepotManuelRequis",
    "DepotRefuseParLePortail",
    "DepotSessions",
    "DepotSessionsMemoire",
    "DocumentATransmettre",
    "EntreeAudit",
    "EtatCompte",
    "FormatTransmission",
    "Habilitation",
    "HabilitationIntrouvable",
    "IdentifiantsRefuses",
    "IntergicielUniteDeTravail",
    "Jeton",
    "JetonInvalide",
    "JournalAltere",
    "JournalAudit",
    "JournalAuditMemoire",
    "MessageRetenu",
    "ModeDepot",
    "MotDePasseRefuse",
    "MotifHabilitation",
    "MotifRequis",
    "MotifRevocation",
    "Permission",
    "Portail",
    "PortailDeclaratif",
    "PortailManuel",
    "Role",
    "SecondFacteurAbsent",
    "SecondFacteurRequis",
    "ServiceEmpreinte",
    "ServiceEmpreinteArgon2",
    "ServiceNotification",
    "ServiceNotificationMemoire",
    "Session",
    "SessionInvalide",
    "TypeJeton",
    "acces_courant",
    "acces_optionnel",
    "accorder",
    "code_attendu",
    "ReaffectationRefusee",
    "affecter_dossier",
    "reaffecter_dossier",
    "atelier",
    "controler_mot_de_passe",
    "definir_mot_de_passe",
    "depots_demo",
    "dossiers_accessibles",
    "emettre_jeton",
    "empreinte_de",
    "empreinte_document",
    "engendrer_secret_totp",
    "enroler_second_facteur",
    "exiger",
    "exiger_dossier",
    "fermer_habilitation",
    "fermer_session",
    "habilitations_actives",
    # Pas 111 : savoir si l'adhérent a lu la relance qui lui a été envoyée.
    "depot_des_lectures",
    "inviter_collaborateur",
    "ouvrir_acces_adherent",
    "ouvrir_session",
    "permissions_au",
    "permissions_de",
    "reinitialiser_atelier",
    "renforcer_session",
    "resoudre_acces",
    "boite_d_envoi",
    "repertoire_des_tenants",
    "restreindre",
    "service_de_notification",
    "retablir_compte",
    "session_de_travail",
    "revoquer_les_sessions",
    "roles_au",
    "suspendre_compte",
    "unite_de_travail",
    "uri_provisionnement",
    "verifier_chaine",
    "verifier_code",
    "verifier_session",
    # Rendre l'accès à un adhérent, par son chargé de clientèle (pas 116)
    "ReglagesDuRenvoi",
    "RenvoiRefuse",
    "reglages_du_renvoi",
    "renvois_du_jour",
    "type_de_lien",
]


def reglages_du_renvoi() -> ReglagesDuRenvoi:
    """Les réglages du renvoi de lien, relus au référentiel à chaque geste."""
    from app.contextes.transverse.adaptateurs.sortant.acces_adherent_yaml import (
        charger_les_reglages_du_renvoi,
    )
    from app.infrastructure.config import configuration

    return charger_les_reglages_du_renvoi(configuration().dossier_referentiel)
