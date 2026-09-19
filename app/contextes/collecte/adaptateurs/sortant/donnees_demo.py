"""Le flux entrant de démonstration — vingt-neuf pièces, plus un doublon.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE JEU RACONTE

Il dérive des factures de démonstration du contexte D : chacune est arrivée par un
canal, un jour, avec un délai de transmission, et se trouve à un stade de
traitement. C'est ce que l'écran E03 — la boîte de réception — doit montrer, et sa
fiche exige vingt-cinq lignes au minimum avec une distribution réaliste.

Cinq pièces restent **à l'état LUE**, pour deux raisons différentes et toutes deux
instructives.

Quatre parce que leur contrôle **interdit** la comptabilisation : le NIU du
fournisseur est absent ou radié. Elles attendent une facture rectificative — ce
qu'un cabinet vit tous les jours, et ce qu'un logiciel ordinaire rend invisible en
les comptabilisant quand même.

Une cinquième parce que ses lignes ne totalisent pas son hors-taxes : l'imputation
**refuse de deviner**. Répartir l'écart au prorata produirait une écriture
équilibrée mais fausse, et personne ne saurait plus lequel des deux montants
faisait foi. Le contrôle est ici moins sévère qu'au premier cas — rien n'est
bloqué — et pourtant la pièce ne peut pas avancer : ce sont bien deux mécaniques
distinctes.

Une trentième pièce est un **doublon délibéré** : la facture F-2026-0412, déjà
comptabilisée, redéposée sur le portail cinq jours plus tard. Elle donne à
l'écran de réception un cas d'arbitrage à afficher — et elle représente ce qui,
sans détection, ferait déduire la TVA deux fois.

LA NUMÉROTATION DES ÉCRITURES REDÉMARRE À CHAQUE DOSSIER

Une clé d'écriture `2026/AC/000007` désigne la septième écriture du journal des
achats de **ce dossier-là** pour l'exercice 2026. Elle n'est unique qu'à
l'intérieur d'un dossier : deux adhérents ont chacun leur journal AC, et chacun
sa septième écriture. Les pièces comptabilisées ci-dessous sont donc numérotées
par entreprise, dans l'ordre où elles sont arrivées au cabinet.

Le jeu de démonstration du contexte E **dérive de celui-ci** — la comptabilité a
le droit de lire la collecte, l'inverse est interdit. Il n'y a donc aucune
convention à maintenir en double : les écritures sont construites à partir des
pièces, et portent la clé que la pièce annonce.

⚠️ Données fictives.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.contextes.collecte.domaine.demandes import DemandePiece
from app.contextes.collecte.domaine.pieces import (
    CanalDepot,
    EtatPiece,
    PieceJustificative,
    TypePiece,
    empreinte,
)
from app.contextes.conformite.api import FACTURES_DEMO

__all__ = [
    "DEMANDES_DEMO",
    "DOCUMENTS_DEMO",
    "PIECES_DEMO",
    "REFERENCES_BLOQUANTES",
    "REFERENCES_NON_IMPUTABLES",
    "identifiant_piece_demo",
    "semer_les_documents_demo",
]

#: Les factures dont le contrôle interdit la comptabilisation : NIU du fournisseur
#: absent ou radié. Recopiées ici plutôt que recalculées, parce que faire tourner
#: le moteur à l'import coûterait la lecture du référentiel à chaque démarrage.
#: Un test vérifie que cette liste est exactement celle que le moteur produit.
REFERENCES_BLOQUANTES = frozenset(
    {"F-2026-0414", "F-2026-0424", "F-2026-0427", "F-2026-0431"}
)

#: Les factures dont le détail des lignes ne totalise pas le hors-taxes annoncé.
#: Elles ne sont pas bloquantes au sens du contrôle de conformité, mais elles ne
#: produisent aucune écriture : `proposer_ecriture_achat` lève plutôt que de
#: répartir l'écart. Une facture rectificative est nécessaire — voir FAC-CAL-002.
REFERENCES_NON_IMPUTABLES = frozenset({"F-2026-0430"})

#: Canal de dépôt et délai de transmission associé, en jours. Le mode hors ligne
#: du terminal mobile explique le délai le plus long : une pièce photographiée sur
#: un chantier sans réseau attend la prochaine connexion.
_CANAUX: tuple[tuple[CanalDepot, int], ...] = (
    (CanalDepot.WHATSAPP, 2),
    (CanalDepot.PORTAIL, 0),
    (CanalDepot.MOBILE, 5),
    (CanalDepot.COURRIEL, 3),
    (CanalDepot.DEPOT_CABINET, 1),
)

#: Au-delà de cette date de réception, la pièce est encore dans le travail à faire.
#: C'est ce qui donne à la boîte de réception un mélange réaliste de lignes
#: traitées et de lignes en attente.
_TRAITEES_JUSQU_AU = date(2026, 7, 24)
_LUES_JUSQU_AU = date(2026, 7, 29)

_REFERENCES = sorted(FACTURES_DEMO)


def identifiant_piece_demo(reference_facture: str) -> str:
    """Identifiant de la pièce portant cette facture."""
    return f"PJ-2026-{_REFERENCES.index(reference_facture) + 1:04d}"


#: Le contenu de chaque document de démonstration, par empreinte (pas 88).
DOCUMENTS_DEMO: dict[str, bytes] = {}


def _document_pdf(lignes: list[str]) -> bytes:
    """Un PDF d'une page, valide et lisible, portant quelques lignes de texte (pas 88).

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ POURQUOI DE VRAIS DOCUMENTS

    Les pièces de démonstration annonçaient un fichier (« f-2026-0412.pdf », 180 Ko)
    dont l'empreinte était celle de leur référence, `empreinte(b"F-2026-0412")`, et
    aucun fichier n'existait au magasin. Essai réel : « Télécharger le document » rendait
    « Le document est introuvable au magasin » sur toutes les pièces de démonstration.
    Une base qui prétend détenir des documents qu'elle n'a pas montre le produit cassé
    là où il fonctionne.

    Le document porte désormais les données de la facture, son empreinte est la sienne,
    et sa taille est la vraie. Il est rangé au magasin au moment où les pièces sont
    chargées (voir `semer_les_documents_demo`).

    Les objets sont écrits à la main, avec leurs positions dans la table de références
    croisées : c'est ce qui rend le fichier ouvrable par un lecteur ordinaire, et un PDF
    minimal n'a besoin d'aucune bibliothèque.
    ─────────────────────────────────────────────────────────────────────────────
    """

    def echapper(texte: str) -> str:
        return texte.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    flux = "BT /F1 11 Tf 56 780 Td 16 TL " + " ".join(
        f"({echapper(ligne)}) Tj T*" for ligne in lignes
    ) + " ET"
    flux_octets = flux.encode("latin-1", errors="replace")
    objets = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length "
        + str(len(flux_octets)).encode()
        + b" >>\nstream\n"
        + flux_octets
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    corps = bytearray(b"%PDF-1.4\n")
    positions = []
    for numero, objet in enumerate(objets, start=1):
        positions.append(len(corps))
        corps += f"{numero} 0 obj\n".encode() + objet + b"\nendobj\n"
    debut_xref = len(corps)
    corps += f"xref\n0 {len(objets) + 1}\n0000000000 65535 f \n".encode()
    for position in positions:
        corps += f"{position:010d} 00000 n \n".encode()
    corps += (
        f"trailer\n<< /Size {len(objets) + 1} /Root 1 0 R >>\nstartxref\n{debut_xref}\n%%EOF\n"
    ).encode()
    return bytes(corps)


def _document_de(lignes: list[str]) -> tuple[str, bytes]:
    """Fabrique le document, le retient, et rend son empreinte."""
    contenu = _document_pdf(["DOCUMENT DE DEMONSTRATION - donnees fictives", "", *lignes])
    cle = empreinte(contenu)
    DOCUMENTS_DEMO[cle] = contenu
    return cle, contenu


def semer_les_documents_demo(magasin) -> int:
    """Range au magasin les documents des pièces de démonstration absents. Rend le nombre rangé.

    Idempotent : le magasin est adressé par le contenu, et un document déjà présent
    n'est pas réécrit. Appelé là où les pièces de démonstration sont chargées, et nulle
    part ailleurs : une installation sans démonstration n'a pas à recevoir ces fichiers.
    """
    ranges = 0
    for cle, contenu in DOCUMENTS_DEMO.items():
        if not magasin.existe(cle):
            magasin.deposer(cle, contenu, type_mime="application/pdf")
            ranges += 1
    return ranges


def _construire() -> dict[str, PieceJustificative]:
    pieces: dict[str, PieceJustificative] = {}
    #: Compteur par dossier : la séquence du journal des achats redémarre à
    #: chaque adhérent — voir l'en-tête du module.
    numeros: dict[str, int] = {}

    for rang, reference in enumerate(_REFERENCES):
        facture = FACTURES_DEMO[reference]
        canal, delai = _CANAUX[rang % len(_CANAUX)]

        depose_le = facture.document.date_emission + timedelta(days=1)
        recue_le = datetime.combine(
            depose_le + timedelta(days=delai), datetime.min.time()
        ).replace(hour=8 + rang % 9, minute=(rang * 7) % 60)

        cle, contenu = _document_de(
            [
                f"Facture {reference} du {facture.document.date_emission:%d/%m/%Y}",
                f"Emetteur : {facture.emetteur.denomination}",
                f"Destinataire : {facture.destinataire.denomination}",
                f"Total TTC : {facture.montants.total_ttc} FCFA",
            ]
        )
        piece = PieceJustificative(
            identifiant=identifiant_piece_demo(reference),
            entreprise=facture.destinataire.niu,
            canal=canal,
            depose_le=depose_le,
            recue_le=recue_le,
            type=TypePiece.FACTURE_ACHAT,
            nom_fichier=f"{reference.lower()}.pdf",
            empreinte=cle,
            taille_octets=len(contenu),
            reference_document=reference,
            date_document=facture.document.date_emission,
            montant_ttc=facture.montants.total_ttc,
            emetteur=facture.emetteur.denomination,
            reference_rapport=reference,
            depose_par=facture.destinataire.denomination,
        ).marquer_lue()

        if reference in REFERENCES_BLOQUANTES:
            # Elle s'arrête là, et c'est le sujet : la comptabilisation est
            # interdite tant que le fournisseur n'a pas régularisé.
            piece = piece.model_copy(
                update={
                    "commentaire": (
                        "Comptabilisation interdite — NIU du fournisseur absent ou "
                        "radié. Facture rectificative demandée."
                    )
                }
            )
        elif reference in REFERENCES_NON_IMPUTABLES:
            piece = piece.model_copy(
                update={
                    "commentaire": (
                        "Le détail des lignes ne totalise pas le hors-taxes annoncé. "
                        "L'imputation refuse de deviner — facture rectificative "
                        "demandée, voir FAC-CAL-002."
                    )
                }
            )
        elif recue_le.date() <= _TRAITEES_JUSQU_AU:
            dossier = facture.destinataire.niu
            numeros[dossier] = numeros.get(dossier, 0) + 1
            piece = piece.comptabiliser(f"2026/AC/{numeros[dossier]:06d}")
        elif recue_le.date() > _LUES_JUSQU_AU:
            # Reçue mais pas encore ouverte par le comptable : elle repart à
            # l'état RECUE, sans les données du document.
            piece = piece.model_copy(
                update={
                    "etat": EtatPiece.RECUE,
                    "type": TypePiece.INDETERMINE,
                    "reference_document": None,
                    "date_document": None,
                    "montant_ttc": None,
                    "emetteur": None,
                    "reference_rapport": None,
                }
            )

        pieces[piece.identifiant] = piece

    # Le doublon délibéré : même facture, autre fichier, autre canal, cinq jours
    # plus tard. C'est le cas que la détection d'empreintes ne voit pas.
    doublon_de = "F-2026-0412"
    facture = FACTURES_DEMO[doublon_de]
    # Un autre fichier, donc une autre empreinte : c'est le sujet du doublon délibéré.
    cle, contenu = _document_de(
        [
            f"Scan portail de la facture {doublon_de}",
            f"Emetteur : {facture.emetteur.denomination}",
            "Redepot du 18/07/2026",
        ]
    )
    pieces["PJ-2026-0900"] = PieceJustificative(
        identifiant="PJ-2026-0900",
        entreprise=facture.destinataire.niu,
        canal=CanalDepot.PORTAIL,
        depose_le=date(2026, 7, 18),
        recue_le=datetime(2026, 7, 18, 15, 42),
        type=TypePiece.FACTURE_ACHAT,
        nom_fichier="scan_quincaillerie_juillet.pdf",
        empreinte=cle,
        taille_octets=len(contenu),
        reference_document=doublon_de,
        date_document=facture.document.date_emission,
        montant_ttc=facture.montants.total_ttc,
        emetteur=facture.emetteur.denomination,
        depose_par=facture.destinataire.denomination,
        commentaire="Redépôt — à arbitrer avant toute comptabilisation.",
    ).marquer_lue()

    return pieces


PIECES_DEMO: dict[str, PieceJustificative] = _construire()


# ── Les demandes de pièces ───────────────────────────────────────────────────────

_MOTIF_RECTIFICATIVE = (
    "Facture rectificative — le NIU du fournisseur est absent ou radié, la TVA "
    "n'est pas déductible en l'état"
)


def _demandes() -> dict[str, DemandePiece]:
    demandes: list[DemandePiece] = []

    for rang, reference in enumerate(sorted(REFERENCES_BLOQUANTES), start=1):
        facture = FACTURES_DEMO[reference]
        demandes.append(
            DemandePiece(
                identifiant=f"DP-2026-{rang:03d}",
                entreprise=facture.destinataire.niu,
                type_attendu=TypePiece.FACTURE_ACHAT,
                motif=f"{_MOTIF_RECTIFICATIVE} — {reference}, {facture.emetteur.denomination}",
                demandee_le=date(2026, 8, 3),
                # Calée sur l'échéance de la déclaration de TVA de juillet : une
                # date d'attente sans lien avec une échéance n'engage personne.
                attendue_pour=date(2026, 8, 12),
                bloquante=True,
                # Pas 74 : la pièce est nommée, et plus seulement citée dans le motif.
                piece_a_rectifier=identifiant_piece_demo(reference),
            )
        )

    demandes += [
        DemandePiece(
            identifiant="DP-2026-009",
            entreprise="M081234567890P",
            type_attendu=TypePiece.FACTURE_ACHAT,
            motif=(
                "Facture rectificative — F-2026-0430, MENUISERIE BAFOUSSAM : le détail "
                "des lignes dépasse le total hors taxes de 12 400 F"
            ),
            demandee_le=date(2026, 8, 3),
            attendue_pour=date(2026, 8, 12),
            bloquante=True,
            piece_a_rectifier=identifiant_piece_demo("F-2026-0430"),
        ),
        DemandePiece(
            identifiant="DP-2026-010",
            entreprise="M081234567890P",
            type_attendu=TypePiece.RELEVE_BANCAIRE,
            motif="Relevé bancaire de juillet 2026 — nécessaire au rapprochement",
            demandee_le=date(2026, 8, 5),
            attendue_pour=date(2026, 8, 20),
        ),
        DemandePiece(
            identifiant="DP-2026-011",
            entreprise="M065544332211L",
            type_attendu=TypePiece.CONTRAT,
            motif="Contrat de bail de l'entrepôt de Bafoussam — justification de la charge",
            # Trente jours au 14 août : ce n'est plus un oubli, c'est un dossier
            # qui n'avance pas.
            demandee_le=date(2026, 7, 15),
        ),
        DemandePiece(
            identifiant="DP-2026-012",
            entreprise="P019876543210K",
            type_attendu=TypePiece.QUITTANCE_IMPOT,
            motif="Quittance de l'impôt libératoire du 2e trimestre 2026",
            demandee_le=date(2026, 7, 28),
            attendue_pour=date(2026, 8, 8),
        ).satisfaire("PJ-2026-0006", date(2026, 8, 6)),
    ]

    return {demande.identifiant: demande for demande in demandes}


DEMANDES_DEMO: dict[str, DemandePiece] = _demandes()
