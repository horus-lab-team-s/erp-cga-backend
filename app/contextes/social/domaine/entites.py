"""Entités du contexte G · Social et paie.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE CONTEXTE MODÉLISE

Les salariés d'un dossier, ce qu'ils gagnent, et ce que l'employeur doit à ce
titre : cotisations CNPS, retenues fiscales sur salaire, et la déclaration
mensuelle qui les récapitule.

C'est le volet qui manquait au suivi. L'échéancier fiscal le disait lui-même en
toutes lettres : « cet échéancier suppose que le dossier n'a pas de salariés ».
Un dossier avec un seul salarié doit davantage d'obligations qu'un dossier sans,
et l'impôt libératoire ne les efface pas — les cotisations sociales ne dépendent
pas du régime fiscal mais de la présence de salariés.

UN BULLETIN SE CALCULE, IL NE SE SAISIT PAS

C'est la décision structurante du contexte, et elle est la même qu'à
F · Obligations pour l'échéancier. Le bulletin est une **fonction** du contrat, de
la période et du référentiel à cette date. Le stocker comme vérité obligerait à le
recalculer à chaque correction de taux, et personne ne saurait dire lequel des
deux — le stocké ou le recalculé — fait foi.

Ce qui se conserve, c'est ce qui a été **payé** : un bulletin émis est figé, avec
les paramètres qui ont servi à le produire. Un contrôle en 2029 sur une paie de
2026 doit pouvoir refaire le calcul avec le référentiel de 2026, et retomber sur
le même chiffre. C'est la même discipline que le rapport de conformité, qui
conserve les paramètres employés.

CE QU'IL NE MODÉLISE PAS

Ni la comptabilisation de la paie — l'écriture de charge de personnel appartient à
E · Comptabilité — ni le calendrier des échéances sociales, qui appartient à F.
G dit **combien** est dû et **pour qui** ; F dit **quand**, E dit **où** au bilan.

Aucun taux, aucun barème en dur. Tout se lit au référentiel à une date : le
Cameroun a modifié ses taux de cotisation, et il les modifiera encore.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

__all__ = [
    "AvantageNature",
    "Bulletin",
    "Contrat",
    "GroupeRisque",
    "LigneRetenue",
    "NatureAvantage",
    "Periode",
    "Salarie",
    "TypeContrat",
    "a_employe_sur",
]


class TypeContrat(StrEnum):
    """La nature de l'engagement.

    Sert au DIPE, qui distingue les effectifs permanents des temporaires, et au
    contrôle : un CDD qui se renouvelle indéfiniment se requalifie.
    """

    CDI = "CDI"
    CDD = "CDD"
    #: Contrat d'apprentissage ou de stage rémunéré.
    APPRENTISSAGE = "APPRENTISSAGE"
    #: Travailleur payé à la tâche ou à la journée.
    OCCASIONNEL = "OCCASIONNEL"


class GroupeRisque(StrEnum):
    """Le groupe de tarification du risque professionnel, notifié par la CNPS.

    ⚠️ **Il ne se déduit pas de l'activité déclarée.** La CNPS le notifie à chaque
    employeur, et deux entreprises du même secteur peuvent relever de groupes
    différents selon leur sinistralité. Le déduire coûterait le triple ou le tiers
    de la cotisation, et l'erreur ne se verrait qu'au contrôle.
    """

    A = "A"
    B = "B"
    C = "C"


class NatureAvantage(StrEnum):
    """Les avantages en nature évalués forfaitairement par le CGI, art. 33.

    Fermée volontairement : un avantage hors de cette liste n'est pas un avantage
    en nature au sens du barème forfaitaire, c'est un élément de rémunération en
    espèces. Les confondre appliquerait un forfait là où il faut le montant réel.

    ⚠️ LA LISTE S'ALLONGE, ET C'EST POURQUOI ELLE EST DATÉE

    Six natures jusqu'en 2023. La loi de finances 2024 en a ajouté quatre :
    téléphone, carburant, gardiennage, internet. Le téléphone servait d'ailleurs
    d'exemple, dans la version précédente de ce commentaire, de ce qui n'était
    **pas** un avantage en nature — l'exemple était juste quand il a été écrit et
    ne l'est plus. C'est exactement le genre de bascule qu'un référentiel daté
    absorbe et qu'une constante en dur ne voit jamais passer.

    Chaque forfait porte sa propre date d'effet au référentiel : un bulletin de
    2023 recalculé aujourd'hui ne doit pas se voir appliquer un barème de 2024.
    """

    LOGEMENT = "LOGEMENT"
    ELECTRICITE = "ELECTRICITE"
    EAU = "EAU"
    DOMESTIQUE = "DOMESTIQUE"
    VEHICULE = "VEHICULE"
    NOURRITURE = "NOURRITURE"
    #: Ajoutés par la loi de finances 2024, applicables au 1er janvier 2024.
    TELEPHONE = "TELEPHONE"
    CARBURANT = "CARBURANT"
    GARDIENNAGE = "GARDIENNAGE"
    INTERNET = "INTERNET"


#: Le code du paramètre qui porte le forfait de chaque nature.
CODE_FORFAIT: dict[NatureAvantage, str] = {
    NatureAvantage.LOGEMENT: "AVANTAGE_NATURE_LOGEMENT",
    NatureAvantage.ELECTRICITE: "AVANTAGE_NATURE_ELECTRICITE",
    NatureAvantage.EAU: "AVANTAGE_NATURE_EAU",
    NatureAvantage.DOMESTIQUE: "AVANTAGE_NATURE_DOMESTIQUE",
    NatureAvantage.VEHICULE: "AVANTAGE_NATURE_VEHICULE",
    NatureAvantage.NOURRITURE: "AVANTAGE_NATURE_NOURRITURE",
    NatureAvantage.TELEPHONE: "AVANTAGE_NATURE_TELEPHONE",
    NatureAvantage.CARBURANT: "AVANTAGE_NATURE_CARBURANT",
    NatureAvantage.GARDIENNAGE: "AVANTAGE_NATURE_GARDIENNAGE",
    NatureAvantage.INTERNET: "AVANTAGE_NATURE_INTERNET",
}

#: Le code du paramètre de taux accident du travail, par groupe notifié.
CODE_ACCIDENTS_TRAVAIL: dict[GroupeRisque, str] = {
    GroupeRisque.A: "CNPS_ACCIDENTS_TRAVAIL_TAUX_GROUPE_A",
    GroupeRisque.B: "CNPS_ACCIDENTS_TRAVAIL_TAUX_GROUPE_B",
    GroupeRisque.C: "CNPS_ACCIDENTS_TRAVAIL_TAUX_GROUPE_C",
}


class Periode(BaseModel):
    """Un mois de paie, désigné par son année et son mois.

    Un mois entier et non un intervalle libre : la paie camerounaise est mensuelle,
    les cotisations sont plafonnées **au mois**, et le DIPE est mensuel. Autoriser
    une période de quarante jours obligerait chaque calcul à décider comment
    proratiser un plafond mensuel — décision qui n'appartient pas au code.
    """

    model_config = ConfigDict(frozen=True)

    annee: int = Field(ge=2000, le=2100)
    mois: int = Field(ge=1, le=12)

    @property
    def libelle(self) -> str:
        return f"{self.annee}-{self.mois:02d}"

    @property
    def premier_jour(self) -> date:
        return date(self.annee, self.mois, 1)

    @property
    def dernier_jour(self) -> date:
        if self.mois == 12:
            return date(self.annee, 12, 31)
        return date(self.annee, self.mois + 1, 1) - timedelta(days=1)

    def suivante(self) -> Periode:
        """Le mois d'après — celui où la déclaration est due."""
        if self.mois == 12:
            return Periode(annee=self.annee + 1, mois=1)
        return Periode(annee=self.annee, mois=self.mois + 1)



