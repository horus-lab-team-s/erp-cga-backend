"""Chaque service du catalogue, de la demande jusqu'où il s'arrête.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Le catalogue annonce six services. Les tests existants éprouvent la création en
détail, l'adhésion par endroits, et la paie depuis son ajout. Personne ne
vérifiait **les six d'un coup**, ni jusqu'où chacun va réellement.

Or un service qui entre par la porte sans questionnaire ni prix produit un
dossier **sans issue** : il se dépose, il s'affecte, un responsable s'en occupe,
et il ne devient jamais une proposition. Il ne se voit pas pour autant, il
ressemble à un dossier qui attend. C'est le défaut que
`_exiger_un_service_du_catalogue` empêche à l'entrée, et que rien ne surveillait
ensuite.

CE QUE CE FICHIER MESURE

Il joue la chaîne pour chaque service, du dépôt au chiffrage, et consigne
**l'étape la plus avancée atteinte**. Puis il la confronte à un tableau déclaré.

⚠️ LE TABLEAU EST FERMÉ, ET C'EST SON OBJET. Un service qui avance ou qui recule
fait rougir le cas, ce qui oblige à la décision plutôt qu'à la découverte. Il
n'affirme pas que tout doit aller jusqu'au bout : trois services se traitent à la
main aujourd'hui, et c'est un choix. Il affirme que **nous savons lesquels**.

⚠️ LES RÉPONSES NE SONT PAS RECOPIÉES. Elles sont fabriquées à partir de ce que
le questionnaire déclare, type par type. Un questionnaire qui gagne une question
n'oblige donc pas à toucher ce fichier, et surtout une réponse ne peut pas y
devenir périmée en silence.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from tests.conftest import exige_postgresql, ouvrir_une_session

pytestmark = exige_postgresql

RESPONSABLE = "b.mballa@cga-brcg.cm"

#: L'étape la plus avancée que chaque service atteint aujourd'hui, et pourquoi.
#:
#: `CHIFFRE` veut dire qu'un prix sort tout seul, sur les réponses enregistrées.
#: `EN_CONVERSATION` veut dire que le dossier s'arrête là : le responsable doit
#: traiter la suite hors du logiciel.
ARRETS_ATTENDUS: dict[str, tuple[str, str]] = {
    "CREATION": ("CHIFFREE", "questionnaire de dix questions, barème au référentiel"),
    "ADHESION": ("CHIFFREE", "questionnaire de neuf questions, barème au référentiel"),
    "PAIE": ("CHIFFREE", "questionnaire de sept questions, barème au référentiel"),
    # ⚠️ Ces trois-là n'ont NI questionnaire NI barème. Ils ne peuvent se chiffrer
    # que sur un **prix ferme arrêté par la direction**, et aucun ne l'est encore :
    # les six tarifs du catalogue sont au statut `A_VALIDER`. Tant qu'ils y
    # restent, le dossier s'arrête à la conversation.
    "DOMICILIATION": ("EN_CONVERSATION", "aucun questionnaire, prix non arrêté"),
    "FORMATION": ("EN_CONVERSATION", "aucun questionnaire, prix non arrêté"),
    "PONCTUEL": ("EN_CONVERSATION", "aucun questionnaire, prix sur étude"),
    # 19 octobre 2026 (point 24) : la DSF achetée en ligne. Demandée par le formulaire de
    # contact, elle n'a ni questionnaire ni prix arrêté : elle s'arrête à la conversation,
    # comme la domiciliation, jusqu'à ce que la direction fixe ses deux tranches.
    "DSF": ("EN_CONVERSATION", "aucun questionnaire, prix non arrêté"),
}


def _valeur_pour(question: dict) -> object:
    """Une réponse plausible, fabriquée sur le type déclaré.

    ⚠️ Fabriquée et non recopiée : voir l'en-tête. Le but n'est pas d'éprouver le
    calcul du prix, que d'autres fichiers font en détail, mais de vérifier que la
    chaîne **passe**.
    """
    match question["type"]:
        case "ENUM":
            valeurs = question.get("valeurs") or []
            assert valeurs, f"question ENUM sans valeurs : {question['code']}"
            return valeurs[0]
        case "ENTIER":
            return 3
        case "DECIMAL":
            return "1000000"
        case "BOOLEEN":
            return False
        case "DATE":
            return "2026-11-02"
        case _:
            return "valeur d'épreuve"


def _jouer(client, service: str, telephone: str) -> str:
    """Joue la chaîne et rend l'état le plus avancé atteint."""
    depot = client.post(
        "/acquisition/demandes",
        json={
            "nom": f"Épreuve {service}",
            "telephone": telephone,
            "service_souhaite": service,
            "canal_prefere": "APPEL",
            "message": f"épreuve de bout en bout du service {service}",
        },
    )
    assert depot.status_code in (200, 201), f"{service} refusé à la porte : {depot.text}"

    (dossier,) = [
        d for d in client.get("/acquisition/dossiers").json() if d["etat"] == "DEPOSEE"
    ]
    reference = dossier["reference"]

    affecte = client.post(f"/acquisition/dossiers/{reference}/affectation", json={})
    assert affecte.json()["affecte"] is True, affecte.text

    contact = client.post(
        f"/acquisition/dossiers/{reference}/premier-contact",
        json={"compte_rendu": "Premier échange mené, le besoin est cerné."},
    )
    assert contact.status_code == 200, f"{service} : premier contact refusé {contact.text}"

    questionnaire = client.get(f"/acquisition/questionnaires/{service}")
    if questionnaire.status_code == 200:
        questions = questionnaire.json()["questions"]
        reponses = [
            {"code": q["code"], "valeur": _valeur_pour(q)}
            for q in questions
            if q.get("obligatoire")
        ]
        qualifie = client.post(
            f"/acquisition/dossiers/{reference}/qualification",
            json={"reponses": reponses},
        )
        assert qualifie.status_code == 200, f"{service} : qualification {qualifie.text}"

    # ⚠️ LE CODE DU CHIFFRAGE EST VÉRIFIÉ, PAS SEULEMENT L'ÉTAT D'ARRIVÉE.
    #
    # Un service qui ne se chiffre pas doit le dire par un refus lisible — 404 ou
    # 409 — jamais par une panne. Mesuré le 6 octobre : l'ADHÉSION rendait **500**,
    # parce que son tarif se décline en trois tranches de chiffre d'affaires et que
    # la recherche d'un prix ferme ne le prévoyait pas. Le service le plus vendu du
    # cabinet, et aucun écran ne le montrait.
    chiffrage = client.post(
        f"/acquisition/dossiers/{reference}/chiffrage", json={"debours": []}
    )
    assert chiffrage.status_code < 500, (
        f"{service} : le chiffrage tombe en {chiffrage.status_code}. "
        "Un service qui ne se chiffre pas doit refuser en nommant la raison, "
        f"jamais tomber en panne. {chiffrage.text[:300]}"
    )
    return client.get(f"/acquisition/dossiers/{reference}").json()["etat"]


class TestDeLaDemandeAuPrix:
    def test_chaque_service_du_catalogue_va_ou_l_on_croit(self, plateforme):
        from app.contextes.souscription.api import CATALOGUE

        client = plateforme
        ouvrir_une_session(client, RESPONSABLE)

        codes = sorted(s.code for s in CATALOGUE)
        assert codes == sorted(ARRETS_ATTENDUS), (
            "le catalogue a changé sans que ce tableau suive.\n"
            f"  catalogue : {codes}\n"
            f"  tableau   : {sorted(ARRETS_ATTENDUS)}\n"
            "Un service ajouté doit déclarer jusqu'où il va, faute de quoi il "
            "produira des dossiers sans issue que personne ne verra."
        )

        constate: dict[str, str] = {}
        for rang, code in enumerate(codes):
            constate[code] = _jouer(client, code, f"69900{rang:04d}")

        attendu = {code: arret for code, (arret, _) in ARRETS_ATTENDUS.items()}
        ecarts = {
            code: (attendu[code], constate[code])
            for code in codes
            if attendu[code] != constate[code]
        }
        assert not ecarts, (
            "des services ne s'arrêtent plus où le tableau le dit "
            f"(attendu, constaté) : {ecarts}"
        )
