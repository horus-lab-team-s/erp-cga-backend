"""L'échange d'écritures avec le logiciel du client.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Un centre de gestion ne remplace pas le logiciel de ses adhérents : il s'y branche.
La plateforme doit s'adapter au système final, jamais l'inverse.

⚠️ **Le format est donc une donnée du référentiel, pas du code.** Ces cas gardent
d'abord cela : le moteur ne connaît aucun logiciel, et brancher un progiciel de
plus doit être un fichier YAML, pas un déploiement.

Ils gardent ensuite les trois pièges qui coûtent un fichier entier, et dont deux ne
se voient qu'après l'import chez le client : l'encodage, le séparateur dans un
libellé, et le séparateur de milliers.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.contextes.comptabilite.adaptateurs.sortant.profils_echange import (
    ProfilInconnu,
    charger_les_profils,
    charger_un_profil,
)
from app.contextes.comptabilite.domaine.echange import (
    CHAMPS_DISPONIBLES,
    Colonne,
    EchangeRefuse,
    FormeDuSens,
    ProfilDEchange,
    rendre,
)
from app.contextes.comptabilite.domaine.entites import (
    EcritureComptable,
    EtatEcriture,
    LigneEcriture,
    Sens,
)
from app.infrastructure.config import RACINE_DEPOT

REFERENTIEL = RACINE_DEPOT / "Docs" / "referentiel" / "echange"
LE_JOUR = date(2026, 7, 2)


def _ecriture(*, etat=EtatEcriture.VALIDEE, libelle="Achat de semences", **surcharges):
    defauts = {
        "journal": "AC",
        "exercice": "2026",
        "numero": 1,
        "date_operation": LE_JOUR,
        "libelle": libelle,
        "piece_justificative": "PJ-2026-0007",
        "reference_externe": "F-2026-0418",
        "lignes": [
            LigneEcriture(
                compte="602", libelle="Semences", sens=Sens.DEBIT,
                montant=Decimal("2840000"),
            ),
            LigneEcriture(
                compte="401", libelle="Fournisseur", sens=Sens.CREDIT,
                montant=Decimal("2840000"), tiers="M034455667788W",
            ),
        ],
        "etat": etat,
        # ⚠️ Une écriture validée porte **qui** l'a validée et **quand** : le
        # domaine le refuse autrement. « Le Centre engage sa responsabilité sur
        # ce qu'il présente : la validation est un acte personnel. »
        "validee_par": "a.bouba" if etat is EtatEcriture.VALIDEE else None,
        "validee_le": datetime(2026, 7, 3, 9, 0) if etat is EtatEcriture.VALIDEE else None,
    }
    return EcritureComptable(**{**defauts, **surcharges})


def _profil(**surcharges) -> ProfilDEchange:
    defauts = {
        "code": "essai",
        "libelle": "Profil d'essai",
        "colonnes": (Colonne(champ="compte"), Colonne(champ="montant")),
    }
    return ProfilDEchange(**{**defauts, **surcharges})


class TestLesProfilsDuReferentiel:
    """⚠️ Les profils **réels** du centre, et non des profils forgés.

    Un profil inventé éprouverait le moteur sans rien dire de ce que la plateforme
    livrera au client.
    """

    def test_ils_se_chargent_tous(self):
        profils = charger_les_profils(REFERENTIEL)
        assert set(profils) == {"pivot-csv", "sage-ligne100"}

    def test_le_profil_de_progiciel_porte_les_trois_choix_qui_comptent(self):
        """⚠️ Encodage, fin de ligne et séparateur décimal.

        Les trois abîment un fichier entier, et deux ne se voient qu'après
        l'import chez le client. Ce cas les verrouille : les changer doit être un
        geste conscient, pas une retouche.
        """
        sage = charger_un_profil(REFERENTIEL, "sage-ligne100")
        assert sage.encodage == "cp1252"
        assert sage.fin_de_ligne == "\r\n"
        assert sage.decimale == ","

    def test_le_pivot_ne_vise_aucun_logiciel(self):
        """Il est destiné à un humain et à un tableur moderne.

        ⚠️ Un pivot qui adopterait les contraintes d'un progiciel cesserait d'être
        neutre, et la reprise d'un dossier hériterait des défauts de Sage.
        """
        pivot = charger_un_profil(REFERENTIEL, "pivot-csv")
        assert pivot.encodage == "utf-8"
        assert pivot.fin_de_ligne == "\n"
        assert pivot.forme_du_sens is FormeDuSens.COLONNE_UNIQUE

    def test_un_code_inconnu_nomme_ceux_qui_existent(self):
        with pytest.raises(ProfilInconnu, match="pivot-csv"):
            charger_un_profil(REFERENTIEL, "cegid")

    def test_un_champ_hors_du_pivot_est_refuse(self, tmp_path):
        """⚠️ Refusé, jamais ignoré.

        Une colonne silencieusement vide ferait importer au client un fichier
        amputé qu'il croirait complet — et l'écart ne se verrait qu'à la balance.
        """
        (tmp_path / "faux.yaml").write_text(
            "code: faux\nlibelle: Faux\ncolonnes:\n  - champ: chiffre_affaires\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="chiffre_affaires"):
            charger_les_profils(tmp_path)

    def test_un_encodage_inconnu_est_refuse_au_chargement(self, tmp_path):
        """⚠️ Au chargement, pas à l'export.

        Découvert au moment où un client attend son fichier, c'est un incident ;
        découvert au démarrage, c'est une faute de frappe.
        """
        (tmp_path / "faux.yaml").write_text(
            "code: faux\nlibelle: Faux\nencodage: cp9999\ncolonnes:\n  - champ: compte\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="cp9999"):
            charger_les_profils(tmp_path)


class TestCeQuiNeSortPas:
    def test_un_brouillon_fait_refuser_le_lot_entier(self):
        """⚠️ **Refusé en entier, et nominativement.**

        Un brouillon est une écriture que le centre n'a pas engagée. L'exporter la
        ferait tenir pour arrêtée par le client, qui déclare avec — puis découvre
        au contrôle que le centre l'a corrigée depuis.

        Filtrer en silence serait pire : le fichier serait incomplet, et l'écart
        ne se verrait qu'à la balance.
        """
        lot = [_ecriture(), _ecriture(numero=2, etat=EtatEcriture.BROUILLON)]
        with pytest.raises(EchangeRefuse, match="AC/2"):
            rendre(lot, _profil())

    def test_un_lot_valide_sort_entierement(self):
        """⚠️ La contre-épreuve : sans elle, un export qui refuserait tout
        passerait le cas précédent."""
        rendu = rendre([_ecriture(), _ecriture(numero=2)], _profil(entete=False))
        assert len(rendu.strip().splitlines()) == 4


class TestLesTroisPieges:
    def test_le_separateur_dans_un_libelle_ne_decale_rien(self):
        """⚠️ **On substitue, on n'entoure pas de guillemets.**

        Entourer serait la réponse d'un lecteur CSV moderne. Les importeurs
        hérités prennent le guillemet pour un caractère du libellé : le client
        verrait `"Quincaillerie"` avec ses guillemets dans son grand livre, sur
        chaque ligne, pour toujours.
        """
        rendu = rendre(
            [_ecriture(libelle="Achat ; urgent")],
            _profil(
                entete=False,
                colonnes=(Colonne(champ="libelle_ecriture"), Colonne(champ="compte")),
            ),
        )
        for ligne in rendu.strip().splitlines():
            assert ligne.count(";") == 1, ligne
            assert '"' not in ligne

    def test_un_retour_a_la_ligne_dans_un_libelle_ne_coupe_pas_l_ecriture(self):
        """Un libellé multiligne couperait l'écriture en deux, et la seconde
        moitié serait rejetée comme une ligne incomplète."""
        rendu = rendre(
            [_ecriture(libelle="Achat\nurgent")],
            _profil(entete=False, colonnes=(Colonne(champ="libelle_ecriture"),)),
        )
        assert len(rendu.strip().splitlines()) == 2

    def test_aucun_montant_ne_porte_de_separateur_de_milliers(self):
        """⚠️ `2 840 000,00` est lu comme `2` par un importeur qui coupe au
        premier caractère non numérique — **et il ne le dit pas**."""
        rendu = rendre([_ecriture()], _profil(entete=False))
        assert "2840000,00" in rendu
        assert " " not in rendu.replace("\r", "").replace("\n", "")

    def test_le_fichier_se_termine_par_une_fin_de_ligne(self):
        """⚠️ Un fichier dont la dernière ligne n'est pas terminée est tronqué
        pour la moitié des importeurs, et la dernière écriture disparaît sans
        message."""
        assert rendre([_ecriture()], _profil(fin_de_ligne="\r\n")).endswith("\r\n")


class TestLeRenduSuitLeProfil:
    def test_les_colonnes_separees_laissent_l_autre_vide(self):
        rendu = rendre(
            [_ecriture()],
            _profil(
                entete=False,
                colonnes=(Colonne(champ="debit"), Colonne(champ="credit")),
            ),
        )
        debit, credit = rendu.strip().splitlines()
        assert debit == "2840000,00;"
        assert credit == ";2840000,00"

    def test_la_colonne_unique_porte_un_marqueur(self):
        rendu = rendre(
            [_ecriture()],
            _profil(
                entete=False,
                forme_du_sens=FormeDuSens.COLONNE_UNIQUE,
                colonnes=(Colonne(champ="sens"), Colonne(champ="montant")),
            ),
        )
        assert rendu.strip().splitlines() == ["D;2840000,00", "C;2840000,00"]

    def test_un_compte_se_complete_a_gauche(self):
        """⚠️ Livrer « 401 » là où le plan attend « 40100000 » produit un import
        **accepté** et des comptes inconnus, ce qui est pire qu'un refus."""
        rendu = rendre(
            [_ecriture()],
            _profil(entete=False, colonnes=(Colonne(champ="compte", largeur=8),)),
        )
        assert rendu.strip().splitlines() == ["00000602", "00000401"]

    def test_la_decimale_et_la_date_suivent_le_profil(self):
        rendu = rendre(
            [_ecriture()],
            _profil(
                entete=False,
                decimale=".",
                format_date="%Y-%m-%d",
                colonnes=(Colonne(champ="date_operation"), Colonne(champ="montant")),
            ),
        )
        assert rendu.strip().splitlines()[0] == "2026-07-02;2840000.00"

    def test_l_entete_emploie_l_intitule_ou_le_champ(self):
        rendu = rendre(
            [_ecriture()],
            _profil(
                colonnes=(
                    Colonne(champ="compte", intitule="Compte"),
                    Colonne(champ="montant"),
                )
            ),
        )
        assert rendu.splitlines()[0] == "Compte;montant"


