"""Le portail en dépôt manuel — la réalisation qui fonctionne aujourd'hui.

─────────────────────────────────────────────────────────────────────────────────
CE QU'ELLE FAIT, ET CE QU'ELLE NE PRÉTEND PAS FAIRE

Elle ne dépose rien. `deposer()` lève, et ce n'est pas une lacune : la DGI ne
publie aucune interface programmatique, et un adaptateur qui feindrait de déposer
produirait des accusés inventés — c'est-à-dire de faux justificatifs de dépôt,
dans un système dont c'est justement la raison d'être d'en produire de vrais.

Ce qu'elle fait est la moitié utile : elle **consigne** l'accusé que le réviseur
rapporte du portail, après avoir vérifié qu'il porte bien sur le document préparé.

LA VÉRIFICATION EST LE CŒUR DE CET ADAPTATEUR

Deux contrôles, et chacun ferme une faute réelle du travail de cabinet :

**Même référence.** Un numéro d'accusé recopié dans la mauvaise ligne — la TVA de
juin saisie sur juillet, un dossier pour un autre — attesterait d'un dépôt qui n'a
pas eu lieu. C'est la faute la plus banale d'une saisie manuelle, et celle qui ne
se découvre qu'au contrôle.

**Même empreinte.** On prépare un dossier, on découvre une écriture à corriger, on
la corrige, on régénère — et l'on colle le numéro obtenu **avant** la correction.
Les chiffres déposés ne sont alors pas ceux du système, et plus personne ne le
sait. L'empreinte le refuse.

L'IDEMPOTENCE EST PORTÉE ICI, ET C'EST LE BON ENDROIT

`retrouver()` interroge par référence — dossier, obligation, période. Déposer deux
fois la même déclaration est une faute qui coûte cher : l'administration en tire
une déclaration rectificative non demandée, parfois un rejet, parfois un double
appel de paiement. Le refus vient donc de la couche qui connaît les dépôts
antérieurs, pas de la mémoire de celui qui saisit.

⚠️ EN MÉMOIRE, DONC PERDU AU REDÉMARRAGE

Un accusé perdu, c'est une preuve de dépôt perdue. C'est le dépôt **le plus
critique de tout le système** à faire passer en base — avant les écritures, avant
les pièces. Une comptabilité se refait ; un accusé de réception, non.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from app.contextes.transverse.domaine.teledeclaration import (
    AccuseReception,
    DocumentATransmettre,
    ModeDepot,
    Portail,
)

__all__ = ["AccuseIncoherent", "DepotManuelRequis", "PortailManuel"]


class DepotManuelRequis(RuntimeError):
    """Ce guichet ne se dépose pas par programme.

    Le message porte les instructions : il est destiné à être montré au réviseur,
    qui doit savoir quoi faire — pas seulement que la machine ne peut pas le faire
    à sa place.
    """


class AccuseIncoherent(ValueError):
    """L'accusé ne porte pas sur le document préparé — voir l'en-tête."""


class PortailManuel:
    """Réalisation de `PortailDeclaratif` pour un guichet à saisie humaine."""

    def __init__(self, portail: Portail = Portail.DGI_TELEDECLARATION) -> None:
        self.portail = portail
        self._accuses: dict[str, AccuseReception] = {}

    def depose_automatiquement(self) -> bool:
        return False

    def deposer(self, document: DocumentATransmettre, *, par: str) -> AccuseReception:
        raise DepotManuelRequis(
            f"Le portail {self.portail} ne reçoit pas de dépôt automatique. "
            f"Déposer la déclaration {document.code_document} de "
            f"{document.entreprise} pour la période du {document.periode_debut} au "
            f"{document.periode_fin} sur le portail, puis revenir saisir le numéro "
            "d'accusé de réception. Le bordereau préparé porte les chiffres à "
            "reporter, et son empreinte sera confrontée à l'accusé."
        )

    def enregistrer_accuse(
        self, accuse: AccuseReception, *, document: DocumentATransmettre
    ) -> AccuseReception:
        if not accuse.concerne(document):
            raise AccuseIncoherent(
                f"l'accusé {accuse.numero} ne correspond pas au document préparé. "
                f"Attendu : {document.reference}, empreinte {document.empreinte[:12]}… ; "
                f"reçu : {accuse.reference_document}, empreinte "
                f"{accuse.empreinte_deposee[:12]}…. Soit le numéro a été saisi sur la "
                "mauvaise ligne, soit le dossier a été modifié après le dépôt — dans les "
                "deux cas, les chiffres déposés ne sont pas ceux qu'on croit."
            )
        # ⚠️ Contrôlé contre le guichet **du document**, et non contre celui du
        # registre (pas 59). Le registre tient les accusés du locataire pour tous
        # ses guichets : la CNPS n'a pas de registre à elle, et un contrôle contre
        # `self.portail` refusait toute cotisation sociale. La garde reste entière :
        # un accusé CNPS ne s'attache toujours pas à une déclaration DGI.
        if accuse.portail is not document.portail:
            raise AccuseIncoherent(
                f"accusé du portail {accuse.portail} pour un document du portail "
                f"{document.portail}. Un accusé CNPS ne prouve rien devant la DGI."
            )
        depose = self._accuses.get(document.reference)
        if depose is not None:
            raise AccuseIncoherent(
                f"{document.reference} porte déjà l'accusé {depose.numero} du "
                f"{depose.depose_le}. Un second dépôt de la même période produirait une "
                "déclaration rectificative non demandée, et parfois un double appel de "
                "paiement."
            )
        # Le mode est imposé, jamais recopié de l'appelant : c'est ce portail qui
        # sait comment le dépôt a eu lieu, pas celui qui saisit.
        consigne = accuse.model_copy(update={"mode": ModeDepot.MANUEL})
        self._accuses[document.reference] = consigne
        return consigne

    def retrouver(self, reference_document: str) -> AccuseReception | None:
        return self._accuses.get(reference_document)

    def tous(self) -> list[AccuseReception]:
        return sorted(self._accuses.values(), key=lambda a: a.depose_le, reverse=True)
