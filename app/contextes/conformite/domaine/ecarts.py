"""Écarter un constat : décider qu'une anomalie détectée par le moteur n'en est pas une.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE N'EST PAS UNE SUPPRESSION (pas 92)

Le moteur **décrit**, il n'applique rien (voir `app.moteur.consequence`). C'est ce
qui laisse au réviseur la possibilité de dire : « la règle a réagi, mais elle a tort
sur cette facture ». Exemple : une facture que le moteur lit comme réglée en espèces
au-delà du seuil, alors que le relevé bancaire déposé au dossier atteste un virement.

Écarter n'efface donc jamais le constat. Le rapport reste tel que le moteur l'a
produit, et le constat écarté passe dans une liste séparée, `constats_ecartes`, avec
la référence de la décision. Six mois plus tard, un vérificateur doit pouvoir lire :
*la règle a réagi, voici qui l'a écartée, quand, et pourquoi*.

CE QUE LE CODE NE DÉCIDE PAS : LA POLITIQUE

Trois questions relèvent du cabinet et non du logiciel :

* quelles sévérités peuvent être écartées (un BLOQUANT peut-il l'être ?) ;
* lesquelles exigent un second regard avant de produire leur effet ;
* qui peut donner ce second regard, et quelle longueur de motif est exigée.

Elles sont lues dans `Docs/referentiel/ecarts/politique.yaml`. En l'absence du
fichier, ou d'une entrée, c'est **le réglage le plus prudent** qui s'applique : rien
n'est écartable, et tout ce qui le serait demanderait un second regard. Un réglage
oublié ne doit jamais ouvrir une porte ; il doit la laisser fermée et le dire.

⚠️ L'EMPREINTE, OU POURQUOI UN ÉCART PEUT DEVENIR CADUC

Un écart vise **un constat précis**, pas une règle sur une facture. Si la facture est
rectifiée, ou si le référentiel change et que l'enjeu passe de 38 500 à 96 250 FCFA,
le constat n'est plus celui que le réviseur a examiné. Laisser l'écart s'appliquer
reviendrait à lui faire approuver un montant qu'il n'a jamais vu.

L'écart retient donc une empreinte du constat (règle, sévérité, enjeu, conséquence).
Tant qu'elle correspond, l'écart s'applique. Dès qu'elle diffère, il cesse de
s'appliquer et le constat réapparaît : c'est le sens prudent de l'erreur.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contextes.conformite.domaine.entites import Constat, Severite

__all__ = [
    "MotifType",
    "EcartDeConstat",
    "EcartIntrouvable",
    "EcartRefuse",
    "MotifInsuffisant",
    "PolitiqueDEcart",
    "RegleDEcart",
    "StatutEcart",
    "empreinte_du_constat",
    "piece_appui_exigee_pour",
    "verifier_le_motif",
    "a_regulariser",
]


class EcartRefuse(ValueError):
    """La politique ou l'état de l'écart s'opposent au geste demandé.

    Le message est destiné à l'écran : il dit ce qui empêche, et ce qu'il reste à
    faire. Un refus muet serait contourné par une saisie manuelle.
    """


class MotifInsuffisant(EcartRefuse):
    """Le motif est trop court pour la politique. Distinct du refus : ce n'est pas le
    geste qui est interdit, c'est la demande qui est incomplète (422 et non 409)."""


class EcartIntrouvable(LookupError):
    """Aucun écart ne porte cet identifiant, sur cette pièce."""


class StatutEcart(StrEnum):
    """Où en est la décision.

    * `EN_ATTENTE` : proposée, mais la politique exige un second regard. **Aucun
      effet** tant qu'il n'est pas donné : le constat continue de compter.
    * `EFFECTIF` : l'écart s'applique (sous réserve de l'empreinte, voir l'en-tête).
    * `REFUSE` : le second regard a dit non. Conservé, jamais effacé.
    * `LEVE` : l'écart a été retiré après coup. Conservé lui aussi.
    """

    EN_ATTENTE = "EN_ATTENTE"
    EFFECTIF = "EFFECTIF"
    REFUSE = "REFUSE"
    LEVE = "LEVE"


