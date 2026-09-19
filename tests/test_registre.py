"""Le registre des services : trois états, jamais fondus, et jamais déclarés à la main.

⚠️ **Ce que ce fichier protège avant tout, c'est la distinction entre les trois
états.** Les mêler produit un registre qui ment dans les deux sens : un service
complet dont la base est tombée passerait pour incomplet, et un service à peine
commencé mais dont le processus tourne passerait pour opérationnel. Le second est le
plus coûteux, parce qu'il fait croire qu'une fonctionnalité existe.

Le reste tient en une phrase : **rien de ce qui peut être constaté n'est déclaré.**
L'état de construction se lit sur le disque et sur l'application montée, l'état
d'exécution vient d'une sonde, et l'état de dépendance se calcule sur le graphe. Ce
qui était de la prose recopiée dans un document dérivait ; ce qui est constaté ne le
peut pas.
"""

from __future__ import annotations

import threading

import pytest

from app.registre import (
    ARETES_AUTORISEES,
    SERVICES,
    SOCLE,
    autorises_pour,
    dependances_transitives,
    qui_tombe_avec,
    service,
)
from app.registre.etat import (
    EtatDeConstruction,
    EtatDExecution,
    Verdict,
    construction_de,
    fiche_de,
    fiches,
    joignables_selon,
)


class TestLaDeclaration:
    def test_les_quatorze_services_sont_declares(self):
        assert len(SERVICES) == 14

    def test_un_nom_inconnu_leve_plutot_que_de_rendre_none(self):
        """Un nom inconnu ici est une faute de frappe dans du code, pas une donnée
        absente. Rendre `None` la ferait voyager jusqu'à un `AttributeError` loin de
        sa cause."""
        with pytest.raises(KeyError, match="service inconnu"):
            service("comptabilte")

    def test_le_message_d_erreur_donne_les_noms_valides(self):
        """Celui qui s'est trompé d'une lettre doit voir la bonne, pas aller la chercher."""
        try:
            service("comptabilte")
        except KeyError as erreur:
            assert "comptabilite" in str(erreur)


class TestLEtatDeConstruction:
    def test_il_est_constate_et_non_declare(self):
        """Aucun champ de `Service` ne porte l'état de construction.

        ⚠️ Ce cas garde la propriété qui compte. L'écrire à la main aurait été plus
        simple, et il aurait dérivé — comme a dérivé le tableau « ce qui tourne
        aujourd'hui » du document de conception, qui était de la prose recopiée.
        Un service dont on retire les routes redevient « en construction » sans que
        personne y pense.
        """
        champs = set(type(SERVICES[0]).model_fields)
        assert "construction" not in champs
        assert "etat" not in champs

    def test_en_service_exige_des_routes_ET_un_domaine(self, tmp_path, monkeypatch):
        """Des routes sans règles derrière elles ne sont pas un service.

        ─────────────────────────────────────────────────────────────────────────
        C'est une façade sur du vide, et l'annoncer « en service » ferait croire
        qu'une fonctionnalité existe : le plus coûteux des deux mensonges possibles.

        ⚠️ **Le cas emploie un service fabriqué, et il le doit.** Aucun des quatorze
        services réels n'expose de routes sans domaine, donc aucun ne distingue les
        deux implémentations : une mutation retirant l'exigence de domaine a survécu
        pour cette seule raison. Un cas ne peut mesurer un garde que s'il existe une
        situation où le garde change quelque chose.
        ─────────────────────────────────────────────────────────────────────────
        """
        import app.registre.etat as module

        faux = tmp_path / "facade"
        (faux / "adaptateurs" / "entrant").mkdir(parents=True)
        (faux / "adaptateurs" / "entrant" / "routes_http.py").write_text("")
        monkeypatch.setattr(module, "CONTEXTES_DIR", tmp_path)

        facade = service("referentiel").model_copy(update={"nom": "facade"})
        assert construction_de(facade, joignable=True) is EtatDeConstruction.DECLARE

        (faux / "domaine").mkdir()
        (faux / "domaine" / "regles.py").write_text("")
        assert construction_de(facade, joignable=True) is EtatDeConstruction.EN_SERVICE

    def test_un_service_reel_sans_routes_montees_n_est_pas_en_service(self):
        """Le repli sur le disque, sur un service réel."""
        referentiel = service("referentiel")
        assert construction_de(referentiel, joignable=True) is EtatDeConstruction.EN_SERVICE
        assert construction_de(referentiel, joignable=False) is not (
            EtatDeConstruction.EN_SERVICE
        )

    def test_un_service_sans_routes_montees_n_est_pas_en_service(self):
        """Le cas réel des Tenants : ils sont pilotés par le Transverse et par
        l'ordonnanceur, et n'exposent aucune route de leur propre chef. C'est un
        fait, pas un oubli, et le registre doit le montrer plutôt que le taire."""
        assert construction_de(service("tenants"), joignable=False) is (
            EtatDeConstruction.CAS_D_USAGE
        )


