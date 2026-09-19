"""Contexte M · Souscription.

L'offre commerciale, le devis, l'encaissement mobile et l'ouverture de l'accès.
Le parcours qui va de la page de tarifs du site public au premier écran de
l'espace adhérent.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI UN TREIZIÈME CONTEXTE

Le dossier d'architecture en décrivait douze. Celui-ci s'ajoute, et l'ajout se
justifie par l'échec des trois autres placements possibles :

* **L · Vitrine** est du contenu éditorial, et son graphe de dépendances est vide
  délibérément — « du contenu qui aurait besoin d'un paramètre légal ne serait
  plus du contenu ». Un encaissement encore moins.
* **I · Création d'entreprise** est une prestation parmi cinq. Y loger la
  souscription obligerait à passer par la création pour vendre une domiciliation.
* **A · Référentiel** porte des valeurs **légales**. Les honoraires du cabinet
  n'en sont pas : il les fixe librement, et les mêler aux taux du Code général
  des impôts brouillerait la seule chose que le référentiel doit garantir.

Ce que ce contexte détient et que personne d'autre ne détient : **combien coûte
notre service, et comment on l'encaisse**.

SES ARÊTES

`souscription → portefeuille`, pour une seule question — ce NIU est-il déjà
suivi ? — plus le socle, `referentiel` et `transverse`.

Personne ne le lit, sauf J · Pilotage le jour où il existera. Une souscription
n'est pas un dossier : elle dit qu'un accès a été payé, pas que l'entreprise est
connue.

CE QUI EST IMPLÉMENTÉ

Catalogue à barème daté, devis figeant le prix du jour, engagement, encaissement
mobile par Tara avec clé d'idempotence, rapprochement à trois stratégies,
réconciliation par appel sortant, péremption à vingt-quatre heures, et ouverture
de l'accès adhérent par le contexte K.

CE QUI RESTE À FAIRE

* **Les frais officiels de création**, au contexte A. Tant qu'ils n'y sont pas,
  la création d'entreprise n'est pas souscriptible en ligne — décision assumée,
  voir `domaine/offre.py`.
* **Les échéances d'abonnement.** La première mensualité est encaissée ; les
  suivantes ne sont ni appelées, ni relancées. C'est le manque le plus important
  de ce lot.
* **Le remboursement.** Aucun geste ne le représente aujourd'hui.
* **La facturation des honoraires**, qui relève de K et qui devra passer par les
  mêmes règles de conformité que celles appliquées aux adhérents.
─────────────────────────────────────────────────────────────────────────────────
"""
