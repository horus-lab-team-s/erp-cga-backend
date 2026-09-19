"""Le socle d'orchestration : enchaîner des effets qui débordent la transaction.

─────────────────────────────────────────────────────────────────────────────────
CE PAQUET EST UN MÉCANISME, PAS DU MÉTIER

Même discipline que `app/moteur/` : il ne connaît ni tenant, ni paiement, ni
facture. Ce dont il a besoin lui est passé en argument. Un test d'architecture le
vérifie, et il existe pour la même raison que celui du moteur : la première
importation d'un contexte métier le rendrait inutilisable par les autres, et
personne ne s'en apercevrait avant d'essayer.

À QUOI SERT UNE SAGA, ET QUAND ELLE NE SERT À RIEN

Une saga découpe une opération longue en étapes, chacune avec sa compensation, et
sait reprendre là où elle s'est arrêtée.

⚠️ **Elle ne sert à rien pour ce qui tient dans une transaction.** Ce système est
un monolithe modulaire : deux écritures dans deux contextes différents partagent
la même base et la même transaction. Y poser une saga ajouterait de la machinerie
pour remplacer un `COMMIT` qui fonctionne, et ce serait du culte du modèle.

**La saga sert exactement là où la transaction s'arrête** : les effets **hors
base**. Créer un enregistrement DNS, ouvrir un préfixe de stockage, envoyer un
courriel, appeler un prestataire de paiement. Aucun `ROLLBACK` ne les défait.

C'est le cas de l'ouverture d'un tenant, et c'est pour cela que la première saga
du système est celle-là.

L'ORCHESTRATION PLUTÔT QUE LA CHORÉGRAPHIE

Deux façons de coordonner des étapes. En **chorégraphie**, chaque service écoute
les événements des autres et réagit : personne ne connaît le déroulé complet. En
**orchestration**, un chef d'orchestre appelle les étapes dans l'ordre et sait où
l'on en est.

Nous prenons l'orchestration, pour trois raisons :

* le déroulé est **linéaire et connu** — sept étapes dans un ordre imposé. Une
  chorégraphie l'éparpillerait dans sept endroits sans que le déroulé n'existe
  nulle part ;
* **la question qu'on posera est « où en est ce tenant ? »**, et elle a une
  réponse en un endroit ;
* **la compensation demande de connaître l'ordre inverse**, ce qu'aucun
  participant d'une chorégraphie ne connaît.

La chorégraphie sera le bon choix quand le parcours se ramifiera entre équipes.
Pas avant.

LA REPRISE EN AVANT EST LE DÉFAUT, LA COMPENSATION L'EXCEPTION

Une étape qui échoue n'annule pas tout : elle se rejoue. C'est ce qu'il faut pour
un provisionnement, où l'échec est presque toujours passager — un service lent, un
verrou, une coupure.

La compensation ne sert qu'à **abandonner volontairement** : le client se rétracte,
le paiement est contesté, l'exploitation renonce. Elle est alors demandée, jamais
déclenchée par un échec technique.

Confondre les deux produit le pire comportement possible : un tenant à moitié
créé, détruit par une coupure réseau de trois secondes.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

__all__: list[str] = []
