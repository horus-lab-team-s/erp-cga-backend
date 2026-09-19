"""Les règles de régime : franchissement de seuil et période probatoire.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE EXISTE

Le franchissement d'un seuil de chiffre d'affaires est le scénario qui coûte le
plus cher aux entreprises qui réussissent, et il les prend toujours par surprise.

Une entreprise passe au régime du réel en septembre. Son comptable, qui suit le
dossier de loin, continue de facturer sans TVA jusqu'en décembre. Au contrôle,
l'administration considère le prix perçu comme un montant toutes taxes comprises
et en extrait la taxe : l'entreprise doit alors une TVA **qu'elle n'a jamais
encaissée**, majorée des pénalités.

Le rôle du logiciel n'est pas de constater le franchissement après coup — c'est de
l'annoncer avant. D'où le palier d'alerte anticipée.

CE QUE CE MODULE NE FAIT PAS

Il ne connaît **aucun seuil**. Les valeurs vivent au référentiel normatif, datées,
et lui sont passées en argument. C'est le principe d'architecture n° 1, et il vaut
ici autant qu'ailleurs : un seuil changé par une loi de finances ne doit toucher
aucune ligne de code.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.contextes.portefeuille.domaine.entites import Entreprise, RegimeFiscal
from app.contextes.referentiel.contrats import Borne, seuil_atteint

__all__ = [
    "PALIER_ALERTE",
    "DiagnosticSeuil",
    "diagnostiquer_seuil",
    "retour_au_synthetique_admis",
]

#: Part du seuil à partir de laquelle on alerte, avant tout franchissement.
#:
#: Ce n'est pas une valeur légale : c'est un réglage de vigilance, qui appartient
#: au cabinet. À 80 %, il reste en général un trimestre pour s'organiser — obtenir
#: un numéro de TVA, prévenir les clients, adapter la facturation. Alerter au
#: franchissement lui-même serait déjà trop tard.
PALIER_ALERTE = Decimal("0.80")


class DiagnosticSeuil(BaseModel):
    """Où en est une entreprise par rapport à un seuil, et ce qu'il faut en faire."""

    model_config = ConfigDict(frozen=True)

    regime_actuel: RegimeFiscal
    chiffre_affaires: Decimal
    seuil: Decimal
    #: Ce que le texte dit de la valeur même du seuil. Rendu avec le diagnostic :
    #: « franchi » à 50 000 000 exactement ne se comprend qu'avec elle.
    borne: Borne

    #: Le chiffre d'affaires rapporté au seuil. 0,84 = 84 % du seuil atteint.
    taux_d_approche: Decimal

    franchi: bool

    #: Franchi **et** encore au régime inférieur : il y a quelque chose à faire.
    reclassement_requis: bool

    #: Pas encore franchi, mais assez proche pour prévenir.
    alerte_anticipee: bool

    @property
    def a_signaler(self) -> bool:
        return self.reclassement_requis or self.alerte_anticipee


def diagnostiquer_seuil(
    entreprise: Entreprise,
    chiffre_affaires: Decimal,
    seuil: Decimal,
    a_la_date: date,
    *,
    borne: Borne,
    palier: Decimal = PALIER_ALERTE,
) -> DiagnosticSeuil:
    """Compare le chiffre d'affaires au seuil, à la lumière du régime en vigueur.

    Le régime est lu **à la date demandée**, jamais « le régime actuel » : un
    diagnostic porté sur l'exercice 2024 doit raisonner avec le régime de 2024.

    Le seuil est reçu, pas connu. Il vient de
    `SEUIL_ASSUJETTISSEMENT_TVA` ou de `SEUIL_COMPTABILITE_OBLIGATOIRE` au
    référentiel, résolu à la même date.

    ⚠️ **`borne` est obligatoire, et ce n'est pas une précaution de style.** Cette
    fonction comparait avec `>=` pour les deux seuils qu'elle nomme ci-dessus, alors
    que le premier est « supérieur à » et le second « dès » : l'un des deux était
    forcément faux à sa borne, et c'était celui de la TVA. Une entreprise à
    50 000 000 exactement était déclarée en franchissement.
    """
    if seuil <= 0:
        raise ValueError(
            f"seuil {seuil} : un seuil nul ou négatif ne permet aucune comparaison. "
            "Vérifier la résolution du paramètre au référentiel."
        )

    regime = entreprise.regime_au(a_la_date)
    taux = (chiffre_affaires / seuil).quantize(Decimal("0.0001"))
    franchi = seuil_atteint(chiffre_affaires, seuil, borne)

    return DiagnosticSeuil(
        regime_actuel=regime,
        chiffre_affaires=chiffre_affaires,
        seuil=seuil,
        borne=borne,
        taux_d_approche=taux,
        franchi=franchi,
        reclassement_requis=franchi and regime is RegimeFiscal.IGS,
        alerte_anticipee=not franchi and taux >= palier,
    )


def retour_au_synthetique_admis(
    entreprise: Entreprise,
    a_la_date: date,
    exercices_probatoires: int,
) -> bool:
    """L'entreprise peut-elle redescendre au régime synthétique ?

    Le mécanisme existe pour empêcher qu'une entreprise n'oscille d'un régime à
    l'autre au gré de sa conjoncture : après un passage au réel, elle doit y
    demeurer un nombre donné d'exercices avant de pouvoir en sortir.

    Le décompte porte sur les exercices **entièrement écoulés et clos** depuis le
    début de la période au réel en cours. Un exercice à cheval sur le changement
    ne compte pas : le retenir abrégerait la période probatoire d'un exercice
    complet.

    `exercices_probatoires` vient du référentiel — `IGS_PERIODE_PROBATOIRE_EXERCICES`,
    deux exercices selon nos sources, au statut à valider.

    > **Question ouverte.** La période probatoire s'applique-t-elle de la même
    > façon à un reclassement *subi* — dépassement de seuil — et à une option
    > *volontaire* pour le réel ? Le décompte est ici le même dans les deux cas,
    > ce qui est le traitement le plus prudent. À faire confirmer.
    """
    if entreprise.regime_au(a_la_date) is RegimeFiscal.IGS:
        return False  # elle y est déjà : la question ne se pose pas

    depuis = entreprise.regime_courant.debut
    return entreprise.exercices_clos_depuis(depuis) >= exercices_probatoires
