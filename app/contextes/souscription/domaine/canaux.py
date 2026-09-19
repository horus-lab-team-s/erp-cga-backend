"""Par quel canal joindre un prospect, et ce qui se passe quand l'un tombe.

─────────────────────────────────────────────────────────────────────────────────
LE PRINCIPE, ET C'EST UNE CORRECTION DE CONCEPTION

**Aucune plateforme extérieure n'est un préalable au parcours.**

Le pas 5 avait installé, sans le vouloir, une dépendance dure : le premier
message d'une relation partait d'un modèle approuvé, et tant qu'aucun modèle ne
l'était, aucun premier contact n'était possible. Le parcours entier attendait
l'approbation d'un tiers.

C'est exactement l'inverse de ce qu'il faut. Une messagerie instantanée est un
**confort**, pas une infrastructure. Le centre travaille depuis des années sans
elle, et le système doit pouvoir en faire autant, du dépôt de la demande jusqu'au
paiement.

LE CANAL PLANCHER EST LE TÉLÉPHONE, ET IL N'A PAS D'API

C'est la garantie sur laquelle tout repose. Le numéro est le seul champ
réellement obligatoire de la demande de contact ; un responsable peut toujours le
composer. **Un téléphone ne demande ni compte vérifié, ni modèle approuvé, ni
palier de messagerie, ni fenêtre de service.**

Tant qu'un numéro est joignable, le parcours avance. Tout le reste est du gain de
temps par-dessus.

LE REPLI EST DÉTERMINÉ, ET IL EST CONFIGURÉ

Le centre décide quels canaux il exploite et dans quel ordre il se replie. C'est
bien lui qui décide, cette fois : ouvrir un compte de messagerie, arrêter le
courriel automatique, réserver un canal à certains services. Le plan vit donc au
référentiel.

⚠️ **Ne pas confondre avec la fenêtre de service.** Celle-ci est imposée par la
plateforme et reste une constante du code. Voir l'en-tête de `conversation.py` :
la configuration est pour ce que le centre décide, ce que le monde impose est une
constante avec sa citation. Ici, c'est le centre qui décide.

CE QUE CE MODULE NE FAIT PAS

Il ne modifie pas la demande. Ce que le visiteur a coché reste ce qu'il a coché,
même si le canal est indisponible le jour où l'on rappelle. Le repli est une
décision d'exécution, prise à l'instant du contact, et journalisée comme telle.

Réécrire la préférence effacerait ce que le client avait demandé, et l'on ne
saurait plus, en rétablissant le canal, qui rebasculer.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contextes.souscription.domaine.demande_de_contact import Canal

__all__ = [
    "AucunCanalJoignable",
    "CANAL_PLANCHER",
    "EtatCanal",
    "PlanDeContact",
    "Repli",
    "canal_a_employer",
    "plan_depuis",
]

#: Le canal qui ne dépend de personne. Voir l'en-tête : un téléphone n'a pas
#: d'API, pas de compte à vérifier, pas de modèle à faire approuver.
#:
#: Le désactiver au référentiel est possible, et le système le dira franchement
#: plutôt que de faire semblant. Mais c'est une décision lourde, et le message
#: d'erreur la nomme.
CANAL_PLANCHER = Canal.APPEL


class AucunCanalJoignable(RuntimeError):
    """Aucun canal actif ne permet de joindre ce prospect.

    Rare, et cela doit le rester : il faut pour cela que le centre ait désactivé
    l'appel. Le message le dit, plutôt que de laisser chercher.
    """


class EtatCanal(BaseModel):
    """Un canal, son état, et son rang dans l'ordre de repli."""

    model_config = ConfigDict(frozen=True)

    canal: Canal
    actif: bool = True
    #: Plus petit = essayé plus tôt. L'ordre est celui du centre, pas une
    #: hiérarchie technique : il peut vouloir appeler d'abord, ou écrire d'abord,
    #: selon ses effectifs du moment.
    rang: int = Field(ge=0)
    #: Pourquoi il est inactif, en clair. Affiché au responsable : « la messagerie
    #: n'est pas encore ouverte » se comprend, « canal indisponible » non.
    motif: str = ""

    @model_validator(mode="after")
    def _un_canal_inactif_dit_pourquoi(self) -> EtatCanal:
        if not self.actif and not self.motif.strip():
            raise ValueError(
                f"canal {self.canal} désactivé sans motif. Un responsable qui ne "
                "peut pas employer un canal doit lire pourquoi, sinon il ouvrira "
                "un incident pour une décision volontaire."
            )
        return self


class Repli(BaseModel):
    """Le canal retenu à l'instant du contact, et pourquoi celui-là."""

    model_config = ConfigDict(frozen=True)

    canal: Canal
    #: `True` si ce n'est pas celui que le client avait demandé.
    replie: bool
    #: Ce qui a écarté les canaux précédents, dans l'ordre. Journalisé au fil :
    #: sans cela, on ne saura pas pourquoi un client qui avait coché la messagerie
    #: a reçu un appel.
    ecartes: tuple[str, ...] = ()


