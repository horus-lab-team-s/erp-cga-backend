"""Réalisation de `ServiceOuvertureAcces` par le contexte K.

─────────────────────────────────────────────────────────────────────────────────
C'EST ICI, ET NULLE PART AILLEURS, QUE M TOUCHE À L'IDENTITÉ

Un seul fichier du contexte M appelle le contexte K. Le reste — les entités, les
cas d'usage — ne connaît que le port. Le jour où un cabinet client délègue son
identité à un annuaire externe, c'est ce fichier qui change.

CE QUE `ouvrir_acces_adherent` GARANTIT, ET QUI REND CET APPEL SÛR

Elle ne prend **aucun `Acces`** : il n'y a pas d'humain à autoriser, l'acte est
déclenché par un encaissement. Ce qui la rend sûre est qu'elle n'a **aucun degré
de liberté** — le rôle est `ADHERENT`, la portée est le seul NIU souscrit, et rien
de tout cela n'est paramétrable depuis ici. Une souscription ne peut pas
fabriquer un réviseur.

LE LIEN EST CONSTRUIT ICI, ET IL PORTE LE SECRET

Le secret n'existe en clair que le temps de cet appel. Il entre dans l'adresse du
lien, part dans le courriel, et n'est conservé nulle part — seule son empreinte
l'est, côté K.

⚠️ Un lien d'activation est un identifiant de session en devenir. Il ne doit
figurer dans **aucun** journal applicatif. La clé `secret` du contexte de
notification est expurgée par le journal d'audit du contexte K ; la clé `lien`,
elle, ne l'est pas, et c'est pourquoi le lien n'est jamais journalisé.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.contextes.souscription.domaine.souscriptions import Souscription
from app.contextes.transverse.api import (
    DepotComptes,
    DepotHabilitations,
    DepotJetons,
    JournalAudit,
    ServiceNotification,
    ouvrir_acces_adherent,
)

__all__ = ["OuvertureAccesTransverse"]


class OuvertureAccesTransverse:
    """Crée le compte adhérent, émet le lien, l'envoie."""

    def __init__(
        self,
        *,
        comptes: DepotComptes,
        habilitations: DepotHabilitations,
        jetons: DepotJetons,
        journal: JournalAudit,
        notifications: ServiceNotification,
        locataire: str,
        adresse_publique: str,
    ) -> None:
        self._comptes = comptes
        self._habilitations = habilitations
        self._jetons = jetons
        self._journal = journal
        self._notifications = notifications
        self._locataire = locataire
        self._adresse = adresse_publique.rstrip("/")

    def ouvrir(self, souscription: Souscription, a_l_instant: datetime) -> str:
        if souscription.niu is None:
            raise ValueError(
                f"souscription {souscription.reference} : aucun dossier à ouvrir. "
                "Cette prestation n'ouvre pas d'accès."
            )

        compte, jeton, secret = ouvrir_acces_adherent(
            identifiant_compte=f"A-{uuid.uuid4().hex[:12]}",
            identifiant_habilitation=f"H-{uuid.uuid4().hex[:12]}",
            identifiant_jeton=f"J-{uuid.uuid4().hex[:12]}",
            courriel=souscription.prospect.courriel,
            nom=souscription.prospect.nom,
            prenom=souscription.prospect.prenom,
            niu=souscription.niu,
            locataire=self._locataire,
            depuis=a_l_instant.date(),
            comptes=self._comptes,
            habilitations=self._habilitations,
            jetons=self._jetons,
            journal=self._journal,
            a_l_instant=a_l_instant,
            telephone=souscription.prospect.telephone,
            reference_souscription=souscription.reference,
        )

        self._notifications.envoyer(
            "compte.activation",
            destinataire=compte.courriel,
            contexte={
                "prenom": compte.prenom,
                "denomination": souscription.prospect.denomination,
                "service": souscription.libelle,
                "lien": f"{self._adresse}/activation?jeton={secret}",
                "expire_le": jeton.expire_le.isoformat(),
                "souscription": souscription.reference,
            },
        )
        return compte.identifiant
