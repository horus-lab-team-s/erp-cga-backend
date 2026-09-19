# Grille d'affectation commerciale

À qui confier une demande de contact, et pourquoi celui-là.

## Ce que contient ce dossier

Un fichier par critère, plus ce README. Chaque critère est évalué par
`app/moteur/` sur une **candidature** : la rencontre entre une demande et un
responsable possible. On évalue autant de candidatures qu'il y a de responsables,
et le moins pénalisé qui n'a pas été écarté l'emporte.

## La convention de lecture, qui surprend au premier abord

**VRAI = ce responsable convient de ce point de vue. FAUX = le critère est
atteint.**

On écrit donc `meme_agence == true` pour **pénaliser l'éloignement**, et non
l'inverse. C'est la convention des trois domaines qui tournent sur ce moteur, et
en changer pour celui-ci reviendrait à avoir deux moteurs.

## Deux natures de critère

| Champ | Effet |
|---|---|
| `redhibitoire: true` | La candidature est **écartée**. Le responsable ne peut pas prendre ce dossier. |
| `penalite: N` | La candidature est **moins bonne** de N points. Elle reste possible. |

Un critère peut être rédhibitoire sans pénalité : écarter est déjà le maximum.
Un critère qui n'est ni rédhibitoire ni pénalisant est refusé au chargement, parce
qu'il n'aurait aucun effet et laisserait croire le contraire à qui lit la grille.

**La distinction est ici, dans les fichiers, et non dans le code.** Ce qui bloque
aujourd'hui sera une préférence demain : le jour où le centre décidera qu'une
charge de plus de quarante dossiers interdit toute nouvelle affectation, ce sera
un booléen dans un fichier, pas une livraison.

## Les faits disponibles

Ceux du schéma `AFFECTATION_COMMERCIALE`, déclaré dans
`app/contextes/souscription/domaine/affectation.py`. Un chemin absent du schéma
fait échouer le chargement avec une suggestion, plutôt que de rendre un critère
silencieusement toujours vrai.

| Fait | Type | Ce qu'il dit |
|---|---|---|
| `service` | texte | La clé du service demandé au catalogue |
| `region_demande` | texte | La région déclarée par le visiteur |
| `agence_responsable` | texte | L'agence de rattachement du responsable |
| `meme_agence` | booléen | Dérivé, insensible à la casse. Faux si la région n'a pas été déclarée |
| `competences` | liste | Ce que le responsable sait faire |
| `competence_requise` | texte | Ce que le service exige. Vide quand il n'exige rien |
| `dossiers_ouverts` | entier | Dossiers commerciaux en cours chez lui |
| `charge_ponderee` | décimal | Ces dossiers pondérés par leur score de charge |
| `disponible` | booléen | En mesure de prendre un dossier aujourd'hui |

## Le statut de validation

Comme partout : `A_VALIDER` tant que le centre n'a pas arrêté la grille. Un
critère `VALIDE` doit porter `valide_par` et `valide_le`. Sans signataire nommé,
la règle de routage n'est opposable à personne.

Les valeurs ci-dessous sont **provisoires** : elles reproduisent la pratique
actuelle du centre telle qu'elle a été décrite, et attendent d'être arrêtées.
