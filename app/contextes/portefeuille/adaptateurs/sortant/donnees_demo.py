"""Le portefeuille de démonstration — six dossiers, et leurs statuts datés.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE JEU EXISTE ICI ET PAS AILLEURS

Le contexte D porte depuis longtemps six adhérents dans ses factures de
démonstration, mais il ne les connaît que par une dénomination et un NIU : le
moteur de conformité n'a pas besoin d'en savoir plus. Le portefeuille, lui, doit
en connaître le régime, le centre de rattachement, l'adhésion et les exercices —
et surtout leur **histoire**, puisque tout y est daté.

Ce module est donc la source unique de vérité sur *qui sont* les six adhérents.
Les factures de démonstration du contexte D s'y alignent, et non l'inverse.

UNE DIVERGENCE CORRIGÉE

Le jeu de démonstration du frontend classait ETS TCHOUMBA & FILS et CABINET
NGUEMA CONSEIL au régime de l'impôt général synthétique, tandis que celui du
backend attribuait le régime du réel à tous les destinataires. Les deux jeux
racontaient donc deux portefeuilles différents, et l'écart se voyait précisément
là où il compte : une facture reçue par un adhérent au synthétique n'ouvre aucun
droit à déduction de TVA, et la règle `FAC-ACH-007` n'a alors rien à dire.

Le portefeuille ci-dessous tranche : **deux adhérents sur six sont au
synthétique**, ce qui correspond à la proportion annoncée par le cabinet et donne
au jeu de démonstration un cas de chaque sorte.

⚠️ Données fictives. Les NIU et RCCM respectent les formats du référentiel mais ne
désignent aucune entreprise réelle.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.contextes.portefeuille.domaine.entites import (
    Adhesion,
    CentreRattachement,
    Dirigeant,
    Entreprise,
    Exercice,
    FormeJuridique,
    MandatDeclaratif,
    RegimeFiscal,
    StatutRattachement,
    StatutRegime,
)
from app.contextes.portefeuille.domaine.temporel import MotifChangement

__all__ = ["PORTEFEUILLE_DEMO", "entreprise_demo"]


def _exercices(premier: int, dernier: int) -> list[Exercice]:
    """Exercices civils, clos jusqu'à l'avant-dernier.

    L'exercice courant reste ouvert : c'est celui sur lequel la plateforme
    travaille, et le déclarer clos ferait disparaître tout le travail en cours.
    """
    return [
        Exercice(
            libelle=str(annee),
            ouverture=date(annee, 1, 1),
            cloture=date(annee, 12, 31),
            clos=annee < dernier,
        )
        for annee in range(premier, dernier + 1)
    ]


def _mandat_complet(depuis: date, signe_par: str) -> MandatDeclaratif:
    return MandatDeclaratif(
        debut=depuis,
        motif=MotifChangement.ADHESION,
        obligations=["TVA", "IRPP_ACOMPTE", "DSF"],
        signe_le=depuis,
        signe_par=signe_par,
    )


def _mandat_partiel(depuis: date, signe_par: str) -> MandatDeclaratif:
    """Un mandat peut être partiel, et il l'est souvent.

    L'adhérent qui garde sa paie en interne ne mandate pas le Centre pour le DIPE.
    Déposer hors mandat n'est pas une négligence de procédure : c'est agir sans
    qualité.
    """
    return MandatDeclaratif(
        debut=depuis,
        motif=MotifChangement.ADHESION,
        obligations=["DSF"],
        signe_le=depuis,
        signe_par=signe_par,
    )


BATIMENT = Entreprise(
    niu="M081234567890P",
    denomination="SARL BATIMENT PLUS",
    forme_juridique=FormeJuridique.SARL,
    date_creation=date(2021, 3, 15),
    rccm="RC/DLA/2021/B/0977",
    capital=Decimal("5000000"),
    activite="Construction de bâtiments résidentiels et non résidentiels",
    siege="Bonabéri, Douala IV",
    etablissements=["Dépôt de Bonabéri", "Chantier Bonabéri — Rue des Palmiers"],
    regimes=[
        StatutRegime(
            debut=date(2021, 3, 15),
            fin=date(2023, 1, 1),
            regime=RegimeFiscal.IGS,
            motif=MotifChangement.CREATION,
        ),
        # Le chiffre d'affaires de 2022 a franchi le seuil : le reclassement prend
        # effet à l'ouverture de l'exercice suivant, jamais en cours d'exercice.
        StatutRegime(
            debut=date(2023, 1, 1),
            regime=RegimeFiscal.REEL,
            motif=MotifChangement.DEPASSEMENT_SEUIL,
            precision="CA 2022 de 62 400 000 F — notification CDI Bonabéri du 12/12/2022",
        ),
    ],
    rattachements=[
        StatutRattachement(
            debut=date(2021, 3, 15),
            centre=CentreRattachement.CDI,
            motif=MotifChangement.CREATION,
        )
    ],
    adhesions=[
        Adhesion(
            debut=date(2022, 6, 1),
            numero="ADH-2022-014",
            motif=MotifChangement.ADHESION,
        )
    ],
    mandats=[_mandat_complet(date(2022, 6, 1), "NKOA Jean-Pierre, gérant")],
    exercices=_exercices(2021, 2026),
    dirigeants=[
        Dirigeant(
            nom="NKOA Jean-Pierre",
            qualite="Gérant",
            depuis=date(2021, 3, 15),
            niu="P081111222233Q",
        )
    ],
)

TCHOUMBA = Entreprise(
    niu="P019876543210K",
    denomination="ETS TCHOUMBA & FILS",
    forme_juridique=FormeJuridique.ETS,
    date_creation=date(2019, 9, 2),
    rccm="RC/DLA/2019/A/1842",
    activite="Commerce général de détail",
    siege="Marché Central, Douala I",
    regimes=[
        StatutRegime(
            debut=date(2019, 9, 2),
            regime=RegimeFiscal.IGS,
            motif=MotifChangement.CREATION,
        )
    ],
    rattachements=[
        StatutRattachement(
            debut=date(2019, 9, 2),
            centre=CentreRattachement.CDI,
            motif=MotifChangement.CREATION,
        )
    ],
    adhesions=[
        Adhesion(
            debut=date(2023, 2, 1),
            numero="ADH-2023-007",
            motif=MotifChangement.ADHESION,
        )
    ],
    mandats=[_mandat_partiel(date(2023, 2, 1), "TCHOUMBA Émile, propriétaire")],
    exercices=_exercices(2019, 2026),
    dirigeants=[
        Dirigeant(nom="TCHOUMBA Émile", qualite="Propriétaire", depuis=date(2019, 9, 2))
    ],
)

COLOMBE = Entreprise(
    niu="M071122334455J",
    denomination="BOULANGERIE LA COLOMBE SARL",
    forme_juridique=FormeJuridique.SARL,
    date_creation=date(2020, 5, 18),
    rccm="RC/YAO/2020/B/0311",
    capital=Decimal("2000000"),
    activite="Boulangerie et pâtisserie",
    siege="Mvog-Mbi, Yaoundé III",
    regimes=[
        StatutRegime(
            debut=date(2020, 5, 18),
            fin=date(2022, 1, 1),
            regime=RegimeFiscal.IGS,
            motif=MotifChangement.CREATION,
        ),
        StatutRegime(
            debut=date(2022, 1, 1),
            regime=RegimeFiscal.REEL,
            motif=MotifChangement.OPTION,
            precision="Option exercée pour récupérer la TVA sur l'achat du four",
        ),
    ],
    rattachements=[
        StatutRattachement(
            debut=date(2020, 5, 18),
            centre=CentreRattachement.CDI,
            motif=MotifChangement.CREATION,
        )
    ],
    adhesions=[
        Adhesion(
            debut=date(2021, 1, 1),
            numero="ADH-2021-003",
            motif=MotifChangement.ADHESION,
        )
    ],
    mandats=[_mandat_complet(date(2021, 1, 1), "ESSOMBA Marie-Claire, gérante")],
    exercices=_exercices(2020, 2026),
    dirigeants=[
        Dirigeant(nom="ESSOMBA Marie-Claire", qualite="Gérante", depuis=date(2020, 5, 18))
    ],
)

AGRO = Entreprise(
    niu="M065544332211L",
    denomination="AGRO-NKOLO SA",
    forme_juridique=FormeJuridique.SA,
    date_creation=date(2018, 2, 7),
    rccm="RC/BAF/2018/B/0145",
    capital=Decimal("50000000"),
    activite="Culture, collecte et négoce de produits agricoles",
    siege="Bafoussam I",
    etablissements=["Entrepôt de Bafoussam", "Antenne de Douala — Bonabéri"],
    regimes=[
        StatutRegime(
            debut=date(2018, 2, 7),
            regime=RegimeFiscal.REEL,
            motif=MotifChangement.CREATION,
        )
    ],
    rattachements=[
        StatutRattachement(
            debut=date(2018, 2, 7),
            fin=date(2024, 1, 1),
            centre=CentreRattachement.CIME,
            motif=MotifChangement.CREATION,
        ),
        # Le rattachement change avec la taille, et il commande les dates limites :
        # une DGE ne dépose pas le même jour qu'un CDI.
        StatutRattachement(
            debut=date(2024, 1, 1),
            centre=CentreRattachement.DGE,
            motif=MotifChangement.DECISION_ADMINISTRATION,
            precision="Transfert notifié le 20/11/2023 — CA supérieur au seuil DGE",
        ),
    ],
    adhesions=[
        Adhesion(
            debut=date(2019, 1, 1),
            numero="ADH-2019-001",
            motif=MotifChangement.ADHESION,
        )
    ],
    mandats=[_mandat_complet(date(2019, 1, 1), "NKOLO Bertrand, directeur général")],
    exercices=_exercices(2018, 2026),
    dirigeants=[
        Dirigeant(
            nom="NKOLO Bertrand", qualite="Directeur général", depuis=date(2018, 2, 7)
        )
    ],
)

NGUEMA = Entreprise(
    niu="P027788990011M",
    denomination="CABINET NGUEMA CONSEIL",
    forme_juridique=FormeJuridique.ETS,
    date_creation=date(2022, 4, 11),
    rccm="RC/DLA/2022/A/2510",
    activite="Conseil en gestion et assistance administrative",
    siege="Akwa, Douala I",
    regimes=[
        StatutRegime(
            debut=date(2022, 4, 11),
            regime=RegimeFiscal.IGS,
            motif=MotifChangement.CREATION,
        )
    ],
    rattachements=[
        StatutRattachement(
            debut=date(2022, 4, 11),
            centre=CentreRattachement.CDI,
            motif=MotifChangement.CREATION,
        )
    ],
    adhesions=[
        Adhesion(
            debut=date(2024, 1, 1),
            numero="ADH-2024-021",
            motif=MotifChangement.ADHESION,
        )
    ],
    mandats=[_mandat_partiel(date(2024, 1, 1), "NGUEMA Sylvain, propriétaire")],
    exercices=_exercices(2022, 2026),
    dirigeants=[
        Dirigeant(nom="NGUEMA Sylvain", qualite="Propriétaire", depuis=date(2022, 4, 11))
    ],
)

CLINIQUE = Entreprise(
    niu="M093344556677N",
    denomination="CLINIQUE LE BON SAMARITAIN",
    forme_juridique=FormeJuridique.SARL,
    date_creation=date(2017, 11, 23),
    rccm="RC/YAO/2017/B/0892",
    capital=Decimal("25000000"),
    activite="Activités hospitalières et soins ambulatoires",
    siege="Nlongkak, Yaoundé I",
    regimes=[
        StatutRegime(
            debut=date(2017, 11, 23),
            regime=RegimeFiscal.REEL,
            motif=MotifChangement.CREATION,
        )
    ],
    rattachements=[
        StatutRattachement(
            debut=date(2017, 11, 23),
            centre=CentreRattachement.CIME,
            motif=MotifChangement.CREATION,
        )
    ],
    adhesions=[
        # Une adhésion résiliée puis reprise : le trou est réel, et il compte.
        # L'abattement CGA ne court pas pendant l'interruption.
        Adhesion(
            debut=date(2018, 1, 1),
            fin=date(2021, 1, 1),
            numero="ADH-2018-004",
            motif=MotifChangement.ADHESION,
        ),
        Adhesion(
            debut=date(2022, 7, 1),
            numero="ADH-2022-019",
            motif=MotifChangement.ADHESION,
            precision="Reprise après deux exercices hors Centre",
        ),
    ],
    mandats=[_mandat_complet(date(2022, 7, 1), "AMOUGOU Pascal, gérant")],
    exercices=_exercices(2017, 2026),
    dirigeants=[Dirigeant(nom="AMOUGOU Pascal", qualite="Gérant", depuis=date(2017, 11, 23))],
)


#: Le portefeuille complet, indexé par NIU.
PORTEFEUILLE_DEMO: dict[str, Entreprise] = {
    entreprise.niu: entreprise
    for entreprise in (BATIMENT, TCHOUMBA, COLOMBE, AGRO, NGUEMA, CLINIQUE)
}


def entreprise_demo(niu: str) -> Entreprise:
    """Rend un dossier de démonstration, ou lève avec la liste des NIU connus."""
    if niu not in PORTEFEUILLE_DEMO:
        raise KeyError(
            f"NIU « {niu} » absent du portefeuille de démonstration. "
            f"Connus : {', '.join(sorted(PORTEFEUILLE_DEMO))}"
        )
    return PORTEFEUILLE_DEMO[niu]
