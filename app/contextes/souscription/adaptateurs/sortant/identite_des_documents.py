"""L'identité imprimée du cabinet : en-tête et pied des documents, et de la page web.

Lue dans `Docs/referentiel/cabinet/identite_des_documents.yaml` (9 novembre 2026). Les PDF
(proforma, facture, reçu, note d'honoraires) et la page web de la proforma lisent la même
source : ce que le client voit à l'écran est ce qu'il imprime.

⚠️ UN FICHIER ABSENT NE FAIT PAS ÉCHOUER LE RENDU, comme un logo absent : les valeurs du
8 novembre 2026 prennent le relais, et le document s'émet quand même. Un fichier mal formé,
lui, lève : une coquille ne doit pas s'imprimer en silence sur des centaines de documents.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.infrastructure.config import configuration

__all__ = ["IdentiteDesDocuments", "identite_des_documents"]


class IdentiteDesDocuments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    raison_sociale: str = Field(min_length=1)
    activite: str = ""
    site: str = ""
    courriel: str = ""
    pied: tuple[str, ...] = ()


#: Les valeurs imprimées jusqu'au 8 novembre 2026, si le fichier manque.
_PAR_DEFAUT = IdentiteDesDocuments(
    raison_sociale="CGA BROAD RANGE CONSULTING",
    activite="Centre de gestion agréé  ·  Comptabilité, fiscalité, juridique, formations",
    site="www.cga-brcgroup.com",
    courriel="contact@cga-brcgroup.com",
    pied=(
        "+237 233 424 847   ·   +237 699 902 184   ·   WhatsApp +237 699 902 184",
        "contact@cga-brcgroup.com   ·   www.cga-brcgroup.com",
        "Douala Akwa, B.P. 7761   ·   Yaoundé Elig-Essono   ·   Bafoussam Quartier Haoussa",
        "Agrément n° 00000048/MINFI/DGI du 28 janvier 2020",
    ),
)


def identite_des_documents(referentiel: Path | None = None) -> IdentiteDesDocuments:
    """Relue à chaque appel : un fichier de quelques lignes, corrigé sans redémarrer."""
    dossier = referentiel or Path(configuration().dossier_referentiel)
    fichier = dossier / "cabinet" / "identite_des_documents.yaml"
    if not fichier.is_file():
        return _PAR_DEFAUT
    return IdentiteDesDocuments.model_validate(yaml.safe_load(fichier.read_text(encoding="utf-8")))
