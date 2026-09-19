"""Voir venir le franchissement de seuil, plutôt que le constater.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE EXISTE

Le domaine du portefeuille porte depuis longtemps `diagnostiquer_seuil`, avec une
docstring qui dit exactement ce qu'il faut en faire :

> *Une entreprise passe au régime du réel en septembre. Son comptable, qui suit le
> dossier de loin, continue de facturer sans TVA jusqu'en décembre. Au contrôle,
> l'administration considère le prix perçu comme un montant toutes taxes comprises
> et en extrait la taxe : l'entreprise doit alors une TVA qu'elle n'a jamais
> encaissée, majorée des pénalités.*
>
> *Le rôle du logiciel n'est pas de constater le franchissement après coup — c'est
> de l'annoncer avant.*

**Personne ne l'appelait.** La fonction attend un chiffre d'affaires en argument,
et aucun code n'en calculait un. C'est la quatrième capacité sans appelant trouvée
dans ce projet.

⚠️ POURQUOI ICI, ET PAS AU PORTEFEUILLE

Le portefeuille ne dépend d'aucun contexte, et c'est voulu : il porte l'histoire
juridique des dossiers, qui ne doit rien à la comptabilité. Or le chiffre
d'affaires vient des livres.

Le contexte F · Obligations voit les deux, et c'est sa raison d'être : **le seuil
commande ce que l'entreprise doit.** Le franchir ne change pas le dossier, il
change les obligations — assujettissement à la TVA, périodicité des déclarations,
tenue d'une comptabilité complète.

⚠️ DEUX MESURES, DEUX SENS DIFFÉRENTS, ET LES CONFONDRE COÛTE CHER

    l'exercice clos      un FAIT      le chiffre est définitif, le franchissement
                                      est acquis, le reclassement est dû
    l'exercice en cours  une VEILLE   le chiffre est partiel, le taux d'approche
                                      sous-estime, et c'est la seule mesure qui
                                      permette encore d'agir

Ne regarder que l'exercice clos revient à constater après coup, c'est-à-dire à
faire précisément ce que le module du portefeuille dit de ne pas faire. Ne
regarder que l'exercice en cours ferait manquer un franchissement acquis.

⚠️ CE MODULE N'EXTRAPOLE PAS, ET C'EST UNE DÉCISION

Un chiffre d'affaires de 60 % du seuil à mi-exercice « annonce » 120 % en fin
d'année, et il serait tentant de le dire. Ce serait inventer un nombre.

Une entreprise saisonnière rend l'extrapolation linéaire fausse : une école
réalise son chiffre en septembre, un négociant de matériaux en saison sèche. Le
rapport porte donc le chiffre réel **et la part de l'exercice écoulée**, pour que
celui qui lit juge lui-même. *Un logiciel qui extrapole sans le dire fait prendre
une projection pour un fait.*
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.contextes.comptabilite.contrats import SoldeCompte, chiffre_affaires
from app.contextes.portefeuille.contrats import (
    DiagnosticSeuil,
    Entreprise,
    Exercice,
    RegimeFiscal,
    diagnostiquer_seuil,
)
from app.contextes.referentiel.contrats import Borne

__all__ = [
    "MesureDeSeuil",
    "SurveillanceDuDossier",
    "surveiller_le_portefeuille",
    "surveiller_un_dossier",
]


class MesureDeSeuil(BaseModel):
    """Un diagnostic, et ce qu'il faut savoir pour le lire.

    ⚠️ `part_ecoulee` est la clé de lecture. Sur un exercice clos elle vaut 1 et
    le diagnostic est un fait. Sur un exercice en cours elle dit combien du
    chiffre reste à venir, et donc à quel point le taux d'approche sous-estime.
    """

    model_config = ConfigDict(frozen=True)

    exercice: str
    clos: bool
    #: La part de l'exercice déjà écoulée à la date d'observation, entre 0 et 1.
    part_ecoulee: Decimal = Field(ge=0, le=1)
    diagnostic: DiagnosticSeuil


class SurveillanceDuDossier(BaseModel):
    """Ce que le centre doit savoir d'un dossier au regard du seuil."""

    model_config = ConfigDict(frozen=True)

    niu: str
    denomination: str
    a_la_date: date
    seuil: Decimal
    borne: Borne

    #: Le dernier exercice clos, s'il en existe un. Un franchissement y est un
    #: **fait** : le reclassement est dû.
    acquis: MesureDeSeuil | None = None
    #: L'exercice en cours, s'il en existe un. Un franchissement y est une
    #: **veille** : le chiffre est partiel.
    en_cours: MesureDeSeuil | None = None

    #: La date d'effet d'un passage au réel **déjà inscrit** et pas encore entré en
    #: vigueur, s'il en existe un.
    #:
    #: ⚠️ Sans ce champ, la surveillance punissait le cabinet qui fait son
    #: travail. Un franchissement vu en novembre prend effet au 1er janvier ; le
    #: réviseur l'inscrit aussitôt, et le dossier restait pourtant signalé
    #: jusqu'au 1er janvier, puisque le régime **en vigueur** était encore l'IGS.
    #: Deux mois d'alerte sur un dossier réglé : le collaborateur apprend à
    #: l'ignorer, et il ignore aussi la vraie.
    reclassement_inscrit_au: date | None = None

    # ⚠️ **`computed_field`, et non `property` seule.** Une propriété ordinaire ne
    # sort pas en JSON : l'écran recevrait le diagnostic sans la conclusion, et
    # devrait refaire le raisonnement chez lui — c'est-à-dire l'écrire une seconde
    # fois, et le voir diverger au premier correctif.
    #
    # Le défaut s'est produit ici même, et sans bruit : un cas affirmait que tous
    # les dossiers rendus étaient à surveiller, et il passait parce que la liste
    # était vide.
    @computed_field
    @property
    def reclassement_du(self) -> bool:
        """Le franchissement est acquis sur un exercice clos, et rien n'a été inscrit."""
        return (
            self.reclassement_inscrit_au is None
            and self.acquis is not None
            and self.acquis.diagnostic.reclassement_requis
        )

    @computed_field
    @property
    def a_surveiller(self) -> bool:
        """Quelque chose mérite le regard d'un collaborateur.

        ⚠️ Un franchissement **déjà acquis** compte ici aussi : un écran qui ne
        listerait que les alertes anticipées laisserait sortir les dossiers où il
        est trop tard pour prévenir et encore temps de régulariser.
        """
        if self.reclassement_inscrit_au is not None:
            # ⚠️ Le passage au réel est décidé et daté : l'approche du seuil n'est
            # plus une information, c'est un fait déjà traité.
            return False
        return self.reclassement_du or (
            self.en_cours is not None and self.en_cours.diagnostic.a_signaler
        )


