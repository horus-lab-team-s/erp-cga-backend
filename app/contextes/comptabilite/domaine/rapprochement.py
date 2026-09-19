"""Le rapprochement bancaire : confronter le relevé aux écritures (pas 101).

─────────────────────────────────────────────────────────────────────────────────
CE QUE C'EST, POUR QUI N'EN A JAMAIS FAIT

La banque tient, de son côté, le compte de l'entreprise : c'est le **relevé**. Le cabinet
tient le même compte dans la comptabilité : c'est le compte 521 (ou 5231 pour le Mobile
Money), mouvementé par le journal de banque. Deux tiers indépendants tiennent le même
compte ; s'ils ne disent pas la même chose, l'un des deux se trompe, ou une opération
manque d'un côté. C'est le contrôle le plus efficace qui existe en comptabilité.

Rapprocher, c'est apparier chaque ligne du relevé avec la ligne d'écriture qui la
représente, puis **expliquer ce qui reste**. L'état de rapprochement le chiffre :

    solde du relevé
    − opérations du relevé sans écriture   (frais bancaires pas encore saisis…)
    + opérations comptables absentes du relevé   (chèque émis, pas encore encaissé…)
    = solde comptable            ← sinon, il reste un écart inexpliqué

⚠️ LE SIGNE, UNE FOIS POUR TOUTES

Tout est exprimé **du point de vue de l'entreprise**, comme la comptabilité : un
encaissement augmente le compte 521, il est au **débit** ; un décaissement est au
**crédit**. Le relevé de la banque dit l'inverse (« crédit » pour un versement reçu), parce
qu'il est tenu du point de vue de la banque. Le profil de lecture fait la traduction une
seule fois, à l'import ; au-delà, personne n'a plus à y penser. Les soldes sont signés :
positif, l'entreprise a de l'argent en banque ; négatif, elle est à découvert.

⚠️ POURQUOI LE RAPPROCHEMENT NE MARQUE PAS LES ÉCRITURES

Une écriture validée ne se modifie plus (voir `EcritureComptable`). Poser une marque de
rapprochement sur ses lignes reviendrait à la réécrire. Le rapprochement est donc un objet
à part, qui **désigne** les lignes d'écriture par leur clé et leur rang. Les écritures
restent intactes, et un rapprochement abandonné ne laisse aucune trace dans le journal.

⚠️ CE QUE LE MOTEUR PROPOSE, ET CE QU'IL NE FAIT JAMAIS

Il propose des correspondances **motivées** (« montant exact · référence F-2026-0412
présente dans le libellé · 2 jours d'écart ») : le comptable doit pouvoir refuser en
connaissance de cause. Il n'apparie seul que lorsqu'une seule écriture correspond
fortement. Il n'absorbe **jamais** un écart de montant : 2 350 000 contre 2 349 000 n'est
pas un rapprochement, c'est une question.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contextes.comptabilite.domaine.entites import EcritureComptable, EtatEcriture, Sens
from app.partage.copie import transiter

__all__ = [
    "Appariement",
    "EtatDeRapprochement",
    "ForceDeCorrespondance",
    "Justification",
    "LigneDeReleve",
    "ModeAppariement",
    "MouvementComptable",
    "NatureJustification",
    "ProfilDeReleve",
    "Proposition",
    "RapprochementBancaire",
    "RapprochementRefuse",
    "ReglagesDuRapprochement",
    "ReleveIllisible",
    "StatutRapprochement",
    "etat_du_rapprochement",
    "lire_le_releve",
    "mouvements_du_compte",
    "proposer_des_correspondances",
    "signe",
]


class RapprochementRefuse(ValueError):
    """Le geste n'est pas permis. Le message dit pourquoi et quoi faire, pour l'écran."""


class ReleveIllisible(ValueError):
    """Le fichier ne se lit pas avec ce profil. Le message nomme la ligne et la colonne."""


def signe(montant: Decimal, sens: Sens) -> Decimal:
    """Le montant signé du point de vue de l'entreprise : débit positif, crédit négatif."""
    return montant if sens is Sens.DEBIT else -montant


# ── Le réglage et les profils, lus au référentiel ─────────────────────────────


