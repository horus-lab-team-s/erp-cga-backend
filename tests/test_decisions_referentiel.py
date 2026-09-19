"""Décider sur le référentiel, avec effet immédiat pour le seul cabinet (pas 95).

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE

- **La surcouche** : une validation marque la version, une nouvelle version s'insère à sa
  date sans effacer les versions postérieures du fichier, garde la borne du seuil, et
  seules les décisions validées comptent.
- **Le cloisonnement** : la décision d'un cabinet ne change rien pour un autre.
- **Le circuit** : lu au référentiel, fermé sans fichier, quatre yeux sur un taux légal,
  la direction seule pour une politique du cabinet, jamais pour un taux légal.
- **Le point de montage unique** : une valeur validée est vue par les autres contextes
  (paie, conformité) au calcul suivant, sans redémarrage.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contextes.referentiel.api import (
    CircuitDeValidation,
    DecisionRefusee,
    DecisionSurLeReferentiel,
    Fondement,
    HorsDuCircuit,
    SorteDeDecision,
    StatutDecision,
    charger_le_circuit,
    parametres_communs,
    parametres_du_cabinet,
    proposer_une_version,
    vider_les_decisions,
)
from app.contextes.referentiel.contrats import Parametre
from app.contextes.referentiel.domaine.surcouche import (
    appliquer_la_surcouche,
    decision_sans_objet,
    valeur_conforme_a_l_unite,
)
from app.contextes.transverse.api import (
    MOT_DE_PASSE_DEMO,
    Permission,
    atelier,
    reinitialiser_atelier,
)
from app.infrastructure.config import configuration
from app.main import creer_application
from app.partage.locataire import etabli
from tests.conftest import exige_postgresql, ouvrir_une_session

FISCALISTE = "r.ebolo@cga-brcg.cm"
REVISEUR = "a.bouba@cga-brcg.cm"
DIRECTION = "b.mballa@cga-brcg.cm"
COMPTABLE = "l.fotso@cga-brcg.cm"
ADHERENT = "jp.nkoa@batimentplus.cm"
ADMINISTRATEUR = "s.onana@cga-brcg.cm"
LE = datetime(2026, 9, 15, 10, 0)
#: La DSF, livrée « à valider » dans le référentiel commun depuis le 18 août 2026.
VALIDER_LE_DELAI_DSF = "/transverse/referentiel/parametres/DSF_DELAI_JOURS_APRES_CLOTURE/versions/2019-01-01/validation"
FONDEMENT = {
    "texte": "Loi de finances 2027, article 7",
    "source": "Journal officiel du 30/12/2026, consulté",
}


@pytest.fixture(autouse=True)
def decisions_neuves():
    reinitialiser_atelier()
    vider_les_decisions()
    yield
    vider_les_decisions()


def _client(courriel: str) -> TestClient:
    client = TestClient(creer_application())
    reponse = client.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert reponse.status_code == 200, reponse.text
    return client


def _parametre(code: str) -> Parametre:
    return next(p for p in parametres_communs() if p.code == code)


def _decision(**surcharges) -> DecisionSurLeReferentiel:
    valeurs = {
        "identifiant": "DEC-1",
        "code": "TVA_TAUX_GENERAL",
        "sorte": SorteDeDecision.NOUVELLE_VERSION,
        "applicable_du": date(2027, 1, 1),
        "valeur": 20.0,
        "fondement": Fondement(**FONDEMENT),
        "motif": "Loi de finances 2027 publiée.",
        "propose_par": "C-006",
        "propose_par_nom": "Roger EBOLO",
        "propose_le": LE,
        "statut": StatutDecision.APPLIQUEE,
        "tranche_par": "C-003",
        "tranche_par_nom": "Aïcha BOUBA",
        "tranche_le": LE,
    }
    return DecisionSurLeReferentiel(**{**valeurs, **surcharges})


def _resoudre(parametres: list[Parametre], code: str, jour: date):
    from app.contextes.referentiel.api import ServiceParametres

    return ServiceParametres(parametres).resoudre(code, jour)


# ── La surcouche ──────────────────────────────────────────────────────────────


class TestLaSurcouche:
    def test_une_nouvelle_version_s_insere_a_sa_date_signee_par_qui_l_a_validee(self):
        parametres = appliquer_la_surcouche(parametres_communs(), [_decision()])
        avant = _resoudre(parametres, "TVA_TAUX_GENERAL", date(2026, 12, 31))
        apres = _resoudre(parametres, "TVA_TAUX_GENERAL", date(2027, 1, 1))
        assert (avant.valeur, apres.valeur) == (19.25, 20.0)
        assert apres.valide_par == "Aïcha BOUBA" and apres.statut.value == "VALIDE"

    def test_les_versions_posterieures_du_fichier_sont_conservees(self):
        commun = Parametre.model_validate(
            {
                **_parametre("TVA_TAUX_GENERAL").model_dump(),
                "versions": [
                    {**v.model_dump(), "applicable_au": date(2028, 1, 1)}
                    for v in _parametre("TVA_TAUX_GENERAL").versions
                ]
                + [
                    {
                        **_parametre("TVA_TAUX_GENERAL").versions[0].model_dump(),
                        "valeur": 21.0,
                        "applicable_du": date(2028, 1, 1),
                        "applicable_au": None,
                    }
                ],
            }
        )
        [resultat] = appliquer_la_surcouche([commun], [_decision()])
        assert [(str(v.applicable_du), str(v.applicable_au), v.valeur) for v in resultat.versions][
            -2:
        ] == [
            ("2027-01-01", "2028-01-01", 20.0),
            ("2028-01-01", "None", 21.0),
        ]

    def test_la_borne_du_seuil_suit_la_nouvelle_valeur(self):
        seuil = next(p for p in parametres_communs() if p.versions[-1].borne is not None)
        derniere = seuil.versions[-1]
        decision = _decision(
            code=seuil.code,
            applicable_du=date(2030, 1, 1),
            valeur=float(derniere.valeur) + 1,
        )
        [nouvelle] = [
            v
            for p in appliquer_la_surcouche(parametres_communs(), [decision])
            if p.code == seuil.code
            for v in p.versions
            if v.applicable_du == date(2030, 1, 1)
        ]
        assert nouvelle.borne == derniere.borne

    def test_a_la_meme_date_la_decision_du_cabinet_remplace_la_version_du_fichier(self):
        """Le fichier commun a pu gagner, depuis, une version à la date même de la décision."""
        tva = _parametre("TVA_TAUX_GENERAL")
        commun = Parametre.model_validate(
            {
                **tva.model_dump(),
                "versions": [
                    {**tva.versions[0].model_dump(), "applicable_au": date(2027, 1, 1)},
                    {
                        **tva.versions[0].model_dump(),
                        "valeur": 18.0,
                        "applicable_du": date(2027, 1, 1),
                    },
                ],
            }
        )
        [resultat] = appliquer_la_surcouche([commun], [_decision()])
        assert [(str(v.applicable_du), v.valeur) for v in resultat.versions] == [
            (str(tva.versions[0].applicable_du), 19.25),
            ("2027-01-01", 20.0),
        ]

    def test_une_validation_devenue_sans_objet_ne_reecrit_pas_la_signature_du_fichier(self):
        """La plateforme a validé la version entre-temps : c'est sa signature qui reste."""
        tva = _parametre("TVA_TAUX_GENERAL")
        decision = _decision(
            sorte=SorteDeDecision.VALIDATION,
            applicable_du=tva.versions[0].applicable_du,
            valeur=None,
            fondement=None,
        )
        resolu = _resoudre(
            appliquer_la_surcouche(parametres_communs(), [decision]),
            "TVA_TAUX_GENERAL",
            date(2026, 9, 15),
        )
        assert resolu.valide_par == tva.versions[0].valide_par

    @pytest.mark.parametrize("statut", [StatutDecision.PROPOSEE, StatutDecision.REFUSEE])
    def test_une_decision_non_validee_ne_change_rien(self, statut):
        parametres = appliquer_la_surcouche(parametres_communs(), [_decision(statut=statut)])
        assert _resoudre(parametres, "TVA_TAUX_GENERAL", date(2027, 6, 1)).valeur == 19.25

    def test_valider_une_version_livree_a_valider(self):
        [version] = [v for v in _parametre("DSF_DELAI_JOURS_APRES_CLOTURE").versions]
        decision = _decision(
            code="DSF_DELAI_JOURS_APRES_CLOTURE",
            sorte=SorteDeDecision.VALIDATION,
            applicable_du=version.applicable_du,
            valeur=None,
            fondement=None,
        )
        parametres = appliquer_la_surcouche(parametres_communs(), [decision])
        resolu = _resoudre(parametres, "DSF_DELAI_JOURS_APRES_CLOTURE", date(2026, 9, 15))
        assert resolu.statut.value == "VALIDE" and resolu.valide_par == "Aïcha BOUBA"

    def test_une_decision_dont_la_version_a_disparu_est_sans_objet(self):
        decision = _decision(sorte=SorteDeDecision.VALIDATION, applicable_du=date(1999, 1, 1))
        assert "n'existe plus" in decision_sans_objet(_parametre("TVA_TAUX_GENERAL"), decision)
        deja = _decision(
            sorte=SorteDeDecision.VALIDATION,
            applicable_du=_parametre("TVA_TAUX_GENERAL").versions[0].applicable_du,
        )
        assert "désormais validée" in decision_sans_objet(_parametre("TVA_TAUX_GENERAL"), deja)

    @pytest.mark.parametrize(
        ("valeur", "unite", "attendu"),
        [
            ("19,25 %", "POURCENTAGE", "numérique"),
            (120, "POURCENTAGE", "100"),
            (-1, "FCFA", "négative"),
            (30.5, "JOURS", "entier"),
            (32, "JOUR_DU_MOIS", "31"),
            ("([", "REGEX", "invalide"),
            (15, "JOUR_DU_MOIS", None),
        ],
    )
    def test_la_valeur_doit_convenir_a_l_unite(self, valeur, unite, attendu):
        from app.contextes.referentiel.contrats import Unite

        raison = valeur_conforme_a_l_unite(valeur, Unite(unite))
        assert (raison is None) if attendu is None else (attendu in raison)


