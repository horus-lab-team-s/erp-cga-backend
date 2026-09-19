"""Contexte K · Transverse.

Multi-tenant, IAM et RBAC, journal d'audit chaîné par hachage, GED à conservation
10 ans, notifications multicanal, et la facturation des honoraires du CGA lui-même —
soumise aux mêmes règles que celles qu'il contrôle.

─────────────────────────────────────────────────────────────────────────────────
CE QUI EST IMPLÉMENTÉ

**L'identité** — `Compte`, avec un état à trois valeurs dont la plus utile est
« créé mais jamais activé ».

**Les habilitations datées** — un rôle n'est pas un attribut, c'est une période.
La question à laquelle ce contexte doit savoir répondre n'est pas « qui est
comptable » mais « qui était habilité le 12 mars, jour de ce dépôt ».

**Le cloisonnement par portefeuille** — un comptable ne voit que les dossiers de sa
portée. Deux comptables aux portefeuilles disjoints détiennent exactement les mêmes
permissions et ne voient pas les mêmes dossiers.

**Les sessions révocables**, côté serveur, pour que couper un accès veuille dire
maintenant et non ce soir.

**Les liens à usage unique** — activation après souscription, réinitialisation,
invitation. Le secret n'existe en clair qu'une fois ; seule son empreinte est
conservée.

**Le journal d'audit chaîné**, append-only, sans méthode de modification ni de
suppression.

CE QUI RESTE À FAIRE

* **Le second facteur.** Déclaré dans `EXIGE_MFA`, jamais fourni : le dépôt d'une
  déclaration est donc refusé. Blocage assumé et visible, préféré à une
  autorisation silencieuse.
* **La GED** — stockage objet, chiffrement au repos, purge à dix ans.
* **Les honoraires du CGA** — facturation du cabinet à ses adhérents.
* **L'envoi réel des courriels.** Le port existe, taillé sur la signature du
  module `mail+paiement/mail/` du dépôt ; l'adaptateur retient les messages au
  lieu de les émettre.
* **L'ancrage externe du journal.** Le chaînage détecte l'altération ponctuelle,
  pas un recalcul complet par quelqu'un qui contrôle la base.

K APPARTIENT AU SOCLE

Lisible par les onze autres contextes, il n'en lit aucun. Un dossier y est désigné
par son NIU, jamais par une entité `Entreprise` : sans cela, le portefeuille
lirait K pour ses droits et K lirait le portefeuille pour ses dossiers, et les
deux n'en feraient plus qu'un.

Voir Docs/architecture/05-securite-multitenant.md et 01-contextes-bornes.md.
─────────────────────────────────────────────────────────────────────────────────
"""