class ReglagesDuRapprochement(BaseModel):
    """`Docs/referentiel/rapprochement/reglages.yaml`. Les valeurs par défaut sont sobres."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Au-delà de cet écart de dates, une écriture n'est même pas proposée. Un virement
    #: saisi le 30 et débité le 2 du mois suivant est courant ; trois semaines, non.
    fenetre_jours: int = Field(15, ge=0, le=90)
    #: En deçà, la date compte comme un argument de correspondance (« date proche »).
    fenetre_proche_jours: int = Field(5, ge=0, le=30)
    source: str = "valeurs par défaut"

    @model_validator(mode="after")
    def _fenetres_ordonnees(self) -> ReglagesDuRapprochement:
        if self.fenetre_proche_jours > self.fenetre_jours:
            raise ValueError(
                "fenetre_proche_jours dépasse fenetre_jours : une date « proche » serait "
                "hors de la fenêtre de recherche."
            )
        return self


class ProfilDeReleve(BaseModel):
    """Comment lire l'export d'une banque. Un fichier par format, au référentiel.

    ⚠️ **Le moteur ne connaît aucune banque**, comme les profils d'échange ne connaissent
    aucun logiciel. Accepter l'export d'une nouvelle banque, c'est déposer un fichier.

    Deux formes de montant existent dans les exports : une colonne signée, ou deux
    colonnes « débit » et « crédit » **du point de vue de la banque**. Le profil en déclare
    exactement une.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    libelle: str = Field(min_length=3)
    separateur: str = Field(";", min_length=1, max_length=1)
    encodage: str = "utf-8-sig"
    #: Lignes à sauter en tête (titres de colonnes, nom de la banque…).
    lignes_d_entete: int = Field(1, ge=0, le=20)
    format_date: str = "%d/%m/%Y"
    #: Les colonnes sont numérotées **à partir de 1**, comme un tableur les montre.
    colonne_date: int = Field(ge=1)
    colonne_libelle: int = Field(ge=1)
    colonne_montant: int | None = Field(None, ge=1)
    #: Avec `colonne_montant` : vrai si un montant positif est un versement reçu.
    montant_positif_encaisse: bool = True
    colonne_debit_banque: int | None = Field(None, ge=1)
    colonne_credit_banque: int | None = Field(None, ge=1)
    separateur_decimal: str = Field(",", min_length=1, max_length=1)

    @model_validator(mode="after")
    def _une_seule_forme_de_montant(self) -> ProfilDeReleve:
        signee = self.colonne_montant is not None
        deux = self.colonne_debit_banque is not None and self.colonne_credit_banque is not None
        une_des_deux = (self.colonne_debit_banque is None) != (self.colonne_credit_banque is None)
        if signee == deux or une_des_deux:
            raise ValueError(
                f"profil {self.code} : déclarer soit colonne_montant, soit colonne_debit_banque "
                "et colonne_credit_banque ensemble."
            )
        return self


class LigneDeReleve(BaseModel):
    """Une opération du relevé, traduite du point de vue de l'entreprise."""

    model_config = ConfigDict(frozen=True)

    #: Le rang dans le relevé, à partir de 1. C'est par lui que tout geste désigne la ligne.
    rang: int = Field(ge=1)
    date: date
    libelle: str = Field(min_length=1, max_length=300)
    montant: Decimal = Field(gt=0)
    #: DEBIT : l'argent est entré sur le compte ; CREDIT : il en est sorti.
    sens: Sens

    @property
    def signe(self) -> Decimal:
        return signe(self.montant, self.sens)


def _montant(texte: str, profil: ProfilDeReleve) -> Decimal | None:
    """« 2 350 000,00 » ou « (18 500) » en décimal ; une cellule vide rend `None`."""
    propre = texte.strip().replace(" ", "").replace(" ", "").replace(" ", "")
    if not propre:
        return None
    negatif = propre.startswith("(") and propre.endswith(")")
    propre = propre.strip("()")
    if profil.separateur_decimal == ",":
        propre = propre.replace(".", "").replace(",", ".")
    else:
        propre = propre.replace(",", "")
    valeur = Decimal(propre)
    return -valeur if negatif else valeur


