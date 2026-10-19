"""Les gabarits des courriels transactionnels, et leur mise en page.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI DES GABARITS EN CODE, ET NON EN BASE ÉDITABLE

Le module `mail+paiement/` du dépôt range les siens dans une table modifiable
depuis l'administration. C'est la bonne cible, et ce n'est pas le bon point de
départ.

Ces messages-là ne sont pas de la communication : ils portent un **lien
d'activation**, un **délai de validité** et une **procédure**. Une coquille dans
« valable 2 heures » se répare ; une coquille dans le lien laisse un adhérent qui
a payé sans accès, et personne ne s'en aperçoit — le message est parti, le
tableau de bord est vert. En code, la revue voit le changement avant l'envoi.

Le jour où le cabinet voudra régler le ton, l'édition en base arrivera **avec**
son écran et sa journalisation. Le port ne bougera pas.

DEUX RÈGLES DE SÛRETÉ, ET ELLES COMPTENT PLUS QUE LA MISE EN PAGE

**1 · Une clé manquante annule l'envoi.** `str.format` sur un gabarit auquel il
manque `{lien}` produirait un courriel affichant `{lien}` en toutes lettres. Le
destinataire croirait avoir été servi et n'aurait aucun recours. Ne rien envoyer
laisse au moins la trace d'un échec à rejouer. `cles_manquantes` fait ce contrôle
avant tout rendu.

**2 · Le contexte est échappé avant d'entrer dans le HTML.** Une dénomination
sociale contenant `<` casserait la mise en page ; un contexte hostile y logerait
un lien. Le corps texte reçoit la valeur brute, le corps HTML la valeur échappée.
C'est la même donnée rendue deux fois, et une seule des deux est du balisage.

POURQUOI DES TABLES ET DU CSS EN LIGNE

Parce que les clients de messagerie n'ont pas de moteur de rendu moderne :
Outlook ignore `flex`, Gmail retire `<style>`. Un courriel se met en page comme
en 2003, et ce n'est pas négociable. La palette reprend celle de l'application
— `Frontend_erp_cga/app/styles/tokens.css` — pour que le message et le site se
reconnaissent.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from html import escape
from string import Formatter
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.partage.marque import logo_disponible

__all__ = [
    "CATALOGUE",
    "Gabarit",
    "MessageRendu",
    "cles_du_gabarit",
    "gabarit",
]

# ── Palette, reprise de tokens.css ──────────────────────────────────────────

_INDIGO_900 = "#2e1b4d"
_INDIGO_700 = "#492f79"
_MAGENTA_600 = "#8c2d86"
_INK_900 = "#1a1523"
_INK_500 = "#6b6480"
_LINE_200 = "#e5e1ec"
_SURFACE = "#ffffff"
_SURFACE_ALT = "#faf9fc"
_WARNING_100 = "#fbf0e2"
_WARNING = "#b4690e"

#: Le pied de page, identique sur tous les messages. L'agrément y figure parce
#: qu'il est la seule chose qui distingue un CGA d'un cabinet ordinaire, et
#: qu'un destinataire qui doute de l'expéditeur le cherche.
#: ⚠️ **AUCUN TIRET CADRATIM DANS CE QUE LE CLIENT LIT.** La règle valait pour le
#: document de conception, où la chaîne d'intégration la vérifie ; le cabinet l'a
#: étendue à tous les messages le 30 septembre. Trente-sept en sortaient, dont
#: trente-quatre venaient de ce pied et de son séparateur.
#:
#: Le nom et l'agrément sont désormais séparés par une virgule, qui dit la même
#: chose sans le trait.
_PIED = (
    "CGA Broad Range Consulting Group, agrément MINFI/DGI n° 00000048<br />"
    "Douala Akwa · Yaoundé Elig-Essono · Bafoussam"
)


class Gabarit(BaseModel):
    """Un message transactionnel, avant rendu.

    Les chaînes portent des marques `{cle}` au format `str.format`. Ce choix est
    délibéré : un moteur de gabarit complet exécuterait des expressions venues
    d'une future table éditable, et un courriel n'a aucun besoin de boucles.
    """

    model_config = ConfigDict(frozen=True)

    code: str
    objet: str
    titre: str
    #: Les paragraphes du corps, dans l'ordre.
    paragraphes: tuple[str, ...]
    #: Le bouton d'action : libellé et gabarit d'adresse. Facultatif — tous les
    #: messages n'appellent pas un geste.
    action: tuple[str, str] | None = None
    #: L'encart d'avertissement, rendu en jaune. Sert aux délais de validité et
    #: aux consignes de sécurité, que le corps courant noierait.
    avertissement: str | None = None
    #: La clé du contexte qui porte un TABLEAU : une liste de couples
    #: `(désignation, valeur)`, dont le dernier est le total.
    #:
    #: ⚠️ C'est la seule donnée du contexte qui ne soit pas un texte, et elle ne
    #: passe pas par les marques `{cle}` : un tableau n'a pas de longueur fixe,
    #: et un gabarit n'a pas de boucles — voir l'en-tête de la classe. Elle est
    #: facultative à l'envoi : absente du contexte, le tableau ne se dessine pas.
    tableau: str | None = None


class MessageRendu(BaseModel):
    """Le message prêt à partir : objet, corps texte, corps HTML."""

    model_config = ConfigDict(frozen=True)

    objet: str
    texte: str
    html: str


def cles_du_gabarit(gabarit_: Gabarit) -> frozenset[str]:
    """Toutes les marques `{cle}` du gabarit, objet et action compris.

    Sert au contrôle d'avant-envoi et au test qui vérifie que chaque gabarit du
    catalogue se rend avec le contexte que ses appelants fournissent.
    """
    morceaux = [gabarit_.objet, gabarit_.titre, *gabarit_.paragraphes]
    if gabarit_.action is not None:
        morceaux.extend(gabarit_.action)
    if gabarit_.avertissement is not None:
        morceaux.append(gabarit_.avertissement)
    lecteur = Formatter()
    return frozenset(nom for morceau in morceaux for _, nom, _, _ in lecteur.parse(morceau) if nom)


def cles_manquantes(gabarit_: Gabarit, contexte: dict[str, Any]) -> frozenset[str]:
    """Les marques que le contexte ne sait pas remplir. Voir l'en-tête, règle 1."""
    return cles_du_gabarit(gabarit_) - frozenset(contexte)