class RegleDEcart(BaseModel):
    """Ce que la politique permet pour une sévérité donnée."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Faux par défaut : une sévérité que le cabinet n'a pas nommée n'est pas écartable.
    ecartable: bool = False
    #: Vrai par défaut : si elle l'était, un second regard serait exigé.
    second_regard: bool = True
    #: Pas 118 : une pièce d'appui est-elle exigée pour cette sévérité ? Vrai par défaut, comme le
    #: second regard : ce qu'on écarte sans preuve, on le défend sur sa seule parole.
    piece_appui_exigee: bool = True


class RevueDesRegles(BaseModel):
    """Quand une règle est-elle signalée comme bruyante (pas 99).

    Une règle souvent écartée est une règle qui accuse des cas légitimes : son seuil est mal
    calibré, ou sa formulation piège. Le taux d'écartement (écartés sur constats) le mesure.
    Les défauts reprennent la maquette du parcours réviseur : signalée au-delà d'un sur trois,
    trop bruyante au-delà de six sur dix, et jamais jugée sur moins de cinq constats.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    a_recalibrer: float = Field(default=0.34, gt=0, le=1)
    trop_bruyante: float = Field(default=0.60, gt=0, le=1)
    #: En dessous, on ne juge pas : deux écarts sur trois constats ne disent rien d'une règle.
    constats_minimum: int = Field(default=5, ge=1)

    @model_validator(mode="after")
    def _seuils_ordonnes(self) -> RevueDesRegles:
        if self.trop_bruyante < self.a_recalibrer:
            raise ValueError(
                "« trop_bruyante » ne peut pas être plus bas que « a_recalibrer » : une règle "
                "serait trop bruyante avant d'être à recalibrer."
            )
        return self


class MotifType(BaseModel):
    """Un motif type d'écart, proposé par le cabinet (pas 103).

    ⚠️ **Le motif type accélère, le motif détaillé engage.** La maquette le dit, et le code le
    tient : un motif type ne remplace jamais le motif détaillé, qui reste obligatoire et n'est
    jamais prérempli. Le type sert à classer et à relire (« 4 écarts pour NIU vérifié hors
    facture ») ; le détail est ce qui sera opposé à l'administration.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,39}$")
    libelle: str = Field(min_length=5, max_length=120)
    #: Les règles pour lesquelles ce motif est proposé. Vide : toutes.
    regles: tuple[str, ...] = ()


class PolitiqueDEcart(BaseModel):
    """La politique du cabinet, telle qu'elle est lue au référentiel.

    ⚠️ Chaque valeur par défaut est **la plus prudente**. `prudente()` rend la
    politique d'une installation où le fichier manque : rien n'est écartable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Nombre minimal de caractères du motif. Un motif de trois lettres (« ok ») ne
    #: répond pas à la question « pourquoi » que posera le vérificateur.
    motif_minimum: int = Field(default=20, ge=10)
    #: La règle par sévérité. Une sévérité absente reçoit `RegleDEcart()`, fermée.
    par_severite: dict[Severite, RegleDEcart] = Field(default_factory=dict)
    #: Des règles que le cabinet interdit d'écarter, quelle que soit leur sévérité :
    #: celles dont il estime qu'aucune pièce du dossier ne peut contredire le constat.
    regles_non_ecartables: frozenset[str] = frozenset()
    #: Les permissions qui ouvrent le second regard. Chaînes et non énumération : le
    #: domaine de la conformité ne connaît pas la table des rôles, c'est la route
    #: qui confronte ces noms à la session.
    permissions_du_second_regard: tuple[str, ...] = ("ECARTER_CONSTAT",)
    #: Pas 99 : les seuils de la revue de qualité des règles (voir `RevueDesRegles`).
    revue_des_regles: RevueDesRegles = Field(default_factory=lambda: RevueDesRegles())
    #: Pas 118 : combien de jours le cabinet se donne pour joindre la pièce d'appui d'un écart qui
    #: l'exige. Au-delà, l'écart est « à régulariser » et le journal le compte : c'est ce que la
    #: revue trimestrielle regarde en premier.
    delai_de_regularisation_jours: int = Field(default=30, ge=0, le=365)

    #: Pas 103 : les motifs types proposés à l'écart, en particulier en masse.
    motifs_types: tuple[MotifType, ...] = ()
    #: D'où vient la politique : le chemin du fichier, ou « défaut prudent ».
    #: Affiché à l'écran, pour qu'un « non écartable » s'explique.
    source: str = "défaut prudent : aucun fichier de politique n'a été trouvé"

    @model_validator(mode="after")
    def _motifs_types_uniques(self) -> PolitiqueDEcart:
        codes = [m.code for m in self.motifs_types]
        doublons = sorted({c for c in codes if codes.count(c) > 1})
        if doublons:
            raise ValueError(f"motifs types en double : {', '.join(doublons)}")
        return self

    @classmethod
    def prudente(cls) -> PolitiqueDEcart:
        return cls()

    def motifs_pour(self, code_regle: str) -> tuple[MotifType, ...]:
        """Les motifs types proposés pour cette règle, dans l'ordre du fichier."""
        return tuple(m for m in self.motifs_types if not m.regles or code_regle in m.regles)

    def piece_appui_exigee(self, severite: Severite) -> bool:
        """Une pièce d'appui est-elle exigée pour cette sévérité ? Vrai par défaut, comme le second
        regard : ce que la politique n'a pas nommé, elle ne l'allège pas."""
        return self.par_severite.get(severite, RegleDEcart()).piece_appui_exigee

    def regle_pour(self, constat: Constat) -> RegleDEcart:
        """La règle qui s'applique à ce constat, compte tenu de ses deux clés."""
        if constat.code_regle in self.regles_non_ecartables:
            return RegleDEcart(ecartable=False)
        return self.par_severite.get(constat.severite, RegleDEcart())