def lire_le_releve(contenu: bytes, profil: ProfilDeReleve) -> list[LigneDeReleve]:
    """Lit un export de relevé. Toute ligne illisible fait échouer l'import **entier**.

    ⚠️ Importer les lignes lisibles et ignorer les autres produirait un relevé incomplet,
    dont le solde ne correspondrait plus, et l'écart serait cherché dans la comptabilité
    alors qu'il est dans le fichier. Le message nomme la ligne du fichier, comme un tableur.
    """
    try:
        texte = contenu.decode(profil.encodage)
    except UnicodeDecodeError as erreur:
        raise ReleveIllisible(
            f"le fichier n'est pas encodé en {profil.encodage} (profil {profil.code})."
        ) from erreur
    lignes: list[LigneDeReleve] = []
    lecteur = csv.reader(io.StringIO(texte), delimiter=profil.separateur)
    for numero, cellules in enumerate(lecteur, start=1):
        if numero <= profil.lignes_d_entete or not any(c.strip() for c in cellules):
            continue

        def cellule(colonne: int, nom: str, _numero: int = numero, _cellules=cellules) -> str:
            if colonne > len(_cellules):
                raise ReleveIllisible(
                    f"ligne {_numero} : la colonne {colonne} ({nom}) manque ; la ligne en compte "
                    f"{len(_cellules)}. Le séparateur « {profil.separateur} » est-il le bon ?"
                )
            return _cellules[colonne - 1]

        brut_date = cellule(profil.colonne_date, "date").strip()
        try:
            jour = datetime.strptime(brut_date, profil.format_date).date()
        except ValueError as erreur:
            raise ReleveIllisible(
                f"ligne {numero} : date « {brut_date} » illisible au format {profil.format_date}."
            ) from erreur
        try:
            if profil.colonne_montant is not None:
                valeur = _montant(cellule(profil.colonne_montant, "montant"), profil)
                if valeur is None or valeur == 0:
                    raise ReleveIllisible(f"ligne {numero} : montant vide ou nul.")
                encaisse = (valeur > 0) == profil.montant_positif_encaisse
                montant = abs(valeur)
            else:
                assert profil.colonne_debit_banque and profil.colonne_credit_banque
                debit = _montant(cellule(profil.colonne_debit_banque, "débit"), profil)
                credit = _montant(cellule(profil.colonne_credit_banque, "crédit"), profil)
                if bool(debit) == bool(credit):
                    raise ReleveIllisible(
                        f"ligne {numero} : il faut un montant au débit **ou** au crédit, pas "
                        "les deux ni aucun."
                    )
                # Le crédit de la banque est un versement reçu par l'entreprise.
                encaisse = bool(credit)
                montant = abs(credit or debit)  # type: ignore[arg-type]
        except InvalidOperation as erreur:
            raise ReleveIllisible(f"ligne {numero} : montant illisible.") from erreur
        if montant != montant.to_integral_value():
            raise ReleveIllisible(
                f"ligne {numero} : montant {montant} avec centimes ; "
                "le franc CFA n'en comporte pas."
            )
        lignes.append(
            LigneDeReleve(
                rang=len(lignes) + 1,
                date=jour,
                libelle=cellule(profil.colonne_libelle, "libellé").strip() or "(sans libellé)",
                montant=montant,
                sens=Sens.DEBIT if encaisse else Sens.CREDIT,
            )
        )
    if not lignes:
        raise ReleveIllisible("le fichier ne contient aucune opération.")
    return lignes


# ── Le côté comptable ─────────────────────────────────────────────────────────


class MouvementComptable(BaseModel):
    """Une ligne d'écriture sur le compte de trésorerie, telle que le rapprochement la lit."""

    model_config = ConfigDict(frozen=True)

    ecriture: str
    #: L'index de la ligne dans l'écriture, à partir de 0.
    ligne: int = Field(ge=0)
    date: date
    libelle: str
    montant: Decimal
    sens: Sens
    piece: str | None = None
    reference: str | None = None
    validee: bool

    @property
    def signe(self) -> Decimal:
        return signe(self.montant, self.sens)

    @property
    def designation(self) -> tuple[str, int]:
        return (self.ecriture, self.ligne)