class AvantageNature(BaseModel):
    """Un avantage en nature accordé, avec sa quantité.

    `nombre` sert au seul avantage qui se compte : les domestiques. Deux gardiens
    valent deux fois le forfait. Pour les autres, il vaut un.
    """

    model_config = ConfigDict(frozen=True)

    nature: NatureAvantage
    nombre: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _seul_le_domestique_se_compte(self) -> AvantageNature:
        if self.nombre > 1 and self.nature is not NatureAvantage.DOMESTIQUE:
            raise ValueError(
                f"l'avantage {self.nature} ne se compte pas : un salarié loge dans un "
                "logement, il n'en a pas deux. Seul le domestique se multiplie."
            )
        return self


class Salarie(BaseModel):
    """Une personne employée par un dossier du portefeuille.

    Le matricule CNPS est facultatif à la saisie : un salarié tout juste embauché
    n'en a pas encore, et l'exiger empêcherait de le déclarer — or c'est
    précisément la déclaration qui le lui fera attribuer.
    """

    model_config = ConfigDict(frozen=True)

    matricule: str = Field(min_length=1)
    nom: str = Field(min_length=1)
    prenom: str = Field(min_length=1)
    #: NIU du dossier employeur. Le lien vers B · Portefeuille.
    entreprise: str = Field(min_length=1)
    matricule_cnps: str | None = None
    date_naissance: date | None = None
    #: Nombre d'enfants à charge — porté pour le DIPE, sans effet sur le calcul :
    #: le Cameroun n'applique pas de quotient familial sur les salaires.
    enfants_a_charge: int = Field(default=0, ge=0)


