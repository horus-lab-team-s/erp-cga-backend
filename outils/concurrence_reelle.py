"""Ce que fait l'API quand le cabinet travaille en même temps.

⚠️ CE N'EST PAS UN ESSAI DE CHARGE DE PRODUCTION. Base, API, deux interfaces et
relais de courrier partagent un poste de travail. On ne cherche pas un débit
maximal : on cherche à savoir si la latence s'effondre, si des requêtes
échouent, et si le cloisonnement tient quand vingt requêtes se croisent.

⚠️ CHAQUE COMPTE NE FAIT QUE CE QU'IL A LE DROIT DE FAIRE. Mesurer des 403
mesurerait la vitesse d'un refus, pas celle du travail : un refus est rendu
avant toute lecture en base, donc rapide, et il embellirait la moyenne.

⚠️ LA LIMITATION DE DÉBIT COMPTE LES CONNEXIONS : 30 par tranche de 5 minutes et
par adresse. Relancer cet outil trois fois de suite l'épuise, et les sessions ne
s'ouvrent plus (429). Ce n'est pas une panne, c'est la règle qui fonctionne —
mais il faut attendre la fenêtre avant de remesurer, sans quoi on croit à une
régression de l'API.
"""
import concurrent.futures as cf, json, statistics, time, urllib.request, urllib.error

BASE = "http://localhost:8100"
MDP = "cabinet brcg douala 2026"
JOUR = "2026-09-24"

# Le travail réel de chaque profil, tel que ses permissions l'autorisent.
PROFILS = {
    "b.mballa@cga-brcg.cm": [  # DIRECTION
        "/transverse/moi", "/pilotage/tableau-de-bord",
        f"/portefeuille/entreprises?a_la_date={JOUR}", f"/portefeuille/anomalies?a_la_date={JOUR}",
    ],
    "s.onana@cga-brcg.cm": [  # ADMINISTRATEUR
        "/transverse/moi", f"/transverse/comptes?a_la_date={JOUR}", "/transverse/roles", "/transverse/audit",
    ],
    "a.bouba@cga-brcg.cm": [  # RÉVISEUR
        "/transverse/moi", "/conformite/regles", "/collecte/doublons",
        f"/collecte/pieces?a_la_date={JOUR}",
    ],
    "l.fotso@cga-brcg.cm": [  # COMPTABLE
        "/transverse/moi", "/comptabilite/plan-comptable", "/comptabilite/journaux",
        f"/collecte/pieces?a_la_date={JOUR}",
    ],
    "c.ndongo@cga-brcg.cm": [  # COMPTABLE
        "/transverse/moi", "/comptabilite/plan-comptable", "/conformite/regles",
        f"/portefeuille/entreprises?a_la_date={JOUR}",
    ],
}

def appeler(chemin, temoin=None, corps=None):
    requete = urllib.request.Request(BASE + chemin, method="POST" if corps else "GET")
    requete.add_header("Accept", "application/json")
    if temoin: requete.add_header("Cookie", temoin)
    if corps is not None:
        requete.add_header("Content-Type", "application/json")
        requete.data = json.dumps(corps).encode()
    debut = time.perf_counter()
    try:
        with urllib.request.urlopen(requete, timeout=30) as r:
            return (time.perf_counter() - debut) * 1000, r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return (time.perf_counter() - debut) * 1000, e.code, e.read(), e.headers

sessions = {}
for courriel in PROFILS:
    _, statut, corps, entetes = appeler("/transverse/session", corps={"courriel": courriel, "mot_de_passe": MDP})
    if statut != 200:
        print(f"  ⚠️ {courriel} : {statut}"); continue
    sessions[courriel] = entetes.get("set-cookie", "").split(";")[0]
    time.sleep(2.5)
print(f"sessions ouvertes : {len(sessions)}/{len(PROFILS)}\n")

comptes = list(sessions)
def travail(n):
    courriel = comptes[n % len(comptes)]
    routes = PROFILS[courriel]
    chemin = routes[(n // len(comptes)) % len(routes)]
    ms, statut, corps, _ = appeler(chemin, temoin=sessions[courriel])
    return chemin, ms, statut, courriel, len(corps)

for paralleles in (1, 5, 10, 20, 40):
    total = paralleles * 10
    debut = time.perf_counter()
    with cf.ThreadPoolExecutor(max_workers=paralleles) as pool:
        resultats = list(pool.map(travail, range(total)))
    duree = time.perf_counter() - debut
    latences = sorted(r[1] for r in resultats)
    statuts = {}
    for _, _, s, _, _ in resultats: statuts[s] = statuts.get(s, 0) + 1
    octets = sum(r[4] for r in resultats)
    print(f"{paralleles:3d} en parallèle · {total:4d} requêtes en {duree:5.2f}s "
          f"({total/duree:6.1f}/s) · médiane {statistics.median(latences):6.1f} ms "
          f"· p95 {latences[int(len(latences)*0.95)-1]:6.1f} ms · max {latences[-1]:6.1f} ms "
          f"· {octets//1024} Kio · statuts {statuts}")

# ⚠️ LE CONTRÔLE QUI COMPTE VRAIMENT : le cloisonnement a-t-il tenu ? Vingt
# requêtes concurrentes partagent un pool de connexions ; si le locataire était
# posé sur la CONNEXION et non sur la transaction, une requête pourrait hériter
# du locataire de la précédente. C'est le défaut le plus grave qu'un essai de
# concurrence puisse révéler dans un produit multi-locataire.
print("\n── Cloisonnement sous concurrence")
# ⚠️ LE CONTRÔLE QUI COMPTE VRAIMENT. Quarante requêtes concurrentes partagent un
# pool de connexions. Si le locataire était posé sur la CONNEXION et non sur la
# transaction, une requête pourrait hériter du locataire — ou de l'identité — de
# la précédente. C'est le défaut le plus grave qu'un essai de concurrence puisse
# révéler dans un produit multi-locataire, et il est INVISIBLE hors concurrence.
#
# ⚠️ L'identité se relève d'abord AU CALME, une session à la fois : `compte` est
# un identifiant interne (« C-001 »), pas l'adresse électronique. Comparer la
# réponse à l'adresse ferait échouer les quatre cents essais et accuserait le
# cloisonnement d'un défaut qui n'existe pas.
attendu = {}
for courriel in comptes:
    _, _, corps, _ = appeler("/transverse/moi", temoin=sessions[courriel])
    rendu = json.loads(corps)
    attendu[courriel] = (rendu["compte"], rendu["locataire"])
print("   identités relevées :", {c.split("@")[0]: v[0] for c, v in attendu.items()})

def identite(n):
    courriel = comptes[n % len(comptes)]
    _, _, corps, _ = appeler("/transverse/moi", temoin=sessions[courriel])
    rendu = json.loads(corps)
    return courriel, (rendu.get("compte"), rendu.get("locataire"))

with cf.ThreadPoolExecutor(max_workers=40) as pool:
    verifs = list(pool.map(identite, range(400)))
fautes = [(c, vu, attendu[c]) for c, vu in verifs if vu != attendu[c]]
print(f"   400 requêtes concurrentes, 40 de front")
print(f"   identités mélangées : {len(fautes)}")
if fautes:
    for f in fautes[:3]: print("     ", f)
print(f"   locataires distincts rendus : {sorted({v[1] for _, v in verifs})}")
