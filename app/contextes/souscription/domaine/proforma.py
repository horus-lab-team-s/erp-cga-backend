"""La proforma : de l'arrêté du tarif à l'acceptation qui vaut contrat.

─────────────────────────────────────────────────────────────────────────────────
TROIS RÈGLES, ET LA DEUXIÈME COMMANDE TOUT LE MODULE

**1 · Le modèle est une donnée, pas un gabarit dans le code.** Le centre en a
plusieurs, un par type de prestation, et il les fait évoluer. Ajouter un modèle
est une opération de configuration.

**2 · Le document émis est figé pour toujours.** Une fois généré, il est conservé
tel quel avec son empreinte. **On ne le régénère jamais.** Si le modèle change,
les proformas déjà émises gardent l'ancien. Si le montant change, une *nouvelle
version* est émise, avec un nouveau numéro, et l'ancienne reste consultable,
marquée remplacée.

La raison est simple et elle ne souffre aucune exception : ce qui a été envoyé à
un client doit pouvoir être ressorti à l'identique, y compris devant un tribunal,
dix ans plus tard.

**3 · L'acceptation ne demande pas de compte.** Le lien porte une signature, une
durée de validité et un usage unique. Demander une inscription à ce moment précis
fait perdre une partie des prospects, et n'apporte rien : le compte se crée de
toute façon à l'ouverture du tenant.

⚠️ LE PIÈGE NOMMÉ PAR LE DOCUMENT DE CONCEPTION

> « Autoriser la modification après validation. Le montant validé devient
> immuable ; un changement produit une nouvelle version de la proforma, jamais
> une modification de l'ancienne. »

C'est pourquoi il n'existe dans ce module **aucune méthode qui change un
montant**. La seule façon d'en obtenir un autre est `nouvelle_version`, qui
produit un objet distinct et marque le précédent.

CE QUE LA SÉPARATION DES RÔLES FAIT ICI, ET CE QU'ELLE NE FAIT PAS

Celui qui chiffre et celui qui engage peuvent être la même personne dans un petit
centre. Le domaine **enregistre les deux séparément** et expose
`separation_respectee` ; il ne refuse pas. C'est une règle de contrôle interne qui
constatera, et le contrôle interne constate, il n'agit pas.

Refuser ici rendrait le produit inutilisable dans un cabinet de trois personnes,
et ferait contourner la traçabilité par un compte partagé, ce qui est bien pire
que la situation qu'on voulait éviter.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contextes.souscription.domaine.tarification import Debours, LigneTarifaire
from app.partage.copie import transiter

__all__ = [
    "Acceptation",
    "EcartAuBareme",
    "EtatProforma",
    "LienDAcceptation",
    "LienInvalide",
    "ModificationInterdite",
    "Proforma",
    "TarifArrete",
    "TarifRefuse",
    "accepter",
    "emettre",
    "empreinte",
    "lien_pour",
    "numero_suivant",
    "serie_continue",
]

#: La forme d'un numéro : série, année, rang sur quatre chiffres. Le rang repart
#: à un chaque année, ce qui est l'usage, et la série distingue les documents
#: d'un même exercice.
_NUMERO = re.compile(r"^(?P<serie>[A-Z]{2,6})-(?P<annee>\d{4})-(?P<rang>\d{4,})$")


class TarifRefuse(ValueError):
    """Le montant proposé n'est pas acceptable en l'état."""


class ModificationInterdite(ValueError):
    """Ce qui a été émis ne se modifie pas. Voir l'en-tête, règle 2."""


class LienInvalide(ValueError):
    """Le lien d'acceptation est expiré, déjà employé, ou mal signé."""


class EtatProforma(StrEnum):
    EMISE = "EMISE"
    #: Partie chez le client, sur au moins un canal.
    TRANSMISE = "TRANSMISE"
    ACCEPTEE = "ACCEPTEE"
    #: Une version plus récente l'a supplantée. **Elle reste consultable.**
    REMPLACEE = "REMPLACEE"
    ANNULEE = "ANNULEE"