def mouvements_du_compte(
    ecritures: list[EcritureComptable], compte: str
) -> list[MouvementComptable]:
    """Les lignes portées sur le compte **ou ses sous-comptes** (521, 5211, 52111…)."""
    mouvements = []
    for ecriture in ecritures:
        for index, ligne in enumerate(ecriture.lignes):
            if ligne.compte.startswith(compte):
                mouvements.append(
                    MouvementComptable(
                        ecriture=ecriture.cle,
                        ligne=index,
                        date=ecriture.date_operation,
                        libelle=ligne.libelle or ecriture.libelle,
                        montant=ligne.montant,
                        sens=ligne.sens,
                        piece=ecriture.piece_justificative,
                        reference=ecriture.reference_externe,
                        validee=ecriture.etat is EtatEcriture.VALIDEE,
                    )
                )
    return sorted(mouvements, key=lambda m: (m.date, m.ecriture, m.ligne))


# ── Les propositions ──────────────────────────────────────────────────────────


class ForceDeCorrespondance(StrEnum):
    #: Une référence du mouvement figure dans le libellé, ou c'est le seul candidat proche.
    FORTE = "FORTE"
    #: Le montant et le sens concordent, mais rien d'autre ne départage.
    POSSIBLE = "POSSIBLE"
    #: Le mouvement concorde, mais il est déjà rapproché d'une autre ligne du relevé.
    A_ECARTER = "A_ECARTER"


class Proposition(BaseModel):
    model_config = ConfigDict(frozen=True)

    mouvement: MouvementComptable
    force: ForceDeCorrespondance
    #: Les raisons, en clair, dans l'ordre où un comptable les vérifierait.
    motifs: list[str]
    ecart_jours: int


def _normaliser(texte: str) -> str:
    sans_accents = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]", "", sans_accents.upper())


def _reference_trouvee(libelle: str, mouvement: MouvementComptable) -> str | None:
    """La référence du mouvement qui figure dans le libellé bancaire, s'il y en a une.

    Comparaison **sans ponctuation ni espaces** : la banque écrit « FACT 0412 » ce que la
    facture écrit « F-2026-0412 ». On compare donc aussi le dernier bloc de chiffres, d'au
    moins trois caractères (en deçà, « 12 » se trouverait partout).
    """
    cible = _normaliser(libelle)
    for reference in (mouvement.reference, mouvement.piece):
        if not reference:
            continue
        if _normaliser(reference) and _normaliser(reference) in cible:
            return reference
        blocs = re.findall(r"\d{3,}", reference)
        if blocs and re.search(rf"(?<!\d){blocs[-1]}(?!\d)", libelle):
            return reference
    return None


def proposer_des_correspondances(
    ligne: LigneDeReleve,
    mouvements: list[MouvementComptable],
    deja_rapproches: set[tuple[str, int]],
    reglages: ReglagesDuRapprochement,
) -> list[Proposition]:
    """Les écritures qui peuvent représenter cette ligne, les plus probables d'abord.

    Le **montant exact et le sens** sont exigés : ils ne sont pas un argument, ils sont la
    condition. Viennent ensuite la référence et la proximité des dates.
    """
    candidats = [
        m
        for m in mouvements
        if m.montant == ligne.montant
        and m.sens is ligne.sens
        and abs((m.date - ligne.date).days) <= reglages.fenetre_jours
    ]
    libres = [m for m in candidats if m.designation not in deja_rapproches]
    proches = [
        m for m in libres if abs((m.date - ligne.date).days) <= reglages.fenetre_proche_jours
    ]
    propositions = []
    for m in candidats:
        ecart = abs((m.date - ligne.date).days)
        motifs = ["montant exact"]
        reference = _reference_trouvee(ligne.libelle, m)
        if reference:
            motifs.append(f"référence {reference} présente dans le libellé")
        if ecart <= reglages.fenetre_proche_jours:
            motifs.append("même jour" if ecart == 0 else f"date proche ({ecart} j)")
        else:
            motifs.append(f"date éloignée ({ecart} j)")
        if not m.validee:
            motifs.append("écriture encore en brouillon")
        if m.designation in deja_rapproches:
            force = ForceDeCorrespondance.A_ECARTER
            motifs.append("déjà rapprochée d'une autre ligne")
        elif reference or (len(proches) == 1 and m in proches and len(libres) == 1):
            force = ForceDeCorrespondance.FORTE
        else:
            force = ForceDeCorrespondance.POSSIBLE
        propositions.append(Proposition(mouvement=m, force=force, motifs=motifs, ecart_jours=ecart))
    ordre = {
        ForceDeCorrespondance.FORTE: 0,
        ForceDeCorrespondance.POSSIBLE: 1,
        ForceDeCorrespondance.A_ECARTER: 2,
    }
    return sorted(propositions, key=lambda p: (ordre[p.force], p.ecart_jours, p.mouvement.ecriture))


