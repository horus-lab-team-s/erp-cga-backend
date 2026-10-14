"""Le PDF que la cliente lit, et sur lequel elle s'engage.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Jusqu'au pas 148, `empreinte_document` hachait la chaîne
« numéro|dossier|montant|instant ». Elle prouvait que ces quatre valeurs
n'avaient pas bougé — ce qui n'est pas rien — mais elle ne prouvait rien du
papier que la cliente lit. Le commentaire de la route le disait sans détour :
« le jour où le PDF existera, c'est lui qui devra être haché ».

Ce fichier tient ce jour-là. Les cas :

  1. avec une composition, l'empreinte est celle du PDF, et le PDF se relit ;
  2. le document servi est un vrai PDF, et son contenu porte les rubriques ;
  3. le total arrêté par le responsable prime sur celui de la composition ;
  4. sans composition, RIEN NE CHANGE : c'est ce qui protège les proformas
     déjà émises, dont l'empreinte ne correspond à aucun fichier ;
  5. la route publique exige le sceau, comme la consultation ;
  6. une composition inconnue est refusée à l'émission, pas découverte plus tard ;
  7. le rendu est déterministe, sans quoi l'empreinte ne voudrait rien dire ;
  8. aucun caractère hors Latin-1 n'atteint le flux : il s'y dessinerait en carré.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from app.contextes.souscription.adaptateurs.sortant.compositions_proforma import (
    RepertoireDesCompositions,
)
from app.contextes.souscription.adaptateurs.sortant.document_proforma import (
    rendre_le_document,
)
from app.contextes.souscription.domaine.proforma import empreinte
from app.infrastructure.config import configuration
from tests.conftest import exige_postgresql
from tests.test_envoi_de_la_proforma import _dossier_chiffre

pytestmark = exige_postgresql

COMPOSITION = "SARL_SOUS_SEING_AVEC_SUIVI"


def _composition(code=COMPOSITION):
    # ⚠️ Le même chemin que la route, par la configuration : le coder en dur ici
    # aurait laissé passer le défaut qui rendait la liste vide en conteneur.
    repertoire = RepertoireDesCompositions.depuis(
        configuration().dossier_referentiel / "tarification"
    )
    return repertoire.prendre(code, datetime(2026, 9, 30).date())


def _emettre(client, *, composition=COMPOSITION, montant=None):
    reference, propose = _dossier_chiffre(client, "699114499")
    emise = client.post(
        f"/acquisition/dossiers/{reference}/proforma",
        json={
            "montant": montant if montant is not None else propose,
            **({"composition": composition} if composition else {}),
        },
    )
    return emise


class TestLEmpreintePorteLeDocument:
    def test_le_document_se_relit_et_son_empreinte_correspond(self, plateforme):
        client = plateforme
        emise = _emettre(client)
        assert emise.status_code == 201, emise.text
        numero = emise.json()["numero"]

        lu = client.get(f"/acquisition/proformas/{numero}/document-interne")
        assert lu.status_code == 200, lu.text
        assert lu.headers["content-type"].startswith("application/pdf")
        assert lu.content.startswith(b"%PDF-")
        # ⚠️ LE POINT DU PAS : l'empreinte enregistrée est celle des octets servis.
        assert empreinte(lu.content) == emise.json()["empreinte_document"]

    def test_le_document_porte_les_rubriques_du_referentiel(self, plateforme):
        client = plateforme
        emise = _emettre(client)
        numero = emise.json()["numero"]
        contenu = client.get(f"/acquisition/proformas/{numero}/document-interne").content

        # Le texte d'un PDF non compressé se lit dans le flux. reportlab comprime
        # par défaut, donc on décompresse les flux avant de chercher.
        texte = _texte_du_pdf(contenu)
        for attendu in ("PROCEDURE AU CFCE", "PROCEDURE IMPOTS", "PROCEDURE CNPS"):
            assert attendu in texte, f"« {attendu} » absent du document"
        assert "TOTAL GENERAL" in texte

    def test_le_total_arrete_par_le_responsable_prime(self, plateforme):
        """⚠️ Le responsable a le droit de s'écarter du référentiel, motif à
        l'appui. Le document doit porter CE QU'IL A ARRÊTÉ, sans quoi la cliente
        signerait un total que le détail sous ses yeux ne justifie pas."""
        composition = _composition()
        autre = composition.total - Decimal(55_000)
        rendu = rendre_le_document(
            composition,
            numero="PRO-2026-0001",
            version=1,
            emise_le=datetime(2026, 9, 30, 10, 30),
            client="NGONO COUTURE SARL",
            total_arrete=autre,
        )
        texte = _texte_du_pdf(rendu)
        assert "450 000 FCFA" in texte, "le total arrêté ne figure pas au document"


class TestLesProformasDAvantNeBougentPas:
    def test_sans_composition_l_empreinte_reste_celle_du_resume(self, plateforme):
        """⚠️ LA GARANTIE DE NON-RÉGRESSION.

        Recalculer l'empreinte des proformas déjà émises donnerait une valeur qui
        ne correspond à aucun document jamais envoyé. Sans composition, le chemin
        d'avant est conservé octet pour octet.
        """
        client = plateforme
        emise = _emettre(client, composition=None)
        assert emise.status_code == 201, emise.text
        numero = emise.json()["numero"]

        # Aucun document au magasin : le 404 dit la vérité, il n'y a pas de PDF.
        lu = client.get(f"/acquisition/proformas/{numero}/document-interne")
        assert lu.status_code == 404
        assert "avant" in lu.json()["detail"]

    def test_une_composition_inconnue_est_refusee_a_l_emission(self, plateforme):
        """Refusée tout de suite, et non découverte le jour où la cliente clique."""
        client = plateforme
        emise = _emettre(client, composition="SARL_QUI_N_EXISTE_PAS")
        assert emise.status_code == 422, emise.text


class TestLaRoutePubliqueExigeLeSceau:
    def test_sans_sceau_valable_rien_ne_sort(self, plateforme):
        client = plateforme
        emise = _emettre(client)
        numero = emise.json()["numero"]

        nu = client.get(f"/acquisition/proformas/{numero}/document")
        assert nu.status_code == 422, "un document contractuel sans sceau"

        faux = client.get(
            f"/acquisition/proformas/{numero}/document",
            params={
                "version": 1,
                "expire_le": emise.json()["expire_le"],
                "sceau": "0" * 64,
            },
        )
        assert faux.status_code == 422

    def test_avec_son_lien_la_cliente_lit_son_document(self, plateforme):
        client = plateforme
        emise = _emettre(client)
        corps = emise.json()
        lu = client.get(
            f"/acquisition/proformas/{corps['numero']}/document",
            params={
                "version": corps["version"],
                "expire_le": corps["expire_le"],
                "sceau": corps["lien_acceptation"],
            },
        )
        assert lu.status_code == 200, lu.text
        assert lu.content.startswith(b"%PDF-")
        # ⚠️ `inline` : elle doit LIRE avant d'accepter, pas télécharger un fichier.
        assert lu.headers["content-disposition"].startswith("inline")


class TestLeRenduEstDeterministe:
    def test_deux_rendus_des_memes_donnees_donnent_le_meme_octet(self):
        """⚠️ SANS CELA L'EMPREINTE NE VEUT RIEN DIRE.

        reportlab pose par défaut un identifiant aléatoire et l'heure courante
        dans le document. `invariant=1` les supprime. Si ce test tombe un jour,
        ce n'est pas lui qu'il faut ajuster : c'est que `document_intact` a cessé
        de pouvoir distinguer une altération d'un simple nouveau rendu.
        """
        args = dict(
            numero="PRO-2026-0007",
            version=1,
            emise_le=datetime(2026, 9, 30, 10, 30),
            client="NGONO COUTURE SARL",
        )
        composition = _composition()
        assert empreinte(rendre_le_document(composition, **args)) == empreinte(
            rendre_le_document(composition, **args)
        )

    @pytest.mark.parametrize("code", ["SARL_SOUS_SEING_AVEC_SUIVI", "SARL_SOUS_SEING_SANS_SUIVI"])
    def test_aucun_caractere_hors_latin_1_n_atteint_le_flux(self, code):
        """⚠️ CE DÉFAUT A ÉTÉ VU, PAS IMAGINÉ.

        Les montants employaient l'espace fine insécable U+202F, absente de
        l'encodage Latin-1 d'Helvetica : reportlab dessinait un carré noir à la
        place, et le document rendait « 63■000 FCFA ». Aucun test ne l'aurait
        montré ; il a fallu regarder la page.
        """
        rendu = rendre_le_document(
            _composition(code),
            numero="PRO-2026-0008",
            version=1,
            emise_le=datetime(2026, 9, 30, 10, 30),
            client="NGONO COUTURE SARL",
        )
        assert "\u202f".encode() not in rendu
        texte = _texte_du_pdf(rendu)
        assert "\u202f" not in texte
        assert "\u00a0" not in texte, (
            "reportlab remplace l'insécable par une espace ordinaire : la garder "
            "dans le code promettrait une insécabilité que le fichier n'a pas."
        )
        # ⚠️ Le séparateur attendu est bien là, et c'est une espace ORDINAIRE.
        # Un PDF ne recompose pas : il n'y a aucun repli de ligne à empêcher.
        assert "63 000 FCFA" in texte or "12 500 FCFA" in texte


class TestAucunTiretCadratinDansLeDocument:
    """⚠️ LA RÈGLE DU CABINET, APPLIQUÉE LÀ OÙ ELLE COMPTE.

    Aucun tiret cadratin dans ce qu'un client lit. Les gabarits de courriel et le
    document de conception sont déjà gardés ; le PDF de la proforma est le
    troisième document qui sort, et c'est le seul sur lequel la cliente s'engage.

    ⚠️ **ON MESURE LE RENDU, PAS LA SOURCE.** Un commentaire de code peut en
    porter sans conséquence : personne hors de l'équipe ne le lit. C'est le
    fichier produit qui est contrôlé.
    """

    @pytest.mark.parametrize(
        "code", ["SARL_SOUS_SEING_AVEC_SUIVI", "SARL_SOUS_SEING_SANS_SUIVI"]
    )
    def test_le_document_rendu_n_en_porte_aucun(self, code):
        texte = _texte_du_pdf(
            rendre_le_document(
                _composition(code),
                numero="PRO-2026-0009",
                version=1,
                emise_le=datetime(2026, 9, 30, 10, 30),
                client="NGONO COUTURE SARL",
            )
        )
        fautifs = [ligne for ligne in texte.splitlines() if "\u2014" in ligne]
        assert not fautifs, "tiret cadratin dans le document :\n  " + "\n  ".join(fautifs)


def _texte_du_pdf(octets: bytes) -> str:
    """Le texte dessiné, flux décodés.

    reportlab enchaîne deux filtres, `ASCII85Decode` puis `FlateDecode`, et il
    faut les défaire dans cet ordre. Volontairement rustique : on ne réimplémente
    pas un lecteur de PDF, on retrouve des chaînes qu'on sait avoir dessinées.

    ⚠️ Aucune dépendance à `pdftotext` ni à un lecteur tiers : la suite doit
    tourner en intégration continue sans poppler.
    """
    import base64
    import re
    import zlib

    morceaux = []
    for flux in re.findall(rb"stream\n(.*?)endstream", octets, re.S):
        brut = flux.strip()
        try:
            brut = base64.a85decode(brut, adobe=True)
        except ValueError:
            pass
        try:
            brut = zlib.decompress(brut)
        except zlib.error:
            pass
        # ⚠️ UNE IMAGE N'EST PAS DU TEXTE. Depuis le 14 octobre 2026 le document
        # porte le logo du cabinet, et son flux JPEG, lu comme une page, contient
        # par hasard des parenthèses suivies de « Tj » et l'octet de l'insécable :
        # la garde ci-dessous rougissait sur un document irréprochable. On ne lit
        # que les flux de contenu ; une image commence par son marqueur JPEG.
        if brut[:2] == b"\xff\xd8":
            continue
        morceaux.append(brut)
    rendu = b"\n".join(morceaux).decode("latin-1", "replace")
    # Les chaînes dessinées sont entre parenthèses, suivies de l'opérateur `Tj`.
    # Les octets non ASCII y sont échappés en octal : `\240` pour l'insécable.
    textes = re.findall(r"\((.*?)\)\s*Tj", rendu, re.S)
    return "\n".join(
        re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), morceau)
        for morceau in textes
    )


class TestLaSecondePageDuCabinet:
    """⚠️ CE QUE LES PROFORMAS PAPIER DISENT APRÈS LE TOTAL (14 octobre 2026).

    Le document de la plateforme s'arrêtait au montant en toutes lettres. Ceux du
    cabinet continuent : ce que le client doit apporter, ce qu'il recevra, en
    combien de temps, et comment régler. Le client recevait donc un prix, puis un
    appel pour lui dicter la liste des pièces.

    Éprouvé dans les deux sens : en retirant l'appel à `_suite` du rendu, les
    trois premiers cas rougissent ; le quatrième rougit si une composition sans
    suite tourne quand même la page.
    """

    def _rendu(self, composition):
        return rendre_le_document(
            composition,
            numero="PRO-2026-0008",
            version=1,
            emise_le=datetime(2026, 10, 14, 10, 30),
            client="MBARGA COUTURE SARL",
        )

    @staticmethod
    def _pages(octets: bytes) -> int:
        import re

        return len(re.findall(rb"/Type\s*/Page\b(?!s)", octets))

    @pytest.mark.parametrize("code", ["SARL_SOUS_SEING_AVEC_SUIVI", "SARL_SOUS_SEING_SANS_SUIVI"])
    def test_le_client_lit_ce_qu_il_doit_apporter_et_ce_qu_il_recevra(self, code):
        composition = _composition(code)
        texte = _texte_du_pdf(self._rendu(composition))

        assert "DOCUMENTS A FOURNIR" in texte
        assert "DOCUMENTS A RECEVOIR" in texte
        assert len(composition.documents_a_fournir) == 12
        assert len(composition.documents_a_recevoir) == 6
        # La première et la dernière pièce : la liste est entière, pas amorcée.
        assert "Le nom de votre entreprise" in texte
        assert "Cachet de l'entreprise" in texte
        assert "Un registre de commerce" in texte

    def test_le_delai_et_les_moyens_de_reglement_sont_imprimes(self):
        texte = _texte_du_pdf(self._rendu(_composition()))

        assert "8 semaines maximum" in texte
        # ⚠️ Les coordonnées de règlement du cabinet, telles que son papier les
        # porte. Une proforma qui les tait oblige le client à rappeler pour payer.
        assert "10033-05201-01016000835-92" in texte
        assert "586853" in texte

    def test_la_suite_tient_sur_une_seconde_page_avec_son_en_tete(self):
        rendu = self._rendu(_composition())

        assert self._pages(rendu) == 2
        # L'en-tête et le pied sont redessinés : une page sans la marque de la
        # maison, glissée seule dans un dossier, ne dit pas d'où elle vient.
        assert _texte_du_pdf(rendu).count("CGA BROAD RANGE CONSULTING") == 2

    def test_une_composition_sans_suite_reste_sur_une_page(self):
        nue = _composition().model_copy(
            update={
                "documents_a_fournir": (),
                "documents_a_recevoir": (),
                "delai_d_execution": "",
            }
        )
        rendu = self._rendu(nue)

        assert self._pages(rendu) == 1
        assert "DOCUMENTS A FOURNIR" not in _texte_du_pdf(rendu)

    def test_sans_le_fichier_du_logo_le_document_sort_quand_meme(self, monkeypatch, tmp_path):
        """Une image mal construite doit encore émettre des proformas."""
        from app.contextes.souscription.adaptateurs.sortant import document_proforma

        monkeypatch.setattr(document_proforma, "_LOGO", tmp_path / "absent.jpg")
        texte = _texte_du_pdf(self._rendu(_composition()))

        assert "TOTAL GENERAL" in texte
        assert "DOCUMENTS A FOURNIR" in texte


class TestLeDetailAjusteParLeResponsable:
    """⚠️ L'APERÇU SE CORRIGE, ET LE PRIX SUIT SES LIGNES (14 octobre 2026).

    Le cabinet veut que le juriste voie la proforma avant de l'émettre et la
    retouche. Le référentiel donne le point de départ ; sept pages de statuts
    deviennent neuf, une prestation s'ajoute.

    La règle qui en découle : **le montant arrêté est le total des lignes**. Un
    prix que son propre détail ne totalise pas n'est justifié par rien.

    Éprouvé dans les deux sens : sans le contrôle du total, le deuxième cas
    rougit ; sans le remplacement des rubriques, le premier.
    """

    def _rubriques(self, *, timbres: int = 63000, libelle_timbres: str | None = None):
        composition = _composition()
        rubriques = [
            {
                "numero": r.numero,
                "intitule": r.intitule,
                "lignes": [
                    {"libelle": ligne.libelle, "montant": str(ligne.montant)}
                    for ligne in r.lignes
                ],
            }
            for r in composition.rubriques
        ]
        rubriques[0]["lignes"][0]["montant"] = str(timbres)
        if libelle_timbres:
            rubriques[0]["lignes"][0]["libelle"] = libelle_timbres
        total = sum(int(ligne["montant"]) for r in rubriques for ligne in r["lignes"])
        return rubriques, total

    def _emettre(self, client, rubriques, montant, **plus):
        reference, _ = _dossier_chiffre(client, "699114477")
        return client.post(
            f"/acquisition/dossiers/{reference}/proforma",
            json={
                "montant": montant,
                "composition": COMPOSITION,
                "rubriques": rubriques,
                "motif": "Statuts de neuf pages : deux pages de timbres en plus.",
                **plus,
            },
        )

    def test_le_document_porte_la_ligne_retouchee_et_son_total(self, plateforme):
        client = plateforme
        rubriques, total = self._rubriques(
            timbres=81000,
            libelle_timbres="Timbres Statuts (9 pages X 1500 FCFA X 6 exemplaires)",
        )
        assert total == 523000

        emise = self._emettre(client, rubriques, total)
        assert emise.status_code == 201, emise.text

        document = client.get(
            f"/acquisition/proformas/{emise.json()['numero']}/document-interne"
        )
        assert document.status_code == 200, document.text
        texte = _texte_du_pdf(document.content)
        assert "9 pages X 1500 FCFA" in texte
        assert "81 000 FCFA" in texte and "63 000 FCFA" not in texte
        assert "523 000 FCFA" in texte
        # Le premier sous-total a suivi : il se calcule, il ne se recopie pas.
        assert "195 000 FCFA" in texte

    def test_la_page_du_client_explique_le_prix_par_les_rubriques_du_document(
        self, plateforme
    ):
        """⚠️ Une seule explication du prix, celle du document (14 octobre 2026).

        La page montrait les lignes du barème (« Siège hors du Littoral +
        37 500 »), qui ne totalisaient pas le montant de la proforma reçue.
        Éprouvé : sans les rubriques gardées à l'émission, ce cas rougit ; en
        rendant aussi les lignes du barème, il rougit encore.
        """
        from tests.test_prix_du_dossier import REPONSES, _chiffrer, _qualifier

        client = plateforme
        rubriques, total = self._rubriques(
            timbres=81000,
            libelle_timbres="Timbres Statuts (9 pages X 1500 FCFA X 6 exemplaires)",
        )
        # ⚠️ UN SIÈGE HORS DU LITTORAL, pour que le barème ait une ligne à
        # montrer (TAR-REG-001). Au Littoral, il n'en a aucune, et le cas
        # restait vert même en rendant le calcul du barème : éprouvé.
        reference, _ = _dossier_chiffre(client, "699114455")
        _qualifier(
            client,
            reference,
            [(c, "CENTRE" if c == "region_siege" else v) for c, v in REPONSES],
        )
        _chiffrer(client, reference)
        emise = client.post(
            f"/acquisition/dossiers/{reference}/proforma",
            json={
                "montant": total,
                "composition": COMPOSITION,
                "rubriques": rubriques,
                "motif": "Statuts de neuf pages : deux pages de timbres en plus.",
            },
        )
        assert emise.status_code == 201, emise.text
        corps = emise.json()

        lu = client.get(
            f"/acquisition/proformas/{corps['numero']}/consultation",
            params={
                "version": corps["version"],
                "expire_le": corps["expire_le"],
                "sceau": corps["lien_acceptation"],
            },
        )

        assert lu.status_code == 200, lu.text
        page = lu.json()
        assert page["lignes"] == [], "le calcul du barème contredirait le document"
        assert [r["numero"] for r in page["rubriques"]] == [r["numero"] for r in rubriques]
        assert page["rubriques"][0]["lignes"][0]["libelle"].startswith("Timbres Statuts (9 pages")
        assert sum(Decimal(r["sous_total"]) for r in page["rubriques"]) == Decimal(page["montant"])

    def test_un_montant_que_les_lignes_ne_totalisent_pas_est_refuse(self, plateforme):
        rubriques, total = self._rubriques(timbres=81000)

        refus = self._emettre(plateforme, rubriques, total - 23000)

        assert refus.status_code == 422, refus.text
        assert "n'est pas le total des lignes" in refus.text
        assert "523000" in refus.text

    def test_un_detail_sans_document_est_refuse(self, plateforme):
        client = plateforme
        rubriques, total = self._rubriques()
        reference, _ = _dossier_chiffre(client, "699114466")

        refus = client.post(
            f"/acquisition/dossiers/{reference}/proforma",
            json={"montant": total, "rubriques": rubriques, "motif": "essai"},
        )

        assert refus.status_code == 422, refus.text
        assert "sans document" in refus.text

    def test_une_rubrique_vide_est_refusee(self, plateforme):
        rubriques, total = self._rubriques()
        rubriques[2]["lignes"] = []

        refus = self._emettre(plateforme, rubriques, total)

        assert refus.status_code == 422, refus.text