# ── Le circuit ────────────────────────────────────────────────────────────────


class TestLeCircuit:
    def test_sans_fichier_personne_ne_decide(self, tmp_path: Path):
        circuit = charger_le_circuit(tmp_path)
        with etabli("cga"), pytest.raises(HorsDuCircuit):
            proposer_une_version(
                parametres_communs(),
                code="TVA_TAUX_GENERAL",
                valeur=20.0,
                applicable_du=date(2027, 1, 1),
                fondement=Fondement(**FONDEMENT),
                note=None,
                motif="Loi de finances 2027 publiée.",
                par="C-006",
                nom="R",
                le=LE,
                permissions={"MODIFIER_PARAMETRE"},
                circuit=circuit,
                depot=_DepotVide(),
            )

    def test_une_cle_mal_orthographiee_fait_echouer_le_chargement(self, tmp_path: Path):
        (tmp_path / "validation").mkdir()
        (tmp_path / "validation" / "circuit.yaml").write_text(
            yaml.safe_dump({"par_nature": {"LOI": {"valideur": ["X"]}}}), encoding="utf-8"
        )
        with pytest.raises(ValidationError):
            charger_le_circuit(tmp_path)

    def test_le_circuit_livre_nomme_des_permissions_qui_existent(self):
        circuit = charger_le_circuit(configuration().dossier_referentiel)
        nommees = {p for e in circuit.par_nature.values() for p in (*e.proposer, *e.valider)}
        assert nommees and nommees <= set(Permission.__members__)

    def test_la_direction_ne_valide_jamais_un_taux_legal(self):
        """Elle détient `CLOTURER_EXERCICE` : le circuit ne doit pas s'en servir pour la LOI."""
        from app.contextes.transverse.api import PERMISSIONS_PAR_ROLE, Role

        circuit = charger_le_circuit(configuration().dossier_referentiel)
        direction = {p.value for p in PERMISSIONS_PAR_ROLE[Role.DIRECTION]}
        assert not direction.intersection(
            circuit.etape(_parametre("TVA_TAUX_GENERAL").nature).valider
        )