# ── Le rapprochement ──────────────────────────────────────────────────────────


class ModeAppariement(StrEnum):
    AUTOMATIQUE = "AUTOMATIQUE"
    MANUEL = "MANUEL"


class Appariement(BaseModel):
    model_config = ConfigDict(frozen=True)

    rang: int
    ecriture: str
    ligne: int
    mode: ModeAppariement
    motifs: list[str] = Field(default_factory=list)
    par: str
    le: datetime


class NatureJustification(StrEnum):
    #: Aucune pièce ne justifie le mouvement : elle est demandée à l'adhérent. C'est la
    #: règle du contrôle en amont : pas d'écriture d'attente sans justificatif.
    PIECE_DEMANDEE = "PIECE_DEMANDEE"
    #: L'écriture sera passée sur la période suivante (frais connus en fin de mois…).
    ECRITURE_A_VENIR = "ECRITURE_A_VENIR"
    #: Une erreur de la banque, signalée à l'établissement.
    ERREUR_BANCAIRE = "ERREUR_BANCAIRE"


class Justification(BaseModel):
    model_config = ConfigDict(frozen=True)

    rang: int
    nature: NatureJustification
    motif: str = Field(min_length=10, max_length=500)
    par: str
    le: datetime


class StatutRapprochement(StrEnum):
    EN_COURS = "EN_COURS"
    #: Arrêté : l'état de rapprochement est figé et signé. Plus aucun geste.
    VALIDE = "VALIDE"
    #: Importé par erreur (mauvais relevé, mauvaise période). Ne bloque plus la période.
    ABANDONNE = "ABANDONNE"


class EtatDeRapprochement(BaseModel):
    """L'état de rapprochement, calculé. Voir l'en-tête pour la formule."""

    model_config = ConfigDict(frozen=True)

    au: date
    solde_releve: Decimal
    solde_comptable: Decimal
    #: Somme signée des lignes du relevé sans écriture rapprochée.
    releve_non_rapproche: Decimal
    #: Somme signée des mouvements comptables de la période absents du relevé.
    comptable_non_rapproche: Decimal
    ecart_inexplique: Decimal
    lignes: int
    rapprochees: int
    automatiques: int
    justifiees: int
    a_traiter: int


