"""Charge et production : qui est saturé, et ce qu'il faudrait réaffecter (pas 105).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LA DIRECTION VEUT SAVOIR

Maquette « Pilotage direction », vue C : « qui est saturé, ce qui avance, ce qu'il faut
réaffecter ». Le tableau de bord du pilotage disait déjà combien de dossiers et quel risque
chacun porte ; il ne disait ni si c'était **trop**, ni **quoi faire**.

* **La charge** d'un collaborateur est un nombre de points (dossiers, pièces en attente,
  échéances du mois, retards), rapporté à la **capacité de son rôle**. Les points et les
  capacités sont au référentiel : ce sont des arbitrages du cabinet, pas des vérités.
* **La production du mois** est mesurée **sur les dossiers qu'il porte** : pièces reçues et
  traitées, écritures validées, remarques de revue reçues.
* **Les réaffectations proposées** : pour un collaborateur saturé, les dossiers qu'un collègue
  du même rôle peut reprendre sans dépasser le seuil cible. La direction valide ; la plateforme
  ne réaffecte jamais seule.

⚠️ CE N'EST PAS UNE ÉVALUATION DES PERSONNES

La doctrine de `ChargeCollaborateur` vaut ici, et plus encore. La plateforme **ne sait pas**
qui a traité une pièce (la pièce ne le retient pas) ; elle sait sur quels dossiers elle a été
traitée. Attribuer cette production à une personne serait inventer une mesure individuelle, et
une mesure individuelle se met à être optimisée : on refuse les dossiers difficiles. Tout ce
qui suit décrit **la répartition du travail**, et sa seule action est de la rééquilibrer.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "DossierPourLaCharge",
    "LigneDeCharge",
    "PorteurDeDossiers",
    "PropositionDeReaffectation",
    "ReglagesDeLaCharge",
    "mesurer_la_charge",
    "proposer_des_reaffectations",
]


class ReglagesDeLaCharge(BaseModel):
    """`Docs/referentiel/pilotage/charge.yaml`. Valeurs de départ, à arrêter par la direction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Les points qu'un collaborateur de ce rôle porte à 100 %. Un rôle absent n'a pas de
    #: capacité connue : sa charge n'est pas exprimée en pourcentage, et rien ne lui est proposé.
    capacite_par_role: dict[str, int] = Field(
        default_factory=lambda: {"COMPTABLE": 150, "CHARGE_CLIENTELE": 400}
    )
    points_par_dossier: int = Field(10, ge=0)
    points_par_piece_en_attente: int = Field(1, ge=0)
    points_par_echeance_du_mois: int = Field(3, ge=0)
    points_par_retard: int = Field(1, ge=0)
    #: Au-delà, le collaborateur est saturé, et des réaffectations sont cherchées.
    seuil_de_saturation: int = Field(90, ge=1, le=300)
    #: Aucune proposition ne porte un collègue au-delà de ce seuil ; et on cesse de soulager un
    #: collaborateur saturé dès qu'il y redescend.
    seuil_cible: int = Field(85, ge=1, le=300)
    source: str = "valeurs par défaut"

    @model_validator(mode="after")
    def _seuils_ordonnes(self) -> ReglagesDeLaCharge:
        if self.seuil_cible > self.seuil_de_saturation:
            raise ValueError(
                "seuil_cible dépasse seuil_de_saturation : une réaffectation pourrait saturer "
                "le collègue qui reçoit le dossier."
            )
        if any(c <= 0 for c in self.capacite_par_role.values()):
            raise ValueError("une capacité par rôle est strictement positive.")
        return self


class DossierPourLaCharge(BaseModel):
    """Ce que la route a relevé sur un dossier, pour le mois de la lecture."""

    model_config = ConfigDict(frozen=True)

    niu: str
    denomination: str
    pieces_en_attente: int = Field(0, ge=0)
    echeances_du_mois: int = Field(0, ge=0)
    retards: int = Field(0, ge=0)
    pieces_traitees_du_mois: int = Field(0, ge=0)
    ecritures_du_mois: int = Field(0, ge=0)
    reprises_du_mois: int = Field(0, ge=0)

    def points(self, reglages: ReglagesDeLaCharge) -> int:
        return (
            reglages.points_par_dossier
            + self.pieces_en_attente * reglages.points_par_piece_en_attente
            + self.echeances_du_mois * reglages.points_par_echeance_du_mois
            + self.retards * reglages.points_par_retard
        )


class PorteurDeDossiers(BaseModel):
    """Un collaborateur **à portée explicite**, et l'habilitation par laquelle il porte ses
    dossiers. Les habilitations transverses (direction, réviseur) ont accès à tout et ne
    « portent » rien : voir `_qui_suit_quoi`."""

    model_config = ConfigDict(frozen=True)

    compte: str
    nom: str
    role: str
    habilitation: str
    dossiers: tuple[str, ...]


class LigneDeCharge(BaseModel):
    model_config = ConfigDict(frozen=True)

    compte: str
    nom: str
    role: str
    habilitation: str
    capacite: int | None
    points: int
    #: En pour cent de la capacité ; `None` si le rôle n'a pas de capacité au référentiel.
    charge: int | None
    sature: bool
    dossiers: int
    pieces_en_attente: int
    echeances_du_mois: int
    retards: int
    pieces_traitees_du_mois: int
    ecritures_du_mois: int
    reprises_du_mois: int


class PropositionDeReaffectation(BaseModel):
    model_config = ConfigDict(frozen=True)

    dossier: str
    denomination: str
    points: int
    de_compte: str
    de_nom: str
    de_habilitation: str
    vers_compte: str
    vers_nom: str
    vers_habilitation: str
    charge_de_avant: int
    charge_de_apres: int
    charge_vers_avant: int
    charge_vers_apres: int
    raison: str


