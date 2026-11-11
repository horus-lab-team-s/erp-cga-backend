"""Le bilan et le compte de résultat du Système minimal de trésorerie (11 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE

La DSF d'un adhérent à l'IGS se dépose au modèle officiel « SMT » (décret n° 2019/262,
art. 6). Sa feuille « MOD BILAN » n'a pas de codes REF : un bilan simplifié de neuf lignes
et un compte de résultat de TRÉSORERIE, ramené au résultat par trois variations.

Les adhérents tiennent pourtant une comptabilité d'engagement. Les encaissements et les
décaissements se reconstituent donc à partir des écritures (voir
`Docs/dsf-smt-regle-de-passage.md`, dont ce module tranche les questions ouvertes).

LA RÈGLE DE SIGNE, LUE DANS LE MODÈLE LUI-MÊME

Le modèle écrit « RÉSULTAT EXERCICE (G = C − D + E − F) », avec « − variations des
stocks », « − variations des créances », « + variations des dettes ». Pour que G retombe
sur le résultat d'engagement, chaque variation se porte en **N-1 moins N** : un stock qui
monte donne une variation négative, que « − D » ajoute au résultat. C'est la seule lecture
qui rende la formule juste ; elle est vérifiée à chaque fichier (voir le contrôle).

LE CONTRÔLE QUI REND LA RÈGLE VÉRIFIABLE

G doit égaler le résultat comptable de l'exercice (classes 6, 7 et 8). Sinon, aucun fichier
ne sort et l'écart est nommé : il signale une écriture de stock qui ne passe pas par
603 ou 73, ou un compte sans ligne.

⚠️ À FAIRE CONFIRMER par l'expert-comptable du cabinet avant le premier dépôt réel, comme
la table du modèle Normal (`etats_dgi.py`).
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal

__all__ = ["EtatsSmt", "etablir_les_etats_smt"]

ZERO = Decimal(0)

#: Les lignes du bilan SMT (feuille « MOD BILAN »), dans l'ordre du modèle.
LIGNES_ACTIF = ("immobilisations", "stocks", "clients_et_debiteurs", "caisse", "banque")
LIGNES_PASSIF = ("compte_exploitant", "resultat", "emprunt", "fournisseurs_et_crediteurs")
#: Le compte de résultat, dans l'ordre du modèle.
LIGNES_RESULTAT = (
    "recettes_ventes",
    "autres_recettes",
    "depenses_achats",
    "depenses_loyers",
    "depenses_salaires",
    "depenses_impots",
    "charges_interets",
    "autres_depenses",
    "variation_stocks",
    "variation_creances",
    "variation_dettes",
    "dotations",
)


def _commence(compte: str, prefixes: Iterable[str]) -> bool:
    return any(compte.startswith(p) for p in prefixes)


@dataclass(frozen=True)
class EtatsSmt:
    actif: dict[str, Decimal]
    passif: dict[str, Decimal]
    resultat: dict[str, Decimal]
    #: A, B, C et G du modèle ; les totaux du bilan.
    total_recettes: Decimal
    total_depenses: Decimal
    solde_de_tresorerie: Decimal
    resultat_g: Decimal
    resultat_comptable: Decimal
    total_actif: Decimal
    total_passif: Decimal
    comptes_non_couverts: list[str] = field(default_factory=list)

    @property
    def equilibre(self) -> bool:
        return self.total_actif == self.total_passif

    @property
    def concorde(self) -> bool:
        """G égale le résultat comptable : la règle de passage tient pour ce dossier."""
        return self.resultat_g == self.resultat_comptable


def _bilan(soldes: dict[str, Decimal]) -> tuple[dict[str, Decimal], dict[str, Decimal], list[str]]:
    actif = dict.fromkeys(LIGNES_ACTIF, ZERO)
    passif = dict.fromkeys(LIGNES_PASSIF, ZERO)
    non_couverts: list[str] = []
    for compte, solde in sorted(soldes.items()):
        if solde == 0:
            continue
        classe = compte[0]
        if classe == "2":
            actif["immobilisations"] += solde  # 28 et 29, créditeurs, viennent en déduction
        elif classe == "3":
            actif["stocks"] += solde
        elif classe == "4":
            if compte.startswith("49"):
                actif["clients_et_debiteurs"] += solde
            elif solde > 0:
                actif["clients_et_debiteurs"] += solde
            else:
                passif["fournisseurs_et_crediteurs"] -= solde
        elif classe == "5":
            if compte.startswith("57"):
                actif["caisse"] += solde
            else:
                actif["banque"] += solde  # « en + ou en − »
        elif classe == "1":
            if _commence(compte, ("16", "17")):
                passif["emprunt"] -= solde
            else:
                passif["compte_exploitant"] -= solde
        elif classe not in "678":
            non_couverts.append(compte)
    return actif, passif, non_couverts


def _total(
    soldes: dict[str, Decimal], prefixes: Iterable[str], sauf: Iterable[str] = ()
) -> Decimal:
    return sum(
        (s for c, s in soldes.items() if _commence(c, prefixes) and not _commence(c, sauf)),
        ZERO,
    )


#: Comptes de résultat et leur ligne. Un compte de classe 6, 7 ou 8 hors de ces préfixes
#: est rendu dans `comptes_non_couverts`, jamais rangé d'office.
_PRODUITS_AUTRES = ("71", "72", "75", "77", "78", "79", "82", "84", "86", "88")
_DEPENSES_AUTRES = ("61", "62", "63", "65", "81", "83", "87")
_DOTATIONS = ("68", "69", "85")
_COUVERTS = (
    "70",
    "73",
    *_PRODUITS_AUTRES,
    "60",
    "622",
    *_DEPENSES_AUTRES,
    "64",
    "66",
    "67",
    "89",
    *_DOTATIONS,
)


def _creances(soldes: dict[str, Decimal]) -> Decimal:
    """Les clients, nets des avances reçues (41)."""
    return _total(soldes, ("41",))


def _dettes(soldes: dict[str, Decimal]) -> Decimal:
    """Les fournisseurs d'exploitation, en positif (40)."""
    return -_total(soldes, ("40",))


