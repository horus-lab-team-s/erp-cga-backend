"""La proforma : le document qui devient contrat.

Quatre familles, et la première commande les trois autres :

* **l'immuabilité**, parce que ce qui a été envoyé doit ressortir à l'identique
  dix ans plus tard, y compris devant un tribunal ;
* **la numérotation continue**, parce qu'une série trouée fait chercher un
  document qui n'a jamais existé ;
* **le lien d'acceptation**, signé, daté, à usage unique, et sans compte ;
* **l'écart au barème**, qui se motive quand il sort de l'intervalle.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contextes.souscription.domaine.proforma import (
    Acceptation,
    EcartAuBareme,
    EtatProforma,
    LienInvalide,
    ModificationInterdite,
    Proforma,
    TarifArrete,
    accepter,
    emettre,
    empreinte,
    lien_pour,
    numero_suivant,
    serie_continue,
)
from app.contextes.souscription.domaine.tarification import Debours

T0 = datetime(2026, 9, 10, 10, 0)
SECRET = "un-secret-de-deploiement-suffisamment-long"
DOCUMENT = b"%PDF-1.7 la proforma telle qu'elle a ete envoyee"


def _tarif(**surcharges) -> TarifArrete:
    defauts = {
        "montant": Decimal("250000"),
        "plancher": Decimal("200000"),
        "reference": Decimal("250000"),
        "plafond": Decimal("375000"),
        "version_bareme": "2026.1",
        "chiffre_par": "awono",
        "valide_par": "direction",
        "arrete_le": T0,
    }
    return TarifArrete(**{**defauts, **surcharges})


def _proforma(**surcharges) -> Proforma:
    defauts = {
        "numero": "PRO-2026-0001",
        "dossier": "dos-1",
        "service": "creation-sarl",
        "tarif": _tarif(),
        "contenu": DOCUMENT,
        "modele": "creation-sarl",
        "version_modele": "2026.1",
        "a_l_instant": T0,
    }
    return emettre(**{**defauts, **surcharges})


# ── L'écart au barème ─────────────────────────────────────────────────────────


class TestEcartAuBareme:
    def test_dans_l_intervalle_aucun_motif_n_est_demande(self):
        assert _tarif().ecart is EcartAuBareme.DANS_L_INTERVALLE

    @pytest.mark.parametrize(
        ("montant", "attendu"),
        [
            (Decimal("150000"), EcartAuBareme.SOUS_LE_PLANCHER),
            (Decimal("400000"), EcartAuBareme.AU_DESSUS_DU_PLAFOND),
        ],
    )
    def test_hors_intervalle_le_motif_devient_obligatoire(self, montant, attendu):
        """Un écart accordé sans raison écrite est un écart que personne ne saura
        défendre six mois plus tard."""
        with pytest.raises(ValidationError, match="sans motif"):
            _tarif(montant=montant)
        motive = _tarif(montant=montant, motif="client historique, remise accordée")
        assert motive.ecart is attendu

    def test_l_ecart_a_la_reference_est_signe(self):
        """C'est ce que le pilotage agrège pour voir la dérive : un cabinet qui
        vend systématiquement sous la référence a un problème de barème, pas de
        commerciaux."""
        assert _tarif(montant=Decimal("220000")).ecart_a_la_reference == Decimal(
            "-30000"
        )

    def test_la_separation_est_constatee_jamais_imposee(self):
        """⚠️ Refuser rendrait le produit inutilisable dans un cabinet de trois
        personnes, et ferait contourner la traçabilité par un compte partagé, ce
        qui est bien pire que la situation qu'on voulait éviter."""
        seul = _tarif(chiffre_par="awono", valide_par="awono")
        assert seul.separation_respectee is False
        assert _tarif().separation_respectee is True


# ── La numérotation ───────────────────────────────────────────────────────────


