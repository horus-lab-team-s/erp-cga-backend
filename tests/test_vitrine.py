"""Contexte L · Vitrine publique — contenu éditorial du site.

Deux niveaux d'épreuve, et ils ne servent pas à la même chose :

* **Le cas d'usage**, éprouvé sur un dépôt en mémoire. Aucun disque, aucun YAML :
  ce sont les décisions de lecture qu'on vérifie — l'ordre, le filtrage, le
  voisinage, la fenêtre d'une annonce. C'est possible **parce que** le service
  reçoit un port et non un chemin de fichier ; ces tests sont la preuve que
  l'inversion de dépendance sert à quelque chose.
* **Le contenu réel**, celui que le cabinet édite. On ne vérifie pas ce qu'il dit
  — ce n'est pas notre rôle — mais qu'il se charge, et qu'il respecte les
  contraintes qui abîmeraient le site si elles étaient violées.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.contextes.vitrine.api import (
    Annonce,
    Article,
    ArticleInconnu,
    ContenuIllisible,
    DepotContenuVitrineYaml,
    Institution,
    Rubrique,
    ServiceContenu,
    SlugsEnDoublon,
)
from app.infrastructure.config import RACINE_DEPOT

CONTENU = RACINE_DEPOT / "Contenu_vitrine"


# ── Matière de test ───────────────────────────────────────────────────────────


def _article(slug: str, rubrique: Rubrique, jour: str) -> Article:
    """Un article minimal mais valide. Seuls slug, rubrique et date varient : ce
    sont les trois seules choses dont les cas d'usage se servent."""
    return Article(
        slug=slug,
        rubrique=rubrique,
        titre=f"Titre {slug}",
        accroche=f"Accroche {slug}",
        resume=f"Résumé {slug}",
        date=date.fromisoformat(jour),
        image="/images/services/conseil.jpg",
        minutes=3,
        mots_cles=[],
        corps=[{"type": "paragraphe", "texte": "Corps."}],
    )


class DepotEnMemoire:
    """Réalise `DepotContenuVitrine` sans toucher au disque."""

    def __init__(
        self,
        articles: list[Article] | None = None,
        annonces: list[Annonce] | None = None,
        institutions: list[Institution] | None = None,
    ) -> None:
        self._articles = articles or []
        self._annonces = annonces or []
        self._institutions = institutions or []

    def charger_articles(self) -> list[Article]:
        return list(self._articles)

    def charger_annonces(self) -> list[Annonce]:
        return list(self._annonces)

    def charger_institutions(self) -> list[Institution]:
        return list(self._institutions)


# ── Cas d'usage ───────────────────────────────────────────────────────────────


class TestLectureDesArticles:
    def test_le_sommaire_va_du_plus_recent_au_plus_ancien(self):
        # Volontairement dans le désordre : c'est le service qui range, pas le dépôt.
        service = ServiceContenu.depuis_depot(
            DepotEnMemoire(
                [
                    _article("vieux", Rubrique.SAVIEZ_VOUS, "2018-01-15"),
                    _article("recent", Rubrique.SAVIEZ_VOUS, "2026-01-17"),
                    _article("median", Rubrique.ANNONCES, "2025-04-08"),
                ]
            )
        )
        assert [a.slug for a in service.articles()] == ["recent", "median", "vieux"]

    def test_le_filtre_par_rubrique_conserve_l_ordre(self):
        service = ServiceContenu.depuis_depot(
            DepotEnMemoire(
                [
                    _article("a", Rubrique.SAVIEZ_VOUS, "2020-01-01"),
                    _article("b", Rubrique.ANNONCES, "2021-01-01"),
                    _article("c", Rubrique.SAVIEZ_VOUS, "2022-01-01"),
                ]
            )
        )
        assert [a.slug for a in service.articles(Rubrique.SAVIEZ_VOUS)] == ["c", "a"]

    def test_un_slug_inconnu_leve_plutot_que_de_rendre_rien(self):
        """Une page d'article absente doit produire un 404 lisible, jamais une page
        à moitié vide dont personne ne comprend l'origine."""
        service = ServiceContenu.depuis_depot(DepotEnMemoire())
        with pytest.raises(ArticleInconnu):
            service.article("inexistant")

    def test_deux_articles_au_meme_slug_sont_refuses_au_chargement(self):
        """Deux articles à la même adresse, c'est l'un des deux définitivement
        inatteignable, et l'ordre du fichier qui décide lequel — au hasard."""
        with pytest.raises(SlugsEnDoublon):
            ServiceContenu.depuis_depot(
                DepotEnMemoire(
                    [
                        _article("meme", Rubrique.SAVIEZ_VOUS, "2020-01-01"),
                        _article("meme", Rubrique.ANNONCES, "2021-01-01"),
                    ]
                )
            )

    def test_les_comptes_couvrent_toutes_les_rubriques_meme_vides(self):
        """Une rubrique vide doit se voir avant d'être ouverte : sans son compte à
        zéro, le filtre invite à cliquer dans le vide."""
        service = ServiceContenu.depuis_depot(
            DepotEnMemoire([_article("a", Rubrique.SAVIEZ_VOUS, "2020-01-01")])
        )
        comptes = service.comptes_par_rubrique()
        assert set(comptes) == set(Rubrique)
        assert comptes[Rubrique.SAVIEZ_VOUS] == 1
        assert comptes[Rubrique.COIN_FISCALISTE] == 0


