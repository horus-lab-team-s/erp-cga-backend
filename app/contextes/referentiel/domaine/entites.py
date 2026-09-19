"""Modèles du référentiel normatif.

Principe non négociable du projet : **aucune valeur légale en dur dans le code, aucune
lecture de paramètre sans date**. Un paramètre n'est pas une constante, c'est une
fonction du temps.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StatutValidation(StrEnum):
    """Le statut ne bloque pas le calcul : il le marque.

    Un rapport produit à partir d'un paramètre `A_VALIDER` le signale, ce qui permet de
    livrer sans attendre la validation juridique complète, sans faire croire à une
    exactitude qui n'existe pas.
    """

    A_VALIDER = "A_VALIDER"
    VALIDE = "VALIDE"
    ABROGE = "ABROGE"


class NatureParametre(StrEnum):
    """Qui est compétent pour valider ce paramètre.

    ─────────────────────────────────────────────────────────────────────────────
    LE STATUT DIT *SI* C'EST VALIDÉ ; LA NATURE DIT *PAR QUI ÇA PEUT L'ÊTRE*

    Les deux se confondaient jusqu'ici, et cela produisait une absurdité : le poids
    du retard déclaratif dans le score de risque attendait la signature d'un
    fiscaliste, alors qu'aucun texte ne le fixe et qu'aucun fiscaliste ne peut donc
    l'attester. Il attendait une signature que personne n'avait qualité pour
    donner.

    `LOI` engage un professionnel sur un texte : CGI, loi de finances, Code du
    travail, actes uniformes OHADA. Se valide en confrontant la valeur au texte,
    et le fondement doit citer l'article.

    `POLITIQUE_CABINET` relève d'un arbitrage interne : pondérations du score de
    risque, fenêtre de détection des doublons, délais d'alerte. Se valide par une
    décision de la direction. Une valeur y est *choisie*, jamais *constatée*, et
    aucune loi ne la contredira jamais.

    ⚠️ Le défaut est `LOI`, et c'est délibéré : un paramètre dont on a oublié de
    déclarer la nature est traité comme engageant. L'oubli coûte alors une revue
    inutile, jamais une valeur légale passée sans contrôle.
    ─────────────────────────────────────────────────────────────────────────────
    """

    LOI = "LOI"
    POLITIQUE_CABINET = "POLITIQUE_CABINET"


class Unite(StrEnum):
    POURCENTAGE = "POURCENTAGE"
    FCFA = "FCFA"
    JOURS = "JOURS"
    JOUR_DU_MOIS = "JOUR_DU_MOIS"
    EXERCICES = "EXERCICES"
    REGEX = "REGEX"
    #: Sans dimension. Réservé aux réglages de pilotage — poids et seuils du score
    #: de risque —, qui ne relèvent d'aucun texte mais d'une décision de la
    #: direction. L'unité les distingue au premier coup d'œil des valeurs légales,
    #: et c'est utile : un fiscaliste qui parcourt le référentiel pour valider
    #: doit pouvoir sauter ce qui ne le concerne pas.
    POINTS = "POINTS"


class Borne(StrEnum):
    """Ce que le texte dit de la valeur même du seuil.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LA BORNE EST UNE DONNÉE LÉGALE, PAS UN OPÉRATEUR DU CODE.**

    Les textes ne parlent pas tous de la même façon, et la différence ne se voit
    qu'au franc près :

        « supérieur à 50 000 000 »       EXCLUSE   50 000 000 n'atteint pas
        « n'excède pas 100 000 000 »     EXCLUSE   100 000 000 reste en deçà
        « au moins égale à 100 000 »     INCLUSE   100 000 atteint
        « dès 10 M »                     INCLUSE   10 000 000 atteint

    Le diagnostic de seuil comparait avec `>=` pour tous les seuils qu'il déclarait
    servir, dont celui de la TVA, que le CGI dit « supérieur à ». Une entreprise à
    50 000 000 exactement était donc déclarée en franchissement, et invitée à un
    reclassement au réel que la loi ne lui impose pas. Aucun cas ne mesurait la
    borne, et rien ne pouvait la mesurer : elle n'était écrite nulle part.

    Une loi de finances peut changer la formulation sans changer la valeur. La
    borne vit donc **sur la version**, à côté de la valeur et de son fondement.
    ─────────────────────────────────────────────────────────────────────────────
    """

    #: Le seuil est atteint dès sa valeur : « à partir de », « dès », « au moins égal ».
    INCLUSE = "INCLUSE"
    #: Le seuil n'est atteint qu'au-delà : « supérieur à », « n'excède pas ».
    EXCLUSE = "EXCLUSE"


class SeuilSansBorne(ValueError):
    """Une comparaison a été demandée à un paramètre qui ne dit pas sa borne."""


def seuil_atteint(montant: Decimal, seuil: Decimal, borne: Borne) -> bool:
    """La seule comparaison d'un montant à un seuil, pour tout le produit.

    ⚠️ Un contexte qui reçoit un seuil et sa borne appelle cette fonction, et
    n'écrit jamais `>=` ou `>` lui-même : c'est ainsi que l'opérateur a été choisi
    à la place de la loi pendant quarante-huit pas.
    """
    return montant >= seuil if borne is Borne.INCLUSE else montant > seuil


class Fondement(BaseModel):
    """Sans fondement, on ne saura pas quoi mettre à jour à la loi de finances suivante,
    ni justifier un rejet auprès d'un adhérent mécontent. Il est donc obligatoire."""

    model_config = ConfigDict(frozen=True)

    texte: str = Field(min_length=1)
    source: str = Field(min_length=1)


