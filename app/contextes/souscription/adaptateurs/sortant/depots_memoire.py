"""Réalisations en mémoire des trois dépôts du contexte M.

Même raisonnement que pour les treize autres dépôts du système — voir l'en-tête de
`app/partage/depot_memoire.py`.

─────────────────────────────────────────────────────────────────────────────────
CE QUI N'EST PAS TENU, ET QUI COMPTE PARTICULIÈREMENT ICI

Le port `DepotPaiements` exige que `enregistrer` soit atomique, parce que c'est là
que se joue la protection contre le double-comptage. `dict.__setitem__` l'est à
l'échelle d'un fil d'exécution Python, et **ne l'est pas** entre deux processus.

Deux instances derrière un répartiteur de charge recevant chacune un exemplaire de
la même notification liraient toutes deux un paiement `EN_ATTENTE`, le
valideraient toutes deux, et ouvriraient deux accès. La garde `activee_le` de la
souscription rattraperait le second — c'est précisément pourquoi elle existe —,
mais compter dessus serait se reposer sur la ceinture après avoir retiré les
bretelles.

En base : une transaction avec `SELECT … FOR UPDATE` sur la ligne du paiement.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime

from app.contextes.souscription.domaine.devis import Devis
from app.contextes.souscription.domaine.dossier_commercial import (
    DossierCommercial,
    EtatDossier,
)
from app.contextes.souscription.domaine.fonds_de_tiers import AppelDeFonds
from app.contextes.souscription.domaine.honoraires import (
    ContratDHonoraires,
    PeriodeDejaReglee,
    ReglementDHonoraires,
)
from app.contextes.souscription.domaine.paiements import Paiement, StatutPaiement
from app.contextes.souscription.domaine.proforma import EtatProforma, Proforma
from app.contextes.souscription.domaine.qualification import Qualification
from app.contextes.souscription.domaine.rappels import RappelAPasser
from app.contextes.souscription.domaine.relance import SuiviDeRelance
from app.contextes.souscription.domaine.situation_de_reglement import (
    GraceDeReglement,
    RegleDeDepassement,
)
from app.contextes.souscription.domaine.souscriptions import (
    EtatSouscription,
    Souscription,
)
from app.partage.depot_memoire import EntrepotMemoire

__all__ = [
    "DepotGracesDeReglementMemoire",
    "DepotRegleDeDepassementMemoire",
    "DepotDevisMemoire",
    "DepotProformasMemoire",
    "DepotQualificationsMemoire",
    "DepotRappelsMemoire",
    "RappelIntrouvable",
    "DepotSuivisDeRelanceMemoire",
    "ProformaIntrouvable",
    "DepotDossiersMemoire",
    "DossierIntrouvable",
    "DepotPaiementsMemoire",
    "DepotSouscriptionsMemoire",
    "DevisIntrouvable",
    "PaiementIntrouvable",
    "SouscriptionIntrouvable",
]

LOCATAIRE_PAR_DEFAUT = "CGA-BRCG"


class DevisIntrouvable(LookupError):
    pass


class DossierIntrouvable(LookupError):
    pass


class ProformaIntrouvable(LookupError):
    pass


class SouscriptionIntrouvable(LookupError):
    pass


class PaiementIntrouvable(LookupError):
    pass


class DepotDevisMemoire:
    """Réalisation de `DepotDevis`."""

    def __init__(self, locataire: str = LOCATAIRE_PAR_DEFAUT) -> None:
        self.locataire = locataire
        self._entrepot: EntrepotMemoire[Devis] = EntrepotMemoire(
            locataire, cle=lambda d: d.reference
        )

    def lire(self, reference: str) -> Devis:
        devis = self._entrepot.prendre(reference)
        if devis is None:
            raise DevisIntrouvable(
                f"aucun devis {reference}. Un lien de devis périmé ou mal recopié "
                "donne exactement cette erreur."
            )
        return devis

    def enregistrer(self, devis: Devis) -> None:
        self._entrepot.poser(devis)

    def lister(self, *, courriel: str | None = None) -> list[Devis]:
        cible = courriel.strip().lower() if courriel else None
        devis = self._entrepot.filtrer(
            lambda d: cible is None or d.prospect.courriel == cible
        )
        return sorted(devis, key=lambda d: d.etabli_le, reverse=True)


class DepotSouscriptionsMemoire:
    """Réalisation de `DepotSouscriptions`."""

    def __init__(self, locataire: str = LOCATAIRE_PAR_DEFAUT) -> None:
        self.locataire = locataire
        self._entrepot: EntrepotMemoire[Souscription] = EntrepotMemoire(
            locataire, cle=lambda s: s.reference
        )

    def lire(self, reference: str) -> Souscription:
        souscription = self._entrepot.prendre(reference)
        if souscription is None:
            raise SouscriptionIntrouvable(f"aucune souscription {reference}")
        return souscription

    def enregistrer(self, souscription: Souscription) -> None:
        self._entrepot.poser(souscription)

    def a_activer(self) -> list[Souscription]:
        """Payées et non activées. **La** requête de surveillance.

        Triée par date d'encaissement croissante : la plus ancienne d'abord, parce
        que c'est le client qui attend depuis le plus longtemps.
        """
        return sorted(
            self._entrepot.filtrer(lambda s: s.etat == EtatSouscription.PAYEE),
            key=lambda s: s.payee_le or s.engagee_le,
        )

    def lister(self) -> list[Souscription]:
        return sorted(self._entrepot.tout(), key=lambda s: s.engagee_le, reverse=True)


class DepotPaiementsMemoire:
    """Réalisation de `DepotPaiements`. Voir l'en-tête pour ce qui n'est pas tenu."""

    def __init__(self, locataire: str = LOCATAIRE_PAR_DEFAUT) -> None:
        self.locataire = locataire
        self._entrepot: EntrepotMemoire[Paiement] = EntrepotMemoire(
            locataire, cle=lambda p: p.identifiant
        )

    def lire(self, identifiant: str) -> Paiement:
        paiement = self._entrepot.prendre(identifiant)
        if paiement is None:
            raise PaiementIntrouvable(f"aucun paiement {identifiant}")
        return paiement

    def par_souscription(self, souscription: str) -> list[Paiement]:
        return sorted(
            self._entrepot.filtrer(lambda p: p.reference_reglee == souscription),
            key=lambda p: p.initie_le,
        )

    def en_attente(self, *, depuis: datetime | None = None) -> list[Paiement]:
        return sorted(
            self._entrepot.filtrer(
                lambda p: p.statut == StatutPaiement.EN_ATTENTE
                and (depuis is None or p.initie_le >= depuis)
            ),
            key=lambda p: p.initie_le,
        )

    def encaisses(self) -> list[Paiement]:
        """Les paiements validés, toutes natures : le journal de caisse (18 octobre 2026)."""
        return sorted(
            self._entrepot.filtrer(lambda p: p.statut == StatutPaiement.VALIDE),
            key=lambda p: p.initie_le,
        )

    def rapprochables(self) -> list[Paiement]:
        """Tout sauf les périmés et les rejetés.

        Les **validés** sont inclus délibérément : un rejeu doit retrouver son
        paiement pour que la validation se déclare sans effet. L'en écarter ferait
        passer un rejeu ordinaire pour un encaissement orphelin, et l'on
        chercherait à la main un problème qui n'existe pas.
        """
        return sorted(
            self._entrepot.filtrer(
                lambda p: p.statut in (StatutPaiement.EN_ATTENTE, StatutPaiement.VALIDE)
            ),
            key=lambda p: p.initie_le,
        )

    def enregistrer(self, paiement: Paiement) -> None:
        self._entrepot.poser(paiement)

    def lister(self) -> list[Paiement]:
        return sorted(self._entrepot.tout(), key=lambda p: p.initie_le, reverse=True)


class DepotDossiersMemoire:
    """Réalisation de `DepotDossiersCommerciaux`."""

    def __init__(self, locataire: str = LOCATAIRE_PAR_DEFAUT) -> None:
        self.locataire = locataire
        self._entrepot: EntrepotMemoire[DossierCommercial] = EntrepotMemoire(
            locataire, cle=lambda d: d.reference
        )

    def lire(self, reference: str) -> DossierCommercial:
        dossier = self._entrepot.prendre(reference)
        if dossier is None:
            raise DossierIntrouvable(f"aucun dossier commercial {reference}")
        return dossier

    def enregistrer(self, dossier: DossierCommercial) -> None:
        self._entrepot.poser(dossier)

    def par_telephone(
        self, telephone: str, *, depuis: datetime
    ) -> list[DossierCommercial]:
        """Le numéro est comparé tel quel, sous sa forme canonique.

        Le normaliser ici serait une seconde normalisation, donc une seconde
        occasion de diverger de celle de la demande — et la divergence ne se
        verrait qu'au moment où un doublon passerait à travers.
        """
        return sorted(
            self._entrepot.filtrer(
                lambda d: any(
                    demande.telephone == telephone and demande.deposee_le >= depuis
                    for demande in d.toutes_les_demandes
                )
            ),
            key=lambda d: d.demande.deposee_le,
        )

    def ouverts(self, *, etat: EtatDossier | None = None) -> list[DossierCommercial]:
        return sorted(
            self._entrepot.filtrer(
                lambda d: d.ouvert and (etat is None or d.etat is etat)
            ),
            key=lambda d: d.depuis_le,
        )

    def payes(self) -> list[DossierCommercial]:
        return sorted(
            self._entrepot.filtrer(lambda d: d.etat is EtatDossier.PAYEE),
            key=lambda d: d.depuis_le,
        )

    def lister(self) -> list[DossierCommercial]:
        return sorted(
            self._entrepot.tout(), key=lambda d: d.demande.deposee_le, reverse=True
        )


    def charge_par_responsable(self) -> dict[str, int]:
        """Combien de dossiers **en cours** chaque responsable porte.

        ⚠️ Les états terminaux sont exclus, comme dans la version SQL : un dossier
        payé ou sans suite ne pèse plus sur personne, et les compter ferait qu'un
        ancien collaborateur productif paraîtrait surchargé pour toujours.
        """
        charge: dict[str, int] = {}
        for dossier in self.ouverts():
            if dossier.responsable is None:
                continue
            charge[dossier.responsable] = charge.get(dossier.responsable, 0) + 1
        return charge


class DepotProformasMemoire:
    """Réalisation de `DepotProformas`."""

    def __init__(self, locataire: str = LOCATAIRE_PAR_DEFAUT) -> None:
        self.locataire = locataire
        self._entrepot: EntrepotMemoire[Proforma] = EntrepotMemoire(
            locataire, cle=lambda p: p.numero
        )

    def lire(self, numero: str) -> Proforma:
        proforma = self._entrepot.prendre(numero)
        if proforma is None:
            raise ProformaIntrouvable(f"aucune proforma {numero}")
        return proforma

    def enregistrer(self, proforma: Proforma) -> None:
        self._entrepot.poser(proforma)

    def par_dossier(self, dossier: str) -> list[Proforma]:
        """De la plus ancienne à la plus récente : c'est l'ordre du fil des
        versions, et celui dans lequel on le raconte au client."""
        return sorted(
            self._entrepot.filtrer(lambda p: p.dossier == dossier),
            key=lambda p: (p.emise_le, p.numero),
        )

    def dernier_numero(self, serie: str, annee: int) -> str | None:
        """Le plus grand numéro de la série pour cette année, ou `None`.

        ⚠️ En mémoire, deux appels concurrents rendraient le même : l'unicité
        est arbitrée par la base, et cette réalisation ne la tient pas. Elle
        sert au développement, jamais à valider le mécanisme.
        """
        prefixe = f"{serie}-{annee}-"
        numeros = [p.numero for p in self._entrepot.tout() if p.numero.startswith(prefixe)]
        return max(numeros) if numeros else None

    def a_relancer(self) -> list[Proforma]:
        """Transmises et sans réponse, de la plus ancienne d'abord."""
        return sorted(
            self._entrepot.filtrer(
                lambda p: p.etat is EtatProforma.TRANSMISE and p.transmise_le is not None
            ),
            key=lambda p: p.transmise_le,
        )

    def lister(self) -> list[Proforma]:
        return sorted(self._entrepot.tout(), key=lambda p: p.emise_le, reverse=True)


