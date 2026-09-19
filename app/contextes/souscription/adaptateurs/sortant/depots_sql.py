"""Les dépôts PostgreSQL du contexte M."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.contextes.souscription.adaptateurs.sortant.depots_memoire import (
    DevisIntrouvable,
    DossierIntrouvable,
    PaiementIntrouvable,
    ProformaIntrouvable,
    RappelIntrouvable,
    SouscriptionIntrouvable,
)
from app.contextes.souscription.adaptateurs.sortant.tables import (
    TableDevis,
    TableDossierCommercial,
    TablePaiement,
    TableProforma,
    TableQualification,
    TableRappelAPasser,
    TableSouscription,
    TableSuiviDeRelance,
)
from app.contextes.souscription.domaine.devis import Devis
from app.contextes.souscription.domaine.dossier_commercial import (
    DossierCommercial,
    EtatDossier,
)
from app.contextes.souscription.domaine.paiements import Paiement, StatutPaiement
from app.contextes.souscription.domaine.proforma import EtatProforma, Proforma
from app.contextes.souscription.domaine.qualification import Qualification
from app.contextes.souscription.domaine.rappels import RappelAPasser
from app.contextes.souscription.domaine.relance import SuiviDeRelance
from app.contextes.souscription.domaine.souscriptions import (
    EtatSouscription,
    Souscription,
)
from app.infrastructure.depot_document import DepotDocument

__all__ = [
    "DepotDevisSql",
    "DepotProformasSql",
    "DepotQualificationsSql",
    "DepotRappelsSql",
    "DepotSuivisDeRelanceSql",
    "DepotDossiersSql",
    "DepotPaiementsSql",
    "DepotSouscriptionsSql",
]


class DepotDevisSql(DepotDocument[Devis]):
    """Réalisation de `DepotDevis`."""

    _table = TableDevis
    _entite = Devis

    def _cle(self, entite: Devis) -> dict[str, Any]:
        return {"reference": entite.reference}

    def _colonnes(self, entite: Devis) -> dict[str, Any]:
        return {
            "courriel": entite.prospect.courriel,
            "etabli_le": entite.etabli_le,
            "valide_jusqu_au": entite.valide_jusqu_au,
            "etat": entite.etat.value,
        }

    def lire(self, reference: str) -> Devis:
        trouve = self._premier(self._requete().where(TableDevis.reference == reference))
        if trouve is None:
            raise DevisIntrouvable(
                f"aucun devis {reference}. Un lien de devis périmé ou mal recopié "
                "donne exactement cette erreur."
            )
        return trouve

    def enregistrer(self, devis: Devis) -> None:
        self._poser(devis)

    def lister(self, *, courriel: str | None = None) -> list[Devis]:
        requete = self._requete().order_by(TableDevis.etabli_le.desc())
        if courriel is not None:
            requete = requete.where(TableDevis.courriel == courriel.strip().lower())
        return self._tous(requete)


class DepotSouscriptionsSql(DepotDocument[Souscription]):
    """Réalisation de `DepotSouscriptions`."""

    _table = TableSouscription
    _entite = Souscription

    def _cle(self, entite: Souscription) -> dict[str, Any]:
        return {"reference": entite.reference}

    def _colonnes(self, entite: Souscription) -> dict[str, Any]:
        return {
            "devis": entite.devis,
            "service": entite.service,
            "etat": entite.etat.value,
            "montant": entite.montant,
            "engagee_le": entite.engagee_le,
            "payee_le": entite.payee_le,
            "niu": entite.niu,
            "compte": entite.compte,
            "prend_effet_le": entite.prend_effet_le,
        }

    def lire(self, reference: str) -> Souscription:
        trouvee = self._premier(
            self._requete().where(TableSouscription.reference == reference)
        )
        if trouvee is None:
            raise SouscriptionIntrouvable(f"aucune souscription {reference}")
        return trouvee

    def enregistrer(self, souscription: Souscription) -> None:
        self._poser(souscription)

    def a_activer(self) -> list[Souscription]:
        """Payées et non activées. **La** requête de surveillance.

        La plus ancienne d'abord : c'est le client qui attend depuis le plus
        longtemps. Un index porte l'état, parce que cette requête doit rester
        instantanée même quand la table grossit — une surveillance lente finit
        par n'être plus consultée.
        """
        return self._tous(
            self._requete()
            .where(TableSouscription.etat == EtatSouscription.PAYEE.value)
            .order_by(TableSouscription.payee_le, TableSouscription.engagee_le)
        )

    def lister(self) -> list[Souscription]:
        return self._tous(self._requete().order_by(TableSouscription.engagee_le.desc()))


class DepotPaiementsSql(DepotDocument[Paiement]):
    """Réalisation de `DepotPaiements`.

    ⚠️ Deux contraintes d'unicité de la base tiennent ici des garanties qui
    n'étaient que du code : la clé d'idempotence, et l'échéance. Une violation
    n'est pas une erreur technique — c'est un second débit évité, et le message
    le dit.
    """

    _table = TablePaiement
    _entite = Paiement

    def _cle(self, entite: Paiement) -> dict[str, Any]:
        return {"identifiant": entite.identifiant}

    def _colonnes(self, entite: Paiement) -> dict[str, Any]:
        return {
            "reference_reglee": entite.reference_reglee,
            "cle_idempotence": entite.cle_idempotence,
            "echeance": entite.echeance,
            "statut": entite.statut.value,
            "montant": entite.montant,
            "telephone": entite.telephone,
            "initie_le": entite.initie_le,
            "reference_externe": entite.reference_externe,
        }

    def lire(self, identifiant: str) -> Paiement:
        trouve = self._premier(
            self._requete().where(TablePaiement.identifiant == identifiant)
        )
        if trouve is None:
            raise PaiementIntrouvable(f"aucun paiement {identifiant}")
        return trouve

    def enregistrer(self, paiement: Paiement) -> None:
        try:
            self._poser(paiement)
        except IntegrityError as conflit:
            self._session.rollback()
            raise ValueError(
                f"paiement {paiement.identifiant} refusé par la base : la clé "
                f"d'idempotence {paiement.cle_idempotence} ou l'échéance "
                f"{paiement.echeance} est déjà employée. Ce n'est pas une panne — "
                "c'est un second débit évité."
            ) from conflit

    def par_souscription(self, souscription: str) -> list[Paiement]:
        return self._tous(
            self._requete()
            .where(TablePaiement.reference_reglee == souscription)
            .order_by(TablePaiement.initie_le)
        )

    def en_attente(self, *, depuis: datetime | None = None) -> list[Paiement]:
        requete = (
            self._requete()
            .where(TablePaiement.statut == StatutPaiement.EN_ATTENTE.value)
            .order_by(TablePaiement.initie_le)
        )
        if depuis is not None:
            requete = requete.where(TablePaiement.initie_le >= depuis)
        return self._tous(requete)

    def rapprochables(self) -> list[Paiement]:
        """En attente **et validés** : un rejeu doit retrouver son paiement pour
        que la validation se déclare sans effet. L'en écarter ferait passer un
        rejeu ordinaire pour un encaissement orphelin."""
        return self._tous(
            self._requete()
            .where(
                TablePaiement.statut.in_(
                    (StatutPaiement.EN_ATTENTE.value, StatutPaiement.VALIDE.value)
                )
            )
            .order_by(TablePaiement.initie_le)
        )

    def lister(self) -> list[Paiement]:
        return self._tous(self._requete().order_by(TablePaiement.initie_le.desc()))


class DepotDossiersSql(DepotDocument[DossierCommercial]):
    """Réalisation de `DepotDossiersCommerciaux`.

    ─────────────────────────────────────────────────────────────────────────
    POURQUOI `par_telephone` INTERROGE LA COLONNE ET NON LE DOCUMENT

    Le numéro est aussi dans le document JSON, et PostgreSQL sait l'y lire. Il
    le ferait en parcourant la table, sur **chaque dépôt de formulaire**, y
    compris les dépôts d'un robot. La colonne promue et son index rendent la
    même réponse en temps constant.

    C'est exactement ce à quoi sert la promotion de colonne, et la seule
    discipline qu'elle demande est que la colonne suive le document. Elle le
    suit : `_poser` les réécrit ensemble.

    ⚠️ `depuis` PORTE SUR `deposee_le`, PAS SUR `depuis_le`

    Deux colonnes aux noms voisins, et les confondre donnerait une détection de
    doublon qui ignore les fils récemment repris et rattrape ceux qui dorment
    depuis trois semaines. La fenêtre anti-doublon regarde **quand le client a
    écrit**, jamais où en est le cabinet.
    ─────────────────────────────────────────────────────────────────────────
    """

    _table = TableDossierCommercial
    _entite = DossierCommercial

    def _cle(self, entite: DossierCommercial) -> dict[str, Any]:
        return {"reference": entite.reference}

    def _colonnes(self, entite: DossierCommercial) -> dict[str, Any]:
        return {
            "telephone": entite.demande.telephone,
            "etat": entite.etat.value,
            "depuis_le": entite.depuis_le,
            # La dernière arrivée, et non la première : c'est elle qui borne la
            # fenêtre anti-doublon pour le dépôt suivant.
            "deposee_le": entite.derniere_demande.deposee_le,
            "responsable": entite.responsable,
        }

    def lire(self, reference: str) -> DossierCommercial:
        trouve = self._premier(
            self._requete().where(TableDossierCommercial.reference == reference)
        )
        if trouve is None:
            raise DossierIntrouvable(f"aucun dossier commercial {reference}")
        return trouve

    def enregistrer(self, dossier: DossierCommercial) -> None:
        self._poser(dossier)

    def par_telephone(
        self, telephone: str, *, depuis: datetime
    ) -> list[DossierCommercial]:
        requete = (
            self._requete()
            .where(TableDossierCommercial.telephone == telephone)
            .where(TableDossierCommercial.deposee_le >= depuis)
            .order_by(TableDossierCommercial.deposee_le)
        )
        return self._tous(requete)

    def ouverts(self, *, etat: EtatDossier | None = None) -> list[DossierCommercial]:
        fermes = [EtatDossier.PAYEE.value, EtatDossier.SANS_SUITE.value]
        requete = (
            self._requete()
            .where(TableDossierCommercial.etat.not_in(fermes))
            .order_by(TableDossierCommercial.depuis_le)
        )
        if etat is not None:
            requete = requete.where(TableDossierCommercial.etat == etat.value)
        return self._tous(requete)

    def charge_par_responsable(self) -> dict[str, int]:
        """Combien de dossiers **en cours** chaque responsable porte.

        ─────────────────────────────────────────────────────────────────────────
        ⚠️ **Un agrégat, et non une liste chargée puis comptée en Python.**

        L'affectation interroge cette charge à chaque dossier déposé. Charger tous
        les dossiers de l'année pour en compter une poignée ferait grossir le coût
        avec l'historique, alors que la question ne porte que sur ce qui est ouvert.

        L'index `ix_dossier_responsable` porte `(locataire, responsable, etat)` : il
        a été posé au pas 2 pour cette requête précisément.

        ⚠️ **Les états terminaux sont exclus.** Un dossier payé ou sans suite ne
        pèse plus sur personne, et les compter ferait qu'un ancien collaborateur
        productif paraîtrait surchargé pour toujours.
        ─────────────────────────────────────────────────────────────────────────
        """
        termines = (EtatDossier.PAYEE.value, EtatDossier.SANS_SUITE.value)
        lignes = self._session.execute(
            select(TableDossierCommercial.responsable, func.count())
            .where(TableDossierCommercial.locataire == self.locataire)
            .where(TableDossierCommercial.responsable.is_not(None))
            .where(TableDossierCommercial.etat.not_in(termines))
            .group_by(TableDossierCommercial.responsable)
        )
        return {responsable: nombre for responsable, nombre in lignes}

    def lister(self) -> list[DossierCommercial]:
        return self._tous(
            self._requete().order_by(TableDossierCommercial.deposee_le.desc())
        )


class DepotProformasSql(DepotDocument[Proforma]):
    """Réalisation SQL. C'est ici que la numérotation devient sûre.

    ⚠️ `dernier_numero` lit le plus grand numéro de la série, et l'écriture qui
    suit peut échouer sur `uq_proforma_locataire_numero`. **C'est voulu** : entre
    la lecture et l'écriture, une autre requête a pu émettre. L'appelant doit
    traiter le conflit en relisant et en réessayant, jamais en ignorant.

    Verrouiller la série entière sérialiserait toutes les émissions du cabinet ;
    une contrainte d'unicité et une reprise sur conflit coûtent moins cher et
    donnent la même garantie.
    """

    _table = TableProforma
    _entite = Proforma

    def _cle(self, entite: Proforma) -> dict[str, Any]:
        """La clé est composite : voir l'en-tête de `TableProforma`.

        Un numéro de proforma est séquentiel par cabinet ; `PRO-2026-0001`
        existe chez chacun d'eux.
        """
        return {"locataire": self.locataire, "numero": entite.numero}

    def _colonnes(self, entite: Proforma) -> dict[str, Any]:
        return {
            "dossier": entite.dossier,
            "etat": entite.etat.value,
            "version": entite.version,
            "remplace": entite.remplace,
            "montant": entite.tarif.montant,
            "emise_le": entite.emise_le,
            "transmise_le": entite.transmise_le,
        }

    def lire(self, numero: str) -> Proforma:
        trouve = self._premier(self._requete().where(TableProforma.numero == numero))
        if trouve is None:
            raise ProformaIntrouvable(f"aucune proforma {numero}")
        return trouve

    def enregistrer(self, proforma: Proforma) -> None:
        self._poser(proforma)

    def par_dossier(self, dossier: str) -> list[Proforma]:
        return self._tous(
            self._requete()
            .where(TableProforma.dossier == dossier)
            .order_by(TableProforma.emise_le, TableProforma.numero)
        )

    def dernier_numero(self, serie: str, annee: int) -> str | None:
        """Le plus grand numéro de la série. Voir l'en-tête pour la concurrence."""
        ligne = self._session.scalars(
            select(TableProforma.numero)
            .where(TableProforma.locataire == self.locataire)
            .where(TableProforma.numero.like(f"{serie}-{annee}-%"))
            .order_by(TableProforma.numero.desc())
            .limit(1)
        ).first()
        return ligne

    def a_relancer(self) -> list[Proforma]:
        return self._tous(
            self._requete()
            .where(TableProforma.etat == EtatProforma.TRANSMISE.value)
            .where(TableProforma.transmise_le.is_not(None))
            .order_by(TableProforma.transmise_le)
        )

    def lister(self) -> list[Proforma]:
        return self._tous(self._requete().order_by(TableProforma.emise_le.desc()))


