"""Contrats de domaine du contexte K · Transverse.

**Deux surfaces publiques, pas une.**

* `contrats.py` — ce module — n'expose que des **entités pures** : des types,
  aucun service, aucune entrée-sortie. C'est la seule chose qu'une couche
  `domaine` d'un autre contexte a le droit d'importer.
* `api.py` expose en plus les **cas d'usage**. Réservé aux couches `application`
  et `adaptateurs`.

─────────────────────────────────────────────────────────────────────────────────
K APPARTIENT AU SOCLE

Comme le référentiel normatif, ce contexte est **lisible par les onze autres et
n'en lit aucun**. C'est ce qui rend le graphe acyclique : si K lisait le
portefeuille pour connaître une entreprise, et que le portefeuille lisait K pour
savoir qui a le droit de la modifier, les deux n'en feraient plus qu'un.

La conséquence est visible dans les signatures : un dossier est ici un **NIU**,
une chaîne. K sait dire « ce compte a le droit d'ouvrir M081234567890P » ; il ne
sait pas ce que ce dossier contient, et n'a pas à le savoir.

CE QUE LES AUTRES CONTEXTES VIENDRONT Y CHERCHER

`Permission` et `Role`, pour déclarer ce que leurs propres actes exigent. Le
reste — comptes, sessions, jetons — ne les regarde pas : ils reçoivent un `Acces`
déjà résolu, exposé par `api.py`.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from app.contextes.transverse.domaine.audit import (
    GENESE,
    EntreeAudit,
    JournalAltere,
    verifier_chaine,
)
from app.contextes.transverse.domaine.habilitations import (
    Habilitation,
    MotifHabilitation,
    dossiers_accessibles,
    habilitations_actives,
    permissions_au,
    roles_au,
)
from app.contextes.transverse.domaine.identites import (
    Compte,
    CompteInactif,
    EtatCompte,
)
from app.contextes.transverse.domaine.jetons import (
    Jeton,
    JetonInvalide,
    TypeJeton,
    empreinte_de,
)
from app.contextes.transverse.domaine.roles import (
    EXIGE_MFA,
    EXIGE_MOTIF,
    PERMISSIONS_PAR_ROLE,
    ROLES_A_PORTEE_OBLIGATOIRE,
    ROLES_INTERNES,
    Permission,
    Role,
    permissions_de,
)
from app.contextes.transverse.domaine.second_facteur import (
    DUREE_RENFORCEMENT,
    CodeInvalide,
    code_attendu,
    engendrer_secret_totp,
    uri_provisionnement,
    verifier_code,
)
from app.contextes.transverse.domaine.sessions import (
    DUREE_SESSION,
    MotifRevocation,
    Session,
    SessionInvalide,
)
from app.contextes.transverse.domaine.teledeclaration import (
    AccuseReception,
    DepotRefuseParLePortail,
    DocumentATransmettre,
    FormatTransmission,
    ModeDepot,
    Portail,
    empreinte_document,
    reference_de_depot,
)

__all__ = [
    "DUREE_RENFORCEMENT",
    "DUREE_SESSION",
    "EXIGE_MFA",
    "EXIGE_MOTIF",
    "GENESE",
    "PERMISSIONS_PAR_ROLE",
    "ROLES_A_PORTEE_OBLIGATOIRE",
    "ROLES_INTERNES",
    "reference_de_depot",
    "AccuseReception",
    "CodeInvalide",
    "Compte",
    "CompteInactif",
    "DepotRefuseParLePortail",
    "DocumentATransmettre",
    "EntreeAudit",
    "EtatCompte",
    "FormatTransmission",
    "Habilitation",
    "Jeton",
    "JetonInvalide",
    "JournalAltere",
    "ModeDepot",
    "MotifHabilitation",
    "MotifRevocation",
    "Permission",
    "Portail",
    "Role",
    "Session",
    "SessionInvalide",
    "TypeJeton",
    "code_attendu",
    "dossiers_accessibles",
    "empreinte_de",
    "empreinte_document",
    "engendrer_secret_totp",
    "habilitations_actives",
    "permissions_au",
    "permissions_de",
    "roles_au",
    "uri_provisionnement",
    "verifier_chaine",
    "verifier_code",
]
