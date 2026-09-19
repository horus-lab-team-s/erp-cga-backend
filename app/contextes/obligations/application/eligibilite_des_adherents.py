"""La revue des adhérents sortis, ou sortant, du champ de l'article 118.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE EXISTE

Le Centre admet une entreprise sur un chiffre d'affaires déclaré (pas 48), et
refuse l'abattement à la liasse quand les livres dépassent le seuil (pas 46). Entre
les deux, rien ne lui disait **qu'un adhérent grandissait**.

Or une entreprise qui dépasse 100 millions ne relève plus d'un centre de gestion
agréé. Découvrir le dépassement à la liasse, c'est le découvrir des mois après la
clôture, au moment où l'adhérent attend un abattement qu'il n'aura pas. Le Centre
doit le voir venir, le lui dire, et décider de la suite de l'adhésion.

⚠️ CE QUE CETTE REVUE N'EST PAS

Elle ne résilie rien et n'accorde rien. Sortir du champ de l'article 118 ne met pas
fin à l'adhésion de plein droit ; c'est une information que le cabinet doit porter
à son adhérent, et la décision reste la sienne. La résiliation, si elle vient, passe
par sa route, son motif et son journal.

⚠️ LA MÊME MÉCANIQUE QUE LA SURVEILLANCE DU RÉGIME, PAS LE MÊME VOCABULAIRE

Les deux mesures sont celles du pas 44, calculées par la même fonction :

    l'exercice clos      un FAIT      le dernier exercice arrêté dépasse le seuil
    l'exercice en cours  une VEILLE   le chiffre partiel approche ou dépasse

Mais le diagnostic du régime parle de « reclassement requis » et de « régime
actuel », qui n'ont aucun sens ici. Les rendre tels quels dans une revue d'adhésion
ferait lire à un collaborateur qu'un adhérent doit changer de régime fiscal, alors
qu'il s'agit de son éligibilité au Centre. Les mesures sont donc **retraduites**,
sans être recalculées.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.contextes.comptabilite.contrats import SoldeCompte
from app.contextes.obligations.application.surveillance_des_seuils import (
    MesureDeSeuil,
    surveiller_un_dossier,
)
from app.contextes.portefeuille.contrats import Entreprise
from app.contextes.referentiel.contrats import Borne

__all__ = [
    "EligibiliteDeLAdherent",
    "MesureDEligibilite",
    "revoir_l_eligibilite",
]


class MesureDEligibilite(BaseModel):
    """Un exercice mesuré contre le seuil d'adhésion."""

    model_config = ConfigDict(frozen=True)

    exercice: str
    clos: bool
    part_ecoulee: Decimal = Field(ge=0, le=1)
    chiffre_affaires: Decimal
    taux_d_approche: Decimal
    #: Le seuil est atteint au sens du texte, borne comprise : « n'excède pas »,
    #: donc 100 000 000 exactement reste dans le champ.
    au_dela_du_seuil: bool
    #: Pas encore au-delà, mais assez proche pour prévenir l'adhérent.
    alerte_anticipee: bool

    @classmethod
    def depuis(cls, mesure: MesureDeSeuil | None) -> MesureDEligibilite | None:
        if mesure is None:
            return None
        diagnostic = mesure.diagnostic
        return cls(
            exercice=mesure.exercice,
            clos=mesure.clos,
            part_ecoulee=mesure.part_ecoulee,
            chiffre_affaires=diagnostic.chiffre_affaires,
            taux_d_approche=diagnostic.taux_d_approche,
            au_dela_du_seuil=diagnostic.franchi,
            alerte_anticipee=diagnostic.alerte_anticipee,
        )


class EligibiliteDeLAdherent(BaseModel):
    """Ce que le Centre doit savoir d'un adhérent au regard de l'article 118."""

    model_config = ConfigDict(frozen=True)

    niu: str
    denomination: str
    numero_adhesion: str | None
    a_la_date: date
    seuil: Decimal
    borne: Borne
    acquis: MesureDEligibilite | None = None
    en_cours: MesureDEligibilite | None = None

    @computed_field
    @property
    def hors_champ(self) -> bool:
        """Le dernier exercice clos dépasse le seuil : c'est un fait, pas une prévision."""
        return self.acquis is not None and self.acquis.au_dela_du_seuil

    @computed_field
    @property
    def a_surveiller(self) -> bool:
        """Quelque chose mérite d'être porté à la connaissance de l'adhérent.

        ⚠️ **Un exercice en cours déjà au-delà du seuil compte**, et pas seulement
        l'alerte anticipée : le diagnostic ne lève plus d'alerte une fois le seuil
        franchi, et un adhérent à 120 % sur son exercice en cours sortirait sinon de
        la revue au moment précis où il faut l'appeler.
        """
        if self.hors_champ:
            return True
        return self.en_cours is not None and (
            self.en_cours.au_dela_du_seuil or self.en_cours.alerte_anticipee
        )


def revoir_l_eligibilite(
    dossiers: Iterable[Entreprise],
    *,
    soldes_de: Callable[[str, str], list[SoldeCompte]],
    seuil: Decimal,
    borne: Borne,
    a_la_date: date,
) -> list[EligibiliteDeLAdherent]:
    """Les adhérents du jour, mesurés contre le seuil d'adhésion, triés par urgence.

    ⚠️ **Seuls les dossiers adhérents à la date d'observation sont revus.** Un dossier
    hors du Centre qui dépasse 100 millions n'appelle aucune démarche du Centre ; le
    faire figurer noierait les adhérents qui en appellent une.

    ⚠️ **Le fait passe avant la prévision**, comme dans la revue du régime : un adhérent
    dont l'exercice clos dépasse le seuil précède un adhérent qui s'en approche,
    quel que soit le taux affiché.
    """
    revues: list[EligibiliteDeLAdherent] = []
    for entreprise in dossiers:
        adhesion = entreprise.adhesion_au(a_la_date)
        if adhesion is None:
            continue
        vue = surveiller_un_dossier(
            entreprise,
            soldes_de=lambda exercice, niu=entreprise.niu: soldes_de(niu, exercice),
            seuil=seuil,
            borne=borne,
            a_la_date=a_la_date,
        )
        revues.append(
            EligibiliteDeLAdherent(
                niu=entreprise.niu,
                denomination=entreprise.denomination,
                numero_adhesion=adhesion.numero,
                a_la_date=a_la_date,
                seuil=seuil,
                borne=borne,
                acquis=MesureDEligibilite.depuis(vue.acquis),
                en_cours=MesureDEligibilite.depuis(vue.en_cours),
            )
        )
    return sorted(revues, key=_urgence, reverse=True)


def _urgence(revue: EligibiliteDeLAdherent) -> tuple[int, int, Decimal]:
    taux = max(
        (m.taux_d_approche for m in (revue.acquis, revue.en_cours) if m is not None),
        default=Decimal(0),
    )
    return (int(revue.hors_champ), int(revue.a_surveiller), taux)
