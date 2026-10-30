"""Le registre des tiers d'un dossier : ses fournisseurs et ses clients (16 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI

Les modèles « MODELE FOURNISSEURS » et « MODELE TIERS » de la DGI demandent, pour chaque
tiers, son sigle, son téléphone, son pays de résidence et sa ville. Une facture lue donne
le NIU et la raison sociale, rien de plus : ces colonnes partaient vides. Le registre
existait au dossier (`Tiers`), mais rien ne permettait de le remplir.

Le comptable complète ici la fiche d'un tiers, une fois : chaque export la reprend, sans
seconde saisie.

LA CLÉ
`code` est la référence du tiers dans les écritures : un code interne, ou le NIU quand
l'écriture est née d'une facture lue. Renseigner un code inconnu crée la fiche ; un code
connu la remplace en entier, comme le formulaire l'affiche.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.portefeuille.adaptateurs.entrant.routes_http import depot, lire_le_dossier
from app.contextes.portefeuille.domaine.entites import Tiers, TypeTiers
from app.contextes.transverse.api import AccesRequis, Permission, atelier, exiger_dossier
from app.partage.horloge import maintenant

__all__ = ["OBJET_TIERS", "routeur"]

routeur = APIRouter(prefix="/portefeuille", tags=["Portefeuille · registre des tiers"])

#: Le type d'objet des fiches de tiers au journal d'audit ; l'objet est « niu/code ».
OBJET_TIERS = "tiers"


class SaisieDuTiers(BaseModel):
    model_config = ConfigDict(extra="forbid")

    denomination: str = Field(min_length=1, max_length=200)
    type: TypeTiers = TypeTiers.FOURNISSEUR
    niu: str | None = Field(default=None, max_length=20)
    sigle: str | None = Field(default=None, max_length=40)
    telephone: str | None = Field(default=None, max_length=40)
    ville: str | None = Field(default=None, max_length=80)
    etranger: bool = False
    #: Lu seulement pour un tiers étranger.
    pays: str | None = Field(default=None, max_length=80)


def _net(valeur: str | None) -> str | None:
    """Un champ effacé dans le formulaire arrive vide : il ne vaut rien."""
    return (valeur or "").strip() or None


@routeur.get(
    "/entreprises/{niu}/tiers",
    summary="Le registre des tiers du dossier",
)
def lire_le_registre(acces: AccesRequis, niu: str) -> list[Tiers]:
    exiger_dossier(acces, Permission.LIRE_DOSSIER, niu)
    return sorted(lire_le_dossier(niu).tiers, key=lambda t: t.denomination.casefold())


@routeur.put(
    "/entreprises/{niu}/tiers/{code}",
    summary="Renseigner la fiche d'un tiers : la crée, ou la remplace",
)
def renseigner_un_tiers(acces: AccesRequis, niu: str, code: str, saisie: SaisieDuTiers) -> Tiers:
    exiger_dossier(acces, Permission.SAISIR_ECRITURE, niu)
    entreprise = lire_le_dossier(niu)
    tiers = Tiers(
        code=code,
        denomination=saisie.denomination.strip(),
        type=saisie.type,
        niu=_net(saisie.niu),
        sigle=_net(saisie.sigle),
        telephone=_net(saisie.telephone),
        ville=_net(saisie.ville),
        etranger=saisie.etranger,
        pays=_net(saisie.pays) if saisie.etranger else None,
    )
    # Les champs que ce formulaire ne montre pas (RCCM, régime) se gardent.
    ancien = next((t for t in entreprise.tiers if t.code == code), None)
    if ancien is not None:
        tiers = tiers.model_copy(update={"rccm": ancien.rccm, "regime": ancien.regime})
    registre = [t for t in entreprise.tiers if t.code != code] + [tiers]
    depot().enregistrer(entreprise.model_copy(update={"tiers": registre}))
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action="portefeuille.tiers_renseigne",
        objet_type=OBJET_TIERS,
        objet_id=f"{niu}/{code}",
        avant=ancien.model_dump(mode="json") if ancien else None,
        apres=tiers.model_dump(mode="json"),
    )
    return tiers
