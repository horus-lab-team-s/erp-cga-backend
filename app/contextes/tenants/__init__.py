"""Contexte N · Tenants.

Le plan de contrôle. Cycle de vie d'un tenant : slug, ouverture, quotas, suspension,
résiliation. C'est lui qui décide qu'un sous-domaine répond, et à qui.

POURQUOI IL EST SÉPARÉ DE L'IDENTITÉ

Ouvrir un tenant est une opération longue, rare, et transactionnelle sur plusieurs
systèmes. Vérifier un jeton est une opération courte, constante, sur le chemin critique de
chaque requête. Les deux n'ont ni le même profil de charge, ni la même exigence de
disponibilité : si le provisionnement tombe, les tenants déjà ouverts continuent de
fonctionner, seules les nouvelles souscriptions attendent.

CE QU'IL NE CONNAÎT D'AUCUN MÉTIER

Rien, et c'est la règle. Un tenant ne sait pas s'il porte une comptabilité, une paie ou
autre chose. Le jour où la plateforme sert un autre secteur, ce contexte ne bouge pas.
"""