class DepotSuivisDeRelanceMemoire:
    """Réalisation de `DepotSuivisDeRelance`.

    ⚠️ En mémoire, ceci **est** la persistance : un redémarrage oublie ce qui a été
    relancé, et le balayage suivant repart au premier palier. Le client reçoit alors
    une seconde fois le message qu'il a déjà reçu. C'est la raison d'être de la
    table, et c'est pourquoi la persistance mémoire n'est pas un mode d'exploitation.
    """

    def __init__(self, locataire: str = LOCATAIRE_PAR_DEFAUT) -> None:
        self.locataire = locataire
        self._entrepot: EntrepotMemoire[SuiviDeRelance] = EntrepotMemoire(
            locataire, cle=lambda s: s.proforma
        )

    def tous(self) -> dict[str, SuiviDeRelance]:
        return {suivi.proforma: suivi for suivi in self._entrepot.tout()}

    def enregistrer(self, suivi: SuiviDeRelance) -> None:
        self._entrepot.poser(suivi)


class RappelIntrouvable(LookupError):
    """Aucun rappel sous cet identifiant."""


class DepotRappelsMemoire:
    """Réalisation de `DepotRappels`."""

    def __init__(self, locataire: str = LOCATAIRE_PAR_DEFAUT) -> None:
        self.locataire = locataire
        self._entrepot: EntrepotMemoire[RappelAPasser] = EntrepotMemoire(
            locataire, cle=lambda r: r.identifiant
        )

    def lire(self, identifiant: str) -> RappelAPasser:
        rappel = self._entrepot.prendre(identifiant)
        if rappel is None:
            raise RappelIntrouvable(f"aucun rappel {identifiant}")
        return rappel

    def enregistrer(self, rappel: RappelAPasser) -> None:
        self._entrepot.poser(rappel)

    def en_attente(self) -> list[RappelAPasser]:
        """Du plus ancien au plus récent : c'est celui qui attend depuis le plus
        longtemps qu'on rappelle en premier."""
        return sorted(
            self._entrepot.filtrer(lambda r: r.en_attente), key=lambda r: r.cree_le
        )

    def par_dossier(self, dossier: str) -> list[RappelAPasser]:
        return sorted(
            self._entrepot.filtrer(lambda r: r.dossier == dossier),
            key=lambda r: r.cree_le,
        )


