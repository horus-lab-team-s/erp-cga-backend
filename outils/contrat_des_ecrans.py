"""Le frontend attend-il des champs que le backend ne rend pas ?

Une confrontation, pas une supposition.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE SCRIPT EXISTE (pas 77)

`appeler<T>(chemin)` affirme au compilateur que la réponse a la forme `T`. Rien ne le
vérifie. Au pas 76, `T` déclarait un champ que la route ne rendait jamais : il valait
`undefined`, le compte des pièces « en cours » de l'espace adhérent valait zéro, et
aucun outil ne pouvait le voir, puisque le typage passait et que l'écran s'affichait.

Corriger ce champ-là corrigeait une instance. Ce script corrige la classe : il
confronte **chaque** appel typé du frontend au schéma OpenAPI du backend.

COMMENT IL CONFRONTE

1. Le frontend : `node outils/types-des-appels.mjs`, qui lit chaque `appeler<T>(…)`
   avec le compilateur TypeScript et rend la méthode, le chemin et les champs de `T`.
2. Le backend : le schéma OpenAPI de l'application, la réponse 200, 201 ou 202 de la route
   que ce chemin désigne, références résolues, tableaux et `T | null` traversés.
3. Pour chaque champ attendu par le frontend, il doit exister dans la réponse.

CINQ VERDICTS

    ABSENT      le frontend lit un champ que la réponse ne contient pas : c'est le
                défaut du pas 76. Il fait échouer le script.
    SANS ROUTE  le chemin ne désigne aucune route du schéma, avec cette méthode.
                Il fait échouer le script : un appel vers nulle part.
    TYPE        le champ existe, mais sa nature diffère : un nombre lu comme du texte,
                un tableau lu comme un objet (pas 79). Il fait échouer le script.
    NULLITÉ     la réponse peut rendre le champ `null`, et l'écran le déclare toujours
                présent (pas 79). Il fait échouer le script.
    ILLISIBLE   le chemin n'est pas un littéral, ou la réponse n'a pas de schéma
                exploitable. Signalé, pas bloquant : le script dit ce qu'il n'a pas
                pu vérifier au lieu de le compter comme juste.

⚠️ CE QU'IL NE VÉRIFIE PAS

- La nullité d'un champ déclaré `unknown` côté écran : TypeScript la rend illisible.
- Les **valeurs** : une énumération dont une valeur manque côté écran passe.
- Les champs que le backend rend et que le frontend ignore : ce n'est pas un défaut.
- Les **corps de requête** : la validation `extra="forbid"` du backend les refuse déjà.

USAGE (depuis `Backend_erp_cga`)

    CGA_PERSISTANCE=memoire python -m outils.contrat_des_ecrans          # verdict
    CGA_PERSISTANCE=memoire python -m outils.contrat_des_ecrans --detail # et chaque appel
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
from typing import Any

RACINE_FRONTEND = pathlib.Path(__file__).resolve().parents[2] / "Frontend_erp_cga"


def appels_du_frontend() -> list[dict[str, Any]]:
    """Le relevé du compilateur TypeScript, tel que le script Node le rend."""
    sortie = subprocess.run(
        ["node", "outils/types-des-appels.mjs"],
        cwd=RACINE_FRONTEND,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(sortie.stdout)


def schema_openapi() -> dict[str, Any]:
    # Import tardif : monter l'application démarre son câblage.
    from app.main import creer_application

    return creer_application().openapi()


def _resoudre(schema: dict[str, Any], composants: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        schema = composants[schema["$ref"].rsplit("/", 1)[1]]
    return schema


def _nature(schema: dict[str, Any] | None, composants: dict[str, Any]) -> str:
    """La nature JSON d'un schéma : `texte`, `nombre`, `booleen`, `tableau`, `objet`, `autre`.

    ⚠️ Symétrique de `natureDe` dans le script Node (pas 79). Le schéma décrit la
    **sérialisation** : un `Decimal` sort en texte (avec motif), un `float` en nombre, une
    date en texte. `null` est retiré d'une union avant de conclure.
    """
    if not schema:
        return "autre"
    schema = _resoudre(schema, composants)
    variantes = schema.get("anyOf") or schema.get("oneOf")
    if variantes:
        natures = {
            _nature(v, composants)
            for v in variantes
            if _resoudre(v, composants).get("type") != "null"
        }
        return natures.pop() if len(natures) == 1 else "autre"
    type_json = schema.get("type")
    if type_json == "string" or ("enum" in schema and type_json in (None, "string")):
        return "texte"
    if type_json in ("integer", "number"):
        return "nombre"
    if type_json == "boolean":
        return "booleen"
    if type_json == "array":
        return "tableau"
    if type_json == "object" or "properties" in schema:
        return "objet"
    return "autre"


def champs_de_la_reponse(
    schema: dict[str, Any] | None,
    composants: dict[str, Any],
    prefixe: str = "",
    profondeur: int = 0,
) -> tuple[dict[str, tuple[str, bool]], set[str]] | None:
    """Les chemins de champs d'une réponse, à plat, et ses sous-arbres **opaques**.

    `None` si la racine elle-même ne se lit pas. Un sous-arbre opaque est un champ connu
    dont le contenu n'a pas de schéma nommé (`list[dict[str, str]]`, par exemple) : ses
    enfants ne peuvent être ni confirmés ni infirmés.

    ⚠️ Symétrique du script Node : tableaux traversés, `T | null` réduit à `T`, et un
    objet sans propriétés nommées (un dictionnaire libre) arrête la descente.

    ⚠️ La première version déclarait **absents** les enfants d'un sous-arbre opaque : les
    lignes d'une proforma, typées `dict[str, str]` au backend, passaient pour manquantes.
    Ce qu'on ne peut pas lire n'est pas absent ; c'est non vérifié.
    """
    if schema is None or profondeur > 5:
        return None
    schema = _resoudre(schema, composants)
    variantes = schema.get("anyOf") or schema.get("oneOf")
    if variantes:
        non_nulles = [v for v in variantes if _resoudre(v, composants).get("type") != "null"]
        if len(non_nulles) != 1:
            return None
        return champs_de_la_reponse(non_nulles[0], composants, prefixe, profondeur)
    if schema.get("allOf") and len(schema["allOf"]) == 1:
        return champs_de_la_reponse(schema["allOf"][0], composants, prefixe, profondeur)
    if schema.get("type") == "array":
        return champs_de_la_reponse(schema.get("items"), composants, prefixe, profondeur)
    proprietes = schema.get("properties")
    if not proprietes:
        return None
    # Chaque chemin de champ, avec sa nature JSON et sa nullité (pas 79).
    champs: dict[str, tuple[str, bool]] = {}
    opaques: set[str] = set()
    for nom, sous_schema in proprietes.items():
        chemin = f"{prefixe}{nom}"
        resolu = _resoudre(sous_schema, composants)
        peut_etre_null = any(
            _resoudre(v, composants).get("type") == "null"
            for v in (resolu.get("anyOf") or resolu.get("oneOf") or [])
        )
        champs[chemin] = (_nature(sous_schema, composants), peut_etre_null)
        profond = champs_de_la_reponse(sous_schema, composants, f"{chemin}.", profondeur + 1)
        if profond is None:
            opaques.add(chemin)
        else:
            champs.update(profond[0])
            opaques |= profond[1]
    return champs, opaques


def _route_de(chemin: str, methode: str, chemins: dict[str, Any]) -> str | None:
    """La route OpenAPI qu'un chemin du frontend désigne, pour cette méthode.

    Trois essais, du plus strict au plus large :

    1. **segment pour segment**, un `{}` du frontend en face d'un paramètre de route ;
    2. un `{}` pour **plusieurs** segments : une clé d'écriture « 2026/AC/12 » ;
    3. le chemin **sans son dernier `{}`**, quand ce morceau final est une chaîne de
       requête construite à part (`/plan-comptable${classe ? `?classe=…` : ""}`).

    ⚠️ La première version essayait d'emblée le deuxième, et préférait les routes sans
    paramètre : `/acquisition/dossiers/{}` désignait alors une route littérale voisine,
    et le script comparait la fiche d'un dossier à une liste.
    """
    operations = {r: ops for r, ops in chemins.items() if methode.lower() in ops}
    segments = chemin.strip("/").split("/")

    def segment_pour_segment(route: str) -> bool:
        cibles = route.strip("/").split("/")
        if len(cibles) != len(segments):
            return False
        return all(
            (s == "{}" and c.startswith("{")) or s == c
            for s, c in zip(segments, cibles, strict=True)
        )

    exactes = [r for r in operations if segment_pour_segment(r)]
    if exactes:
        return exactes[0]
    motif = re.compile("^" + re.escape(chemin).replace(r"\{\}", ".+") + "$")
    larges = [r for r in operations if motif.match(re.sub(r"\{[^}]+\}", "x", r))]
    if larges:
        return sorted(larges, key=lambda r: -r.count("{"))[0]
    if chemin.endswith("{}"):
        return _route_de(chemin[:-2], methode, chemins)
    return None


def confronter() -> list[dict[str, Any]]:
    openapi = schema_openapi()
    composants = openapi["components"]["schemas"]
    verdicts = []
    for appel in appels_du_frontend():
        base = {"fichier": appel["fichier"], "methode": appel["methode"], "chemin": appel["chemin"]}
        if not appel["chemin"]:
            verdicts.append({**base, "verdict": "ILLISIBLE", "detail": "chemin non littéral"})
            continue
        # ⚠️ Pas 93 : un chemin **entièrement** interpolé (`${source.chemin}…`, lu au
        # registre par la recherche fédérée) devenait `{}{}`, que le motif large faisait
        # correspondre à n'importe quelle route : l'outil confrontait l'appel à une route
        # tirée au hasard et criait ABSENT. Sans un seul segment littéral, il n'y a rien à
        # confronter, et le dire vaut mieux qu'un faux verdict dans un sens ou dans l'autre.
        if not re.sub(r"\{\}|/", "", appel["chemin"]):
            verdicts.append(
                {**base, "verdict": "ILLISIBLE", "detail": "chemin entièrement dynamique"}
            )
            continue
        route = _route_de(appel["chemin"], appel["methode"], openapi["paths"])
        if route is None:
            verdicts.append(
                {**base, "verdict": "SANS ROUTE", "detail": "aucune route ne correspond"}
            )
            continue
        reponses = openapi["paths"][route][appel["methode"].lower()]["responses"]
        # ⚠️ 202 compris (pas 78) : la demande de règlement répond 202, et la première
        # version ne lisait que 200 et 201. Elle la déclarait illisible alors qu'elle
        # était typée.
        reponse = reponses.get("200") or reponses.get("201") or reponses.get("202")
        schema = (reponse or {}).get("content", {}).get("application/json", {}).get("schema")
        lecture = champs_de_la_reponse(schema, composants)
        attendus = [c["champ"] for c in appel["champs"]]
        if not attendus:
            verdicts.append({**base, "route": route, "verdict": "OK", "detail": "aucun champ lu"})
            continue
        if lecture is None:
            verdicts.append(
                {
                    **base,
                    "route": route,
                    "verdict": "ILLISIBLE",
                    "detail": "réponse sans schéma d'objet",
                }
            )
            continue
        rendus, opaques = lecture
        non_verifies = [c for c in attendus if any(c.startswith(f"{o}.") for o in opaques)]
        absents = [c for c in attendus if c not in rendus and c not in non_verifies]
        # ⚠️ Pas 79 : la nature ne se compare que lorsqu'elle est connue des deux côtés. Un
        # « autre » (une union mêlée, un `unknown`) n'est ni juste ni faux.
        connues = {"texte", "nombre", "booleen", "tableau", "objet"}
        types_faux = [
            f"{c['champ']} (écran {c['nature']}, réponse {rendus[c['champ']][0]})"
            for c in appel["champs"]
            if c["champ"] in rendus
            and c.get("nature") in connues
            and rendus[c["champ"]][0] in connues
            and c["nature"] != rendus[c["champ"]][0]
        ]
        # ⚠️ Pas 79 : un champ que la réponse peut rendre `null`, déclaré toujours présent
        # à l'écran. Les champs `autre` sont écartés : TypeScript réduit `unknown | null` à
        # `unknown`, et la nullité n'y est plus lisible.
        nuls_ignores = [
            c["champ"]
            for c in appel["champs"]
            if c["champ"] in rendus
            and rendus[c["champ"]][1]
            and not c.get("nullable")
            and not c.get("facultatif")
            and c.get("nature") in connues
        ]
        if absents:
            verdict, detail = "ABSENT", ", ".join(absents)
        elif types_faux:
            verdict, detail = "TYPE", ", ".join(types_faux)
        elif nuls_ignores:
            verdict, detail = "NULLITÉ", ", ".join(nuls_ignores)
        else:
            verdict, detail = "OK", f"{len(attendus) - len(non_verifies)} champs"
            if non_verifies:
                detail += f" ; non vérifiés (sous-arbre sans schéma) : {', '.join(non_verifies)}"
        verdicts.append({**base, "route": route, "verdict": verdict, "detail": detail})
    return verdicts


def main() -> None:
    verdicts = confronter()
    comptes: dict[str, int] = {}
    for v in verdicts:
        comptes[v["verdict"]] = comptes.get(v["verdict"], 0) + 1
    print(
        " · ".join(f"{k} {n}" for k, n in sorted(comptes.items())), f"· sur {len(verdicts)} appels"
    )
    for v in verdicts:
        if v["verdict"] != "OK" or "--detail" in sys.argv:
            appel = f"{v['methode']:6} {v['chemin']}  ({v['fichier']})"
            print(f"  {v['verdict']:10} {appel}  {v['detail']}")
    if any(comptes.get(v) for v in ("ABSENT", "SANS ROUTE", "TYPE", "NULLITÉ")):
        sys.exit(1)


if __name__ == "__main__":
    main()