# ── Rendu ───────────────────────────────────────────────────────────────────


def _remplir(modele: str, valeurs: dict[str, str]) -> str:
    """`str.format` restreint aux marques nommées.

    Les accolades d'un gabarit sont toutes des marques : aucun de ces messages
    ne contient de JSON ni de code. Une accolade littérale serait donc une
    faute de frappe, et il vaut mieux qu'elle se voie.
    """
    return modele.format(**valeurs)


#: Les seules balises admises dans un gabarit. Le corps texte les retire.
#:
#: Une liste blanche plutôt qu'une expression régulière générique : elle dit ce
#: que le gabarit a le droit d'employer, et un `<table>` glissé dans un
#: paragraphe se verrait tout de suite au lieu de disparaître en silence.
_BALISES_ADMISES = ("<strong>", "</strong>", "<em>", "</em>")


def _sans_balises(texte: str) -> str:
    """Le paragraphe débarrassé de son balisage, pour la version texte.

    Un destinataire dont le client de messagerie n'affiche pas le HTML — ou qui
    l'a désactivé, ce qui est fréquent en entreprise — lirait sinon
    « votre souscription <strong>SOUS-2026-0007</strong> ».
    """
    for balise in _BALISES_ADMISES:
        texte = texte.replace(balise, "")
    return texte


def _texte(
    gabarit_: Gabarit, valeurs: dict[str, str], tableau: list[tuple[str, str]] | None = None
) -> str:
    lignes = [_sans_balises(_remplir(gabarit_.titre, valeurs)), ""]
    # 18 octobre 2026 : un paragraphe qui se remplit à vide ne s'écrit pas (la réponse à un
    # candidat en a jusqu'à dix, selon la longueur du texte relu).
    lignes.extend(
        _sans_balises(_remplir(p, valeurs)) + "\n"
        for p in gabarit_.paragraphes
        if _remplir(p, valeurs).strip()
    )
    if tableau:
        lignes.extend(f"  {designation} : {valeur}" for designation, valeur in tableau)
        lignes.append("")
    if gabarit_.action is not None:
        libelle, adresse = gabarit_.action
        lignes.append(f"{_remplir(libelle, valeurs)} : {_remplir(adresse, valeurs)}\n")
    if gabarit_.avertissement is not None:
        lignes.append(_sans_balises(_remplir(gabarit_.avertissement, valeurs)) + "\n")
    # ⚠️ `-- ` est le séparateur de signature des courriels, posé par la RFC 3676 :
    # les clients de messagerie le reconnaissent et replient ce qui suit. Un tiret
    # cadratin ne leur dit rien, et le cabinet n'en veut nulle part.
    lignes.append("-- ")
    lignes.append("CGA Broad Range Consulting Group, agrément MINFI/DGI n° 00000048")
    lignes.append("Douala Akwa · Yaoundé Elig-Essono · Bafoussam")
    return "\n".join(lignes)


#: L'identifiant sous lequel le logo voyage DANS le message.
#:
#: ⚠️ Une image d'en-tête ne peut pas être une adresse : en démonstration elle
#: pointerait `localhost`, et la plupart des messageries bloquent de toute façon
#: les images distantes tant que le destinataire n'a pas cliqué. Elle ne peut pas
#: non plus être encodée dans le HTML, que Gmail supprime. Reste l'image jointe
#: au message et désignée par `cid:` : c'est la seule forme affichée partout.
CID_DU_LOGO = "logo-cga"


def _tableau_html(tableau: list[tuple[str, str]]) -> str:
    """Le détail, au style de la proforma : lignes fines, total en cartouche."""
    rangees = []
    for rang, (designation, valeur) in enumerate(tableau):
        dernier = rang == len(tableau) - 1
        style = (
            f"padding:11px 14px;font-size:15px;font-weight:700;color:{_INK_900};"
            f"border:1.5px solid {_INK_900};"
            if dernier
            else f"padding:8px 14px;font-size:14px;color:{_INK_900};"
            f"border-bottom:1px solid {_LINE_200};"
        )
        droite = "border-left:0;" if dernier else ""
        gauche = "border-right:0;" if dernier else ""
        rangees.append(
            f'<tr><td style="{style}{gauche}">{escape(designation, quote=True)}</td>'
            f'<td align="right" style="{style}{droite}white-space:nowrap;">'
            f"{escape(valeur, quote=True)}</td></tr>"
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="margin:6px 0 22px;border-collapse:collapse;">' + "".join(rangees) + "</table>"
    )


