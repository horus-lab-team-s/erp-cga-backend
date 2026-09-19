"""Le cycle de vie d'une demande de pièce : demander, satisfaire, classer, relancer.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE EXISTE (pas 74)

Le domaine des demandes est ancien et soigné : « le cabinet ne peut relancer que ce
qu'il sait attendre », « une relance non tracée n'a pas eu lieu », « une demande
satisfaite ne se relance jamais ». **Aucune route ne créait, ne satisfaisait, ne
classait ni ne traçait une demande.** Seul le jeu de démonstration en amorçait.

Conséquences, en service :

    une demande ne naissait jamais          le cabinet n'attendait rien d'opposable
    une pièce reçue ne satisfaisait rien    la demande restait ouverte, relancée
                                            aux jalons : « la première cause
                                            d'exaspération d'un adhérent »
    une relance émise n'était jamais tracée la liste de défense du Centre restait vide

Et l'écran d'une pièce proposait « Demander une facture rectificative », un bouton
sans action.

⚠️ CE QUE CE MODULE NE FAIT PAS

Il ne satisfait **jamais** une demande tout seul, à la réception d'une pièce. Deviner
qu'une facture reçue répond à telle demande, sur le type ou le fournisseur, c'est
deviner ; une demande close à tort ne se relance plus, et la pièce n'arrive jamais.
C'est le collaborateur qui rattache.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.contextes.collecte.domaine.demandes import (
    DemandePiece,
    NatureDeReponse,
    ReponseDeLAdherent,
)
from app.contextes.collecte.domaine.pieces import CanalDepot, TypePiece
from app.contextes.collecte.domaine.ports import DepotDemandes, DepotPieces
from app.partage.erreurs import message_lisible

__all__ = [
    "DemandeIntrouvable",
    "DemandeRefusee",
    "classer_une_demande",
    "demander_une_piece",
    "demander_une_rectificative",
    "repondre_a_une_demande",
    "satisfaire_une_demande",
    "tracer_une_relance",
]


class DemandeIntrouvable(LookupError):
    """La demande, ou la pièce désignée, n'existe pas."""


class DemandeRefusee(ValueError):
    """Le geste contredit l'état de la demande ou de la pièce."""


def demander_une_rectificative(
    *,
    identifiant: str,
    piece: str,
    motif: str,
    attendue_pour: date | None,
    le: date,
    par: str,
    pieces: DepotPieces,
    demandes: DepotDemandes,
) -> DemandePiece:
    """Demande à l'adhérent la facture rectificative d'une pièce reçue.

    ⚠️ **Le dossier et le type viennent de la pièce**, jamais de la requête : une
    demande rattachée au mauvais dossier serait relancée chez un adhérent qui n'a
    rien à envoyer.

    ⚠️ **Une seule demande ouverte par pièce.** Deux demandes pour la même
    rectification feraient relancer deux fois l'adhérent, et la seconde satisfaite
    laisserait la première ouverte.

    Toute demande de rectificative est **bloquante** : la pièce qu'elle remplace ne
    se comptabilise pas en l'état, et c'est précisément pourquoi on la demande.
    """
    origine = pieces.par_identifiant(piece)
    if origine is None:
        raise DemandeIntrouvable(f"pièce {piece} introuvable.")
    if not motif.strip():
        raise DemandeRefusee("une demande de rectificative dit ce qui est à rectifier.")
    deja = [d for d in demandes.ouvertes(origine.entreprise) if d.piece_a_rectifier == piece]
    if deja:
        raise DemandeRefusee(
            f"une demande de rectificative est déjà ouverte pour la pièce {piece} : "
            f"{deja[0].identifiant}, émise le {deja[0].demandee_le:%d/%m/%Y}. La relancer "
            "plutôt que d'en émettre une seconde."
        )
    demande = DemandePiece(
        identifiant=identifiant,
        entreprise=origine.entreprise,
        type_attendu=origine.type,
        motif=motif.strip(),
        demandee_le=le,
        attendue_pour=attendue_pour,
        bloquante=True,
        piece_a_rectifier=piece,
        demandee_par=par,
    )
    demandes.enregistrer(demande)
    return demande


def demander_une_piece(
    *,
    identifiant: str,
    entreprise: str,
    type_attendu: TypePiece,
    motif: str,
    attendue_pour: date | None,
    le: date,
    par: str,
    demandes: DepotDemandes,
) -> DemandePiece:
    """Rend explicite une attente déduite (pas 111) : la pièce devient une demande opposable.

    ⚠️ **Idempotent par identifiant.** L'identifiant vient de l'attente (« ATT-2026-07-releve-bq ») :
    relancer deux fois la même attente relance la **même** demande, au lieu d'en ouvrir une
    seconde que l'adhérent recevrait comme une nouvelle exigence. Une demande déjà satisfaite ou
    classée n'est pas rouverte : elle est rendue telle quelle, et l'appelant ne la relance pas.
    """
    existante = demandes.par_identifiant(identifiant)
    if existante is not None:
        if existante.entreprise != entreprise:
            raise DemandeRefusee(
                f"l'identifiant {identifiant} désigne la demande d'un autre dossier."
            )
        return existante
    if len(motif.strip()) < 3:
        raise DemandeRefusee("une demande de pièce dit ce qui est attendu.")
    demande = DemandePiece(
        identifiant=identifiant,
        entreprise=entreprise,
        type_attendu=type_attendu,
        motif=motif.strip(),
        demandee_le=le,
        attendue_pour=attendue_pour,
        demandee_par=par,
    )
    demandes.enregistrer(demande)
    return demande