class DepotSuivisDeRelanceSql(DepotDocument[SuiviDeRelance]):
    """Le suivi de relance en base, à part de la proforma.

    ⚠️ **La clé est composite**, `(locataire, proforma)`, comme celle de la proforma
    et pour la même raison : un numéro est séquentiel par cabinet, et
    `PRO-2026-0001` existe chez chacun d'eux. Une clé sur le seul numéro ferait que
    le premier cabinet à relancer empêcherait tous les autres d'inscrire leur suivi,
    et le refus citerait une contrainte sans rapport apparent avec le cloisonnement.
    """

    _table = TableSuiviDeRelance
    _entite = SuiviDeRelance

    def _cle(self, entite: SuiviDeRelance) -> dict[str, Any]:
        return {"locataire": self.locataire, "proforma": entite.proforma}

    def _colonnes(self, entite: SuiviDeRelance) -> dict[str, Any]:
        return {"derniere_le": entite.derniere_le}

    def tous(self) -> dict[str, SuiviDeRelance]:
        """Tous les suivis du cabinet, en **une** requête.

        Les chercher un par un ferait une requête par proforma transmise, soit des
        centaines par passage sur un cabinet actif. Voir le port : c'est aussi ce
        que `relances_dues` attend, sous cette forme exactement.
        """
        return {suivi.proforma: suivi for suivi in self._tous(self._requete())}

    def enregistrer(self, suivi: SuiviDeRelance) -> None:
        self._poser(suivi)


