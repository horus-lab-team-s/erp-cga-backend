"""Les missions des prestations ponctuelles : l'agent propose, le juriste décide.

─────────────────────────────────────────────────────────────────────────────────
DEMANDE DU CABINET (14-15 octobre 2026) : l'analyse et la rédaction de contrats, et
la DSF ciblée, « avec des outils et un agent, l'humain dans la boucle ».

⚠️ CE QUI EST GARDÉ, ET OÙ UNE ERREUR COÛTERAIT

* rien ne se soumet tant qu'une proposition attend une décision, ni sans point
  retenu ; une retenue porte son texte, une écartée son motif ;
* le livrable ne porte que les points retenus, dans les mots du juriste, et
  « PROJET » tant que la mission n'est pas livrée ;
* le valideur n'est jamais celui qui a soumis ; après soumission, plus rien ne
  bouge sans renvoi ;
* l'agent ne lève jamais : refus, troncature, réseau rendent un incident ;
* sans clé, aucun document ne sort (503) ;
* une mission ne s'ouvre sur un dossier commercial que s'il est une prestation
  ponctuelle PAYÉE ;
* chacun son geste : le juriste instruit, le réviseur ou la direction valide.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import base64
import json
import re
import zlib
from datetime import datetime
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.contextes.souscription.adaptateurs.sortant.analyse_claude import AnalyseClaude
from app.contextes.souscription.adaptateurs.sortant.document_mission import rendre_le_livrable
from app.contextes.souscription.domaine.missions import (
    DecisionProposition,
    EtatMission,
    Mission,
    OrigineProposition,
    TransitionMissionRefusee,
    TypeMission,
)
from tests.conftest import exige_postgresql, ouvrir_une_session

LE = datetime(2026, 10, 15, 9, 0)
PDF = b"%PDF-1.4\n% contrat de bail commercial\n%%EOF\n"
POINTS = [
    {
        "rubrique": "RESUME",
        "titre": "Bail commercial",
        "texte": "Bail d'un local à Akwa.",
        "citation": None,
        "gravite": None,
    },
    {
        "rubrique": "CLAUSE_A_RISQUE",
        "titre": "Résiliation unilatérale",
        "texte": "Le bailleur peut résilier sans préavis.",
        "citation": "Le bailleur pourra résilier à tout moment",
        "gravite": "ELEVEE",
    },
    {
        "rubrique": "MANQUE",
        "titre": "Force majeure",
        "texte": "Aucune clause de force majeure.",
        "citation": None,
        "gravite": "MOYENNE",
    },
    {
        "rubrique": "OBLIGATION",
        "titre": "",
        "texte": "point vide, à écarter",
        "citation": None,
        "gravite": None,
    },
]


def _mission(**champs) -> Mission:
    base = {
        "identifiant": "MIS-2026-TEST01",
        "type": TypeMission.ANALYSE_CONTRAT,
        "client": "SARL BATIMENT PLUS",
        "objet": "Analyse du bail du dépôt de Bonabéri",
        "responsable": "C-007",
        "ouverte_le": LE,
    }
    return Mission(**(base | champs))


def _avec_points(mission: Mission | None = None) -> Mission:
    lecture = AnalyseClaude(cle_api="x", modele="claude-opus-5-5", client=_Faux(POINTS)).lire(PDF)
    return (mission or _mission()).ajouter(lecture.propositions, origine=OrigineProposition.AGENT)


def _texte_pdf(octets: bytes) -> str:
    morceaux = []
    for flux in re.findall(rb"stream\r?\n(.*?)endstream", octets, re.S):
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
    return " ".join(
        re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), t).replace("\\", "")
        for t in re.findall(r"\((.*?)\)\s*Tj", rendu, re.S)
    )


# ── Le faux client de l'API : jamais d'appel réel dans la suite ────────────────────


class _Flux:
    def __init__(self, reponse=None, erreur=None):
        self._reponse, self._erreur = reponse, erreur

    def __enter__(self):
        if self._erreur:
            raise self._erreur
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._reponse


class _Faux:
    def __init__(self, points=None, *, stop_reason="end_turn", erreur=None):
        texte = json.dumps({"points": points or []})
        self.reponse = SimpleNamespace(
            stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=texte)]
        )
        self.erreur = erreur
        self.appels: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **demande):
        self.appels.append(demande)
        return _Flux(self.reponse, self.erreur)


class TestLAgent:
    def test_des_propositions_normalisees_et_les_points_vides_ecartes(self):
        faux = _Faux(POINTS)
        lecture = AnalyseClaude(cle_api="x", modele="claude-opus-5-5", client=faux).lire(PDF)

        assert lecture.incident is None
        assert [p["titre"] for p in lecture.propositions] == [
            "Bail commercial",
            "Résiliation unilatérale",
            "Force majeure",
        ]
        # Une gravité n'a de sens que pour une clause à risque.
        assert lecture.propositions[1]["gravite"] == "ELEVEE"
        assert lecture.propositions[2]["gravite"] is None
        (appel,) = faux.appels
        assert appel["model"] == "claude-opus-5-5"
        assert appel["thinking"] == {"type": "adaptive"}
        assert appel["output_config"]["format"]["type"] == "json_schema"
        assert appel["messages"][0]["content"][0]["type"] == "document"

    @pytest.mark.parametrize(
        ("faux", "attendu"),
        [
            (_Faux(stop_reason="refusal"), "décliné"),
            (_Faux(stop_reason="max_tokens"), "trop long"),
            (
                _Faux(
                    erreur=anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x"))
                ),
                "injoignable",
            ),
        ],
    )
    def test_elle_ne_leve_jamais(self, faux, attendu):
        lecture = AnalyseClaude(cle_api="x", modele="claude-opus-5-5", client=faux).lire(PDF)

        assert lecture.propositions == [] and attendu in lecture.incident

    def test_un_format_illisible_ne_part_pas(self):
        faux = _Faux(POINTS)
        lecture = AnalyseClaude(cle_api="x", modele="claude-opus-5-5", client=faux).lire(
            b"GIF89a...."
        )

        assert "format" in lecture.incident and faux.appels == []


class TestLeJuristeDecide:
    def test_rien_ne_se_soumet_tant_qu_une_proposition_attend(self):
        mission = _avec_points()

        with pytest.raises(TransitionMissionRefusee, match="attendent une décision"):
            mission.soumettre(par="C-007", a_l_instant=LE)

    def test_retenir_modifier_ecarter(self):
        m = _avec_points()
        m = m.decider(1, decision=DecisionProposition.RETENUE, par="C-007", a_l_instant=LE)
        m = m.decider(
            2,
            decision=DecisionProposition.MODIFIEE,
            par="C-007",
            a_l_instant=LE,
            texte_retenu="Clause à renégocier : préavis de trois mois au minimum.",
        )
        m = m.decider(
            3,
            decision=DecisionProposition.ECARTEE,
            par="C-007",
            a_l_instant=LE,
            motif="Couvert par l'article 12 du contrat.",
        )

        assert m.propositions[0].texte_retenu == "Bail d'un local à Akwa."
        assert [p.rang for p in m.retenues] == [1, 2]
        with pytest.raises(ValueError, match="écartée sans motif"):
            m.decider(3, decision=DecisionProposition.ECARTEE, par="C-007", a_l_instant=LE)
        with pytest.raises(ValueError, match="sans texte retenu"):
            m.decider(2, decision=DecisionProposition.MODIFIEE, par="C-007", a_l_instant=LE)

    def test_aucun_point_retenu_pas_de_livrable(self):
        m = _avec_points()
        for rang in (1, 2, 3):
            m = m.decider(
                rang,
                decision=DecisionProposition.ECARTEE,
                par="C-007",
                a_l_instant=LE,
                motif="hors sujet",
            )

        with pytest.raises(TransitionMissionRefusee, match="vide"):
            m.soumettre(par="C-007", a_l_instant=LE)

    def test_le_second_regard_et_le_gel_apres_soumission(self):
        m = _avec_points()
        for rang in (1, 2, 3):
            m = m.decider(rang, decision=DecisionProposition.RETENUE, par="C-007", a_l_instant=LE)
        soumise = m.soumettre(par="C-007", a_l_instant=LE)

        with pytest.raises(TransitionMissionRefusee, match="regard d'un autre"):
            soumise.valider(par="C-007", a_l_instant=LE)
        with pytest.raises(TransitionMissionRefusee, match="ouverte"):
            soumise.decider(
                1, decision=DecisionProposition.ECARTEE, par="C-007", a_l_instant=LE, motif="x"
            )
        rouverte = soumise.renvoyer(motif="Préciser la clause de juridiction.")
        assert rouverte.etat is EtatMission.OUVERTE and rouverte.renvoi
        livree = soumise.valider(par="C-001", a_l_instant=LE)
        assert livree.etat is EtatMission.LIVREE


class TestLeLivrable:
    def test_les_seuls_points_retenus_dans_les_mots_du_juriste(self):
        m = _avec_points()
        m = m.decider(1, decision=DecisionProposition.RETENUE, par="C-007", a_l_instant=LE)
        m = m.decider(
            2,
            decision=DecisionProposition.MODIFIEE,
            par="C-007",
            a_l_instant=LE,
            texte_retenu="Préavis de trois mois à obtenir.",
        )
        m = m.decider(
            3, decision=DecisionProposition.ECARTEE, par="C-007", a_l_instant=LE, motif="hors sujet"
        )
        projet = _texte_pdf(rendre_le_livrable(m))
        livre = _texte_pdf(
            rendre_le_livrable(
                m.soumettre(par="C-007", a_l_instant=LE).valider(par="C-001", a_l_instant=LE),
                noms={"C-007": "Patricia MOUKOURI", "C-001": "Bernadette MBALLA"},
            )
        )

        assert "PROJET" in projet and "PROJET" not in livre
        assert "Préavis de trois mois à obtenir." in livre
        assert "sans préavis" not in livre  # le texte de l'agent, remplacé
        assert "Force majeure" not in livre  # écartée
        assert "Bernadette MBALLA" in livre


# ── Par les routes, sur une vraie base ────────────────────────────────────────────

JURISTE = "p.moukouri@cga-brcg.cm"
DIRECTRICE = "b.mballa@cga-brcg.cm"
ADHERENT = "jp.nkoa@batimentplus.cm"


def _en_tant_que(client, courriel):
    client.cookies.clear()
    ouvrir_une_session(client, courriel)


def _ouvrir(client) -> str:
    _en_tant_que(client, JURISTE)
    ouverte = client.post(
        "/missions",
        json={
            "type": "ANALYSE_CONTRAT",
            "objet": "Bail du dépôt de Bonabéri",
            "client": "SARL BATIMENT PLUS",
        },
    )
    assert ouverte.status_code == 201, ouverte.text
    identifiant = ouverte.json()["identifiant"]
    verse = client.post(
        f"/missions/{identifiant}/document",
        files={"fichier": ("bail.pdf", PDF, "application/pdf")},
    )
    assert verse.status_code == 200, verse.text
    return identifiant


@exige_postgresql
class TestLesRoutes:
    def test_sans_cle_rien_ne_sort(self, plateforme):
        client = plateforme
        identifiant = _ouvrir(client)

        assert client.get("/missions/lecture-automatique").json() == {"disponible": False}
        assert client.post(f"/missions/{identifiant}/analyse-automatique").status_code == 503

    def test_le_parcours_complet_jusqu_au_livrable(self, plateforme, monkeypatch):
        from app.contextes.souscription.adaptateurs.entrant import routes_missions

        monkeypatch.setattr(
            routes_missions,
            "service_d_analyse",
            lambda: AnalyseClaude(cle_api="x", modele="claude-opus-5-5", client=_Faux(POINTS)),
        )
        client = plateforme
        identifiant = _ouvrir(client)

        analysee = client.post(f"/missions/{identifiant}/analyse-automatique").json()
        assert [p["decision"] for p in analysee["propositions"]] == ["EN_ATTENTE"] * 3
        assert client.post(f"/missions/{identifiant}/soumission").status_code == 409

        for rang in (1, 2):
            assert (
                client.post(
                    f"/missions/{identifiant}/propositions/{rang}/decision",
                    json={"decision": "RETENUE"},
                ).status_code
                == 200
            )
        client.post(
            f"/missions/{identifiant}/propositions/3/decision",
            json={"decision": "ECARTEE", "motif": "Couvert ailleurs."},
        )
        assert client.post(f"/missions/{identifiant}/soumission").status_code == 200
        assert client.post(f"/missions/{identifiant}/validation").status_code == 403

        _en_tant_que(client, DIRECTRICE)
        livree = client.post(f"/missions/{identifiant}/validation")
        assert livree.status_code == 200, livree.text
        assert livree.json()["etat"] == "LIVREE"
        texte = _texte_pdf(client.get(f"/missions/{identifiant}/livrable").content)
        assert "Résiliation unilatérale" in texte and "PROJET" not in texte

    def test_une_mission_ne_s_ouvre_que_sur_une_vente_payee(self, plateforme):
        client = plateforme
        _en_tant_que(client, JURISTE)

        refus = client.post(
            "/missions",
            json={"type": "ANALYSE_CONTRAT", "objet": "x", "reference_commerciale": "DC-INCONNU"},
        )

        assert refus.status_code == 404, refus.text

    def test_une_vente_non_payee_n_ouvre_pas_de_mission(self, plateforme):
        """⚠️ Le dossier existe, c'est une prestation ponctuelle, mais rien n'est réglé :
        on ne travaille pas avant le paiement."""
        client = plateforme
        client.cookies.clear()
        assert (
            client.post(
                "/acquisition/demandes",
                json={
                    "nom": "Ets Mbarga Négoce",
                    "telephone": "699445566",
                    "service_souhaite": "PONCTUEL",
                    "canal_prefere": "APPEL",
                },
            ).status_code
            == 201
        )
        _en_tant_que(client, JURISTE)
        dossiers = client.get("/acquisition/dossiers").json()
        (reference,) = [d["reference"] for d in dossiers if d["nom"] == "Ets Mbarga Négoce"]

        refus = client.post(
            "/missions",
            json={"type": "ANALYSE_CONTRAT", "objet": "x", "reference_commerciale": reference},
        )

        assert refus.status_code == 409, refus.text
        assert "payée" in refus.json()["detail"]

    def test_l_adherent_n_y_a_pas_acces(self, plateforme):
        client = plateforme
        _en_tant_que(client, ADHERENT)

        assert client.get("/missions").status_code == 403


# ── La rédaction et la DSF ciblée (15 octobre 2026) ───────────────────────────────

ARTICLES = [
    {"titre": "Objet", "texte": "Le Prestataire assure la tenue de la comptabilité du Client."},
    {"titre": "Prix", "texte": "Honoraires de [À COMPLÉTER : montant] FCFA par mois."},
    {"titre": "", "texte": "article sans titre, à écarter"},
]
CAHIER = "Prestation de tenue comptable mensuelle pour une PME de Douala, douze mois."


class _FauxRedaction(_Faux):
    def __init__(self, articles=None, **k):
        super().__init__(**k)
        texte = json.dumps({"articles": articles or []})
        self.reponse = SimpleNamespace(
            stop_reason=k.get("stop_reason", "end_turn"),
            content=[SimpleNamespace(type="text", text=texte)],
        )


class TestLaRedaction:
    def _redaction(self) -> Mission:
        return _mission(type=TypeMission.REDACTION_CONTRAT).ecrire_le_cahier(
            type_de_contrat="contrat de prestation de services", cahier=CAHIER
        )

    def test_l_agent_propose_des_articles_chacun_a_decider(self):
        from app.contextes.souscription.adaptateurs.sortant.redaction_claude import (
            RedactionClaude,
        )

        faux = _FauxRedaction(ARTICLES)
        lecture = RedactionClaude(cle_api="x", modele="claude-opus-5-5", client=faux).rediger(
            type_de_contrat="contrat de prestation de services", cahier=CAHIER
        )
        mission = self._redaction().ajouter(lecture.propositions, origine=OrigineProposition.AGENT)

        assert [p.titre for p in mission.propositions] == ["Objet", "Prix"]
        assert all(p.rubrique.value == "ARTICLE" for p in mission.propositions)
        assert len(mission.en_attente) == 2
        (appel,) = faux.appels
        assert CAHIER in appel["messages"][0]["content"]

    def test_le_projet_numerote_les_seuls_articles_retenus(self):
        m = self._redaction().ajouter(
            [{**a, "rubrique": "ARTICLE"} for a in ARTICLES[:2]]
            + [{"rubrique": "ARTICLE", "titre": "Exclusivité", "texte": "Clause écartée."}],
            origine=OrigineProposition.COLLABORATEUR,
        )
        m = m.decider(1, decision=DecisionProposition.RETENUE, par="C-007", a_l_instant=LE)
        m = m.decider(
            3,
            decision=DecisionProposition.ECARTEE,
            par="C-007",
            a_l_instant=LE,
            motif="non demandé",
        )
        m = m.decider(
            2,
            decision=DecisionProposition.MODIFIEE,
            par="C-007",
            a_l_instant=LE,
            texte_retenu="Honoraires de 150 000 FCFA par mois, payables le 5.",
        )
        texte = _texte_pdf(rendre_le_livrable(m))

        assert "PROJET DE CONTRAT DE PRESTATION DE SERVICES" in texte
        assert "Article 1 · Objet" in texte and "Article 2 · Prix" in texte
        assert "150 000 FCFA" in texte and "Exclusivité" not in texte

    def test_le_cahier_est_celui_d_une_redaction(self):
        with pytest.raises(TransitionMissionRefusee):
            _mission().ecrire_le_cahier(type_de_contrat="bail", cahier=CAHIER)


@exige_postgresql
class TestLaRedactionEtLaDsfCibleeParLesRoutes:
    def test_sans_cle_la_redaction_ne_part_pas_et_le_cahier_est_requis(
        self, plateforme, monkeypatch
    ):
        from app.contextes.souscription.adaptateurs.entrant import routes_missions
        from app.contextes.souscription.adaptateurs.sortant.redaction_claude import (
            RedactionClaude,
        )

        client = plateforme
        _en_tant_que(client, JURISTE)
        identifiant = client.post(
            "/missions",
            json={"type": "REDACTION_CONTRAT", "objet": "Tenue comptable", "client": "ETS X"},
        ).json()["identifiant"]

        assert client.post(f"/missions/{identifiant}/redaction-automatique").status_code == 503
        monkeypatch.setattr(
            routes_missions,
            "service_de_redaction",
            lambda: RedactionClaude(
                cle_api="x", modele="claude-opus-5-5", client=_FauxRedaction(ARTICLES)
            ),
        )
        sans_cahier = client.post(f"/missions/{identifiant}/redaction-automatique")
        assert sans_cahier.status_code == 409, sans_cahier.text
        assert (
            client.post(
                f"/missions/{identifiant}/cahier",
                json={"type_de_contrat": "contrat de prestation de services", "cahier": CAHIER},
            ).status_code
            == 200
        )
        redigee = client.post(f"/missions/{identifiant}/redaction-automatique").json()
        assert [p["titre"] for p in redigee["propositions"]] == ["Objet", "Prix"]

    def test_une_dsf_ciblee_porte_son_dossier_et_son_exercice(self, plateforme):
        client = plateforme
        _en_tant_que(client, JURISTE)

        sans = client.post(
            "/missions", json={"type": "DSF_CIBLEE", "objet": "DSF 2025", "client": "ETS X"}
        )
        avec = client.post(
            "/missions",
            json={
                "type": "DSF_CIBLEE",
                "objet": "DSF 2025",
                "client": "CABINET NGUEMA CONSEIL",
                "dossier_niu": "P027788990011M",
                "exercice": "2025",
            },
        )

        assert sans.status_code == 422, sans.text
        assert avec.status_code == 201 and avec.json()["exercice"] == "2025"