class _DepotVide:
    def toutes(self):
        return []

    def trouver(self, identifiant):
        return None

    def enregistrer(self, decision):
        raise AssertionError("rien ne devait être enregistré")


def _proposer(client: TestClient, code: str = "TVA_TAUX_GENERAL", **surcharges):
    corps = {
        "valeur": 20.0,
        "applicable_du": "2027-01-01",
        "fondement": FONDEMENT,
        "motif": "Loi de finances 2027 publiée au Journal officiel.",
        **surcharges,
    }
    return client.post(f"/transverse/referentiel/parametres/{code}/propositions", json=corps)


def _trancher(client: TestClient, identifiant: str, decision: str = "VALIDER"):
    return client.post(
        f"/transverse/referentiel/decisions/{identifiant}/tranchage",
        json={"decision": decision, "motif": "Relu sur le Journal officiel, taux conforme."},
    )


# ── Par l'API ─────────────────────────────────────────────────────────────────


class TestUnTauxLegalAQuatreYeux:
    def test_propose_par_le_fiscaliste_valide_par_le_reviseur_applique_au_cabinet(self):
        fiscaliste = _client(FISCALISTE)
        proposee = _proposer(fiscaliste)
        assert proposee.status_code == 201, proposee.text
        identifiant = proposee.json()["identifiant"]
        lecture = {"a_la_date": "2027-01-02"}
        assert (
            fiscaliste.get("/referentiel/parametres/TVA_TAUX_GENERAL", params=lecture).json()[
                "valeur"
            ]
            == 19.25
        )

        soi_meme = _trancher(fiscaliste, identifiant)
        assert soi_meme.status_code == 409 and "autre personne" in soi_meme.json()["detail"]

        reviseur = _client(REVISEUR)
        assert [
            n["titre"] for n in reviseur.get("/transverse/notifications").json()["notifications"]
        ] == ["Nouvelle valeur proposée pour TVA_TAUX_GENERAL"]
        validee = _trancher(reviseur, identifiant)
        assert validee.status_code == 200, validee.text
        assert validee.json()["statut"] == "APPLIQUEE"

        resolu = reviseur.get("/referentiel/parametres/TVA_TAUX_GENERAL", params=lecture).json()
        assert resolu["valeur"] == 20.0 and resolu["valide_par"] == "Aïcha BOUBA"
        veille = reviseur.get(
            "/referentiel/parametres/TVA_TAUX_GENERAL", params={"a_la_date": "2026-12-31"}
        )
        assert veille.json()["valeur"] == 19.25

        actions = [
            e.action for e in atelier().journal.lister() if e.action.startswith("referentiel.")
        ]
        assert actions == ["referentiel.version_proposee", "referentiel.proposition_validee"]

    def test_les_autres_contextes_voient_la_valeur_au_calcul_suivant(self):
        """Le point de montage unique : la paie et la conformité, sans redémarrage."""
        from app.contextes.conformite.api import moteur_par_defaut
        from app.contextes.social.adaptateurs.entrant.routes_http import (
            parametres as parametres_de_la_paie,
        )

        identifiant = _proposer(_client(FISCALISTE)).json()["identifiant"]
        with etabli(configuration().locataire_par_defaut):
            assert (
                parametres_de_la_paie().resoudre("TVA_TAUX_GENERAL", date(2027, 2, 1)).valeur
                == 19.25
            )
        _trancher(_client(REVISEUR), identifiant)
        with etabli(configuration().locataire_par_defaut):
            assert (
                parametres_de_la_paie().resoudre("TVA_TAUX_GENERAL", date(2027, 2, 1)).valeur
                == 20.0
            )
            moteur = moteur_par_defaut()
            assert moteur._parametres.resoudre("TVA_TAUX_GENERAL", date(2027, 2, 1)).valeur == 20.0

    def test_un_autre_cabinet_garde_le_referentiel_commun(self):
        identifiant = _proposer(_client(FISCALISTE)).json()["identifiant"]
        _trancher(_client(REVISEUR), identifiant)
        with etabli(configuration().locataire_par_defaut):
            assert (
                _resoudre(parametres_du_cabinet(), "TVA_TAUX_GENERAL", date(2027, 2, 1)).valeur
                == 20.0
            )
        with etabli("un-autre-cabinet"):
            assert (
                _resoudre(parametres_du_cabinet(), "TVA_TAUX_GENERAL", date(2027, 2, 1)).valeur
                == 19.25
            )

    def test_la_direction_ne_peut_pas_valider_un_taux_legal(self):
        identifiant = _proposer(_client(FISCALISTE)).json()["identifiant"]
        assert _trancher(_client(DIRECTION), identifiant).status_code == 403


