"""La recette de la production comptable : du justificatif à la déclaration.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Le dossier de conception dit ce qui achève la vague 2 : *« un adhérent dépose ses
justificatifs, ils sont contrôlés, imputés, et la déclaration se prépare. »*

Chaque maillon a ses cas. **Aucun ne vérifiait la chaîne.** C'est exactement l'état
dans lequel le parcours d'acquisition se trouvait avant sa propre recette, et cette
recette-là a trouvé quatre défauts de câblage que cent fichiers de domaine ne
pouvaient pas atteindre : des politiques de cloisonnement qui ne s'appliquaient à
personne, un gabarit de courriel réclamé sous un nom absent, un tenant payé dont la
table restait vide, une qualification jetée par sa propre route.

⚠️ **Ce fichier ne vérifie aucune règle métier.** C'est le travail des autres. Il
vérifie que **les pièces sont branchées entre elles**, sur une vraie base, par
HTTP, avec les comptes réels du jeu de démonstration.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import hashlib
import io
from decimal import Decimal

from sqlalchemy import text

from tests.conftest import exige_postgresql, ouvrir_une_session

pytestmark = exige_postgresql

#: L'adhérent qui dépose, et son dossier. Un compte réel : la recette doit
#: prouver que le client du cabinet peut faire ce qu'on attend de lui.
ADHERENT = "jp.nkoa@batimentplus.cm"
DOSSIER = "M081234567890P"

#: Le comptable du cabinet, qui contrôle, impute et valide.
COMPTABLE = "a.bouba@cga-brcg.cm"

#: ⚠️ Le journal des opérations diverses, et non celui des achats. Les cas de ce
#: fichier possèdent leurs écritures : partager le journal des achats avec les
#: autres fichiers ferait échouer l'export sur un brouillon qui n'est pas le nôtre.
JOURNAL = "OD"
EXERCICE = "2026"

PDF = b"%PDF-1.4\nrecette production\n%%EOF\n"


#: ⚠️ La fixture `plateforme` et l'ouverture de session vivent au `conftest` :
#: elles ont été écrites deux fois avant qu'une troisième ne les y renvoie.
_session = ouvrir_une_session


class TestLaChaineEntiere:
    """⚠️ **Un seul cas, et c'est délibéré.**

    Découper la chaîne en six cas indépendants demanderait de reconstruire l'état
    à chaque fois, donc de le fabriquer, donc de ne plus éprouver le câblage. Ce
    qui est vérifié ici est précisément ce qui relie les étapes.
    """

    def test_du_justificatif_depose_a_la_declaration_preparee(
        self, plateforme, moteur_test
    ):
        client = plateforme

        # ── 1 · L'adhérent dépose son justificatif ────────────────────────────
        #
        # ⚠️ Par l'API, avec son propre compte. C'est le maillon que le cabinet
        # ne doit pas avoir à saisir : un centre qui tape les factures de ses
        # adhérents n'a pas de produit, il a un service de saisie.
        _session(client, ADHERENT)
        depot = client.post(
            f"/collecte/fichiers?entreprise={DOSSIER}",
            files={"fichier": ("facture.pdf", io.BytesIO(PDF), "application/pdf")},
        )
        assert depot.status_code == 201, depot.text
        empreinte = depot.json()["empreinte"]
        assert empreinte == hashlib.sha256(PDF).hexdigest()

        piece = client.post(
            "/collecte/pieces",
            json={
                "entreprise": DOSSIER,
                "canal": "PORTAIL",
                "empreinte": empreinte,
                "reference_document": "F-RECETTE-001",
                "emetteur": "QUINCAILLERIE DU WOURI",
                "nom_fichier": "facture.pdf",
            },
        )
        assert piece.status_code == 201, piece.text
        reference_piece = piece.json()["piece"]["identifiant"]

        # ⚠️ Le système a posé ce que le client ne pose pas.
        assert piece.json()["piece"]["etat"] == "RECUE"
        assert piece.json()["piece"]["depose_par"] == "A-001"

        # ── 2 · La pièce est en base, et visible du cabinet ───────────────────
        with moteur_test.connect() as connexion:
            rangee = connexion.execute(
                text(
                    "SELECT entreprise, donnees->>'etat' AS etat "
                    "FROM piece_justificative WHERE identifiant = :p"
                ),
                {"p": reference_piece},
            ).one()
        assert rangee.entreprise == DOSSIER
        assert rangee.etat == "RECUE"

        # ⚠️ **La déclaration est lue maintenant**, avant toute écriture de ce
        # scénario. Sans ce point de départ, l'assertion de l'étape 7 serait
        # vide : « la déclaration n'est pas vide » passe même quand l'écriture
        # qu'on vient de saisir n'y figure pas.
        _session(client, COMPTABLE)
        avant = client.get(
            f"/obligations/dossiers/{DOSSIER}/declaration-tva"
            "?periode_debut=2026-01-01&periode_fin=2026-12-31"
        )
        assert avant.status_code == 200, avant.text
        tva_avant = Decimal(avant.json()["tva_deductible_theorique"])

        # ── 3 · Le comptable contrôle une facture et en tire une écriture ─────
        #
        # ⚠️ La facture du jeu de démonstration, dont le verdict est verrouillé
        # ailleurs : la recette relie ce verdict-là à cette écriture-là.
        _session(client, COMPTABLE)
        from app.contextes.conformite.adaptateurs.sortant.donnees_demo import (
            FACTURES_DEMO,
        )

        facture = FACTURES_DEMO["F-2026-0413"]
        proposition = client.post(
            f"/comptabilite/dossiers/{DOSSIER}/propositions",
            json={"facture": facture.model_dump(mode="json"), "journal": JOURNAL},
        )
        assert proposition.status_code == 200, proposition.text
        corps = proposition.json()
        assert corps["comptabilisable"] is True, corps["empechements"]
        saisie = corps["saisie"]

        # ⚠️ La pièce déposée à l'étape 1 est rattachée ici : c'est le maillon
        # de traçabilité que le contrôle fiscal remontera.
        saisie["piece_justificative"] = reference_piece

        # ── 4 · L'écriture est saisie telle que proposée ──────────────────────
        #
        # ⚠️ **Sans y toucher.** Une proposition que la route de saisie refuserait
        # ne vaudrait rien : le comptable la corrigerait, donc referait le travail
        # du moteur.
        enregistree = client.post(
            f"/comptabilite/dossiers/{DOSSIER}/ecritures", json=saisie
        )
        assert enregistree.status_code == 201, enregistree.text
        numero = enregistree.json()["numero"]
        assert enregistree.json()["etat"] == "BROUILLON"
        assert numero == corps["numero_pressenti"]

        # ⚠️ **Le brouillon ne compte pas encore.** Une écriture saisie n'engage
        # personne : elle peut être corrigée, reprise, supprimée. Si la
        # déclaration la comptait déjà, le cabinet déclarerait de la TVA sur un
        # travail que son comptable n'a pas terminé — et la corriger après dépôt
        # se paie en pénalités. Sans cette lecture intermédiaire, la mesure de
        # l'étape 7 passerait tout aussi bien si le filtre sur l'état
        # disparaissait, puisqu'on ne compare qu'un avant sans écriture du tout
        # à un après avec écriture validée.
        au_brouillon = client.get(
            f"/obligations/dossiers/{DOSSIER}/declaration-tva"
            "?periode_debut=2026-01-01&periode_fin=2026-12-31"
        )
        assert au_brouillon.status_code == 200, au_brouillon.text
        assert Decimal(au_brouillon.json()["tva_deductible_theorique"]) == tva_avant, (
            "la déclaration a bougé alors que rien n'est validé : elle lit les "
            "brouillons, donc elle déclare un travail en cours."
        )

        # ── 5 · Le comptable valide, et l'écriture devient immuable ───────────
        validee = client.post(
            f"/comptabilite/dossiers/{DOSSIER}/ecritures/"
            f"{EXERCICE}/{JOURNAL}/{numero}/validation",
            json={},
        )
        assert validee.status_code == 200, validee.text
        assert validee.json()["etat"] == "VALIDEE"
        # ⚠️ « Le Centre engage sa responsabilité : la validation est un acte
        # personnel, pas un changement d'état anonyme. »
        assert validee.json()["validee_par"] == "C-003"

        # ── 6 · La balance porte l'écriture, et elle est équilibrée ───────────
        sante = client.get(
            f"/comptabilite/dossiers/{DOSSIER}/sante?exercice={EXERCICE}"
        )
        assert sante.status_code == 200, sante.text
        assert sante.json()["equilibree"] is True

        balance = client.get(
            f"/comptabilite/dossiers/{DOSSIER}/balance?exercice={EXERCICE}"
        ).json()
        comptes = {ligne["compte"] for ligne in balance}
        assert comptes >= {ligne["compte"] for ligne in saisie["lignes"]}

        # ── 7 · La déclaration de TVA se prépare depuis ces écritures ─────────
        #
        # ⚠️ **C'est le « fini quand » de la vague 2.** Elle lit la comptabilité :
        # si l'écriture de l'étape 4 n'y était pas, la déclaration serait fausse
        # sans que rien ne le dise.
        declaration = client.get(
            f"/obligations/dossiers/{DOSSIER}/declaration-tva"
            "?periode_debut=2026-01-01&periode_fin=2026-12-31"
        )
        assert declaration.status_code == 200, declaration.text
        tva_apres = Decimal(declaration.json()["tva_deductible_theorique"])

        # ⚠️ **L'écart fait foi, pas la présence.** La TVA déductible doit avoir
        # augmenté de celle de l'écriture validée à l'étape 5 : c'est la seule
        # façon de prouver que la déclaration lit *ces* écritures-là.
        tva_de_l_ecriture = sum(
            Decimal(ligne["montant"])
            for ligne in saisie["lignes"]
            if ligne["compte"].startswith("445") and ligne["sens"] == "DEBIT"
        )
        assert tva_de_l_ecriture > 0, saisie["lignes"]
        assert tva_apres - tva_avant == tva_de_l_ecriture, (
            f"la déclaration n'a pas bougé de {tva_de_l_ecriture} : "
            f"{tva_avant} puis {tva_apres}. L'écriture validée n'y entre pas."
        )

        # ── 7 bis · Le contrôle de conformité pèse sur la déclaration ────────
        #
        # ⚠️ **C'est le maillon qui fait le métier d'un CGA**, et il n'était
        # mesuré nulle part de bout en bout. Une facture réglée en espèces
        # au-delà du plafond reste comptabilisable : la charge est bien engagée.
        # Mais sa TVA n'est pas récupérable. Ce refus naît dans le contexte D
        # (conformité), se pose sur la ligne comme attribut fiscal dans le
        # contexte C (comptabilité), et doit ressortir dans le contexte F
        # (obligations) à la ligne « TVA rejetée ».
        #
        # Trois contextes, trois équipes possibles, un seul chiffre. Si l'un des
        # trois se tait, la déclaration réclame à l'État une TVA que le contrôle
        # fiscal refusera, et le cabinet répond de ce chiffre-là.
        rejetee_avant = Decimal(declaration.json()["tva_rejetee"])
        admise_avant = Decimal(declaration.json()["tva_deductible_admise"])

        especes = FACTURES_DEMO["F-2026-0412"]
        seconde = client.post(
            f"/comptabilite/dossiers/{DOSSIER}/propositions",
            json={"facture": especes.model_dump(mode="json"), "journal": JOURNAL},
        )
        assert seconde.status_code == 200, seconde.text
        corps_especes = seconde.json()
        # ⚠️ Comptabilisable **malgré** le constat : le refus porte sur la TVA,
        # pas sur l'opération. Un moteur qui bloquerait ici empêcherait le
        # cabinet de tenir la comptabilité de son adhérent.
        assert corps_especes["comptabilisable"] is True, corps_especes["empechements"]

        saisie_especes = corps_especes["saisie"]
        saisie_especes["piece_justificative"] = reference_piece
        posee = client.post(
            f"/comptabilite/dossiers/{DOSSIER}/ecritures", json=saisie_especes
        )
        assert posee.status_code == 201, posee.text
        numero_especes = posee.json()["numero"]
        validee_especes = client.post(
            f"/comptabilite/dossiers/{DOSSIER}/ecritures/"
            f"{EXERCICE}/{JOURNAL}/{numero_especes}/validation",
            json={},
        )
        assert validee_especes.status_code == 200, validee_especes.text

        finale = client.get(
            f"/obligations/dossiers/{DOSSIER}/declaration-tva"
            "?periode_debut=2026-01-01&periode_fin=2026-12-31"
        ).json()

        tva_refusee = sum(
            Decimal(ligne["montant"])
            for ligne in saisie_especes["lignes"]
            if ligne["compte"].startswith("445") and ligne["sens"] == "DEBIT"
        )
        assert tva_refusee > 0, saisie_especes["lignes"]

        assert Decimal(finale["tva_rejetee"]) - rejetee_avant == tva_refusee, (
            f"la TVA rejetée n'a pas bougé de {tva_refusee} : {rejetee_avant} puis "
            f"{finale['tva_rejetee']}. Le constat du contexte D n'atteint pas la "
            "déclaration du contexte F."
        )
        # ⚠️ **Et le contre-champ.** Rejetée veut dire retirée : si la TVA admise
        # augmentait elle aussi, le rejet serait affiché sans être appliqué, ce
        # qui est pire que de ne pas l'afficher du tout.
        assert Decimal(finale["tva_deductible_admise"]) == admise_avant, (
            f"la TVA déductible admise est passée de {admise_avant} à "
            f"{finale['tva_deductible_admise']} : le rejet est annoncé mais pas "
            "déduit, et la déclaration réclame une TVA non récupérable."
        )
        # ⚠️ Le motif voyage avec le chiffre : un rejet sans motif est
        # indéfendable devant le vérificateur.
        motifs = {ligne["motif"] for ligne in finale["detail_rejets"]}
        assert any(motif for motif in motifs), finale["detail_rejets"]

        # ── 8 · Et le fichier part vers le logiciel du client ─────────────────
        #
        # ⚠️ Le dernier maillon : le cabinet ne retient pas les écritures de son
        # adhérent. L'export ne rend que les écritures **validées**, donc celle
        # de l'étape 5 et pas les brouillons des autres.
        export = client.get(
            f"/comptabilite/dossiers/{DOSSIER}/export"
            f"?exercice={EXERCICE}&profil=sage-ligne100&journal={JOURNAL}"
        )
        assert export.status_code == 200, export.text
        texte = export.content.decode("cp1252")
        assert str(numero) in texte
        assert "\r\n" in texte


class TestLesMaillonsQuiDoiventRompre:
    """⚠️ La contre-épreuve de la chaîne : ce qui doit **refuser** de passer.

    Sans ces cas, une chaîne qui laisserait tout passer réussirait le scénario
    précédent sans rien garantir.
    """

    def test_une_facture_bloquante_ne_produit_aucune_ecriture(self, plateforme):
        """Une anomalie bloquante interdit la comptabilisation, et le refus
        nomme la règle : sans elle, le comptable saisirait à la main."""
        client = plateforme
        _session(client, COMPTABLE)
        from app.contextes.conformite.adaptateurs.sortant.donnees_demo import (
            FACTURES_DEMO,
        )

        # ⚠️ Dans le dossier de son destinataire : proposée ailleurs, elle est refusée
        # avant tout contrôle depuis le pas 73, et ce cas ne mesurerait plus le blocage.
        bloquante = FACTURES_DEMO["F-2026-0414"]
        reponse = client.post(
            f"/comptabilite/dossiers/{bloquante.destinataire.niu}/propositions",
            json={
                "facture": bloquante.model_dump(mode="json"),
                "journal": JOURNAL,
            },
        )
        assert reponse.status_code == 200, reponse.text
        assert reponse.json()["comptabilisable"] is False
        assert reponse.json()["saisie"] is None
        assert "FAC-ID-003" in reponse.json()["empechements"][0]

    def test_un_brouillon_ne_part_pas_chez_le_client(self, plateforme):
        """⚠️ Un brouillon n'est pas engagé par le centre. L'exporter le ferait
        tenir pour arrêté par le client, qui déclare avec."""
        client = plateforme
        _session(client, COMPTABLE)
        saisie = {
            "journal": JOURNAL,
            "exercice": EXERCICE,
            "date_operation": "2026-07-02",
            "libelle": "Un brouillon qui ne doit pas sortir",
            "piece_justificative": "PJ-RECETTE-BROUILLON",
            "lignes": [
                {"compte": "622", "libelle": "Charge", "sens": "DEBIT", "montant": "1000"},
                {"compte": "571", "libelle": "Caisse", "sens": "CREDIT", "montant": "1000"},
            ],
        }
        assert (
            client.post(
                f"/comptabilite/dossiers/{DOSSIER}/ecritures", json=saisie
            ).status_code
            == 201
        )

        export = client.get(
            f"/comptabilite/dossiers/{DOSSIER}/export"
            f"?exercice={EXERCICE}&profil=pivot-csv&journal={JOURNAL}"
        )
        assert export.status_code == 409, export.text
        assert "non validée" in export.json()["detail"]

    def test_l_adherent_ne_voit_pas_la_comptabilite(self, plateforme):
        """⚠️ Il dépose ses pièces et lit son dossier ; il ne lit pas le grand
        livre que le cabinet tient pour lui.

        C'est la frontière qui sépare un espace client d'un accès au cabinet.
        """
        client = plateforme
        _session(client, ADHERENT)
        reponse = client.get(
            f"/comptabilite/dossiers/{DOSSIER}/balance?exercice={EXERCICE}"
        )
        assert reponse.status_code == 403, reponse.text

    def test_un_adherent_ne_depose_pas_chez_un_autre(self, plateforme):
        """La seule des faiblesses possibles qui exposerait les données d'un
        autre cabinet."""
        client = plateforme
        _session(client, ADHERENT)
        depot = client.post(
            "/collecte/fichiers?entreprise=M065544332211L",
            files={"fichier": ("f.pdf", io.BytesIO(PDF), "application/pdf")},
        )
        assert depot.status_code in (403, 404), depot.text