class TestVoisinsDeLecture:
    def test_la_meme_rubrique_passe_devant(self):
        service = ServiceContenu.depuis_depot(
            DepotEnMemoire(
                [
                    _article("courant", Rubrique.COIN_FISCALISTE, "2026-01-01"),
                    _article("autre-rubrique", Rubrique.ANNONCES, "2026-06-01"),
                    _article("meme-rubrique", Rubrique.COIN_FISCALISTE, "2019-01-01"),
                ]
            )
        )
        # « meme-rubrique » est pourtant le plus ancien des deux : la parenté de
        # rubrique l'emporte sur la fraîcheur.
        assert [a.slug for a in service.voisins("courant")] == [
            "meme-rubrique",
            "autre-rubrique",
        ]

    def test_l_article_courant_ne_se_propose_pas_lui_meme(self):
        service = ServiceContenu.depuis_depot(
            DepotEnMemoire([_article("seul", Rubrique.SAVIEZ_VOUS, "2020-01-01")])
        )
        assert service.voisins("seul") == []

    def test_une_rubrique_maigre_est_completee_par_les_autres(self):
        """Sans ce complément, un bas de page resterait vide dès qu'une rubrique ne
        compte qu'un article."""
        service = ServiceContenu.depuis_depot(
            DepotEnMemoire(
                [
                    _article("courant", Rubrique.VRAI_OU_FAUX, "2026-01-01"),
                    _article("x", Rubrique.ANNONCES, "2025-01-01"),
                    _article("y", Rubrique.ANNONCES, "2024-01-01"),
                    _article("z", Rubrique.ANNONCES, "2023-01-01"),
                    _article("t", Rubrique.ANNONCES, "2022-01-01"),
                ]
            )
        )
        assert len(service.voisins("courant")) == 3