class VersionParametre(BaseModel):
    """Une valeur valide sur l'intervalle [applicable_du, applicable_au[ — borne haute
    exclue. `applicable_au` à None signifie « toujours en vigueur »."""

    model_config = ConfigDict(frozen=True)

    valeur: bool | int | float | str
    applicable_du: date
    applicable_au: date | None = None
    statut: StatutValidation = StatutValidation.A_VALIDER
    fondement: Fondement
    note: str | None = None
    valide_par: str | None = None
    valide_le: date | None = None
    #: Pour un seuil : sa valeur est-elle déjà atteinte ? Voir `Borne`. `None` pour
    #: un paramètre qui ne se compare pas, un taux ou un délai.
    borne: Borne | None = None

    @model_validator(mode="after")
    def _bornes_coherentes(self) -> VersionParametre:
        if self.applicable_au is not None and self.applicable_au <= self.applicable_du:
            raise ValueError(
                f"borne de validité incohérente : {self.applicable_au} <= {self.applicable_du}"
            )
        if self.statut is StatutValidation.VALIDE and not (self.valide_par and self.valide_le):
            raise ValueError(
                "une version VALIDE doit porter valide_par et valide_le : "
                "la validation engage une personne nommée, pas l'éditeur"
            )
        return self

    def couvre(self, a_la_date: date) -> bool:
        if a_la_date < self.applicable_du:
            return False
        return self.applicable_au is None or a_la_date < self.applicable_au


class Parametre(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1)
    libelle: str = Field(min_length=1)
    categorie: str
    unite: Unite
    #: Qui peut valider. Voir `NatureParametre` : le défaut engageant est voulu.
    nature: NatureParametre = NatureParametre.LOI
    versions: list[VersionParametre] = Field(min_length=1)

    @model_validator(mode="after")
    def _versions_sans_chevauchement(self) -> Parametre:
        triees = sorted(self.versions, key=lambda v: v.applicable_du)
        for precedente, suivante in zip(triees, triees[1:], strict=False):
            fin = precedente.applicable_au
            if fin is None or fin > suivante.applicable_du:
                raise ValueError(
                    f"{self.code} : les versions se chevauchent à partir du "
                    f"{suivante.applicable_du}. Fermer la version précédente."
                )
        return self


