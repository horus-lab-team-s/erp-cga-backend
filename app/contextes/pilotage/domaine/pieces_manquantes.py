"""Ce qui manque au mois d'un dossier, déduit des faits, et la relance qui le demande (pas 111).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LA MAQUETTE DEMANDE

« Parcours comptable », vue F (UC09) : « demander en un message ce qui manque, sur le canal que
l'adhérent lit ». Et deux notes qui décident de tout :

* « L'attente est **déduite**, pas saisie : mouvements bancaires sans pièce, séries habituelles
  du dossier, obligations du mois. »
* « Le message annonce la **conséquence**, “la déclaration sera établie sans elles” : c'est ce
  qui fait revenir les pièces. »

Avant ce pas, le cabinet ne pouvait relancer qu'une demande déjà saisie à la main (pas 74), et
« tracer » une relance faite hors de la plateforme.

POURQUOI DANS LE PILOTAGE

L'attente se lit chez quatre voisins : la collecte (pièces, demandes), la comptabilité (relevés,
mouvements non rapprochés), les obligations (le mois), la conformité (anomalies bloquantes). Le
pilotage est le seul contexte qui les lit tous, et il construit déjà le plan de travail du
comptable (pas 104). Les **gestes**, eux, restent à la collecte : la demande et sa relance y sont
créées, par sa surface publique.

LES SIX ORIGINES D'UNE ATTENTE

| Origine | Ce qui est relevé |
| --- | --- |
| DEMANDE_OUVERTE | une demande déjà émise et pas encore satisfaite |
| RELEVE_BANCAIRE | un journal de banque mouvementé, sans relevé importé couvrant la fin du mois |
| MOUVEMENT_SANS_PIECE | une ligne de relevé ni rapprochée d'une écriture, ni justifiée autrement |
| SERIE_HABITUELLE | un émetteur présent chaque mois sur la période observée, absent ce mois-ci |
| OBLIGATION_DU_MOIS | une obligation du mois dont le référentiel dit qu'elle appelle une pièce |
| ANOMALIE_BLOQUANTE | une pièce que le contrôle interdit de comptabiliser, sans rectificative |

Chaque origine s'active au référentiel (`pilotage/pieces_manquantes.yaml`). Une attente porte un
**code stable** : relancer deux fois la même attente relance la même demande.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "AnomalieBloquante",
    "AttenteDePiece",
    "CanalDeRelance",
    "DemandeObservee",
    "LigneDeReleveOuverte",
    "ModeleDeRelance",
    "OrigineDAttente",
    "PieceObservee",
    "ReglagesDesPiecesManquantes",
    "attentes_du_mois",
    "code_d_attente",
    "nom_du_mois",
    "rendre_la_relance",
]


class OrigineDAttente(StrEnum):
    DEMANDE_OUVERTE = "DEMANDE_OUVERTE"
    RELEVE_BANCAIRE = "RELEVE_BANCAIRE"
    MOUVEMENT_SANS_PIECE = "MOUVEMENT_SANS_PIECE"
    SERIE_HABITUELLE = "SERIE_HABITUELLE"
    OBLIGATION_DU_MOIS = "OBLIGATION_DU_MOIS"
    ANOMALIE_BLOQUANTE = "ANOMALIE_BLOQUANTE"


class CanalDeRelance(StrEnum):
    #: La notification dans l'espace de l'adhérent. Lue ou non, la plateforme le sait.
    APPLICATION = "APPLICATION"
    COURRIEL = "COURRIEL"
    WHATSAPP = "WHATSAPP"


class ModeleDeRelance(BaseModel):
    """Un modèle de message. `{liste}` reçoit les pièces cochées, une par ligne."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    libelle: str = Field(min_length=3, max_length=80)
    introduction: str = Field(min_length=3)
    conclusion: str = Field(min_length=3)

    @model_validator(mode="after")
    def _la_consequence_est_annoncee(self) -> ModeleDeRelance:
        # « C'est ce qui fait revenir les pièces » (maquette, note 3). Un modèle qui tairait la
        # date limite serait une relance polie qu'on laisse attendre.
        if "{date_limite}" not in self.conclusion:
            raise ValueError(
                f"le modèle « {self.code} » n'annonce pas la date limite : sa conclusion doit "
                "contenir {date_limite}, pour dire ce qui arrive sans les pièces."
            )
        return self


class ReglageDeCanal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    canal: CanalDeRelance
    actif: bool = True
    #: Coché d'office dans l'écran.
    par_defaut: bool = False
    #: Un canal inactif dit pourquoi : « indisponible » ferait ouvrir un incident.
    motif: str | None = None

    @model_validator(mode="after")
    def _inactif_dit_pourquoi(self) -> ReglageDeCanal:
        if not self.actif and not self.motif:
            raise ValueError(f"le canal {self.canal.value} est inactif : dire pourquoi (motif).")
        if not self.actif and self.par_defaut:
            raise ValueError(
                f"le canal {self.canal.value} est inactif : il ne peut être coché d'office."
            )
        return self


