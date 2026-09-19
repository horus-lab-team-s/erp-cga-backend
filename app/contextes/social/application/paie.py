"""Le calcul d'un bulletin de paie.

─────────────────────────────────────────────────────────────────────────────────
L'ORDRE DES OPÉRATIONS EST LA MOITIÉ DU MÉTIER

    1. brut taxable      = base + primes + avantages évalués au forfait
    2. assiette cotisable = brut taxable, PLAFONNÉ pour certaines branches
    3. assiette IRPP      = brut annualisé − frais professionnels − abattement
    4. IRPP mensuel       = barème progressif annuel / 12, puis CAC
    5. net à payer        = base + primes − retenues salariales

Chaque étape a sa raison d'être et son piège :

* **Les avantages en nature entrent dans l'assiette et sortent du net.** Ils sont
  imposables — le CGI les évalue forfaitairement — mais ils ont déjà été fournis
  en nature. Les laisser dans le net paierait le logement deux fois.
* **Le plafond ne s'applique pas à toutes les branches.** Pensions et prestations
  familiales sont plafonnées ; les accidents du travail portent sur le salaire
  réel. C'est l'erreur la plus fréquente du calcul, et elle est invisible tant
  qu'aucun salarié ne dépasse le plafond.
* **Le barème IRPP est annuel.** L'appliquer à un salaire mensuel donnerait un
  impôt dix fois trop faible — la première tranche absorberait tout. Le calcul
  annualise, applique le barème, puis divise.
* **Le barème est progressif, jamais un taux moyen.** Appliquer le taux de la
  tranche atteinte à l'assiette entière — l'erreur classique — ferait baisser le
  net d'un salarié augmenté de mille francs.

RIEN N'EST EN DUR

Chaque taux, chaque plafond, chaque abattement se lit au référentiel **à la date
de la période**. Une paie de mars 2026 se calcule avec les taux de mars 2026, même
recalculée en 2029. Et chaque ligne du bulletin porte le code du paramètre qui l'a
produite : c'est ce qui permet de répondre à « d'où sort ce chiffre ? ».
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.contextes.referentiel.api import (
    AucuneVersionApplicable,
    ParametreInconnu,
    ParametreResolu,
    ServiceBaremes,
    ServiceParametres,
    StatutValidation,
)
from app.contextes.social.domaine.entites import (
    CODE_ACCIDENTS_TRAVAIL,
    CODE_FORFAIT,
    Bulletin,
    Contrat,
    GroupeRisque,
    LigneRetenue,
    Periode,
)

__all__ = ["ParametrePaieAbsent", "calculer_bulletin", "evaluer_les_avantages"]

#: Les montants de paie s'arrondissent au franc. Le FCFA n'a pas de subdivision en
#: circulation, et un bulletin au centime serait invérifiable à la main.
_FRANC = Decimal("1")

#: Le barème IRPP est annuel ; la retenue est mensuelle.
_MOIS_PAR_AN = Decimal(12)


class ParametrePaieAbsent(LookupError):
    """Un paramètre indispensable au calcul manque au référentiel.

    ─────────────────────────────────────────────────────────────────────────
    POURQUOI CELUI-CI LÈVE, ALORS QUE LE DIAGNOSTIC DE CRÉATION N'A PAS LEVÉ

    Le contexte I tolère un paramètre absent : il produit un constat non bloquant
    et laisse instruire le dossier. Ici, non — et la différence est de nature.

    Un capital minimum inconnu empêche de *vérifier* une donnée. Un taux de
    cotisation inconnu empêche de *calculer* un montant. Continuer produirait un
    bulletin où la ligne manque, donc un net trop élevé, donc un salarié payé en
    trop et une cotisation non versée. Personne ne s'en apercevrait avant le
    contrôle CNPS, sur toute la masse salariale et sur trois ans.

    Un calcul qui ne peut pas être juste doit refuser de rendre un résultat.
    ─────────────────────────────────────────────────────────────────────────
    """


def _francs(montant: Decimal) -> Decimal:
    return montant.quantize(_FRANC, rounding=ROUND_HALF_UP)


def _lire(parametres: ServiceParametres, code: str, periode: Periode) -> ParametreResolu:
    """Lit un paramètre à la date de la période, ou refuse le calcul.

    La date de lecture est le **dernier jour de la période**, jamais le jour du
    calcul : une paie de mars refaite en décembre doit employer les taux de mars.
    """
    try:
        return parametres.resoudre(code, periode.dernier_jour)
    except (ParametreInconnu, AucuneVersionApplicable) as absence:
        raise ParametrePaieAbsent(
            f"{code} introuvable au {periode.dernier_jour} : le bulletin de "
            f"{periode.libelle} ne peut pas être calculé sans lui. ({absence})"
        ) from absence


def evaluer_les_avantages(
    contrat: Contrat, parametres: ServiceParametres, periode: Periode
) -> tuple[Decimal, list[str]]:
    """Évalue les avantages en nature au forfait, sur base + primes.

    ⚠️ **L'assiette du forfait est le salaire brut EN ESPÈCES**, pas le brut
    taxable — sinon l'évaluation d'un avantage dépendrait des autres avantages, et
    le calcul deviendrait circulaire : le logement à 15 % du brut, brut qui inclut
    le logement, qui inclut… La référence est donc base + primes.

    Rend le montant et les codes des paramètres employés, pour la traçabilité.
    """
    total = Decimal(0)
    codes: list[str] = []
    assiette = contrat.salaire_base + contrat.primes
    for avantage in contrat.avantages:
        code = CODE_FORFAIT[avantage.nature]
        taux = _lire(parametres, code, periode).valeur_decimale
        total += assiette * taux / Decimal(100) * Decimal(avantage.nombre)
        codes.append(code)
    return _francs(total), codes


def _ligne(
    *,
    code: str,
    libelle: str,
    assiette: Decimal,
    resolu: ParametreResolu,
    a_charge_du_salarie: bool,
) -> LigneRetenue:
    taux = resolu.valeur_decimale
    return LigneRetenue(
        code=code,
        libelle=libelle,
        assiette=assiette,
        taux=taux,
        montant=_francs(assiette * taux / Decimal(100)),
        a_charge_du_salarie=a_charge_du_salarie,
        fondement_code=resolu.code,
        non_valide=resolu.statut is not StatutValidation.VALIDE,
    )


def calculer_bulletin(
    contrat: Contrat,
    entreprise: str,
    periode: Periode,
    parametres: ServiceParametres,
    baremes: ServiceBaremes,
    *,
    groupe_risque: GroupeRisque = GroupeRisque.A,
) -> Bulletin:
    """Le bulletin de paie du mois, entièrement calculé.

    `groupe_risque` est **notifié par la CNPS à l'employeur** et se saisit ; il ne
    se déduit ni de l'activité, ni de la forme juridique. Le défaut au groupe A
    est le moins-disant : il vaut mieux sous-estimer une charge patronale que la
    surestimer sur un dossier dont on n'a pas encore la notification, parce que le
    premier écart se corrige au régularisation et le second fait fuir l'adhérent.
    """
    avantages, _codes = evaluer_les_avantages(contrat, parametres, periode)
    brut_taxable = contrat.salaire_base + contrat.primes + avantages

    plafond = _lire(parametres, "CNPS_PLAFOND_MENSUEL", periode).valeur_decimale
    # ⚠️ Deux assiettes, et c'est tout l'objet de la ligne suivante. Les branches
    # pensions et prestations familiales sont plafonnées ; les accidents du
    # travail portent sur le salaire réel. Employer une seule assiette est
    # l'erreur la plus fréquente, et elle reste invisible tant qu'aucun salarié ne
    # dépasse le plafond — c'est-à-dire jusqu'au jour où le cabinet gagne un
    # client qui paie ses cadres.
    assiette_plafonnee = min(brut_taxable, plafond)

    lignes: list[LigneRetenue] = [
        _ligne(
            code="CNPS_PVID_S",
            libelle="CNPS — pension vieillesse, invalidité, décès",
            assiette=assiette_plafonnee,
            resolu=_lire(parametres, "CNPS_PVID_TAUX_SALARIE", periode),
            a_charge_du_salarie=True,
        ),
        _ligne(
            code="CFC_S",
            libelle="Crédit foncier du Cameroun",
            assiette=brut_taxable,
            resolu=_lire(parametres, "CFC_TAUX_SALARIE", periode),
            a_charge_du_salarie=True,
        ),
        _ligne(
            code="CNPS_PVID_P",
            libelle="CNPS — pension vieillesse, invalidité, décès (patronale)",
            assiette=assiette_plafonnee,
            resolu=_lire(parametres, "CNPS_PVID_TAUX_EMPLOYEUR", periode),
            a_charge_du_salarie=False,
        ),
        _ligne(
            code="CNPS_PF",
            libelle="CNPS — prestations familiales",
            assiette=assiette_plafonnee,
            resolu=_lire(parametres, "CNPS_PRESTATIONS_FAMILIALES_TAUX", periode),
            a_charge_du_salarie=False,
        ),
        _ligne(
            code="CNPS_AT",
            libelle=f"CNPS — accidents du travail, groupe {groupe_risque}",
            # Non plafonnée : voir le commentaire ci-dessus.
            assiette=brut_taxable,
            resolu=_lire(parametres, CODE_ACCIDENTS_TRAVAIL[groupe_risque], periode),
            a_charge_du_salarie=False,
        ),
        _ligne(
            code="CFC_P",
            libelle="Crédit foncier du Cameroun (patronale)",
            assiette=brut_taxable,
            resolu=_lire(parametres, "CFC_TAUX_EMPLOYEUR", periode),
            a_charge_du_salarie=False,
        ),
        _ligne(
            code="FNE",
            libelle="Fonds national de l'emploi",
            assiette=brut_taxable,
            resolu=_lire(parametres, "FNE_TAUX_EMPLOYEUR", periode),
            a_charge_du_salarie=False,
        ),
    ]

    lignes.append(_calculer_irpp(brut_taxable, parametres, baremes, periode))
    lignes.append(_calculer_tdl(contrat, baremes, periode))

    return Bulletin(
        salarie=contrat.salarie,
        entreprise=entreprise,
        periode=periode,
        salaire_base=contrat.salaire_base,
        primes=contrat.primes,
        avantages_evalues=avantages,
        lignes=tuple(lignes),
    )


def _calculer_irpp(
    brut_taxable: Decimal,
    parametres: ServiceParametres,
    baremes: ServiceBaremes,
    periode: Periode,
) -> LigneRetenue:
    """L'impôt sur le revenu retenu à la source, centimes communaux compris.

    ─────────────────────────────────────────────────────────────────────────
    QUATRE ÉTAPES, ET AUCUNE NE SE SAUTE

    1. **Annualiser** — le barème est annuel. L'appliquer au mois placerait tout
       salaire dans la première tranche, et un cadre paierait le taux d'un
       manœuvre.
    2. **Déduire les frais professionnels**, en pourcentage du brut annuel.
    3. **Déduire l'abattement forfaitaire annuel**, en francs.
    4. **Appliquer le barème progressif**, puis mensualiser, puis ajouter les
       centimes additionnels communaux — qui portent sur l'impôt, pas sur le
       revenu.

    Une assiette négative après abattements rend zéro : un revenu inférieur aux
    abattements n'est pas imposable, et un impôt négatif serait un crédit que le
    droit ne prévoit pas.
    ─────────────────────────────────────────────────────────────────────────
    """
    brut_annuel = brut_taxable * _MOIS_PAR_AN
    frais = _lire(parametres, "IRPP_ABATTEMENT_FRAIS_PROFESSIONNELS", periode)
    forfait = _lire(parametres, "IRPP_ABATTEMENT_FORFAITAIRE_ANNUEL", periode)

    apres_frais = brut_annuel * (Decimal(100) - frais.valeur_decimale) / Decimal(100)
    assiette_annuelle = max(Decimal(0), apres_frais - forfait.valeur_decimale)

    bareme = baremes.resoudre("IRPP_SALAIRES", periode.dernier_jour)
    impot_annuel = bareme.appliquer(assiette_annuelle)

    cac = _lire(parametres, "CAC_TAUX", periode)
    avec_cac = impot_annuel * (Decimal(100) + cac.valeur_decimale) / Decimal(100)

    non_valide = (
        bareme.statut is not StatutValidation.VALIDE
        or frais.statut is not StatutValidation.VALIDE
        or forfait.statut is not StatutValidation.VALIDE
        or cac.statut is not StatutValidation.VALIDE
    )
    return LigneRetenue(
        code="IRPP",
        libelle="Impôt sur le revenu, centimes additionnels compris",
        assiette=_francs(assiette_annuelle / _MOIS_PAR_AN),
        taux=None,  # progressif : aucun taux unique ne le décrit
        montant=_francs(avec_cac / _MOIS_PAR_AN),
        a_charge_du_salarie=True,
        fondement_code="IRPP_SALAIRES",
        non_valide=non_valide,
    )


def _calculer_tdl(contrat: Contrat, baremes: ServiceBaremes, periode: Periode) -> LigneRetenue:
    """La taxe de développement local, sur le salaire de base mensuel.

    ⚠️ **Montant indicatif.** La TDL est en réalité un barème à montant fixe par
    palier, modélisé ici en taux faute de structure adéquate au référentiel — la
    réserve est écrite dans `baremes.yaml` et cette ligne la propage en marquant
    `non_valide`. Le fiscaliste doit trancher : soit les montants s'expriment
    correctement en taux, soit il faut ajouter au référentiel un type de tranche à
    montant fixe.

    Elle est calculée quand même plutôt qu'omise : une ligne absente d'un bulletin
    ne se remarque pas, une ligne marquée non validée se discute.
    """
    bareme = baremes.resoudre("TDL_SALAIRES", periode.dernier_jour)
    return LigneRetenue(
        code="TDL",
        libelle="Taxe de développement local (montant indicatif)",
        assiette=contrat.salaire_base,
        taux=None,
        montant=_francs(bareme.appliquer(contrat.salaire_base)),
        a_charge_du_salarie=True,
        fondement_code="TDL_SALAIRES",
        # Toujours marquée : la réserve porte sur la forme du barème, pas
        # seulement sur ses valeurs, et elle ne se lèvera pas par une simple
        # validation des chiffres.
        non_valide=True,
    )