class ParametreResolu(BaseModel):
    """Le résultat d'une lecture, avec son contexte.

    C'est cette forme que le moteur de conformité conserve dans le rapport : la valeur
    exacte employée, sa date d'effet et son statut. Un rapport de juillet 2026 reste
    ainsi reproductible même si le seuil change en janvier 2027.

    ⚠️ La résolution reporte AUSSI qui a validé, quand, et à quel titre. Un rapport
    qui dirait « VALIDE » sans nommer le signataire rendrait la validation
    invérifiable — et une validation invérifiable ne protège personne. La nature dit
    en outre à quel titre : un poids de pilotage arrêté par la direction et un taux
    de TVA confirmé sur le CGI ne s'opposent pas de la même façon.
    """

    model_config = ConfigDict(frozen=True)

    code: str
    libelle: str
    valeur: bool | int | float | str
    unite: Unite
    nature: NatureParametre = NatureParametre.LOI
    applicable_du: date
    statut: StatutValidation
    valide_par: str | None = None
    valide_le: date | None = None
    fondement: Fondement
    note: str | None = None
    borne: Borne | None = None

    @property
    def valeur_decimale(self) -> Decimal:
        if isinstance(self.valeur, bool) or isinstance(self.valeur, str):
            raise TypeError(f"{self.code} n'est pas numérique (unité {self.unite})")
        return Decimal(str(self.valeur))

    def atteint(self, montant: Decimal) -> bool:
        """Ce montant atteint-il le seuil, au sens exact du texte ?

        ⚠️ **C'est la seule façon de comparer un montant à un seuil du référentiel.**
        Écrire `montant >= parametre.valeur_decimale` dans un contexte, c'est
        choisir une borne à la place de la loi, et la choisir en silence.

        Un seuil qui ne dit pas sa borne **lève** plutôt que de supposer : c'est au
        franc près que l'erreur se commet, et c'est au franc près qu'elle coûte.
        """
        if self.borne is None:
            raise SeuilSansBorne(
                f"{self.code} ne déclare pas sa borne au référentiel : impossible de "
                "dire si sa valeur même l'atteint. Renseigner « borne » sur la version, "
                "d'après la formulation du texte."
            )
        return seuil_atteint(montant, self.valeur_decimale, self.borne)


# ══ Les barèmes progressifs ═══════════════════════════════════════════════════
#
# ─────────────────────────────────────────────────────────────────────────────
# POURQUOI UN BARÈME N'EST PAS UN PARAMÈTRE
#
# Un paramètre porte une valeur scalaire : un taux, un seuil, un délai. L'IRPP
# sur salaires, lui, est une **fonction par morceaux** — dix pour cent jusqu'à
# deux millions, quinze au-delà, et ainsi de suite. L'écrire en quatre paramètres
# `IRPP_TRANCHE_1_TAUX`, `IRPP_TRANCHE_1_PLAFOND`… le rendrait illisible et,
# surtout, permettrait à un plafond d'être modifié sans son taux : le barème
# cesserait d'être cohérent sans que rien ne le signale.
#
# Le barème est donc une entité à part entière, versionnée comme un paramètre —
# une loi de finances retouche régulièrement les tranches — et **validée comme un
# tout** : ses tranches sont contiguës ou il lève.
#
# Le dossier de conception l'annonçait dès l'origine parmi les entités du
# contexte A. Il arrive avec le premier calcul qui en a besoin, la paie.
# ─────────────────────────────────────────────────────────────────────────────


class TrancheBareme(BaseModel):
    """Une tranche : de `plancher` inclus à `plafond` exclu, à ce taux.

    Borne haute **exclue**, comme partout dans ce projet. Sans cette convention,
    un revenu exactement égal à deux millions relèverait de deux tranches à la
    fois, et le calcul dépendrait de l'ordre de parcours.

    `plafond` à `None` marque la dernière tranche, celle qui n'a pas de fin.
    """

    model_config = ConfigDict(frozen=True)

    plancher: Decimal
    plafond: Decimal | None = None
    taux: Decimal

    @model_validator(mode="after")
    def _bornes_coherentes(self) -> TrancheBareme:
        if self.plancher < 0:
            raise ValueError(f"tranche à plancher négatif : {self.plancher}")
        if self.plafond is not None and self.plafond <= self.plancher:
            raise ValueError(
                f"tranche incohérente : plafond {self.plafond} <= plancher {self.plancher}"
            )
        return self


