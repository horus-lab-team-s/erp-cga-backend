"""Contexte F · Obligations et déclarations.

TypeObligation, ObligationInstance, Relance, Penalite, DeclarationTVA.

**La date d'échéance est calculée**, jamais figée : elle dépend du type
d'obligation, de la date de clôture de l'exercice et du centre de rattachement,
tous deux historisés. Le 15 mars n'est pas une constante mais le résultat d'une
formule sur un exercice clos au 31 décembre — et le dépôt est par ailleurs
échelonné par centre.

**L'échéancier se génère depuis le profil du dossier, jamais depuis les pièces
reçues.** Une entreprise assujettie qui n'a réalisé aucune opération doit tout de
même déposer une déclaration portant la mention « néant » : l'obligation naît de
l'assujettissement, pas de l'activité. Le profil est réévalué à la fin de chaque
période, ce qui rend correct le franchissement de seuil en cours d'exercice.

Le moteur ne raisonne jamais « régime X ⇒ rien à faire » : le caractère
libératoire d'un forfait libère de l'impôt sur le bénéfice, et de lui seul. Les
cotisations sociales, les retenues à la source et les taxes locales subsistent.

**Le retard est calculé, jamais stocké** — c'est une comparaison entre une
échéance et une date, pas une propriété de l'obligation.

Ce contexte porte le **troisième maillon de la chaîne de valeur** : la ligne
« TVA rejetée par le contrôle de conformité » de la déclaration mensuelle. C'est
elle qui transforme les constats du contexte D en argent, et c'est elle qui a
imposé l'arête `obligations → conformite` lors de l'audit des flux.

Voir Docs/architecture/01-contextes-bornes.md, 10-flux-fonctionnels.md, et le
manuel Docs/manuel/ § 3.2 et 3.10.
"""
