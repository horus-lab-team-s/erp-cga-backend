"""Les échéances de l'adhérent, dans ses mots, et la preuve qu'il a payé (pas 113).

─────────────────────────────────────────────────────────────────────────────────
D'OÙ VIENT CE MODULE

Maquette « Espace adhérent CGA », vue D (« Échéances et paiement »), confrontée à l'espace
existant (pas 81) :

    maquette                                          avant le pas 113
    ───────────────────────────────────────────────── ────────────────────────────────────────
    « Impôt trimestriel · En retard de 24 jours ·     le libellé du catalogue (« Impôt général
    275 000 FCFA · 3e trimestre 2026 »                synthétique — versement trimestriel ») et
                                                      le retard, sans montant ni période lisible
    « De quoi s'agit-il ? » en langage courant        rien
    « Trimestre précédent : payé le 14 avril »,       rien
    « Prochaine échéance : 15 novembre »
    « J'ai déjà payé — envoyer la preuve »            rien : la quittance partait comme une pièce
                                                      quelconque, sans dire ce qu'elle réglait
    « Payer maintenant » (Mobile Money)               rien, et **rien au pas 113** (question Q29)

QUATRE ÉTATS POUR L'ADHÉRENT

    EN_RETARD        l'échéance est passée, rien n'est déposé, aucune preuve envoyée
    PREUVE_ENVOYEE   l'adhérent a envoyé sa quittance : le cabinet la vérifie
    A_VENIR          l'échéance n'est pas passée
    DEPOSEE          le cabinet a consigné le dépôt (l'accusé existe)

⚠️ **Une preuve envoyée n'est pas un dépôt.** L'obligation reste « à faire » au calendrier du
cabinet tant qu'un collaborateur n'a pas consigné l'accusé (pas 59), avec la quittance en pièce
jointe. L'adhérent lit « le cabinet la vérifie », pas « réglé » : une quittance illisible, ou d'un
autre trimestre, ne règle rien.

⚠️ **UNE CARTE PAR OBLIGATION, PAS UNE PAR PÉRIODE.** Le premier essai sur le dossier de
démonstration a rendu soixante-dix lignes « en retard » : douze mois de CNPS, de TVA, d'acomptes…
Une liste pareille ne se lit pas, et l'adhérent n'y trouve pas quoi faire en premier. Pour chaque
obligation, l'écran montre **la plus ancienne période en retard sans preuve** (c'est elle qu'on
règle d'abord) et dit combien d'autres sont en retard ; sinon la prochaine à venir. Une période dont
la preuve est envoyée garde sa carte (« le cabinet la vérifie »), et seul le dernier dépôt de
chaque obligation reste affiché.

⚠️ **Les textes « de quoi s'agit-il » sont au référentiel** (`obligations/explications_adherent.yaml`)
et **à valider par le fiscaliste** : une phrase fausse sur un impôt, lue par un adhérent, devient
une décision. Sans texte, l'écran montre le libellé du catalogue et rien d'autre.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contextes.obligations.domaine.obligations import ObligationInstance

__all__ = [
    "EcheanceDeLAdherent",
    "EtatPourLAdherent",
    "ExplicationDObligation",
    "PreuveEnvoyee",
    "RappelDEcheance",
    "ReglagesDesEcheancesDeLAdherent",
    "EcheanceARappeler",
    "echeances_a_rappeler",
    "echeances_de_l_adherent",
    "libelle_de_periode",
]

_MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def libelle_de_periode(debut: date, fin: date) -> str:
    """« juillet 2026 », « 3e trimestre 2026 », « année 2025 », sinon « du 01/07 au 15/09/2026 »."""
    if (debut.year, debut.month) == (fin.year, fin.month) and debut.day == 1:
        return f"{_MOIS[debut.month - 1]} {debut.year}"
    mois = (fin.year - debut.year) * 12 + fin.month - debut.month + 1
    if debut.day == 1 and mois == 3 and debut.month in (1, 4, 7, 10):
        rang = (debut.month - 1) // 3 + 1
        return f"{rang}{'er' if rang == 1 else 'e'} trimestre {debut.year}"
    if debut.day == 1 and mois == 12 and debut.month == 1:
        return f"année {debut.year}"
    return f"du {debut:%d/%m/%Y} au {fin:%d/%m/%Y}"


class ExplicationDObligation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1)
    #: Le nom que l'adhérent emploie : « Impôt trimestriel », pas « IGS — versement trimestriel ».
    titre: str = Field(min_length=3, max_length=60)
    de_quoi_s_agit_il: str = Field(min_length=10, max_length=600)
    en_cas_de_retard: str = Field(min_length=10, max_length=400)


class ReglagesDesEcheancesDeLAdherent(BaseModel):
    """`Docs/referentiel/obligations/explications_adherent.yaml`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Jusqu'où l'adhérent voit venir : une échéance dans six mois ne l'aide pas aujourd'hui.
    horizon_jours: int = Field(default=60, ge=7, le=366)
    #: Combien de temps une obligation déposée reste montrée : l'effet visible du travail du cabinet.
    deposees_montrees_jours: int = Field(default=90, ge=0, le=366)
    explications: tuple[ExplicationDObligation, ...] = ()
    #: « à valider » tant que le fiscaliste n'a pas relu les textes ; l'écran le dit.
    statut: str = Field(default="A_VALIDER", pattern=r"^(A_VALIDER|VALIDE)$")
    valide_par: str | None = None
    source: str = "valeurs par défaut"

    @model_validator(mode="after")
    def _coherence(self) -> ReglagesDesEcheancesDeLAdherent:
        codes = [e.code for e in self.explications]
        if len(codes) != len(set(codes)):
            raise ValueError(f"{self.source} : une obligation est expliquée deux fois.")
        if self.statut == "VALIDE" and not (self.valide_par or "").strip():
            raise ValueError(
                f"{self.source} : des textes « validés » nomment qui les a validés. Une validation "
                "sans nom ne se vérifie pas, et c'est l'adhérent qui lit ces phrases."
            )
        return self

    def explication(self, code: str) -> ExplicationDObligation | None:
        return next((e for e in self.explications if e.code == code), None)


