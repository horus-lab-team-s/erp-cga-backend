"""Les modèles de contrat du cabinet (18 octobre 2026, point 23).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE CABINET A DEMANDÉ : un agent rédacteur de contrat. L'agent existait (voir
`redaction_claude.py`) ; il manquait le point de départ. Un modèle donne deux choses :

* **le canevas du cahier de rédaction** : les questions que le juriste pose au client
  pour ce type de contrat. Le cahier rempli est ce que l'agent lit ;
* **les articles types**, versés comme propositions EN ATTENTE. Le juriste retient,
  reformule ou écarte chacun, comme il le fait d'un article de l'agent.

⚠️ SANS CLÉ, LE MODÈLE SUFFIT. Partir d'un modèle ne demande pas l'agent : un cabinet
sans clé d'API rédige quand même, à partir des articles types.

⚠️ AUCUNE CITATION D'ARTICLE DE LOI. Un numéro d'article recopié d'une version périmée
de l'Acte uniforme ou du Code du travail se lit comme vérifié. Les règles qui comptent
sont signalées « à vérifier par le juriste ». Les données propres au client restent
« [À COMPLÉTER : …] » : un projet ne part jamais au client avec une de ces mentions,
c'est au juriste de les remplir en reformulant l'article.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["MODELES_DE_CONTRAT", "ArticleType", "ModeleDeContrat", "modele_de_contrat"]


@dataclass(frozen=True)
class ArticleType:
    titre: str
    texte: str


@dataclass(frozen=True)
class ModeleDeContrat:
    code: str
    libelle: str
    #: Le canevas du cahier de rédaction : les questions à poser au client.
    cahier: str
    articles: tuple[ArticleType, ...]


_PARTIES_ET_LITIGES = (
    ArticleType(
        "Règlement des litiges",
        "Les parties rechercheront d'abord une solution amiable à tout différend né de "
        "l'interprétation ou de l'exécution du présent contrat. À défaut d'accord dans un "
        "délai de trente jours suivant la notification écrite du différend, le litige sera "
        "porté devant les juridictions compétentes de [À COMPLÉTER : ville].",
    ),
    ArticleType(
        "Dispositions finales",
        "Le présent contrat exprime l'intégralité de l'accord des parties. Toute "
        "modification fait l'objet d'un avenant écrit et signé des deux parties. Si une "
        "clause est déclarée nulle, les autres conservent leur effet. Fait en deux "
        "exemplaires originaux, à [À COMPLÉTER : lieu], le [À COMPLÉTER : date].",
    ),
)

MODELES_DE_CONTRAT: tuple[ModeleDeContrat, ...] = (
    ModeleDeContrat(
        code="PRESTATION_DE_SERVICES",
        libelle="Contrat de prestation de services",
        cahier=(
            "Prestataire (dénomination, forme, RCCM, NIU, siège, représentant) : \n"
            "Client (mêmes informations) : \n"
            "Nature exacte des prestations et livrables attendus : \n"
            "Lieu et délais d'exécution : \n"
            "Prix (forfait, taux journalier, au résultat), TVA, modalités et délais de "
            "paiement : \n"
            "Durée du contrat, reconduction : \n"
            "Moyens fournis par le client : \n"
            "Points sensibles pour le client (confidentialité, propriété des livrables, "
            "pénalités de retard) : "
        ),
        articles=(
            ArticleType(
                "Objet",
                "Le présent contrat a pour objet de définir les conditions dans lesquelles le "
                "Prestataire réalise pour le Client les prestations suivantes : [À COMPLÉTER : "
                "description des prestations et des livrables].",
            ),
            ArticleType(
                "Durée",
                "Le contrat prend effet le [À COMPLÉTER : date] pour une durée de "
                "[À COMPLÉTER : durée]. Il pourra être renouvelé par avenant écrit.",
            ),
            ArticleType(
                "Prix et modalités de paiement",
                "En contrepartie des prestations, le Client verse au Prestataire la somme de "
                "[À COMPLÉTER : montant] francs CFA hors taxes, majorée de la TVA au taux en "
                "vigueur le cas échéant. Les factures sont payables dans un délai de "
                "[À COMPLÉTER : nombre] jours à compter de leur réception, par "
                "[À COMPLÉTER : mode de paiement].",
            ),
            ArticleType(
                "Obligations du Prestataire",
                "Le Prestataire exécute les prestations avec diligence et selon les règles de "
                "l'art. Il est tenu d'une obligation de moyens. Il informe sans délai le Client "
                "de toute difficulté susceptible d'affecter les délais ou la qualité des "
                "prestations.",
            ),
            ArticleType(
                "Obligations du Client",
                "Le Client met à la disposition du Prestataire les informations, documents et "
                "accès nécessaires à l'exécution des prestations, et paie le prix convenu aux "
                "échéances prévues.",
            ),
            ArticleType(
                "Confidentialité",
                "Chaque partie garde confidentielles les informations de l'autre dont elle a "
                "connaissance à l'occasion du contrat, pendant toute sa durée et "
                "[À COMPLÉTER : durée] après son terme.",
            ),
            ArticleType(
                "Résiliation",
                "En cas de manquement grave de l'une des parties à ses obligations, non réparé "
                "dans un délai de trente jours suivant une mise en demeure écrite, l'autre "
                "partie peut résilier le contrat par lettre recommandée ou remise contre "
                "décharge. Chaque partie peut en outre y mettre fin moyennant un préavis de "
                "[À COMPLÉTER : durée].",
            ),
            ArticleType(
                "Force majeure",
                "Aucune partie n'est responsable d'un manquement causé par un événement "
                "imprévisible, irrésistible et extérieur. La partie empêchée en informe l'autre "
                "sans délai ; si l'empêchement dure plus de [À COMPLÉTER : durée], chacune peut "
                "résilier le contrat sans indemnité.",
            ),
            *_PARTIES_ET_LITIGES,
        ),
    ),
    ModeleDeContrat(
        code="BAIL_COMMERCIAL",
        libelle="Bail à usage professionnel",
        cahier=(
            "Bailleur (identité ou dénomination, NIU, adresse) : \n"
            "Preneur (dénomination, RCCM, NIU, représentant) : \n"
            "Description et adresse des locaux, titre foncier si connu : \n"
            "Activité autorisée dans les locaux : \n"
            "Loyer mensuel, caution ou avance, date de paiement, révision : \n"
            "Durée du bail et date d'effet : \n"
            "Charges, travaux, entretien : qui paie quoi : \n"
            "Sous-location ou cession : autorisées ou non : "
        ),
        articles=(
            ArticleType(
                "Objet et désignation des locaux",
                "Le Bailleur donne à bail au Preneur, qui accepte, les locaux situés à "
                "[À COMPLÉTER : adresse et description], que le Preneur déclare connaître pour "
                "les avoir visités.",
            ),
            ArticleType(
                "Destination",
                "Les locaux sont destinés exclusivement à l'activité de [À COMPLÉTER : "
                "activité]. Tout changement d'activité requiert l'accord écrit du Bailleur.",
            ),
            ArticleType(
                "Durée",
                "Le bail est consenti pour une durée de [À COMPLÉTER : durée] à compter du "
                "[À COMPLÉTER : date]. Les règles de renouvellement du bail à usage "
                "professionnel prévues par le droit OHADA s'appliquent ; leurs délais sont à "
                "vérifier par le juriste.",
            ),
            ArticleType(
                "Loyer",
                "Le loyer est fixé à [À COMPLÉTER : montant] francs CFA par mois, payable "
                "d'avance le [À COMPLÉTER : jour] de chaque mois. Le Preneur verse à la "
                "signature [À COMPLÉTER : montant de la caution ou de l'avance], restituée en "
                "fin de bail déduction faite des sommes dues. La révision du loyer suit les "
                "conditions légales, à vérifier par le juriste.",
            ),
            ArticleType(
                "Charges et entretien",
                "Le Preneur supporte les consommations d'eau et d'électricité et l'entretien "
                "courant des locaux. Les grosses réparations restent à la charge du Bailleur. "
                "[À COMPLÉTER : répartition particulière convenue].",
            ),
            ArticleType(
                "Sous-location et cession",
                "Le Preneur ne peut sous-louer tout ou partie des locaux sans l'accord écrit "
                "du Bailleur. La cession du bail suit les règles applicables au bail à usage "
                "professionnel, à vérifier par le juriste.",
            ),
            ArticleType(
                "Résiliation",
                "À défaut de paiement du loyer ou d'exécution d'une clause du bail, et après "
                "une mise en demeure restée sans effet dans le délai légal, le Bailleur peut "
                "demander la résiliation du bail en justice. Les formes de la mise en demeure "
                "sont à vérifier par le juriste.",
            ),
            *_PARTIES_ET_LITIGES,
        ),
    ),
    ModeleDeContrat(
        code="TRAVAIL_CDI",
        libelle="Contrat de travail à durée indéterminée",
        cahier=(
            "Employeur (dénomination, NIU, numéro employeur CNPS, représentant) : \n"
            "Salarié (nom, date et lieu de naissance, adresse, pièce d'identité) : \n"
            "Emploi, catégorie et échelon selon la convention collective applicable : \n"
            "Date d'embauche, période d'essai souhaitée : \n"
            "Lieu de travail, horaires : \n"
            "Salaire de base, primes et indemnités, avantages en nature : \n"
            "Clauses particulières (confidentialité, non-concurrence, mobilité) : "
        ),
        articles=(
            ArticleType(
                "Engagement",
                "L'Employeur engage le Salarié, à compter du [À COMPLÉTER : date], pour une "
                "durée indéterminée, en qualité de [À COMPLÉTER : emploi], catégorie "
                "[À COMPLÉTER : catégorie], échelon [À COMPLÉTER : échelon] de la convention "
                "collective [À COMPLÉTER : convention].",
            ),
            ArticleType(
                "Période d'essai",
                "Le présent contrat ne devient définitif qu'à l'issue d'une période d'essai de "
                "[À COMPLÉTER : durée], dans les limites fixées par la réglementation selon la "
                "catégorie, à vérifier par le juriste. Pendant l'essai, chaque partie peut "
                "rompre le contrat sans préavis ni indemnité.",
            ),
            ArticleType(
                "Lieu et durée du travail",
                "Le Salarié exerce ses fonctions à [À COMPLÉTER : lieu]. La durée du travail "
                "est celle de l'établissement, soit [À COMPLÉTER : horaires], dans le respect "
                "de la durée légale.",
            ),
            ArticleType(
                "Rémunération",
                "Le Salarié perçoit un salaire de base mensuel brut de [À COMPLÉTER : montant] "
                "francs CFA, auquel s'ajoutent [À COMPLÉTER : primes et indemnités]. Les "
                "cotisations sociales et l'impôt sur le revenu sont retenus à la source selon "
                "la réglementation en vigueur.",
            ),
            ArticleType(
                "Obligations du Salarié",
                "Le Salarié exécute personnellement les tâches qui lui sont confiées, respecte "
                "le règlement intérieur et garde la discrétion sur les informations de "
                "l'entreprise dont il a connaissance.",
            ),
            ArticleType(
                "Congés",
                "Le Salarié bénéficie des congés payés prévus par la réglementation et la "
                "convention collective applicables.",
            ),
            ArticleType(
                "Rupture du contrat",
                "Le contrat peut être rompu par chacune des parties dans les conditions "
                "prévues par le Code du travail et la convention collective, moyennant le "
                "préavis applicable, sauf faute lourde. Les durées de préavis sont à vérifier "
                "par le juriste selon la catégorie.",
            ),
            *_PARTIES_ET_LITIGES,
        ),
    ),
    ModeleDeContrat(
        code="TRAVAIL_CDD",
        libelle="Contrat de travail à durée déterminée",
        cahier=(
            "Employeur (dénomination, NIU, numéro employeur CNPS, représentant) : \n"
            "Salarié (nom, date et lieu de naissance, adresse, pièce d'identité) : \n"
            "Motif du recours à un contrat à durée déterminée : \n"
            "Emploi, catégorie et échelon : \n"
            "Date de début, terme précis ou fin de la tâche : \n"
            "Salaire de base, primes et indemnités : \n"
            "Renouvellements déjà intervenus avec ce salarié : "
        ),
        articles=(
            ArticleType(
                "Engagement et motif",
                "L'Employeur engage le Salarié en qualité de [À COMPLÉTER : emploi], "
                "catégorie [À COMPLÉTER : catégorie], pour le motif suivant : [À COMPLÉTER : "
                "motif].",
            ),
            ArticleType(
                "Durée",
                "Le contrat est conclu pour une durée déterminée de [À COMPLÉTER : durée], du "
                "[À COMPLÉTER : date de début] au [À COMPLÉTER : date de fin]. La durée "
                "maximale et le nombre de renouvellements autorisés sont à vérifier par le "
                "juriste : au-delà, le contrat devient à durée indéterminée.",
            ),
            ArticleType(
                "Rémunération",
                "Le Salarié perçoit un salaire de base mensuel brut de [À COMPLÉTER : montant] "
                "francs CFA, auquel s'ajoutent [À COMPLÉTER : primes et indemnités].",
            ),
            ArticleType(
                "Fin du contrat",
                "Le contrat prend fin de plein droit à son terme. Avant le terme, il ne peut "
                "être rompu qu'en cas de faute lourde, de force majeure ou d'accord des "
                "parties constaté par écrit.",
            ),
            *_PARTIES_ET_LITIGES,
        ),
    ),
    ModeleDeContrat(
        code="CONFIDENTIALITE",
        libelle="Accord de confidentialité",
        cahier=(
            "Parties (dénomination, RCCM, NIU, représentants) : \n"
            "Projet ou discussion à l'occasion duquel les informations sont échangées : \n"
            "Accord unilatéral ou réciproque : \n"
            "Informations particulièrement sensibles : \n"
            "Durée de l'obligation de confidentialité : "
        ),
        articles=(
            ArticleType(
                "Objet",
                "Le présent accord définit les conditions dans lesquelles les parties "
                "échangent des informations confidentielles dans le cadre de [À COMPLÉTER : "
                "projet].",
            ),
            ArticleType(
                "Informations confidentielles",
                "Sont confidentielles toutes les informations, quel qu'en soit le support, "
                "communiquées par une partie à l'autre dans ce cadre, à l'exception de celles "
                "qui étaient publiques ou connues de la partie qui les reçoit avant leur "
                "communication.",
            ),
            ArticleType(
                "Engagements",
                "La partie qui reçoit les informations ne les utilise que pour le projet, ne "
                "les communique qu'à ceux de ses collaborateurs qui en ont besoin et qui sont "
                "tenus à la même discrétion, et les restitue ou les détruit à la demande de "
                "l'autre partie.",
            ),
            ArticleType(
                "Durée",
                "Les obligations du présent accord courent pendant [À COMPLÉTER : durée] à "
                "compter de sa signature.",
            ),
            *_PARTIES_ET_LITIGES,
        ),
    ),
)


def modele_de_contrat(code: str) -> ModeleDeContrat | None:
    return next((m for m in MODELES_DE_CONTRAT if m.code == code), None)
