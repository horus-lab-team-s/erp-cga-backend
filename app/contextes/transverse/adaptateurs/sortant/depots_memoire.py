"""Réalisations en mémoire des cinq dépôts du contexte K.

Même raisonnement que pour les huit autres dépôts du système : réaliser un port
une première fois révèle ce qui manque à l'interface, avant qu'un schéma ne le
fige. Voir l'en-tête de `app/partage/depot_memoire.py`.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CES RÉALISATIONS TIENNENT

* Le cloisonnement par locataire, porté par l'instance — aucun port ne prend de
  paramètre `locataire`, et l'entrepôt n'a aucun moyen d'en voir un autre.
* L'unicité du courriel, contrôlée à l'enregistrement.
* L'ajout au journal d'audit sous verrou, donc chaîné correctement tant que le
  processus est unique.
* L'interdiction de modifier une entrée d'audit déjà écrite.

CE QU'ELLES NE TIENNENT PAS, ET QU'IL FAUT SAVOIR AVANT DE S'EN SERVIR

* **Rien n'est durable.** Un redémarrage vide tout, sessions comprises.
* **L'atomicité du journal ne vaut que dans un processus.** `threading.Lock`
  ordonne les fils d'exécution d'une même instance ; deux instances derrière un
  répartiteur de charge écriraient deux entrées de même rang chaînées au même
  prédécesseur, et `verifier_chaine` signalerait une altération qui n'en serait
  pas une. Seule une contrainte d'unicité sur le rang, en base, le garantira —
  c'est écrit dans le port, et ce n'est pas tenu ici.
* **Aucune expiration automatique.** Les sessions périmées restent en mémoire ;
  elles sont refusées à la lecture, mais rien ne les balaie.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.contextes.transverse.domaine.audit import EntreeAudit
from app.contextes.transverse.domaine.habilitations import Habilitation
from app.contextes.transverse.domaine.identites import Compte
from app.contextes.transverse.domaine.jetons import Jeton
from app.contextes.transverse.domaine.mandats import Mandat
from app.contextes.transverse.domaine.sessions import Session
from app.partage.depot_memoire import EntrepotMemoire
from app.partage.locataire import sous_mandat

__all__ = [
    "CompteIntrouvable",
    "CourrielEnDouble",
    "DepotComptesMemoire",
    "DepotHabilitationsMemoire",
    "DepotJetonsMemoire",
    "DepotSessionsMemoire",
    "HabilitationIntrouvable",
    "JournalAuditMemoire",
]

LOCATAIRE_PAR_DEFAUT = "CGA-BRCG"


class CompteIntrouvable(LookupError):
    """Le compte n'existe pas dans ce locataire.

    « Dans ce locataire » n'est pas une précaution de style : le compte peut
    parfaitement exister ailleurs, et c'est précisément ce que le cloisonnement
    doit rendre indiscernable de l'absence.
    """


class HabilitationIntrouvable(LookupError):
    pass


class CourrielEnDouble(ValueError):
    """Deux comptes ne peuvent pas porter la même adresse — voir `DepotComptes`."""


class DepotComptesMemoire:
    """Réalisation de `DepotComptes`."""

    def __init__(self, locataire: str = LOCATAIRE_PAR_DEFAUT) -> None:
        self.locataire = locataire
        self._entrepot: EntrepotMemoire[Compte] = EntrepotMemoire(
            locataire, cle=lambda c: c.identifiant
        )

    def lire(self, identifiant: str) -> Compte:
        compte = self._entrepot.prendre(identifiant)
        if compte is None:
            raise CompteIntrouvable(
                f"aucun compte {identifiant} chez le locataire {self.locataire}"
            )
        return compte

    def par_courriel(self, courriel: str) -> Compte | None:
        cible = courriel.strip().lower()
        return next((c for c in self._entrepot if c.courriel == cible), None)

    def enregistrer(self, compte: Compte) -> None:
        if compte.locataire != self.locataire:
            raise ValueError(
                f"compte {compte.identifiant} du locataire {compte.locataire} enregistré "
                f"dans le dépôt de {self.locataire}. Le cloisonnement est porté par "
                "l'instance : une écriture croisée est un défaut, pas un cas limite."
            )
        homonyme = self.par_courriel(compte.courriel)
        if homonyme is not None and homonyme.identifiant != compte.identifiant:
            raise CourrielEnDouble(
                f"l'adresse {compte.courriel} est déjà celle du compte "
                f"{homonyme.identifiant}"
            )
        self._entrepot.poser(compte)

    def lister(self) -> list[Compte]:
        return sorted(self._entrepot.tout(), key=lambda c: (c.nom, c.prenom))


class DepotHabilitationsMemoire:
    """Réalisation de `DepotHabilitations`.

    ⚠️ `pour_compte` rend **toutes** les habilitations, fermées comprises. Un
    dépôt qui filtrerait les actives priverait l'appelant de l'historique, seul
    capable de répondre à « qui était habilité le 12 mars ».
    """

    def __init__(self, locataire: str = LOCATAIRE_PAR_DEFAUT) -> None:
        self.locataire = locataire
        self._entrepot: EntrepotMemoire[Habilitation] = EntrepotMemoire(
            locataire, cle=lambda h: h.identifiant
        )

    def lire(self, identifiant: str) -> Habilitation:
        habilitation = self._entrepot.prendre(identifiant)
        if habilitation is None:
            raise HabilitationIntrouvable(f"aucune habilitation {identifiant}")
        return habilitation

    def pour_compte(self, compte: str) -> list[Habilitation]:
        return sorted(
            self._entrepot.filtrer(lambda h: h.compte == compte),
            key=lambda h: (h.debut, h.role.value),
        )

    def pour_dossier(self, niu: str) -> list[Habilitation]:
        """Inclut les habilitations transverses : elles couvrent ce dossier aussi,
        et un écran d'affectation qui les omettrait laisserait croire que personne
        n'y a accès."""
        return sorted(
            self._entrepot.filtrer(lambda h: h.couvre_dossier(niu)),
            key=lambda h: (h.compte, h.debut),
        )

    def enregistrer(self, habilitation: Habilitation) -> None:
        self._entrepot.poser(habilitation)

    def lister(self) -> list[Habilitation]:
        return sorted(self._entrepot.tout(), key=lambda h: (h.compte, h.debut))


