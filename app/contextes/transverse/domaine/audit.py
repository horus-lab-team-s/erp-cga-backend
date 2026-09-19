"""Le journal d'audit chaîné par hachage.

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE CHAÎNAGE APPORTE, ET CE QU'IL N'APPORTE PAS

Chaque entrée porte l'empreinte de la précédente, et sa propre empreinte est
calculée sur l'ensemble de son contenu — empreinte précédente comprise. Modifier
une entrée ancienne change son empreinte, donc invalide celle de la suivante, donc
toutes celles d'après. Une falsification isolée devient **détectable**.

Ce que cela n'apporte pas, et qu'il faut dire clairement avant qu'on ne s'y fie :
quelqu'un qui contrôle la base peut **recalculer toute la chaîne** après avoir
modifié une entrée, et le journal redeviendra cohérent. Le chaînage protège de
l'altération ponctuelle et de la corruption, pas d'un administrateur déterminé
disposant du code et du temps.

Pour aller plus loin il faudrait ancrer périodiquement l'empreinte de tête
ailleurs — un support que le cabinet ne contrôle pas. Ce n'est pas fait, et c'est
noté ici plutôt que sous-entendu.

APPEND-ONLY : AUCUNE MISE À JOUR, AUCUNE SUPPRESSION

Le port `JournalAudit` n'offre ni `modifier` ni `supprimer`, y compris pour un
administrateur. Ce n'est pas un oubli à combler : une correction s'écrit comme une
**nouvelle entrée** qui dit ce qui a été corrigé et pourquoi. C'est exactement la
discipline de la contre-passation comptable, et pour la même raison — la trace de
l'erreur fait partie du dossier.

LE COÛT DE L'AJOUT APRÈS COUP EST INFINI

`05-securite-multitenant.md` § 3 le dit : greffer un journal chaîné sur un système
en production suppose de reconstituer l'historique, c'est-à-dire de le fabriquer,
c'est-à-dire de détruire la garantie recherchée. Le chaînage se pose au premier
jour ou jamais. C'est pourquoi il figure dans ce lot, avant tout écran.

CE QUI EST ENREGISTRÉ, ET CE QUI NE DOIT PAS L'ÊTRE

`avant` et `apres` reçoivent l'état de l'objet. **Jamais un mot de passe, jamais
une empreinte de mot de passe, jamais un secret de jeton** : le journal est lu par
tous les rôles qui portent `LIRE_AUDIT`, et il est conservé dix ans. `_EXPURGES`
retire ces clés à l'écriture — un garde-fou, parce que compter sur la vigilance de
l'appelant est ce qui finit toujours par échouer.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "GENESE",
    "EntreeAudit",
    "JournalAltere",
    "calculer_empreinte",
    "corps_canonique",
    "expurger",
    "verifier_chaine",
]

#: L'empreinte précédente de la toute première entrée. Soixante-quatre zéros :
#: la valeur ne peut être le SHA-256 d'aucun contenu réel, ce qui rend le début
#: de chaîne reconnaissable sans champ supplémentaire.
GENESE = "0" * 64

#: Clés dont la valeur ne doit jamais entrer au journal. Comparaison sur le nom
#: normalisé — minuscules, tirets et espaces ramenés au souligné — pour attraper
#: `motDePasse`, `mot-de-passe` et `Mot De Passe` comme `mot_de_passe`.
#:
#: ⚠️ **Les pluriels y figurent**, et ce n'est pas du zèle. Une clé `jetons`
#: portant une liste de jetons fuyait entièrement : la comparaison est exacte, et
#: `jetons` n'est pas `jeton`. Le cas est d'autant plus probable que c'est
#: précisément sous une clé au pluriel qu'on range plusieurs secrets.
_EXPURGES: frozenset[str] = frozenset(
    {
        "mot_de_passe",
        "mots_de_passe",
        "motdepasse",
        "password",
        "passwords",
        "empreinte_mot_de_passe",
        "empreintes_mot_de_passe",
        "secret",
        "secrets",
        "jeton",
        "jetons",
        "token",
        "tokens",
        "empreinte",
        "empreintes",
    }
)

_MASQUE = "«expurgé»"


class JournalAltere(RuntimeError):
    """La chaîne ne se vérifie plus. Le message dit **où**.

    Un journal d'audit qui signale « incohérence détectée » sans dire à quel rang
    oblige à tout relire à la main. Le rang exact permet de savoir quelles entrées
    précèdent la rupture — celles-là restent fiables.
    """


def expurger(donnees: dict[str, Any] | None) -> dict[str, Any] | None:
    """Remplace récursivement les valeurs sensibles par un masque.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LA RÉCURSION DESCEND AUSSI DANS LES LISTES.**

    Elle ne le faisait pas. Un secret imbriqué dans une liste — `{"comptes":
    [{"mot_de_passe": "…"}]}` — traversait le masque et entrait en clair dans un
    journal lu par tous les porteurs de `LIRE_AUDIT` et conservé dix ans.

    Le défaut était **latent** : les cinq appelants qui passent aujourd'hui une
    liste au journal passent des listes de chaînes. Il l'était exactement autant
    que la vigilance sur laquelle ce garde-fou existe pour ne pas compter : *« un
    garde-fou, parce que compter sur la vigilance de l'appelant est ce qui finit
    toujours par échouer »*. Un garde-fou qui ne couvre qu'une forme de donnée
    reporte l'échec, il ne l'évite pas.

    ⚠️ **LE MASQUAGE SE FAIT PAR LA CLÉ, JAMAIS PAR LA VALEUR.**

    On ne cherche pas ce qui *ressemble* à un secret : deviner produirait des faux
    positifs illisibles — une référence de paiement masquée parce qu'elle ressemble
    à un jeton — et des faux négatifs rassurants.

    La clé est normalisée sur les tirets **et les espaces** avant comparaison :
    une charge utile venue d'un prestataire n'écrit pas ses clés comme nous.
    ─────────────────────────────────────────────────────────────────────────────
    """
    if donnees is None:
        return None
    return {cle: _expurger_valeur(cle, valeur) for cle, valeur in donnees.items()}


def _expurger_valeur(cle: str, valeur: Any) -> Any:
    """Le masque s'applique d'abord, la descente ensuite.

    ⚠️ L'ordre compte : une clé sensible portant un dictionnaire doit être masquée
    **en entier**, et non parcourue. `{"jeton": {"valeur": "…"}}` masqué par
    descente laisserait le secret sous une clé que `_EXPURGES` ne connaît pas.
    """
    if cle.lower().replace("-", "_").replace(" ", "_") in _EXPURGES:
        return _MASQUE
    if isinstance(valeur, dict):
        return expurger(valeur)
    if isinstance(valeur, list | tuple):
        # ⚠️ La clé du parent est reconduite : un élément de liste n'a pas de clé
        # à lui, et le tester contre `_EXPURGES` masquerait tout ou rien. Ce sont
        # les clés **à l'intérieur** de chaque élément qui décident.
        return [_expurger_valeur("", element) for element in valeur]
    return valeur


def corps_canonique(
    *,
    rang: int,
    horodatage: datetime,
    acteur: str,
    locataire: str,
    action: str,
    objet_type: str,
    objet_id: str | None,
    avant: dict[str, Any] | None,
    apres: dict[str, Any] | None,
    motif: str | None,
    empreinte_precedente: str,
    mandat: str | None = None,
) -> str:
    """La sérialisation sur laquelle l'empreinte est calculée.

    Trois précautions, sans lesquelles la même entrée produirait deux empreintes
    différentes selon la machine ou la version de Python :

    * `sort_keys` — l'ordre d'insertion d'un dictionnaire ne doit pas compter ;
    * `separators` sans espace — le formatage par défaut a déjà changé par le
      passé ;
    * `ensure_ascii=False` — un accent doit s'écrire de la même façon partout, et
      le corpus est français.

    Les dates sont rendues par `default=str`, donc en ISO 8601. Une empreinte qui
    dépendrait d'un format d'affichage local serait invérifiable ailleurs.
    """
    return json.dumps(
        {
            "rang": rang,
            "horodatage": horodatage,
            "acteur": acteur,
            "locataire": locataire,
            "action": action,
            "objet_type": objet_type,
            "objet_id": objet_id,
            "avant": avant,
            "apres": apres,
            "motif": motif,
            "empreinte_precedente": empreinte_precedente,
            # ⚠️ **La clé n'apparaît que lorsqu'un mandat existe**, et ce n'est pas une
            # coquetterie. Le corps canonique est ce que l'empreinte hache : y ajouter une
            # clé toujours présente, fût-elle nulle, changerait l'empreinte recalculée de
            # **toutes les entrées déjà écrites**, et la vérification de la chaîne
            # échouerait sur un journal que personne n'a touché. Un champ s'ajoute donc à
            # un journal chaîné en ne pesant que sur les entrées qui le portent.
            **({"mandat": mandat} if mandat is not None else {}),
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def calculer_empreinte(corps: str) -> str:
    return hashlib.sha256(corps.encode("utf-8")).hexdigest()


class EntreeAudit(BaseModel):
    """Une ligne du journal. Immuable par construction et par discipline."""

    model_config = ConfigDict(frozen=True)

    #: Position dans la chaîne, à partir de 1. Un rang manquant est une entrée
    #: supprimée : c'est ce que `verifier_chaine` détecte en premier.
    rang: int = Field(ge=1)

    horodatage: datetime

    #: L'identifiant du compte, ou `systeme` pour ce que déclenche un webhook, un
    #: cron ou une migration. Un acteur vide n'existe pas : une action sans auteur
    #: est une action que personne n'assume.
    acteur: str = Field(min_length=1)

    locataire: str = Field(min_length=1)

    #: Convention `objet.verbe` — `session.ouverte`, `ecriture.validee`,
    #: `habilitation.fermee`. Une chaîne plutôt qu'une énumération : les douze
    #: contextes écrivent dans ce journal, et une énumération centrale obligerait
    #: à modifier le socle chaque fois qu'un contexte nomme une action nouvelle.
    action: str = Field(min_length=1)

    objet_type: str = Field(min_length=1)
    objet_id: str | None = None

    avant: dict[str, Any] | None = None
    apres: dict[str, Any] | None = None

    #: Obligatoire pour les actions de `EXIGE_MOTIF` — la contrainte est portée
    #: par la couche d'autorisation, qui seule sait de quelle permission il s'agit.
    motif: str | None = None

    adresse_ip: str | None = None

    #: L'identifiant du mandat sous lequel l'action a été exercée, quand elle l'a été.
    #:
    #: ⚠️ **Il ne remplace pas `locataire`, il le complète.** `locataire` dit *dans quelles
    #: données* l'action a eu lieu ; `mandat` dit *à quel titre* quelqu'un d'ailleurs y a
    #: touché. Une entrée sans mandat dit qu'un locataire a agi chez lui, une entrée avec
    #: mandat nomme celui au titre duquel un autre l'a fait à sa place. C'est la première
    #: question d'un litige, et elle ne se reconstitue pas après coup.
    mandat: str | None = None

    empreinte_precedente: str = Field(min_length=64, max_length=64)
    empreinte: str = Field(min_length=64, max_length=64)

    @classmethod
    def poser(
        cls,
        *,
        precedente: EntreeAudit | None,
        horodatage: datetime,
        acteur: str,
        locataire: str,
        action: str,
        objet_type: str,
        objet_id: str | None = None,
        avant: dict[str, Any] | None = None,
        apres: dict[str, Any] | None = None,
        motif: str | None = None,
        adresse_ip: str | None = None,
        mandat: str | None = None,
    ) -> EntreeAudit:
        """Construit l'entrée suivante, empreinte comprise.

        L'expurgation a lieu **avant** le calcul : ce qui est haché est ce qui est
        stocké, sans quoi la vérification échouerait sur les entrées expurgées.
        """
        rang = 1 if precedente is None else precedente.rang + 1
        chainon = GENESE if precedente is None else precedente.empreinte
        avant_net = expurger(avant)
        apres_net = expurger(apres)
        corps = corps_canonique(
            rang=rang,
            horodatage=horodatage,
            acteur=acteur,
            locataire=locataire,
            action=action,
            objet_type=objet_type,
            objet_id=objet_id,
            avant=avant_net,
            apres=apres_net,
            motif=motif,
            empreinte_precedente=chainon,
            mandat=mandat,
        )
        return cls(
            rang=rang,
            horodatage=horodatage,
            acteur=acteur,
            locataire=locataire,
            action=action,
            objet_type=objet_type,
            objet_id=objet_id,
            avant=avant_net,
            apres=apres_net,
            motif=motif,
            adresse_ip=adresse_ip,
            mandat=mandat,
            empreinte_precedente=chainon,
            empreinte=calculer_empreinte(corps),
        )

    def recalculer(self) -> str:
        """L'empreinte que le contenu actuel devrait produire."""
        return calculer_empreinte(
            corps_canonique(
                rang=self.rang,
                horodatage=self.horodatage,
                acteur=self.acteur,
                locataire=self.locataire,
                action=self.action,
                objet_type=self.objet_type,
                objet_id=self.objet_id,
                avant=self.avant,
                apres=self.apres,
                motif=self.motif,
                empreinte_precedente=self.empreinte_precedente,
                mandat=self.mandat,
            )
        )