class TestNumerotation:
    def test_la_premiere_de_l_annee(self):
        assert numero_suivant(None, serie="PRO", annee=2026) == "PRO-2026-0001"

    def test_le_rang_s_incremente(self):
        assert numero_suivant("PRO-2026-0041", serie="PRO", annee=2026) == "PRO-2026-0042"

    def test_le_rang_repart_a_un_l_annee_suivante(self):
        assert numero_suivant("PRO-2026-0350", serie="PRO", annee=2027) == "PRO-2027-0001"

    def test_un_dernier_numero_illisible_leve(self):
        """Poursuivre une série dont on ne sait pas lire le dernier numéro
        produirait un doublon ou un trou."""
        with pytest.raises(ValueError, match="SERIE-ANNEE-RANG"):
            numero_suivant("42", serie="PRO", annee=2026)

    def test_une_serie_sans_trou_est_continue(self):
        assert serie_continue(["PRO-2026-0001", "PRO-2026-0002", "PRO-2026-0003"])

    def test_une_serie_trouee_ne_l_est_pas(self):
        """Un contrôle qui constate un saut demande où est passé le document
        manquant, et « nulle part » est une réponse qu'on ne peut pas prouver."""
        assert not serie_continue(["PRO-2026-0001", "PRO-2026-0003"])

    def test_les_annees_se_comptent_separement(self):
        assert serie_continue(
            ["PRO-2026-0001", "PRO-2026-0002", "PRO-2027-0001"]
        )

    def test_une_serie_vide_est_continue(self):
        assert serie_continue([])


# ── L'immuabilité ─────────────────────────────────────────────────────────────


class TestImmuabilite:
    def test_aucune_methode_ne_change_un_montant(self):
        """⚠️ **Le piège nommé par le document de conception.** La seule façon
        d'obtenir un autre montant est `nouvelle_version`, qui produit un objet
        distinct et marque le précédent."""
        muables = [
            nom
            for nom in dir(Proforma)
            if not nom.startswith("_")
            and callable(getattr(Proforma, nom, None))
            and nom in {"changer_montant", "ajuster", "modifier"}
        ]
        assert muables == []

    def test_la_proforma_est_figee(self):
        with pytest.raises(ValidationError):
            _proforma().numero = "PRO-2026-9999"

    def test_l_empreinte_detecte_une_alteration(self):
        """Un document touché après coup ne vaut plus rien devant un tribunal.
        Encore faut-il pouvoir le dire."""
        p = _proforma()
        assert p.document_intact(DOCUMENT) is True
        assert p.document_intact(DOCUMENT + b" ") is False

    def test_l_empreinte_est_celle_du_contenu(self):
        assert _proforma().empreinte_document == empreinte(DOCUMENT)


class TestNouvelleVersion:
    def _v2(self, depart: Proforma | None = None):
        p = depart or _proforma()
        return p.nouvelle_version(
            _tarif(montant=Decimal("300000")),
            numero="PRO-2026-0002",
            contenu=b"%PDF-1.7 la version deux",
            a_l_instant=T0 + timedelta(days=1),
        )

    def test_elle_rend_les_deux_ensemble(self):
        """Rendre la seule nouvelle laisserait l'appelant oublier de marquer
        l'ancienne, et **deux proformas actives pour le même engagement**."""
        remplacee, suivante = self._v2()
        assert remplacee.etat is EtatProforma.REMPLACEE
        assert suivante.etat is EtatProforma.EMISE

    def test_la_remplacee_reste_consultable(self):
        """Le client qui revient voit ce qu'on lui avait proposé, pas une page
        introuvable."""
        remplacee, _ = self._v2()
        assert remplacee.tarif.montant == Decimal("250000")
        assert remplacee.empreinte_document == empreinte(DOCUMENT)

    def test_la_nouvelle_porte_un_numero_et_une_version_distincts(self):
        """Ce ne sont pas deux états d'un document, ce sont deux documents."""
        _, suivante = self._v2()
        assert suivante.numero == "PRO-2026-0002"
        assert suivante.version == 2
        assert suivante.remplace == "PRO-2026-0001"

    def test_la_nouvelle_a_sa_propre_empreinte(self):
        _, suivante = self._v2()
        assert suivante.empreinte_document == empreinte(b"%PDF-1.7 la version deux")

    def test_la_nouvelle_repart_non_transmise(self):
        """Le calendrier de relance repart de la transmission de **cette**
        version : relancer sur la date de la précédente relancerait un client
        sur un montant qu'il n'a plus."""
        p = _proforma().transmise(T0)
        _, suivante = self._v2(p)
        assert suivante.transmise_le is None
        assert suivante.etat is EtatProforma.EMISE

    def test_on_ne_remplace_pas_une_proforma_acceptee(self):
        """⚠️ Elle vaut contrat. Ce qui suit une acceptation est un avenant, pas
        une version de plus, et c'est un autre objet parce que c'est un autre
        accord."""
        acceptee = _proforma().acceptee()
        with pytest.raises(ModificationInterdite, match="avenant"):
            self._v2(acceptee)

    def test_une_version_ulterieure_dit_ce_qu_elle_remplace(self):
        """Une suite de versions dont on ne peut pas remonter le fil ne prouve
        rien."""
        with pytest.raises(ValidationError, match="sans numéro remplacé"):
            _proforma().model_copy(update={"version": 2, "remplace": None}).model_validate(
                _proforma().model_dump() | {"version": 2, "remplace": None}
            )