class DepotJetonsMemoire:
    """Réalisation de `DepotJetons`. Indexé par empreinte, jamais par secret."""

    def __init__(self, locataire: str = LOCATAIRE_PAR_DEFAUT) -> None:
        self.locataire = locataire
        self._entrepot: EntrepotMemoire[Jeton] = EntrepotMemoire(
            locataire, cle=lambda j: j.identifiant
        )

    def par_empreinte(self, empreinte: str) -> Jeton | None:
        return next((j for j in self._entrepot if j.empreinte == empreinte), None)

    def pour_compte(self, compte: str) -> list[Jeton]:
        return sorted(
            self._entrepot.filtrer(lambda j: j.compte == compte),
            key=lambda j: j.emis_le,
            reverse=True,
        )

    def enregistrer(self, jeton: Jeton) -> None:
        self._entrepot.poser(jeton)


class DepotSessionsMemoire:
    """Réalisation de `DepotSessions`."""

    def __init__(self, locataire: str = LOCATAIRE_PAR_DEFAUT) -> None:
        self.locataire = locataire
        self._entrepot: EntrepotMemoire[Session] = EntrepotMemoire(
            locataire, cle=lambda s: s.identifiant
        )

    def lire(self, identifiant: str) -> Session | None:
        return self._entrepot.prendre(identifiant)

    def pour_compte(self, compte: str) -> list[Session]:
        return sorted(
            self._entrepot.filtrer(lambda s: s.compte == compte),
            key=lambda s: s.ouverte_le,
            reverse=True,
        )

    def enregistrer(self, session: Session) -> None:
        self._entrepot.poser(session)


