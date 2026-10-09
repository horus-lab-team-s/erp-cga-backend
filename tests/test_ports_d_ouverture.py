"""Les trois ports de l'ouverture d'un tenant, et ce qu'ils promettent.

─────────────────────────────────────────────────────────────────────────────────
⚠️ CE QUE CES PORTS DÉBLOQUENT

`ProvisionneurLocal` les prend en option et **substitue** l'étape quand le port
manque. Il était câblé sans aucun des trois : toute ouverture se terminait avec
le schéma, le stockage et le compte administrateur sans effet — un client payé
sans identifiant ni lien d'activation.

C'était honnête, la substitution étant déclarée. Ce n'était pas livrable : un
second cabinet n'aurait pas pu ouvrir son espace.

⚠️ CHAQUE PORT EST ÉPROUVÉ SUR SON REJEU, et c'est la propriété qui compte le
plus. Le relais publie **au moins une fois** : rejoué, un port doit constater que
son travail est fait, et surtout ne pas envoyer un second lien d'activation au
client — qui se demanderait lequel est le bon.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.contextes.transverse.adaptateurs.sortant.ports_d_ouverture import (
    port_de_stockage,
    port_des_comptes,
)

CONTEXTE = {
    "tenant": "tnt-essai-ports",
    "slug": "essai-ports",
    "courriel_titulaire": "titulaire@essai-ports.cm",
    "nom_titulaire": "ESSAI PORTS SARL",
}


@pytest.fixture
def unite_en_memoire():
    """Une unité de travail en mémoire, sur le tenant du CLIENT.

    ⚠️ **Pas `AtelierMemoire()`**, et pas la fixture `plateforme`.

    `AtelierMemoire` construit ses dépôts sur `LOCATAIRE_PAR_DEFAUT` — une
    constante, pas le locataire courant. Ses dépôts refusent donc toute écriture
    d'un autre locataire, et c'est exactement le garde-fou qui a révélé le défaut
    de ma première version du port. On construit ici les quatre dépôts dont le
    port a besoin, liés au tenant du client.

    ⚠️ **Elle rend une FABRIQUE, pas un atelier.** Le port ouvre lui-même son
    unité de travail sur le tenant du client : c'est le cœur de la correction.
    """
    from contextlib import contextmanager
    from types import SimpleNamespace

    from app.contextes.transverse.adaptateurs.sortant.depots_memoire import (
        DepotComptesMemoire,
        DepotHabilitationsMemoire,
        DepotJetonsMemoire,
        JournalAuditMemoire,
    )

    locataire = CONTEXTE["tenant"]
    boutique = SimpleNamespace(
        comptes=DepotComptesMemoire(locataire),
        habilitations=DepotHabilitationsMemoire(locataire),
        jetons=DepotJetonsMemoire(locataire),
        journal=JournalAuditMemoire(locataire),
    )

    @contextmanager
    def unite(_locataire: str | None = None):
        yield boutique

    return unite


class TestLePortDuStockage:
    """⚠️ Le préfixe est créé À L'OUVERTURE, et non au premier dépôt.

    Si le volume est absent, en lecture seule ou plein, on l'apprend pendant
    qu'un collaborateur regarde l'écran — et non six semaines plus tard, quand un
    adhérent dépose sa première facture depuis son téléphone.
    """

    def test_il_cree_le_prefixe_du_locataire(self, tmp_path: Path):
        contexte = port_de_stockage(tmp_path)(CONTEXTE)

        prefixe = tmp_path / "tnt-essai-ports"
        assert prefixe.is_dir()
        assert contexte["prefixe_de_stockage"] == str(prefixe)

    def test_le_prefixe_porte_le_nom_que_le_magasin_emploie(self, tmp_path: Path):
        """⚠️ `MagasinLocal` range en `racine/<locataire>/xx/yy/clé`. Un préfixe
        créé sous un autre nom serait un répertoire vide à côté du vrai."""
        port_de_stockage(tmp_path)(CONTEXTE)
        assert (tmp_path / CONTEXTE["tenant"]).is_dir()

    def test_rejoue_il_constate_sans_echouer(self, tmp_path: Path):
        ouvrir = port_de_stockage(tmp_path)
        ouvrir(CONTEXTE)
        ouvrir(CONTEXTE)  # le relais publie au moins une fois
        assert (tmp_path / "tnt-essai-ports").is_dir()

    def test_les_droits_sont_resserres(self, tmp_path: Path):
        """Les justificatifs d'un cabinet n'ont rien à faire en lecture pour les
        autres comptes de la machine."""
        port_de_stockage(tmp_path)(CONTEXTE)
        mode = (tmp_path / "tnt-essai-ports").stat().st_mode & 0o777
        assert mode == 0o700, oct(mode)

    def test_un_volume_en_lecture_seule_est_refuse_franchement(self, tmp_path: Path):
        """⚠️ `mkdir` réussit sur un chemin déjà là, même en lecture seule : un
        tenant ouvert sur un tel volume accepterait les dépôts et les perdrait."""
        racine = tmp_path / "verrouille"
        (racine / CONTEXTE["tenant"]).mkdir(parents=True)
        racine.chmod(0o500)
        (racine / CONTEXTE["tenant"]).chmod(0o500)
        try:
            with pytest.raises(RuntimeError, match="n'accepte pas l'écriture"):
                port_de_stockage(racine)(CONTEXTE)
        finally:
            racine.chmod(0o700)
            (racine / CONTEXTE["tenant"]).chmod(0o700)


# ⚠️ IL N'Y A PAS DE CAS POUR UN PORT DE SCHÉMA, PARCE QU'IL N'Y A PAS DE PORT.
#
# J'en avais écrit un, et ses cas. Il vérifiait que les politiques de
# cloisonnement étaient en vigueur — une propriété GLOBALE de la plateforme, que
# `/sante` rend déjà. Et il cassait quatre cas de bout en bout : la recette monte
# son schéma par `create_all`, qui crée les tables mais pas les politiques.
#
# L'étape `SCHEMA_CREE` reste donc substituée, et l'écran le dit. Voir l'en-tête
# de `ports_d_ouverture.py`.


class TestLePortDesComptes:
    """Le premier administrateur du tenant, et son lien à usage unique."""

    def test_sans_adresse_l_etape_se_SUBSTITUE_au_lieu_de_bloquer(self):
        """⚠️ La règle est écrite dans le provisionneur : « le silence mentirait,
        l'échec bloquerait, la substitution déclarée informe. »

        Une adresse absente est un cas où l'étape NE PEUT PAS se faire — comme une
        infrastructure absente. Lever laisserait le tenant à moitié ouvert ; la
        substitution laisse la saga finir et fait dire à l'écran « l'espace
        existe, mais il n'est pas utilisable ».

        ⚠️ Cela vaut aussi pour un événement déposé **avant** que la charge ne
        transporte l'adresse : il en existe dans la boîte d'envoi, et il ne doit
        pas tourner jusqu'à la quarantaine.
        """
        from app.contextes.tenants.api import substituees

        contexte = port_des_comptes(lambda: None)({**CONTEXTE, "courriel_titulaire": ""})

        assert "ADMINISTRATEUR_CREE" in substituees(contexte)
        assert "adresse du titulaire absente" in contexte["administrateur"]

    def test_sans_courrier_configure_l_etape_se_substitue_AVANT_de_creer(self, unite_en_memoire):
        """⚠️ Le notifieur est vérifié AVANT la création du compte. L'inverse
        laisserait un compte sans lien : un état à moitié fait, le pire des
        trois."""
        from app.contextes.tenants.api import substituees

        contexte = port_des_comptes(unite_en_memoire, None)(CONTEXTE)

        assert "ADMINISTRATEUR_CREE" in substituees(contexte)
        assert "aucun courrier" in contexte["administrateur"]
        # Et surtout : aucun compte n'a été créé.
        with unite_en_memoire() as boutique:
            assert boutique.comptes.par_courriel(CONTEXTE["courriel_titulaire"]) is None

    def test_le_secret_n_entre_JAMAIS_dans_le_contexte_de_la_saga(self, unite_en_memoire):
        """⚠️ LE CAS LE PLUS IMPORTANT DE CE FICHIER.

        Un contexte de saga est écrit en base, recopié dans les journaux et dans
        les sauvegardes. Un lien d'activation qui traînerait là serait un compte
        à prendre pour qui lit une sauvegarde.
        """
        postes: list[dict] = []

        class Notifieur:
            @staticmethod
            def envoyer(code, *, destinataire, contexte) -> bool:
                postes.append({"code": code, "destinataire": destinataire, **contexte})
                return True

        contexte = port_des_comptes(unite_en_memoire, Notifieur())(CONTEXTE)

        assert contexte["administrateur"].startswith("C-")
        # Le lien est parti au client, et lui seul.
        assert postes and postes[0]["destinataire"] == CONTEXTE["courriel_titulaire"]
        assert "jeton=" in postes[0]["lien"]
        # ⚠️ Et rien de tout cela n'est dans le contexte.
        aplati = " ".join(str(v) for v in contexte.values())
        assert "jeton=" not in aplati
        assert postes[0]["lien"] not in aplati

    def test_rejoue_il_n_envoie_PAS_un_second_lien(self, unite_en_memoire):
        """Le client se demanderait lequel est le bon, et le premier serait
        invalidé par le second."""
        envois: list[str] = []

        class Notifieur:
            @staticmethod
            def envoyer(code, *, destinataire, contexte) -> bool:
                envois.append(destinataire)
                return True

        creer = port_des_comptes(unite_en_memoire, Notifieur())
        creer(CONTEXTE)
        second = creer(CONTEXTE)

        assert len(envois) == 1
        assert second["administrateur"] == "déjà créé"

    def test_un_envoi_refuse_leve_plutot_que_de_passer(self, unite_en_memoire):
        class NotifieurEnPanne:
            @staticmethod
            def envoyer(code, *, destinataire, contexte) -> bool:
                return False

        with pytest.raises(RuntimeError, match="n'a pas pu être posté"):
            port_des_comptes(unite_en_memoire, NotifieurEnPanne())(
                {**CONTEXTE, "courriel_titulaire": "autre@essai-ports.cm"}
            )


class TestLeLienDuTitulaire:
    """Le lien d'activation doit mener LÀ OÙ LE COMPTE EXISTE.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ LE DÉFAUT QUE CES CAS FERMENT, ET IL RENDAIT LE PRODUIT INVENDABLE.

    Le compte du titulaire et son jeton vivent dans le locataire du client. La
    route qui définit le mot de passe travaille dans le locataire que **le nom
    d'hôte** désigne — voir `tenants/domaine/resolution.py`. Un lien posé sur
    l'hôte du cabinet est donc servi dans le locataire du cabinet, où ce jeton
    n'existe pas.

    Conséquence mesurée le 27 septembre, en suivant le lien dans un vrai
    navigateur : la cliente payait sa création, recevait son courriel d'accès
    trois secondes plus tard, et lisait « ce lien n'est plus valable » sur un
    jeton émis à l'instant. Elle n'avait **aucun** chemin vers son espace.

    Deux choses se sont ajoutées à ce lien, et chacune le cassait à elle seule :
    l'adresse de l'API au lieu de celle du site, puis l'hôte du cabinet au lieu
    du sous-domaine du client.
    ─────────────────────────────────────────────────────────────────────────────
    """

    def test_le_lien_porte_le_sous_domaine_du_tenant(self):
        from app.contextes.transverse.adaptateurs.sortant.ports_d_ouverture import (
            _hote_du_tenant,
        )

        assert (
            _hote_du_tenant("https://cga-brcg.cm", "station-bonaberi")
            == "https://station-bonaberi.cga-brcg.cm"
        )

    def test_le_port_est_conserve(self):
        """⚠️ En démonstration l'adresse porte `:3100`, et `*.localhost` répond."""
        from app.contextes.transverse.adaptateurs.sortant.ports_d_ouverture import (
            _hote_du_tenant,
        )

        assert (
            _hote_du_tenant("http://localhost:3100", "couture-1a2b")
            == "http://couture-1a2b.localhost:3100"
        )

    def test_sans_slug_l_adresse_reste_entiere(self):
        """Un lien faux mais lisible vaut mieux qu'un hôte tordu : celui-ci
        aurait l'air d'une panne de réseau, et l'on chercherait ailleurs."""
        from app.contextes.transverse.adaptateurs.sortant.ports_d_ouverture import (
            _hote_du_tenant,
        )

        assert _hote_du_tenant("https://cga-brcg.cm", "") == "https://cga-brcg.cm"

    def test_le_lien_ne_part_JAMAIS_sur_l_adresse_de_l_api(self):
        """⚠️ `adresse_publique` est celle de l'API — celle que le prestataire de
        paiement rappelle. Le lien d'activation mène à une PAGE.

        Ce cas relit la source plutôt que d'exécuter : c'est un choix de
        vocabulaire, et un choix de vocabulaire se garde au vocabulaire.
        """
        from pathlib import Path

        source = Path(
            "app/contextes/transverse/adaptateurs/sortant/ports_d_ouverture.py"
        ).read_text()
        utiles = [
            ligne
            for ligne in source.splitlines()
            if "configuration()." in ligne and not ligne.strip().startswith("#")
        ]
        assert utiles, "le port ne lit plus la configuration : ce cas est à revoir"
        for ligne in utiles:
            assert "adresse_publique_site" in ligne, ligne.strip()