# ── Les transitions ───────────────────────────────────────────────────────────


class TestTransitions:
    def test_la_transmission_est_rejouable_sans_repousser_la_date(self):
        """Retransmettre sur un autre canal ne fait pas repartir le calendrier
        de relance. Le repousser relancerait un client indéfiniment."""
        p = _proforma().transmise(T0)
        retransmise = p.transmise(T0 + timedelta(days=2))
        assert retransmise.transmise_le == T0
        assert retransmise is p

    def test_une_proforma_annulee_ne_s_accepte_pas(self):
        with pytest.raises(ModificationInterdite):
            _proforma().annulee().acceptee()

    def test_une_proforma_acceptee_ne_s_annule_pas(self):
        """Elle engage les deux parties. Un accord de rupture est un autre geste,
        et il laisse sa propre trace."""
        with pytest.raises(ModificationInterdite, match="engage les deux parties"):
            _proforma().acceptee().annulee()

    def test_annuler_deux_fois_ne_change_rien(self):
        annulee = _proforma().annulee()
        assert annulee.annulee() is annulee

    def test_le_total_ajoute_les_debours(self):
        p = _proforma(
            debours=(
                Debours(code="ENR", libelle="Enregistrement", montant=Decimal("120000")),
            )
        )
        assert p.total == Decimal("370000")


# ── Le lien d'acceptation ─────────────────────────────────────────────────────


class TestLienDAcceptation:
    def _lien(self, p: Proforma | None = None, **surcharges):
        return lien_pour(
            p or _proforma(),
            expire_le=surcharges.get("expire_le", T0 + timedelta(days=14)),
            secret=SECRET,
        )

    def test_il_est_valide_dans_sa_periode(self):
        assert self._lien().valide_a(T0 + timedelta(days=1), secret=SECRET) is True

    def test_il_expire(self):
        lien = self._lien()
        assert lien.valide_a(T0 + timedelta(days=15), secret=SECRET) is False
        with pytest.raises(LienInvalide, match="expiré"):
            lien.exiger_valide(T0 + timedelta(days=15), secret=SECRET)

    def test_un_sceau_forge_est_refuse(self):
        lien = self._lien().model_copy(update={"sceau": "a" * 64})
        with pytest.raises(LienInvalide, match="sceau invalide"):
            lien.exiger_valide(T0, secret=SECRET)

    def test_un_secret_different_invalide_le_lien(self):
        """Un lien de recette ne doit pas ouvrir une proforma de production."""
        with pytest.raises(LienInvalide, match="sceau invalide"):
            self._lien().exiger_valide(T0, secret="un-autre-secret-de-longueur-egale")

    def test_le_sceau_couvre_la_version(self):
        """⚠️ Sans la version, un lien émis pour la v1 permettrait d'accepter la
        v2 : le client accepterait un montant qu'il n'a jamais vu."""
        v1 = self._lien()
        _, v2 = _proforma().nouvelle_version(
            _tarif(montant=Decimal("300000")), numero="PRO-2026-0002",
            contenu=b"v2", a_l_instant=T0,
        )
        force = v1.model_copy(update={"version": 2})
        with pytest.raises(LienInvalide, match="sceau invalide"):
            force.exiger_valide(T0, secret=SECRET)

    def test_il_est_a_usage_unique(self):
        """Le rouvrir ne permet pas d'accepter deux fois."""
        employe = self._lien().employe_a(T0)
        assert employe.employe is True
        with pytest.raises(LienInvalide, match="déjà employé"):
            employe.exiger_valide(T0, secret=SECRET)

    def test_marquer_employe_deux_fois_leve(self):
        """C'est cette méthode qui porte l'unicité d'usage. La rendre idempotente
        reviendrait à ne pas l'avoir."""
        employe = self._lien().employe_a(T0)
        with pytest.raises(LienInvalide):
            employe.employe_a(T0 + timedelta(minutes=1))

    def test_les_trois_causes_ont_trois_messages(self):
        """Un « lien invalide » générique ferait chercher au support ce que le
        message peut dire."""
        expire = self._lien()
        employe = self._lien().employe_a(T0)
        forge = self._lien().model_copy(update={"sceau": "b" * 64})
        motifs = []
        for lien, instant in (
            (employe, T0),
            (expire, T0 + timedelta(days=20)),
            (forge, T0),
        ):
            with pytest.raises(LienInvalide) as echec:
                lien.exiger_valide(instant, secret=SECRET)
            motifs.append(str(echec.value))
        assert len({m.split(":")[-1] for m in motifs}) == 3