class DepotMandatsMemoire:
    """Réalisation de `DepotMandats`. Cloisonné sur le **mandant**.

    ⚠️ L'entrepôt en mémoire porte déjà le locataire ; on vérifie quand même le mandant à
    l'écriture, comme le fait la réalisation SQL. Deux réalisations d'un même port qui
    refusent des choses différentes finissent par cacher un défaut dans celle qui refuse
    le moins, et c'est toujours celle des tests.
    """

    def __init__(self, locataire: str = LOCATAIRE_PAR_DEFAUT) -> None:
        self.locataire = locataire
        self._entrepot: EntrepotMemoire[Mandat] = EntrepotMemoire(
            locataire, cle=lambda m: m.identifiant
        )

    def lire(self, identifiant: str) -> Mandat | None:
        return self._entrepot.prendre(identifiant)

    def tous(self) -> list[Mandat]:
        return sorted(
            self._entrepot.filtrer(lambda _: True),
            key=lambda m: (m.debut, m.identifiant),
            reverse=True,
        )

    def enregistrer(self, mandat: Mandat) -> None:
        if mandat.mandant != self.locataire:
            raise ValueError(
                f"mandat {mandat.identifiant} du mandant {mandat.mandant} enregistré dans "
                f"le dépôt de {self.locataire}. Un locataire n'accorde de mandat que sur "
                "ses propres données."
            )
        self._entrepot.poser(mandat)


class JournalAuditMemoire:
    """Réalisation de `JournalAudit`. Append-only, chaîné, sous verrou.

    Le verrou couvre la lecture de la tête **et** l'écriture. Le relâcher entre
    les deux suffirait à produire deux entrées de même rang — voir l'en-tête du
    module pour la limite de cette garantie.
    """

    def __init__(self, locataire: str = LOCATAIRE_PAR_DEFAUT) -> None:
        self.locataire = locataire
        self._entrees: list[EntreeAudit] = []
        self._verrou = threading.Lock()

    def ajouter(
        self,
        *,
        horodatage: datetime,
        acteur: str,
        action: str,
        objet_type: str,
        objet_id: str | None = None,
        avant: dict[str, Any] | None = None,
        apres: dict[str, Any] | None = None,
        motif: str | None = None,
        adresse_ip: str | None = None,
    ) -> EntreeAudit:
        with self._verrou:
            entree = EntreeAudit.poser(
                precedente=self._entrees[-1] if self._entrees else None,
                horodatage=horodatage,
                acteur=acteur,
                locataire=self.locataire,
                # ⚠️ Lu dans le contexte, jamais reçu en argument. Dix-huit appelants qui
                # devraient le passer en oublieraient un, et l'entrée la plus intéressante
                # d'un litige serait précisément celle qui ne dit pas au nom de qui elle a
                # été écrite. Même contrat que le locataire.
                mandat=sous_mandat(),
                action=action,
                objet_type=objet_type,
                objet_id=objet_id,
                avant=avant,
                apres=apres,
                motif=motif,
                adresse_ip=adresse_ip,
            )
            self._entrees.append(entree)
            return entree

    def tete(self) -> EntreeAudit | None:
        return self._entrees[-1] if self._entrees else None

    def lister(
        self,
        *,
        acteur: str | None = None,
        objet_type: str | None = None,
        objet_id: str | None = None,
        depuis: datetime | None = None,
    ) -> list[EntreeAudit]:
        """Dans l'ordre des rangs, jamais trié autrement — c'est la séquence qui
        porte la garantie, et un journal rendu par date ne se vérifie pas."""
        return [
            e
            for e in self._entrees
            if (acteur is None or e.acteur == acteur)
            and (objet_type is None or e.objet_type == objet_type)
            and (objet_id is None or e.objet_id == objet_id)
            and (depuis is None or e.horodatage >= depuis)
        ]

    def __len__(self) -> int:
        return len(self._entrees)
