"""Le tunnel de création : ouvrir, avancer, diagnostiquer, abandonner.

Les cas d'usage du contexte I. Chacun rend un **nouveau** dossier : rien n'est
modifié sur place, ce qui permet de comparer l'avant et l'après, et empêche qu'un
cas d'usage altère un dossier qu'un autre tient déjà.

⚠️ Aucun de ces cas d'usage ne parle à une base ni à une horloge. La date lui est
donnée, le dépôt lui est passé. C'est ce qui les rend testables sans montage, et
c'est aussi ce qui a permis de trouver, sur ce projet, qu'un cas d'usage lisant
l'horloge murale casse la suite à minuit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.contextes.creation_entreprise.domaine.checklist import (
    checklist_de,
    code_du_capital_minimum,
)
from app.contextes.creation_entreprise.domaine.entites import (
    DossierCreation,
    EtapeCreation,
    Fondateur,
    Jalon,
    PieceConstitution,
    TransitionInterdite,
    etapes_ouvertes_depuis,
)
from app.contextes.portefeuille.api import FormeJuridique
from app.contextes.referentiel.api import (
    AucuneVersionApplicable,
    ParametreInconnu,
    ServiceParametres,
    StatutValidation,
)

__all__ = [
    "ConstatConstitution",
    "Diagnostic",
    "abandonner",
    "avancer",
    "diagnostiquer",
    "enregistrer_identifiant",
    "fournir_piece",
    "ouvrir_dossier",
]


# ── Le diagnostic de constitution ────────────────────────────────────────────
@dataclass(frozen=True)
class ConstatConstitution:
    """Un constat sur l'état du dossier, en langage de guichet.

    `bloquant` distingue ce qui fait revenir du CFCE de ce qui mérite seulement
    d'être signalé. La distinction n'est pas cosmétique : c'est elle qui décide si
    le chargé de formalités peut déposer aujourd'hui.
    """

    code: str
    message: str
    bloquant: bool


@dataclass(frozen=True)
class Diagnostic:
    """Ce qu'il manque au dossier pour être déposable."""

    constats: tuple[ConstatConstitution, ...]

    @property
    def deposable(self) -> bool:
        return not any(c.bloquant for c in self.constats)


def ouvrir_dossier(
    *,
    reference: str,
    fondateur: Fondateur,
    denomination_souhaitee: str,
    forme_juridique: FormeJuridique,
    activite: str,
    siege: str,
    a_la_date: date,
    capital: Decimal | None = None,
    par: str | None = None,
) -> DossierCreation:
    """Ouvre un dossier, avec sa checklist déjà posée.

    ─────────────────────────────────────────────────────────────────────────
    LA CHECKLIST EST FIGÉE À L'OUVERTURE, PAS RECALCULÉE À CHAQUE LECTURE

    Un dossier ouvert en janvier sous une checklist donnée doit rester lisible
    en juin, même si la checklist a changé entre-temps. La recalculer à chaque
    affichage ferait apparaître, du jour au lendemain, une pièce manquante que
    personne n'a jamais demandée au fondateur — et le cabinet passerait pour
    négligent sur un dossier qui était complet.

    C'est le même principe que le référentiel daté, appliqué à une liste plutôt
    qu'à un taux : ce qui a servi à instruire un dossier reste attaché à ce
    dossier.
    ─────────────────────────────────────────────────────────────────────────
    """
    return DossierCreation(
        reference=reference,
        fondateur=fondateur,
        denomination_souhaitee=denomination_souhaitee,
        forme_juridique=forme_juridique,
        activite=activite,
        siege=siege,
        capital=capital,
        ouvert_le=a_la_date,
        etape=EtapeCreation.QUALIFICATION,
        pieces=checklist_de(forme_juridique),
        jalons=(Jalon(etape=EtapeCreation.QUALIFICATION, survenu_le=a_la_date, par=par),),
    )


def diagnostiquer(
    dossier: DossierCreation,
    parametres: ServiceParametres,
    a_la_date: date,
) -> Diagnostic:
    """Dit ce qui manque au dossier pour partir au guichet.

    Deux familles de constats, et elles n'ont pas la même autorité.

    **Les pièces** sont un fait vérifiable : elles sont au dossier ou non. Un
    manque est bloquant, sans nuance.

    **Le capital minimum** est une valeur légale, et elle porte son statut. Tant
    qu'un fiscaliste n'a pas confirmé le montant sur le texte OHADA, le constat
    est émis en **avertissement et non en blocage** : refuser un dépôt sur un
    chiffre non validé coûterait un client au cabinet, sur une règle dont on
    n'est pas sûr. Le jour où le paramètre passe VALIDE, le même constat devient
    bloquant sans qu'une ligne de code change.

    C'est la discipline que tout le produit applique : un chiffre non validé
    informe, il n'interdit pas.
    """
    constats: list[ConstatConstitution] = []

    for piece in dossier.pieces_manquantes:
        constats.append(
            ConstatConstitution(
                code=f"PIECE_MANQUANTE:{piece.code}",
                message=f"Pièce manquante : {piece.libelle}.",
                bloquant=True,
            )
        )

    code_capital = code_du_capital_minimum(dossier.forme_juridique)
    if code_capital is not None:
        constats.extend(_constater_le_capital(dossier, code_capital, parametres, a_la_date))

    if not dossier.denomination_souhaitee.strip():
        constats.append(
            ConstatConstitution(
                code="DENOMINATION_ABSENTE",
                message="Aucune dénomination souhaitée : le greffe ne peut pas en vérifier "
                "la disponibilité.",
                bloquant=True,
            )
        )

    return Diagnostic(constats=tuple(constats))