# ── L'acceptation ─────────────────────────────────────────────────────────────


class TestAcceptation:
    def _accepter(self, p: Proforma | None = None, **surcharges):
        proforma = p or _proforma()
        lien = lien_pour(proforma, expire_le=T0 + timedelta(days=14), secret=SECRET)
        defauts = {
            "identite_declaree": "Abena Ndzana, gérante",
            "a_l_instant": T0 + timedelta(days=2),
            "secret": SECRET,
            "origine": "41.202.0.1",
        }
        return accepter(proforma, lien, **{**defauts, **surcharges})

    def test_elle_rend_les_trois_ensemble(self):
        proforma, lien, acceptation = self._accepter()
        assert proforma.etat is EtatProforma.ACCEPTEE
        assert lien.employe is True
        assert isinstance(acceptation, Acceptation)

    def test_elle_fige_le_montant_la_version_et_l_empreinte(self):
        """Recopiés, pas référencés : l'acceptation reste lisible seule, y
        compris dans un export."""
        _, _, acceptation = self._accepter()
        assert acceptation.montant == Decimal("250000")
        assert acceptation.version == 1
        assert acceptation.empreinte_document == empreinte(DOCUMENT)

    def test_elle_conserve_l_identite_declaree_et_l_origine(self):
        _, _, acceptation = self._accepter()
        assert acceptation.identite_declaree == "Abena Ndzana, gérante"
        assert acceptation.origine == "41.202.0.1"

    def test_un_lien_pour_une_autre_version_est_refuse(self):
        """Accepter ainsi ferait engager le client sur un montant qu'il n'a pas
        vu."""
        v1 = _proforma()
        lien = lien_pour(v1, expire_le=T0 + timedelta(days=14), secret=SECRET)
        _, v2 = v1.nouvelle_version(
            _tarif(montant=Decimal("300000")), numero="PRO-2026-0002",
            contenu=b"v2", a_l_instant=T0,
        )
        with pytest.raises(LienInvalide, match="présenté sur"):
            accepter(
                v2, lien, identite_declaree="x", a_l_instant=T0, secret=SECRET
            )

    def test_le_lien_est_verifie_avant_la_proforma(self):
        """⚠️ Un lien mal signé ne doit **rien** apprendre sur l'existence ou
        l'état d'une proforma : c'est la seule chose qui protège un numéro
        devinable."""
        annulee = _proforma().annulee()
        forge = lien_pour(
            annulee, expire_le=T0 + timedelta(days=14), secret=SECRET
        ).model_copy(update={"sceau": "c" * 64})
        with pytest.raises(LienInvalide, match="sceau invalide"):
            accepter(annulee, forge, identite_declaree="x", a_l_instant=T0, secret=SECRET)

    def test_on_n_accepte_pas_deux_fois(self):
        proforma, lien, _ = self._accepter()
        with pytest.raises(LienInvalide, match="déjà employé"):
            accepter(
                proforma, lien, identite_declaree="x",
                a_l_instant=T0 + timedelta(days=3), secret=SECRET,
            )

    def test_elle_ne_demande_aucun_compte(self):
        """Demander une inscription à ce moment précis fait perdre une partie des
        prospects, et n'apporte rien : le compte se crée à l'ouverture du
        tenant."""
        champs = set(Acceptation.model_fields)
        assert not {"compte", "utilisateur", "jeton"} & champs