def empreinte_du_constat(constat: Constat) -> str:
    """Ce qui fait qu'un constat est **le même** d'un contrôle à l'autre.

    Le message et la remédiation n'y entrent pas : ce sont des textes, qu'un
    fiscaliste reformule sans rien changer au fond. Y entrent la règle, la sévérité,
    l'enjeu et la conséquence, c'est-à-dire ce que le réviseur a accepté de ne pas
    faire payer au dossier.

    L'enjeu est normalisé (`Decimal("38500.00")` et `Decimal("38500")` sont le même
    montant) : sans cela, un simple changement d'arrondi rendrait tous les écarts
    caducs.
    """
    enjeu = None if constat.enjeu is None else format(constat.enjeu.normalize(), "f")
    matiere = {
        "regle": constat.code_regle,
        "severite": constat.severite.value,
        "enjeu": enjeu,
        "consequence": constat.consequence.model_dump(mode="json"),
    }
    brut = json.dumps(matiere, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(brut.encode("utf-8")).hexdigest()[:24]


class EcartDeConstat(BaseModel):
    """Une décision d'écart, avec toute son histoire.

    Immuable : chaque geste rend un **nouvel** objet. Les transitions vivent ici, et
    non dans la route, pour qu'aucun appelant ne puisse confirmer son propre écart en
    oubliant une vérification.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: `<référence>:<règle>:<rang>`. Le rang compte les décisions successives sur
    #: le même constat : un écart refusé puis reproposé ne remplace pas le premier.
    identifiant: str
    dossier: str
    reference_document: str
    code_regle: str
    severite: Severite
    empreinte: str
    #: Pas 99 : l'enjeu du constat **au moment de l'écart**, ce que le cabinet a accepté de
    #: ne pas faire payer au dossier. Le journal des dérogations le totalise ; le relire sur
    #: le rapport d'aujourd'hui donnerait un montant que le réviseur n'a jamais vu.
    enjeu: Decimal | None = None

    motif: str
    #: Pas 103 : le code du motif type choisi, s'il y en a un. Le motif détaillé reste
    #: obligatoire (voir `MotifType`).
    motif_type: str | None = None
    propose_par: str
    propose_le: datetime
    second_regard_requis: bool
    statut: StatutEcart

    tranche_par: str | None = None
    tranche_le: datetime | None = None
    motif_du_second_regard: str | None = None

    leve_par: str | None = None
    leve_le: datetime | None = None
    motif_de_levee: str | None = None

    #: Pas 118 : le document qui **prouve** ce que le motif affirme (l'attestation d'immatriculation
    #: obtenue du fournisseur, la copie d'écran du fichier DGI, le courriel de l'adhérent). Le motif
    #: dit ce que le cabinet a vérifié ; la pièce d'appui est ce qu'il montrera au vérificateur.
    #: Une pièce de la collecte (« PJ-2026-0042 ») ou une référence externe, telle que saisie.
    piece_appui: str | None = None
    piece_appui_le: datetime | None = None
    piece_appui_par: str | None = None

    def joindre_une_piece_d_appui(
        self, reference: str, *, par: str, le: datetime
    ) -> EcartDeConstat:
        """Attache (ou remplace) la pièce d'appui.

        ⚠️ **Sur un écart levé ou refusé, c'est refusé** : joindre une preuve à une décision qui
        n'a plus d'effet ferait croire, au journal, qu'elle en a un. La pièce se joint tant que la
        décision vit, avant ou après le second regard.

        ⚠️ **Remplacer est permis, et daté** : une première pièce jointe par erreur se corrige, et
        le journal d'audit garde les deux gestes. Ce que l'écart porte est la dernière, avec son
        auteur et son heure.
        """
        if self.statut in (StatutEcart.LEVE, StatutEcart.REFUSE):
            raise EcartRefuse(
                f"l'écart {self.identifiant} est {self.statut.value} : il n'a plus d'effet, et une "
                "pièce d'appui jointe maintenant ferait croire le contraire."
            )
        reference = reference.strip()
        if len(reference) < 3:
            raise EcartRefuse(
                "la pièce d'appui se désigne : l'identifiant d'une pièce du dossier, ou la "
                "référence du document que vous conservez."
            )
        return self.model_copy(
            update={"piece_appui": reference, "piece_appui_le": le, "piece_appui_par": par}
        )

    @property
    def ouvert(self) -> bool:
        """En attente ou effectif : il occupe la place, un autre ne peut s'y ajouter."""
        return self.statut in (StatutEcart.EN_ATTENTE, StatutEcart.EFFECTIF)

    def trancher(
        self, *, confirme: bool, par: str, le: datetime, motif: str, motif_minimum: int
    ) -> EcartDeConstat:
        """Le second regard : confirmer ou refuser.

        ⚠️ **Celui qui propose ne tranche pas.** C'est tout le sens du second regard,
        et la vérification est ici plutôt que dans la route : une route écrite demain
        pour un autre écran ne pourra pas l'oublier.
        """
        if self.statut is not StatutEcart.EN_ATTENTE:
            raise EcartRefuse(
                f"l'écart {self.identifiant} est {self.statut.value} : seul un écart en "
                "attente reçoit un second regard."
            )
        if par == self.propose_par:
            raise EcartRefuse(
                "le second regard doit venir d'une autre personne que celle qui a proposé l'écart."
            )
        verifier_le_motif(motif, motif_minimum)
        return self.model_copy(
            update={
                "statut": StatutEcart.EFFECTIF if confirme else StatutEcart.REFUSE,
                "tranche_par": par,
                "tranche_le": le,
                "motif_du_second_regard": motif.strip(),
            }
        )

    def lever(self, *, par: str, le: datetime, motif: str, motif_minimum: int) -> EcartDeConstat:
        """Retirer un écart : le constat compte de nouveau.

        N'importe quel détenteur du droit d'écarter peut lever, y compris l'auteur :
        revenir sur une indulgence est toujours le sens prudent, et l'exiger d'un
        tiers ralentirait précisément la correction qu'on souhaite.
        """
        if not self.ouvert:
            raise EcartRefuse(
                f"l'écart {self.identifiant} est {self.statut.value} : il ne produit déjà "
                "aucun effet."
            )
        verifier_le_motif(motif, motif_minimum)
        return self.model_copy(
            update={
                "statut": StatutEcart.LEVE,
                "leve_par": par,
                "leve_le": le,
                "motif_de_levee": motif.strip(),
            }
        )


def verifier_le_motif(motif: str, minimum: int) -> None:
    """Le motif est mesuré sans ses espaces de bord : dix espaces ne sont pas un motif."""
    if len(motif.strip()) < minimum:
        raise MotifInsuffisant(
            f"le motif doit compter au moins {minimum} caractères : il répondra à la "
            "question « pourquoi » que posera le vérificateur."
        )


def piece_appui_exigee_pour(ecart: EcartDeConstat, politique: PolitiqueDEcart) -> bool:
    """La politique exige-t-elle une pièce d'appui pour **cet** écart ?

    Sur sa sévérité, jamais sur sa règle : c'est la gravité de ce qui est écarté qui décide de ce
    qu'on devra montrer. Un écart refusé ou levé n'a plus d'effet : il n'exige plus rien.
    """
    if ecart.statut in (StatutEcart.REFUSE, StatutEcart.LEVE):
        return False
    return politique.piece_appui_exigee(ecart.severite)


def a_regulariser(ecart: EcartDeConstat, politique: PolitiqueDEcart, a_la_date: date) -> bool:
    """L'écart exige une pièce d'appui, n'en a pas, et le délai est écoulé.

    ⚠️ **Le jour de la proposition ne compte pas comme un retard** : un écart proposé ce matin, dont
    l'attestation arrive cet après-midi, n'est pas une irrégularité. C'est passé le délai du
    référentiel que le journal le compte, et que la revue trimestrielle le voit.
    """
    if ecart.piece_appui or not piece_appui_exigee_pour(ecart, politique):
        return False
    return (a_la_date - ecart.propose_le.date()).days > politique.delai_de_regularisation_jours
