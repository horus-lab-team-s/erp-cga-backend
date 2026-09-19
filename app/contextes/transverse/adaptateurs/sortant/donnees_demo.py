"""L'équipe du cabinet et les accès adhérents, en démonstration.

─────────────────────────────────────────────────────────────────────────────────
⚠️ MOTS DE PASSE DE DÉMONSTRATION — AUCUN DE CES COMPTES N'EST UTILISABLE EN PROD

Tous partagent le même mot de passe, écrit en clair ci-dessous. C'est acceptable
parce que ces comptes n'existent que dans un jeu de démonstration monté en
mémoire, et qu'ils disparaissent au redémarrage. C'est **inacceptable** dès qu'une
base persiste : le jour où la persistance PostgreSQL arrive, ce module doit cesser
d'être chargé hors développement, et l'amorçage doit passer par des invitations
réelles.

L'empreinte est dérivée **une seule fois** et partagée. Argon2 coûte une
quarantaine de millisecondes par dérivation ; onze comptes en feraient une demi-
seconde à chaque import, payée par chaque exécution de tests. Le partage est ici
sans conséquence puisque le mot de passe est le même.

LES NIU SONT RECOPIÉS, ET C'EST STRUCTUREL

K appartient au socle : il ne lit aucun contexte métier, portefeuille compris. Il
ne peut donc pas importer `PORTEFEUILLE_DEMO`, et les NIU figurent ici en
littéraux.

Ce n'est pas une duplication tolérée faute de mieux, c'est la conséquence directe
du cloisonnement : K sait qu'un compte a le droit d'ouvrir `M081234567890P`, il
ne sait pas ce que ce dossier contient. La cohérence entre les deux jeux est
vérifiée par un test — `tests/test_transverse.py`, qui lui a le droit d'importer
les deux — plutôt que par une dépendance qui créerait un cycle.

CE QUE CE JEU MET EN SCÈNE

* **Deux comptables au portefeuille disjoint.** Le premier a trois dossiers, le
  second en a deux. Aucun des deux ne voit ceux de l'autre : c'est le
  cloisonnement par portefeuille, et il se démontre en deux requêtes.
* **Un dossier orphelin** — la clinique n'est affectée à personne. C'est l'anomalie
  que l'écran d'administration doit faire remonter, et elle existe pour cela.
* **Une habilitation fermée** : un comptable parti en avril, dont la ligne demeure.
  Résoudre ses droits au 1er mars et au 1er juin donne deux réponses différentes,
  et c'est toute la raison d'être de l'habilitation datée.
* **Un adhérent non activé** — compte créé, lien parti, mot de passe jamais défini.
* **Un inspecteur** en mission sur un seul dossier, en lecture et avis seulement.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime

from app.contextes.transverse.adaptateurs.sortant.depots_memoire import (
    LOCATAIRE_PAR_DEFAUT,
    DepotComptesMemoire,
    DepotHabilitationsMemoire,
    DepotJetonsMemoire,
    DepotSessionsMemoire,
    JournalAuditMemoire,
)
from app.contextes.transverse.adaptateurs.sortant.empreinte_argon2 import (
    ServiceEmpreinteArgon2,
)
from app.contextes.transverse.domaine.habilitations import Habilitation, MotifHabilitation
from app.contextes.transverse.domaine.identites import Compte, EtatCompte
from app.contextes.transverse.domaine.roles import Role

__all__ = [
    "MOT_DE_PASSE_DEMO",
    "NIU_DEMO",
    "COMPTES_DEMO",
    "HABILITATIONS_DEMO",
    "depots_demo",
]

#: ⚠️ Voir l'en-tête. Vingt-deux caractères, conforme à la politique de
#: `activation.py` — la démonstration n'a aucune raison de montrer l'exemple d'un
#: mot de passe que le système refuserait.
MOT_DE_PASSE_DEMO = "cabinet brcg douala 2026"

#: Les six dossiers du portefeuille de démonstration du contexte B. Recopiés —
#: voir l'en-tête.
NIU_DEMO: dict[str, str] = {
    "BATIMENT": "M081234567890P",
    "TCHOUMBA": "P019876543210K",
    "COLOMBE": "M071122334455J",
    "AGRO": "M065544332211L",
    "NGUEMA": "P027788990011M",
    "CLINIQUE": "M093344556677N",
}

_CREATION = datetime(2026, 1, 5, 8, 0)

#: Dérivée une seule fois — voir l'en-tête. Le service est construit avec les
#: paramètres de production : la démonstration ne doit pas donner de faux
#: renseignement sur le coût réel d'une connexion.
_EMPREINTE_DEMO = ServiceEmpreinteArgon2().deriver(MOT_DE_PASSE_DEMO)


def _compte(
    identifiant: str,
    prenom: str,
    nom: str,
    courriel: str,
    *,
    telephone: str | None = None,
    active: bool = True,
) -> Compte:
    return Compte(
        identifiant=identifiant,
        courriel=courriel,
        nom=nom,
        prenom=prenom,
        locataire=LOCATAIRE_PAR_DEFAUT,
        telephone=telephone,
        empreinte_mot_de_passe=_EMPREINTE_DEMO if active else None,
        etat=EtatCompte.ACTIF if active else EtatCompte.EN_ATTENTE_ACTIVATION,
        cree_le=_CREATION,
    )


# ── L'équipe du cabinet ──────────────────────────────────────────────────────

COMPTES_DEMO: list[Compte] = [
    _compte("C-001", "Bernadette", "MBALLA", "b.mballa@cga-brcg.cm", telephone="+237699112233"),
    _compte("C-002", "Serge", "ONANA", "s.onana@cga-brcg.cm", telephone="+237677445566"),
    _compte("C-003", "Aïcha", "BOUBA", "a.bouba@cga-brcg.cm", telephone="+237690778899"),
    _compte("C-004", "Léonard", "FOTSO", "l.fotso@cga-brcg.cm"),
    _compte("C-005", "Christelle", "NDONGO", "c.ndongo@cga-brcg.cm"),
    _compte("C-006", "Roger", "EBOLO", "r.ebolo@cga-brcg.cm"),
    _compte("C-007", "Patricia", "MOUKOURI", "p.moukouri@cga-brcg.cm"),
    # Parti en avril. Le compte est suspendu, l'habilitation fermée, et l'un
    # comme l'autre demeurent : les écritures qu'il a validées portent son
    # identifiant, et le journal d'audit y renvoie.
    _compte("C-008", "Alain", "TCHINDA", "a.tchinda@cga-brcg.cm").suspendre(),
    # ── Les adhérents ────────────────────────────────────────────────────────
    _compte("A-001", "Jean-Pierre", "NKOA", "jp.nkoa@batimentplus.cm", telephone="+237655102030"),
    _compte("A-002", "Marie-Claire", "ESSOMBA", "mc.essomba@lacolombe.cm"),
    # A-003 a payé, le lien est parti, le mot de passe n'a jamais été défini.
    # C'est l'état le plus fréquent en production, et celui qu'on oublie de
    # traiter dans les écrans : ni actif, ni inexistant.
    _compte("A-003", "Émile", "TCHOUMBA", "e.tchoumba@tchoumbaetfils.cm", active=False),
    # ── L'externe ────────────────────────────────────────────────────────────
    _compte("I-001", "Georges", "ATANGANA", "g.atangana@inspection.cm"),
]


# ── Qui peut quoi, depuis quand, sur quels dossiers ──────────────────────────


def _hab(
    identifiant: str,
    compte: str,
    role: Role,
    portee: set[str] | None,
    debut: date,
    motif: MotifHabilitation = MotifHabilitation.RECRUTEMENT,
    *,
    fin: date | None = None,
    accordee_par: str = "C-002",
    precision: str | None = None,
) -> Habilitation:
    return Habilitation(
        identifiant=identifiant,
        compte=compte,
        role=role,
        portee=None if portee is None else frozenset(portee),
        debut=debut,
        fin=fin,
        motif=motif,
        accordee_par=accordee_par,
        precision=precision,
    )


HABILITATIONS_DEMO: list[Habilitation] = [
    # La direction et l'administration voient tout le cabinet.
    _hab("H-001", "C-001", Role.DIRECTION, None, date(2019, 1, 2), accordee_par="C-001",
         precision="associée fondatrice"),
    _hab("H-002", "C-002", Role.ADMINISTRATEUR, None, date(2019, 1, 2), accordee_par="C-001"),
    # Le réviseur signe : il lui faut l'ensemble des dossiers.
    _hab("H-003", "C-003", Role.REVISEUR, None, date(2020, 3, 1), accordee_par="C-001"),
    # Deux comptables, deux portefeuilles disjoints. C'est ici que se démontre
    # le cloisonnement : ni l'un ni l'autre ne peut ouvrir les dossiers de son
    # collègue, alors qu'ils détiennent exactement les mêmes permissions.
    _hab(
        "H-004", "C-004", Role.COMPTABLE,
        {NIU_DEMO["BATIMENT"], NIU_DEMO["COLOMBE"], NIU_DEMO["AGRO"]},
        date(2021, 9, 1), precision="portefeuille industrie et commerce",
    ),
    _hab(
        "H-005", "C-005", Role.COMPTABLE,
        {NIU_DEMO["TCHOUMBA"], NIU_DEMO["NGUEMA"]},
        date(2023, 4, 3), precision="portefeuille services et professions libérales",
    ),
    # ⚠️ La clinique n'apparaît dans aucune portée de comptable : dossier
    # orphelin, délibérément — voir l'en-tête.
    _hab("H-006", "C-006", Role.FISCALISTE, None, date(2022, 1, 10), accordee_par="C-001"),
    _hab(
        "H-007", "C-007", Role.CHARGE_CLIENTELE,
        set(NIU_DEMO.values()), date(2024, 2, 1),
        precision="suit l'ensemble des adhérents",
    ),
    _hab("H-008", "C-007", Role.CHARGE_FORMALITES, None, date(2025, 6, 1),
         motif=MotifHabilitation.CHANGEMENT_DE_POSTE,
         precision="cumule les formalités depuis le départ de A. TCHINDA"),
    # Le comptable parti : habilitation fermée, ligne conservée. Résoudre les
    # droits de C-008 au 1er mars et au 1er juin 2026 donne deux réponses.
    _hab(
        "H-009", "C-008", Role.COMPTABLE,
        {NIU_DEMO["CLINIQUE"]}, date(2020, 5, 4), fin=date(2026, 4, 30),
        motif=MotifHabilitation.DEPART, precision="fermée par C-002 — fin de contrat",
    ),
    # Les adhérents : un dossier chacun, jamais plus.
    _hab("H-010", "A-001", Role.ADHERENT, {NIU_DEMO["BATIMENT"]}, date(2022, 6, 1),
         motif=MotifHabilitation.SOUSCRIPTION, accordee_par="systeme"),
    _hab("H-011", "A-002", Role.ADHERENT, {NIU_DEMO["COLOMBE"]}, date(2021, 1, 1),
         motif=MotifHabilitation.SOUSCRIPTION, accordee_par="systeme"),
    _hab("H-012", "A-003", Role.ADHERENT, {NIU_DEMO["TCHOUMBA"]}, date(2023, 2, 1),
         motif=MotifHabilitation.SOUSCRIPTION, accordee_par="systeme"),
    # L'inspecteur assistant : un dossier, une mission datée, lecture et avis.
    _hab(
        "I-H-001", "I-001", Role.INSPECTEUR, {NIU_DEMO["AGRO"]},
        date(2026, 6, 1), motif=MotifHabilitation.MISSION_CONTROLE,
        accordee_par="C-001", precision="lettre de mission DGI du 28 mai 2026",
    ),
]


def depots_demo() -> tuple[
    DepotComptesMemoire,
    DepotHabilitationsMemoire,
    DepotJetonsMemoire,
    DepotSessionsMemoire,
    JournalAuditMemoire,
]:
    """Les cinq dépôts, garnis.

    Le journal d'audit est rendu **vierge**, et c'est délibéré : fabriquer un
    historique d'audit de démonstration reviendrait à produire une chaîne de
    hachage qui n'atteste de rien. Le journal se remplit à la première action
    réelle, ce qui est exactement ce qu'il doit faire.
    """
    comptes = DepotComptesMemoire()
    for compte in COMPTES_DEMO:
        comptes.enregistrer(compte)

    habilitations = DepotHabilitationsMemoire()
    for habilitation in HABILITATIONS_DEMO:
        habilitations.enregistrer(habilitation)

    return (
        comptes,
        habilitations,
        DepotJetonsMemoire(),
        DepotSessionsMemoire(),
        JournalAuditMemoire(),
    )