class TestLaJoignabilite:
    def test_un_prefixe_expose_rend_le_service_joignable(self):
        joignables = joignables_selon(["/referentiel/parametres", "/sante"])
        assert joignables["referentiel"] is True
        assert joignables["comptabilite"] is False

    def test_un_prefixe_n_attrape_pas_un_chemin_qui_commence_pareil(self):
        """⚠️ `/souscription` ne doit pas se reconnaître dans `/souscriptions-tierces`.

        Sans la borne sur le séparateur, un préfixe court attraperait tout ce qui
        commence par lui, et le registre déclarerait joignables des services qui ne
        le sont pas — l'erreur la plus difficile à voir, puisqu'elle affiche du vert.
        """
        joignables = joignables_selon(["/souscriptions-tierces/tout"])
        assert joignables["souscription"] is False

    def test_le_chemin_exactement_egal_au_prefixe_compte(self):
        assert joignables_selon(["/vitrine"])["vitrine"] is True

    def test_un_service_sans_prefixe_n_est_jamais_joignable(self):
        """Les Tenants n'en déclarent aucun : aucun chemin ne peut les rendre joignables."""
        assert joignables_selon(["/tenants", "/transverse"])["tenants"] is False


class TestLEtatDExecution:
    def test_une_sonde_qui_rend_none_dit_que_le_service_repond(self):
        fiche = fiche_de("referentiel", {"referentiel": lambda: None})
        assert fiche.execution.etat is EtatDExecution.REPOND
        assert fiche.execution.motif is None

    def test_un_service_sans_sonde_le_dit_au_lieu_de_se_declarer_sain(self):
        """⚠️ Distinct de `REPOND` à dessein.

        Un registre qui répond « tout va bien » pour un service qu'il n'interroge
        pas est exactement la sonde complaisante que le pas 12 a supprimée : il
        affirme précisément la chose qu'il ne sait pas.
        """
        assert fiche_de("comptabilite", {}).execution.etat is EtatDExecution.SANS_SONDE

    def test_une_sonde_qui_leve_est_rattrapee_et_le_registre_repond_quand_meme(self):
        """Ce n'est pas théorique : la première sonde des Tenants appelait une
        méthode inexistante. Le registre l'a marquée en panne et a rendu les treize
        autres fiches, au lieu de rendre une pile d'appels.

        Une sonde mal écrite ne doit pas priver l'exploitant du registre au moment
        précis où il en a besoin.
        """

        def casse() -> Verdict | None:
            raise AttributeError("'Repertoire' object has no attribute 'tous'")

        inventaire = fiches({"tenants": casse})
        assert len(inventaire) == 14
        fiche = next(f for f in inventaire if f.service.nom == "tenants")
        assert fiche.execution.etat is EtatDExecution.EN_PANNE
        assert "AttributeError" in fiche.execution.motif

    def test_les_sondes_sont_appelees_une_fois_chacune(self):
        """⚠️ Les appeler au fil des fiches ferait sonder le Référentiel douze fois
        par consultation, une fois par service qui en dépend. À trois sondes c'est
        invisible ; à trente, la page de diagnostic devient elle-même une charge."""
        appels = []
        fiches({"referentiel": lambda: appels.append(1)})
        assert len(appels) == 1