class EcartAuBareme(StrEnum):
    """Où le montant arrêté se situe par rapport à ce que le moteur proposait."""

    #: Dans l'intervalle. Le responsable module librement.
    DANS_L_INTERVALLE = "DANS_L_INTERVALLE"
    #: Sous le plancher. La marge du centre n'est plus protégée.
    SOUS_LE_PLANCHER = "SOUS_LE_PLANCHER"
    #: Au dessus du plafond. Le client n'est plus protégé.
    AU_DESSUS_DU_PLAFOND = "AU_DESSUS_DU_PLAFOND"


class TarifArrete(BaseModel):
    """Le montant retenu, et de quoi le défendre.

    ⚠️ **Hors de l'intervalle, le motif est obligatoire.** Un rabais exceptionnel
    accordé à un gros client reste possible, mais il laisse une trace nominative.
    Sans cette contrainte, l'écart existe quand même et personne ne sait pourquoi.
    """

    model_config = ConfigDict(frozen=True)

    montant: Decimal = Field(gt=0)
    #: Recopiés de la proposition : sans eux, l'écart ne se recalcule pas.
    plancher: Decimal
    reference: Decimal
    plafond: Decimal
    version_bareme: str = Field(min_length=1)

    #: Qui a chiffré, et qui a engagé le centre. Distincts même quand c'est la
    #: même personne : voir l'en-tête.
    chiffre_par: str = Field(min_length=1)
    valide_par: str = Field(min_length=1)
    arrete_le: datetime

    motif: str | None = None

    @model_validator(mode="after")
    def _un_ecart_se_motive(self) -> TarifArrete:
        if self.ecart is not EcartAuBareme.DANS_L_INTERVALLE and not (
            self.motif or ""
        ).strip():
            raise TarifRefuse(
                f"montant {self.montant} hors de l'intervalle "
                f"[{self.plancher} ; {self.plafond}] sans motif. Un écart accordé "
                "sans raison écrite est un écart que personne ne saura défendre "
                "six mois plus tard, ni devant le client, ni devant la direction."
            )
        return self

    @property
    def ecart(self) -> EcartAuBareme:
        if self.montant < self.plancher:
            return EcartAuBareme.SOUS_LE_PLANCHER
        if self.montant > self.plafond:
            return EcartAuBareme.AU_DESSUS_DU_PLAFOND
        return EcartAuBareme.DANS_L_INTERVALLE

    @property
    def ecart_a_la_reference(self) -> Decimal:
        """En francs, signé. Ce que le pilotage agrège pour voir la dérive."""
        return self.montant - self.reference

    @property
    def separation_respectee(self) -> bool:
        """Celui qui chiffre n'est pas celui qui engage.

        ⚠️ **Constaté, jamais imposé.** Refuser rendrait le produit inutilisable
        dans un cabinet de trois personnes, et ferait contourner la traçabilité
        par un compte partagé, ce qui est bien pire. C'est une règle de contrôle
        interne qui s'en saisira, et le contrôle interne constate.
        """
        return self.chiffre_par != self.valide_par


# ── La numérotation ──────────────────────────────────────────────────────────