def surveiller_un_dossier(
    entreprise: Entreprise,
    *,
    soldes_de: Callable[[str], list[SoldeCompte]],
    seuil: Decimal,
    borne: Borne,
    a_la_date: date,
) -> SurveillanceDuDossier:
    """Mesure le dossier sur son dernier exercice clos et sur celui en cours.

    ⚠️ **`soldes_de` reçoit le libellé d'exercice**, et non l'entreprise : c'est
    l'appelant qui sait dans quel dossier il lit, et lui passer l'entreprise
    l'obligerait à la retrouver. La fonction ne connaît donc aucun dépôt.
    """
    acquis = _mesurer(
        entreprise, _dernier_clos(entreprise, a_la_date), soldes_de, seuil, borne, a_la_date
    )
    en_cours = _mesurer(
        entreprise, _en_cours(entreprise, a_la_date), soldes_de, seuil, borne, a_la_date
    )
    return SurveillanceDuDossier(
        niu=entreprise.niu,
        denomination=entreprise.denomination,
        a_la_date=a_la_date,
        seuil=seuil,
        borne=borne,
        acquis=acquis,
        en_cours=en_cours,
        reclassement_inscrit_au=_reel_a_venir(entreprise, a_la_date),
    )


def surveiller_le_portefeuille(
    dossiers: Iterable[Entreprise],
    *,
    soldes_de: Callable[[str, str], list[SoldeCompte]],
    seuil: Decimal,
    borne: Borne,
    a_la_date: date,
) -> list[SurveillanceDuDossier]:
    """La revue du portefeuille, triée par urgence décroissante.

    ⚠️ **L'ordre est le produit.** Un tableau de cent dossiers dans l'ordre des NIU
    ne se lit pas : le collaborateur le parcourt une fois, puis plus jamais. Les
    reclassements dus viennent en tête, puis les approches les plus avancées.
    """
    vues = [
        surveiller_un_dossier(
            entreprise,
            soldes_de=lambda exercice, niu=entreprise.niu: soldes_de(niu, exercice),
            seuil=seuil,
            borne=borne,
            a_la_date=a_la_date,
        )
        for entreprise in dossiers
    ]
    return sorted(vues, key=_urgence, reverse=True)