def _constater_le_capital(
    dossier: DossierCreation,
    code: str,
    parametres: ServiceParametres,
    a_la_date: date,
) -> list[ConstatConstitution]:
    """Compare le capital annoncé au minimum légal, si ce minimum est connaissable.

    Un paramètre absent ou sans version applicable ne fait **pas** échouer le
    diagnostic : il produit un constat non bloquant qui le dit. Lever ici
    empêcherait d'instruire tout dossier de SARL pour une ligne manquante dans un
    fichier YAML — la panne serait totale et son origine invisible depuis le
    guichet.
    """
    try:
        resolu = parametres.resoudre(code, a_la_date)
    except (ParametreInconnu, AucuneVersionApplicable) as absence:
        return [
            ConstatConstitution(
                code="CAPITAL_MINIMUM_INCONNU",
                message=f"Capital minimum non résolu au référentiel ({absence}). "
                "Vérification manuelle nécessaire.",
                bloquant=False,
            )
        ]

    minimum = resolu.valeur_decimale
    valide = resolu.statut is StatutValidation.VALIDE

    if dossier.capital is None:
        return [
            ConstatConstitution(
                code="CAPITAL_ABSENT",
                message=f"Capital non renseigné, alors que la forme "
                f"{dossier.forme_juridique} en exige un minimum de "
                f"{minimum:,.0f} FCFA.".replace(",", " "),
                bloquant=valide,
            )
        ]

    if dossier.capital < minimum:
        suffixe = "" if valide else " ⚠️ Montant non encore validé sur le texte OHADA."
        return [
            ConstatConstitution(
                code="CAPITAL_INSUFFISANT",
                message=f"Capital de {dossier.capital:,.0f} FCFA inférieur au minimum de "
                f"{minimum:,.0f} FCFA pour une {dossier.forme_juridique}.".replace(",", " ")
                + suffixe,
                bloquant=valide,
            )
        ]
    return []


def fournir_piece(
    dossier: DossierCreation,
    code_piece: str,
    a_la_date: date,
    *,
    empreinte: str | None = None,
) -> DossierCreation:
    """Marque une pièce comme reçue.

    Une pièce inconnue de la checklist est **ajoutée** plutôt que refusée : le
    guichet réclame parfois une pièce que la liste n'a pas prévue, et le dossier
    doit pouvoir en garder la trace. La liste dit ce qu'il faut préparer, elle ne
    prétend pas régenter ce que le greffe accepte.
    """
    connue = any(p.code == code_piece for p in dossier.pieces)
    if connue:
        pieces = tuple(
            p.model_copy(update={"fournie_le": a_la_date, "empreinte": empreinte})
            if p.code == code_piece
            else p
            for p in dossier.pieces
        )
    else:
        pieces = dossier.pieces + (
            PieceConstitution(
                code=code_piece,
                libelle=f"Pièce complémentaire {code_piece}",
                obligatoire=False,
                fournie_le=a_la_date,
                empreinte=empreinte,
            ),
        )
    return dossier.model_copy(update={"pieces": pieces})


def enregistrer_identifiant(
    dossier: DossierCreation,
    *,
    rccm: tuple[str, date] | None = None,
    niu: tuple[str, date] | None = None,
    patente: tuple[str, date] | None = None,
    cnps: tuple[str, date] | None = None,
) -> DossierCreation:
    """Porte au dossier un identifiant délivré, avec sa date d'obtention.

    Chaque identifiant arrive séparément, souvent à plusieurs semaines d'écart :
    la signature les prend donc un par un plutôt que d'exiger un objet complet
    qu'on n'a jamais sous la main.
    """
    actuelle = dossier.immatriculation
    mise_a_jour: dict[str, object] = {}
    for nom, couple, champ_date in (
        ("rccm", rccm, "rccm_obtenu_le"),
        ("niu", niu, "niu_obtenu_le"),
        ("patente", patente, "patente_obtenue_le"),
        ("cnps", cnps, "cnps_obtenu_le"),
    ):
        if couple is not None:
            mise_a_jour[nom] = couple[0]
            mise_a_jour[champ_date] = couple[1]
    if not mise_a_jour:
        return dossier
    return dossier.model_copy(
        update={"immatriculation": actuelle.model_copy(update=mise_a_jour)}
    )


