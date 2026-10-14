"""L'abonné qui ouvre le dossier de formalité dès qu'une création est payée.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CET ABONNÉ EXISTE

Le site vend « votre SARL immatriculée, statuts et attestations compris ». Le
client qualifie son projet avec un responsable, reçoit sa proforma, l'accepte, la
règle — et jusqu'ici, il recevait **un accès à l'ERP**. Rien de plus. Le dossier
de formalité, celui qui mène au RCCM et au NIU, n'existait que si un collaborateur
pensait à l'ouvrir à la main, depuis un autre écran, sans que rien ne le lui
rappelle.

Le client a payé pour que sa société soit créée, pas pour recevoir un compte.
Faire dépendre le début du travail de la vigilance de quelqu'un, après
encaissement, est exactement ce qu'un produit ne doit pas faire.

⚠️ POURQUOI PAR UN ÉVÉNEMENT, ET NON PAR UN APPEL DIRECT

Le graphe des contextes autorise `creation_entreprise → {conformite,
portefeuille}` et `souscription → {portefeuille}`. Ni l'un ni l'autre ne peut
lire son voisin, et c'est voulu : une création d'entreprise n'a pas à savoir
comment on vend, et la vente n'a pas à savoir comment on immatricule.

L'événement est précisément le mécanisme prévu pour cela. `PaiementEncaissé`
transporte ce qu'il faut savoir — le service vendu, et les cinq faits fondateurs
recueillis à la qualification — et cet abonné n'a besoin de rien d'autre.

⚠️ REJOUABLE, PARCE QUE LE RELAIS GARANTIT « AU MOINS UNE FOIS »

La référence du dossier de formalité est **dérivée de celle du dossier
commercial** : le même encaissement rejoué désigne le même dossier. S'il existe
déjà, l'abonné s'arrête sans rien changer. C'est la seule protection qui tienne
face à une coupure entre la publication et la remise.

⚠️ CE QUI MANQUE N'EST PAS INVENTÉ

Un fait fondateur absent de la qualification est absent de l'événement. L'abonné
ne fabrique alors ni dénomination, ni siège, ni forme juridique : il le dit dans
le journal d'exploitation et laisse le dossier à ouvrir à la main. Un dossier de
formalité ouvert au nom de « Société à nommer » circulerait jusqu'au greffe.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from app.contextes.creation_entreprise.api import (
    DossierCreationIntrouvable,
    Fondateur,
    ouvrir_dossier,
)
from app.contextes.portefeuille.contrats import FormeJuridique
from app.orchestration.boite_d_envoi import EvenementSortant
from app.partage.horloge import maintenant

__all__ = ["abonne_de_souscription", "ouvrir_le_dossier_de_creation"]

_journal = logging.getLogger(__name__)

#: Le service dont l'encaissement ouvre un dossier de formalité.
#:
#: ⚠️ Le code du CATALOGUE, celui que publie `GET /souscription/services`. C'est
#: la décision du 27 septembre : un seul vocabulaire, et c'est le catalogue qui
#: fait foi. Voir `Docs/referentiel/qualification/README.md`.
SERVICE_DE_CREATION = "CREATION"

#: Ce qu'il faut savoir pour ouvrir un dossier, et sans quoi on n'ouvre rien.
FAITS_REQUIS = ("denomination_souhaitee", "forme_juridique", "activite", "siege")


def _reference_du_dossier(dossier_commercial: str) -> str:
    """La référence du dossier de formalité, dérivée du dossier commercial.

    ⚠️ **DÉRIVÉE, ET NON TIRÉE AU HASARD.** C'est ce qui rend l'abonné rejouable :
    le même encaissement, remis deux fois, désigne le même dossier, et le second
    passage constate qu'il existe. Une référence aléatoire ouvrirait deux dossiers
    pour un seul client payé.

    Elle est en outre **lisible** : un exploitant qui voit `CRE-dos-abc123` sait
    de quel dossier commercial il vient, sans requête.
    """
    return f"CRE-{dossier_commercial}"


def ouvrir_le_dossier_de_creation(evenement: EvenementSortant) -> None:
    """Reçoit un `PaiementEncaissé` et ouvre le dossier, si c'était une création."""
    if evenement.charge.get("service") != SERVICE_DE_CREATION:
        return

    # Importé à l'appel : le dépôt vit avec les routes, et ce module est chargé à
    # la composition de l'application.
    from app.contextes.creation_entreprise.adaptateurs.entrant.routes_http import (
        depot,
    )

    commercial = evenement.cle
    reference = _reference_du_dossier(commercial)
    magasin = depot()
    try:
        magasin.lire(reference)
    except DossierCreationIntrouvable:
        pass
    else:
        # Déjà ouvert : événement rejoué, ou collaborateur passé avant.
        return

    faits = evenement.charge.get("faits_fondateurs") or {}
    manquants = [cle for cle in FAITS_REQUIS if not faits.get(cle)]
    if manquants:
        _journal.warning(
            "Création %s payée, dossier de formalité NON ouvert : il manque %s "
            "à la qualification. À ouvrir à la main.",
            commercial,
            ", ".join(manquants),
        )
        return

    try:
        forme = FormeJuridique(faits["forme_juridique"])
    except ValueError:
        _journal.warning(
            "Création %s payée, dossier de formalité NON ouvert : forme juridique "
            "« %s » inconnue du portefeuille. À ouvrir à la main.",
            commercial,
            faits["forme_juridique"],
        )
        return

    dossier = ouvrir_dossier(
        reference=reference,
        fondateur=Fondateur(
            nom=evenement.charge.get("nom_titulaire", ""),
            # ⚠️ Pas de prénom sur une demande de contact : le nom y est entier.
            # On n'en fabrique pas — voir la charge de l'événement.
            prenom="",
            courriel=evenement.charge.get("courriel_titulaire", ""),
            telephone=evenement.charge.get("telephone_titulaire", ""),
        ),
        denomination_souhaitee=faits["denomination_souhaitee"],
        forme_juridique=forme,
        activite=faits["activite"],
        siege=faits["siege"],
        capital=_capital(faits.get("capital_social")),
        a_la_date=maintenant().date(),
        # ⚠️ Le système, et non un collaborateur : personne n'a cliqué.
        par="système:encaissement",
    )
    magasin.enregistrer(dossier)
    _journal.info(
        "Création %s payée : dossier de formalité %s ouvert pour « %s ».",
        commercial,
        reference,
        faits["denomination_souhaitee"],
    )
    _annoncer_l_ouverture(dossier)


