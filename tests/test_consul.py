"""Ce que nos quatorze services deviennent pour Consul, et les deux pièges de la traduction.

⚠️ **Rien ici n'appelle Consul.** Ces cas vérifient ce que le générateur produit, pas
ce qu'un agent en fait : monter un Consul dans la suite de tests remplacerait une
vérification rapide et déterministe par un montage lent et intermittent, pour
constater ce que la documentation de Consul dit déjà.

CE QUI SE VÉRIFIE DONC ICI

Les deux traductions qui peuvent se tromper sans que rien ne le signale : **le sens
du graphe**, que Consul déclare à l'envers du nôtre, et **le développement du
socle**, que notre déclaration laisse implicite. Une erreur sur l'une des deux
produit une configuration syntaxiquement valide qui refuse le trafic réel.
"""

from __future__ import annotations

from app.registre import SERVICES, autorises_pour
from app.registre.consul import (
    ESPACE_DE_NOMS,
    chemin_de_sante,
    definitions_de_services,
    intentions,
    nom_consul,
    politique_par_defaut,
)


def _sources_de(nom_du_service: str) -> set[str]:
    """Les sources admises vers ce service, lues dans les intentions engendrées."""
    for entree in intentions():
        if entree["Name"] == nom_du_service:
            return {source["Name"] for source in entree["Sources"]}
    return set()


class TestLesNoms:
    def test_ils_portent_l_espace_de_noms(self):
        """Une instance Consul sert souvent plusieurs applications, et « referentiel »
        tout court entrerait en collision avec le référentiel de n'importe qui."""
        assert nom_consul(SERVICES[0]).startswith(f"{ESPACE_DE_NOMS}-")

    def test_aucun_nom_ne_porte_de_tiret_bas(self):
        """⚠️ Consul emploie les noms de service dans son interface DNS, où le tiret
        bas n'est pas admis par la RFC 1123.

        `creation_entreprise` serait irrésolvable sous son nom Python, et la panne
        n'apparaîtrait qu'au premier appel entre services.
        """
        for service in SERVICES:
            assert "_" not in nom_consul(service)

    def test_les_noms_restent_uniques_apres_transformation(self):
        """Remplacer les tirets bas pourrait faire converger deux noms distincts."""
        noms = [nom_consul(s) for s in SERVICES]
        assert len(set(noms)) == len(noms)


class TestLesEnregistrements:
    def test_les_quatorze_s_enregistrent_meme_dans_un_seul_processus(self):
        """⚠️ Le point qui surprend, et le plus utile.

        Un agent Consul peut porter plusieurs services ; rien n'oblige à un
        processus par service. Consul montre donc la santé service par service alors
        que tout vit encore dans un processus, et l'extraction ne changera qu'une
        adresse.
        """
        definitions = definitions_de_services(adresse="10.0.0.4", port=8000)
        assert len(definitions) == 14
        assert {d["address"] for d in definitions} == {"10.0.0.4"}

    def test_chaque_service_a_son_propre_controle(self):
        """Un contrôle unique ferait tomber ou tenir les quatorze ensemble, ce qui
        est précisément l'information qu'on ne veut pas : « la plateforme est en
        panne » n'aide personne à décider quoi redémarrer."""
        chemins = {
            d["checks"][0]["http"] for d in definitions_de_services(adresse="a", port=1)
        }
        assert len(chemins) == 14

    def test_le_controle_vise_le_chemin_du_service(self):
        definitions = definitions_de_services(adresse="a", port=1)
        referentiel = next(d for d in definitions if d["name"] == "cga-referentiel")
        assert chemin_de_sante(SERVICES[0]) in referentiel["checks"][0]["http"]

    def test_sans_jeton_aucun_en_tete_n_est_invente(self):
        """⚠️ Un défaut ferait croire que la protection est levée.

        Sans jeton, les contrôles échouent en `401` et Consul dit les quatorze
        services critiques : c'est franc, et l'exploitant cherche le jeton. Un
        en-tête vide ou fabriqué laisserait chercher ailleurs.
        """
        definitions = definitions_de_services(adresse="a", port=1)
        assert "header" not in definitions[0]["checks"][0]

    def test_avec_jeton_l_en_tete_est_pose_sur_chaque_controle(self):
        definitions = definitions_de_services(adresse="a", port=1, jeton_de_sonde="s3cr")
        for definition in definitions:
            assert definition["checks"][0]["header"]["Authorization"] == ["Bearer s3cr"]

    def test_un_service_critique_n_est_pas_retire_tout_de_suite(self):
        """⚠️ Un service retiré disparaît de l'écran de l'exploitant au moment précis
        où il le cherche. « Service inconnu » est bien pire que « service en panne »."""
        definition = definitions_de_services(adresse="a", port=1)[0]
        assert definition["checks"][0]["deregister_critical_service_after"] == "72h"

    def test_les_prefixes_http_deviennent_des_tags(self):
        """C'est là-dessus que la passerelle routera. Un tag absent ne se rattrape
        pas côté requête."""
        definitions = definitions_de_services(adresse="a", port=1)
        souscription = next(d for d in definitions if d["name"] == "cga-souscription")
        assert "prefixe=/acquisition" in souscription["tags"]
        assert "prefixe=/souscription" in souscription["tags"]


