"""Le plan de correspondance balance → postes de la liasse.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE PLAN VIT DANS LE DOMAINE ET NON AU RÉFÉRENTIEL

Même raisonnement que pour la checklist de constitution : le référentiel porte ce
qu'une loi de finances change chaque année — taux, seuils, délais. La
correspondance entre les classes du plan comptable SYSCOHADA et les postes de la
liasse ne change pas avec la loi de finances : elle change quand le droit
comptable OHADA est révisé, c'est-à-dire une fois par décennie.

Y placer une structure imbriquée — une liste de préfixes par poste — porterait la
complexité au mauvais endroit, le référentiel ne sachant porter que des valeurs
datées. **Le seuil qui décide du système**, lui, reste au référentiel : c'est un
montant, une date d'entrée en vigueur, un fondement.

⚠️ CE PLAN EST UNE SIMPLIFICATION, ET IL FAUT LE SAVOIR

La liasse SYSCOHADA du Système Normal compte plus de deux cents postes répartis
sur quatre états. Ce plan en retient une trentaine — ceux qui structurent le bilan
et le compte de résultat d'une TPE-PME. Il produit un **bilan équilibré et un
résultat juste** ; il ne produit pas la liasse complète, et notamment ni le TAFIRE
ni les annexes.

C'est délibéré pour une première version : mieux vaut trente postes justes et
vérifiables qu'une liasse complète dont personne n'a contrôlé la moitié. Ce que le
plan ne couvre pas, `postes_non_couverts` le **signale** au lieu de le laisser
disparaître — c'est le point qui distingue une simplification d'une erreur.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from app.contextes.cloture.domaine.entites import PosteLiasse, SensPoste, SystemeDsf

__all__ = [
    "COMPTES_BIDIRECTIONNELS",
    "PLAN_LIASSE",
    "poste_du_compte",
    "postes_du_systeme",
]


#: Le plan de correspondance. L'ordre est celui de lecture d'un état — actif du
#: haut de bilan vers le bas, puis passif, puis charges, puis produits.
#:
#: Les préfixes suivent les classes SYSCOHADA :
#:   1 ressources durables · 2 actif immobilisé · 3 stocks · 4 tiers
#:   5 trésorerie · 6 charges · 7 produits
PLAN_LIASSE: tuple[PosteLiasse, ...] = (
    # ── Actif ────────────────────────────────────────────────────────────────
    PosteLiasse(
        code="AI",
        libelle="Immobilisations incorporelles",
        sens=SensPoste.ACTIF,
        prefixes=("21",),
    ),
    PosteLiasse(
        code="AQ",
        libelle="Immobilisations corporelles",
        sens=SensPoste.ACTIF,
        prefixes=("22", "23", "24"),
    ),
    PosteLiasse(
        code="AU",
        libelle="Immobilisations financières",
        sens=SensPoste.ACTIF,
        prefixes=("26", "27"),
    ),
    # ⚠️ Les amortissements sont au crédit d'un compte de classe 2 (28x). Leur
    # solde est donc créditeur et vient **en diminution** de l'actif. Les traiter
    # comme un poste d'actif ordinaire gonflerait le bilan du double de leur
    # montant — c'est l'erreur qui déséquilibre le plus souvent une liasse
    # assemblée à la main.
    PosteLiasse(
        code="AZ",
        libelle="Amortissements et dépréciations",
        sens=SensPoste.ACTIF,
        prefixes=("28", "29"),
    ),
    PosteLiasse(
        code="BB",
        libelle="Stocks et en-cours",
        sens=SensPoste.ACTIF,
        prefixes=("3",),
    ),
    PosteLiasse(
        code="BI",
        libelle="Clients et comptes rattachés",
        sens=SensPoste.ACTIF,
        prefixes=(),
    ),
    PosteLiasse(
        code="BJ",
        libelle="Autres créances",
        sens=SensPoste.ACTIF,
        prefixes=("45", "46", "47"),
    ),
    PosteLiasse(
        code="BS",
        libelle="Fournisseurs débiteurs — avances versées",
        sens=SensPoste.ACTIF,
        prefixes=(),
    ),
    PosteLiasse(
        code="BT",
        libelle="Trésorerie — actif",
        sens=SensPoste.ACTIF,
        prefixes=("58",),
    ),
    # ── Passif ───────────────────────────────────────────────────────────────
    PosteLiasse(
        code="CA",
        libelle="Capital",
        sens=SensPoste.PASSIF,
        prefixes=("10",),
    ),
    PosteLiasse(
        code="CD",
        libelle="Réserves et report à nouveau",
        sens=SensPoste.PASSIF,
        prefixes=("11", "12"),
    ),
    PosteLiasse(
        code="CL",
        libelle="Subventions et provisions réglementées",
        sens=SensPoste.PASSIF,
        prefixes=("14", "15"),
    ),
    PosteLiasse(
        code="DA",
        libelle="Emprunts et dettes financières",
        sens=SensPoste.PASSIF,
        prefixes=("16", "17", "18", "19"),
    ),
    PosteLiasse(
        code="DI",
        libelle="Fournisseurs et comptes rattachés",
        sens=SensPoste.PASSIF,
        prefixes=(),
    ),
    PosteLiasse(
        code="DJ",
        libelle="Dettes fiscales et sociales",
        sens=SensPoste.PASSIF,
        prefixes=(),
    ),
    PosteLiasse(
        code="DH",
        libelle="Clients créditeurs — avances reçues",
        sens=SensPoste.PASSIF,
        prefixes=(),
    ),
    PosteLiasse(
        code="DQ",
        libelle="Trésorerie — passif",
        sens=SensPoste.PASSIF,
        prefixes=("56",),
    ),
    # ── Charges ──────────────────────────────────────────────────────────────
    PosteLiasse(
        code="RA",
        libelle="Achats de marchandises",
        sens=SensPoste.CHARGE,
        prefixes=("601", "6031"),
    ),
    PosteLiasse(
        code="RB",
        libelle="Achats de matières et fournitures",
        sens=SensPoste.CHARGE,
        prefixes=("602", "604", "605", "608", "6032", "6033"),
    ),
    PosteLiasse(
        code="RC",
        libelle="Transports",
        sens=SensPoste.CHARGE,
        prefixes=("61",),
    ),
    PosteLiasse(
        code="RD",
        libelle="Services extérieurs",
        sens=SensPoste.CHARGE,
        prefixes=("62", "63"),
    ),
    PosteLiasse(
        code="RE",
        libelle="Impôts et taxes",
        sens=SensPoste.CHARGE,
        prefixes=("64",),
    ),
    PosteLiasse(
        code="RF",
        libelle="Autres charges",
        sens=SensPoste.CHARGE,
        prefixes=("65",),
    ),
    PosteLiasse(
        code="RG",
        libelle="Charges de personnel",
        sens=SensPoste.CHARGE,
        prefixes=("66",),
    ),
    PosteLiasse(
        code="RH",
        libelle="Frais financiers",
        sens=SensPoste.CHARGE,
        prefixes=("67",),
    ),
    PosteLiasse(
        code="RI",
        libelle="Dotations aux amortissements et provisions",
        sens=SensPoste.CHARGE,
        prefixes=("68", "69"),
    ),
    PosteLiasse(
        code="RK",
        libelle="Impôt sur le résultat",
        sens=SensPoste.CHARGE,
        prefixes=("89",),
    ),
    # ── Produits ─────────────────────────────────────────────────────────────
    PosteLiasse(
        code="TA",
        libelle="Ventes de marchandises",
        sens=SensPoste.PRODUIT,
        prefixes=("701",),
    ),
    PosteLiasse(
        code="TB",
        libelle="Ventes de produits et services",
        sens=SensPoste.PRODUIT,
        prefixes=("702", "703", "704", "705", "706", "707", "708"),
    ),
    PosteLiasse(
        code="TF",
        libelle="Production stockée et immobilisée",
        sens=SensPoste.PRODUIT,
        prefixes=("72", "73"),
    ),
    PosteLiasse(
        code="TH",
        libelle="Subventions d'exploitation et autres produits",
        sens=SensPoste.PRODUIT,
        prefixes=("71", "75"),
    ),
    PosteLiasse(
        code="TK",
        libelle="Produits financiers",
        sens=SensPoste.PRODUIT,
        prefixes=("77",),
    ),
    PosteLiasse(
        code="TL",
        libelle="Reprises de provisions et transferts de charges",
        sens=SensPoste.PRODUIT,
        prefixes=("78", "79"),
    ),
)


#: Les comptes de tiers dont le **sens du solde** décide du côté du bilan.
#:
#: ─────────────────────────────────────────────────────────────────────────────
#: UN NUMÉRO DE COMPTE NE DIT PAS DE QUEL CÔTÉ DU BILAN IL FIGURE
#:
#: C'est l'erreur que ce plan a d'abord commise : ranger « 44 État » au passif
#: parce que c'est ordinairement une dette. Ordinairement, oui — mais un crédit
#: de TVA reportable est un compte 44 **débiteur**, donc une créance sur l'État,
#: et le porter au passif le compterait du mauvais côté.
#:
#: De même, un fournisseur à qui l'on a versé une avance est un compte 40
#: débiteur, donc une créance ; un client qui a payé d'avance est un 41
#: créditeur, donc une dette.
#:
#: La règle n'est donc pas « quel numéro » mais « quel sens », et elle s'applique
#: à toute la classe 4. Chaque entrée donne le poste si le solde est
#: **débiteur**, puis s'il est **créditeur**.
#: ─────────────────────────────────────────────────────────────────────────────
COMPTES_BIDIRECTIONNELS: dict[str, tuple[str, str]] = {
    "40": ("BS", "DI"),
    "41": ("BI", "DH"),
    "42": ("BJ", "DJ"),
    "43": ("BJ", "DJ"),
    "44": ("BJ", "DJ"),
    # La trésorerie l'est aussi : un compte bancaire créditeur est un
    # **découvert**, donc une dette, et non un actif de signe négatif. L'oubli
    # de ce cas fait apparaître un actif là où il y a un concours bancaire —
    # et l'entreprise paraît d'autant plus solide qu'elle est plus à découvert.
    "52": ("BT", "DQ"),
    "53": ("BT", "DQ"),
    "57": ("BT", "DQ"),
}


def postes_du_systeme(systeme: SystemeDsf) -> tuple[PosteLiasse, ...]:
    """Les postes existant dans ce système de présentation."""
    return tuple(p for p in PLAN_LIASSE if p.systeme is None or p.systeme is systeme)


def poste_du_compte(
    compte: str, systeme: SystemeDsf, *, solde_debiteur: bool = True
) -> PosteLiasse | None:
    """Le poste qui accueille ce compte, ou `None` s'il n'est pas couvert.

    ─────────────────────────────────────────────────────────────────────────
    LE PRÉFIXE LE PLUS LONG L'EMPORTE, ET LE `None` EST UNE RÉPONSE

    « 4011 Fournisseurs » relève de DI par « 40 », et non de BJ par « 4 » : le
    préfixe le plus spécifique gagne. Sans cet arbitrage, l'ordre de déclaration
    du plan déciderait du résultat, et ajouter un poste en déplacerait d'autres.

    Un compte non couvert rend `None` plutôt que d'être rangé dans un poste
    « divers ». Le fourre-tout est confortable et faux : il équilibre le bilan en
    dissimulant exactement ce qu'il faudrait voir. Le `None` remonte à
    `postes_non_couverts`, et la liasse le signale.

    `solde_debiteur` tranche pour les comptes de tiers, dont le numéro ne dit pas
    le côté du bilan — voir `COMPTES_BIDIRECTIONNELS`.
    ─────────────────────────────────────────────────────────────────────────
    """
    par_code = {p.code: p for p in postes_du_systeme(systeme)}

    # Le sens d'abord : il porte une information que le numéro de compte n'a pas.
    bidirectionnel = _prefixe_bidirectionnel(compte)
    if bidirectionnel is not None:
        debiteur, crediteur = COMPTES_BIDIRECTIONNELS[bidirectionnel]
        return par_code.get(debiteur if solde_debiteur else crediteur)

    candidats = [p for p in par_code.values() if p.prefixes and p.couvre(compte)]
    if not candidats:
        return None
    return max(candidats, key=lambda p: p.longueur_du_prefixe(compte))


def _prefixe_bidirectionnel(compte: str) -> str | None:
    """Le plus long préfixe bidirectionnel couvrant ce compte, s'il y en a un."""
    correspondants = [p for p in COMPTES_BIDIRECTIONNELS if compte.startswith(p)]
    return max(correspondants, key=len) if correspondants else None