def numero_suivant(dernier: str | None, *, serie: str, annee: int) -> str:
    """Le numéro qui suit, ou le premier de l'année.

    ─────────────────────────────────────────────────────────────────────────
    LE NUMÉRO EST ATTRIBUÉ À L'ÉMISSION, JAMAIS RÉSERVÉ

    Réserver un numéro avant d'émettre paraît prudent et produit des trous : le
    responsable abandonne, le numéro est perdu, et la série n'est plus continue.

    Une série trouée n'est pas un défaut esthétique. Un contrôle qui constate un
    saut demande où est passé le document manquant, et « nulle part » est une
    réponse qu'on ne peut pas prouver.
    ─────────────────────────────────────────────────────────────────────────
    """
    if dernier is None:
        return f"{serie}-{annee}-0001"
    trouve = _NUMERO.match(dernier)
    if trouve is None:
        raise ValueError(
            f"« {dernier} » n'est pas un numéro de la forme SERIE-ANNEE-RANG. "
            "Poursuivre une série dont on ne sait pas lire le dernier numéro "
            "produirait un doublon ou un trou."
        )
    if int(trouve["annee"]) != annee:
        return f"{serie}-{annee}-0001"
    return f"{serie}-{annee}-{int(trouve['rang']) + 1:04d}"


def serie_continue(numeros: list[str]) -> bool:
    """La série ne saute aucun rang, année par année.

    Sert au contrôle interne et à la recette. Une série vide est continue : il
    n'y a rien à sauter.
    """
    par_annee: dict[tuple[str, int], list[int]] = {}
    for numero in numeros:
        trouve = _NUMERO.match(numero)
        if trouve is None:
            return False
        cle = (trouve["serie"], int(trouve["annee"]))
        par_annee.setdefault(cle, []).append(int(trouve["rang"]))
    for rangs in par_annee.values():
        attendus = list(range(1, len(rangs) + 1))
        if sorted(rangs) != attendus:
            return False
    return True


# ── L'empreinte ──────────────────────────────────────────────────────────────


def empreinte(contenu: bytes) -> str:
    """L'empreinte du document émis, calculée une fois et conservée.

    ⚠️ Vérifiée à chaque lecture, jamais recalculée pour la remplacer. Une
    proforma dont l'empreinte diverge est **signalée**, jamais servie en
    silence : c'est le seul moyen de savoir qu'un document a été touché après
    coup, et un document touché après coup ne vaut plus rien devant un tribunal.
    """
    return hashlib.sha256(contenu).hexdigest()


# ── La proforma ──────────────────────────────────────────────────────────────