class RapprochementBancaire(BaseModel):
    """Un relevé importé, et ce que le comptable en a fait. Immuable : chaque geste rend
    un nouvel objet, **revalidé** par `transiter` (jamais `model_copy`, qui sauterait
    l'invariant du relevé qui tombe juste ; un contrôle d'architecture y veille)."""

    model_config = ConfigDict(frozen=True)

    identifiant: str
    dossier: str
    journal: str
    compte: str
    exercice: str
    du: date
    au: date
    #: Soldes signés, du point de vue de l'entreprise (voir l'en-tête).
    solde_initial: Decimal
    solde_final: Decimal
    lignes: list[LigneDeReleve] = Field(min_length=1)
    #: Le profil de lecture, ou « saisie » pour un relevé saisi à la main.
    source: str
    importe_par: str
    importe_le: datetime
    appariements: list[Appariement] = Field(default_factory=list)
    justifications: list[Justification] = Field(default_factory=list)
    statut: StatutRapprochement = StatutRapprochement.EN_COURS
    valide_par: str | None = None
    valide_le: datetime | None = None
    #: L'état figé à la validation. Recalculé tant que le rapprochement est en cours.
    etat_valide: EtatDeRapprochement | None = None
    motif_abandon: str | None = None

    @model_validator(mode="after")
    def _releve_coherent(self) -> RapprochementBancaire:
        if self.du > self.au:
            raise ValueError("la période du relevé commence après sa fin.")
        hors = [l_.rang for l_ in self.lignes if not (self.du <= l_.date <= self.au)]
        if hors:
            raise ValueError(
                f"lignes {', '.join(map(str, hors))} hors de la période du "
                f"{self.du:%d/%m/%Y} au {self.au:%d/%m/%Y} : le relevé ou la période est faux."
            )
        mouvement = sum((l_.signe for l_ in self.lignes), Decimal(0))
        if self.solde_initial + mouvement != self.solde_final:
            # ⚠️ Le contrôle qui évite de chercher dans la comptabilité une erreur qui est
            # dans le fichier : une ligne manquante, un montant mal lu, un solde recopié faux.
            raise ValueError(
                f"le relevé ne tombe pas juste : solde initial {self.solde_initial} + opérations "
                f"{mouvement} = {self.solde_initial + mouvement}, et non {self.solde_final}. "
                "Vérifier les soldes saisis et que le fichier est complet."
            )
        return self

    # ── Lectures ──────────────────────────────────────────────────────────────

    def ligne(self, rang: int) -> LigneDeReleve:
        for ligne in self.lignes:
            if ligne.rang == rang:
                return ligne
        raise RapprochementRefuse(f"le relevé n'a pas de ligne {rang}.")

    def appariement(self, rang: int) -> Appariement | None:
        return next((a for a in self.appariements if a.rang == rang), None)

    def justification(self, rang: int) -> Justification | None:
        return next((j for j in self.justifications if j.rang == rang), None)

    @property
    def designations(self) -> set[tuple[str, int]]:
        return {(a.ecriture, a.ligne) for a in self.appariements}

    # ── Gestes ────────────────────────────────────────────────────────────────

    def _exiger_en_cours(self) -> None:
        if self.statut is not StatutRapprochement.EN_COURS:
            raise RapprochementRefuse(
                f"rapprochement {self.statut.value.lower()} : plus aucun geste n'y est possible."
            )

    def apparier(
        self,
        rang: int,
        mouvement: MouvementComptable,
        *,
        mode: ModeAppariement,
        motifs: list[str],
        par: str,
        le: datetime,
        deja_rapproches: set[tuple[str, int]],
    ) -> RapprochementBancaire:
        self._exiger_en_cours()
        ligne = self.ligne(rang)
        if self.appariement(rang) is not None:
            raise RapprochementRefuse(
                f"la ligne {rang} est déjà rapprochée : la dissocier d'abord."
            )
        if mouvement.montant != ligne.montant or mouvement.sens is not ligne.sens:
            raise RapprochementRefuse(
                f"la ligne {rang} ({ligne.signe}) et l'écriture {mouvement.ecriture} "
                f"({mouvement.signe}) ne portent pas le même montant : un rapprochement "
                "n'absorbe pas d'écart. Corriger l'écriture, ou justifier la ligne."
            )
        if mouvement.designation in deja_rapproches:
            raise RapprochementRefuse(
                f"l'écriture {mouvement.ecriture} (ligne {mouvement.ligne + 1}) est déjà "
                "rapprochée d'une autre ligne de relevé."
            )
        nouveau = Appariement(
            rang=rang,
            ecriture=mouvement.ecriture,
            ligne=mouvement.ligne,
            mode=mode,
            motifs=motifs,
            par=par,
            le=le,
        )
        # Une ligne rapprochée n'a plus besoin de justification : on retire l'ancienne.
        return transiter(
            self,
            appariements=[*self.appariements, nouveau],
            justifications=[j for j in self.justifications if j.rang != rang],
        )

    def dissocier(self, rang: int) -> RapprochementBancaire:
        self._exiger_en_cours()
        if self.appariement(rang) is None:
            raise RapprochementRefuse(f"la ligne {rang} n'est pas rapprochée.")
        return transiter(self, appariements=[a for a in self.appariements if a.rang != rang])

    def justifier(
        self, rang: int, *, nature: NatureJustification, motif: str, par: str, le: datetime
    ) -> RapprochementBancaire:
        self._exiger_en_cours()
        self.ligne(rang)
        if self.appariement(rang) is not None:
            raise RapprochementRefuse(
                f"la ligne {rang} est rapprochée : elle n'a rien à justifier."
            )
        justification = Justification(rang=rang, nature=nature, motif=motif.strip(), par=par, le=le)
        return transiter(
            self,
            justifications=[*(j for j in self.justifications if j.rang != rang), justification],
        )

    def valider(
        self,
        etat: EtatDeRapprochement,
        mouvements: list[MouvementComptable],
        *,
        par: str,
        le: datetime,
    ) -> RapprochementBancaire:
        self._exiger_en_cours()
        if etat.a_traiter:
            raise RapprochementRefuse(
                f"{etat.a_traiter} ligne(s) ni rapprochée(s) ni justifiée(s) : chaque opération "
                "du relevé doit être expliquée avant d'arrêter le rapprochement."
            )
        if etat.ecart_inexplique != 0:
            raise RapprochementRefuse(
                f"écart inexpliqué de {etat.ecart_inexplique} FCFA entre le relevé et la "
                "comptabilité : un rapprochement ne s'arrête pas sur un écart."
            )
        par_designation = {m.designation: m for m in mouvements}
        brouillons = sorted(
            a.ecriture
            for a in self.appariements
            if (m := par_designation.get((a.ecriture, a.ligne))) is None or not m.validee
        )
        if brouillons:
            raise RapprochementRefuse(
                f"écritures non validées ou disparues : {', '.join(brouillons)}. Un rapprochement "
                "arrêté ne peut s'appuyer que sur des écritures qui ne bougeront plus."
            )
        # ⚠️ Un brouillon rapproché peut avoir été corrigé depuis (pas 71) : son montant ne
        # correspond plus à la ligne du relevé. L'appariement est alors faux, et le valider
        # arrêterait un état qui ne tombe juste qu'en apparence.
        divergents = sorted(
            f"ligne {a.rang} ({m.ecriture})"
            for a in self.appariements
            if (m := par_designation[(a.ecriture, a.ligne)]).montant != self.ligne(a.rang).montant
            or m.sens is not self.ligne(a.rang).sens
        )
        if divergents:
            raise RapprochementRefuse(
                f"écritures modifiées depuis le rapprochement : {', '.join(divergents)}. "
                "Les dissocier et les rapprocher de nouveau."
            )
        return transiter(
            self, statut=StatutRapprochement.VALIDE, valide_par=par, valide_le=le, etat_valide=etat
        )

    def abandonner(self, motif: str) -> RapprochementBancaire:
        self._exiger_en_cours()
        if len(motif.strip()) < 10:
            raise RapprochementRefuse(
                "dire pourquoi le relevé est abandonné (10 caractères au moins)."
            )
        return transiter(self, statut=StatutRapprochement.ABANDONNE, motif_abandon=motif.strip())


