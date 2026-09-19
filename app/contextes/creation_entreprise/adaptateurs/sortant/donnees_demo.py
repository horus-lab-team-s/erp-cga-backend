"""Le pipeline de démonstration du contexte I.

Six dossiers, choisis pour que chaque étape du tunnel soit peuplée et que les
trois situations qui font agir le chargé de formalités soient visibles d'un coup
d'œil : un dossier bloqué faute de pièces, un dossier qui dort au guichet
au-delà du délai annoncé, et un dossier perdu avec son motif.

⚠️ Ces données sont **fictives**. Les NIU et RCCM respectent les formats mais ne
désignent aucune entreprise réelle. Les identités sont inventées.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.contextes.creation_entreprise.domaine.checklist import checklist_de
from app.contextes.creation_entreprise.domaine.entites import (
    DossierCreation,
    EtapeCreation,
    Fondateur,
    Immatriculation,
    Jalon,
    PieceConstitution,
)
from app.contextes.portefeuille.api import FormeJuridique

__all__ = ["PIPELINE_DEMO"]


def _fournies(
    forme: FormeJuridique, codes: set[str] | None, le: date
) -> tuple[PieceConstitution, ...]:
    """La checklist de cette forme, avec les pièces citées marquées reçues.

    `codes` à `None` marque tout : c'est le cas courant d'un dossier qui a
    dépassé la constitution.
    """
    return tuple(
        p.model_copy(update={"fournie_le": le})
        if codes is None or p.code in codes
        else p
        for p in checklist_de(forme)
    )


PIPELINE_DEMO: list[DossierCreation] = [
    # ── Qualification : le dossier tout neuf, rien n'a encore été demandé ────
    DossierCreation(
        reference="CR-2026-0011",
        fondateur=Fondateur(
            nom="MOMHA",
            prenom="Estelle",
            courriel="e.momha@exemple.cm",
            telephone="+237699112233",
        ),
        denomination_souhaitee="ESTELLE COSMETIQUES",
        forme_juridique=FormeJuridique.ETS,
        activite="Commerce de produits cosmétiques",
        siege="Douala, Bonapriso",
        ouvert_le=date(2026, 8, 10),
        etape=EtapeCreation.QUALIFICATION,
        pieces=checklist_de(FormeJuridique.ETS),
        jalons=(
            Jalon(
                etape=EtapeCreation.QUALIFICATION,
                survenu_le=date(2026, 8, 10),
                par="p.moukouri@cga-brcg.cm",
                commentaire="Entreprise individuelle : dossier le plus court du catalogue.",
            ),
        ),
    ),
    # ── Constitution incomplète : le cas qui fait agir ──────────────────────
    #
    # Il manque la déclaration notariée de souscription du capital. C'est la
    # pièce la plus souvent en retard : elle suppose un rendez-vous chez le
    # notaire et le versement effectif des fonds.
    DossierCreation(
        reference="CR-2026-0012",
        fondateur=Fondateur(
            nom="BIYA'A",
            prenom="Roger",
            courriel="r.biyaa@exemple.cm",
            telephone="+237677445566",
            piece_identite="CNI 1098765432",
        ),
        denomination_souhaitee="TECHNO SERVICES SARL",
        forme_juridique=FormeJuridique.SARL,
        activite="Maintenance informatique",
        siege="Yaoundé, Bastos",
        capital=Decimal("1000000"),
        ouvert_le=date(2026, 7, 20),
        etape=EtapeCreation.CONSTITUTION,
        pieces=_fournies(
            FormeJuridique.SARL,
            {
                "ID_FONDATEUR",
                "CASIER_JUDICIAIRE",
                "JUSTIFICATIF_SIEGE",
                "STATUTS",
                "LISTE_ASSOCIES",
                "ID_DIRIGEANTS",
                "PV_NOMINATION",
            },
            date(2026, 8, 3),
        ),
        jalons=(
            Jalon(etape=EtapeCreation.QUALIFICATION, survenu_le=date(2026, 7, 20)),
            Jalon(
                etape=EtapeCreation.CONSTITUTION,
                survenu_le=date(2026, 8, 3),
                par="p.moukouri@cga-brcg.cm",
                commentaire="Reste la déclaration notariée de souscription du capital.",
            ),
        ),
    ),
    # ── Dépôt CFCE en dépassement : le délai annoncé de 3 jours est loin ────
    DossierCreation(
        reference="CR-2026-0013",
        fondateur=Fondateur(
            nom="ATEBA",
            prenom="Clarisse",
            courriel="c.ateba@exemple.cm",
            telephone="+237698223344",
            piece_identite="CNI 1122334455",
        ),
        denomination_souhaitee="CLARISSE AGRO SARLU",
        forme_juridique=FormeJuridique.SARLU,
        activite="Transformation agroalimentaire",
        siege="Bafoussam, Marché A",
        capital=Decimal("500000"),
        ouvert_le=date(2026, 6, 15),
        etape=EtapeCreation.DEPOT_CFCE,
        pieces=_fournies(FormeJuridique.SARLU, None, date(2026, 7, 25)),
        jalons=(
            Jalon(etape=EtapeCreation.QUALIFICATION, survenu_le=date(2026, 6, 15)),
            Jalon(etape=EtapeCreation.CONSTITUTION, survenu_le=date(2026, 7, 25)),
            Jalon(
                etape=EtapeCreation.DEPOT_CFCE,
                survenu_le=date(2026, 7, 30),
                par="p.moukouri@cga-brcg.cm",
                commentaire="Dépôt au guichet unique de Bafoussam.",
            ),
        ),
    ),
    # ── Suivi : le RCCM est là, le NIU se fait attendre ─────────────────────
    #
    # La situation la plus fréquente, et celle qui explique pourquoi la
    # conversion exige les deux : la société existe en droit commercial mais
    # n'est pas encore un contribuable.
    DossierCreation(
        reference="CR-2026-0014",
        fondateur=Fondateur(
            nom="NJOYA",
            prenom="Ibrahim",
            courriel="i.njoya@exemple.cm",
            telephone="+237691556677",
            piece_identite="CNI 1234509876",
        ),
        denomination_souhaitee="SAHEL DISTRIBUTION SA",
        forme_juridique=FormeJuridique.SA,
        activite="Distribution de matériaux de construction",
        siege="Douala, Bonabéri",
        capital=Decimal("15000000"),
        ouvert_le=date(2026, 5, 4),
        etape=EtapeCreation.SUIVI_IMMATRICULATION,
        pieces=_fournies(FormeJuridique.SA, None, date(2026, 6, 12)),
        immatriculation=Immatriculation(
            rccm="RC/DLA/2026/B/0417", rccm_obtenu_le=date(2026, 7, 2)
        ),
        jalons=(
            Jalon(etape=EtapeCreation.QUALIFICATION, survenu_le=date(2026, 5, 4)),
            Jalon(etape=EtapeCreation.CONSTITUTION, survenu_le=date(2026, 6, 12)),
            Jalon(etape=EtapeCreation.DEPOT_CFCE, survenu_le=date(2026, 6, 20)),
            Jalon(
                etape=EtapeCreation.SUIVI_IMMATRICULATION,
                survenu_le=date(2026, 7, 2),
                commentaire="RCCM délivré. NIU en attente à la DGE.",
            ),
        ),
    ),
    # ── Livraison : les deux identifiants sont là, prêt à convertir ─────────
    DossierCreation(
        reference="CR-2026-0015",
        fondateur=Fondateur(
            nom="TCHOUTA",
            prenom="Bernadette",
            courriel="b.tchouta@exemple.cm",
            telephone="+237696778899",
            piece_identite="CNI 1567890123",
        ),
        denomination_souhaitee="BERNA TRANSIT SARL",
        forme_juridique=FormeJuridique.SARL,
        activite="Transit et commission en douane",
        siege="Douala, Port",
        capital=Decimal("2000000"),
        ouvert_le=date(2026, 4, 8),
        etape=EtapeCreation.LIVRAISON,
        pieces=_fournies(FormeJuridique.SARL, None, date(2026, 5, 6)),
        immatriculation=Immatriculation(
            rccm="RC/DLA/2026/B/0298",
            rccm_obtenu_le=date(2026, 5, 22),
            niu="M112233445566T",
            niu_obtenu_le=date(2026, 6, 2),
            patente="PAT-2026-DLA-4471",
            patente_obtenue_le=date(2026, 6, 18),
        ),
        jalons=(
            Jalon(etape=EtapeCreation.QUALIFICATION, survenu_le=date(2026, 4, 8)),
            Jalon(etape=EtapeCreation.CONSTITUTION, survenu_le=date(2026, 5, 6)),
            Jalon(etape=EtapeCreation.DEPOT_CFCE, survenu_le=date(2026, 5, 15)),
            Jalon(etape=EtapeCreation.SUIVI_IMMATRICULATION, survenu_le=date(2026, 5, 22)),
            Jalon(
                etape=EtapeCreation.LIVRAISON,
                survenu_le=date(2026, 6, 25),
                par="p.moukouri@cga-brcg.cm",
                commentaire="Originaux remis. Adhésion à proposer.",
            ),
        ),
    ),
    # ── Abandonné : le motif est ce qui rend le pipeline analysable ─────────
    DossierCreation(
        reference="CR-2026-0009",
        fondateur=Fondateur(
            nom="EYENGA",
            prenom="Paul",
            courriel="p.eyenga@exemple.cm",
            telephone="+237675334455",
        ),
        denomination_souhaitee="PAUL BTP SAS",
        forme_juridique=FormeJuridique.SAS,
        activite="Bâtiment et travaux publics",
        siege="Yaoundé, Nsam",
        ouvert_le=date(2026, 3, 2),
        etape=EtapeCreation.ABANDONNE,
        pieces=_fournies(FormeJuridique.SAS, {"ID_FONDATEUR"}, date(2026, 3, 10)),
        motif_abandon="Projet reporté : financement bancaire refusé.",
        jalons=(
            Jalon(etape=EtapeCreation.QUALIFICATION, survenu_le=date(2026, 3, 2)),
            Jalon(
                etape=EtapeCreation.ABANDONNE,
                survenu_le=date(2026, 4, 18),
                par="p.moukouri@cga-brcg.cm",
                commentaire="Projet reporté : financement bancaire refusé.",
            ),
        ),
    ),
]