class TestCeQuiEstRefuse:
    def test_une_date_passee_ou_anterieure_a_la_derniere_version(self):
        fiscaliste = _client(FISCALISTE)
        passe = _proposer(fiscaliste, applicable_du="2026-01-01")
        assert passe.status_code == 409 and "passé" in passe.json()["detail"]

    def test_une_valeur_qui_ne_convient_pas_a_l_unite(self):
        reponse = _proposer(_client(FISCALISTE), valeur="19,25 %")
        assert reponse.status_code == 409 and "numérique" in reponse.json()["detail"]

    def test_une_seconde_proposition_sur_le_meme_parametre(self):
        fiscaliste = _client(FISCALISTE)
        assert _proposer(fiscaliste).status_code == 201
        assert _proposer(fiscaliste, applicable_du="2027-06-01").status_code == 409

    def test_un_motif_trop_court_pour_le_circuit(self):
        assert _proposer(_client(FISCALISTE), motif="LF 2027 publiée").status_code == 409

    @pytest.mark.parametrize("courriel", [COMPTABLE, ADHERENT, ADMINISTRATEUR])
    def test_hors_du_circuit_on_ne_propose_pas(self, courriel):
        assert _proposer(_client(courriel)).status_code == 403

    def test_un_refus_ne_change_rien_et_se_conserve(self):
        identifiant = _proposer(_client(FISCALISTE)).json()["identifiant"]
        reviseur = _client(REVISEUR)
        assert _trancher(reviseur, identifiant, "REFUSER").json()["statut"] == "REFUSEE"
        assert (
            reviseur.get(
                "/referentiel/parametres/TVA_TAUX_GENERAL", params={"a_la_date": "2027-02-01"}
            ).json()["valeur"]
            == 19.25
        )
        [etat] = reviseur.get("/transverse/referentiel/decisions").json()
        assert etat["decision"]["statut"] == "REFUSEE" and etat["nature"] == "LOI"
        assert _trancher(reviseur, identifiant).status_code == 409


