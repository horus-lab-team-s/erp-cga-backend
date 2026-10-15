"""Le bilan et le compte de résultat aux codes REF officiels de la DSF (15 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE

Le modèle Excel officiel de la DGI (« DSF Normal », SYSCOHADA révisé) repère chaque
ligne du bilan et du compte de résultat par un code REF (AD, AE… BZ ; CA… DZ ; TA… XI),
dans des cellules fixes, SANS AUCUNE FORMULE : totaux et soldes intermédiaires y sont
des valeurs à fournir.

Les postes de la liasse interne (`postes.py`) ressemblaient à ces codes sans en avoir
le sens : AI y désignait les immobilisations incorporelles, quand le modèle officiel
réserve AI aux corporelles ; AZ y portait les amortissements, quand AZ est le total de
l'actif immobilisé. Remplir le modèle avec eux aurait posé des montants justes sur de
mauvaises lignes. Ce module porte la table OFFICIELLE.

LES RÈGLES
* Une ligne d'actif a un BRUT, des AMORTISSEMENTS ET DÉPRÉCIATIONS, un NET. Les comptes
  28 et 29 se rattachent à l'actif qu'ils corrigent par la règle miroir du plan
  SYSCOHADA (2811 corrige 211, 2918 corrige 218) ; 39 corrige 3, 49 corrige 4, 59 corrige 5.
* Les comptes de tiers et de trésorerie (40 à 47, 52, 53) vont à l'actif ou au passif
  selon le SENS DE LEUR SOLDE, compte par compte.
* Les totaux (AD, AI, AQ, AZ, BK, BT, BZ ; CP, DD, DF, DP, DT, DZ) et les soldes
  intermédiaires (XA à XI) se calculent ici, selon les définitions du modèle.
* Le résultat de l'exercice (CJ) est celui du compte de résultat (XI). Un solde au
  compte 13 au moment de l'arrêté est un résultat antérieur non affecté : il va au
  report à nouveau (CH), et c'est signalé.
* Ce qui ne trouve pas de ligne n'est JAMAIS rangé d'office : il est rendu dans
  `comptes_non_couverts`, comme pour la liasse interne.

⚠️ TABLE À FAIRE VALIDER : elle suit la correspondance comptes / postes du SYSCOHADA
révisé (Acte uniforme de 2017) et les libellés du modèle DGI ; l'expert-comptable du
cabinet la confirme avant le premier dépôt réel.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal

__all__ = [
    "ACTIF",
    "COMPTE_DE_RESULTAT",
    "PASSIF",
    "EtatsDgi",
    "LigneActif",
    "etablir_les_etats_dgi",
]

ZERO = Decimal(0)

# ── Les lignes de détail : code REF, libellé, préfixes de comptes (le plus long gagne) ──

#: (code, libellé, préfixes). Les comptes de correction (28, 29, 39, 49, 59) n'y figurent
#: pas : ils suivent l'actif qu'ils corrigent.
ACTIF: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("AE", "Frais de développement et de prospection", ("211", "2181", "2191")),
    ("AF", "Brevets, licences, logiciels et droits similaires", ("212", "213", "214", "2193")),
    ("AG", "Fonds commercial et droit au bail", ("215", "216")),
    ("AH", "Autres immobilisations incorporelles", ("217", "218", "219")),
    ("AJ", "Terrains", ("22",)),
    ("AK", "Bâtiments", ("231", "232", "233", "237", "2391")),
    ("AL", "Aménagements, agencements et installations", ("234", "235", "238", "2392", "2393")),
    ("AM", "Matériel, mobilier et actifs biologiques", ("24",)),
    ("AN", "Matériel de transport", ("245", "2495")),
    ("AP", "Avances et acomptes versés sur immobilisations", ("25",)),
    ("AR", "Titres de participation", ("26",)),
    ("AS", "Autres immobilisations financières", ("27",)),
    ("BA", "Actif circulant HAO", ("485", "488")),
    ("BB", "Stocks et encours", ("31", "32", "33", "34", "35", "36", "37", "38")),
    ("BH", "Fournisseurs, avances versées", ("409",)),
    ("BI", "Clients", ("41",)),
    ("BJ", "Autres créances", ("185", "42", "43", "44", "45", "46", "47")),
    ("BQ", "Titres de placement", ("50",)),
    ("BR", "Valeurs à encaisser", ("51",)),
    ("BS", "Banques, chèques postaux, caisse et assimilés", ("52", "53", "54", "55", "57", "58")),
    ("BU", "Écart de conversion actif", ("478",)),
)

PASSIF: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("CA", "Capital", ("101", "102", "103", "104")),
    ("CB", "Apporteurs capital non appelé", ("109",)),
    ("CD", "Primes liées au capital social", ("105",)),
    ("CE", "Écarts de réévaluation", ("106",)),
    ("CF", "Réserves indisponibles", ("111", "112", "113")),
    ("CG", "Réserves libres", ("118",)),
    ("CH", "Report à nouveau", ("12",)),
    ("CL", "Subventions d'investissement", ("14",)),
    ("CM", "Provisions réglementées", ("15",)),
    ("DA", "Emprunts et dettes financières diverses", ("16", "181", "182", "183", "184")),
    ("DB", "Dettes de location acquisition", ("17",)),
    ("DC", "Provisions pour risques et charges", ("19",)),
    ("DH", "Dettes circulantes HAO", ("481", "482", "484", "4998")),
    ("DI", "Clients, avances reçues", ("419", "41")),
    ("DJ", "Fournisseurs d'exploitation", ("40",)),
    ("DK", "Dettes fiscales et sociales", ("42", "43", "44")),
    ("DM", "Autres dettes", ("185", "45", "46", "47")),
    ("DN", "Provisions pour risques à court terme", ("499", "599")),
    ("DQ", "Banques, crédits d'escompte", ("564", "565")),
    ("DR", "Banques, établissements financiers et crédits de trésorerie", ("52", "53", "56")),
    ("DY", "Écart de conversion passif", ("479",)),
)

#: (code, libellé, préfixes, nature) : « C » charge, « P » produit.
COMPTE_DE_RESULTAT: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    ("TA", "Ventes de marchandises", ("701",), "P"),
    ("RA", "Achats de marchandises", ("601",), "C"),
    ("RB", "Variation de stocks de marchandises", ("6031",), "C"),
    ("TB", "Ventes de produits fabriqués", ("702", "703", "704"), "P"),
    ("TC", "Travaux, services vendus", ("705", "706"), "P"),
    ("TD", "Produits accessoires", ("707",), "P"),
    ("TE", "Production stockée (ou déstockage)", ("73",), "P"),
    ("TF", "Production immobilisée", ("72",), "P"),
    ("TG", "Subventions d'exploitation", ("71",), "P"),
    ("TH", "Autres produits", ("75",), "P"),
    ("TI", "Transferts de charges d'exploitation", ("781",), "P"),
    ("RC", "Achats de matières premières et fournitures liées", ("602",), "C"),
    ("RD", "Variation de stocks de matières premières", ("6032",), "C"),
    ("RE", "Autres achats", ("604", "605", "608"), "C"),
    ("RF", "Variation de stocks d'autres approvisionnements", ("6033",), "C"),
    ("RG", "Transports", ("61",), "C"),
    ("RH", "Services extérieurs", ("62", "63"), "C"),
    ("RI", "Impôts et taxes", ("64",), "C"),
    ("RJ", "Autres charges", ("65",), "C"),
    ("RK", "Charges de personnel", ("66",), "C"),
    ("TJ", "Reprises d'amortissements, provisions et dépréciations", ("791", "798", "799"), "P"),
    ("RL", "Dotations aux amortissements, provisions et dépréciations", ("681", "691"), "C"),
    ("TK", "Revenus financiers et assimilés", ("77",), "P"),
    ("TL", "Reprises de provisions et dépréciations financières", ("797",), "P"),
    ("TM", "Transferts de charges financières", ("787",), "P"),
    ("RM", "Frais financiers et charges assimilées", ("67",), "C"),
    ("RN", "Dotations aux provisions et dépréciations financières", ("697",), "C"),
    ("TN", "Produits des cessions d'immobilisations", ("82",), "P"),
    ("TO", "Autres produits HAO", ("84", "86", "88"), "P"),
    ("RO", "Valeurs comptables des cessions d'immobilisations", ("81",), "C"),
    ("RP", "Autres charges HAO", ("83", "85"), "C"),
    ("RQ", "Participation des travailleurs", ("87",), "C"),
    ("RS", "Impôt sur le résultat", ("89",), "C"),
)

#: Les lignes qu'un amortissement (28) ou une dépréciation (29) peut corriger.
_IMMOBILISATIONS_AMORTISSABLES = ("AE", "AF", "AG", "AH", "AJ", "AK", "AL", "AM", "AN")
_IMMOBILISATIONS = (*_IMMOBILISATIONS_AMORTISSABLES, "AP", "AR", "AS")

#: Les totaux du bilan, tels que le modèle les définit.
TOTAUX_ACTIF = {
    "AD": ("AE", "AF", "AG", "AH"),
    "AI": ("AJ", "AK", "AL", "AM", "AN"),
    "AQ": ("AR", "AS"),
    "AZ": ("AD", "AI", "AP", "AQ"),
    "BK": ("BA", "BB", "BH", "BI", "BJ"),
    "BT": ("BQ", "BR", "BS"),
    "BZ": ("AZ", "BK", "BT", "BU"),
}
TOTAUX_PASSIF = {
    "CP": ("CA", "CB", "CD", "CE", "CF", "CG", "CH", "CJ", "CL", "CM"),
    "DD": ("DA", "DB", "DC"),
    "DF": ("CP", "DD"),
    "DP": ("DH", "DI", "DJ", "DK", "DM", "DN"),
    "DT": ("DQ", "DR"),
    "DZ": ("DF", "DP", "DT", "DY"),
}


@dataclass(frozen=True)
class LigneActif:
    brut: Decimal = ZERO
    amortissements: Decimal = ZERO

    @property
    def net(self) -> Decimal:
        return self.brut - self.amortissements


@dataclass(frozen=True)
class EtatsDgi:
    actif: dict[str, LigneActif]
    passif: dict[str, Decimal]
    resultat: dict[str, Decimal]
    comptes_non_couverts: tuple[str, ...] = ()
    #: Les comptes qui alimentent chaque ligne de détail : ce qui répond à « d'où vient
    #: ce montant ? » sur la liasse.
    comptes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Ce que le comptable doit savoir avant de déposer (résultat antérieur non affecté…).
    avertissements: tuple[str, ...] = field(default=())

    @property
    def equilibre(self) -> bool:
        return self.actif["BZ"].net == self.passif["DZ"]


def _fcfa(montant: Decimal) -> str:
    """« 800 000 FCFA » : un avertissement se lit, il ne se recopie pas."""
    return f"{int(montant):,} FCFA".replace(",", " ")


def _ligne(compte: str, table: Iterable[tuple]) -> str | None:
    """Le code de la ligne dont le préfixe est le plus long à couvrir le compte."""
    meilleur, longueur = None, 0
    for entree in table:
        for prefixe in entree[2]:
            if compte.startswith(prefixe) and len(prefixe) > longueur:
                meilleur, longueur = entree[0], len(prefixe)
    return meilleur


def _actif_corrige(compte: str) -> str | None:
    """Le compte d'actif qu'un compte de correction corrige (règle miroir SYSCOHADA)."""
    if compte[:2] in ("28", "29"):
        return "2" + compte[2:]
    # 499 et 599 sont des provisions pour risques à court terme (passif), pas des dépréciations.
    if compte[:2] in ("39", "49", "59") and not compte.startswith(("499", "599")):
        return compte[0] + compte[2:]
    return None


def etablir_les_etats_dgi(soldes: dict[str, Decimal]) -> EtatsDgi:
    """`soldes` : par compte, le solde net débit moins crédit, écritures validées de l'exercice.

    Les comptes des classes 1 à 5 font le bilan, 6 à 8 le compte de résultat.
    """
    actif = {code: [ZERO, ZERO] for code, _, _ in ACTIF}
    passif = {code: ZERO for code, _, _ in PASSIF}
    resultat = {code: ZERO for code, _, _, _ in COMPTE_DE_RESULTAT}
    non_couverts: list[str] = []
    avertissements: list[str] = []
    a_rattacher: list[tuple[str, Decimal]] = []
    sources: dict[str, set[str]] = {}

    def noter(code: str, compte: str) -> None:
        sources.setdefault(code, set()).add(compte)

    for compte, solde in sorted(soldes.items()):
        if solde == 0:
            continue
        classe = compte[0]
        if classe in "678":
            code = _ligne(compte, COMPTE_DE_RESULTAT)
            if code is None:
                non_couverts.append(compte)
                continue
            charge = next(e[3] for e in COMPTE_DE_RESULTAT if e[0] == code) == "C"
            resultat[code] += solde if charge else -solde
            noter(code, compte)
            continue
        if compte.startswith("13"):
            # Un résultat antérieur que l'assemblée n'a pas encore affecté.
            passif["CH"] += -solde
            noter("CH", compte)
            avertissements.append(
                f"Le compte {compte} porte un résultat antérieur non affecté "
                f"({_fcfa(-solde)}) : présenté en report à nouveau (CH)."
            )
            continue
        corrige = _actif_corrige(compte)
        if corrige is not None:
            code = _ligne(corrige, ACTIF)
            if code is None and compte in ("28", "29"):
                # Un compte racine ne dit pas quel actif il corrige : décidé après coup.
                a_rattacher.append((compte, solde))
                continue
            if code is None:
                non_couverts.append(compte)
                continue
            # Un amortissement est créditeur : il vient en déduction du brut.
            actif[code][1] += -solde
            noter(code, compte)
            continue
        a_l_actif, au_passif = _ligne(compte, ACTIF), _ligne(compte, PASSIF)
        if classe == "1":
            a_l_actif = None
        if a_l_actif and au_passif:
            # Les deux côtés possibles (tiers, banques, 185) : le sens du solde décide.
            cote = a_l_actif if solde > 0 else None
            if cote is None:
                passif[au_passif] += -solde
                noter(au_passif, compte)
            else:
                actif[cote][0] += solde
                noter(cote, compte)
        elif a_l_actif:
            actif[a_l_actif][0] += solde
            noter(a_l_actif, compte)
        elif au_passif:
            passif[au_passif] += -solde
            noter(au_passif, compte)
        else:
            non_couverts.append(compte)

    # Un amortissement porté au compte racine (28, 29) : le plan de référence n'offre que
    # lui. S'il n'y a qu'une ligne d'immobilisation qu'il puisse corriger, il s'y
    # rattache, et c'est dit ; sinon il reste non couvert, et le dépôt attend la
    # ventilation (281, 283, 284…).
    for compte, solde in a_rattacher:
        candidates = _IMMOBILISATIONS_AMORTISSABLES if compte == "28" else _IMMOBILISATIONS
        portees = [c for c in candidates if actif[c][0] > 0]
        if len(portees) == 1:
            actif[portees[0]][1] += -solde
            noter(portees[0], compte)
            avertissements.append(
                f"Le compte {compte} n'est pas ventilé par nature : ses {_fcfa(-solde)} sont "
                f"rattachés à la seule immobilisation du bilan ({portees[0]}). Ventilez-le "
                "(281, 283, 284…) pour lever cet avertissement."
            )
        else:
            non_couverts.append(compte)

    # Le résultat de l'exercice, puis les soldes intermédiaires (définitions du modèle).
    r = resultat
    r["XA"] = r["TA"] - r["RA"] - r["RB"]
    r["XB"] = r["TA"] + r["TB"] + r["TC"] + r["TD"]
    r["XC"] = (
        r["XA"]
        + r["TB"]
        + r["TC"]
        + r["TD"]
        + r["TE"]
        + r["TF"]
        + r["TG"]
        + r["TH"]
        + r["TI"]
        - r["RC"]
        - r["RD"]
        - r["RE"]
        - r["RF"]
        - r["RG"]
        - r["RH"]
        - r["RI"]
        - r["RJ"]
    )
    r["XD"] = r["XC"] - r["RK"]
    r["XE"] = r["XD"] + r["TJ"] - r["RL"]
    r["XF"] = r["TK"] + r["TL"] + r["TM"] - r["RM"] - r["RN"]
    r["XG"] = r["XE"] + r["XF"]
    r["XH"] = r["TN"] + r["TO"] - r["RO"] - r["RP"]
    r["XI"] = r["XG"] + r["XH"] - r["RQ"] - r["RS"]
    passif["CJ"] = r["XI"]

    lignes_actif = {code: LigneActif(brut=v[0], amortissements=v[1]) for code, v in actif.items()}
    for total, membres in TOTAUX_ACTIF.items():
        lignes_actif[total] = LigneActif(
            brut=sum((lignes_actif[m].brut for m in membres), ZERO),
            amortissements=sum((lignes_actif[m].amortissements for m in membres), ZERO),
        )
    for total, membres in TOTAUX_PASSIF.items():
        passif[total] = sum((passif[m] for m in membres), ZERO)

    return EtatsDgi(
        actif=lignes_actif,
        passif=passif,
        resultat=r,
        comptes_non_couverts=tuple(non_couverts),
        comptes={code: tuple(sorted(c)) for code, c in sources.items()},
        avertissements=tuple(avertissements),
    )