def verifier_chaine(entrees: Sequence[EntreeAudit]) -> None:
    """Relit le journal du début et lève au premier défaut.

    Trois contrôles, dans cet ordre — l'ordre compte, parce qu'un rang manquant
    expliquerait à lui seul les ruptures d'empreinte qui suivent :

    1. les rangs se suivent sans trou, à partir de 1 ;
    2. chaque entrée référence l'empreinte de celle qui précède ;
    3. chaque empreinte correspond au contenu qu'elle scelle.

    ⚠️ L'adresse IP n'entre pas dans le calcul, et c'est délibéré : elle décrit
    l'accès, pas l'acte. La faire entrer rendrait la chaîne invérifiable après une
    anonymisation, alors même que l'acte, lui, n'aurait pas bougé.
    """
    precedente: EntreeAudit | None = None
    for position, entree in enumerate(entrees, start=1):
        if entree.rang != position:
            raise JournalAltere(
                f"rupture de séquence au rang {position} : l'entrée porte le rang "
                f"{entree.rang}. Une entrée a été supprimée ou insérée. Les {position - 1} "
                "premières entrées restent vérifiées."
            )
        attendue = GENESE if precedente is None else precedente.empreinte
        if entree.empreinte_precedente != attendue:
            raise JournalAltere(
                f"chaînage rompu au rang {entree.rang} : l'entrée renvoie à "
                f"{entree.empreinte_precedente[:12]}… alors que la précédente vaut "
                f"{attendue[:12]}…"
            )
        if entree.recalculer() != entree.empreinte:
            raise JournalAltere(
                f"contenu altéré au rang {entree.rang} (action « {entree.action} », "
                f"acteur {entree.acteur}) : l'empreinte enregistrée ne correspond plus "
                "à ce que l'entrée contient."
            )
        precedente = entree