class EtatPourLAdherent(StrEnum):
    EN_RETARD = "EN_RETARD"
    PREUVE_ENVOYEE = "PREUVE_ENVOYEE"
    A_VENIR = "A_VENIR"
    DEPOSEE = "DEPOSEE"


class PreuveEnvoyee(BaseModel):
    model_config = ConfigDict(frozen=True)

    code_obligation: str
    periode_debut: date
    piece: str
    envoyee_le: datetime
    par: str


class RappelDEcheance(BaseModel):
    """La même obligation, période précédente : « Trimestre précédent · 275 000 FCFA · payé le 14 avril »."""

    model_config = ConfigDict(frozen=True)

    periode: str
    declaree_le: date | None
    montant_constate: Decimal | None


class EcheanceDeLAdherent(BaseModel):
    model_config = ConfigDict(frozen=True)

    code_obligation: str
    titre: str
    libelle: str
    periode_debut: date
    periode_fin: date
    periode: str
    echeance: date
    etat: EtatPourLAdherent
    #: Jours de retard (EN_RETARD), jours restants (A_VENIR), sinon 0. Toujours positif.
    jours: int
    #: Estimé, jamais définitif ; `None` plutôt que zéro quand rien ne s'estime.
    montant_estime: Decimal | None
    #: Pour une obligation déposée : le montant porté à l'accusé.
    montant_constate: Decimal | None
    declaree_le: date | None
    de_quoi_s_agit_il: str | None
    en_cas_de_retard: str | None
    precedente: RappelDEcheance | None
    prochaine_echeance: date | None
    preuve: PreuveEnvoyee | None
    #: Pour la carte « en retard » : les autres périodes de la même obligation en retard, sans preuve.
    autres_periodes_en_retard: int = 0
    effectif_a_confirmer: bool