def _lire(identifiant: str, demandes: DepotDemandes) -> DemandePiece:
    demande = demandes.par_identifiant(identifiant)
    if demande is None:
        raise DemandeIntrouvable(f"demande {identifiant} introuvable.")
    return demande


def satisfaire_une_demande(
    identifiant: str,
    *,
    piece: str,
    le: date,
    pieces: DepotPieces,
    demandes: DepotDemandes,
) -> DemandePiece:
    """Rattache la pièce reçue à la demande qu'elle satisfait.

    ⚠️ **La pièce doit être du même dossier.** Une facture de BATIMENT PLUS qui
    satisferait une demande faite à la BOULANGERIE fermerait une attente qui reste
    entière, et plus personne ne la relancerait.
    """
    demande = _lire(identifiant, demandes)
    recue = pieces.par_identifiant(piece)
    if recue is None:
        raise DemandeIntrouvable(f"pièce {piece} introuvable.")
    if recue.entreprise != demande.entreprise:
        raise DemandeRefusee(
            f"la pièce {piece} appartient au dossier {recue.entreprise}, la demande "
            f"{identifiant} au dossier {demande.entreprise}."
        )
    # ⚠️ Les deux gardes suivantes viennent de l'essai réel du pas 74 : la pièce
    # PJ-2026-0019, reçue en juillet et elle-même visée par une demande de
    # rectificative, a satisfait la demande d'une autre facture émise le 3 août.
    if recue.recue_le.date() < demande.demandee_le:
        raise DemandeRefusee(
            f"la pièce {piece} a été reçue le {recue.recue_le:%d/%m/%Y}, avant la demande "
            f"{identifiant} du {demande.demandee_le:%d/%m/%Y} : elle ne peut pas y répondre."
        )
    a_rectifier = [
        d for d in demandes.ouvertes(demande.entreprise)
        if d.piece_a_rectifier == piece and d.identifiant != identifiant
    ]
    if a_rectifier:
        raise DemandeRefusee(
            f"la pièce {piece} est elle-même à rectifier ({a_rectifier[0].identifiant}) : "
            "elle ne satisfait pas une autre demande."
        )
    try:
        satisfaite = demande.satisfaire(piece, le)
    except ValueError as refus:
        raise DemandeRefusee(str(refus)) from refus
    demandes.enregistrer(satisfaite)
    return satisfaite


def classer_une_demande(
    identifiant: str, *, motif: str, le: date, demandes: DepotDemandes
) -> DemandePiece:
    demande = _lire(identifiant, demandes)
    try:
        classee = demande.classer_sans_suite(motif, le)
    except ValueError as refus:
        raise DemandeRefusee(str(refus)) from refus
    demandes.enregistrer(classee)
    return classee


def tracer_une_relance(
    identifiant: str, *, canal: CanalDepot, le: date, demandes: DepotDemandes
) -> DemandePiece:
    """Trace qu'une relance a été émise, par tel canal, tel jour.

    ⚠️ **Tracer n'est pas émettre.** Ce geste consigne un appel, un message WhatsApp
    ou un courriel envoyé par le collaborateur. Le jour où la messagerie émettra
    elle-même, elle tracera par ce même cas d'usage.
    """
    demande = _lire(identifiant, demandes)
    try:
        relancee = demande.relancer(le, canal)
    except ValueError as refus:
        raise DemandeRefusee(str(refus)) from refus
    demandes.enregistrer(relancee)
    return relancee


def repondre_a_une_demande(
    identifiant: str,
    *,
    entreprise: str,
    nature: NatureDeReponse,
    message: str | None,
    le: datetime,
    par: str,
    jours_si_plus_tard: int,
    demandes: DepotDemandes,
) -> DemandePiece:
    """L'adhérent répond à une demande du cabinet (pas 112).

    ⚠️ **Le dossier est vérifié ici aussi**, en plus du périmètre contrôlé par la route : une
    réponse rattachée à la demande d'un autre dossier dirait au cabinet qu'un adhérent a répondu,
    alors que celui à qui la pièce est demandée n'a rien dit.

    « Plus tard » ne fait pas choisir une date à l'adhérent : le référentiel dit ce que « la
    semaine prochaine » veut dire (`jours_si_plus_tard`). Le cabinet lit alors un jour, et sait
    quand relancer.
    """
    demande = _lire(identifiant, demandes)
    if demande.entreprise != entreprise:
        raise DemandeIntrouvable(f"demande {identifiant} introuvable.")
    try:
        reponse = ReponseDeLAdherent(
            nature=nature,
            message=message,
            le=le,
            par=par,
            annoncee_pour=(
                le.date() + timedelta(days=jours_si_plus_tard)
                if nature is NatureDeReponse.PLUS_TARD
                else None
            ),
        )
        repondue = demande.repondre(reponse)
    except ValueError as refus:
        # `message_lisible` : la phrase du validateur, sans l'enveloppe technique de pydantic.
        raise DemandeRefusee(message_lisible(refus)) from refus
    demandes.enregistrer(repondue)
    return repondue
