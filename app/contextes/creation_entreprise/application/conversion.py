"""La conversion : le jour où une intention devient une entreprise.

─────────────────────────────────────────────────────────────────────────────────
LE SEUL CAS D'USAGE QUI FRANCHIT UNE FRONTIÈRE DE CONTEXTE

Tout le reste du contexte I vit chez lui. La conversion, non : elle fait naître
une `Entreprise` au sens de B · Portefeuille. C'est pourquoi elle est isolée dans
son propre module — le jour où l'on cherchera « qui écrit dans le portefeuille ? »,
la réponse tiendra dans un fichier.

CE QUE LA CONVERSION FAIT, ET CE QU'ELLE NE FAIT PAS

Elle **fabrique** l'entreprise, avec son régime d'entrée et son rattachement. Elle
ne la persiste pas : c'est l'adaptateur entrant qui appelle le dépôt du
portefeuille, parce que lui seul sait dans quelle transaction il travaille.

Elle ne calcule **aucune** obligation. La tentation est de générer ici le
calendrier fiscal du nouvel adhérent, puisque c'est l'intérêt annoncé du
contexte. Mais F · Obligations calcule déjà ce calendrier **depuis le
portefeuille**, à la demande, et le referait mieux. Le dupliquer produirait deux
calendriers qui divergeraient au premier changement de régime.

⚠️ **MAIS ELLE CRÉE LE PREMIER EXERCICE, ET C'EST INDISPENSABLE.**

Cette distinction a coûté un défaut, trouvé en exécutant le parcours complet
contre PostgreSQL et invisible pour les quarante-cinq tests unitaires du
contexte. La première version ne fabriquait ni exercice ni calendrier, au motif
que « c'est au portefeuille de les porter ». L'entreprise entrait bien au
portefeuille — et `GET /obligations/dossiers/{niu}/echeancier` répondait **404 :
exercice 2026 inconnu**. F calcule les échéances *sur un exercice* ; sans
exercice, il n'a rien sur quoi calculer, et la promesse du contexte — « elle
bascule avec son calendrier déjà généré » — était creuse.

La ligne juste passe entre le **fait** et le **calcul**. La période couverte par
les premiers comptes est un fait, décidé à la constitution et écrit dans les
statuts : il appartient au dossier de création de le porter. Les échéances qui en
découlent sont un calcul, refait à chaque lecture au vu du régime du jour : il
appartient à F. Créer l'exercice n'est donc pas une duplication, c'est la donnée
sans laquelle le calcul n'a pas d'objet.

LE RÉGIME D'ENTRÉE

Une entreprise qui vient d'être immatriculée n'a aucun chiffre d'affaires, donc
aucun élément pour déterminer son régime au seuil. Le droit la place au régime le
plus léger, à charge pour elle d'en changer au franchissement. Le régime d'entrée
est donc un **choix explicite du chargé de formalités**, pas une déduction : le
fondateur peut opter pour le réel dès l'origine, et c'est fréquent quand il sait
qu'il facturera de la TVA à ses clients.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date

from app.contextes.creation_entreprise.domaine.entites import (
    DossierCreation,
    EtapeCreation,
    Jalon,
    TransitionInterdite,
)
from app.contextes.portefeuille.api import (
    Adhesion,
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

__all__ = ["ConversionImpossible", "convertir"]


class ConversionImpossible(TransitionInterdite):
    """Le dossier ne réunit pas les conditions de la conversion.

    Hérite de `TransitionInterdite` : du point de vue de l'appelant, c'est un
    franchissement d'étape refusé comme un autre, et il n'a pas à connaître deux
    familles d'exceptions pour un même geste.
    """


def convertir(
    dossier: DossierCreation,
    *,
    a_la_date: date,
    regime: RegimeFiscal,
    centre: CentreRattachement,
    adherent: bool = True,
    premiere_cloture: date | None = None,
    par: str | None = None,
) -> tuple[DossierCreation, Entreprise]:
    """Fait naître l'entreprise au portefeuille et clôt le dossier de création.

    Rend le couple `(dossier clos, entreprise née)`. Les deux ensemble, parce
    qu'ils ne valent que l'un par l'autre : un dossier marqué converti sans
    entreprise enregistrée serait un mensonge, et une entreprise enregistrée sans
    dossier clos se ferait reconvertir au prochain passage.

    ⚠️ **La date de création de l'entreprise est celle du RCCM, jamais celle du
    jour.** C'est le RCCM qui fait naître la personne morale ; convertir trois
    semaines plus tard avec la date du jour ferait commencer les obligations
    fiscales trois semaines trop tard, et le premier acompte serait déclaré en
    retard sans que personne comprenne pourquoi.

    `premiere_cloture` porte la date de clôture du premier exercice. Par défaut,
    le 31 décembre de l'année du RCCM — l'usage ordinaire. Elle est **explicite**
    plutôt que déduite parce que le premier exercice long est fréquent et légal :
    une entreprise immatriculée en octobre clôture généralement au 31 décembre de
    l'année *suivante*, soit quinze mois. Le deviner d'après le mois de création
    reviendrait à prendre, dans le code, une décision qui appartient aux statuts.
    """
    if dossier.clos:
        raise ConversionImpossible(
            f"le dossier {dossier.reference} est déjà clos ({dossier.etape})."
        )
    if dossier.etape is not EtapeCreation.LIVRAISON:
        raise ConversionImpossible(
            f"le dossier {dossier.reference} est à l'étape {dossier.etape} : la "
            "conversion suit la livraison."
        )

    immatriculation = dossier.immatriculation
    if not immatriculation.immatriculee:
        raise ConversionImpossible(
            f"le dossier {dossier.reference} n'a pas ses deux identifiants essentiels : "
            "sans RCCM il n'y a pas de personne morale, sans NIU il n'y a pas de "
            "contribuable."
        )

    niu = immatriculation.niu
    assert niu is not None  # garanti par `immatriculee`, redit pour le typage
    naissance = immatriculation.rccm_obtenu_le
    assert naissance is not None  # `Immatriculation` refuse un RCCM sans date

    if naissance > a_la_date:
        raise ConversionImpossible(
            f"le RCCM du dossier {dossier.reference} est daté du {naissance}, "
            f"postérieur au jour de la conversion ({a_la_date})."
        )

    entreprise = Entreprise(
        niu=niu,
        denomination=dossier.denomination_souhaitee,
        forme_juridique=dossier.forme_juridique,
        date_creation=naissance,
        activite=dossier.activite,
        siege=dossier.siege,
        # ⚠️ `statut_initial` plutôt qu'un `StatutRegime` monté à la main : le
        # motif `CREATION` se pose en un seul endroit. La fonction existait
        # depuis le premier jour **pour ce site d'appel précis** — sa docstring le
        # nomme — et personne ne l'appelait.
        regimes=[
            statut_initial(
                regime,
                naissance,
                precision=(
                    f"Régime d'entrée arrêté à la création, dossier "
                    f"{dossier.reference}."
                ),
            )
        ],
        rattachements=[
            StatutRattachement(
                debut=naissance,
                fin=None,
                centre=centre,
                motif=MotifChangement.CREATION,
                precision=f"Rattachement d'origine, dossier {dossier.reference}.",
            )
        ],
        adhesions=(
            [
                Adhesion(
                    # ⚠️ L'adhésion prend effet le jour de la conversion, pas
                    # celui du RCCM. Elle ouvre l'abattement CGA sur le bénéfice :
                    # l'antidater accorderait un avantage fiscal sur une période
                    # où l'entreprise n'était pas adhérente, et exposerait le
                    # Centre autant que l'adhérent.
                    debut=a_la_date,
                    fin=None,
                    motif=MotifChangement.ADHESION,
                    precision=f"Adhésion à l'issue de la création, dossier "
                    f"{dossier.reference}.",
                )
            ]
            if adherent
            else []
        ),
        exercices=[_premier_exercice(naissance, premiere_cloture)],
        dirigeants=[
            Dirigeant(
                nom=f"{dossier.fondateur.prenom} {dossier.fondateur.nom}".strip(),
                qualite=_qualite_du_fondateur(dossier.forme_juridique),
                depuis=naissance,
            )
        ],
    )

    clos = dossier.model_copy(
        update={
            "etape": EtapeCreation.CONVERTI,
            "converti_en": niu,
            "jalons": dossier.jalons
            + (
                Jalon(
                    etape=EtapeCreation.CONVERTI,
                    survenu_le=a_la_date,
                    par=par,
                    commentaire=f"Entrée au portefeuille sous le NIU {niu}.",
                ),
            ),
        }
    )
    return clos, entreprise


def _premier_exercice(naissance: date, cloture_voulue: date | None) -> Exercice:
    """Le premier exercice, du jour du RCCM à sa clôture.

    Le libellé est l'année de **clôture**, jamais celle de l'ouverture : c'est
    sous cette année que la liasse est déposée, et c'est donc celle que le
    comptable cherche. Un premier exercice ouvert en octobre 2026 et clos en
    décembre 2027 se lit « 2027 » — l'appeler « 2026 » ferait chercher au mauvais
    endroit une DSF déposée en 2028.
    """
    cloture = cloture_voulue or date(naissance.year, 12, 31)
    return Exercice(
        libelle=str(cloture.year),
        ouverture=naissance,
        cloture=cloture,
        clos=False,
    )


#: La qualité du fondateur une fois la société née. Le mot compte : il figure au
#: RCCM et sur les actes, et « gérant » écrit sur une SA se voit.
_QUALITES: dict[FormeJuridique, str] = {
    FormeJuridique.ETS: "Exploitant",
    FormeJuridique.PERSONNE_PHYSIQUE: "Exploitant",
    FormeJuridique.SARL: "Gérant",
    FormeJuridique.SARLU: "Gérant associé unique",
    FormeJuridique.SAS: "Président",
    FormeJuridique.SA: "Directeur général",
    FormeJuridique.SCI: "Gérant",
    FormeJuridique.GIE: "Administrateur",
    FormeJuridique.ASSOCIATION: "Président",
}


def _qualite_du_fondateur(forme: FormeJuridique) -> str:
    """La qualité sous laquelle le fondateur devient dirigeant.

    Une forme non répertoriée rend « Dirigeant » : générique, mais jamais faux.
    Lever ici empêcherait de convertir un dossier abouti pour une entrée absente
    d'une table de libellés — le RCCM est délivré, l'entreprise existe, et le
    logiciel refuserait de l'enregistrer.
    """
    return _QUALITES.get(forme, "Dirigeant")
