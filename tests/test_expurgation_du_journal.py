"""Ce qui n'entre jamais au journal d'audit.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Le module d'audit porte cette phrase depuis le premier jour :

    `_EXPURGES` retire ces clés à l'écriture — **un garde-fou, parce que compter
    sur la vigilance de l'appelant est ce qui finit toujours par échouer.**

Le garde-fou n'était nommé par **aucun test**. Éprouvé à la main, il laissait
passer un secret imbriqué dans une liste : `{"comptes": [{"mot_de_passe": "…"}]}`
traversait le masque et entrait en clair dans un journal lu par tous les porteurs
de `LIRE_AUDIT` et conservé dix ans.

⚠️ Le défaut était **latent** : les cinq appelants qui passent aujourd'hui une
liste au journal passent des listes de chaînes. Il l'était exactement autant que
la vigilance sur laquelle ce garde-fou existe pour ne pas compter.

*Un garde-fou qui ne couvre qu'une forme de donnée reporte l'échec, il ne l'évite
pas. Et un garde-fou sans test est une intention.*
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from app.contextes.transverse.domaine.audit import EntreeAudit, expurger

T0 = datetime(2026, 9, 11, 9, 0)

#: Les clés que le module dit retirer. La liste est **recopiée ici à dessein** :
#: si quelqu'un en retire une du domaine, ce fichier doit tomber plutôt que de
#: suivre en silence. C'est le seul endroit du projet où une liste recopiée est
#: préférable à une liste lue, parce que c'est elle qu'on garde.
SENSIBLES = (
    "mot_de_passe",
    "mots_de_passe",
    "motdepasse",
    "password",
    "passwords",
    "empreinte_mot_de_passe",
    "empreintes_mot_de_passe",
    "secret",
    "secrets",
    "jeton",
    "jetons",
    "token",
    "tokens",
    "empreinte",
    "empreintes",
)

SECRET = "Tr3sSecret-ne-doit-jamais-paraitre"


def _entree(apres: dict) -> EntreeAudit:
    """Une entrée réelle, telle que le journal en fabrique.

    ⚠️ Le test passe par `EntreeAudit` et non par `expurger` seul : c'est la
    construction de l'entrée qui doit masquer, et vérifier la fonction sans son
    appelant laisserait passer le jour où l'appel disparaît.
    """
    return EntreeAudit.poser(
        precedente=None,
        horodatage=T0,
        acteur="essai",
        locataire="CGA-BRCG",
        action="essai.enregistrement",
        objet_type="essai",
        objet_id="1",
        apres=apres,
    )


class TestChaqueCleSensibleEstMasquee:
    @pytest.mark.parametrize("cle", SENSIBLES)
    def test_a_la_racine(self, cle):
        entree = _entree({cle: SECRET})
        assert SECRET not in json.dumps(entree.apres)

    @pytest.mark.parametrize("cle", SENSIBLES)
    def test_dans_un_dictionnaire_imbrique(self, cle):
        entree = _entree({"compte": {"courriel": "a@b.cm", cle: SECRET}})
        assert SECRET not in json.dumps(entree.apres)

    @pytest.mark.parametrize("cle", SENSIBLES)
    def test_dans_une_liste(self, cle):
        """⚠️ **Le défaut trouvé.** La récursion descendait dans les
        dictionnaires et pas dans les listes."""
        entree = _entree({"comptes": [{"identifiant": "c1", cle: SECRET}]})
        assert SECRET not in json.dumps(entree.apres)

    @pytest.mark.parametrize("cle", SENSIBLES)
    def test_dans_une_liste_de_listes(self, cle):
        entree = _entree({"lots": [[{cle: SECRET}]]})
        assert SECRET not in json.dumps(entree.apres)


class TestLesFormesQuiTrompent:
    def test_une_cle_sensible_portant_un_objet_est_masquee_en_entier(self):
        """⚠️ L'ordre du masquage compte.

        Descendre d'abord laisserait le secret sous une clé que `_EXPURGES` ne
        connaît pas : `{"jeton": {"valeur": "…"}}` deviendrait
        `{"jeton": {"valeur": "…"}}`, inchangé.
        """
        entree = _entree({"jeton": {"valeur": SECRET, "expire_le": "2026-12-31"}})
        assert SECRET not in json.dumps(entree.apres)
        assert entree.apres["jeton"] == "«expurgé»"

    def test_une_cle_sensible_portant_une_liste_est_masquee_en_entier(self):
        """⚠️ **Le pluriel aussi.**

        La comparaison est exacte, et `jetons` n'est pas `jeton` : une clé au
        pluriel portant une liste de secrets fuyait entièrement. Le cas est
        d'autant plus probable que c'est précisément sous une clé au pluriel
        qu'on range plusieurs secrets.
        """
        entree = _entree({"jetons": [SECRET, SECRET]})
        assert entree.apres["jetons"] == "«expurgé»"

    def test_les_tirets_et_les_espaces_sont_normalises(self):
        """Une charge utile venue d'un prestataire n'écrit pas ses clés comme
        nous."""
        for ecriture in ("mot-de-passe", "Mot De Passe", "MOT_DE_PASSE"):
            entree = _entree({ecriture: SECRET})
            assert SECRET not in json.dumps(entree.apres), ecriture

    def test_le_masquage_ne_se_fait_jamais_sur_la_valeur(self):
        """⚠️ On ne masque pas ce qui *ressemble* à un secret.

        Deviner produirait des faux positifs illisibles — une référence de
        paiement masquée parce qu'elle ressemble à un jeton — et des faux négatifs
        rassurants. Ce cas garde la décision, qui est explicite.
        """
        entree = _entree({"reference_externe": "eyJhbGciOiJIUzI1NiJ9.abcdef"})
        assert entree.apres["reference_externe"] == "eyJhbGciOiJIUzI1NiJ9.abcdef"


class TestCeQuiDoitPasser:
    """⚠️ La contre-épreuve. Sans elle, un masque qui expurgerait **tout**
    passerait chacun des cas précédents, et le journal d'audit ne dirait plus
    rien."""

    def test_les_donnees_ordinaires_traversent_intactes(self):
        ordinaire = {
            "courriel": "gerant@boulangerie-wouri.cm",
            "portee": ["dossier-1", "dossier-2"],
            "montant": "250000",
            "compte": {"identifiant": "C-001", "role": "DIRECTION"},
        }
        entree = _entree(ordinaire)
        assert entree.apres == ordinaire

    def test_une_liste_de_chaines_reste_une_liste_de_chaines(self):
        """Les cinq appelants réels du projet passent exactement cela."""
        entree = _entree({"cles": ["productId", "status", "amount"]})
        assert entree.apres["cles"] == ["productId", "status", "amount"]

    def test_l_absence_de_donnees_reste_une_absence(self):
        """`None` n'est pas un dictionnaire vide : la distinction est portée par
        l'entrée, et l'effacer ferait croire à un état connu et vide."""
        assert expurger(None) is None


class TestLaChaineResteVerifiable:
    def test_le_masquage_est_dans_l_empreinte(self):
        """⚠️ Le masque est appliqué **avant** le calcul de l'empreinte.

        L'appliquer après ferait qu'une entrée relue ne se vérifierait plus : son
        empreinte porterait un contenu que la base ne contient pas.
        """
        from app.contextes.transverse.domaine.audit import verifier_chaine

        entree = _entree({"mot_de_passe": SECRET, "acteur": "essai"})
        verifier_chaine([entree])
        assert entree.apres["mot_de_passe"] == "«expurgé»"