def _pourcent(points: int, capacite: int | None) -> int | None:
    return None if not capacite else round(points * 100 / capacite)


def mesurer_la_charge(
    porteurs: list[PorteurDeDossiers],
    dossiers: dict[str, DossierPourLaCharge],
    reglages: ReglagesDeLaCharge,
) -> list[LigneDeCharge]:
    """Une ligne par porteur, **du plus chargé au moins chargé**.

    ⚠️ Un dossier suivi par deux personnes compte entier chez les deux, comme au tableau de
    bord : les deux ont bien le dossier entier sur les bras.
    """
    lignes = []
    for porteur in porteurs:
        siens = [dossiers[n] for n in porteur.dossiers if n in dossiers]
        points = sum(d.points(reglages) for d in siens)
        capacite = reglages.capacite_par_role.get(porteur.role)
        charge = _pourcent(points, capacite)
        lignes.append(
            LigneDeCharge(
                compte=porteur.compte,
                nom=porteur.nom,
                role=porteur.role,
                habilitation=porteur.habilitation,
                capacite=capacite,
                points=points,
                charge=charge,
                sature=charge is not None and charge > reglages.seuil_de_saturation,
                dossiers=len(siens),
                pieces_en_attente=sum(d.pieces_en_attente for d in siens),
                echeances_du_mois=sum(d.echeances_du_mois for d in siens),
                retards=sum(d.retards for d in siens),
                pieces_traitees_du_mois=sum(d.pieces_traitees_du_mois for d in siens),
                ecritures_du_mois=sum(d.ecritures_du_mois for d in siens),
                reprises_du_mois=sum(d.reprises_du_mois for d in siens),
            )
        )
    return sorted(lignes, key=lambda l_: (-(l_.charge or 0), -l_.points, l_.nom))


def proposer_des_reaffectations(
    porteurs: list[PorteurDeDossiers],
    dossiers: dict[str, DossierPourLaCharge],
    reglages: ReglagesDeLaCharge,
) -> list[PropositionDeReaffectation]:
    """Pour chaque collaborateur saturé, les dossiers qu'un collègue du **même rôle** peut
    reprendre **sans dépasser le seuil cible**.

    L'algorithme est volontairement simple, pour qu'un directeur puisse le refaire à la main :

    1. les saturés, du plus chargé au moins chargé ;
    2. leurs dossiers, du plus lourd au plus léger (un gros dossier soulage plus) ;
    3. pour chacun, le collègue qui resterait **le moins chargé** après l'avoir reçu, pourvu
       qu'il reste sous le seuil cible et ne suive pas déjà ce dossier ;
    4. on s'arrête de soulager un collaborateur dès qu'il redescend sous le seuil cible.

    Les charges sont recalculées après chaque proposition : deux propositions ne peuvent pas
    toutes deux supposer la même marge chez le même collègue.
    """
    points = {porteur.compte: 0 for porteur in porteurs}
    portes = {porteur.compte: set(porteur.dossiers) for porteur in porteurs}
    for porteur in porteurs:
        points[porteur.compte] = sum(
            dossiers[n].points(reglages) for n in porteur.dossiers if n in dossiers
        )

    def charge(porteur: PorteurDeDossiers, delta: int = 0) -> int | None:
        return _pourcent(
            points[porteur.compte] + delta, reglages.capacite_par_role.get(porteur.role)
        )

    propositions: list[PropositionDeReaffectation] = []
    satures = sorted(
        (p for p in porteurs if (charge(p) or 0) > reglages.seuil_de_saturation),
        key=lambda p: -(charge(p) or 0),
    )
    for source in satures:
        candidats = sorted(
            (dossiers[n] for n in portes[source.compte] if n in dossiers),
            key=lambda d: (-d.points(reglages), d.niu),
        )
        for dossier in candidats:
            if (charge(source) or 0) <= reglages.seuil_cible:
                break
            poids = dossier.points(reglages)
            collegues = [
                p
                for p in porteurs
                if p.role == source.role
                # Ni qui suit déjà le dossier, ce qui écarte aussi le collaborateur qui le cède.
                and dossier.niu not in portes[p.compte]
                and (charge(p, poids) is not None and charge(p, poids) <= reglages.seuil_cible)
            ]
            if not collegues:
                continue
            cible = min(collegues, key=lambda p: (charge(p, poids), p.nom))
            de_avant, vers_avant = charge(source), charge(cible)
            points[source.compte] -= poids
            points[cible.compte] += poids
            portes[source.compte].discard(dossier.niu)
            portes[cible.compte].add(dossier.niu)
            propositions.append(
                PropositionDeReaffectation(
                    dossier=dossier.niu,
                    denomination=dossier.denomination,
                    points=poids,
                    de_compte=source.compte,
                    de_nom=source.nom,
                    de_habilitation=source.habilitation,
                    vers_compte=cible.compte,
                    vers_nom=cible.nom,
                    vers_habilitation=cible.habilitation,
                    charge_de_avant=de_avant or 0,
                    charge_de_apres=charge(source) or 0,
                    charge_vers_avant=vers_avant or 0,
                    charge_vers_apres=charge(cible) or 0,
                    raison=(
                        f"{source.nom} est à {de_avant} % ; {dossier.denomination} "
                        f"({poids} points) peut passer à {cible.nom}, qui irait à "
                        f"{charge(cible)} % sans dépasser {reglages.seuil_cible} %."
                    ),
                )
            )
    return propositions