class TestUneVersionLivreeAValider:
    def test_le_reviseur_valide_une_valeur_legale_livree_a_valider(self):
        reviseur = _client(REVISEUR)
        adresse = VALIDER_LE_DELAI_DSF
        motif = {"motif": "Confronté au CGI, article 18, délai confirmé."}
        validee = reviseur.post(adresse, json=motif)
        assert validee.status_code == 200, validee.text
        resolu = reviseur.get(
            "/referentiel/parametres/DSF_DELAI_JOURS_APRES_CLOTURE",
            params={"a_la_date": "2026-09-15"},
        ).json()
        assert resolu["statut"] == "VALIDE" and resolu["valide_par"] == "Aïcha BOUBA"
        assert reviseur.post(adresse, json=motif).status_code == 409

    def test_la_direction_ne_valide_pas_une_valeur_legale(self):
        adresse = VALIDER_LE_DELAI_DSF
        assert (
            _client(DIRECTION)
            .post(adresse, json={"motif": "Arrêté en comité de direction."})
            .status_code
            == 403
        )

    def test_la_direction_propose_et_arrete_seule_une_politique_du_cabinet(self):
        direction = _client(DIRECTION)
        proposee = _proposer(
            direction,
            code="SEUIL_RISQUE_ELEVE",
            valeur=70,
            applicable_du="2026-10-01",
            fondement={
                "texte": "Décision du comité de direction",
                "source": "Procès-verbal du 12/09/2026",
            },
        )
        assert proposee.status_code == 201, proposee.text
        assert _trancher(direction, proposee.json()["identifiant"]).json()["statut"] == "APPLIQUEE"
        assert (
            direction.get(
                "/referentiel/parametres/SEUIL_RISQUE_ELEVE", params={"a_la_date": "2026-10-01"}
            ).json()["valeur"]
            == 70
        )

    def test_mon_circuit_dit_ce_que_la_session_peut_faire(self):
        fiscaliste = _client(FISCALISTE).get("/transverse/referentiel/circuit").json()
        assert fiscaliste["par_nature"]["LOI"] == {
            "peut_proposer": True,
            "peut_valider": True,
            "quatre_yeux": True,
        }
        assert fiscaliste["par_nature"]["POLITIQUE_CABINET"]["peut_valider"] is False
        assert _client(ADHERENT).get("/transverse/referentiel/circuit").status_code == 403


class TestSurUneVraieBase:
    pytestmark = exige_postgresql

    def test_une_version_validee_s_applique_d_une_requete_a_l_autre(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, FISCALISTE)
        proposee = _proposer(client)
        assert proposee.status_code == 201, proposee.text
        ouvrir_une_session(client, REVISEUR)
        assert _trancher(client, proposee.json()["identifiant"]).status_code == 200
        resolu = client.get(
            "/referentiel/parametres/TVA_TAUX_GENERAL", params={"a_la_date": "2027-01-02"}
        )
        assert resolu.json()["valeur"] == 20.0


def test_une_decision_refusee_est_une_erreur_de_valeur():
    """`DecisionRefusee` se traduit en 409 : c'est une `ValueError`, pas une permission."""
    assert issubclass(DecisionRefusee, ValueError) and not issubclass(HorsDuCircuit, ValueError)
    assert CircuitDeValidation().etape  # le circuit vide se lit sans lever
