"""Le stockage des fichiers déposés, sur le disque de la machine.

─────────────────────────────────────────────────────────────────────────────────
LE FICHIER EST ADRESSÉ PAR SON CONTENU

La clé d'un document **est** son empreinte SHA-256. Ce n'est pas une astuce : le
domaine l'affirme déjà — « deux fichiers de même empreinte sont le même fichier,
ce n'est pas une présomption, c'est une identité » (`domaine/pieces.py`).

Trois propriétés en découlent, gratuitement :

* **la dédoublonnage est acquise.** Un adhérent qui renvoie trois fois la même
  facture — cas quotidien, pas cas limite — n'occupe la place qu'une fois ;
* **le dépôt est idempotent.** Rejouer une réception interrompue ne corrompt
  rien : la clé désigne le même contenu, ou elle ne le désigne pas ;
* **l'altération se détecte.** Relire et recalculer l'empreinte dit si l'octet
  a bougé. Sur des justificatifs qu'un contrôle fiscal peut réclamer six ans
  plus tard, ce n'est pas un luxe.

⚠️ La contrepartie est à connaître : **on ne supprime pas** un fichier partagé
par plusieurs pièces sans compter ses références. Rien ici ne supprime, et c'est
délibéré — voir plus bas.

POURQUOI UN ARBRE À DEUX NIVEAUX

`ab/cd/abcdef…` plutôt que `abcdef…` en vrac. Un dossier plat de cent mille
fichiers reste correct pour le système de fichiers mais devient impraticable
pour un humain : `ls` ne rend plus la main, une sauvegarde incrémentale relit
tout le répertoire, et un `rsync` prend des heures. Deux niveaux de deux
caractères hexadécimaux donnent 65 536 dossiers, soit quelques fichiers chacun
même à l'échelle de plusieurs exercices.

CE QUE CE MODULE REFUSE, ET POURQUOI C'EST LE CŒUR DU FICHIER

**Une clé qui n'est pas une empreinte hexadécimale est rejetée.** C'est la seule
défense contre la traversée de chemin, et elle doit être une **liste blanche**.
Une clé venue d'une requête, concaténée sans contrôle, permet `../../etc/passwd`
en lecture et l'écrasement de n'importe quel fichier du serveur en écriture. Les
protections par liste noire — « refuser `..` » — se contournent toutes, par
encodage, par lien symbolique, par séparateur exotique. Ici, tout ce qui n'est
pas 64 caractères de `[0-9a-f]` n'existe pas.

**Le locataire préfixe le chemin.** Deux cabinets ne partagent pas de dossier,
et une erreur de requête ne peut donc pas rendre le justificatif d'un autre. Le
locataire est validé de la même manière — liste blanche.

L'ÉCRITURE EST ATOMIQUE, ET IL LE FAUT

Écriture dans un fichier temporaire, puis `os.replace`. Sans cela, une coupure
au milieu d'un dépôt laisse un fichier **partiel** à une clé valide : il se
relit sans erreur, s'affiche comme un PDF tronqué, et son empreinte ne
correspond plus à son nom — anomalie que personne ne cherche puisque rien n'a
échoué. `os.replace` est atomique sur un même système de fichiers : le fichier
existe entier, ou n'existe pas.

⚠️ CE QUE CE MAGASIN NE FAIT PAS

* **Il ne supprime rien.** Un justificatif se conserve dix ans (art. 18 du Code
  général des impôts camerounais, à confirmer — voir le référentiel). Une purge
  demanderait un comptage de références et une décision de conservation ; les
  deux relèvent du métier, pas de l'adaptateur.
* **Il ne réplique pas.** Un seul disque, une seule machine. La sauvegarde est
  affaire d'exploitation. ⚠️ Sans sauvegarde de ce dossier, une panne disque
  fait perdre les justificatifs de tous les adhérents, et la comptabilité qui
  en dépend devient indéfendable devant un vérificateur.
* **Il ne chiffre pas au repos.** Le chiffrement du volume est du ressort de
  l'hébergeur, et le faire ici empêcherait la dédoublonnage — deux scellés du
  même fichier diffèrent par leur nonce.

La cible reste un stockage objet compatible S3, où ces trois manques sont
résolus par le fournisseur. Le port ne changera pas ; seule cette classe sera
remplacée.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

__all__ = [
    "CleInvalide",
    "FichierAbsent",
    "MagasinFichiersLocal",
    "TYPES_ACCEPTES",
    "detecter_le_type",
]

#: Une empreinte SHA-256 en hexadécimal minuscule, et rien d'autre.
_CLE_VALIDE = re.compile(r"^[0-9a-f]{64}$")

#: Un identifiant de locataire : lettres, chiffres et tiret. Sert de nom de
#: dossier, donc soumis au même contrôle que la clé.
_LOCATAIRE_VALIDE = re.compile(r"^[A-Za-z0-9-]{1,64}$")

#: Le fichier voisin qui porte le type déclaré. Le magasin reste ainsi
#: autosuffisant : un dossier recopié sur une autre machine se relit sans la
#: base, ce qui compte le jour où c'est la base qu'on a perdue.
_SUFFIXE_TYPE = ".type"


class CleInvalide(ValueError):
    """La clé n'est pas une empreinte. Voir l'en-tête — traversée de chemin."""


class FichierAbsent(KeyError):
    """Aucun fichier à cette clé."""


#: Les types acceptés au dépôt, et leur signature en tête de fichier.
#:
#: ⚠️ **Le type déclaré par le client n'est jamais cru.** Un navigateur envoie
#: le `Content-Type` qu'on lui dit d'envoyer ; un automate envoie ce qu'il veut.
#: Accepter un document HTML étiqueté `image/jpeg`, puis le rendre plus tard
#: avec cette étiquette, laisse s'exécuter du script dans le domaine du cabinet
#: — sur la page où un comptable est connecté.
#:
#: La liste est courte à dessein : c'est ce qu'un adhérent photographie ou
#: numérise. Tout le reste — archives, documents bureautiques, exécutables —
#: n'a rien à faire dans une boîte de réception de justificatifs.
TYPES_ACCEPTES: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    # TIFF, produit par la plupart des scanneurs de bureau. Deux ordres
    # d'octets, selon le constructeur.
    "image/tiff": (b"II*\x00", b"MM\x00*"),
}


def detecter_le_type(contenu: bytes) -> str | None:
    """Le type réel du fichier, lu dans ses premiers octets. `None` si inconnu.

    Une signature ne prouve pas qu'un fichier est sain — un PDF peut être
    malveillant tout en commençant par `%PDF-`. Elle prouve seulement que ce
    n'est pas *autre chose déguisé*, et c'est précisément ce qui permet de le
    rendre plus tard sans risque d'exécution.
    """
    for type_mime, signatures in TYPES_ACCEPTES.items():
        if any(contenu.startswith(signature) for signature in signatures):
            return type_mime
    return None


class MagasinFichiersLocal:
    """Réalisation de `MagasinFichiers` sur le système de fichiers."""

    def __init__(self, racine: Path, locataire: str) -> None:
        if not _LOCATAIRE_VALIDE.match(locataire):
            raise CleInvalide(
                f"identifiant de locataire refusé : {locataire!r}. "
                "Il sert de nom de dossier."
            )
        self._racine = Path(racine).resolve()
        self._locataire = locataire

    # ── Le port ─────────────────────────────────────────────────────────────

    def deposer(self, cle: str, contenu: bytes, *, type_mime: str) -> None:
        """Écrit le fichier, ou ne fait rien s'il est déjà là.

        Le silence sur un fichier existant est correct **parce que la clé est le
        contenu** : réécrire produirait octet pour octet la même chose. Sur un
        magasin adressé autrement, ce serait un écrasement silencieux.
        """
        chemin = self._chemin(cle)
        if chemin.exists():
            return

        chemin.parent.mkdir(parents=True, exist_ok=True)
        # Le temporaire est créé dans le **dossier de destination** : `os.replace`
        # n'est atomique qu'à l'intérieur d'un même système de fichiers, et
        # `/tmp` en est souvent un autre.
        descripteur, provisoire = tempfile.mkstemp(dir=chemin.parent, suffix=".partiel")
        try:
            with os.fdopen(descripteur, "wb") as fichier:
                fichier.write(contenu)
                fichier.flush()
                # ⚠️ Sans `fsync`, `os.replace` peut publier un nom dont le
                # contenu n'a pas encore atteint le disque. Une coupure de
                # courant laisse alors un fichier vide portant une clé valide.
                os.fsync(fichier.fileno())
            os.replace(provisoire, chemin)
        except BaseException:
            Path(provisoire).unlink(missing_ok=True)
            raise

        chemin.with_name(chemin.name + _SUFFIXE_TYPE).write_text(
            type_mime, encoding="ascii"
        )

    def lire(self, cle: str) -> bytes:
        chemin = self._chemin(cle)
        try:
            return chemin.read_bytes()
        except FileNotFoundError as absent:
            raise FichierAbsent(f"aucun fichier à la clé {cle}") from absent

    def existe(self, cle: str) -> bool:
        return self._chemin(cle).is_file()

    # ── Au-delà du port ─────────────────────────────────────────────────────

    def type_mime(self, cle: str) -> str:
        """Le type enregistré au dépôt.

        `application/octet-stream` en dernier recours : un type inconnu doit
        faire télécharger le fichier, jamais l'interpréter.
        """
        chemin = self._chemin(cle)
        # `with_name` et non `with_suffix` : une clé est un nom sans extension,
        # et `with_suffix` remplacerait ce qu'il prendrait pour une extension.
        voisin = chemin.with_name(chemin.name + _SUFFIXE_TYPE)
        try:
            return voisin.read_text(encoding="ascii").strip()
        except OSError:
            return "application/octet-stream"

    def taille(self, cle: str) -> int:
        try:
            return self._chemin(cle).stat().st_size
        except FileNotFoundError as absent:
            raise FichierAbsent(f"aucun fichier à la clé {cle}") from absent

    # ── Interne ─────────────────────────────────────────────────────────────

    def _chemin(self, cle: str) -> Path:
        """Le chemin d'une clé, après validation. Voir l'en-tête.

        ⚠️ La validation est **ici**, au seul endroit qui compose un chemin.
        La placer chez les appelants garantirait qu'un jour l'un d'eux l'oublie.
        """
        if not _CLE_VALIDE.match(cle):
            raise CleInvalide(
                f"clé de fichier refusée : {cle!r}. "
                "Une clé est une empreinte SHA-256 en hexadécimal minuscule."
            )
        return self._racine / self._locataire / cle[:2] / cle[2:4] / cle