def _urgence(vue: SurveillanceDuDossier) -> tuple[int, Decimal]:
    approche = (
        vue.en_cours.diagnostic.taux_d_approche if vue.en_cours is not None else Decimal(0)
    )
    acquise = vue.acquis.diagnostic.taux_d_approche if vue.acquis is not None else Decimal(0)
    return (1 if vue.reclassement_du else 0, max(approche, acquise))


def _mesurer(
    entreprise: Entreprise,
    exercice: Exercice | None,
    soldes_de: Callable[[str], list[SoldeCompte]],
    seuil: Decimal,
    borne: Borne,
    a_la_date: date,
) -> MesureDeSeuil | None:
    if exercice is None:
        return None
    return MesureDeSeuil(
        exercice=exercice.libelle,
        clos=exercice.clos,
        part_ecoulee=_part_ecoulee(exercice, a_la_date),
        diagnostic=diagnostiquer_seuil(
            entreprise,
            chiffre_affaires(soldes_de(exercice.libelle)),
            seuil,
            # ⚠️ Le régime est lu **à la date d'observation**, pas à celle de
            # l'exercice mesuré. C'est la bonne lecture : la question posée est
            # « que doit faire ce dossier aujourd'hui », et un reclassement déjà
            # inscrit doit faire disparaître l'alerte, pas la maintenir.
            a_la_date,
            borne=borne,
        ),
    )


def _part_ecoulee(exercice: Exercice, a_la_date: date) -> Decimal:
    """Combien de l'exercice est derrière nous, entre 0 et 1.

    ⚠️ Bornée des deux côtés. Une date antérieure à l'ouverture rend 0, une date
    postérieure à la clôture rend 1 : sans cela, un exercice clos observé un an
    plus tard rendrait une part supérieure à 1, que le modèle refuserait, et la
    surveillance échouerait sur un dossier ancien.
    """
    total = (exercice.cloture - exercice.ouverture).days + 1
    ecoule = (a_la_date - exercice.ouverture).days + 1
    if ecoule <= 0:
        return Decimal(0)
    if ecoule >= total:
        return Decimal(1)
    return (Decimal(ecoule) / Decimal(total)).quantize(Decimal("0.0001"))


def _reel_a_venir(entreprise: Entreprise, a_la_date: date) -> date | None:
    """Le premier passage au réel inscrit après la date d'observation.

    ⚠️ **Au réel, et seulement si le dossier n'y est pas déjà.** Un statut futur à
    l'IGS n'éteint rien : c'est un retour, et il ne répond pas à un franchissement.
    """
    if entreprise.regime_au(a_la_date) is RegimeFiscal.REEL:
        return None
    return min(
        (
            s.debut
            for s in entreprise.regimes
            if s.debut > a_la_date and s.regime is RegimeFiscal.REEL
        ),
        default=None,
    )


def _dernier_clos(entreprise: Entreprise, a_la_date: date) -> Exercice | None:
    """Le dernier exercice clos avant la date d'observation.

    ⚠️ **Clos, et non pas simplement terminé.** Un exercice dont la date de clôture
    est passée mais que le cabinet n'a pas arrêté porte encore des écritures à
    venir : son chiffre d'affaires n'est pas définitif, et le présenter comme un
    fait ferait réclamer un reclassement sur un chiffre qui va bouger.
    """
    passes = sorted(
        (e for e in entreprise.exercices if e.clos and e.cloture <= a_la_date),
        key=lambda e: e.cloture,
    )
    return passes[-1] if passes else None


def _en_cours(entreprise: Entreprise, a_la_date: date) -> Exercice | None:
    return next(
        (
            e
            for e in entreprise.exercices
            if not e.clos and e.ouverture <= a_la_date <= e.cloture
        ),
        None,
    )
