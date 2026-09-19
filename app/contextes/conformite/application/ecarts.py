"""Les cas d'usage de l'écart de constat (pas 92).

─────────────────────────────────────────────────────────────────────────────────
TROIS GESTES ET UNE LECTURE

* `proposer_un_ecart` : le réviseur désigne un constat du rapport **actuel** et donne
  son motif. Selon la politique, l'écart est effectif tout de suite ou attend un
  second regard.
* `trancher_un_ecart` : une autre personne confirme ou refuse.
* `lever_un_ecart` : on revient sur la décision, le constat compte de nouveau.
* `appliquer_les_ecarts` : rend le rapport tel que le cabinet l'a arbitré.

⚠️ POURQUOI LA POLITIQUE EST RELUE AU MOMENT D'APPLIQUER

Un écart confirmé en mars sous une politique qui permettait d'écarter un MAJEUR sans
second regard ne doit pas continuer à s'appliquer en septembre, si le cabinet a
entre-temps durci la politique. L'inverse serait une règle qui ne vaut que pour
l'avenir, et les dossiers anciens garderaient l'indulgence d'hier.

L'écart n'est pas effacé pour autant : son statut reste `EFFECTIF`, et `etat_des_ecarts`
dit pourquoi il ne s'applique plus. Le réviseur peut le lever, ou le faire confirmer.

⚠️ CE QU'UN ÉCART NE CHANGE PAS : LES ÉCRITURES DÉJÀ VALIDÉES

La proposition d'écriture lit le rapport arbitré : une TVA dont le rejet a été écarté
est proposée récupérable. Mais une écriture validée avant l'écart garde son attribut
fiscal. Un journal validé ne se réécrit pas ; il se contre-passe, et c'est un geste
distinct, qui a son propre motif.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.contextes.conformite.domaine.ecarts import (
    EcartDeConstat,
    EcartIntrouvable,
    EcartRefuse,
    PolitiqueDEcart,
    StatutEcart,
    empreinte_du_constat,
    verifier_le_motif,
)
from app.contextes.conformite.domaine.entites import Constat, ConstatEcarte, RapportConformite
from app.contextes.conformite.domaine.ports import DepotEcarts

__all__ = [
    "EtatDUnEcart",
    "appliquer_les_ecarts",
    "etat_des_ecarts",
    "joindre_une_piece_d_appui",
    "lever_un_ecart",
    "proposer_un_ecart",
    "trancher_un_ecart",
]


def proposer_un_ecart(
    rapport: RapportConformite,
    *,
    dossier: str,
    code_regle: str,
    motif: str,
    par: str,
    le: datetime,
    politique: PolitiqueDEcart,
    depot: DepotEcarts,
    motif_type: str | None = None,
) -> EcartDeConstat:
    """Propose d'écarter le constat `code_regle` du rapport **tel qu'il vient d'être
    produit**.

    Le rapport est passé par l'appelant, fraîchement recalculé : écarter un constat
    qu'on a lu sur un écran resté ouvert depuis la veille reviendrait à écarter un
    constat que le moteur ne produit peut-être plus.
    """
    constat = _constat_du_rapport(rapport, code_regle)
    regle = politique.regle_pour(constat)
    if not regle.ecartable:
        raise EcartRefuse(
            f"la politique du cabinet ne permet pas d'écarter un constat {constat.severite.value}"
            f" ({code_regle}). L'action attendue est la demande de facture rectificative."
            f" Politique appliquée : {politique.source}."
        )
    verifier_le_motif(motif, politique.motif_minimum)
    if motif_type is not None and motif_type not in {
        m.code for m in politique.motifs_pour(code_regle)
    }:
        raise EcartRefuse(
            f"le motif type « {motif_type} » n'est pas proposé pour la règle {code_regle} "
            f"({politique.source})."
        )

    precedents = depot.pour_la_piece(dossier, rapport.reference_document)
    empreinte = empreinte_du_constat(constat)
    for ecart in precedents:
        if ecart.code_regle == code_regle and ecart.ouvert and ecart.empreinte == empreinte:
            raise EcartRefuse(
                f"un écart est déjà {ecart.statut.value} sur ce constat ({ecart.identifiant})."
                " Le lever d'abord, ou attendre le second regard."
            )
    # ⚠️ Un écart ouvert dont l'empreinte a changé est caduc : il ne s'applique plus
    # et ne bloque pas une nouvelle proposition. On le laisse tel quel au dépôt, pour
    # que l'histoire dise qu'il a existé.
    rang = 1 + sum(1 for e in precedents if e.code_regle == code_regle)

    ecart = EcartDeConstat(
        identifiant=f"{rapport.reference_document}:{code_regle}:{rang}",
        dossier=dossier,
        reference_document=rapport.reference_document,
        code_regle=code_regle,
        severite=constat.severite,
        empreinte=empreinte,
        enjeu=constat.enjeu,
        motif=motif.strip(),
        motif_type=motif_type,
        propose_par=par,
        propose_le=le,
        second_regard_requis=regle.second_regard,
        statut=StatutEcart.EN_ATTENTE if regle.second_regard else StatutEcart.EFFECTIF,
    )
    depot.enregistrer(ecart)
    return ecart


def trancher_un_ecart(
    *,
    dossier: str,
    reference_document: str,
    identifiant: str,
    confirme: bool,
    motif: str,
    par: str,
    le: datetime,
    politique: PolitiqueDEcart,
    depot: DepotEcarts,
) -> EcartDeConstat:
    ecart = _ecart_de_la_piece(depot, dossier, reference_document, identifiant)
    tranche = ecart.trancher(
        confirme=confirme, par=par, le=le, motif=motif, motif_minimum=politique.motif_minimum
    )
    depot.enregistrer(tranche)
    return tranche


def lever_un_ecart(
    *,
    dossier: str,
    reference_document: str,
    identifiant: str,
    motif: str,
    par: str,
    le: datetime,
    politique: PolitiqueDEcart,
    depot: DepotEcarts,
) -> EcartDeConstat:
    ecart = _ecart_de_la_piece(depot, dossier, reference_document, identifiant)
    leve = ecart.lever(par=par, le=le, motif=motif, motif_minimum=politique.motif_minimum)
    depot.enregistrer(leve)
    return leve


class EtatDUnEcart(BaseModel):
    """Un écart, et ce qu'il produit **aujourd'hui** sur le rapport."""

    model_config = ConfigDict(frozen=True)

    ecart: EcartDeConstat
    #: Vrai si le constat visé sort effectivement du rapport.
    applique: bool
    #: Pourquoi il ne s'applique pas, en clair. `None` quand il s'applique.
    raison: str | None = None


def etat_des_ecarts(
    rapport: RapportConformite,
    ecarts: list[EcartDeConstat],
    politique: PolitiqueDEcart,
) -> list[EtatDUnEcart]:
    """Chaque écart de la pièce, et s'il s'applique au rapport tel qu'il est produit.

    `rapport` est le rapport **brut**, sorti du moteur : c'est sur lui qu'on cherche
    le constat visé.
    """
    par_regle = {c.code_regle: c for c in rapport.constats}
    etats = []
    for ecart in ecarts:
        raison = _raison_de_ne_pas_appliquer(ecart, par_regle, politique)
        etats.append(EtatDUnEcart(ecart=ecart, applique=raison is None, raison=raison))
    return etats


def appliquer_les_ecarts(
    rapport: RapportConformite,
    ecarts: list[EcartDeConstat],
    politique: PolitiqueDEcart,
) -> RapportConformite:
    """Le rapport arbitré : un **nouveau** rapport, l'original reste intact.

    Les constats neutralisés passent dans `constats_ecartes`. Toutes les propriétés
    du rapport (`conforme`, `tva_deductible`, `comptabilisation_interdite`, `enjeu_total`)
    lisent `constats` : elles suivent sans rien avoir à savoir des écarts.
    """
    etats = etat_des_ecarts(rapport, ecarts, politique)
    ecartes = {e.ecart.code_regle: e.ecart.identifiant for e in etats if e.applique}
    if not ecartes:
        return rapport
    return rapport.model_copy(
        update={
            "constats": [c for c in rapport.constats if c.code_regle not in ecartes],
            "constats_ecartes": [
                *rapport.constats_ecartes,
                *(
                    ConstatEcarte(constat=c, identifiant_ecart=ecartes[c.code_regle])
                    for c in rapport.constats
                    if c.code_regle in ecartes
                ),
            ],
        }
    )


def _raison_de_ne_pas_appliquer(
    ecart: EcartDeConstat, par_regle: dict[str, Constat], politique: PolitiqueDEcart
) -> str | None:
    """`None` si l'écart s'applique ; sinon, la raison, en une phrase.

    L'ordre des tests suit l'ordre dans lequel un réviseur poserait les questions :
    la décision est-elle prise, le constat existe-t-il encore, est-ce bien le même,
    la politique le permet-elle toujours.
    """
    if ecart.statut is StatutEcart.EN_ATTENTE:
        return "en attente du second regard : le constat continue de compter."
    if ecart.statut is not StatutEcart.EFFECTIF:
        return f"écart {ecart.statut.value.lower()} : il ne produit aucun effet."
    constat = par_regle.get(ecart.code_regle)
    if constat is None:
        return "le moteur ne produit plus ce constat : il n'y a rien à écarter."
    if empreinte_du_constat(constat) != ecart.empreinte:
        return (
            "caduc : le constat a changé depuis la décision (enjeu, sévérité ou "
            "conséquence). Il doit être réexaminé."
        )
    regle = politique.regle_pour(constat)
    if not regle.ecartable:
        return "suspendu : la politique du cabinet ne permet plus d'écarter ce constat."
    if regle.second_regard and ecart.tranche_par is None:
        return (
            "suspendu : la politique exige désormais un second regard, que cet écart n'a pas reçu."
        )
    return None


def _constat_du_rapport(rapport: RapportConformite, code_regle: str) -> Constat:
    for constat in rapport.constats:
        if constat.code_regle == code_regle:
            return constat
    raise EcartRefuse(
        f"le rapport de {rapport.reference_document} ne porte aucun constat {code_regle}. "
        "Recharger la pièce : le contrôle a peut-être changé."
    )


def _ecart_de_la_piece(
    depot: DepotEcarts, dossier: str, reference_document: str, identifiant: str
) -> EcartDeConstat:
    for ecart in depot.pour_la_piece(dossier, reference_document):
        if ecart.identifiant == identifiant:
            return ecart
    raise EcartIntrouvable(f"écart « {identifiant} » inconnu sur cette pièce.")


def joindre_une_piece_d_appui(
    *,
    dossier: str,
    reference_document: str,
    identifiant: str,
    piece: str,
    par: str,
    le: datetime,
    depot: DepotEcarts,
) -> EcartDeConstat:
    """Attache à un écart le document qui prouve ce que son motif affirme (pas 118).

    ⚠️ **Aucun motif n'est demandé ici**, et c'est volontaire : la décision est déjà motivée et
    journalisée. Ce geste ne décide rien, il apporte la preuve de ce qui a été décidé. Exiger un
    second motif ferait écrire « pièce jointe » vingt fois par mois, et diluerait les vrais motifs.
    """
    ecart = _ecart_de_la_piece(depot, dossier, reference_document, identifiant)
    avec = ecart.joindre_une_piece_d_appui(piece, par=par, le=le)
    depot.enregistrer(avec)
    return avec