class DepotQualificationsMemoire:
    """Réalisation de `DepotQualifications`."""

    def __init__(self, locataire: str = LOCATAIRE_PAR_DEFAUT) -> None:
        self.locataire = locataire
        self._entrepot: EntrepotMemoire[Qualification] = EntrepotMemoire(
            locataire, cle=lambda q: q.dossier
        )

    def trouver(self, dossier: str) -> Qualification | None:
        """Rend `None` plutôt que de lever : une qualification absente veut dire
        « pas encore commencée », qui est l'état de tout dossier neuf."""
        return self._entrepot.prendre(dossier)

    def enregistrer(self, qualification: Qualification) -> None:
        self._entrepot.poser(qualification)


class DepotContratsDHonorairesMemoire:
    """Réalisation en mémoire de `DepotContratsDHonoraires`."""

    def __init__(self) -> None:
        self._contrats: dict[str, ContratDHonoraires] = {}

    def lire(self, niu: str) -> ContratDHonoraires | None:
        return self._contrats.get(niu)

    def enregistrer(self, contrat: ContratDHonoraires) -> None:
        self._contrats[contrat.niu] = contrat

    def lister(self) -> list[ContratDHonoraires]:
        return sorted(self._contrats.values(), key=lambda c: c.denomination)


class DepotReglementsDHonorairesMemoire:
    """Réalisation en mémoire de `DepotReglementsDHonoraires`.

    ⚠️ La même garde que la clé unique de la base : sans elle, les cas en mémoire
    accepteraient un double règlement que PostgreSQL refuserait.
    """

    def __init__(self) -> None:
        self._reglements: dict[tuple[str, date], ReglementDHonoraires] = {}

    def pour(self, niu: str) -> list[ReglementDHonoraires]:
        return sorted(
            (r for r in self._reglements.values() if r.niu == niu), key=lambda r: r.periode
        )

    def enregistrer(self, reglement: ReglementDHonoraires) -> None:
        cle = (reglement.niu, reglement.periode)
        if cle in self._reglements:
            raise PeriodeDejaReglee(
                f"la période du {reglement.periode:%d/%m/%Y} de {reglement.niu} est déjà réglée."
            )
        self._reglements[cle] = reglement

    def lister(self) -> list[ReglementDHonoraires]:
        return sorted(self._reglements.values(), key=lambda r: r.constate_le)


