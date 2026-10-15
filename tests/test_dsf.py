"""La DSF : préparée sur un exercice clos, validée par un second regard, puis déposée.

─────────────────────────────────────────────────────────────────────────────────
DEMANDE DU CABINET, 14 OCTOBRE 2026 : « le flux jusqu'à la génération de la DSF, sa
prévisualisation et sa soumission ».

⚠️ CE QUI EST GARDÉ, ET OÙ UNE ERREUR COÛTERAIT

* on ne prépare que sur un exercice clos et une liasse cohérente, et l'on dit
  tout ce qui manque d'un coup ;
* une version est figée : son empreinte est celle de son contenu, l'aperçu est
  rendu de ce contenu, une nouvelle version remplace l'ancienne sans l'effacer ;
* le valideur n'est jamais le préparateur, et une valeur du référentiel « à
  valider » bloque la validation (la DGI n'admet que 12 soumissions par an) ;
* on ne dépose qu'une version validée ; l'accusé porte l'empreinte de la version,
  et l'obligation DSF passe à « déclarée » à l'échéancier ;
* le constat libre refuse la DSF, comme il refuse la TVA ;
* chacun son geste : le comptable prépare, le réviseur valide et dépose, avec le
  second facteur.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
import zlib
from datetime import date, datetime

import pytest

from app.contextes.cloture.adaptateurs.sortant.document_dsf import rendre_la_dsf
from app.contextes.cloture.application.dsf import (
    DSFNonPreparable,
    constater_le_depot_de_la_dsf,
    contenu_de_la_liasse,
    preparer_la_dsf,
    valider_la_dsf,
)
from app.contextes.cloture.domaine.declaration_dsf import EtatDSF, TransitionDSFRefusee
from app.contextes.portefeuille.api import Exercice
from app.contextes.transverse.api import (
    NIU_DEMO,
    AccuseIncoherent,
    JournalAuditMemoire,
    PortailManuel,
)
from tests.conftest import enroler_par_le_courriel, exige_postgresql, ouvrir_une_session

CLOS = Exercice(libelle="2025", ouverture=date(2025, 1, 1), cloture=date(2025, 12, 31), clos=True)
OUVERT = CLOS.model_copy(update={"clos": False})
LE = datetime(2026, 10, 14, 10, 0)
#: La comptable de CABINET NGUEMA CONSEIL, dont la DSF 2025 reste à faire au jeu d'essai.
COMPTABLE = "c.ndongo@cga-brcg.cm"
REVISEUR = "a.bouba@cga-brcg.cm"


def _liasse(**changements) -> dict:
    base = {
        "entreprise": "M081234567890P",
        "denomination": "SARL BATIMENT PLUS",
        "exercice": "2025",
        "systeme": "MINIMAL",
        "systeme_non_valide": False,
        "passage_non_valide": False,
        "lignes": [
            {
                "poste": "BA",
                "libelle": "Disponibilités",
                "sens": "ACTIF",
                "montant": "821125",
                "comptes": ["521"],
            },
            {
                "poste": "CA",
                "libelle": "Capital",
                "sens": "PASSIF",
                "montant": "1000000",
                "comptes": ["101"],
            },
        ],
        "comptes_non_couverts": [],
        "controles": [
            {
                "code": "BALANCE_EQUILIBREE",
                "libelle": "La balance est équilibrée",
                "satisfait": True,
            },
        ],
        "total_actif": "821125",
        "total_passif": "1000000",
        "total_charges": "178875",
        "total_produits": "0",
        "resultat_comptable": "-178875",
        "total_reintegrations": "0",
        "total_deductions": "0",
        "resultat_fiscal": "-178875",
        "passage": [],
        "droit_cga": {"ouvert": True, "motif": "adhésion sur tout l'exercice."},
        "abattement_cga_ecarte": None,
    }
    return base | changements


def _texte(octets: bytes) -> str:
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
    return "\n".join(
        re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), t).replace("\\", "")
        for t in re.findall(r"\((.*?)\)\s*Tj", rendu, re.S)
    )


def _preparee(liasse=None, existantes=(), par="l.fotso"):
    dsf, _ = preparer_la_dsf(
        liasse or _liasse(), exercice=CLOS, existantes=list(existantes), par=par, a_l_instant=LE
    )
    return dsf


class TestPreparer:
    def test_on_dit_tout_ce_qui_manque_d_un_coup(self):
        liasse = _liasse(
            controles=[{"code": "X", "libelle": "Le résultat concorde", "satisfait": False}],
            comptes_non_couverts=["471"],
        )
        with pytest.raises(DSFNonPreparable) as refus:
            preparer_la_dsf(liasse, exercice=OUVERT, existantes=[], par="x", a_l_instant=LE)

        message = str(refus.value)
        assert "n'est pas clos" in message
        assert "Le résultat concorde" in message
        assert "471" in message

    def test_la_version_est_figee_et_son_empreinte_est_celle_de_son_contenu(self):
        dsf = _preparee()

        assert dsf.version == 1 and dsf.etat is EtatDSF.PREPAREE
        assert dsf.contenu == contenu_de_la_liasse(_liasse())
        assert dsf.empreinte == hashlib.sha256(dsf.contenu.encode()).hexdigest()
        assert json.loads(dsf.contenu)["resultat_fiscal"] == "-178875"

    def test_une_nouvelle_version_remplace_l_ancienne_sans_l_effacer(self):
        v1 = _preparee()
        v1_validee = valider_la_dsf(v1, par="a.bouba", a_l_instant=LE)

        v2, remplacees = preparer_la_dsf(
            _liasse(total_actif="900000"),
            exercice=CLOS,
            existantes=[v1_validee],
            par="x",
            a_l_instant=LE,
        )

        assert v2.version == 2
        assert [r.etat for r in remplacees] == [EtatDSF.REMPLACEE]
        assert remplacees[0].empreinte == v1.empreinte

    def test_une_dsf_deposee_ne_se_remplace_pas(self):
        deposee = _deposee(_valide())

        with pytest.raises(DSFNonPreparable, match="rectificative"):
            preparer_la_dsf(_liasse(), exercice=CLOS, existantes=[deposee], par="x", a_l_instant=LE)


def _valide(dsf=None):
    return valider_la_dsf(dsf or _preparee(), par="a.bouba", a_l_instant=LE)


def _deposee(dsf, portail=None, numero="DGI-2026-DSF-001"):
    deposee, _ = constater_le_depot_de_la_dsf(
        dsf,
        numero=numero,
        depose_le=datetime(2026, 3, 12, 15, 0),
        piece=None,
        par="a.bouba",
        portail=portail or PortailManuel(),
        journal=JournalAuditMemoire(),
        a_l_instant=LE,
    )
    return deposee


class TestValider:
    def test_le_valideur_n_est_jamais_le_preparateur(self):
        with pytest.raises(TransitionDSFRefusee, match="regard d'un autre"):
            valider_la_dsf(_preparee(par="a.bouba"), par="a.bouba", a_l_instant=LE)

    def test_une_valeur_a_valider_au_referentiel_bloque(self):
        with pytest.raises(DSFNonPreparable, match="seuil du Système Normal"):
            valider_la_dsf(
                _preparee(_liasse(systeme_non_valide=True)), par="a.bouba", a_l_instant=LE
            )
        with pytest.raises(DSFNonPreparable, match="passage au résultat fiscal"):
            valider_la_dsf(
                _preparee(_liasse(passage_non_valide=True)), par="a.bouba", a_l_instant=LE
            )

    def test_on_ne_valide_qu_une_version_preparee(self):
        with pytest.raises(TransitionDSFRefusee):
            valider_la_dsf(_valide(), par="autre", a_l_instant=LE)


class TestDeposer:
    def test_l_accuse_porte_l_empreinte_de_la_version_et_le_portail_le_garde(self):
        portail = PortailManuel()
        dsf = _valide()

        deposee = _deposee(dsf, portail)

        assert deposee.etat is EtatDSF.DEPOSEE and deposee.numero_accuse == "DGI-2026-DSF-001"
        (consigne,) = portail.tous()
        assert consigne.empreinte_deposee == dsf.empreinte
        assert consigne.numero == "DGI-2026-DSF-001"

    def test_on_ne_depose_qu_une_version_validee(self):
        with pytest.raises(TransitionDSFRefusee, match="seule une version validée"):
            _deposee(_preparee())

    def test_ni_dans_le_futur_ni_avant_la_fin_de_l_exercice(self):
        for jour in (datetime(2026, 12, 1), datetime(2025, 12, 15)):
            with pytest.raises(DSFNonPreparable):
                constater_le_depot_de_la_dsf(
                    _valide(),
                    numero="N",
                    depose_le=jour,
                    piece=None,
                    par="a.bouba",
                    portail=PortailManuel(),
                    journal=JournalAuditMemoire(),
                    a_l_instant=LE,
                )

    def test_un_second_accuse_est_refuse(self):
        portail = PortailManuel()
        _deposee(_valide(), portail)

        with pytest.raises(AccuseIncoherent):
            _deposee(_valide(), portail, numero="DGI-2026-DSF-002")


class TestLApercu:
    def test_il_est_rendu_de_la_version_figee_et_dit_son_etat(self):
        dsf = _preparee()
        texte = _texte(rendre_la_dsf(dsf))

        assert "PROJET" in texte
        assert "SARL BATIMENT PLUS" in texte and "M081234567890P" in texte
        assert "(178 875)" in texte  # le résultat fiscal, négatif entre parenthèses
        assert dsf.empreinte in texte
        assert "Il ne remplace pas la DSF" in texte

    def test_valide_puis_depose_le_document_le_dit(self):
        texte = _texte(rendre_la_dsf(_deposee(_valide())))

        assert "Déposée à la DGI" in texte and "DGI-2026-DSF-001" in texte
        assert "a.bouba" in texte


# ── Par les routes, sur une vraie base ────────────────────────────────────────────

BATIMENT = NIU_DEMO["BATIMENT"]
COLOMBE = NIU_DEMO["COLOMBE"]
NGUEMA = NIU_DEMO["NGUEMA"]
DSF = f"/cloture/dossiers/{NGUEMA}/dsf/2025"


def _en_tant_que(client, courriel):
    client.cookies.clear()
    ouvrir_une_session(client, courriel)


def _renforcer(client):
    from app.contextes.transverse.api import code_attendu
    from app.partage.horloge import maintenant

    secret = enroler_par_le_courriel(client)
    renfort = client.post(
        "/transverse/session/renforcement", json={"code": code_attendu(secret, maintenant())}
    )
    assert renfort.status_code == 200, renfort.text


def _piece(client, dossier=NGUEMA, courriel=REVISEUR, canal="DEPOT_CABINET") -> str:
    _en_tant_que(client, courriel)
    contenu = b"%PDF-1.4\n% accuse DSF " + uuid.uuid4().hex.encode() + b"\n%%EOF\n"
    fichier = client.post(
        "/collecte/fichiers",
        params={"entreprise": dossier},
        files={"fichier": ("accuse.pdf", contenu, "application/pdf")},
    )
    assert fichier.status_code in (200, 201), fichier.text
    depot = client.post(
        "/collecte/pieces",
        json={"entreprise": dossier, "canal": canal, "empreinte": fichier.json()["empreinte"]},
    )
    assert depot.status_code in (200, 201), depot.text
    return depot.json()["piece"]["identifiant"]


ACCUSE = {"numero": "DGI-2026-DSF-118250", "depose_le": "2026-03-12T15:40:00"}


@exige_postgresql
class TestLeParcoursParLesRoutes:
    def test_preparee_validee_deposee_puis_declaree_a_l_echeancier(self, plateforme):
        client = plateforme
        _en_tant_que(client, COMPTABLE)
        preparee = client.post(f"{DSF}/preparation")
        assert preparee.status_code == 201, preparee.text
        assert preparee.json()["etat"] == "PREPAREE"

        apercu = client.get(f"{DSF}/1/document")
        assert apercu.status_code == 200
        assert apercu.headers["content-type"] == "application/pdf"
        assert preparee.json()["empreinte"] in _texte(apercu.content)

        _en_tant_que(client, REVISEUR)
        validee = client.post(f"{DSF}/1/validation")
        assert validee.status_code == 200, validee.text

        piece = _piece(client)
        _renforcer(client)
        deposee = client.post(f"{DSF}/1/depot", json={**ACCUSE, "piece": piece})
        assert deposee.status_code == 200, deposee.text
        assert deposee.json()["etat"] == "DEPOSEE"

        echeancier = client.get(
            f"/obligations/dossiers/{NGUEMA}/echeancier",
            params={"exercice": "2025", "a_la_date": "2026-10-14"},
        )
        assert echeancier.status_code == 200, echeancier.text
        (dsf,) = [
            ligne["obligation"]
            for ligne in echeancier.json()
            if ligne["obligation"]["code_obligation"] == "DSF"
        ]
        assert dsf["statut"] == "DECLAREE", dsf
        assert dsf["reference_depot"] == ACCUSE["numero"]

        versions = client.get(DSF).json()["versions"]
        assert [v["etat"] for v in versions] == ["DEPOSEE"]

    def test_chacun_son_geste(self, plateforme):
        client = plateforme
        _en_tant_que(client, REVISEUR)
        assert client.post(f"{DSF}/preparation").status_code == 201

        propre = client.post(f"{DSF}/1/validation")
        _en_tant_que(client, COMPTABLE)
        comptable = client.post(f"{DSF}/1/validation")

        assert propre.status_code == 409, propre.text  # préparée par elle
        assert comptable.status_code == 403, comptable.text

    def test_sans_second_facteur_pas_de_depot(self, plateforme):
        client = plateforme
        _en_tant_que(client, COMPTABLE)
        client.post(f"{DSF}/preparation")
        _en_tant_que(client, REVISEUR)
        client.post(f"{DSF}/1/validation")

        refus = client.post(f"{DSF}/1/depot", json=ACCUSE)

        assert refus.status_code == 403, refus.text

    def test_l_accuse_est_une_piece_de_ce_dossier(self, plateforme):
        client = plateforme
        _en_tant_que(client, COMPTABLE)
        client.post(f"{DSF}/preparation")
        d_ailleurs = _piece(client, COLOMBE, courriel="mc.essomba@lacolombe.cm", canal="PORTAIL")
        _en_tant_que(client, REVISEUR)
        client.post(f"{DSF}/1/validation")
        _renforcer(client)

        refus = client.post(f"{DSF}/1/depot", json={**ACCUSE, "piece": d_ailleurs})

        assert refus.status_code == 404, refus.text
        assert client.get(DSF).json()["versions"][0]["etat"] == "VALIDEE"

    def test_les_chiffres_figes_sont_ceux_de_l_exercice_repris(self, plateforme):
        client = plateforme
        _en_tant_que(client, COMPTABLE)

        preparee = client.post(f"{DSF}/preparation").json()

        assert preparee["resultat_comptable"] == "27520000"
        # Le nom se lit ; l'identifiant du compte reste la trace.
        assert preparee["preparee_par_nom"] == "Christelle NDONGO"
        assert preparee["preparee_par"] == "C-005"
        # L'abattement CGA s'applique : le résultat fiscal est sous le résultat comptable.
        assert 0 < float(preparee["resultat_fiscal"]) < 27520000
        texte = _texte(client.get(f"{DSF}/1/document").content)
        assert "CABINET NGUEMA CONSEIL" in texte and "32 520 000" in texte
        assert "Christelle NDONGO (C-005)" in texte
        # ⚠️ Le passif est imprimé hors résultat : sans le dire, 32 M contre 5 M se lirait
        # comme un bilan déséquilibré.
        assert "hors résultat de l'exercice" in texte

    def test_une_dsf_deja_deposee_a_la_dgi_ne_se_prepare_pas(self, plateforme):
        """⚠️ Bâtiment Plus : sa DSF 2025 porte un accusé consigné hors de ce parcours."""
        client = plateforme
        _en_tant_que(client, REVISEUR)

        refus = client.post(f"/cloture/dossiers/{BATIMENT}/dsf/2025/preparation")

        assert refus.status_code == 409, refus.text
        assert "déjà déposée à la DGI" in refus.json()["detail"]

    def test_un_exercice_ouvert_ne_se_prepare_pas(self, plateforme):
        client = plateforme
        _en_tant_que(client, COMPTABLE)

        refus = client.post(f"/cloture/dossiers/{NGUEMA}/dsf/2026/preparation")

        assert refus.status_code == 409, refus.text
        assert "n'est pas clos" in refus.json()["detail"]

    def test_le_constat_libre_refuse_la_dsf(self, plateforme):
        client = plateforme
        _en_tant_que(client, REVISEUR)
        _renforcer(client)

        refus = client.post(
            f"/obligations/dossiers/{NGUEMA}/depots",
            json={
                "code_obligation": "DSF",
                "periode_debut": "2025-01-01",
                "periode_fin": "2025-12-31",
                **ACCUSE,
            },
        )

        assert refus.status_code == 409, refus.text
        assert "/cloture/dossiers/" in refus.json()["detail"]


class TestLExerciceRepris:
    def test_son_a_nouveau_est_celui_que_la_cloture_aurait_pose(self):
        """⚠️ Écrit à la main en comptabilité (qui ne lit pas la clôture) : il doit rester
        identique, ligne à ligne, à `lignes_d_a_nouveau` sur la balance 2025."""
        from app.contextes.cloture.domaine.a_nouveau import lignes_d_a_nouveau
        from app.contextes.comptabilite.adaptateurs.entrant.routes_http import _plan
        from app.contextes.comptabilite.api import balance, ecritures_demo

        ecritures = ecritures_demo(NGUEMA)
        attendu = lignes_d_a_nouveau(
            balance([e for e in ecritures if e.exercice == "2025"]), plan=_plan.charger()
        )
        (pose,) = [e for e in ecritures if e.exercice == "2026" and e.journal == "AN"]

        def lignes(ls):
            return sorted((ligne.compte, ligne.sens, ligne.montant) for ligne in ls)

        assert lignes(pose.lignes) == lignes(attendu)