class TestLeVocabulaireEstClos:
    def test_chaque_champ_disponible_se_rend(self):
        """⚠️ Le cas qui empêche un champ déclaré et non implémenté.

        Un champ présent dans `CHAMPS_DISPONIBLES` mais absent du rendu passerait
        le chargement — donc serait proposé à un client — et lèverait à l'export,
        au moment où il attend son fichier.
        """
        colonnes = tuple(Colonne(champ=c) for c in sorted(CHAMPS_DISPONIBLES))
        rendu = rendre([_ecriture()], _profil(entete=False, colonnes=colonnes))
        assert len(rendu.strip().splitlines()) == 2


# ── L'export par l'API ───────────────────────────────────────────────────────


class TestLExportParLApi:
    """⚠️ **Le fichier est rendu en octets, pas en texte.**

    L'encodage fait partie du format : le faire passer par du JSON le convertirait
    en UTF-8, et le client recevrait des libellés abîmés après avoir cru
    télécharger le bon fichier. C'est le piège le plus coûteux de cet échange,
    parce qu'il ne se voit qu'après l'import.
    """

    COMPTABLE = "a.bouba@cga-brcg.cm"

    @pytest.fixture
    def comptable(self):
        from fastapi.testclient import TestClient

        from app.contextes.transverse.api import MOT_DE_PASSE_DEMO
        from app.main import app

        with TestClient(app) as client:
            reponse = client.post(
                "/transverse/session",
                json={"courriel": self.COMPTABLE, "mot_de_passe": MOT_DE_PASSE_DEMO},
            )
            assert reponse.status_code == 200, reponse.text
            yield client

    #: ⚠️ **Un journal que ces cas possèdent.**
    #:
    #: Ils exportaient le journal des achats, partagé avec tous les autres
    #: fichiers de test. L'un d'eux y laisse un brouillon, et l'export refusait le
    #: lot — **le garde-fou fonctionnait**, mes cas dépendaient d'un état qui ne
    #: leur appartenait pas.
    #:
    #: Les opérations diverses ne servent à personne d'autre, et la fixture y pose
    #: sa propre écriture, validée. Au passage, ces cas éprouvent la chaîne
    #: réelle — saisir, valider, exporter — au lieu de s'appuyer sur un décor.
    JOURNAL = "OD"

    @pytest.fixture
    def dossier(self, comptable):
        """Un dossier, et **une écriture validée à nous** dans un journal à nous."""
        entreprises = comptable.get("/portefeuille/entreprises?a_la_date=2026-09-11")
        assert entreprises.status_code == 200, entreprises.text
        niu = entreprises.json()[0]["niu"]

        saisie = {
            "journal": self.JOURNAL,
            "exercice": "2026",
            "date_operation": "2026-07-02",
            # ⚠️ Un libellé **accentué**, et c'est le point : sans accent, le cas
            # d'encodage ne mesure rien — cp1252 et UTF-8 rendent les mêmes octets
            # pour de l'ASCII pur.
            "libelle": "Régularisation — écart de caisse",
            # ⚠️ Obligatoire pour valider : « une écriture validée porte sa pièce
            # justificative. Sans elle, la traçabilité est rompue dès le premier
            # maillon. » Le domaine refuse autrement.
            "piece_justificative": "PJ-OD-2026-0001",
            "reference_externe": "OD-2026-0001",
            "lignes": [
                # ⚠️ **Les accents sont sur les libellés de ligne**, et pas
                # seulement sur celui de l'écriture : le profil Sage n'exporte
                # pas le second. Un cas d'encodage dont l'accent tombe dans une
                # colonne absente ne mesure rien — et c'est ce qui est arrivé.
                {"compte": "622", "libelle": "Régularisation d'écart", "sens": "DEBIT",
                 "montant": "12500"},
                {"compte": "571", "libelle": "Caisse — espèces", "sens": "CREDIT",
                 "montant": "12500"},
            ],
        }
        creee = comptable.post(f"/comptabilite/dossiers/{niu}/ecritures", json=saisie)
        assert creee.status_code == 201, creee.text
        numero = creee.json()["numero"]
        validee = comptable.post(
            f"/comptabilite/dossiers/{niu}/ecritures/2026/{self.JOURNAL}/{numero}/validation",
            json={},
        )
        assert validee.status_code == 200, validee.text
        return niu

    def test_les_profils_viennent_du_referentiel(self, comptable):
        """⚠️ Lue au référentiel et non écrite dans la route : un écran qui
        recopierait la liste proposerait un profil retiré, ou tairait un profil
        ajouté le matin même."""
        reponse = comptable.get("/comptabilite/echange/profils")
        assert reponse.status_code == 200, reponse.text
        codes = {p["code"] for p in reponse.json()}
        assert codes == set(charger_les_profils(REFERENTIEL))

    def test_le_profil_publie_porte_sa_reserve(self, comptable):
        """Un exploitant qui choisit un profil doit lire ce que le centre en sait
        **sans ouvrir le dépôt**."""
        profils = {p["code"]: p for p in comptable.get("/comptabilite/echange/profils").json()}
        assert "installation réelle" in profils["sage-ligne100"]["remarque"]

    def test_le_fichier_est_encode_comme_le_profil_le_dit(self, comptable, dossier):
        reponse = comptable.get(
            f"/comptabilite/dossiers/{dossier}/export"
            "?exercice=2026&profil=sage-ligne100&journal=OD"
        )
        assert reponse.status_code == 200, reponse.text
        texte = reponse.content.decode("cp1252")
        assert "Journal;Date;NoPiece;Compte" in texte
        assert "\r\n" in texte

        # ⚠️ **L'assertion qui mesure vraiment l'encodage porte sur un accent.**
        #
        # Une mutation remplaçant `choisi.encodage` par `"utf-8"` survivait à ce
        # cas : l'en-tête est en ASCII pur, où les deux encodages produisent les
        # mêmes octets. Un cas qui n'éprouve que de l'ASCII n'éprouve pas un
        # encodage — il éprouve qu'il y a des octets.
        accentues = [m for m in texte.splitlines() if "é" in m or "ï" in m]
        assert accentues, "aucun libellé accentué : ce cas ne mesure plus rien"
        # En UTF-8 mal décodé, « é » (0xC3 0xA9) donnerait « Ã© ».
        assert "Ã" not in texte
        # Et la preuve par les octets : `é` vaut 0xE9 en cp1252, deux octets en UTF-8.
        assert b"\xe9" in reponse.content

    def test_le_pivot_et_le_progiciel_ne_rendent_pas_le_meme_fichier(
        self, comptable, dossier
    ):
        """⚠️ La contre-épreuve du mécanisme entier.

        Si les deux profils rendaient la même chose, le profil ne piloterait rien
        et le mécanisme serait décoratif.
        """
        sage = comptable.get(
            f"/comptabilite/dossiers/{dossier}/export?exercice=2026&profil=sage-ligne100&journal=OD"
        ).content
        pivot = comptable.get(
            f"/comptabilite/dossiers/{dossier}/export?exercice=2026&profil=pivot-csv&journal=OD"
        ).content
        assert sage != pivot
        assert b"\r\n" in sage and b"\r\n" not in pivot

    def test_un_profil_inconnu_nomme_ceux_qui_existent(self, comptable, dossier):
        reponse = comptable.get(
            f"/comptabilite/dossiers/{dossier}/export?exercice=2026&profil=cegid"
        )
        assert reponse.status_code == 404
        assert "pivot-csv" in reponse.json()["detail"]

    def test_le_nom_du_fichier_dit_le_dossier_l_exercice_et_le_profil(
        self, comptable, dossier
    ):
        """Un fichier téléchargé trois fois dans la journée doit se distinguer
        dans un dossier de téléchargements."""
        reponse = comptable.get(
            f"/comptabilite/dossiers/{dossier}/export?exercice=2026&profil=pivot-csv&journal=OD"
        )
        disposition = reponse.headers["content-disposition"]
        assert dossier in disposition
        assert "2026" in disposition
        assert "pivot-csv" in disposition

    def test_l_export_demande_une_habilitation_sur_ce_dossier(self, dossier):
        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app) as anonyme:
            reponse = anonyme.get(
                f"/comptabilite/dossiers/{dossier}/export?exercice=2026"
            )
        assert reponse.status_code in (401, 403)