def _stocks(soldes: dict[str, Decimal]) -> Decimal:
    """Les stocks bruts : la dépréciation (39) passe par 659 et 759, déjà au résultat."""
    return _total(soldes, ("3",), sauf=("39",))


def etablir_les_etats_smt(soldes: dict[str, Decimal], *, ouverture: dict[str, Decimal]) -> EtatsSmt:
    """`soldes` : par compte, débit moins crédit à la clôture (écritures validées de
    l'exercice, à-nouveaux compris). `ouverture` : les soldes d'ouverture (à-nouveaux)."""
    actif, passif, non_couverts = _bilan(soldes)

    resultat_comptable = -_total(soldes, ("6", "7", "8"))
    passif["resultat"] = resultat_comptable
    for compte, solde in sorted(soldes.items()):
        if solde and compte[0] in "678" and not _commence(compte, _COUVERTS):
            non_couverts.append(compte)

    # N-1 moins N : la lecture qui rend juste G = C − D + E − F (voir l'en-tête).
    variation_stocks = _stocks(ouverture) - _stocks(soldes)
    variation_creances = _creances(ouverture) - _creances(soldes)
    variation_dettes = _dettes(ouverture) - _dettes(soldes)

    ventes = -_total(soldes, ("70",))
    achats = _total(soldes, ("60",), sauf=("603",))
    resultat = {
        # Ce qui a été encaissé : les ventes, moins ce que les clients doivent de plus.
        "recettes_ventes": ventes + variation_creances,
        "autres_recettes": -_total(soldes, _PRODUITS_AUTRES),
        # Ce qui a été payé : les achats, moins ce qui reste dû de plus aux fournisseurs.
        "depenses_achats": achats + variation_dettes,
        "depenses_loyers": _total(soldes, ("622",)),
        "depenses_salaires": _total(soldes, ("66",)),
        "depenses_impots": _total(soldes, ("64", "89")),
        "charges_interets": _total(soldes, ("67",)),
        "autres_depenses": _total(soldes, _DEPENSES_AUTRES, sauf=("622",)),
        "variation_stocks": variation_stocks,
        "variation_creances": variation_creances,
        "variation_dettes": variation_dettes,
        "dotations": _total(soldes, _DOTATIONS),
    }
    total_recettes = resultat["recettes_ventes"] + resultat["autres_recettes"]
    total_depenses = sum((resultat[k] for k in LIGNES_RESULTAT[2:8]), ZERO)
    solde = total_recettes - total_depenses
    d = resultat["variation_stocks"] + resultat["variation_creances"]
    resultat_g = solde - d + resultat["variation_dettes"] - resultat["dotations"]
    # La production stockée (73) n'a pas de ligne : elle est la variation des stocks
    # produits, déjà portée par « variations des stocks ».
    return EtatsSmt(
        actif=actif,
        passif=passif,
        resultat=resultat,
        total_recettes=total_recettes,
        total_depenses=total_depenses,
        solde_de_tresorerie=solde,
        resultat_g=resultat_g,
        resultat_comptable=resultat_comptable,
        total_actif=sum(actif.values(), ZERO),
        total_passif=sum(passif.values(), ZERO),
        comptes_non_couverts=sorted(set(non_couverts)),
    )