def _canaux_par_defaut() -> tuple[ReglageDeCanal, ...]:
    return (
        ReglageDeCanal(canal=CanalDeRelance.APPLICATION, par_defaut=True),
        ReglageDeCanal(canal=CanalDeRelance.COURRIEL, par_defaut=True),
        ReglageDeCanal(
            canal=CanalDeRelance.WHATSAPP,
            actif=False,
            motif="Compte de la plateforme d'envoi non ouvert (voir messagerie/canaux.yaml).",
        ),
    )


def _modeles_par_defaut() -> tuple[ModeleDeRelance, ...]:
    return (
        ModeleDeRelance(
            code="amiable-pieces-du-mois",
            libelle="Relance amiable, pièces du mois",
            introduction="Pour terminer votre mois de {mois}, il nous manque encore :",
            conclusion=(
                "Vous pouvez les déposer depuis votre espace, en photo. Sans ces pièces avant le "
                "{date_limite}, votre déclaration sera établie sans elles."
            ),
        ),
    )


class ReglagesDesPiecesManquantes(BaseModel):
    """`Docs/referentiel/pilotage/pieces_manquantes.yaml`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    origines: frozenset[OrigineDAttente] = frozenset(OrigineDAttente)
    #: Série habituelle : un émetteur présent dans au moins `presence_minimum` des
    #: `mois_observes` mois précédents, et absent du mois.
    mois_observes: int = Field(3, ge=1, le=12)
    presence_minimum: int = Field(3, ge=1, le=12)
    #: Les obligations du mois qui appellent une pièce de l'adhérent, et laquelle.
    pieces_par_obligation: dict[str, str] = Field(default_factory=dict)
    #: La date limite annoncée : tant de jours avant l'échéance de l'obligation de référence.
    obligation_de_reference: str = "TVA"
    jours_avant_echeance: int = Field(3, ge=0, le=20)
    #: Quand l'échéance du mois est déjà passée, la date annoncée est reportée à tant de jours
    #: après l'envoi : « avant le 12/08 » écrit le 17 septembre ne ferait revenir aucune pièce.
    delai_si_echeance_passee: int = Field(5, ge=1, le=30)
    #: Le nom du cabinet sous la signature du collaborateur.
    cabinet: str = Field("le cabinet", min_length=2, max_length=120)
    canaux: tuple[ReglageDeCanal, ...] = Field(default_factory=_canaux_par_defaut)
    modeles: tuple[ModeleDeRelance, ...] = Field(default_factory=_modeles_par_defaut, min_length=1)
    source: str = "valeurs par défaut"

    @model_validator(mode="after")
    def _coherents(self) -> ReglagesDesPiecesManquantes:
        if self.presence_minimum > self.mois_observes:
            raise ValueError("presence_minimum ne dépasse pas mois_observes.")
        codes = [m.code for m in self.modeles]
        if len(set(codes)) != len(codes):
            raise ValueError("deux modèles de relance portent le même code.")
        if len({c.canal for c in self.canaux}) != len(self.canaux):
            raise ValueError("un canal de relance est réglé deux fois.")
        return self

    def canal(self, canal: CanalDeRelance | str) -> ReglageDeCanal | None:
        voulu = CanalDeRelance(canal)
        return next((c for c in self.canaux if c.canal is voulu), None)


# ── Ce que la route relève chez les voisins ──────────────────────────────────


class PieceObservee(BaseModel):
    model_config = ConfigDict(frozen=True)

    identifiant: str
    reference: str | None
    emetteur: str | None
    montant: Decimal | None
    #: La date du document, sinon la réception.
    date: date


class DemandeObservee(BaseModel):
    model_config = ConfigDict(frozen=True)

    identifiant: str
    motif: str
    type_attendu: str
    demandee_le: date
    piece_a_rectifier: str | None = None
    #: Pas 112 : la dernière réponse de l'adhérent, rédigée par la collecte (`texte_de_la_reponse`).
    reponse: str | None = None
    reponse_le: datetime | None = None


class LigneDeReleveOuverte(BaseModel):
    """Une ligne de relevé ni rapprochée, ni justifiée."""

    model_config = ConfigDict(frozen=True)

    journal: str
    rang: int
    date: date
    libelle: str
    montant: Decimal


class AnomalieBloquante(BaseModel):
    model_config = ConfigDict(frozen=True)

    piece: str
    reference: str
    emetteur: str | None
    montant: Decimal | None
    regle: str


class AttenteDePiece(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    libelle: str
    origine: OrigineDAttente
    #: Le type de pièce que la demande portera (collecte).
    type_attendu: str
    montant_estime: Decimal | None = None
    #: La demande existante, s'il y en a une : relancer la relance au lieu d'en émettre une.
    demande: str | None = None
    demandee_le: date | None = None
    #: Pas 112 : la règle d'une anomalie bloquante, **pour le cabinet**. Elle ne figure plus dans le
    #: libellé, qui part chez l'adhérent : « ni FAC-ID-003, ni CGI art. 150-5 » (maquette « Espace
    #: adhérent », vue C, note 1).
    regle: str | None = None
    #: Pas 112 : ce que l'adhérent a répondu à la demande, et quand. Le comptable le lit avant de
    #: relancer : on ne relance pas celui qui a dit « la semaine prochaine » avant-hier.
    reponse: str | None = None
    reponse_le: datetime | None = None


def code_d_attente(*morceaux: str) -> str:
    """Un code stable et lisible : `releve-bq`, `serie-quincaillerie-du-wouri`."""
    texte = "-".join(morceaux)
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", texte.lower()).strip("-")[:60]


def _mois(jour: date) -> tuple[int, int]:
    return (jour.year, jour.month)


def _mois_precedents(debut: date, nombre: int) -> list[tuple[int, int]]:
    mois = []
    courant = debut.replace(day=1)
    for _ in range(nombre):
        courant = (courant - timedelta(days=1)).replace(day=1)
        mois.append(_mois(courant))
    return mois


def nom_du_mois(jour: date) -> str:
    noms = [
        "janvier",
        "février",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "août",
        "septembre",
        "octobre",
        "novembre",
        "décembre",
    ]
    return f"{noms[jour.month - 1]} {jour.year}"


def attentes_du_mois(
    *,
    du: date,
    au: date,
    pieces: list[PieceObservee],
    demandes_ouvertes: list[DemandeObservee],
    journaux_sans_releve: list[str],
    lignes_ouvertes: list[LigneDeReleveOuverte],
    obligations_du_mois: list[str],
    anomalies: list[AnomalieBloquante],
    reglages: ReglagesDesPiecesManquantes,
) -> list[AttenteDePiece]:
    """Les attentes du mois, dédoublonnées contre les demandes déjà ouvertes.

    ⚠️ Une attente déduite dont la demande existe déjà (même code, ou rectificative de la même
    pièce) n'apparaît qu'une fois, **portant la demande** : l'adhérent ne reçoit pas deux fois la
    même exigence sous deux noms.
    """
    mois = nom_du_mois(du)
    actives = reglages.origines
    deduites: list[AttenteDePiece] = []

    if OrigineDAttente.RELEVE_BANCAIRE in actives:
        for journal in sorted(journaux_sans_releve):
            deduites.append(
                AttenteDePiece(
                    code=code_d_attente("releve", journal),
                    libelle=f"Relevé bancaire du journal {journal}, {mois}",
                    origine=OrigineDAttente.RELEVE_BANCAIRE,
                    type_attendu="RELEVE_BANCAIRE",
                )
            )

    if OrigineDAttente.MOUVEMENT_SANS_PIECE in actives:
        for ligne in sorted(lignes_ouvertes, key=lambda l_: (l_.date, l_.journal, l_.rang)):
            if not du <= ligne.date <= au:
                continue
            deduites.append(
                AttenteDePiece(
                    code=code_d_attente(
                        "mouvement", ligne.journal, ligne.date.isoformat(), str(ligne.rang)
                    ),
                    libelle=f"Justificatif du mouvement du {ligne.date:%d/%m} : {ligne.libelle}",
                    origine=OrigineDAttente.MOUVEMENT_SANS_PIECE,
                    type_attendu="AUTRE",
                    montant_estime=ligne.montant,
                )
            )

    if OrigineDAttente.SERIE_HABITUELLE in actives:
        observes = _mois_precedents(du, reglages.mois_observes)
        presences: dict[str, set[tuple[int, int]]] = defaultdict(set)
        montants: dict[str, list[Decimal]] = defaultdict(list)
        du_mois: set[str] = set()
        for piece in pieces:
            if not piece.emetteur:
                continue
            cle = piece.emetteur.strip().upper()
            if du <= piece.date <= au:
                du_mois.add(cle)
            elif _mois(piece.date) in observes:
                presences[cle].add(_mois(piece.date))
                if piece.montant is not None:
                    montants[cle].append(piece.montant)
        for emetteur in sorted(presences):
            if len(presences[emetteur]) < reglages.presence_minimum or emetteur in du_mois:
                continue
            estimes = montants[emetteur]
            deduites.append(
                AttenteDePiece(
                    code=code_d_attente("serie", emetteur),
                    libelle=f"Facture {emetteur}, {mois}",
                    origine=OrigineDAttente.SERIE_HABITUELLE,
                    type_attendu="FACTURE_ACHAT",
                    montant_estime=(sum(estimes, Decimal(0)) / len(estimes)).quantize(Decimal(1))
                    if estimes
                    else None,
                )
            )

    if OrigineDAttente.OBLIGATION_DU_MOIS in actives:
        for code in sorted(set(obligations_du_mois)):
            piece = reglages.pieces_par_obligation.get(code)
            if piece:
                deduites.append(
                    AttenteDePiece(
                        code=code_d_attente("obligation", code),
                        libelle=f"{piece}, {mois}",
                        origine=OrigineDAttente.OBLIGATION_DU_MOIS,
                        type_attendu="QUITTANCE_IMPOT",
                    )
                )

    rectificatives = {d.piece_a_rectifier: d for d in demandes_ouvertes if d.piece_a_rectifier}
    if OrigineDAttente.ANOMALIE_BLOQUANTE in actives:
        for anomalie in sorted(anomalies, key=lambda a: a.piece):
            demande = rectificatives.get(anomalie.piece)
            deduites.append(
                AttenteDePiece(
                    code=code_d_attente("rectificative", anomalie.piece),
                    # ⚠️ Pas 112 : le code de la règle sortait dans le message envoyé à l'adhérent
                    # (« … (FAC-ID-003) »). Il passe dans `regle`, lu par le seul cabinet.
                    libelle=(
                        f"Facture rectificative de {anomalie.emetteur}"
                        + (f" (facture {anomalie.reference})" if anomalie.reference else "")
                        if anomalie.emetteur
                        else f"Facture rectificative de la facture {anomalie.reference}"
                    ),
                    regle=anomalie.regle,
                    origine=OrigineDAttente.ANOMALIE_BLOQUANTE,
                    type_attendu="FACTURE_ACHAT",
                    montant_estime=anomalie.montant,
                    demande=demande.identifiant if demande else None,
                    demandee_le=demande.demandee_le if demande else None,
                )
            )

    # Les demandes ouvertes : celles d'une attente déduite s'y rattachent, les autres s'ajoutent.
    par_identifiant = {d.identifiant: d for d in demandes_ouvertes}
    resultat: list[AttenteDePiece] = []
    rattachees: set[str] = set()
    for attente in deduites:
        identifiant = attente.demande or f"ATT-{du:%Y-%m}-{attente.code}"
        demande = par_identifiant.get(identifiant)
        if demande is not None:
            rattachees.add(demande.identifiant)
            attente = attente.model_copy(
                update={
                    "demande": demande.identifiant,
                    "demandee_le": demande.demandee_le,
                    "reponse": demande.reponse,
                    "reponse_le": demande.reponse_le,
                }
            )
        resultat.append(attente)
    if OrigineDAttente.DEMANDE_OUVERTE in actives:
        for demande in sorted(demandes_ouvertes, key=lambda d: (d.demandee_le, d.identifiant)):
            if demande.identifiant in rattachees or demande.demandee_le > au:
                continue
            resultat.append(
                AttenteDePiece(
                    code=code_d_attente("demande", demande.identifiant),
                    libelle=demande.motif,
                    origine=OrigineDAttente.DEMANDE_OUVERTE,
                    type_attendu=demande.type_attendu,
                    demande=demande.identifiant,
                    demandee_le=demande.demandee_le,
                    reponse=demande.reponse,
                    reponse_le=demande.reponse_le,
                )
            )
    return resultat


class RelanceRendue(BaseModel):
    model_config = ConfigDict(frozen=True)

    introduction: str
    liste: list[str]
    conclusion: str
    signature: str

    @property
    def texte(self) -> str:
        return "\n".join(
            [
                self.introduction,
                *(f"• {ligne}" for ligne in self.liste),
                self.conclusion,
                self.signature,
            ]
        )


def rendre_la_relance(
    modele: ModeleDeRelance,
    *,
    attentes: list[AttenteDePiece],
    du: date,
    date_limite: date,
    signataire: str,
    cabinet: str,
) -> RelanceRendue:
    """Le message tel qu'il part : **un seul rendu**, pour l'aperçu comme pour l'envoi."""
    valeurs = {"mois": nom_du_mois(du), "date_limite": f"{date_limite:%d/%m/%Y}"}
    return RelanceRendue(
        introduction=modele.introduction.format(**valeurs),
        liste=[a.libelle for a in attentes],
        conclusion=modele.conclusion.format(**valeurs),
        signature=f"{signataire}, {cabinet}",
    )
