"""Qui peut prendre un dossier, et ce qu'il porte déjà.

─────────────────────────────────────────────────────────────────────────────────
LE MANQUE QUE CE MODULE COMBLE

Le domaine de l'affectation est éprouvé depuis le pas 4 : cinq critères configurés,
une grille, un classement, un choix motivé. La route, elle, **exigeait que l'appelant
fournisse les candidats**, avec pour chacun son nombre de dossiers ouverts et sa
charge pondérée.

⚠️ C'est une chose qu'aucun appelant ne peut savoir. Une console aurait dû calculer
elle-même la charge de tous les collaborateurs avant chaque affectation, c'est-à-dire
refaire côté client le travail que ce module fait ici. La route était utilisable en
test et inutilisable en service.

CE QUE CE MODULE SAIT, ET CE QU'IL NE SAIT PAS

Il monte les candidatures depuis deux sources réelles : **l'annuaire des
collaborateurs** du contexte transverse, et **la charge en cours** lue sur les
dossiers commerciaux.

⚠️ **Trois données de la grille ne sont pas collectées aujourd'hui**, et il vaut mieux
le dire que le masquer :

* **la région du prospect** : le formulaire public a six champs, délibérément, et
  aucun n'est une région. Le référentiel l'anticipe — « une région non déclarée
  pénalise tout le monde également, l'effet est nul sur le classement » ;
* **l'agence du responsable** : un compte n'en porte pas ;
* **les compétences** : elles sont dérivées des rôles tenus, faute de mieux.

Ces trois-là sont rendues **vides ou dérivées**, jamais devinées. Le critère de
proximité pénalise alors tout le monde de la même façon, ce qui l'annule sans le
fausser.

⚠️ **La conséquence à ne pas commettre** : la route passait le **message libre** du
prospect comme région. Un texte comme « je suis à Bonabéri » aurait fait correspondre
une agence par coïncidence de mots, et une affectation se serait décidée sur un mot du
texte libre. Le vide est franc ; la coïncidence ne l'est pas.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.contextes.souscription.domaine.affectation import Candidature
from app.contextes.transverse.api import EtatCompte, Role, habilitations_actives

__all__ = ["ROLES_PORTEURS", "monter_les_candidatures"]

#: Les rôles qui peuvent porter un dossier commercial.
#:
#: ⚠️ **Dérivé de la permission, et non recopié.** Un rôle créé demain avec
#: `AFFECTER_DOSSIER` entrera dans cette liste sans que personne y pense ; une liste
#: écrite à la main l'aurait oublié, et le nouveau collaborateur n'aurait jamais reçu
#: de dossier sans qu'aucune erreur ne se produise.
def roles_porteurs() -> frozenset[Role]:
    from app.contextes.transverse.api import PERMISSIONS_PAR_ROLE, Permission

    return frozenset(
        role
        for role, permissions in PERMISSIONS_PAR_ROLE.items()
        if Permission.QUALIFIER_PROSPECT in permissions
    )


#: Conservé pour la lisibilité des messages ; calculé une fois.
ROLES_PORTEURS = roles_porteurs()


def monter_les_candidatures(
    *,
    comptes,
    habilitations,
    charge: dict[str, int],
    service: str,
    competence_requise: str = "",
    a_la_date: date,
) -> list[Candidature]:
    """Une candidature par collaborateur en mesure de prendre ce dossier.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **UN COMPTE SUSPENDU N'EST PAS UN CANDIDAT INDISPONIBLE, IL N'EST PAS
    CANDIDAT.**

    La distinction compte. `disponible=False` fait entrer le collaborateur dans le
    classement puis l'en écarte par une règle rédhibitoire, ce qui le fait
    apparaître dans les empêchements : « Awa Bouba, compte suspendu ». C'est
    exactement ce qu'un responsable veut lire quand il se demande pourquoi personne
    n'a été affecté.

    Un compte sans habilitation porteuse, lui, est simplement absent : il n'a jamais
    eu vocation à prendre ce dossier, et le lister encombrerait les empêchements de
    tous les comptables du cabinet.

    LA CHARGE PONDÉRÉE VAUT LE NOMBRE DE DOSSIERS, POUR L'INSTANT

    La matrice de pondération de la section 26 du document de conception n'existe pas
    encore. La rendre à zéro ferait croire à un cabinet où personne n'est chargé, et
    le critère de charge cesserait de départager quoi que ce soit. Le nombre de
    dossiers est une approximation honnête : elle ordonne correctement, elle ne
    prétend rien de plus.
    ─────────────────────────────────────────────────────────────────────────────
    """
    porteurs = roles_porteurs()
    candidatures = []
    for compte in comptes.lister():
        roles = {
            h.role
            for h in habilitations_actives(
                habilitations.pour_compte(compte.identifiant), a_la_date
            )
        }
        if not (roles & porteurs):
            continue

        ouverts = charge.get(compte.identifiant, 0)
        candidatures.append(
            Candidature(
                responsable=compte.identifiant,
                service=service,
                # Voir l'en-tête : vide, jamais deviné. Le message libre du prospect
                # n'est pas une région, et l'employer ferait décider une affectation
                # sur une coïncidence de mots.
                region_demande="",
                agence_responsable="",
                # Les rôles tenus, faute d'un référentiel de compétences. C'est une
                # dérivation assumée : un chargé de formalités *a* la compétence
                # « formalités », et rien ne le dit ailleurs aujourd'hui.
                competences=tuple(sorted(role.value for role in roles)),
                competence_requise=competence_requise,
                dossiers_ouverts=ouverts,
                charge_ponderee=Decimal(ouverts),
                # ⚠️ `ACTIF` seul, et non « pas suspendu ». Un compte en attente
                # d'activation n'a jamais ouvert de session : lui confier un dossier
                # le laisserait sans responsable réel jusqu'à ce que quelqu'un
                # s'aperçoive que la personne n'est jamais venue.
                disponible=compte.etat is EtatCompte.ACTIF,
            )
        )
    return candidatures