class TestAnnonces:
    ANNONCE = Annonce(
        cle="offre",
        etiquette="Offre",
        texte="Une offre.",
        lien="/blog/offre",
        libelle_lien="Voir",
        du=date(2026, 1, 1),
        au=date(2026, 12, 31),
    )

    def test_les_deux_bornes_sont_incluses(self):
        """Une annonce du 1er au 31 doit s'afficher le 31. Une borne haute exclue
        ferait disparaître l'offre le dernier jour, celui où elle presse le plus."""
        service = ServiceContenu.depuis_depot(DepotEnMemoire(annonces=[self.ANNONCE]))
        assert service.annonce_du_jour(date(2026, 1, 1)) is not None
        assert service.annonce_du_jour(date(2026, 12, 31)) is not None

    def test_hors_fenetre_il_n_y_a_rien_a_afficher(self):
        service = ServiceContenu.depuis_depot(DepotEnMemoire(annonces=[self.ANNONCE]))
        assert service.annonce_du_jour(date(2025, 12, 31)) is None
        assert service.annonce_du_jour(date(2027, 1, 1)) is None

    def test_une_seule_annonce_a_la_fois_la_premiere_du_fichier(self):
        """Deux bandeaux superposés font une page qu'on ne lit plus. L'ordre du
        fichier est donc l'ordre de priorité, et il est de la main du cabinet."""
        seconde = self.ANNONCE.model_copy(update={"cle": "seconde"})
        service = ServiceContenu.depuis_depot(DepotEnMemoire(annonces=[self.ANNONCE, seconde]))
        assert service.annonce_du_jour(date(2026, 6, 1)).cle == "offre"

    def test_une_fenetre_a_l_envers_est_refusee(self):
        with pytest.raises(ValueError):
            Annonce(
                cle="x",
                etiquette="X",
                texte="X",
                lien="/x",
                libelle_lien="X",
                du=date(2026, 12, 31),
                au=date(2026, 1, 1),
            )


# ── Le contenu réel, celui que le cabinet édite ───────────────────────────────


@pytest.fixture(scope="module")
def dossier_contenu() -> Path:
    assert CONTENU.is_dir(), f"contenu de la vitrine introuvable : {CONTENU}"
    return CONTENU


@pytest.fixture(scope="module")
def contenu_reel(dossier_contenu: Path) -> ServiceContenu:
    return ServiceContenu.depuis_depot(DepotContenuVitrineYaml(dossier_contenu))


class TestContenuPublie:
    def test_le_contenu_se_charge(self, contenu_reel: ServiceContenu):
        """Le test le plus utile du fichier : il échoue dès qu'une main a laissé le
        YAML dans un état où le site ne démarrerait pas."""
        assert contenu_reel.articles()
        assert contenu_reel.institutions()

    def test_chaque_article_a_une_illustration_partageable(self, contenu_reel: ServiceContenu):
        """L'image sert de vignette sur Facebook et WhatsApp. Une image absente du
        dépôt public donnerait un lien partagé sans vignette — donc sans raison
        d'être cliqué."""
        public = RACINE_DEPOT / "Frontend_erp_cga" / "public"
        for article in contenu_reel.articles():
            chemin = public / article.image.lstrip("/")
            assert chemin.is_file(), f"{article.slug} : image introuvable — {article.image}"

    def test_aucun_appel_a_action_ne_pointe_hors_du_site(self, contenu_reel: ServiceContenu):
        """Un bouton d'article mène à une page du site. Une adresse absolue serait
        soit une faute de saisie, soit une sortie du site déguisée en action."""
        for article in contenu_reel.articles():
            if article.appel is not None:
                assert article.appel.href.startswith("/"), (
                    f"{article.slug} : « {article.appel.href} » n'est pas un chemin interne"
                )

    def test_les_annonces_pointent_vers_une_page_du_site(self, contenu_reel: ServiceContenu):
        for jour in (date(2026, 1, 1), date(2026, 6, 15), date(2026, 12, 31)):
            annonce = contenu_reel.annonce_du_jour(jour)
            if annonce is not None:
                assert annonce.lien.startswith("/")

    def test_un_fichier_manquant_dit_lequel(self, tmp_path: Path):
        """Le message doit désigner le fichier : la personne qui vient de corriger
        le YAML doit savoir quoi reprendre, sans lire une trace d'exécution."""
        depot = DepotContenuVitrineYaml(tmp_path)
        with pytest.raises(ContenuIllisible) as erreur:
            depot.charger_articles()
        assert "articles.yaml" in str(erreur.value)

    def test_une_rubrique_inconnue_fait_echouer_le_chargement(self, tmp_path: Path):
        """Mieux vaut un démarrage refusé qu'un article invisible parce que sa
        rubrique ne correspond à aucun filtre."""
        (tmp_path / "articles.yaml").write_text(
            'articles:\n  - slug: "x"\n    rubrique: "inventee"\n', encoding="utf-8"
        )
        with pytest.raises(ContenuIllisible):
            DepotContenuVitrineYaml(tmp_path).charger_articles()