class Proforma(BaseModel):
    """Le document commercial chiffré. Une fois accepté, il vaut contrat.

    **Figé.** Aucune méthode ne change un montant : la seule façon d'en obtenir
    un autre est `nouvelle_version`.
    """

    model_config = ConfigDict(frozen=True)

    numero: str = Field(min_length=1)
    #: Le rang dans la suite des versions du même engagement. La v2 porte un
    #: numéro distinct de la v1 : ce ne sont pas deux états d'un document, ce
    #: sont deux documents.
    version: int = Field(default=1, ge=1)
    #: Le numéro de la version précédente, ou `None` pour la première.
    remplace: str | None = None

    dossier: str = Field(min_length=1)
    service: str = Field(min_length=1)
    tarif: TarifArrete
    lignes: tuple[LigneTarifaire, ...] = ()
    debours: tuple[Debours, ...] = ()
    #: Les faits de la qualification **au moment du chiffrage**. Un fait modifié
    #: plus tard ne change pas la proforma passée.
    faits: dict[str, str] = Field(default_factory=dict)

    #: Calculée à l'émission sur le document produit. Voir `empreinte`.
    empreinte_document: str = Field(min_length=64, max_length=64)
    #: Quel modèle a servi, et dans quelle version. Le modèle changera ; ce qui a
    #: été envoyé ne changera pas.
    modele: str = Field(min_length=1)
    version_modele: str = Field(min_length=1)

    etat: EtatProforma = EtatProforma.EMISE
    emise_le: datetime
    transmise_le: datetime | None = None

    @model_validator(mode="after")
    def _une_version_ulterieure_dit_ce_qu_elle_remplace(self) -> Proforma:
        if self.version > 1 and not self.remplace:
            raise ValueError(
                f"{self.numero} : version {self.version} sans numéro remplacé. "
                "Une suite de versions dont on ne peut pas remonter le fil ne "
                "prouve rien : c'est ce lien qui permet de montrer au client ce "
                "qu'il avait reçu avant."
            )
        if self.version == 1 and self.remplace:
            raise ValueError(
                f"{self.numero} : une première version ne remplace rien."
            )
        return self

    @property
    def total(self) -> Decimal:
        """Honoraires arrêtés plus débours. Ce que le client règle."""
        return self.tarif.montant + sum(
            (d.montant for d in self.debours), Decimal(0)
        )

    @property
    def opposable(self) -> bool:
        """Émise ou transmise : elle engage le centre sur son montant."""
        return self.etat in (
            EtatProforma.EMISE,
            EtatProforma.TRANSMISE,
            EtatProforma.ACCEPTEE,
        )

    def document_intact(self, contenu: bytes) -> bool:
        """Le document relu est-il bien celui qui a été émis ?

        À appeler à **chaque** lecture. Le résultat faux n'est pas une erreur de
        transport, c'est une altération, et elle doit remonter comme telle.
        """
        return hmac.compare_digest(self.empreinte_document, empreinte(contenu))

    # ── Les transitions ─────────────────────────────────────────────────────

    def transmise(self, a_l_instant: datetime) -> Proforma:
        """Marque la première transmission. **Rejouable.**

        Retransmettre sur un autre canal ne change pas la date de première
        transmission, qui est celle à partir de laquelle le calendrier de relance
        court. La repousser à chaque envoi ferait relancer un client
        indéfiniment.
        """
        if self.etat is not EtatProforma.EMISE:
            return self
        return transiter(
            self, etat=EtatProforma.TRANSMISE, transmise_le=a_l_instant
        )

    def acceptee(self) -> Proforma:
        if self.etat not in (EtatProforma.EMISE, EtatProforma.TRANSMISE):
            raise ModificationInterdite(
                f"{self.numero} à l'état {self.etat} : seule une proforma émise "
                "ou transmise s'accepte."
            )
        return transiter(self, etat=EtatProforma.ACCEPTEE)

    def annulee(self) -> Proforma:
        if self.etat is EtatProforma.ACCEPTEE:
            raise ModificationInterdite(
                f"{self.numero} est acceptée : elle engage les deux parties et ne "
                "s'annule pas unilatéralement. Un accord de rupture est un autre "
                "geste, et il laisse sa propre trace."
            )
        if self.etat is EtatProforma.ANNULEE:
            return self
        return transiter(self, etat=EtatProforma.ANNULEE)

    def nouvelle_version(
        self,
        tarif: TarifArrete,
        *,
        numero: str,
        contenu: bytes,
        a_l_instant: datetime,
        lignes: tuple[LigneTarifaire, ...] | None = None,
        debours: tuple[Debours, ...] | None = None,
    ) -> tuple[Proforma, Proforma]:
        """Rend `(la précédente marquée remplacée, la nouvelle)`.

        ─────────────────────────────────────────────────────────────────────
        LES DEUX ENSEMBLE, ET JAMAIS L'UNE SANS L'AUTRE

        La signature rend un couple parce que les deux écritures vont ensemble.
        Rendre la seule nouvelle laisserait l'appelant marquer l'ancienne, ou
        l'oublier, et une ancienne non marquée resterait opposable : **deux
        proformas actives pour le même engagement, à deux montants**.

        ⚠️ **On ne remplace pas une proforma acceptée.** Elle vaut contrat. Ce
        qui suit une acceptation est un avenant, pas une version de plus, et
        c'est un autre objet parce que c'est un autre accord.
        ─────────────────────────────────────────────────────────────────────
        """
        if self.etat is EtatProforma.ACCEPTEE:
            raise ModificationInterdite(
                f"{self.numero} est acceptée : elle vaut contrat. Une modification "
                "du montant après acceptation est un avenant, pas une version de "
                "plus, et elle suppose l'accord des deux parties."
            )
        remplacee = transiter(self, etat=EtatProforma.REMPLACEE)
        suivante = transiter(
            self,
            numero=numero,
            version=self.version + 1,
            remplace=self.numero,
            tarif=tarif,
            lignes=self.lignes if lignes is None else lignes,
            debours=self.debours if debours is None else debours,
            empreinte_document=empreinte(contenu),
            etat=EtatProforma.EMISE,
            emise_le=a_l_instant,
            transmise_le=None,
        )
        return remplacee, suivante


