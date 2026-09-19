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
_PIED = (
    "CGA Broad Range Consulting Group — agrément MINFI/DGI n° 00000048<br />"
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
    return frozenset(
        nom
        for morceau in morceaux
        for _, nom, _, _ in lecteur.parse(morceau)
        if nom
    )


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


def _texte(gabarit_: Gabarit, valeurs: dict[str, str]) -> str:
    lignes = [_sans_balises(_remplir(gabarit_.titre, valeurs)), ""]
    lignes.extend(_sans_balises(_remplir(p, valeurs)) + "\n" for p in gabarit_.paragraphes)
    if gabarit_.action is not None:
        libelle, adresse = gabarit_.action
        lignes.append(f"{_remplir(libelle, valeurs)} : {_remplir(adresse, valeurs)}\n")
    if gabarit_.avertissement is not None:
        lignes.append(_sans_balises(_remplir(gabarit_.avertissement, valeurs)) + "\n")
    lignes.append("—")
    lignes.append(
        "CGA Broad Range Consulting Group — agrément MINFI/DGI n° 00000048"
    )
    return "\n".join(lignes)


def _html(gabarit_: Gabarit, valeurs: dict[str, str]) -> str:
    """Le corps HTML. Tables et CSS en ligne — voir l'en-tête."""
    corps = "".join(
        f'<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:{_INK_900};">'
        f"{_remplir(p, valeurs)}</p>"
        for p in gabarit_.paragraphes
    )

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

    return f"""\
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" \
style="background:{_SURFACE_ALT};padding:28px 12px;">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" \
style="max-width:560px;background:{_SURFACE};border:1px solid {_LINE_200};\
border-radius:10px;overflow:hidden;font-family:-apple-system,'Segoe UI',\
Roboto,Helvetica,Arial,sans-serif;">
<tr><td style="background:{_INDIGO_900};padding:20px 28px;">
<span style="font-size:15px;font-weight:700;color:#ffffff;letter-spacing:.3px;">\
CGA Broad Range Consulting Group</span></td></tr>
<tr><td style="padding:28px;">
<h1 style="margin:0 0 18px;font-size:20px;line-height:1.35;color:{_INDIGO_700};">\
{_remplir(gabarit_.titre, valeurs)}</h1>
{corps}
</td></tr>
<tr><td style="padding:18px 28px;border-top:1px solid {_LINE_200};\
font-size:12px;line-height:1.6;color:{_INK_500};">{_PIED}</td></tr>
</table>
</td></tr>
</table>"""


def rendre(gabarit_: Gabarit, contexte: dict[str, Any]) -> MessageRendu:
    """Rend l'objet, le corps texte et le corps HTML.

    ⚠️ Lève `KeyError` si une marque manque. L'appelant doit avoir consulté
    `cles_manquantes` d'abord — voir l'en-tête, règle 1.
    """
    brutes = {cle: "" if valeur is None else str(valeur) for cle, valeur in contexte.items()}
    echappees = {cle: escape(valeur, quote=True) for cle, valeur in brutes.items()}
    return MessageRendu(
        objet=_remplir(gabarit_.objet, brutes),
        texte=_texte(gabarit_, brutes),
        html=_html(gabarit_, echappees),
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
                "À votre demande, {par}, du cabinet, vous envoie un lien pour {geste}.",
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
    )
}


def gabarit(code: str) -> Gabarit | None:
    """Le gabarit du code, ou `None` s'il n'est pas au catalogue.

    Rend `None` plutôt que de lever : l'appelant est un service de notification
    qui ne doit jamais interrompre le geste métier qui l'a déclenché.
    """
    return CATALOGUE.get(code)
