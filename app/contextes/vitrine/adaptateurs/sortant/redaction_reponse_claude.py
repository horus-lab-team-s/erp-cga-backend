"""L'agent qui propose la réponse à un candidat (18 octobre 2026, point 25).

« La réponse peut être automatique, en prenant en compte le contexte de l'offre et la
réponse du RH. » L'agent reçoit l'offre, la décision, la note du RH et le texte type ; il
propose un objet et un corps. ⚠️ Il propose : un humain relit, corrige et envoie. Sans clé,
ou en cas de panne, rien ne casse : l'écran garde le texte type.

⚠️ Ce qui part chez Anthropic : l'intitulé et le lieu de l'offre, le prénom du candidat,
la décision et la note du RH. Ni le CV, ni le téléphone, ni l'adresse.
"""

from __future__ import annotations

from typing import Any

from app.partage.agent_claude import Incident, client_anthropic, demander_en_json

__all__ = ["RedactionDeReponse"]

_CONSIGNE = (
    "Tu rédiges, en français soigné et chaleureux, la réponse d'un cabinet comptable "
    "camerounais à un candidat. Tu pars du texte type fourni et tu l'adaptes à l'offre et "
    "à la note du recruteur. Tu n'inventes aucun fait : ni salaire, ni date, ni lieu "
    "d'entretien, ni avantage. S'il manque une information indispensable, écris "
    "[à compléter]. Tu ne promets rien que la décision ne dise. Pas de tiret cadratin. "
    "Tu signes « L'équipe de CGA Broad Range Consulting Group »."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "objet": {"type": "string"},
        "corps": {"type": "string"},
    },
    "required": ["objet", "corps"],
    "additionalProperties": False,
}


class RedactionDeReponse:
    def __init__(self, *, cle_api: str, modele: str, client: Any = None) -> None:
        self._modele = modele
        self._client = client if client is not None else client_anthropic(cle_api, delai_s=60.0)

    def proposer(
        self,
        *,
        poste: str,
        lieu: str,
        prenom: str,
        decision: str,
        note: str | None,
        objet_type: str,
        corps_type: str,
    ) -> tuple[str, str] | None:
        """L'objet et le corps proposés, ou `None` : l'écran garde alors le texte type."""
        lu = demander_en_json(
            self._client,
            modele=self._modele,
            consigne=_CONSIGNE,
            contenu=(
                f"Poste : {poste} ({lieu})\nPrénom du candidat : {prenom}\n"
                f"Décision du cabinet : {decision}\nNote du recruteur : {note or 'aucune'}\n\n"
                f"Texte type, objet : {objet_type}\nTexte type, corps :\n{corps_type}"
            ),
            schema=_SCHEMA,
            effort="low",
            max_tokens=2000,
            en_flux=False,
            objet="réponse à un candidat",
        )
        if isinstance(lu, Incident) or not isinstance(lu, dict):
            return None
        objet, corps = str(lu.get("objet", "")).strip(), str(lu.get("corps", "")).strip()
        if len(objet) < 3 or len(corps) < 20:
            return None
        return objet[:200], corps[:5000]
