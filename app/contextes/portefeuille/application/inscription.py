"""L'inscription au portefeuille d'une entreprise qui existe déjà (16 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
LE MANQUE

Seule la création de société faisait entrer un dossier au portefeuille (la conversion du
contexte I). Une entreprise déjà immatriculée qui achète l'adhésion, la paie ou la tenue
n'y entrait par aucun chemin : payée, elle restait un dossier commercial.

DÉCISION DU CABINET : le cabinet l'inscrit, depuis le dossier commercial payé, qui
préremplit ce qu'il sait déjà ; un humain vérifie l'identité fiscale (NIU, RCCM, régime)
avant l'entrée au portefeuille.

CE QUE L'INSCRIPTION POSE, ET CE QU'ELLE NE POSE PAS

* le régime et le centre de rattachement **depuis la création** : le modèle exige une
  histoire continue. L'historique antérieur n'est pas repris, et la précision le dit ;
* l'exercice en cours, de sa clôture remontée d'un an (ou de la création si elle est
  plus récente) ;
* le groupe de risque CNPS et le numéro employeur s'ils sont connus ;
* **pas l'adhésion au Centre** : elle se numérote et se contrôle (seuil de chiffre
  d'affaires) par sa propre route, `POST /portefeuille/entreprises/{niu}/adhesions`.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, timedelta

from app.contextes.portefeuille.domaine.entites import (
    CentreRattachement,
    Dirigeant,
    Entreprise,
    Exercice,
    FormeJuridique,
    MotifChangement,
    RegimeFiscal,
    StatutRattachement,
    statut_initial,
)
from app.contextes.portefeuille.domaine.fiche_signaletique import FicheSignaletique

__all__ = ["InscriptionImpossible", "inscrire_une_entreprise_existante"]


class InscriptionImpossible(ValueError):
    """Les données ne font pas un dossier cohérent. Rendue en 422."""


def inscrire_une_entreprise_existante(
    *,
    niu: str,
    denomination: str,
    forme_juridique: FormeJuridique,
    date_creation: date,
    regime: RegimeFiscal,
    centre: CentreRattachement,
    cloture_exercice_en_cours: date,
    origine: str,
    a_la_date: date,
    rccm: str | None = None,
    activite: str | None = None,
    siege: str | None = None,
    dirigeant: tuple[str, str] | None = None,
    telephone: str | None = None,
    numero_cnps_employeur: str | None = None,
    groupe_risque_cnps: str | None = None,
    groupe_risque_notifie_le: date | None = None,
) -> Entreprise:
    """Le dossier d'une entreprise existante, prêt à enregistrer. `origine` nomme le
    dossier commercial dont il vient, dans les précisions de ses statuts."""
    if date_creation > a_la_date:
        raise InscriptionImpossible(
            f"création datée du {date_creation:%d/%m/%Y}, après aujourd'hui : une "
            "entreprise existante est déjà née."
        )
    if cloture_exercice_en_cours < a_la_date:
        raise InscriptionImpossible(
            f"l'exercice en cours ne peut pas être clos le "
            f"{cloture_exercice_en_cours:%d/%m/%Y}, date passée : indiquez la clôture de "
            "l'exercice qui court."
        )
    if (groupe_risque_cnps is None) != (groupe_risque_notifie_le is None):
        raise InscriptionImpossible(
            "le groupe de risque CNPS va avec la date de sa notification : les deux, ou aucun."
        )
    ouverture = max(
        date_creation, _veille_d_un_an(cloture_exercice_en_cours) + timedelta(days=1)
    )
    precision = (
        f"Déclaré à l'inscription au portefeuille, dossier {origine} ; "
        "historique antérieur non repris."
    )
    fiche = (
        FicheSignaletique(telephone=telephone, numero_cnps_employeur=numero_cnps_employeur)
        if telephone or numero_cnps_employeur
        else None
    )
    try:
        return Entreprise(
            niu=niu.strip().upper(),
            denomination=denomination.strip(),
            forme_juridique=forme_juridique,
            date_creation=date_creation,
            rccm=(rccm or "").strip() or None,
            activite=activite,
            siege=siege,
            fiche_dsf=fiche,
            groupe_risque_cnps=groupe_risque_cnps,
            groupe_risque_notifie_le=groupe_risque_notifie_le,
            suivi_depuis=a_la_date,
            regimes=[statut_initial(regime, date_creation, precision=precision)],
            rattachements=[
                StatutRattachement(
                    debut=date_creation,
                    fin=None,
                    centre=centre,
                    motif=MotifChangement.CREATION,
                    precision=precision,
                )
            ],
            exercices=[
                Exercice(
                    libelle=str(cloture_exercice_en_cours.year),
                    ouverture=ouverture,
                    cloture=cloture_exercice_en_cours,
                    clos=False,
                )
            ],
            dirigeants=(
                [Dirigeant(nom=dirigeant[0], qualite=dirigeant[1], depuis=date_creation)]
                if dirigeant
                else []
            ),
        )
    except ValueError as invalide:
        raise InscriptionImpossible(str(invalide)) from invalide


def _veille_d_un_an(jour: date) -> date:
    """Le même jour un an plus tôt ; le 29 février tombe au 28."""
    try:
        return jour.replace(year=jour.year - 1)
    except ValueError:
        return jour.replace(year=jour.year - 1, day=28)