class VersionBareme(BaseModel):
    """Le barème tel qu'il s'applique sur un intervalle de dates."""

    model_config = ConfigDict(frozen=True)

    tranches: list[TrancheBareme] = Field(min_length=1)
    applicable_du: date
    applicable_au: date | None = None
    statut: StatutValidation = StatutValidation.A_VALIDER
    fondement: Fondement
    note: str | None = None
    valide_par: str | None = None
    valide_le: date | None = None

    @model_validator(mode="after")
    def _tranches_contigues(self) -> VersionBareme:
        """Les tranches se suivent sans trou ni recouvrement, et la dernière est ouverte.

        C'est l'invariant qui rend le barème sûr : un trou entre deux tranches
        produirait un revenu sans taux, et le calcul lèverait ou — pire —
        rendrait zéro. Un recouvrement ferait dépendre le résultat de l'ordre.

        Contrôlé à la construction et non au calcul : une erreur de saisie doit
        se voir au démarrage, pas sur le bulletin de paie d'un salarié.
        """
        triees = sorted(self.tranches, key=lambda t: t.plancher)
        if triees[0].plancher != 0:
            raise ValueError(
                f"le barème ne couvre pas les revenus faibles : la première tranche "
                f"commence à {triees[0].plancher} et non à zéro."
            )
        for precedente, suivante in zip(triees, triees[1:], strict=False):
            if precedente.plafond is None:
                raise ValueError(
                    "une tranche sans plafond doit être la dernière : "
                    f"celle à {precedente.plancher} est suivie d'une autre."
                )
            if precedente.plafond != suivante.plancher:
                raise ValueError(
                    f"tranches non contiguës : {precedente.plafond} puis "
                    f"{suivante.plancher}. Un revenu entre les deux n'aurait aucun taux."
                )
        if triees[-1].plafond is not None:
            raise ValueError(
                f"la dernière tranche est fermée à {triees[-1].plafond} : les revenus "
                "au-delà n'auraient aucun taux."
            )
        if self.applicable_au is not None and self.applicable_au <= self.applicable_du:
            raise ValueError(
                f"borne de validité incohérente : {self.applicable_au} <= {self.applicable_du}"
            )
        if self.statut is StatutValidation.VALIDE and not (self.valide_par and self.valide_le):
            raise ValueError(
                "une version VALIDE doit porter valide_par et valide_le : "
                "la validation engage une personne nommée, pas l'éditeur"
            )
        return self

    def couvre(self, a_la_date: date) -> bool:
        if a_la_date < self.applicable_du:
            return False
        return self.applicable_au is None or a_la_date < self.applicable_au

    def appliquer(self, assiette: Decimal) -> Decimal:
        """L'impôt dû sur cette assiette, tranche par tranche.

        ⚠️ **Barème progressif, jamais un taux moyen appliqué au tout.** Chaque
        tranche ne taxe que la fraction qui la traverse. Appliquer le taux de la
        tranche atteinte à l'assiette entière — l'erreur la plus fréquente sur ce
        calcul — produirait un impôt très supérieur, et un salarié augmenté de
        mille francs verrait son net baisser.
        """
        if assiette <= 0:
            return Decimal(0)
        du = Decimal(0)
        for tranche in sorted(self.tranches, key=lambda t: t.plancher):
            if assiette <= tranche.plancher:
                break
            haut = assiette if tranche.plafond is None else min(assiette, tranche.plafond)
            du += (haut - tranche.plancher) * tranche.taux / Decimal(100)
        return du


class Bareme(BaseModel):
    """Un barème progressif, versionné dans le temps."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1)
    libelle: str = Field(min_length=1)
    categorie: str
    versions: list[VersionBareme] = Field(min_length=1)

    @model_validator(mode="after")
    def _versions_sans_chevauchement(self) -> Bareme:
        triees = sorted(self.versions, key=lambda v: v.applicable_du)
        for precedente, suivante in zip(triees, triees[1:], strict=False):
            fin = precedente.applicable_au
            if fin is None or fin > suivante.applicable_du:
                raise ValueError(
                    f"{self.code} : les versions se chevauchent à partir du "
                    f"{suivante.applicable_du}. Fermer la version précédente."
                )
        return self


class BaremeResolu(BaseModel):
    """Un barème lu à une date, avec son contexte de validation.

    Même forme que `ParametreResolu`, et pour la même raison : ce qui a servi à
    calculer un bulletin doit rester attaché à ce bulletin. Un contrôle en 2029
    sur une paie de 2026 doit pouvoir refaire le calcul avec le barème de 2026.
    """

    model_config = ConfigDict(frozen=True)

    code: str
    libelle: str
    tranches: list[TrancheBareme]
    applicable_du: date
    statut: StatutValidation
    fondement: Fondement
    note: str | None = None

    def appliquer(self, assiette: Decimal) -> Decimal:
        du = Decimal(0)
        for tranche in sorted(self.tranches, key=lambda t: t.plancher):
            if assiette <= tranche.plancher:
                break
            haut = assiette if tranche.plafond is None else min(assiette, tranche.plafond)
            du += (haut - tranche.plancher) * tranche.taux / Decimal(100)
        return du