class Contrat(BaseModel):
    """L'engagement, sur un intervalle daté.

    ⚠️ **Un intervalle, jamais un attribut du salarié.** Un salaire change, un
    salarié passe de CDD en CDI, et une paie de mars doit se calculer avec le
    contrat de mars. Porter le salaire sur le salarié écraserait l'histoire, et
    le premier contrôle sur un mois passé retomberait sur le salaire d'aujourd'hui.

    Même discipline que les statuts datés du portefeuille : `[debut, fin[`, borne
    haute exclue.
    """

    model_config = ConfigDict(frozen=True)

    salarie: str = Field(min_length=1)
    type_contrat: TypeContrat
    debut: date
    fin: date | None = None
    #: Salaire de base mensuel, hors primes et hors avantages.
    salaire_base: Decimal = Field(ge=0)
    #: Primes et indemnités mensuelles en espèces, imposables.
    primes: Decimal = Field(default=Decimal(0), ge=0)
    avantages: tuple[AvantageNature, ...] = ()
    poste: str | None = None

    @model_validator(mode="after")
    def _bornes_coherentes(self) -> Contrat:
        if self.fin is not None and self.fin <= self.debut:
            raise ValueError(
                f"contrat de {self.salarie} : fin {self.fin} antérieure ou égale au "
                f"début {self.debut}."
            )
        return self

    def couvre(self, jour: date) -> bool:
        if jour < self.debut:
            return False
        return self.fin is None or jour < self.fin

    def couvre_l_intervalle(self, du: date, au_inclus: date) -> bool:
        """Le contrat est-il en vigueur **un jour au moins** entre ces deux dates incluses ?

        Le contrat est `[debut, fin[` : il chevauche l'intervalle s'il commence au
        plus tard le dernier jour, et s'il ne s'est pas terminé avant le premier.
        """
        return self.debut <= au_inclus and (self.fin is None or self.fin > du)

    def couvre_la_periode(self, periode: Periode) -> bool:
        """Le contrat est-il en vigueur au cours de ce mois, même partiellement ?

        Partiellement suffit : un salarié embauché le 20 est déclaré au titre du
        mois, et sa cotisation est due. Exiger le mois entier ferait disparaître
        des déclarations les embauches et les départs — c'est-à-dire exactement
        les mouvements que le DIPE existe pour signaler.

        ⚠️ **Ce test ne regardait que le premier et le dernier jour du mois** (pas
        56). Un contrat du 10 au 20 mars n'en couvre aucun : il disparaissait du
        DIPE de mars et de la paie du mois, alors qu'un saisonnier ou un
        remplaçant est exactement ce contrat-là. Le chevauchement se mesure
        désormais sur tout le mois.
        """
        return self.couvre_l_intervalle(periode.premier_jour, periode.dernier_jour)


class LigneRetenue(BaseModel):
    """Une ligne de cotisation ou de retenue, avec ce qui l'a produite.

    ─────────────────────────────────────────────────────────────────────────
    CHAQUE LIGNE PORTE SON ASSIETTE, SON TAUX ET SON FONDEMENT

    Un bulletin qui n'afficherait que des montants serait indéfendable : ni le
    salarié qui conteste, ni l'inspecteur qui contrôle ne peuvent rien faire d'un
    nombre isolé. Porter l'assiette et le taux permet de refaire le calcul de
    tête, et le code du paramètre permet de remonter au texte.

    C'est aussi ce qui rend visible le plafonnement : quand l'assiette affichée
    est inférieure au brut, c'est que le plafond a joué, et cela se voit sans
    explication.
    ─────────────────────────────────────────────────────────────────────────
    """

    model_config = ConfigDict(frozen=True)

    code: str
    libelle: str
    assiette: Decimal
    taux: Decimal | None
    montant: Decimal
    #: À la charge du salarié (retenue) ou de l'employeur (charge patronale).
    a_charge_du_salarie: bool
    #: Le code du paramètre ou du barème employé — la trace vers le référentiel.
    fondement_code: str | None = None
    #: Vrai si la valeur employée n'est pas encore validée par un fiscaliste.
    non_valide: bool = False