def emettre(
    *,
    numero: str,
    dossier: str,
    service: str,
    tarif: TarifArrete,
    contenu: bytes,
    modele: str,
    version_modele: str,
    a_l_instant: datetime,
    lignes: tuple[LigneTarifaire, ...] = (),
    debours: tuple[Debours, ...] = (),
    faits: dict[str, str] | None = None,
) -> Proforma:
    """Émet la première version. L'empreinte est calculée ici, une fois."""
    return Proforma(
        numero=numero,
        dossier=dossier,
        service=service,
        tarif=tarif,
        lignes=lignes,
        debours=debours,
        faits=dict(faits or {}),
        empreinte_document=empreinte(contenu),
        modele=modele,
        version_modele=version_modele,
        emise_le=a_l_instant,
    )


# ── Le lien d'acceptation ────────────────────────────────────────────────────


class LienDAcceptation(BaseModel):
    """Signé, daté, à usage unique. **Aucun compte demandé.**

    ⚠️ La signature couvre le numéro **et la version**. Sans la version, un lien
    émis pour la v1 permettrait d'accepter la v2 : le client accepterait un
    montant qu'il n'a jamais vu.
    """

    model_config = ConfigDict(frozen=True)

    numero: str = Field(min_length=1)
    version: int = Field(ge=1)
    expire_le: datetime
    sceau: str = Field(min_length=32)
    #: Posé à l'usage. Un lien employé ne se réemploie pas.
    employe_le: datetime | None = None

    @property
    def employe(self) -> bool:
        return self.employe_le is not None

    def valide_a(self, a_l_instant: datetime, *, secret: str) -> bool:
        return (
            not self.employe
            and a_l_instant < self.expire_le
            and hmac.compare_digest(
                self.sceau, _sceller(self.numero, self.version, self.expire_le, secret)
            )
        )

    def exiger_valide(self, a_l_instant: datetime, *, secret: str) -> None:
        """Lève avec le motif exact. Trois causes, trois messages.

        Un « lien invalide » générique ferait chercher au support ce que le
        message peut dire : expiré se renvoie, déjà employé se constate, mal
        signé se signale.
        """
        if self.employe:
            raise LienInvalide(
                f"lien de {self.numero} déjà employé le {self.employe_le}. Un "
                "lien à usage unique rouvert n'accepte pas une seconde fois."
            )
        if a_l_instant >= self.expire_le:
            raise LienInvalide(
                f"lien de {self.numero} expiré le {self.expire_le}. Un nouveau "
                "lien se génère sans réémettre la proforma."
            )
        if not hmac.compare_digest(
            self.sceau, _sceller(self.numero, self.version, self.expire_le, secret)
        ):
            raise LienInvalide(
                f"lien de {self.numero} : sceau invalide. Le lien a été modifié, "
                "ou il vient d'un autre environnement."
            )

    def employe_a(self, a_l_instant: datetime) -> LienDAcceptation:
        """Marque le lien employé. **Non rejouable, et c'est le point.**

        Un second appel lève : c'est cette méthode qui porte l'unicité d'usage,
        et la rendre idempotente reviendrait à ne pas l'avoir.
        """
        if self.employe:
            raise LienInvalide(
                f"lien de {self.numero} déjà employé le {self.employe_le}."
            )
        return self.model_copy(update={"employe_le": a_l_instant})


