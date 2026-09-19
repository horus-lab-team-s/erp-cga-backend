"""Vérification des identifiants fiscaux contre le référentiel daté.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CETTE VÉRIFICATION N'EST PAS DANS L'ENTITÉ

Le format du NIU et celui du RCCM sont des **valeurs légales datées** : ils vivent
au référentiel normatif, sous les codes `FORMAT_NIU` et `FORMAT_RCCM`, et ils se
lisent à une date. Le jour où la DGI change la structure du NIU, on ajoutera une
version au paramètre — et les entreprises immatriculées avant resteront valides
sous l'ancien format.

Une entité du domaine ne peut pas faire cette lecture : elle n'a le droit
d'importer que les *contrats* d'un autre contexte, jamais son service. C'est la
règle vérifiée par `test_architecture.py`, et elle est juste — sans elle, le
cercle interne dépendrait du cercle externe par la bande.

La vérification appartient donc à la couche cas d'usage, qui reçoit le service de
lecture du référentiel.

POURQUOI ELLE REND DES ANOMALIES PLUTÔT QUE DE LEVER

Un portefeuille repris d'un autre cabinet contient toujours des identifiants
douteux. Refuser de charger le dossier rendrait la reprise impossible ; il faut
au contraire pouvoir **lister** ce qui cloche pour le corriger. C'est la même
logique que `RegleEnEchec` dans le moteur de conformité : un défaut signalé vaut
mieux qu'un traitement interrompu.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, ConfigDict

from app.contextes.portefeuille.domaine.entites import Entreprise
from app.contextes.referentiel.api import (
    AucuneVersionApplicable,
    ParametreInconnu,
    ServiceParametres,
)

__all__ = ["AnomalieIdentifiant", "verifier_identifiants", "verifier_portefeuille"]

CODE_FORMAT_NIU = "FORMAT_NIU"
CODE_FORMAT_RCCM = "FORMAT_RCCM"


class AnomalieIdentifiant(BaseModel):
    """Un identifiant absent ou malformé sur un dossier."""

    model_config = ConfigDict(frozen=True)

    niu_entreprise: str
    denomination: str
    champ: str
    valeur: str | None
    motif: str

    #: Vrai quand l'anomalie empêche un contrôle de conformité de conclure.
    #: Un NIU absent chez un adhérent, par exemple, rend impossible de vérifier
    #: qu'il est bien celui que la facture désigne.
    bloquante: bool = False


def _verifier_format(
    valeur: str | None,
    code_parametre: str,
    parametres: ServiceParametres,
    a_la_date: date,
) -> str | None:
    """Rend le motif de l'anomalie, ou `None` si le format est respecté.

    Un paramètre de format absent du référentiel est signalé comme tel plutôt
    qu'ignoré : ne pas pouvoir vérifier n'est pas la même chose que vérifier avec
    succès, et confondre les deux ferait passer un portefeuille entier pour sain.
    """
    if valeur is None:
        return None
    try:
        motif = parametres.valeur_texte(code_parametre, a_la_date)
    except (ParametreInconnu, AucuneVersionApplicable) as exc:
        return f"format non vérifiable — {exc}"
    if re.match(motif, valeur):
        return None
    return f"ne respecte pas le format en vigueur au {a_la_date}"


def verifier_identifiants(
    entreprise: Entreprise,
    parametres: ServiceParametres,
    a_la_date: date,
) -> list[AnomalieIdentifiant]:
    """Contrôle le NIU et le RCCM d'un dossier à une date donnée.

    La date compte : un dossier créé en 2015 se contrôle avec le format en
    vigueur en 2015. C'est le même principe que pour une facture.
    """
    anomalies: list[AnomalieIdentifiant] = []

    def signaler(champ: str, valeur: str | None, motif: str, *, bloquante: bool = False) -> None:
        anomalies.append(
            AnomalieIdentifiant(
                niu_entreprise=entreprise.niu,
                denomination=entreprise.denomination,
                champ=champ,
                valeur=valeur,
                motif=motif,
                bloquante=bloquante,
            )
        )

    motif = _verifier_format(entreprise.niu, CODE_FORMAT_NIU, parametres, a_la_date)
    if motif:
        signaler("niu", entreprise.niu, motif, bloquante=True)

    if entreprise.rccm is None:
        # Toutes les formes n'ont pas de RCCM — une personne physique non
        # commerçante, par exemple. On signale sans bloquer.
        signaler("rccm", None, "absent", bloquante=False)
    else:
        motif = _verifier_format(entreprise.rccm, CODE_FORMAT_RCCM, parametres, a_la_date)
        if motif:
            signaler("rccm", entreprise.rccm, motif)

    for tiers in entreprise.tiers:
        if tiers.etranger or tiers.niu is None:
            # Un fournisseur étranger n'a aucune raison d'avoir un NIU camerounais.
            continue
        motif = _verifier_format(tiers.niu, CODE_FORMAT_NIU, parametres, a_la_date)
        if motif:
            signaler(f"tiers.{tiers.code}.niu", tiers.niu, motif)

    return anomalies


def verifier_portefeuille(
    entreprises: list[Entreprise],
    parametres: ServiceParametres,
    a_la_date: date,
) -> list[AnomalieIdentifiant]:
    """Passe tout le portefeuille en revue.

    Destiné à la reprise d'un portefeuille et au contrôle périodique : il vaut
    mieux découvrir un NIU malformé lors d'une revue que le jour où
    l'administration refuse une déclaration.
    """
    anomalies: list[AnomalieIdentifiant] = []
    for entreprise in entreprises:
        anomalies.extend(verifier_identifiants(entreprise, parametres, a_la_date))
    return anomalies