class Bulletin(BaseModel):
    """Le bulletin de paie d'un salarié pour un mois, entièrement calculé.

    Rien ici n'est saisi : tout découle du contrat, de la période et du
    référentiel à cette date. C'est ce qui garantit qu'un contrôle en 2029 sur une
    paie de 2026 retombe sur le même chiffre — à condition de relire le
    référentiel de 2026, ce que le service sait faire.
    """

    model_config = ConfigDict(frozen=True)

    salarie: str
    entreprise: str
    periode: Periode
    #: Le salaire de base du contrat en vigueur.
    salaire_base: Decimal
    primes: Decimal
    #: Les avantages en nature, évalués au forfait sur le brut taxable.
    avantages_evalues: Decimal
    lignes: tuple[LigneRetenue, ...] = ()

    # ⚠️ Pas 89 : les six montants ci-dessous sont des champs calculés, **rendus** dans la
    # réponse. Ils étaient de simples propriétés : la route du bulletin les calculait sans
    # les envoyer, et un écran aurait dû refaire le net, au risque d'y laisser les
    # avantages en nature que la propriété, justement, en retire.

    @computed_field  # type: ignore[prop-decorator]
    @property
    def brut_taxable(self) -> Decimal:
        """L'assiette de référence : base + primes + avantages évalués."""
        return self.salaire_base + self.primes + self.avantages_evalues

    @computed_field  # type: ignore[prop-decorator]
    @property
    def retenues_salariales(self) -> Decimal:
        return sum(
            (ligne.montant for ligne in self.lignes if ligne.a_charge_du_salarie),
            Decimal(0),
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def charges_patronales(self) -> Decimal:
        return sum(
            (ligne.montant for ligne in self.lignes if not ligne.a_charge_du_salarie),
            Decimal(0),
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def net_a_payer(self) -> Decimal:
        """Ce que le salarié touche.

        ⚠️ Les **avantages en nature ne se versent pas** : ils entrent dans
        l'assiette taxable mais sortent du net, puisqu'ils ont déjà été fournis en
        nature. Les laisser dans le net paierait deux fois le logement — une fois
        en clés, une fois en espèces.
        """
        return self.salaire_base + self.primes - self.retenues_salariales

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cout_employeur(self) -> Decimal:
        """Ce que le salarié coûte réellement, avantages compris."""
        return self.brut_taxable + self.charges_patronales

    @computed_field  # type: ignore[prop-decorator]
    @property
    def repose_sur_des_valeurs_non_validees(self) -> bool:
        """Au moins une ligne emploie une valeur légale non confirmée.

        Le bulletin le dit plutôt que de le taire : un employeur qui remet une
        fiche de paie engage sa responsabilité, et il doit savoir sur quoi elle
        repose. C'est la même discipline que le bandeau du rapport de conformité.
        """
        return any(ligne.non_valide for ligne in self.lignes)


def a_employe_sur(contrats: list[Contrat], du: date, au_inclus: date) -> bool:
    """Un contrat au moins a-t-il couru, un jour au moins, entre ces deux dates ?

    ─────────────────────────────────────────────────────────────────────────────
    LA SEULE QUESTION QUE F · OBLIGATIONS POSE AU SOCIAL (pas 56)

    Les cotisations CNPS et les retenues sur salaires sont dues pour chaque mois où
    le dossier a employé quelqu'un, **quel que soit son régime** : l'impôt
    libératoire n'efface pas les charges sociales. L'échéancier ne peut donc pas
    s'en passer, et il ne lit du fichier du personnel que cette réponse.

    Un jour suffit, pour la raison de `couvre_la_periode` : un salarié embauché le
    20 est déclaré au titre du mois.
    ─────────────────────────────────────────────────────────────────────────────
    """
    return any(contrat.couvre_l_intervalle(du, au_inclus) for contrat in contrats)