class PlanDeContact(BaseModel):
    """Les canaux que le centre exploite, dans l'ordre où il s'y replie.

    Chargé du référentiel. Le jour où la messagerie s'ouvre, un `actif: true` dans
    un fichier suffit, et rien d'autre ne bouge.
    """

    model_config = ConfigDict(frozen=True)

    canaux: tuple[EtatCanal, ...]

    @model_validator(mode="after")
    def _chaque_canal_une_fois(self) -> PlanDeContact:
        vus = [e.canal for e in self.canaux]
        if len(vus) != len(set(vus)):
            raise ValueError(
                "un canal figure deux fois au plan de contact. Lequel des deux "
                "états fait foi dépendrait de l'ordre de lecture du fichier."
            )
        return self

    @property
    def ordonnes(self) -> tuple[EtatCanal, ...]:
        return tuple(sorted(self.canaux, key=lambda e: (e.rang, e.canal.value)))

    def actifs(self) -> tuple[Canal, ...]:
        """Ce que le centre exploite aujourd'hui, dans l'ordre.

        Sert aussi à la vitrine : proposer au visiteur un canal que le centre
        n'exploite pas produirait une préférence impossible à honorer, et une
        déception au premier rappel.
        """
        return tuple(e.canal for e in self.ordonnes if e.actif)

    def etat(self, canal: Canal) -> EtatCanal | None:
        return next((e for e in self.canaux if e.canal is canal), None)

    def est_actif(self, canal: Canal) -> bool:
        etat = self.etat(canal)
        return etat is not None and etat.actif


def canal_a_employer(
    prefere: Canal,
    plan: PlanDeContact,
    *,
    consentement_vaut: bool,
    a_un_courriel: bool,
    messagerie_prete: bool = True,
) -> Repli:
    """Le canal réellement employable maintenant, en partant de la préférence.

    ─────────────────────────────────────────────────────────────────────────
    L'ORDRE D'ESSAI

    La préférence du client d'abord, toujours. Puis les canaux actifs, dans
    l'ordre du plan. Le premier qui satisfait ses conditions l'emporte.

    LES CONDITIONS, CANAL PAR CANAL

    * **messagerie** : active au plan, consentement en vigueur, et **prête**,
      c'est-à-dire un compte ouvert avec au moins un modèle approuvé. Les trois,
      et le troisième est celui que le pas 5 avait oublié de rendre facultatif ;
    * **courriel** : actif au plan, et une adresse renseignée. Elle est
      facultative au dépôt, donc souvent absente ;
    * **appel** : actif au plan. Rien d'autre. C'est ce qui en fait le plancher.

    ⚠️ **`messagerie_prete` vaut `True` par défaut**, et ce n'est pas de la
    négligence : ce module décrit une règle de repli, pas l'état d'un compte
    tiers. L'appelant qui sait que la plateforme n'est pas prête le dit ; celui
    qui ne s'en préoccupe pas obtient la règle pure.

    LÈVE PLUTÔT QUE DE RENDRE `None`

    Un prospect qu'aucun canal ne permet de joindre n'est pas un cas ordinaire :
    il faut pour cela que le centre ait désactivé l'appel. Rendre `None`
    obligerait chaque appelant à traiter un cas qui ne devrait jamais arriver, et
    l'un d'eux le traiterait en ne faisant rien.
    ─────────────────────────────────────────────────────────────────────────
    """
    ecartes: list[str] = []
    ordre = _ordre_d_essai(prefere, plan)

    for canal in ordre:
        refus = _pourquoi_pas(
            canal,
            plan,
            consentement_vaut=consentement_vaut,
            a_un_courriel=a_un_courriel,
            messagerie_prete=messagerie_prete,
        )
        if refus is None:
            return Repli(
                canal=canal, replie=canal is not prefere, ecartes=tuple(ecartes)
            )
        ecartes.append(refus)

    raise AucunCanalJoignable(
        "aucun canal actif ne permet de joindre ce prospect : "
        + " ; ".join(ecartes)
        + f". ⚠️ Il faut pour cela que « {CANAL_PLANCHER} » ait été désactivé au "
        "référentiel, ce qui prive le centre de son seul canal sans dépendance "
        "extérieure."
    )


def _ordre_d_essai(prefere: Canal, plan: PlanDeContact) -> tuple[Canal, ...]:
    """La préférence d'abord, puis le plan, sans répéter la préférence.

    Un canal absent du plan n'est jamais essayé au-delà de la préférence : ne pas
    le déclarer, c'est ne pas l'exploiter, et l'essayer quand même contournerait
    une décision du centre.
    """
    suite = [e.canal for e in plan.ordonnes if e.canal is not prefere]
    return (prefere, *suite)


def _pourquoi_pas(
    canal: Canal,
    plan: PlanDeContact,
    *,
    consentement_vaut: bool,
    a_un_courriel: bool,
    messagerie_prete: bool,
) -> str | None:
    """Le motif d'écartement, ou `None` si le canal convient."""
    etat = plan.etat(canal)
    if etat is None:
        return f"{canal} n'est pas déclaré au plan de contact"
    if not etat.actif:
        return f"{canal} est désactivé ({etat.motif})"

    if canal is Canal.WHATSAPP:
        if not consentement_vaut:
            return f"{canal} sans consentement en vigueur"
        if not messagerie_prete:
            return (
                f"{canal} : la plateforme n'est pas prête, aucun modèle approuvé"
            )
    elif canal is Canal.COURRIEL and not a_un_courriel:
        return f"{canal} sans adresse renseignée"
    return None


def plan_depuis(etats: Sequence[EtatCanal]) -> PlanDeContact:
    """Commodité de construction, employée par l'adaptateur et par les tests."""
    return PlanDeContact(canaux=tuple(etats))