class TestLesAppuisTombes:
    def test_un_appui_en_panne_marque_ses_dependants(self):
        """La sonde du service dit qu'il tourne, le registre dit qu'il ne peut rien
        faire d'utile. C'est ce qui distingue un registre d'une liste de sondes."""
        inventaire = fiches({"referentiel": lambda: Verdict.panne("dossier introuvable")})
        comptabilite = next(f for f in inventaire if f.service.nom == "comptabilite")
        assert "referentiel" in comptabilite.appuis_tombes
        assert comptabilite.a_un_probleme

    def test_un_appui_suspect_n_entraine_personne(self):
        """⚠️ Le cas qui a fait naître le niveau `SUSPECT`.

        La sonde des Tenants signale un répertoire vide, ce qui trahit un garnissage
        échoué neuf fois sur dix et qui est parfaitement normal sur une installation
        neuve. Rendue `EN_PANNE`, elle marquait **treize services** comme ayant un
        appui tombé sur une base vierge.

        Une sonde qui crie au loup finit ignorée, et le jour où elle a raison
        personne ne regarde.
        """
        inventaire = fiches({"tenants": lambda: Verdict.suspect("répertoire vide")})
        assert [f.service.nom for f in inventaire if f.appuis_tombes] == []
        tenants = next(f for f in inventaire if f.service.nom == "tenants")
        assert tenants.execution.etat is EtatDExecution.SUSPECT
        assert not tenants.a_un_probleme

    def test_un_service_sain_dont_un_appui_est_tombe_a_un_probleme(self):
        """Sa propre sonde ne suffit pas à le déclarer sain."""
        inventaire = fiches(
            {
                "referentiel": lambda: Verdict.panne("vide"),
                "comptabilite": lambda: None,
            }
        )
        comptabilite = next(f for f in inventaire if f.service.nom == "comptabilite")
        assert comptabilite.execution.etat is EtatDExecution.REPOND
        assert comptabilite.a_un_probleme