def _sceller(numero: str, version: int, expire_le: datetime, secret: str) -> str:
    """Le sceau, sur les trois éléments qui définissent ce qui est accepté.

    ⚠️ Les champs sont séparés par un caractère qui ne peut apparaître dans
    aucun d'eux. Une concaténation nue permettrait à deux triplets différents de
    produire la même chaîne, donc le même sceau, donc un lien valide pour autre
    chose que ce qu'il annonce.
    """
    charge = f"{numero}\x1f{version}\x1f{expire_le.isoformat()}"
    return hmac.new(secret.encode(), charge.encode(), hashlib.sha256).hexdigest()


def lien_pour(
    proforma: Proforma, *, expire_le: datetime, secret: str
) -> LienDAcceptation:
    """Un lien pour cette proforma, dans cette version."""
    return LienDAcceptation(
        numero=proforma.numero,
        version=proforma.version,
        expire_le=expire_le,
        sceau=_sceller(proforma.numero, proforma.version, expire_le, secret),
    )


# ── L'acceptation ────────────────────────────────────────────────────────────


class Acceptation(BaseModel):
    """Ce que le client a accepté, et quand. **Vaut engagement contractuel.**

    Elle fige le montant, la date, la version du document et l'identité
    déclarée. Elle est un geste **distinct du paiement**, et les deux sont
    conservés séparément : un client peut accepter et ne jamais payer, et cette
    différence est précisément ce que le suivi commercial regarde.
    """

    model_config = ConfigDict(frozen=True)

    proforma: str = Field(min_length=1)
    version: int = Field(ge=1)
    #: Recopié, pas référencé. La proforma est figée, mais recopier le montant
    #: rend l'acceptation lisible seule, y compris dans un export.
    montant: Decimal = Field(gt=0)
    empreinte_document: str = Field(min_length=64, max_length=64)

    acceptee_le: datetime
    #: Ce que le signataire a déclaré. Non vérifié : on n'a pas de pièce
    #: d'identité, et prétendre le contraire serait une fausse garantie.
    identite_declaree: str = Field(min_length=1)
    #: D'où la requête est venue. Sert au litige, pas au contrôle d'accès.
    origine: str = ""

    @property
    def jour(self) -> date:
        return self.acceptee_le.date()


def accepter(
    proforma: Proforma,
    lien: LienDAcceptation,
    *,
    identite_declaree: str,
    a_l_instant: datetime,
    secret: str,
    origine: str = "",
) -> tuple[Proforma, LienDAcceptation, Acceptation]:
    """Accepte, marque le lien employé, et rend les trois ensemble.

    ─────────────────────────────────────────────────────────────────────────
    L'ORDRE DES VÉRIFICATIONS N'EST PAS INDIFFÉRENT

    Le lien d'abord, la proforma ensuite. Un lien mal signé ne doit **rien**
    apprendre sur l'existence ou l'état d'une proforma : c'est la seule chose
    qui protège un numéro devinable.

    LE LIEN DOIT VISER LA BONNE VERSION

    Un lien émis pour la v1 présenté sur la v2 est refusé, même valide en
    lui-même : le client accepterait un montant qu'il n'a jamais vu.
    ─────────────────────────────────────────────────────────────────────────
    """
    lien.exiger_valide(a_l_instant, secret=secret)
    if lien.numero != proforma.numero or lien.version != proforma.version:
        raise LienInvalide(
            f"lien émis pour {lien.numero} v{lien.version}, présenté sur "
            f"{proforma.numero} v{proforma.version}. Accepter ainsi ferait "
            "engager le client sur un montant qu'il n'a pas vu."
        )
    acceptee = proforma.acceptee()
    return (
        acceptee,
        lien.employe_a(a_l_instant),
        Acceptation(
            proforma=proforma.numero,
            version=proforma.version,
            montant=proforma.tarif.montant,
            empreinte_document=proforma.empreinte_document,
            acceptee_le=a_l_instant,
            identite_declaree=identite_declaree,
            origine=origine,
        ),
    )
