# Politique de confidentialité de SMFTP

Dernière mise à jour : 26 novembre 2026. S'applique à SMFTP 1.1 et aux versions suivantes, quelle que soit la source d'installation (Microsoft Store, Uptodown, GitHub).

## En bref

SMFTP ne collecte aucune donnée. L'application ne contient ni statistiques d'utilisation, ni publicité, ni rapport de plantage automatique. L'éditeur de SMFTP ne reçoit rien de ce que vous faites dans l'application.

## Données conservées sur votre ordinateur

SMFTP enregistre localement, dans votre compte Windows uniquement :

- vos profils de serveurs : nom, hôte, port, utilisateur, dossiers de départ, chemin de la clé SSH (`%APPDATA%\SMFTP`) ;
- les empreintes des serveurs SSH que vous avez acceptées (`known_hosts`) ;
- la file de transferts non terminée et les tâches planifiées, sans mot de passe ;
- un journal technique (`smftp.log`), dont les mots de passe sont masqués ;
- vos préférences d'affichage (registre Windows, `HKEY_CURRENT_USER\Software\SMFTP`).

Les mots de passe, phrases secrètes et la clé API de l'assistant ne sont enregistrés que si vous cochez « Mémoriser ». Ils sont alors stockés dans le Gestionnaire d'identification de Windows, jamais dans un fichier de SMFTP.

La version Microsoft Store range ces fichiers dans le dossier privé de l'application. Windows les supprime à la désinstallation.

## Connexions réseau

SMFTP se connecte uniquement :

1. **Aux serveurs que vous configurez** (FTP, FTPS, SFTP, SSH), pour les opérations que vous demandez ou que vous avez planifiées.
2. **À GitHub** (`api.github.com`, et `github.com` avec ses serveurs de fichiers pour télécharger une mise à jour) : au démarrage, une fois par jour au plus, pour vérifier s'il existe une nouvelle version (désactivable dans *Préférences → Vérifier automatiquement les mises à jour*), quand vous choisissez *Aide → Rechercher les mises à jour*, et quand vous choisissez *Télécharger et installer*. Ces requêtes ne contiennent aucune donnée personnelle. La version Microsoft Store, mise à jour par le Store, n'en envoie aucune.
3. **Au service d'intelligence artificielle de votre choix**, seulement si vous utilisez l'assistant (voir ci-dessous).

## Assistant IA (facultatif)

L'assistant est désactivé tant que vous ne l'utilisez pas. Avant le premier envoi à Claude, SMFTP vous demande votre accord.

- **Claude (Anthropic)** : vos messages sont envoyés à Anthropic avec votre propre clé API. S'y ajoutent les informations que l'assistant consulte pour vous répondre : noms de fichiers et de dossiers, noms de profils, état des transferts, extraits du journal. Les mots de passe ne sont jamais transmis. Ces données sont traitées selon la politique de confidentialité d'Anthropic : <https://www.anthropic.com/legal/privacy>.
- **Ollama** : les mêmes informations sont envoyées au serveur Ollama dont vous indiquez l'adresse, par défaut votre propre ordinateur (`localhost`).

## Effacer vos données

- Profils : clic droit sur un profil → Supprimer. Son mot de passe est retiré du Gestionnaire d'identification.
- Tout le reste : supprimez le dossier `%APPDATA%\SMFTP` et la clé de registre `HKEY_CURRENT_USER\Software\SMFTP`, puis les entrées « SMFTP » du Gestionnaire d'identification Windows.

## Contact

Questions ou demandes : <https://github.com/Smeth/smftp/issues>.