class TestLEtatDeDependance:
    def test_le_graphe_n_est_plus_plat_et_voici_ou(self):
        """Un constat, et le jour qu'il annonçait est arrivé (pas 56).

        ─────────────────────────────────────────────────────────────────────────
        Ce cas s'appelait « le graphe d'aujourd'hui est plat » : aucun service n'avait
        de dépendance indirecte seule, et remplacer la fermeture transitive par les
        voisins directs n'aurait rien changé. Il était écrit pour se signaler le jour
        où ce serait faux, parce que c'est ce jour-là que la version directe
        commencerait à sous-déclarer en silence.

        **Ce jour est le pas 56.** Les Obligations lisent le Social pour une seule
        question, la présence de salariés. La Clôture lit les Obligations, et ne lit
        pas le Social : elle en dépend désormais **indirectement seulement**. Arrêter
        le Social, c'est toucher la Clôture sans arête entre eux, et la fermeture
        transitive, déjà en place, le dit.

        ⚠️ **Une limite du registre, écrite ici.** La lecture du personnel par les
        Obligations ne bloque pas : le Social en panne laisse l'échéancier entier,
        obligations sociales « à confirmer ». Le registre ne distingue pas une arête
        qui dégrade d'une arête qui fait tomber, et il sur-déclare donc l'effet de
        l'arrêt du Social. C'est le bon sens de l'erreur pour un exploitant, et c'est
        une nuance que le registre ne sait pas encore porter.
        ─────────────────────────────────────────────────────────────────────────
        """
        indirectes = {
            s.nom: dependances_transitives(s.nom) - autorises_pour(s.nom) for s in SERVICES
        }
        assert {nom: cibles for nom, cibles in indirectes.items() if cibles} == {
            "cloture": {"social"}
        }, "le graphe a encore changé : ce cas doit être relu"

    def test_le_social_entraine_la_cloture_sans_arete_directe(self):
        """La fermeture transitive, éprouvée sur le graphe réel et non plus greffé."""
        assert "social" not in autorises_pour("cloture")
        assert {"obligations", "cloture", "pilotage"} == qui_tombe_avec("social")

    def test_qui_tombe_avec_prend_la_fermeture_et_non_les_voisins(self, monkeypatch):
        """La propriété, éprouvée sur un graphe où elle se voit.

        ⚠️ Elle ne se voit pas sur le graphe réel, qui est plat — voir le cas
        ci-dessus. Un chaînon est donc introduit ici : `souscription` ne lit que le
        portefeuille, on lui fait lire la comptabilité, qui lit la collecte. La
        Souscription doit alors tomber avec la Collecte, **sans arête directe entre
        elles**.

        Sans la fermeture, un exploitant lirait « la Collecte est tombée, deux
        services en dépendent » là où il y en a trois. C'est une fausse
        tranquillité, et elle se paie au moment où l'on décide s'il faut réveiller
        quelqu'un.
        """
        import app.registre.services as module

        greffe = {nom: set(cibles) for nom, cibles in ARETES_AUTORISEES.items()}
        greffe["souscription"] = {"comptabilite"}
        monkeypatch.setattr(module, "ARETES_AUTORISEES", greffe)

        assert "collecte" not in greffe["souscription"]
        assert "souscription" in qui_tombe_avec("collecte")

    def test_le_pilotage_n_entraine_personne(self):
        """Il lit tout le monde et personne ne le lit. Son arrêt n'interrompt aucune
        production, et c'est ce qui décide s'il faut réveiller quelqu'un."""
        assert qui_tombe_avec("pilotage") == set()

    def test_le_referentiel_entraine_presque_tout(self):
        """Douze services sur treize. C'est la réponse qui manquait à « faut-il
        réveiller quelqu'un ? »."""
        assert len(qui_tombe_avec("referentiel")) == 12

    def test_un_service_ne_depend_jamais_de_lui_meme(self):
        for s in SERVICES:
            assert s.nom not in dependances_transitives(s.nom)

    def test_le_socle_est_ajoute_implicitement_hors_du_socle(self):
        """Le recopier dans chaque entrée ferait dix entrées identiques où une seule
        oubliée passerait inaperçue, et rendrait illisible ce qui est une vraie
        dépendance métier."""
        assert SOCLE <= autorises_pour("comptabilite")
        assert autorises_pour("referentiel") == set()

    def test_le_parcours_ne_boucle_pas_sur_un_cycle(self, monkeypatch):
        """⚠️ Un registre qui fige le service est pire qu'un registre qui se trompe.

        Le graphe est acyclique et le test d'architecture le garde ainsi. Mais une
        fonction qui bouclerait indéfiniment sur un cycle transformerait une erreur
        de déclaration en processus qui ne rend jamais la main — et c'est le registre
        qu'on consulte quand plus rien ne va.
        """
        import app.registre.services as module

        cycle = {nom: set(cibles) for nom, cibles in ARETES_AUTORISEES.items()}
        cycle["portefeuille"] = {"comptabilite"}
        monkeypatch.setattr(module, "ARETES_AUTORISEES", cycle)

        # ⚠️ **Interrogé depuis un tiers, et non depuis un membre du cycle.**
        # Demander à `comptabilite` ne prouverait rien : le garde « courant == nom »
        # coupe déjà un cycle qui repasse par le point de départ. C'est un cycle
        # *étranger* au service interrogé qui fait boucler, et c'est le cas
        # dangereux — le pilotage lit tout le monde sans être dans aucune boucle.
        #
        # ⚠️ **Et l'appel est borné par un fil.** Sans cela, un parcours qui boucle
        # fait *pendre* la suite au lieu de l'échouer : la mutation retirant la
        # mémoire des visités a d'abord été tuée par un blocage de quarante-cinq
        # secondes. Un test qui pend ne dit pas ce qui ne va pas, immobilise la
        # chaîne d'intégration, et pousse à ne plus la regarder.
        resultat: dict[str, set[str]] = {}
        fil = threading.Thread(
            target=lambda: resultat.update(deps=dependances_transitives("pilotage")),
            daemon=True,
        )
        fil.start()
        fil.join(timeout=5.0)

        assert not fil.is_alive(), (
            "le parcours ne rend pas la main : il boucle sur le cycle. Un registre "
            "qui fige le service est pire qu'un registre qui se trompe."
        )
        assert {"portefeuille", "comptabilite"} <= resultat["deps"]


