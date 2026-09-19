# Référentiel normatif

Aucune valeur légale n'est écrite dans le code. Toutes vivent ici, datées, sourcées
et modifiables sans redéploiement.

| Fichier | Ce qu'il porte |
|---|---|
| `parametres.yaml` | 59 paramètres scalaires : taux, seuils, délais, plafonds, pondérations |
| `baremes.yaml` | Les barèmes progressifs, dont l'IRPP : un paramètre porte une valeur, un barème porte des tranches |
| `regles/` | Les règles de conformité de facture, une par fichier |
| `ecarts/politique.yaml` | La politique d'écart des constats (pas 92, 103 et 118) : sévérités écartables, second regard, qui le donne, longueur du motif ; et, depuis le pas 103, les motifs types proposés par règle (le motif détaillé reste obligatoire) ; depuis le pas 118, la **pièce d'appui** exigée par sévérité et le délai au-delà duquel une dérogation sans preuve est « à régulariser ». Sans ce fichier, rien n'est écartable |
| `recherche/reglages.yaml` | Les réglages de la recherche globale (pas 93) : longueur minimale d'une requête, résultats par source. Sans ce fichier, les mêmes valeurs sobres s'appliquent |
| `notifications/abonnements.yaml` | Les abonnements aux notifications (pas 94) : pour une action du journal d'audit, qui la reçoit (permission, périmètre, compte nommé) et quel texte l'annonce. Sans ce fichier, aucune notification |
| `validation/circuit.yaml` | Le circuit de décision du cabinet sur le référentiel (pas 95) : qui propose et qui valide, selon la nature (LOI ou POLITIQUE_CABINET), quatre yeux, motif. Les décisions forment une surcouche propre au cabinet, appliquée au calcul suivant ; ce fichier-ci (`parametres.yaml`) n'est jamais réécrit. Sans le circuit, le référentiel est en lecture seule |
| `pilotage/mesures.yaml` | Le catalogue des mesures de direction sur un dossier à risque (pas 100) : code stable, libellé, ce que la mesure engage, niveaux de risque où elle est permise, échéance exigée ou non, longueur du motif. Sans ce fichier, aucune mesure ne peut être décidée ; la vue risque reste lisible |
| `rapprochement/reglages.yaml` | Les fenêtres de dates du rapprochement bancaire (pas 101) : au-delà de `fenetre_jours`, une écriture n'est pas proposée ; en deçà de `fenetre_proche_jours`, la proximité compte comme un argument. Sans ce fichier, 15 et 5 jours |
| `rapprochement/releves/*.yaml` | Un profil par format d'export de relevé bancaire (pas 101) : séparateur, encodage, format de date, colonnes (montant signé, ou débit et crédit du point de vue de la banque). Le moteur ne connaît aucune banque. Sans profil, l'import de fichier est fermé ; la saisie du relevé reste ouverte |
| `revue/echantillon.yaml` | L'échantillon proposé au réviseur sur un mois transmis (pas 102) : ce qu'est un montant rond, combien des plus gros montants entrent d'office, quand un compte est rare, le plafond. Figé à la transmission. Sans ce fichier, les mêmes valeurs |
| `pilotage/plan_de_travail.yaml` | Le plan de travail des collaborateurs (pas 104) : délai de traitement d'une pièce, jour limite du rapprochement et de la transmission au réviseur (au plus le 28), horizon des déclarations, seuils de priorité, ordre de gravité des six natures de tâche. Sans ce fichier, les mêmes valeurs |
| `pilotage/charge.yaml` | La charge des collaborateurs (pas 105) : capacité en points par rôle, points par dossier, pièce en attente, échéance du mois et retard, seuil de saturation et seuil cible des réaffectations proposées. La charge décrit la répartition du travail, elle n'évalue personne. Sans ce fichier, les mêmes valeurs |
| `pilotage/pieces_manquantes.yaml` | Les pièces qui manquent au mois d'un dossier et leur relance (pas 111) : origines d'attente relevées (demande ouverte, relevé bancaire, mouvement sans pièce, série habituelle, obligation du mois, anomalie bloquante), seuils de la série habituelle, pièce appelée par chaque obligation, date limite annoncée (échéance de référence moins un délai, reportée si elle est passée), canaux (actif, coché d'office, motif), modèles de message dont la conclusion annonce la date limite, nom du cabinet sous la signature. Sans ce fichier, des valeurs prudentes et un seul modèle |
| `obligations/rappels_adherent.yaml` | Les rappels d'échéance de l'adhérent (pas 115) : les jalons proposés (7, 3, 2, 1 jours avant), le réglage par défaut (actifs, 7 et 2 jours), le rythme du travail périodique qui les envoie, et le motif affiché sous « Recevoir aussi par WhatsApp ». Refusés : des jalons par défaut hors des choix, des rappels actifs sans jalon, un jalon en double ou hors de 0 à 60 jours. Sans ce fichier, les mêmes valeurs |
| `obligations/explications_adherent.yaml` | Les échéances telles que l'adhérent les lit (pas 113) : pour chaque obligation du catalogue, un titre courant (« Impôt trimestriel »), « de quoi s'agit-il » et ce qu'entraîne un retard ; l'horizon des échéances montrées et la durée d'affichage d'un dépôt. **Statut `A_VALIDER`** : une proposition, que le fiscaliste relit puis passe à `VALIDE` en se nommant (refusé sans nom). Sans ce fichier, le libellé du catalogue seul |
| `acces/adherents.yaml` | Le renvoi d'un lien d'accès à un adhérent par son chargé de clientèle (pas 116) : combien de liens un même compte peut recevoir par jour, et la longueur minimale de la vérification écrite (« rappelé au numéro du dossier »). Le lien part toujours à l'adresse déjà au dossier. Sans ce fichier, deux renvois par jour et quinze caractères |
| `collecte/reponses_adherent.yaml` | Les réponses toutes faites de l'adhérent à une demande du cabinet (pas 112) : « Je l'aurai la semaine prochaine », « Je n'ai pas ce document », et ce que « la semaine prochaine » veut dire en jours. Une réponse ne ferme jamais la demande. Refusés : une réponse réglée deux fois, le message libre réglé comme une réponse. Sans ce fichier, les mots de la maquette et sept jours |
| `portefeuille/espace_adherent.yaml` | « Mon entreprise » dans les mots de l'adhérent (pas 114) : la forme juridique, le centre des impôts et le régime en toutes lettres (le régime avec une explication), les natures de changement que l'adhérent peut signaler (adresse, dirigeant, activité, téléphone, autre) et leur aide, le nombre de signalements récents montrés. **Statut `A_VALIDER`**, validation nommée exigée. Sans ce fichier, les codes et les natures de la maquette |
| `pilotage/file_d_anomalies.yaml` | La file d'anomalies du réviseur (pas 117) : au-delà de combien de jours un constat « dort » et passe devant dans sa gravité, et les gravités montrées (l'information n'y est pas : elle n'appelle aucune décision). Refusés : une gravité inconnue, une file sans gravité, une file sans les constats bloquants. Sans ce fichier, quinze jours et les trois gravités qui appellent une décision |
| `pilotage/espace_adherent.yaml` | L'accueil de l'adhérent (pas 112) : le bandeau du mois dans ses quatre états (pièces à envoyer, avec date limite, date passée ou sans date ; le cabinet vérifie ; aucun justificatif ; complet), et les racines des comptes d'achats montrés pour un mois revu. Refusés au chargement : un bandeau « à envoyer » qui tairait `{date_limite}`, une valeur que l'écran ne connaît pas, « de {mois} » sans élision. Sans ce fichier, les mots de la maquette |
| `obligations/formulaire_tva.yaml` | Les lignes de la déclaration de TVA (pas 109) : pour chaque grandeur calculée (ventes taxables, exonérées, TVA collectée, déductible sur biens, services, immobilisations, autres, TVA rejetée, crédit reporté), son code et son libellé, et les racines de comptes qui la composent. ⚠️ Codes L01 à L30 de la maquette, à confirmer avec le formulaire de la DGI en vigueur. Sans ce fichier, les mêmes valeurs |
| `obligations/depot_tva.yaml` | L'exigence d'une revue du mois avant le dépôt de TVA (pas 109) : AUCUNE, TRANSMISE ou VALIDEE (TRANSMISE par défaut), et le niveau si elle manque, BLOQUANT (défaut) ou RESERVE. « Le comptable transmet, il ne dépose pas. » Sans ce fichier, les mêmes valeurs |
| `lettrage/reglages.yaml` | Le lettrage des comptes de tiers (pas 108) : l'écart admis entre débit et crédit d'un lettrage (0 FCFA par défaut : un écart admis est un reste dû qu'on cesse de voir), et l'exigence d'un même tiers sur toutes les lignes. Sans ce fichier, les mêmes valeurs |
| `cloture_mensuelle/reglages.yaml` | La clôture mensuelle d'un dossier (pas 107) : pour chacun des huit points de contrôle (pièces du mois, brouillons, rapprochement, caisse, balance, numérotation, pièces attendues, écarts en suspens), actif ou non, bloquant ou informatif ; les statuts de revue qui verrouillent le mois (TRANSMISE, VALIDEE) ; les racines des comptes de caisse et d'achats. Les brouillons bloquent toujours, un mois renvoyé ne se verrouille jamais. Sans ce fichier, les mêmes valeurs |
| `pilotage/rapport_mensuel.yaml` | Le rapport mensuel de la direction (pas 106) : numéro d'agrément du centre (à renseigner par le cabinet), engagement de sincérité, sections incluses parmi RISQUE, CHARGE, DEROGATIONS, QUALITE_DES_REGLES, DECISIONS. Sans ce fichier, toutes les sections et un agrément « à renseigner » |
| `fiche_de_contreseing.py` | Édite la fiche de relecture destinée au fiscaliste et à la direction |

## Deux natures, deux autorités

C'est la distinction la plus importante du dossier, et elle manquait jusqu'au
18 août 2026.

    nature: LOI                un texte fixe la valeur. Se valide en la confrontant
                               à l'article visé. Engage un professionnel.
    nature: POLITIQUE_CABINET  aucun texte ne la fixe. Se valide par une décision
                               de la direction. La valeur est choisie, pas constatée.

Le **statut** dit *si* c'est validé. La **nature** dit *par qui ça peut l'être*.
Les confondre produisait une absurdité : les poids du score de risque attendaient
la signature d'un fiscaliste, alors que rien dans le CGI ne dit ce que vaut un
retard déclaratif dans une note interne. Ils attendaient une signature que
personne n'avait qualité pour donner.

Le défaut du champ est `LOI`, délibérément. Un paramètre dont on oublie de
déclarer la nature est traité comme engageant : l'oubli coûte une relecture
inutile, jamais une valeur légale passée sans contrôle.

## Fondement contre source

Deux champs distincts, et la distinction est ce qui rend une validation
vérifiable par un tiers.

    texte    quel texte fait autorité. L'article visé.
    source   ce qui a été RÉELLEMENT ouvert au moment de la validation.

Si la source n'est pas le texte lui-même, c'est exactement là que la relecture
d'un fiscaliste apporte quelque chose. Écrire « CGI art. 143 » en source quand on
a lu une fiche de vulgarisation ferait croire à une lecture qui n'a pas eu lieu.

## Une validation engage quelqu'un de nommé

Le modèle **refuse** une version `VALIDE` qui ne porterait pas `valide_par` et
`valide_le`. La règle de conformité aussi, depuis le 18 août 2026 : une règle
porte une *interprétation* du texte, donc à plus forte raison.

Une validation anonyme vaudrait moins qu'une absence de validation, parce
qu'elle affirmerait sans que personne n'en réponde.

## État au 18 août 2026

| | |
|---|---|
| Validés | 50 paramètres, 1 règle |
| En attente | 9 paramètres, 4 règles |
| Signataire | CGA Broad Range Consulting Group, agrément MINFI/DGI n° 00000048 |

⚠️ **Rien n'a été lu sur le CGI relié.** Les sources sont les fiches officielles de
la DGI, les textes CNPS et les actes uniformes OHADA, consultés en ligne. La fiche
de contreseing existe pour qu'un fiscaliste disposant du texte repasse sur les 50.

## Rééditer la fiche de contreseing

```bash
python3 Docs/referentiel/fiche_de_contreseing.py
```

Elle se génère depuis `parametres.yaml` et ne se modifie pas à la main : corriger
le fichier source, puis rééditer. Ce qu'un relecteur barre sur la fiche repasse au
statut `A_VALIDER` dans le YAML, et le produit le signalera de nouveau sur chaque
chiffre qui en dépend.