class DepotAppelsDeFondsMemoire:
    """Réalisation en mémoire de `DepotAppelsDeFonds`."""

    def __init__(self) -> None:
        self._appels: dict[str, AppelDeFonds] = {}

    def lire(self, identifiant: str) -> AppelDeFonds | None:
        return self._appels.get(identifiant)

    def pour(self, niu: str) -> list[AppelDeFonds]:
        return sorted(
            (a for a in self._appels.values() if a.niu == niu),
            key=lambda a: (a.periode_debut, a.code_obligation),
        )

    def enregistrer(self, appel: AppelDeFonds) -> None:
        self._appels[appel.identifiant] = appel

    def lister(self) -> list[AppelDeFonds]:
        return sorted(self._appels.values(), key=lambda a: a.arrete_le)


class DepotRegleDeDepassementMemoire:
    """La règle de dépassement, en mémoire (18 octobre 2026)."""

    def __init__(self) -> None:
        self._regle: RegleDeDepassement | None = None

    def lire(self) -> RegleDeDepassement | None:
        return self._regle

    def enregistrer(self, regle: RegleDeDepassement) -> None:
        self._regle = regle


class DepotGracesDeReglementMemoire:
    """Les grâces de règlement, en mémoire (18 octobre 2026)."""

    def __init__(self) -> None:
        self._graces: dict[str, GraceDeReglement] = {}

    def pour(self, niu: str) -> list[GraceDeReglement]:
        return sorted(
            (g for g in self._graces.values() if g.niu == niu),
            key=lambda g: g.accordee_le,
            reverse=True,
        )

    def toutes(self) -> list[GraceDeReglement]:
        return list(self._graces.values())

    def enregistrer(self, grace: GraceDeReglement) -> None:
        self._graces[grace.identifiant] = grace