class TestLesTroisEtatsRestentDistincts:
    def test_la_fiche_les_porte_separement(self):
        """⚠️ Un champ unique mentirait dans les deux sens.

        Un service complet dont la base est tombée serait « incomplet ». Un service
        à peine commencé mais dont le processus tourne serait « opérationnel ».
        """
        fiche = fiche_de("referentiel", {"referentiel": lambda: Verdict.panne("vide")})
        assert fiche.construction is EtatDeConstruction.EN_SERVICE
        assert fiche.execution.etat is EtatDExecution.EN_PANNE
        assert fiche.entraine

    def test_une_panne_ne_change_pas_l_etat_de_construction(self):
        sain = fiche_de("referentiel", {})
        casse = fiche_de("referentiel", {"referentiel": lambda: Verdict.panne("vide")})
        assert sain.construction is casse.construction

class TestLesSondesInscrites:
    """⚠️ **Le sens s'inverse une troisième fois, et il faut le garder.**

    Les sondes vivaient dans le module du registre. Trois y étaient, dont celle du
    Référentiel — un contexte que le graphe autorise au Transverse, ce qui rendait la
    faute légale et invisible. La quatrième aurait été refusée par le test
    d'architecture, et l'on aurait découvert à ce moment-là qu'il fallait tout
    déplacer.
    """

    def test_une_sonde_inscrite_est_rendue(self):
        from app.registre.etat import Verdict
        from app.registre.inscription import (
            inscrire_une_sonde,
            oublier_les_sondes,
            sondes_inscrites,
        )

        oublier_les_sondes()
        try:
            inscrire_une_sonde("referentiel", lambda: Verdict.panne("vide"))
            assert set(sondes_inscrites()) == {"referentiel"}
        finally:
            oublier_les_sondes()

    def test_reinscrire_remplace_et_n_empile_pas(self):
        """⚠️ Deux sondes pour un service poseraient une question sans réponse :
        laquelle fait foi quand elles divergent ?

        Contrairement aux abonnés, où plusieurs écoutes du même événement sont
        légitimes, une sonde répond seule de l'état d'un service.
        """
        from app.registre.inscription import (
            inscrire_une_sonde,
            oublier_les_sondes,
            sondes_inscrites,
        )

        oublier_les_sondes()
        try:
            inscrire_une_sonde("referentiel", lambda: None)
            inscrire_une_sonde("referentiel", lambda: None)
            assert len(sondes_inscrites()) == 1
        finally:
            oublier_les_sondes()

    def test_toute_sonde_inscrite_vise_un_service_declare(self):
        """⚠️ **Le garde-fou qui manquait.**

        Le module d'inscription ne vérifie pas les noms : aller chercher la liste des
        services lui ferait importer la déclaration, donc créer le cycle qu'on évite.
        C'est donc ici que la correspondance se garde.

        Une sonde inscrite sous « refentiel » ne serait jamais appelée, le service
        resterait `SANS_SONDE`, et rien ne dirait pourquoi.
        """
        from app.main import creer_application
        from app.registre.inscription import sondes_inscrites

        creer_application()
        inconnus = set(sondes_inscrites()) - {s.nom for s in SERVICES}
        assert not inconnus, (
            f"ces sondes visent des services qui n'existent pas : {sorted(inconnus)}. "
            "Elles ne seraient jamais appelées, et le service resterait SANS_SONDE."
        )

    def test_la_composition_inscrit_les_sondes_attendues(self):
        """La contre-épreuve du cas précédent.

        Sans elle, un `_inscrire_les_sondes` vidé passerait : aucune sonde inscrite
        ne vise un service inconnu, trivialement.
        """
        from app.main import creer_application
        from app.registre.inscription import sondes_inscrites

        creer_application()
        assert len(sondes_inscrites()) >= 8, (
            f"la composition n'inscrit plus que {len(sondes_inscrites())} sonde(s) : "
            "le registre affichera SANS_SONDE pour des services qui en ont une"
        )
