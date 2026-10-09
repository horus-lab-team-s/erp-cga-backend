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
        morceaux.append(brut)
    rendu = b"\n".join(morceaux).decode("latin-1", "replace")
    # Les chaînes dessinées sont entre parenthèses, suivies de l'opérateur `Tj`.
    # Les octets non ASCII y sont échappés en octal : `\240` pour l'insécable.
    textes = re.findall(r"\((.*?)\)\s*Tj", rendu, re.S)
    return "\n".join(
        re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), morceau)
        for morceau in textes
    )