def echeances_de_l_adherent(
    *,
    instances: list[ObligationInstance],
    jour: date,
    reglages: ReglagesDesEcheancesDeLAdherent,
    montants_constates: dict[str, Decimal | None],
    preuves: list[PreuveEnvoyee],
) -> list[EcheanceDeLAdherent]:
    """Les échéances à montrer, dans l'ordre où elles demandent un geste.

    `instances` : l'échéancier de plusieurs exercices (la période précédente d'une obligation de
    janvier est en décembre de l'exercice d'avant). `montants_constates` : par numéro d'accusé.
    `preuves` : les preuves envoyées, la plus récente l'emporte.
    """
    par_code: dict[str, list[ObligationInstance]] = {}
    for o in sorted(instances, key=lambda o: o.periode_debut):
        par_code.setdefault(o.code_obligation, []).append(o)
    derniere_preuve: dict[tuple[str, date], PreuveEnvoyee] = {}
    for p in sorted(preuves, key=lambda p: p.envoyee_le):
        derniere_preuve[(p.code_obligation, p.periode_debut)] = p

    retenues: list[EcheanceDeLAdherent] = []
    for code, suite in par_code.items():
        choisies: list[tuple[int, EtatPourLAdherent, int]] = []
        a_regler = [
            (rang, o)
            for rang, o in enumerate(suite)
            if not o.deposee and o.echeance <= jour + timedelta(days=reglages.horizon_jours)
        ]
        prouvees = [(r, o) for r, o in a_regler if (code, o.periode_debut) in derniere_preuve]
        sans_preuve = [(r, o) for r, o in a_regler if (code, o.periode_debut) not in derniere_preuve]
        en_retard = [(r, o) for r, o in sans_preuve if o.echeance < jour]
        for rang, _ in prouvees:
            choisies.append((rang, EtatPourLAdherent.PREUVE_ENVOYEE, 0))
        if en_retard:
            choisies.append((en_retard[0][0], EtatPourLAdherent.EN_RETARD, len(en_retard) - 1))
        elif sans_preuve:
            choisies.append((sans_preuve[0][0], EtatPourLAdherent.A_VENIR, 0))
        deposees = [
            (r, o)
            for r, o in enumerate(suite)
            if o.deposee
            and o.declaree_le is not None
            and o.declaree_le >= jour - timedelta(days=reglages.deposees_montrees_jours)
        ]
        if deposees:
            dernier = max(deposees, key=lambda ro: (ro[1].declaree_le, ro[1].periode_debut))
            choisies.append((dernier[0], EtatPourLAdherent.DEPOSEE, 0))

        for rang, etat, autres in choisies:
            o = suite[rang]
            avant = suite[rang - 1] if rang > 0 else None
            # ⚠️ Pas 113, trouvé à l'essai réel : « Prochaine échéance : 15 mars 2025 » sur une carte en
            # retard depuis février 2025. La prochaine échéance est la prochaine **à venir**, pas la
            # période suivante, qui est elle-même en retard (et comptée dans « autres périodes »).
            apres = next(
                (s for s in suite[rang + 1 :] if not s.deposee and s.echeance >= jour), None
            )
            explication = reglages.explication(code)
            retenues.append(
                EcheanceDeLAdherent(
                    code_obligation=code,
                    titre=explication.titre if explication else o.libelle,
                    libelle=o.libelle,
                    periode_debut=o.periode_debut,
                    periode_fin=o.periode_fin,
                    periode=libelle_de_periode(o.periode_debut, o.periode_fin),
                    echeance=o.echeance,
                    etat=etat,
                    jours=abs((o.echeance - jour).days)
                    if etat in (EtatPourLAdherent.EN_RETARD, EtatPourLAdherent.A_VENIR)
                    else 0,
                    montant_estime=o.montant_estime,
                    montant_constate=montants_constates.get(o.reference_depot or ""),
                    declaree_le=o.declaree_le,
                    de_quoi_s_agit_il=explication.de_quoi_s_agit_il if explication else None,
                    en_cas_de_retard=explication.en_cas_de_retard if explication else None,
                    precedente=(
                        RappelDEcheance(
                            periode=libelle_de_periode(avant.periode_debut, avant.periode_fin),
                            declaree_le=avant.declaree_le,
                            montant_constate=montants_constates.get(avant.reference_depot or ""),
                        )
                        if avant is not None
                        else None
                    ),
                    prochaine_echeance=apres.echeance if apres is not None else None,
                    preuve=(
                        derniere_preuve.get((code, o.periode_debut))
                        if etat is EtatPourLAdherent.PREUVE_ENVOYEE
                        else None
                    ),
                    autres_periodes_en_retard=autres,
                    effectif_a_confirmer=o.effectif_a_confirmer,
                )
            )
    ordre = {
        EtatPourLAdherent.EN_RETARD: 0,
        EtatPourLAdherent.PREUVE_ENVOYEE: 1,
        EtatPourLAdherent.A_VENIR: 2,
        EtatPourLAdherent.DEPOSEE: 3,
    }

    def cle(e: EcheanceDeLAdherent):
        if e.etat is EtatPourLAdherent.EN_RETARD:
            return (0, -e.jours, e.code_obligation)
        if e.etat is EtatPourLAdherent.DEPOSEE:
            return (3, -(e.declaree_le or date.min).toordinal(), e.code_obligation)
        return (ordre[e.etat], e.echeance.toordinal(), e.code_obligation)

    return sorted(retenues, key=cle)


class EcheanceARappeler(BaseModel):
    model_config = ConfigDict(frozen=True)

    code_obligation: str
    titre: str
    periode: str
    periode_debut: date
    echeance: date
    jours: int


def echeances_a_rappeler(
    *,
    instances: list[ObligationInstance],
    jour: date,
    reglages: ReglagesDesEcheancesDeLAdherent,
    preuves: list[PreuveEnvoyee],
) -> list[EcheanceARappeler]:
    """Chaque période **à venir**, non déposée et sans preuve : ce que les rappels du pas 115 lisent.

    ⚠️ Pas les cartes de « Mes échéances ». Une carte par obligation montre la plus ancienne période
    en retard et cache les périodes suivantes : le premier essai des rappels n'envoyait rien pour la
    CNPS d'août, parce que la CNPS de janvier 2025, impayée, tenait la carte. Un adhérent en retard
    sur une période doit être prévenu de la suivante, pas moins.
    """
    prouvees = {(p.code_obligation, p.periode_debut) for p in preuves}
    retenues = []
    for o in instances:
        if o.deposee or o.echeance < jour or (o.code_obligation, o.periode_debut) in prouvees:
            continue
        explication = reglages.explication(o.code_obligation)
        retenues.append(
            EcheanceARappeler(
                code_obligation=o.code_obligation,
                titre=explication.titre if explication else o.libelle,
                periode=libelle_de_periode(o.periode_debut, o.periode_fin),
                periode_debut=o.periode_debut,
                echeance=o.echeance,
                jours=(o.echeance - jour).days,
            )
        )
    return sorted(retenues, key=lambda e: (e.echeance, e.code_obligation, e.periode_debut))
