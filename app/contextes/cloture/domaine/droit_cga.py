"""Le droit aux avantages d'un centre de gestion agréé, apprécié sur les faits.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE EXISTE

La liasse fiscale accordait l'abattement CGA sur **la parole de la requête**. Un
paramètre `adherent_sur_l_exercice`, envoyé par l'écran, valait `True` par défaut :
toute liasse déduisait l'abattement du bénéfice imposable, que l'entreprise ait
adhéré ou non, sauf si l'écran pensait à dire le contraire.

Sa description le reconnaissait : « passé et non déduit ». La dette était connue,
et son défaut était le plus dangereux des deux possibles.

⚠️ C'EST LE CENTRE QUI ATTESTE

L'abattement réduit l'impôt de l'adhérent parce que le Centre certifie que
l'entreprise relève de lui. Une liasse qui l'accorde à tort n'est pas une erreur
de calcul de l'adhérent : c'est une attestation fausse du Centre, et c'est son
agrément qui répond.

⚠️ DEUX CONDITIONS, ET LA SECONDE N'ÉTAIT VÉRIFIÉE NULLE PART

**L'adhésion couvre l'exercice.** Le portefeuille le dit, par
`adherente_sur_toute_la_periode`.

**Le chiffre d'affaires n'excède pas le seuil d'adhésion.** CGI art. 118 : le
centre assiste les entreprises dont le chiffre d'affaires annuel n'excède pas
100 millions. Le paramètre `SEUIL_ADHESION_CGA` existait au référentiel, validé,
avec une note qui disait « c'est le critère d'éligibilité du métier même du
cabinet » — **et aucune ligne de code ne le lisait.** Une entreprise à 300 millions
restée inscrite recevait l'abattement chaque année.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, computed_field

from app.contextes.portefeuille.contrats import Entreprise, Exercice
from app.contextes.referentiel.contrats import Borne, seuil_atteint

__all__ = ["DroitAuxAvantagesCGA", "apprecier_le_droit"]


class DroitAuxAvantagesCGA(BaseModel):
    """Ce que la liasse sait du droit à l'abattement, et pourquoi.

    ⚠️ Rendu **dans la liasse**, avec son motif. Une déduction qui apparaît ou
    disparaît sans explication fait croire à une erreur de calcul, et le réviseur
    la « corrigerait » en sens inverse.
    """

    model_config = ConfigDict(frozen=True)

    adherent_sur_tout_l_exercice: bool
    chiffre_affaires: Decimal
    #: `None` quand le référentiel ne dit rien à la date de clôture.
    seuil_adhesion: Decimal | None
    #: « n'excède pas » : la valeur même reste éligible. `None` avec le seuil.
    borne_adhesion: Borne | None = None
    motif: str

    @computed_field
    @property
    def ouvert(self) -> bool:
        return (
            self.adherent_sur_tout_l_exercice
            and self.seuil_adhesion is not None
            and self.borne_adhesion is not None
            and not seuil_atteint(self.chiffre_affaires, self.seuil_adhesion, self.borne_adhesion)
        )


def apprecier_le_droit(
    entreprise: Entreprise,
    exercice: Exercice,
    *,
    chiffre_affaires: Decimal,
    seuil_adhesion: Decimal | None,
    borne_adhesion: Borne | None,
) -> DroitAuxAvantagesCGA:
    """Les deux conditions, et le motif de la première qui manque.

    ⚠️ **Un seuil inconnu ferme le droit.** Le référentiel qui ne dit rien à la date
    de clôture ne permet pas d'attester l'éligibilité, et attester sans pouvoir le
    faire est précisément la faute que ce module existe pour empêcher.

    ⚠️ **La borne vient du référentiel, avec la valeur.** « N'excède pas » : une
    entreprise à 100 000 000 exactement relève encore du centre. La première
    version l'écrivait ici avec `<=` ; elle avait raison, et par hasard.
    """
    adherent = entreprise.adherente_sur_toute_la_periode(exercice.ouverture, exercice.cloture)

    if not adherent:
        motif = (
            f"aucune adhésion ne couvre l'exercice {exercice.libelle} du premier au "
            "dernier jour. Une adhésion prise ou reprise en cours d'exercice n'ouvre "
            "pas l'abattement pour cet exercice (question ouverte Q17)."
        )
    elif seuil_adhesion is None or borne_adhesion is None:
        motif = (
            f"le référentiel ne fixe aucun seuil d'adhésion au {exercice.cloture:%d/%m/%Y} : "
            "l'éligibilité ne peut pas être attestée."
        )
    elif seuil_atteint(chiffre_affaires, seuil_adhesion, borne_adhesion):
        motif = (
            f"chiffre d'affaires de {chiffre_affaires:,.0f} FCFA, au-delà du seuil "
            f"d'adhésion de {seuil_adhesion:,.0f} FCFA (CGI art. 118). L'entreprise ne "
            "relève plus d'un centre de gestion agréé pour cet exercice."
        ).replace(",", " ")
    else:
        motif = "adhésion sur tout l'exercice, chiffre d'affaires sous le seuil d'adhésion."

    return DroitAuxAvantagesCGA(
        adherent_sur_tout_l_exercice=adherent,
        chiffre_affaires=chiffre_affaires,
        seuil_adhesion=seuil_adhesion,
        borne_adhesion=borne_adhesion,
        motif=motif,
    )