def etat_du_rapprochement(
    rapprochement: RapprochementBancaire,
    mouvements: list[MouvementComptable],
    rapproches_ailleurs: set[tuple[str, int]] | None = None,
) -> EtatDeRapprochement:
    """L'état de rapprochement à la date de fin du relevé.

    `mouvements` : **tous** les mouvements du compte sur l'exercice (à-nouveaux compris),
    pour que le solde comptable soit celui du compte, et pas seulement de la période.

    ⚠️ Le côté comptable non rapproché ne compte que les mouvements **de la période** du
    relevé : un chèque émis le 28 juin et encaissé le 3 juillet appartient au rapprochement
    de juin. S'il n'y a pas été rapproché, l'écart le montrera, et c'est une information.
    """
    if rapprochement.etat_valide is not None:
        return rapprochement.etat_valide
    solde_comptable = sum((m.signe for m in mouvements if m.date <= rapprochement.au), Decimal(0))
    designations = rapprochement.designations | (rapproches_ailleurs or set())
    releve_nr = sum(
        (l_.signe for l_ in rapprochement.lignes if rapprochement.appariement(l_.rang) is None),
        Decimal(0),
    )
    comptable_nr = sum(
        (
            m.signe
            for m in mouvements
            if rapprochement.du <= m.date <= rapprochement.au and m.designation not in designations
        ),
        Decimal(0),
    )
    rapprochees = len(rapprochement.appariements)
    justifiees = len(rapprochement.justifications)
    return EtatDeRapprochement(
        au=rapprochement.au,
        solde_releve=rapprochement.solde_final,
        solde_comptable=solde_comptable,
        releve_non_rapproche=releve_nr,
        comptable_non_rapproche=comptable_nr,
        ecart_inexplique=rapprochement.solde_final - releve_nr + comptable_nr - solde_comptable,
        lignes=len(rapprochement.lignes),
        rapprochees=rapprochees,
        automatiques=sum(
            1 for a in rapprochement.appariements if a.mode is ModeAppariement.AUTOMATIQUE
        ),
        justifiees=justifiees,
        a_traiter=len(rapprochement.lignes) - rapprochees - justifiees,
    )
