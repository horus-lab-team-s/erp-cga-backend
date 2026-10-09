"""Le magasin de fichiers de la collecte : désormais celui du partage.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER N'EST PLUS QU'UNE RÉEXPORTATION

Le magasin a été écrit ici parce que la collecte fut le premier contexte à
recevoir des fichiers. Rien dedans ne relève pourtant du métier de la collecte :
l'adressage par contenu, la liste blanche sur la clé, le préfixe par locataire
et l'écriture atomique valent pour n'importe quel octet qu'on range.

La Souscription doit maintenant ranger le PDF d'une proforma. Or le graphe des
contextes ne lui donne que le Portefeuille (`app/registre/services.py`), et
c'est **juste** : ranger un fichier n'est pas une dépendance métier, et ajouter
une arête « souscription lit collecte » pour un besoin de stockage aurait payé
une question d'infrastructure au prix d'une cloison.

Le code a donc rejoint `app/partage/magasin_de_fichiers.py`, comme le sceau
avant lui. Ce fichier reste pour les appelants existants : le déplacer d'un coup
aurait touché une trentaine d'imports sans rien améliorer.

⚠️ **Le nouveau code importe du partage**, pas d'ici.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from app.partage.magasin_de_fichiers import (
    TYPES_ACCEPTES,
    CleInvalide,
    FichierAbsent,
    MagasinFichiersLocal,
    detecter_le_type,
)

__all__ = [
    "CleInvalide",
    "FichierAbsent",
    "MagasinFichiersLocal",
    "TYPES_ACCEPTES",
    "detecter_le_type",
]