def _annoncer_l_ouverture(dossier) -> None:
    """Le lien de suivi, envoyé à la fondatrice.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ LE SEUL MESSAGE DES SEMAINES QUI SUIVENT.

    Entre son règlement et son immatriculation, elle ne recevait rien et ne
    pouvait rien consulter : les dix routes du contexte exigent une habilitation
    de collaborateur. Le canal prévu était la messagerie instantanée ; elle ne
    tient pas, sa fenêtre de service se refermant vingt-quatre heures après le
    dernier mot du client, et une formalité durant des semaines.

    ⚠️ **IL NE LÈVE JAMAIS.** Le dossier EST ouvert et enregistré. Lever ici
    ferait rejouer l'événement par le relais, donc rouvrir un dossier déjà
    ouvert, pour un courriel. C'est le raisonnement que tient déjà
    `ouvrir_sur_paiement` : « une saga en échec se reprend déjà toute seule ».

    ⚠️ **SANS ADRESSE, RIEN**, et sans bruit : l'adresse est facultative au
    formulaire public, et le cabinet la rappellera au téléphone.
    ─────────────────────────────────────────────────────────────────────────────
    """
    try:
        courriel = (dossier.fondateur.courriel or "").strip()
        if not courriel:
            _journal.info(
                "Dossier %s ouvert : aucun lien de suivi envoyé, la demande ne "
                "porte pas d'adresse. Le rappel au téléphone reste le canal.",
                dossier.reference,
            )
            return

        from app.contextes.creation_entreprise.domaine.lien_de_suivi import (
            sceau_de_suivi,
        )
        from app.contextes.transverse.api import (
            ouvrir_le_compte_de_la_creation,
            service_de_notification,
        )
        from app.infrastructure.config import configuration

        reglages = configuration()
        secret = reglages.cle_chiffrement or "secret-de-developpement"
        base = reglages.adresse_publique_vitrine.rstrip("/")
        sceau = sceau_de_suivi(dossier.reference, secret=secret)
        contexte = {
            # ⚠️ `appellation`, et jamais `nom` ni `prenom` seuls : une
            # demande de contact ne porte qu'un champ de nom, et composer la
            # salutation à la main a déjà fait partir un « Bonjour » vide.
            "prenom": dossier.fondateur.appellation,
            "denomination": dossier.denomination_souhaitee,
            # ⚠️ L'adresse de la VITRINE : c'est là qu'elle lit sans compte,
            # comme pour sa proforma. Celle de la console mènerait à une page
            # de connexion, qu'elle n'a pas encore les moyens de franchir.
            "lien": f"{base}/fr/mon-dossier/{dossier.reference}?s={sceau}",
        }
        # ⚠️ LE COMPTE CLIENT, OUVERT AU PAIEMENT (14 octobre 2026).
        #
        # Le Transverse fait partie du socle : la création l'appelle par sa
        # surface publique. Sans compte ouvert (adresse déjà prise par un autre
        # compte, panne), le message part avec le seul lien de suivi, comme
        # avant : le compte s'ajoute, il ne conditionne rien.
        lien_compte = ouvrir_le_compte_de_la_creation(
            reference=dossier.reference,
            courriel=courriel,
            nom=dossier.fondateur.appellation,
            telephone=dossier.fondateur.telephone or None,
        )
        if lien_compte:
            service_de_notification().envoyer(
                "creation.dossier_ouvert_avec_compte",
                destinataire=courriel,
                contexte={**contexte, "lien_compte": lien_compte},
            )
        else:
            service_de_notification().envoyer(
                "creation.dossier_ouvert", destinataire=courriel, contexte=contexte
            )
        _journal.info("Dossier %s : lien de suivi envoyé.", dossier.reference)
    except Exception:  # noqa: BLE001 — voir l'en-tête : jamais au prix du dossier
        _journal.exception(
            "Lien de suivi NON envoyé pour %s. Le dossier EST ouvert ; seule "
            "l'annonce manque, et elle peut se refaire à la main.",
            dossier.reference,
        )


def _capital(brut: str | None) -> Decimal | None:
    """Le capital, ou `None` s'il n'est pas lisible.

    ⚠️ Le capital est **facultatif** à l'ouverture, contrairement aux quatre
    autres faits : un dossier s'instruit sans lui, et il se porte plus tard. Une
    valeur illisible ne doit donc pas empêcher l'ouverture — elle doit seulement
    ne pas être inventée.
    """
    if not brut:
        return None
    try:
        return Decimal(str(brut))
    except (InvalidOperation, ValueError):
        return None


def abonne_de_souscription():
    """La fabrique inscrite au relais. Rend un abonné neuf sur la session du moment."""
    return ouvrir_le_dossier_de_creation
