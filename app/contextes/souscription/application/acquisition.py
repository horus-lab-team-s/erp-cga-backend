"""Le dépôt d'une demande, et ce qu'il en advient.

─────────────────────────────────────────────────────────────────────────────────
LE SEUL POINT D'ENTRÉE DU PARCOURS

Tout ce qui suit — l'affectation, la conversation, la qualification, le
chiffrage, la proforma, le paiement, l'ouverture du tenant — part d'ici. C'est
donc ici qu'il faut être strict, parce que tout ce qui entre mal se corrige
ensuite à la main, sur un client réel, par un responsable qui n'a rien demandé.

TROIS DÉCISIONS, DANS CET ORDRE

1. **La demande est-elle valide ?** Le domaine l'a déjà tranché en la
   construisant : un canal impossible ou un numéro inexploitable n'arrive
   jamais jusqu'ici.
2. **Répète-t-elle une demande récente ?** Si oui, elle rejoint le dossier
   vivant plutôt que d'en ouvrir un second. Le visiteur voit la même page de
   confirmation dans les deux cas, et c'est voulu : lui annoncer « vous avez
   déjà écrit » ne lui apporte rien et laisse croire à un refus.
3. **Sinon**, un dossier s'ouvre à l'état `DÉPOSÉE`.

CE QUE CE CAS D'USAGE NE FAIT PAS

Il n'affecte pas. L'affectation est l'étape 2, elle consomme l'événement
`DemandeDéposée`, et elle a besoin de choses que ce module n'a pas : la charge
des responsables, la carte des agences, les compétences. Les fondre ferait
dépendre le dépôt d'un formulaire de la disponibilité d'un annuaire, et un
visiteur perdrait sa demande parce qu'un service tiers est lent.

Il ne limite pas le débit non plus. Un formulaire ouvert sur internet est une
porte à robots, mais la limitation par adresse et l'épreuve de vérification sont
l'affaire de l'adaptateur entrant : elles regardent la requête HTTP, que ce
module ne voit pas.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel, ConfigDict

from app.contextes.souscription.domaine.demande_de_contact import (
    FENETRE_ANTI_DOUBLON,
    DemandeDeContact,
    doublon_parmi,
)
from app.contextes.souscription.domaine.dossier_commercial import (
    DossierCommercial,
    ouvrir_un_dossier,
)
from app.contextes.souscription.domaine.ports import DepotDossiersCommerciaux

__all__ = ["ResultatDepot", "deposer_une_demande"]


class ResultatDepot(BaseModel):
    """Ce que le dépôt a produit, et si un fil existait déjà.

    Deux champs plutôt qu'un dossier seul, parce que l'appelant a deux choses
    différentes à faire : répondre au visiteur, ce qui ne dépend pas du
    rattachement, et publier l'événement, qui lui en dépend. Un dossier
    rattaché ne doit pas republier `DemandeDéposée` : l'affectation tournerait
    une seconde fois et pourrait changer de responsable en cours d'échange.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    dossier: DossierCommercial
    #: `True` si la demande a rejoint un fil existant plutôt que d'en ouvrir un.
    rattachee: bool


def deposer_une_demande(
    demande: DemandeDeContact,
    depot: DepotDossiersCommerciaux,
    *,
    reference: str,
    fenetre: timedelta = FENETRE_ANTI_DOUBLON,
) -> ResultatDepot:
    """Range la demande : nouveau dossier, ou rattachement au fil vivant.

    ─────────────────────────────────────────────────────────────────────────
    LA RECHERCHE EST BORNÉE PAR LA FENÊTRE, PAS PAR UN NOMBRE DE LIGNES

    `depuis` vaut l'instant du dépôt moins la fenêtre. Demander « les vingt
    derniers dossiers de ce numéro » donnerait une réponse différente selon
    l'insistance du visiteur, ce qui est la définition d'un défaut qu'on ne
    reproduit pas.

    ⚠️ SEULS LES DOSSIERS OUVERTS PEUVENT ACCUEILLIR

    Un dépôt arrivé après un paiement ou un classement est une nouvelle
    intention. Le rattacher ferait apparaître un client qui vient d'acheter
    dans la file des demandes à traiter, et disparaître sa nouvelle demande
    dans un dossier que personne ne rouvre.

    LE RÉSULTAT NE DÉPEND PAS DE L'ORDRE D'ARRIVÉE EN BASE

    `doublon_parmi` choisit la demande la plus récente, jamais la première
    rendue par le dépôt. Deux formulaires soumis à la même seconde produisent
    donc le même rattachement quelle que soit la ligne lue en premier.
    ─────────────────────────────────────────────────────────────────────────
    """
    voisins = [
        dossier
        for dossier in depot.par_telephone(
            demande.telephone, depuis=demande.deposee_le - fenetre
        )
        if dossier.ouvert
    ]

    par_demande = {
        connue.identifiant: dossier
        for dossier in voisins
        for connue in dossier.toutes_les_demandes
    }
    jumelle = doublon_parmi(
        demande,
        [connue for dossier in voisins for connue in dossier.toutes_les_demandes],
        fenetre=fenetre,
    )

    if jumelle is not None:
        rattache = par_demande[jumelle.identifiant].rattacher(demande)
        depot.enregistrer(rattache)
        return ResultatDepot(dossier=rattache, rattachee=True)

    dossier = ouvrir_un_dossier(reference, demande)
    depot.enregistrer(dossier)
    return ResultatDepot(dossier=dossier, rattachee=False)