class TestLesIntentions:
    def test_le_graphe_est_inverse(self):
        """⚠️ **Le piège central de ce module.**

        Notre graphe est déclaré source vers destinations : « la Comptabilité lit le
        Portefeuille ». Consul déclare l'inverse : une entrée par destination,
        listant ses sources admises.

        Engendrer sans inverser produirait des intentions syntaxiquement valides et
        sémantiquement retournées : le maillage refuserait **tout le trafic réel** en
        laissant passer celui qui n'existe pas.
        """
        # La Comptabilité lit le Portefeuille, jamais l'inverse.
        assert "cga-comptabilite" in _sources_de("cga-portefeuille")
        assert "cga-portefeuille" not in _sources_de("cga-comptabilite")

    def test_le_socle_est_developpe(self):
        """⚠️ **Le second piège, et le plus coûteux.**

        `ARETES_AUTORISEES` ne mentionne pas le socle : `autorises_pour` l'ajoute
        implicitement, parce que le recopier dans dix entrées ferait dix endroits où
        l'oublier.

        Un générateur qui lirait le dictionnaire brut produirait un maillage où plus
        personne ne peut lire le Référentiel, c'est-à-dire où plus aucun calcul
        fiscal, comptable ou social n'est possible.
        """
        from app.registre import ARETES_AUTORISEES

        assert "referentiel" not in ARETES_AUTORISEES["comptabilite"]
        assert "cga-comptabilite" in _sources_de("cga-referentiel")

    def test_toutes_les_aretes_du_registre_se_retrouvent(self):
        """Le cas qui rend les deux précédents inutiles à maintenir à la main.

        Il parcourt le graphe entier plutôt que deux exemples : une arête ajoutée
        demain sera couverte sans que personne y pense.
        """
        for service in SERVICES:
            for destination in autorises_pour(service.nom):
                cible = nom_consul(next(s for s in SERVICES if s.nom == destination))
                assert nom_consul(service) in _sources_de(cible), (
                    f"« {service.nom} » a le droit de lire « {destination} » et "
                    "l'intention engendrée ne le dit pas"
                )

    def test_aucune_intention_n_autorise_ce_que_le_registre_refuse(self):
        """La contre-épreuve. Sans elle, un générateur qui autoriserait tout le monde
        passerait le cas précédent."""
        for entree in intentions():
            cible = next(s for s in SERVICES if nom_consul(s) == entree["Name"])
            for source in entree["Sources"]:
                depart = next(s for s in SERVICES if nom_consul(s) == source["Name"])
                assert cible.nom in autorises_pour(depart.nom), (
                    f"l'intention autorise « {depart.nom} » à lire « {cible.nom} », "
                    "ce que le registre ne permet pas"
                )

    def test_un_service_que_personne_n_appelle_n_a_pas_d_entree(self):
        """Une entrée vide se lirait comme « tout est refusé », ce que la politique
        par défaut dit déjà, et elle le dirait deux fois."""
        assert _sources_de("cga-pilotage") == set()
        assert "cga-pilotage" not in {e["Name"] for e in intentions()}

    def test_chaque_source_porte_sa_justification(self):
        """Celui qui relit une intention dans l'interface de Consul doit savoir d'où
        elle vient, sans quoi il la supprimera « parce qu'elle a l'air en trop »."""
        for entree in intentions():
            for source in entree["Sources"]:
                assert "flux-fonctionnels" in source["Description"]

    def test_toutes_les_actions_sont_des_autorisations(self):
        """Le refus n'est jamais écrit ici : il vient de la politique par défaut.

        Mêler des `deny` explicites à une liste blanche donnerait deux mécanismes de
        refus, et la question « pourquoi cet appel est-il bloqué » aurait deux
        réponses possibles au lieu d'une.
        """
        for entree in intentions():
            assert {s["Action"] for s in entree["Sources"]} == {"allow"}


class TestLeRefusParDefaut:
    def test_le_maillage_permissif_est_interdit(self):
        """⚠️ Une liste d'autorisations sur un maillage permissif est une liste de
        commentaires.

        Ce n'est pas théorique : Consul admet les deux réglages, et le permissif est
        celui qui laisse une installation fonctionner pendant qu'on croit avoir
        cloisonné. C'est la même leçon que les politiques de sécurité au niveau des
        lignes du pas 12 — une règle parfaitement écrite qui ne s'applique à
        personne ne se signale jamais.
        """
        assert politique_par_defaut()["AllowEnablingPermissiveMutualTLS"] is False
