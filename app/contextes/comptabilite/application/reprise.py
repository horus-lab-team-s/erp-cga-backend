"""Reprendre le passé comptable d'un adhérent qui arrive.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE EXISTE

Un adhérent n'arrive jamais vierge. Il arrive avec dix mois d'écritures dans le
logiciel de son ancien comptable, et la première chose que le centre doit savoir
faire est de les reprendre. Sans cela, la plateforme ne se vend qu'à des
entreprises qui se créent, c'est-à-dire presque à personne.

Le moteur d'échange sait déjà **lire** un fichier : il rend des écritures ou des
anomalies. Ce module fait le reste, et le reste tient en trois décisions.

⚠️ **PREMIÈRE : LE NUMÉRO DU FICHIER NE DEVIENT JAMAIS LE NÔTRE.**

Le fichier porte les numéros du logiciel précédent. Les reprendre tels quels
serait naturel, et ce serait une faute : *« la continuité de la séquence est une
propriété du registre entier, jamais d'une écriture isolée »*. Deux reprises sur
le même journal se marcheraient dessus, et un fichier forgé pourrait insérer une
écriture au numéro d'une autre.

C'est le registre qui numérote, comme pour toute saisie. Le numéro d'origine
n'est pas perdu pour autant : il part en **référence externe** quand le fichier
n'en porte pas, et c'est ce qui permettra au comptable de retrouver la pièce dans
le classeur de l'adhérent.

⚠️ **DEUXIÈME : ON CONTRÔLE TOUT, PUIS ON ÉCRIT TOUT.**

Un lot de quatre mille écritures enregistrées une à une, qui s'arrête à la trois
centième, laisse deux cent quatre-vingt-dix-neuf écritures en base et un
exploitant qui ne sait pas lesquelles. Voir `verifier_l_environnement`.

⚠️ **TROISIÈME : LA REPRISE PASSE PAR LA MÊME PORTE QUE LA SAISIE.**

Il serait plus court d'écrire dans le dépôt directement. Ce serait aussi le moyen
sûr de faire entrer par l'import ce que la saisie refuse : un journal inventé, un
compte hors plan, un exercice déjà déposé à la DGI. Une porte dérobée n'est pas
une porte dérobée parce qu'on la cache : c'est une porte dérobée parce qu'elle
n'a pas la même serrure.

⚠️ **QUATRIÈME (PAS 85) : UNE ÉCRITURE DÉJÀ PRÉSENTE N'ENTRE PAS UNE SECONDE FOIS.**

Essai avant correction : le même fichier, appliqué deux fois au même dossier, a fait
passer le journal des achats de 2 à 4 puis 6 écritures, chaque fois « appliqué ».
Un double clic, un rafraîchissement, un collègue qui reprend le même export :
l'exercice est doublé, et comme un brouillon ne se supprime pas (Q23), il n'y a pas
de retour. Réimporter dans un dossier son propre export faisait la même chose.

Une écriture du fichier est donc refusée, en anomalie, quand le dossier contient
déjà une écriture **de même contenu** : même journal, même date, même référence
externe, mêmes lignes (compte, sens, montant). Le libellé n'en fait pas partie : un
autre logiciel le reformate, et le doublon passerait. Deux écritures identiques dans
le même fichier sont refusées pour la même raison.

Ce n'est pas une empreinte du fichier : un fichier retouché d'une ligne passerait
le contrôle et doublerait tout le reste.

⚠️ **ET UN FICHIER SANS ÉCRITURE NE « S'APPLIQUE » PAS.** Il rendait `applique: true`
sans rien écrire : l'exploitant croyait sa reprise faite.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.contextes.comptabilite.application.tenue_du_journal import (
    BrouillonEcriture,
    SaisieRefusee,
    enregistrer_une_ecriture,
    verifier_l_environnement,
)
from app.contextes.comptabilite.domaine.cloture_mensuelle import PeriodeVerrouillee
from app.contextes.comptabilite.domaine.echange import Anomalie, LotLu
from app.contextes.comptabilite.domaine.entites import Compte, Journal, LigneEcriture
from app.contextes.comptabilite.domaine.ports import DepotEcritures
from app.contextes.portefeuille.contrats import Exercice

__all__ = ["RapportDeReprise", "reprendre_un_lot"]


class RapportDeReprise(BaseModel):
    """Ce que la reprise a fait, ou ce qu'elle aurait fait.

    ⚠️ La même forme dans les deux modes, contrôle et application. Un exploitant
    qui lit un rapport de contrôle doit y voir **exactement** ce qu'il obtiendra
    en appliquant ; deux formes différentes l'obligeraient à comparer, et il ne le
    ferait pas.
    """

    model_config = ConfigDict(frozen=True)

    #: Faux en mode contrôle : rien n'a été écrit, le rapport est une prévision.
    applique: bool
    lignes_lues: int = Field(ge=0)
    ecritures_lues: int = Field(ge=0)
    #: Les clés attribuées **par le registre**, dans l'ordre du fichier. Vides en
    #: mode contrôle, puisque le numéro se tire au moment d'écrire.
    cles_enregistrees: tuple[str, ...] = ()
    anomalies: tuple[Anomalie, ...] = ()

    # ⚠️ Pas 85 : exposé dans la réponse. Une simple propriété n'était pas sérialisée,
    # et un écran aurait dû recalculer la règle, donc la recopier.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def recevable(self) -> bool:
        return not self.anomalies and self.ecritures_lues > 0


#: Ce qui fait qu'une écriture est « la même » qu'une autre. Voir l'en-tête, point quatre.
Empreinte = tuple[str, date, str, tuple[tuple[str, str, Decimal], ...]]


def empreinte(
    journal: str, date_operation: date, reference_externe: str | None, lignes: list[LigneEcriture]
) -> Empreinte:
    """Journal, date, référence externe et lignes, sans le libellé.

    ⚠️ Les montants restent des `Decimal` : `Decimal("2840000.00")` et `Decimal("2840000")`
    sont égaux et ont la même empreinte de hachage, là où leurs écritures en texte
    différeraient et laisseraient passer le doublon d'un fichier arrondi autrement.
    """
    return (
        journal,
        date_operation,
        reference_externe or "",
        tuple(sorted((ligne.compte, str(ligne.sens), ligne.montant) for ligne in lignes)),
    )


def reprendre_un_lot(
    lot: LotLu,
    *,
    journaux: list[Journal],
    plan: list[Compte],
    exercice: Exercice | None,
    periodes_verrouillees: list[PeriodeVerrouillee] | tuple[PeriodeVerrouillee, ...],
    depot: DepotEcritures,
    par: str,
    appliquer: bool,
) -> RapportDeReprise:
    """Contrôle le lot entier, puis l'enregistre s'il est demandé et recevable.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LE MODE CONTRÔLE EST LE DÉFAUT, ET CE N'EST PAS UNE PRÉCAUTION D'USAGE.**

    Reprendre un exercice entier est irréversible, et pas seulement en pratique :
    les écritures entrent en brouillon, et **aucune route ne supprime un brouillon**.
    Ce texte disait « donc elles se suppriment » ; c'était faux (pas 72). Un brouillon
    se corrige à son numéro, se valide, se contre-passe : quatre mille brouillons
    d'un mauvais fichier n'ont pas d'autre issue, et la question est ouverte (Q23).
    Un exploitant qui se trompe de fichier ou de dossier doit pouvoir s'en
    apercevoir **avant**, et le rapport de contrôle lui dit exactement ce qui
    entrerait.

    L'application est donc un second acte, explicite, sur un fichier déjà vu.
    ─────────────────────────────────────────────────────────────────────────────
    """
    anomalies = list(lot.anomalies)
    brouillons: list[BrouillonEcriture] = []
    if not lot.ecritures and not lot.anomalies:
        anomalies.append(
            Anomalie(
                ligne=1,
                motif="le fichier ne contient aucune écriture : rien ne serait repris.",
            )
        )
    # Pas 85 : ce que le dossier contient déjà, dans cet exercice, quel que soit l'état.
    deja_presentes = {
        empreinte(e.journal, e.date_operation, e.reference_externe, list(e.lignes))
        for e in depot.lister(exercice.libelle)
    } if exercice is not None else set()
    vues_dans_le_fichier: set[Empreinte] = set()

    # ⚠️ **Le rang cité est celui du fichier, pas celui de l'écriture.** Le champ
    # `ligne` d'une anomalie est documenté comme « le numéro dans le fichier » :
    # y mettre un indice d'écriture ferait chercher la ligne 3 du tableur pour
    # une écriture qui commence à la ligne 47.
    for indice, ecriture in enumerate(lot.ecritures):
        rang = lot.rangs[indice] if indice < len(lot.rangs) else 1
        brouillon = BrouillonEcriture(
            journal=ecriture.journal,
            exercice=ecriture.exercice,
            date_operation=ecriture.date_operation,
            libelle=ecriture.libelle,
            piece_justificative=ecriture.piece_justificative,
            # ⚠️ Le numéro d'origine survit ici quand le fichier ne portait pas de
            # référence. Sans lui, le comptable qui cherche la pièce au classeur
            # de l'adhérent n'a plus aucun point commun entre les deux systèmes.
            reference_externe=(
                ecriture.reference_externe
                or f"{ecriture.journal}/{ecriture.numero} (origine)"
            ),
            lignes=list(ecriture.lignes),
        )
        try:
            # ⚠️ Pas 107 : une écriture de reprise datée d'un mois verrouillé est une
            # anomalie comme une autre, nommée à sa ligne ; le lot entier reste refusé.
            verifier_l_environnement(
                brouillon,
                journaux=journaux,
                plan=plan,
                exercice=exercice,
                periodes_verrouillees=periodes_verrouillees,
            )
        except SaisieRefusee as refus:
            anomalies.append(
                Anomalie(
                    ligne=rang,
                    motif=(
                        f"écriture {ecriture.journal}/{ecriture.numero} du fichier : "
                        f"{refus}"
                    ),
                )
            )
            continue
        signature = empreinte(
            brouillon.journal,
            brouillon.date_operation,
            brouillon.reference_externe,
            list(brouillon.lignes),
        )
        if signature in deja_presentes or signature in vues_dans_le_fichier:
            anomalies.append(
                Anomalie(
                    ligne=rang,
                    motif=(
                        f"écriture {ecriture.journal}/{ecriture.numero} du fichier : "
                        + (
                            "une écriture identique (journal, date, référence, lignes) "
                            "existe déjà dans ce dossier. Le fichier a-t-il déjà été repris ?"
                            if signature in deja_presentes
                            else "elle figure deux fois dans le fichier."
                        )
                    ),
                )
            )
            continue
        vues_dans_le_fichier.add(signature)
        brouillons.append(brouillon)

    # ⚠️ Une anomalie quelconque arrête tout, y compris celles venues de la
    # lecture. Écrire les écritures saines d'un lot qui en contient de mauvaises
    # produirait une balance incomplète, et l'exploitant croirait sa reprise
    # faite.
    if anomalies or not appliquer:
        return RapportDeReprise(
            applique=False,
            lignes_lues=lot.lignes_lues,
            ecritures_lues=len(lot.ecritures),
            anomalies=tuple(anomalies),
        )

    cles = tuple(
        enregistrer_une_ecriture(
            brouillon,
            journaux=journaux,
            plan=plan,
            exercice=exercice,
            periodes_verrouillees=periodes_verrouillees,
            depot=depot,
            par=par,
        ).cle
        for brouillon in brouillons
    )
    return RapportDeReprise(
        applique=True,
        lignes_lues=lot.lignes_lues,
        ecritures_lues=len(lot.ecritures),
        cles_enregistrees=cles,
    )