class DepotRappelsSql(DepotDocument[RappelAPasser]):
    """Le carnet des rappels en base."""

    _table = TableRappelAPasser
    _entite = RappelAPasser

    def _cle(self, entite: RappelAPasser) -> dict[str, Any]:
        return {"locataire": self.locataire, "identifiant": entite.identifiant}

    def _colonnes(self, entite: RappelAPasser) -> dict[str, Any]:
        return {
            "dossier": entite.dossier,
            "cree_le": entite.cree_le,
            "fait_le": entite.fait_le,
        }

    def lire(self, identifiant: str) -> RappelAPasser:
        trouve = self._premier(
            self._requete().where(TableRappelAPasser.identifiant == identifiant)
        )
        if trouve is None:
            raise RappelIntrouvable(f"aucun rappel {identifiant}")
        return trouve

    def enregistrer(self, rappel: RappelAPasser) -> None:
        self._poser(rappel)

    def en_attente(self) -> list[RappelAPasser]:
        """⚠️ `fait_le IS NULL`, et non un booléen inversé.

        C'est ce que porte `ix_rappel_en_attente` : la requête de l'écran du
        responsable doit rendre en temps constant même quand le carnet a un an
        d'historique.
        """
        return self._tous(
            self._requete()
            .where(TableRappelAPasser.fait_le.is_(None))
            .order_by(TableRappelAPasser.cree_le)
        )

    def par_dossier(self, dossier: str) -> list[RappelAPasser]:
        return self._tous(
            self._requete()
            .where(TableRappelAPasser.dossier == dossier)
            .order_by(TableRappelAPasser.cree_le)
        )


class DepotQualificationsSql(DepotDocument[Qualification]):
    """La qualification en base, une ligne par dossier."""

    _table = TableQualification
    _entite = Qualification

    def _cle(self, entite: Qualification) -> dict[str, Any]:
        return {"locataire": self.locataire, "dossier": entite.dossier}

    def _colonnes(self, entite: Qualification) -> dict[str, Any]:
        return {
            "service": entite.service,
            "version_questionnaire": entite.version_questionnaire,
        }

    def trouver(self, dossier: str) -> Qualification | None:
        """Rend `None` plutôt que de lever : une qualification absente veut dire
        « pas encore commencée », qui est l'état de tout dossier neuf. Lever
        obligerait l'appelant à rattraper une exception pour ouvrir une
        qualification vide, ce qu'il fait de toute façon."""
        return self._premier(
            self._requete().where(TableQualification.dossier == dossier)
        )

    def enregistrer(self, qualification: Qualification) -> None:
        self._poser(qualification)