def _html(
    gabarit_: Gabarit, valeurs: dict[str, str], tableau: list[tuple[str, str]] | None = None
) -> str:
    """Le corps HTML. Tables et CSS en ligne — voir l'en-tête."""
    corps = "".join(
        f'<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:{_INK_900};">'
        f"{_remplir(p, valeurs)}</p>"
        for p in gabarit_.paragraphes
        if _remplir(p, valeurs).strip()
    )
    if tableau:
        corps += _tableau_html(tableau)

    if gabarit_.action is not None:
        libelle, adresse = gabarit_.action
        corps += (
            '<table role="presentation" cellpadding="0" cellspacing="0" '
            'style="margin:24px 0;"><tr><td '
            f'style="background:{_MAGENTA_600};border-radius:6px;">'
            f'<a href="{_remplir(adresse, valeurs)}" '
            'style="display:inline-block;padding:13px 26px;font-size:15px;'
            'font-weight:600;color:#ffffff;text-decoration:none;">'
            f"{_remplir(libelle, valeurs)}</a></td></tr></table>"
        )

    if gabarit_.avertissement is not None:
        corps += (
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="margin:20px 0;background:{_WARNING_100};border-left:3px solid '
            f'{_WARNING};border-radius:4px;"><tr><td style="padding:14px 16px;'
            f'font-size:14px;line-height:1.55;color:{_INK_900};">'
            f"{_remplir(gabarit_.avertissement, valeurs)}</td></tr></table>"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # ⚠️ LE TEXTE DE PRÉVISUALISATION, CELUI QU'ON LIT SANS OUVRIR.
    #
    # Les messageries affichent, à côté de l'objet, les premiers mots trouvés
    # dans le corps. Sans ce bloc, elles prenaient le bandeau : chaque message du
    # cabinet s'annonçait « CGA Broad Range Consulting Group », dix-huit fois la
    # même ligne, qui n'apprend rien à qui décide d'ouvrir ou non.
    #
    # Il porte donc le début du **premier paragraphe**, sans balises. Caché à
    # l'affichage par les trois propriétés que les clients respectent, et suivi
    # d'espaces insécables larges pour que la messagerie n'aille pas chercher la
    # suite dans le bandeau.
    # ─────────────────────────────────────────────────────────────────────────
    apercu = _sans_balises(_remplir(gabarit_.paragraphes[0], valeurs))[:110]
    prevu = (
        f'<div style="display:none;max-height:0;overflow:hidden;'
        f'mso-hide:all;">{apercu}{"&#8199;&#65279;" * 30}</div>'
    )

    return f"""\
{prevu}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" \
style="background:{_SURFACE_ALT};padding:28px 12px;">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" \
style="max-width:560px;background:{_SURFACE};border:1px solid {_LINE_200};\
border-radius:10px;overflow:hidden;font-family:-apple-system,'Segoe UI',\
Roboto,Helvetica,Arial,sans-serif;">
<tr><td style="padding:0;">{_bandeau()}</td></tr>
<tr><td style="padding:28px;">
<h1 style="margin:0 0 18px;font-size:20px;line-height:1.35;color:{_INDIGO_700};">\
{_remplir(gabarit_.titre, valeurs)}</h1>
{corps}
</td></tr>
<tr><td style="padding:18px 28px;border-top:1px solid {_LINE_200};\
font-size:12px;line-height:1.6;color:{_INK_500};">{_PIED}<br />\
<span style="color:{_INK_500};">Ce message vous est adressé par le cabinet qui \
tient votre dossier. Pour toute question, répondez-y simplement.</span></td></tr>
</table>
</td></tr>
</table>"""


def rendre(gabarit_: Gabarit, contexte: dict[str, Any]) -> MessageRendu:
    """Rend l'objet, le corps texte et le corps HTML.

    ⚠️ Lève `KeyError` si une marque manque. L'appelant doit avoir consulté
    `cles_manquantes` d'abord — voir l'en-tête, règle 1.
    """
    tableau = _tableau_du_contexte(gabarit_, contexte)
    brutes = {
        cle: "" if valeur is None else str(valeur)
        for cle, valeur in contexte.items()
        if cle not in CLES_NON_TEXTUELLES and cle != gabarit_.tableau
    }
    echappees = {cle: escape(valeur, quote=True) for cle, valeur in brutes.items()}
    return MessageRendu(
        objet=_remplir(gabarit_.objet, brutes),
        texte=_texte(gabarit_, brutes, tableau),
        html=_html(gabarit_, echappees, tableau),
    )


#: La clé du contexte qui porte les pièces jointes : une liste de triplets
#: `(nom du fichier, octets, type MIME)`. Lue par le transport, jamais rendue.
CLE_PIECES_JOINTES = "pieces_jointes"
#: Ce que le rendu ne doit pas convertir en texte : des octets passés par
#: `str()` deviendraient des kilo-octets de `b'...'` gardés en mémoire pour rien.
CLES_NON_TEXTUELLES = frozenset({CLE_PIECES_JOINTES})


def _tableau_du_contexte(
    gabarit_: Gabarit, contexte: dict[str, Any]
) -> list[tuple[str, str]] | None:
    """Les rangées du tableau, ou `None`. Tolérant : un contexte mal formé ne doit
    pas empêcher le message de partir sans son tableau."""
    if gabarit_.tableau is None:
        return None
    brut = contexte.get(gabarit_.tableau)
    if not isinstance(brut, (list, tuple)):
        return None
    rangees = [(str(r[0]), str(r[1])) for r in brut if isinstance(r, (list, tuple)) and len(r) == 2]
    return rangees or None


def _bandeau() -> str:
    """Le logo à gauche, le nom de la maison à sa droite : l'en-tête du document.

    ⚠️ Sans le fichier du logo, le bandeau reprend toute la largeur comme avant :
    un message sans la marque part quand même.
    """
    nom = (
        f'<td style="background:{_INDIGO_900};padding:18px 22px;">'
        '<span style="font-size:15px;font-weight:700;color:#ffffff;'
        'letter-spacing:.3px;">CGA Broad Range Consulting Group</span><br />'
        '<span style="font-size:10.5px;color:#d9d2ea;letter-spacing:.2px;">'
        "Assistance comptable et fiscale · Assistance juridique · Formations</span></td>"
    )
    logo = (
        f'<td width="112" style="background:#ffffff;padding:10px 12px;">'
        f'<img src="cid:{CID_DU_LOGO}" width="88" alt="CGA Broad Range" '
        'style="display:block;border:0;width:88px;height:auto;" /></td>'
        if logo_disponible()
        else ""
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        f"<tr>{logo}{nom}</tr></table>"
    )


# ── Le catalogue ────────────────────────────────────────────────────────────
#
# ⚠️ Les clés `{…}` doivent correspondre au contexte des appelants. Le test
# `test_courriel.py` vérifie chaque gabarit contre le contexte réel de son site
# d'appel — c'est ce qui empêche qu'un renommage passe inaperçu.

CATALOGUE: dict[str, Gabarit] = {
    g.code: g
    for g in (
        Gabarit(
            code="compte.activation",
            objet="Votre espace {denomination} est ouvert",
            titre="Bienvenue, {prenom}",
            paragraphes=(
                "Votre souscription <strong>{souscription}</strong> est encaissée et "
                "votre espace adhérent est ouvert pour <strong>{denomination}</strong>, "
                "au titre du service « {service} ».",
                "Il reste une étape : définir votre mot de passe. Personne chez CGA "
                "ne le connaîtra, et personne ne vous le demandera jamais.",
            ),
            action=("Définir mon mot de passe", "{lien}"),
            avertissement=(
                "Ce lien expire le {expire_le} et ne fonctionne qu'une fois. "
                "Passé ce délai, demandez-en un nouveau depuis la page de connexion."
            ),
        ),
        Gabarit(
            code="creation.dossier_ouvert",
            objet="Votre dossier {denomination} est ouvert",
            titre="Nous commençons, {prenom}",
            paragraphes=(
                # ─────────────────────────────────────────────────────────────
                # ⚠️ LE SEUL MESSAGE DES SEMAINES QUI SUIVENT.
                #
                # Entre son règlement et son immatriculation, la cliente ne
                # recevait rien et ne pouvait rien consulter. Le canal prévu
                # était la messagerie instantanée ; elle ne tient pas, la fenêtre
                # de service se refermant vingt-quatre heures après son dernier
                # mot, et une formalité durant des semaines.
                #
                # ⚠️ Ce message porte le lien de suivi, qui n'expire pas : sa
                # durée de vie est celle du dossier. Elle le range, et il ouvre
                # encore dans un mois.
                #
                # ⚠️ AUCUN DÉLAI N'EST ANNONCÉ. Les délais de greffe ne sont pas
                # au référentiel, et « sous quinze jours » écrit ici serait une
                # promesse que personne n'a signée.
                # ─────────────────────────────────────────────────────────────
                "Votre règlement est bien reçu, et le dossier de "
                "<strong>{denomination}</strong> est ouvert. Nous préparons son "
                "immatriculation.",
                "Il nous faut maintenant vos pièces. Vous pouvez les envoyer "
                "depuis votre suivi, en photographiant chaque document : la liste "
                "s'y trouve, et vous y voyez à tout moment où en est votre "
                "dossier.",
                "Gardez ce message : le lien ci-dessous reste valable jusqu'à "
                "l'immatriculation de votre société.",
            ),
            action=("Voir mon dossier et envoyer mes pièces", "{lien}"),
            avertissement=(
                "Ce lien vous est personnel : il ouvre votre dossier sans mot de "
                "passe. Ne le transférez pas."
            ),
        ),
        Gabarit(
            code="creation.dossier_ouvert_avec_compte",
            objet="Votre dossier {denomination} est ouvert",
            titre="Nous commençons, {prenom}",
            paragraphes=(
                # ─────────────────────────────────────────────────────────────
                # ⚠️ LE MÊME MESSAGE QUE `creation.dossier_ouvert`, ET UN COMPTE.
                #
                # Le cabinet l'a demandé le 14 octobre 2026 : le compte client
                # s'ouvre au paiement, et elle choisit son mot de passe. Le
                # bouton y mène.
                #
                # ⚠️ Le lien de suivi sans mot de passe reste dans le message,
                # en clair : c'est lui qui sert le jour où elle n'a pas le temps
                # de choisir un mot de passe, ou que le bouton a expiré. Un seul
                # bouton par message (voir `Gabarit`), et c'est le compte qui le
                # prend, parce que c'est lui qui dure.
                # ─────────────────────────────────────────────────────────────
                "Votre règlement est bien reçu, et le dossier de "
                "<strong>{denomination}</strong> est ouvert. Nous préparons son "
                "immatriculation.",
                "Votre espace client est créé à cette adresse. Choisissez votre "
                "mot de passe : vous y verrez chaque étape de votre création, et "
                "vous y enverrez vos pièces en photographiant chaque document.",
                "Pas le temps maintenant ? Votre dossier s'ouvre aussi sans mot "
                "de passe, depuis ce lien personnel, valable jusqu'à "
                "l'immatriculation : {lien}",
            ),
            action=("Choisir mon mot de passe", "{lien_compte}"),
            avertissement=(
                "Le bouton ne fonctionne qu'une fois. S'il a expiré, demandez un "
                "nouveau lien depuis « Mot de passe oublié » sur la page de "
                "connexion. Le lien de suivi vous est personnel : ne le "
                "transférez pas."
            ),
        ),
        Gabarit(
            code="compte.adhesion_ajoutee",
            objet="Votre société {denomination} est immatriculée",
            titre="Félicitations, {prenom}",
            paragraphes=(
                # ⚠️ ELLE A DÉJÀ SON COMPTE : PAS DE LIEN D'ACTIVATION.
                #
                # Ouvert au paiement de sa création, son compte reçoit à
                # l'immatriculation l'espace adhérent de sa société. Lui
                # renvoyer `compte.activation_creation` lui ferait choisir un
                # mot de passe qu'elle a déjà, sur un lien qui n'existe pas.
                "<strong>{denomination}</strong> est immatriculée. Son numéro de "
                "contribuable est <strong>{niu}</strong>, et son dossier est "
                "désormais suivi par le cabinet.",
                "Votre espace adhérent s'ajoute à votre compte : vos échéances, "
                "vos justificatifs et les documents que le cabinet dépose pour "
                "vous s'y trouvent. Connectez-vous avec votre mot de passe "
                "habituel.",
            ),
            action=("Ouvrir mon espace", "{lien}"),
            avertissement=(
                "Ce bouton mène à votre page de connexion habituelle et reste "
                "valable. Le cabinet ne vous demandera jamais votre mot de passe."
            ),
        ),
        Gabarit(
            code="proforma.acceptation",
            objet="Votre accord sur la proposition {numero}",
            titre="C'est noté, {prenom}",
            paragraphes=(
                # ─────────────────────────────────────────────────────────────
                # ⚠️ ELLE VIENT D'ENGAGER SON ENTREPRISE, ET NE RECEVAIT RIEN.
                #
                # La page d'acceptation le dit en toutes lettres : « mon accord
                # engage l'entreprise que je représente », et le domaine le
                # confirme — une proforma acceptée « vaut contrat ».
                #
                # Vérifié sur la pile le 29 septembre : la boîte de la cliente est
                # **inchangée** après l'acceptation. Elle n'a ni le montant
                # qu'elle a accepté, ni la date, ni le nom qu'elle a déclaré. Le
                # cabinet, lui, a tout.
                #
                # ⚠️ CE MESSAGE NE RÉCLAME RIEN. Il constate. La demande de
                # règlement est un geste distinct, et les mêler ferait lire une
                # facture là où il n'y a qu'un accusé.
                # ─────────────────────────────────────────────────────────────
                "Vous avez accepté la proposition <strong>{numero}</strong>, d'un "
                "montant de <strong>{montant} FCFA</strong>, le {acceptee_le}.",
                "Cet accord a été donné au nom de <strong>{identite}</strong>. "
                "Conservez ce message : il est la trace de ce que vous avez "
                "accepté, et de la date.",
                "Le cabinet prépare la suite et vous recontacte. Si quelque chose "
                "ne correspond pas à votre échange, répondez à ce message sans "
                "attendre.",
            ),
            avertissement=(
                "Ce message confirme votre accord. Il ne vaut pas facture : le "
                "règlement vous sera demandé séparément."
            ),
        ),
        Gabarit(
            code="demande.accuse_de_depot",
            # ⚠️ **LE SERVICE À L'OBJET, LA RÉFÉRENCE DANS LE CORPS.**
            #
            # L'objet portait `{reference}`, et donnait dans la boîte de
            # réception : « Votre demande dos-cf776b28a0db44b4 nous est bien
            # parvenue ». Vingt caractères hexadécimaux qu'aucune cliente ne lit,
            # ne retient ni ne dicte au téléphone, à l'endroit précis où le
            # message doit se reconnaître d'un coup d'œil. Vu en lisant la boîte
            # d'une recette de bout en bout, pas dans le gabarit.
            #
            # Les trois autres messages du parcours nomment déjà ce dont ils
            # parlent : la proposition par son numéro, le dossier par la société.
            # Celui-ci était le seul à afficher un identifiant technique.
            #
            # La référence reste **dans le corps**, où elle sert : c'est elle que
            # le cabinet demande au rappel.
            objet="Votre demande concernant {service} nous est bien parvenue",
            titre="Bonjour {prenom}",
            paragraphes=(
                # ─────────────────────────────────────────────────────────────
                # ⚠️ CE MESSAGE NE PROMET QUE CE QUE LE PRODUIT TIENT.
                #
                # Le formulaire du site affiche « un responsable vous
                # recontacte ». Jusqu'au 28 septembre, rien ne le confirmait par
                # écrit : la cliente quittait la page et n'avait plus aucune
                # trace de son geste. Entre son dépôt et le premier appel, elle
                # ne pouvait que se demander si le formulaire avait marché.
                #
                # ⚠️ AUCUN DÉLAI N'EST ANNONCÉ. Le référentiel d'acquisition
                # porte les délais de veille — ce qu'on se donne avant de
                # s'alarmer — et non un engagement pris au client. Écrire « sous
                # 24 heures » ici serait fabriquer une promesse que personne
                # n'a signée, et la première demande déposée un vendredi soir la
                # démentirait.
                # ─────────────────────────────────────────────────────────────
                # ─────────────────────────────────────────────────────────────
                # ⚠️ **LA RÉFÉRENCE A ÉTÉ RETIRÉE D'ICI AUSSI.**
                #
                # La phrase disait : « Elle porte la référence
                # dos-cf776b28a0db44b4 ; conservez-la, elle nous permet de
                # retrouver votre dossier immédiatement. »
                #
                # C'est une promesse que le format ne tient pas. Seize
                # caractères hexadécimaux ne se dictent pas au téléphone, ne se
                # notent pas sans faute et ne se relisent pas. On demandait à la
                # cliente de conserver quelque chose dont elle ne peut rien
                # faire.
                #
                # Et c'était inutile : la phrase suivante dit que **le cabinet
                # la rappelle sur son numéro**. Personne n'attend qu'elle cite
                # une référence.
                #
                # ⚠️ Elle avait déjà été retirée de l'objet. L'y laisser dans le
                # corps était un demi-geste : le même raisonnement valait des
                # deux côtés, et je ne l'avais pas tiré jusqu'au bout.
                # ─────────────────────────────────────────────────────────────
                "Votre demande concernant <strong>{service}</strong> nous est bien parvenue.",
                "Un responsable l'examine et vous recontacte au "
                "<strong>{telephone}</strong>. Vous n'avez rien à faire d'ici "
                "là : si un document nous est nécessaire, il vous sera demandé "
                "à ce moment.",
            ),
            avertissement=(
                "Ce message confirme la réception de votre demande. Il ne vaut "
                "ni devis ni engagement : le montant vous sera proposé après "
                "examen."
            ),
        ),
        Gabarit(
            code="compte.activation_creation",
            objet="Votre société {denomination} est immatriculée",
            titre="Félicitations, {prenom}",
            paragraphes=(
                # ⚠️ UN GABARIT À PART, ET CE N'EST PAS UNE DUPLICATION.
                #
                # `compte.activation` ouvre sur « votre souscription X est
                # encaissée ». Une fondatrice n'a pas souscrit : elle a fait
                # immatriculer sa société, et son accès s'ouvre des semaines
                # après son paiement, le jour où le NIU est délivré. Lui
                # réemployer l'autre message lui aurait fait lire une phrase
                # fausse — et, la référence n'existant pas, une double espace.
                #
                # Deux événements différents, deux messages. Ce que le catalogue
                # interdit, c'est le même message écrit deux fois.
                "<strong>{denomination}</strong> est immatriculée. Son numéro de "
                "contribuable est <strong>{niu}</strong>, et son dossier est "
                "désormais suivi par le cabinet.",
                "Votre espace adhérent est ouvert : vos échéances, vos "
                "justificatifs et les documents que le cabinet dépose pour vous "
                "s'y trouvent. Il reste une étape : définir votre mot de passe. "
                "Personne chez CGA ne le connaîtra, et personne ne vous le "
                "demandera jamais.",
            ),
            action=("Définir mon mot de passe", "{lien}"),
            avertissement=(
                "Ce lien expire le {expire_le} et ne fonctionne qu'une fois. "
                "Passé ce délai, demandez-en un nouveau depuis la page de connexion."
            ),
        ),
        Gabarit(
            code="compte.invitation",
            objet="Votre accès à la plateforme CGA",
            titre="Bonjour {prenom}",
            paragraphes=(
                "Un accès à la plateforme CGA Broad Range Consulting Group vient "
                "d'être ouvert à votre nom, avec le rôle <strong>{role}</strong>.",
                "Définissez votre mot de passe pour l'activer.",
            ),
            action=("Activer mon accès", "{lien}"),
            avertissement=(
                "Ce lien expire le {expire_le}. Si vous n'attendiez pas cette "
                "invitation, ignorez ce message et signalez-le au cabinet."
            ),
        ),
        Gabarit(
            code="compte.reinitialisation",
            objet="Réinitialisation de votre mot de passe",
            titre="Bonjour {prenom}",
            paragraphes=(
                "Une réinitialisation de mot de passe a été demandée pour votre "
                "compte. Si vous êtes à l'origine de cette demande, choisissez un "
                "nouveau mot de passe.",
            ),
            action=("Choisir un nouveau mot de passe", "{lien}"),
            avertissement=(
                "Ce lien est valable <strong>deux heures</strong>. Si vous n'avez "
                "rien demandé, aucune action n'est requise : votre mot de passe "
                "actuel reste valable et ce lien expirera seul."
            ),
        ),
        Gabarit(
            code="compte.mot_de_passe_change",
            objet="Votre mot de passe a été modifié",
            titre="Bonjour {prenom}",
            paragraphes=(
                "Le mot de passe de votre compte vient d'être modifié. Toutes vos "
                "sessions ouvertes ont été fermées ; il faut vous reconnecter.",
            ),
            avertissement=(
                "Si vous n'êtes pas à l'origine de ce changement, contactez le "
                "cabinet immédiatement : quelqu'un d'autre a accès à votre "
                "messagerie ou à votre compte."
            ),
        ),
        Gabarit(
            code="compte.second_facteur_confirmation",
            objet="Confirmez l'association de votre application d'authentification",
            titre="Bonjour {prenom}",
            paragraphes=(
                "Vous avez demandé à associer une application d'authentification à votre "
                "compte. Ouvrez ce lien dans le navigateur où vous êtes connecté pour "
                "afficher la clé à saisir dans l'application.",
            ),
            action=("Afficher ma clé", "{lien}"),
            avertissement=(
                "Ce lien est valable <strong>trente minutes</strong>. Si vous n'avez rien "
                "demandé, ne l'ouvrez pas et contactez le cabinet : quelqu'un connaît votre "
                "mot de passe."
            ),
        ),
        Gabarit(
            code="compte.second_facteur_enrole",
            objet="Un second facteur a été enrôlé sur votre compte",
            titre="Bonjour {prenom}",
            paragraphes=(
                "Une application d'authentification vient d'être associée à votre "
                "compte. Elle vous sera demandée pour les actes sensibles, comme le "
                "dépôt d'une déclaration.",
            ),
            avertissement=(
                "Si vous n'êtes pas à l'origine de cet enrôlement, contactez le cabinet "
                "immédiatement : quelqu'un connaît votre mot de passe."
            ),
        ),
        Gabarit(
            code="compte.second_facteur_reinitialise",
            objet="Votre second facteur a été réinitialisé",
            titre="Bonjour {prenom}",
            paragraphes=(
                "Le second facteur de votre compte a été retiré par le cabinet, et vos "
                "sessions ouvertes ont été fermées.",
                "À votre prochaine connexion, associez votre nouvel appareil avant tout "
                "acte sensible.",
            ),
            avertissement=(
                "Si vous n'avez pas signalé la perte de votre appareil, contactez le "
                "cabinet immédiatement."
            ),
        ),
        Gabarit(
            code="piece.rectificative_demandee",
            objet="Facture rectificative demandée : {reference}",
            titre="Bonjour {prenom}",
            paragraphes=(
                "Le cabinet a contrôlé la facture <strong>{reference}</strong> et ne peut "
                "pas la comptabiliser en l'état.",
                "Ce qui est à rectifier : {motif}",
                "Merci de demander à votre fournisseur une facture rectificative, puis de "
                "la déposer dans votre espace ou de nous la transmettre par le canal "
                "habituel.",
            ),
            # ⚠️ Pas de bouton : la pièce se dépose par plusieurs canaux, et un lien
            # unique ferait croire que l'espace est le seul.
        ),
        # Pas 116 : le chargé de clientèle renvoie un lien d'accès à l'adhérent qui l'a appelé.
        Gabarit(
            code="compte.acces_renvoye",
            objet="Votre lien d'accès à l'espace de {denomination}",
            titre="Bonjour {prenom}",
            paragraphes=(
                # 19 octobre 2026 : le cabinet, jamais le nom du collaborateur.
                "À votre demande, le cabinet vous envoie un lien pour {geste}.",
                "Ce lien est valable <strong>{validite}</strong> et ne sert qu'une fois.",
            ),
            action=("Ouvrir mon espace", "{lien}"),
            avertissement=(
                "Vous n'avez rien demandé ? N'ouvrez pas ce lien et appelez le cabinet. Le "
                "cabinet ne vous demandera jamais votre mot de passe, ni par téléphone, ni par "
                "WhatsApp."
            ),
        ),
        # Pas 115 : le rappel d'échéance que l'adhérent a réglé (« 7 jours et 2 jours avant »).
        Gabarit(
            code="echeance.rappel",
            objet="{titre} : à régler avant le {echeance}",
            titre="Bonjour {prenom}",
            paragraphes=(
                "Votre échéance <strong>{titre}</strong> ({periode}) est à régler avant le "
                "<strong>{echeance}</strong>, dans {jours} jour(s).",
                "Une fois réglée au guichet, envoyez la quittance depuis votre espace, rubrique "
                "Échéances : le cabinet la joint à votre dossier.",
            ),
            avertissement=(
                "Vous recevez ce rappel parce que les rappels d'échéance sont actifs dans vos "
                "réglages. Vous pouvez les changer à tout moment depuis votre espace."
            ),
        ),
        # Pas 111 : la relance composée depuis l'écran « Relancer un adhérent ». Chaque pièce
        # attendue est un paragraphe : le texte du cabinet est échappé, jamais interprété.
        Gabarit(
            code="piece.relance",
            objet="Pièces manquantes pour {mois}",
            titre="Bonjour {prenom}",
            paragraphes=(
                "{introduction}",
                "{liste}",
                "{conclusion}",
                "{signature}",
            ),
        ),
        Gabarit(
            code="compte.suspendu",
            objet="Votre accès a été suspendu",
            titre="Bonjour {prenom}",
            paragraphes=(
                "Votre accès à la plateforme a été suspendu. Vos sessions ouvertes "
                "ont été fermées.",
                "Cette suspension n'efface rien : vos dossiers, vos pièces et vos "
                "déclarations restent en place et vous seront de nouveau accessibles "
                "à la levée de la mesure.",
            ),
            avertissement="Pour en connaître le motif, adressez-vous au cabinet.",
        ),
        # Pas 91 : la levée de suspension a désormais sa route, et le titulaire en est prévenu.
        Gabarit(
            code="compte.retabli",
            objet="Votre accès est rétabli",
            titre="Bonjour {prenom}",
            paragraphes=(
                "La suspension de votre accès à la plateforme a été levée.",
                "Vous pouvez vous reconnecter. Les dossiers auxquels vous avez accès sont "
                "ceux de vos habilitations en vigueur ; en cas de doute, adressez-vous au cabinet.",
            ),
        ),
        # La proposition chiffrée après l'échange avec le responsable. ⚠️ C'est le
        # SEUL courriel qui porte le lien d'acceptation : il part au moment où le
        # lien est forgé, par le serveur, sans que le lien transite par personne.
        # La relance, elle, n'en porte aucun (voir plus bas).
        Gabarit(
            # ─────────────────────────────────────────────────────────────────
            # ⚠️ LA PROPOSITION AVEC SON DÉTAIL (14 octobre 2026).
            #
            # Le cabinet l'a demandé : le courriel doit suivre le style de ses
            # proformas. La cliente lisait un montant et un bouton ; elle lit
            # maintenant ce que le prix couvre, rubrique par rubrique, avec le
            # total en cartouche comme sur le document, qui est joint.
            #
            # Un gabarit distinct, et non une variante du suivant : celui-ci
            # annonce une pièce jointe et un détail. Une proforma émise sans
            # document n'a ni l'un ni l'autre, et le lui promettre serait faux.
            # ─────────────────────────────────────────────────────────────────
            code="proforma.envoi.detaillee",
            objet="Votre proposition {proforma} : {montant} FCFA",
            titre="Bonjour {nom}",
            paragraphes=(
                "Suite à notre échange, voici notre proposition <strong>{proforma}</strong>. "
                "Elle se décompose ainsi :",
            ),
            tableau="detail",
            action=("Consulter et accepter ma proposition", "{lien}"),
            avertissement=(
                "Le document complet est joint à ce message : il précise les pièces à "
                "nous fournir, celles que vous recevrez, le délai et les moyens de "
                "règlement. Ce lien est personnel et reste valable jusqu'au {expire_le}. "
                "Ne le transférez pas : il permet d'accepter la proposition."
            ),
        ),
        Gabarit(
            code="proforma.envoi",
            objet="Votre proposition {proforma} : {montant} FCFA",
            titre="Bonjour {nom}",
            paragraphes=(
                # ⚠️ Pas le nom du service : le référentiel ne porte que son code
                # (`CREATION`), et un code n'a rien à faire dans un courriel.
                # Le détail de ce que le prix couvre est sur la page de la proforma.
                "Suite à notre échange, voici notre proposition <strong>{proforma}</strong>, "
                "d'un montant de <strong>{montant} FCFA</strong>.",
                "Vous pouvez la lire en détail, puis l'accepter en ligne : aucun compte "
                "n'est nécessaire. Si un point appelle une précision, répondez simplement "
                "à ce message.",
            ),
            action=("Consulter ma proposition", "{lien}"),
            avertissement=(
                "Ce lien est personnel et reste valable jusqu'au {expire_le}. "
                "Ne le transférez pas : il permet d'accepter la proposition."
            ),
        ),
        Gabarit(
            code="relance.proforma",
            objet="Votre proposition {proforma} est toujours en attente",
            titre="Bonjour",
            paragraphes=(
                "Nous vous avons transmis la proposition <strong>{proforma}</strong> il "
                "y a {depuis_jours} jours, et nous n'avons pas encore reçu votre "
                "réponse.",
                "Si le document appelle une précision ou une correction, répondez "
                "simplement à ce message : nous préférons ajuster une proposition "
                "plutôt que de la laisser sans suite.",
            ),
            # ⚠️ **Pas de bouton d'action, et c'est délibéré.**
            #
            # Le lien d'acceptation d'une proforma est signé, daté et à usage
            # unique : il expire. Le remettre à chaque relance obligerait à en
            # forger un nouveau ici, donc à donner à un gabarit de courriel le
            # pouvoir de fabriquer un consentement contractuel.
            #
            # La relance rappelle l'existence du document ; l'accepter passe par le
            # lien d'origine ou par une réponse au cabinet.
            avertissement=(
                "Sans réponse de votre part, cette proposition cessera d'être "
                "relancée automatiquement et votre dossier sera repris par votre "
                "interlocuteur."
            ),
        ),
        # ── 15 octobre 2026 : l'adhérent apprend ce que le cabinet a fait pour lui ──
        #
        # Il voyait le résultat en se connectant, et seulement alors. Une quittance
        # refusée qu'il ne découvre pas laisse courir l'échéance.
        Gabarit(
            code="adherent.quittance_verifiee",
            objet="Votre quittance {obligation} est vérifiée",
            titre="C'est en ordre, {prenom}",
            paragraphes=(
                "Le cabinet a vérifié la quittance que vous avez envoyée pour "
                "<strong>{obligation}</strong> ({periode}) : l'échéance de "
                "<strong>{denomination}</strong> est désormais payée.",
            ),
            action=("Voir mes échéances", "{lien}"),
            avertissement=(
                "Ce bouton mène à votre espace et reste valable : connectez-vous avec votre "
                "mot de passe habituel. Le cabinet ne vous demandera jamais votre mot de passe."
            ),
        ),
        Gabarit(
            code="adherent.quittance_refusee",
            objet="Votre quittance {obligation} est à renvoyer",
            titre="Une précision est nécessaire, {prenom}",
            paragraphes=(
                "Le cabinet n'a pas pu valider la quittance envoyée pour "
                "<strong>{obligation}</strong> ({periode}) de <strong>{denomination}</strong>.",
                "Motif : {motif}",
                "Renvoyez la bonne quittance depuis votre espace : l'échéance reste à "
                "régler tant qu'elle n'est pas vérifiée.",
            ),
            action=("Renvoyer la quittance", "{lien}"),
            avertissement=(
                "Ce bouton mène à votre espace et reste valable : connectez-vous avec votre "
                "mot de passe habituel. Le cabinet ne vous demandera jamais votre mot de passe."
            ),
        ),
        Gabarit(
            code="adherent.fonds_recus",
            objet="Le cabinet a reçu vos fonds pour {obligation}",
            titre="Vos fonds sont reçus, {prenom}",
            paragraphes=(
                "Le cabinet a reçu <strong>{montant} FCFA</strong> pour "
                "<strong>{obligation}</strong> ({periode}) de <strong>{denomination}</strong>.",
                "Il règle l'administration en votre nom. L'échéance passera à « payée » "
                "avec la quittance de l'administration, que vous retrouverez dans votre "
                "espace.",
            ),
            action=("Suivre mes échéances", "{lien}"),
            avertissement=(
                "Ce bouton mène à votre espace et reste valable : connectez-vous avec votre "
                "mot de passe habituel. Le cabinet ne vous demandera jamais votre mot de passe."
            ),
        ),
        # 18 octobre 2026 (point 25) : la réponse à un candidat, telle que le cabinet l'a
        # relue. Le texte arrive découpé en paragraphes `p1` à `p10` ; les vides ne
        # s'écrivent pas. Échappé comme toute valeur : aucun balisage ne passe.
        # 18 octobre 2026 (point 22) : le client d'une prestation ponctuelle apprend qu'un
        # juriste l'a prise en charge, puis que le livrable est prêt. Sans nom de juriste :
        # c'est le cabinet qui répond (point 21).
        Gabarit(
            code="mission.ouverte",
            objet="Votre demande {reference} est prise en charge",
            titre="Votre demande est entre de bonnes mains",
            paragraphes=(
                "Bonjour,",
                "Le cabinet a bien reçu votre règlement et a ouvert le travail sur votre "
                "demande : <strong>{objet_de_la_demande}</strong>.",
                "Un juriste du cabinet l'instruit ; un second relit avant que rien ne vous "
                "soit remis. Vous serez prévenu dès que le livrable sera prêt.",
            ),
            # 19 octobre 2026 (point 22) : où en est la demande, sans appeler.
            action=("Suivre ma demande", "{lien}"),
            avertissement=(
                "Ce bouton vous est personnel et reste valable pour cette demande : ne le "
                "transférez pas. Le cabinet ne vous demandera jamais de mot de passe."
            ),
        ),
        Gabarit(
            code="mission.livree",
            objet="Votre demande {reference} est traitée",
            titre="Votre livrable est prêt",
            paragraphes=(
                "Bonjour,",
                "Le travail sur votre demande <strong>{objet_de_la_demande}</strong> est "
                "terminé et validé par le cabinet.",
                "Votre interlocuteur vous le remet et reste disponible pour en parler.",
            ),
            action=("Ouvrir mon livrable", "{lien}"),
            avertissement=(
                "Ce bouton vous est personnel et reste valable pour cette demande : ne le "
                "transférez pas. Le cabinet ne vous demandera jamais de mot de passe."
            ),
        ),
        # 19 octobre 2026 (point 25) : le candidat savait sa candidature reçue à l'écran,
        # puis n'avait plus rien jusqu'à la réponse. L'accusé part au dépôt.
        Gabarit(
            code="recrutement.accuse",
            objet="Votre candidature : {poste}",
            titre="Candidature bien reçue",
            paragraphes=(
                "Bonjour {nom},",
                "Le cabinet a bien reçu votre candidature au poste de "
                "<strong>{poste}</strong>, avec votre CV.",
                "Elle sera lue par la personne chargée du recrutement. Le cabinet vous "
                "répond, que votre profil soit retenu ou non.",
                "Vous n'avez rien à renvoyer : un second dépôt pour la même offre n'est "
                "pas nécessaire.",
            ),
        ),
        Gabarit(
            code="recrutement.reponse",
            objet="{objet}",
            titre="{poste}",
            paragraphes=tuple(f"{{p{rang}}}" for rang in range(1, 11)),
        ),
        Gabarit(
            code="adherent.honoraires_regles",
            objet="Reçu de vos honoraires, {periode}",
            titre="Merci, {prenom}",
            paragraphes=(
                "Le règlement de <strong>{montant} FCFA</strong> pour les honoraires de "
                "<strong>{periode}</strong> de <strong>{denomination}</strong> est bien reçu "
                "(référence {reference}).",
                "Votre reçu est disponible dans votre espace, rubrique Honoraires.",
            ),
            action=("Voir mon reçu", "{lien}"),
            avertissement=(
                "Ce bouton mène à votre espace et reste valable : connectez-vous avec votre "
                "mot de passe habituel. Le cabinet ne vous demandera jamais votre mot de passe."
            ),
        ),
    )
}


def gabarit(code: str) -> Gabarit | None:
    """Le gabarit du code, ou `None` s'il n'est pas au catalogue.

    Rend `None` plutôt que de lever : l'appelant est un service de notification
    qui ne doit jamais interrompre le geste métier qui l'a déclenché.
    """
    return CATALOGUE.get(code)
