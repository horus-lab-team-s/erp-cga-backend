"""Réalisation en mémoire du port `DepotEcritures`.

─────────────────────────────────────────────────────────────────────────────────
LES TROIS GARANTIES DU PORT, ET CE QUE CELLE-CI TIENT VRAIMENT

Le port exige trois choses. Deux sont tenues ici, la troisième ne peut pas l'être,
et il vaut mieux le dire que le laisser croire.

1. **Aucune suppression.** La méthode n'existe pas. Tenue.
2. **Aucune modification d'une écriture validée.** `enregistrer` refuse d'écraser
   une écriture déjà validée, par une exception. Tenue.
3. **Une numérotation continue, même en accès concurrent.** *Non tenue.* Ce dépôt
   calcule le prochain numéro en lisant le maximum existant : deux appels
   simultanés rendraient le même. Seule une séquence de base de données — ou une
   contrainte d'unicité sur `(exercice, journal, numero)` — le garantira.

Le point 3 n'est pas un détail de performance. Un doublon de numérotation est
aussi grave qu'un trou : la continuité de la séquence est ce qui rend une piste
d'audit défendable, et c'est la première chose qu'un vérificateur contrôle.

UN DÉPÔT PAR DOSSIER, ET CE QUE CELA RÉVÈLE

`EcritureComptable` ne porte pas d'identifiant d'entreprise, et le port
`DepotEcritures` n'en prend pas non plus : `lister(exercice, journal)` suffit. La
conséquence est nette et mérite d'être dite — **la clé `2026/AC/000042` n'est
unique qu'à l'intérieur d'un dossier.** Deux adhérents ont chacun leur journal des
achats, et chacun sa quarante-deuxième écriture.

Ce dépôt est donc ouvert **pour un dossier**, et la séparation est portée par
l'instance. Le jour où les écritures partageront une table, il faudra soit une
colonne `entreprise` dans la clé primaire, soit un `entreprise` dans l'entité et
dans `cle`. La question est ouverte, et elle se pose maintenant plutôt qu'après la
migration.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from app.contextes.comptabilite.domaine.entites import EcritureComptable, EtatEcriture
from app.partage.depot_memoire import EntrepotMemoire

__all__ = ["DepotEcrituresMemoire", "EcritureFigee", "EcritureIntrouvable"]


class EcritureIntrouvable(LookupError):
    """Une clé désignée et absente du registre.

    Le port l'exige : jamais `None`. Une écriture désignée par sa clé et
    introuvable est une rupture de traçabilité, pas un cas limite — et rendre
    `None` obligerait chaque appelant à s'en souvenir.
    """


class EcritureFigee(ValueError):
    """On a tenté de réécrire une écriture validée.

    Une écriture validée ne se modifie pas : elle se contre-passe par une écriture
    nouvelle, qui laisse deux traces au lieu d'en effacer une.
    """


class DepotEcrituresMemoire:
    """Le registre des écritures d'**un dossier**, en mémoire."""

    def __init__(self, entreprise: str, locataire: str = "CGA-BRCG") -> None:
        self.entreprise = entreprise
        self._entrepot: EntrepotMemoire[EcritureComptable] = EntrepotMemoire(
            locataire, cle=lambda ecriture: ecriture.cle
        )

    @classmethod
    def avec_demonstration(
        cls, entreprise: str, locataire: str = "CGA-BRCG"
    ) -> DepotEcrituresMemoire:
        from app.contextes.comptabilite.adaptateurs.sortant.donnees_demo import (
            ecritures_demo,
        )

        depot = cls(entreprise, locataire)
        depot._entrepot.poser_tout(ecritures_demo(entreprise))
        return depot

    # ── Port `DepotEcritures` ───────────────────────────────────────────────

    def enregistrer(self, ecriture: EcritureComptable) -> None:
        ancienne = self._entrepot.prendre(ecriture.cle)
        if ancienne is not None and ancienne.etat is EtatEcriture.VALIDEE:
            raise EcritureFigee(
                f"{ecriture.cle} : cette écriture est validée et ne se modifie plus. "
                "La corriger se fait par contre-passation, qui laisse sa propre trace "
                "et conserve l'originale."
            )
        self._entrepot.poser(ecriture)

    def lire(self, cle: str) -> EcritureComptable:
        ecriture = self._entrepot.prendre(cle)
        if ecriture is None:
            raise EcritureIntrouvable(
                f"écriture « {cle} » absente du registre. Une écriture désignée par sa "
                "clé et introuvable est une rupture de la piste d'audit."
            )
        return ecriture

    def lister(self, exercice: str, journal: str | None = None) -> list[EcritureComptable]:
        return sorted(
            self._entrepot.filtrer(
                lambda e: e.exercice == exercice and (journal is None or e.journal == journal)
            ),
            key=lambda e: (e.journal, e.numero),
        )

    def prochain_numero(self, exercice: str, journal: str) -> int:
        """Le maximum existant, plus un.

        ⚠️ Non protégé d'un accès concurrent — voir l'en-tête du module.
        """
        numeros = [
            e.numero
            for e in self._entrepot
            if e.exercice == exercice and e.journal == journal
        ]
        return max(numeros, default=0) + 1

    # ── Commodités pour les adaptateurs entrants ────────────────────────────

    def toutes(self, exercice: str | None = None) -> list[EcritureComptable]:
        return sorted(
            self._entrepot.filtrer(lambda e: exercice is None or e.exercice == exercice),
            key=lambda e: (e.exercice, e.journal, e.numero),
        )

    def exercices(self) -> list[str]:
        return sorted({e.exercice for e in self._entrepot})