def avancer(
    dossier: DossierCreation,
    vers: EtapeCreation,
    a_la_date: date,
    *,
    par: str | None = None,
    commentaire: str | None = None,
) -> DossierCreation:
    """Fait franchir une étape au dossier, ou refuse en disant pourquoi.

    ─────────────────────────────────────────────────────────────────────────
    TROIS CONDITIONS, ET UNE SEULE EST STRUCTURELLE

    1. **La transition doit être ouverte** — on n'avance que d'un cran, on
       n'ouvre pas un état terminal, on ne revient pas en arrière. C'est le
       tunnel lui-même.
    2. **La constitution doit être achevée pour déposer.** Un dossier incomplet
       présenté au guichet revient, et il revient trois jours plus tard, quand le
       fondateur a déjà annoncé sa date à son banquier.
    3. **RCCM et NIU doivent être obtenus pour livrer.** Livrer sans eux
       n'aurait pas d'objet : il n'y a rien à remettre.

    La conversion, elle, n'est pas franchie ici. Elle appartient à
    `application/conversion.py`, parce qu'elle ne modifie pas seulement ce
    dossier : elle fait naître une entreprise au portefeuille.
    ─────────────────────────────────────────────────────────────────────────
    """
    if vers not in etapes_ouvertes_depuis(dossier.etape):
        ouvertes = ", ".join(sorted(e.value for e in etapes_ouvertes_depuis(dossier.etape)))
        raise TransitionInterdite(
            f"le dossier {dossier.reference} est à l'étape {dossier.etape} : "
            f"{vers} n'est pas atteignable. Ouvertes : {ouvertes or 'aucune (dossier clos)'}."
        )

    if vers is EtapeCreation.DEPOT_CFCE and not dossier.constitution_achevee:
        manquantes = ", ".join(p.libelle for p in dossier.pieces_manquantes)
        raise TransitionInterdite(
            f"le dossier {dossier.reference} ne peut pas être déposé : {manquantes}."
        )

    if vers is EtapeCreation.LIVRAISON and not dossier.immatriculation.immatriculee:
        raise TransitionInterdite(
            f"le dossier {dossier.reference} n'a pas encore ses deux identifiants "
            "essentiels (RCCM et NIU) : il n'y a rien à livrer."
        )

    if vers is EtapeCreation.ABANDONNE:
        raise TransitionInterdite(
            "un abandon porte un motif : employer `abandonner` plutôt que `avancer`."
        )

    return dossier.model_copy(
        update={
            "etape": vers,
            "jalons": dossier.jalons
            + (Jalon(etape=vers, survenu_le=a_la_date, par=par, commentaire=commentaire),),
        }
    )


def abandonner(
    dossier: DossierCreation, motif: str, a_la_date: date, *, par: str | None = None
) -> DossierCreation:
    """Ferme un dossier sans immatriculation, en disant pourquoi.

    Le motif est exigé, et c'est tout l'objet de cette fonction séparée : le
    pipeline sert à savoir pourquoi les dossiers sortent, pas seulement combien.
    « Trop cher », « parti chez un concurrent » et « projet reporté » appellent
    trois réponses commerciales différentes.
    """
    if not motif.strip():
        raise TransitionInterdite("un abandon porte un motif écrit.")
    if dossier.clos:
        raise TransitionInterdite(
            f"le dossier {dossier.reference} est déjà clos ({dossier.etape})."
        )
    return dossier.model_copy(
        update={
            "etape": EtapeCreation.ABANDONNE,
            "motif_abandon": motif,
            "jalons": dossier.jalons
            + (
                Jalon(
                    etape=EtapeCreation.ABANDONNE,
                    survenu_le=a_la_date,
                    par=par,
                    commentaire=motif,
                ),
            ),
        }
    )


def depasse_le_delai_annonce(
    dossier: DossierCreation, parametres: ServiceParametres, a_la_date: date
) -> bool:
    """Le dossier est-il au guichet depuis plus longtemps que le délai annoncé ?

    Sert la relance, pas le blocage : le délai du CFCE est annoncé, pas opposable.
    Un paramètre absent rend `False` — mieux vaut ne pas relancer que relancer sur
    un délai imaginaire.
    """
    if dossier.etape is not EtapeCreation.DEPOT_CFCE:
        return False
    depose_le = dossier.depuis_le(EtapeCreation.DEPOT_CFCE)
    if depose_le is None:
        return False
    try:
        delai = int(parametres.valeur_numerique("CFCE_DELAI_ANNONCE_JOURS", a_la_date))
    except (ParametreInconnu, AucuneVersionApplicable):
        return False
    return a_la_date > depose_le + timedelta(days=delai)
