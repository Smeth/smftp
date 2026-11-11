# SMFTP ? V1

Premi?re version publique : **V1 (1.0.0)**. Client de transfert exp?rimental ; les d?fauts connus sont d?taill?s dans le bilan d?audit ci-dessous.

Client de bureau natif Python / PySide6, en français. Le design reprend la lisibilité de Cyberduck, l’explorateur à deux panneaux de Transmit et la barre latérale, les onglets et les accents bleus de VS Code. Il ne réutilise pas leurs marques ni leurs ressources graphiques.

## Démarrage Windows

Python 3.11 ou plus récent :

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Pour lancer avec le script Windows : `powershell -ExecutionPolicy Bypass -File .\run.ps1`.

Sur Linux / macOS : `python3 -m venv .venv`, puis `.venv/bin/pip install -r requirements.txt` et `.venv/bin/python main.py`. Un environnement graphique Qt et un backend de coffre-fort système sont nécessaires. Ces plateformes n’ont pas été testées dans cette session.

## Utilisation

- **Nouvelle connexion** : SFTP (port 22), FTPS explicite (21) ou FTP (21). Le champ secret accepte aussi la phrase secrète de la clé SSH. **Parcourir…** ouvre le sélecteur de fichiers pour une clé privée OpenSSH/PEM (SFTP uniquement). Chaque profil possède un groupe, un dossier local et distant de départ et un délai réseau de 5 à 300 secondes. FTP n’est pas chiffré ; SFTP est choisi par défaut.
- Les profils peuvent être sauvegardés **avant toute connexion**, avec **Enregistrer seulement**. **Enregistrer et connecter** conserve le profil même si la tentative échoue. **Double-cliquer sur un profil connecte directement avec le secret du coffre-fort, sans rouvrir le formulaire.** Si aucun secret n’est disponible et qu’aucune clé SSH n’est configurée, le formulaire est nécessaire. Le clic droit distingue **Connecter**, **Modifier** et **Supprimer**. Le menu **Fichier → Gestionnaire de sites** présente les profils par groupes. Les doublons sont fusionnés par protocole, hôte, port et utilisateur. Le secret est stocké exclusivement dans le coffre-fort si la case est cochée ; la décocher et enregistrer le supprime. Une indisponibilité du coffre-fort est signalée explicitement.
- Première connexion SSH : comparez l’empreinte affichée avec celle fournie par l’administrateur avant de l’accepter. Une clé qui change est bloquée.
- Chaque panneau possède une adresse éditable, un fil d’Ariane cliquable, un historique, une recherche et le tri par colonne.
- Glissez des fichiers locaux (ou depuis l’Explorateur Windows) vers le panneau distant pour les envoyer ; glissez du distant au panneau local pour les télécharger. Les dossiers et sélections multiples sont pris en charge. Un dépôt sur un dossier cible son contenu. Un dépôt dans le même panneau propose un déplacement.
- Le menu contextuel propose transferts, création de dossier, renommage, suppression et permissions distantes. Les suppressions sont définitives et demandent confirmation. Les déplacements ne remplacent pas silencieusement une destination locale existante.
- La file permet 1 à 5 connexions simultanées, pause/reprise, annulation et relance. Les conflits proposent écraser, ignorer, renommer, reprendre, **copier si la source est plus récente** ou **si la taille diffère**. La comparaison par date admet deux secondes d’écart et ne remplace pas une comparaison du contenu. La limite de débit est **par transfert** ; zéro signifie illimité. Concurrence et débit sont conservés entre lancements. Le menu **Transferts** contrôle toute la file.
- La file non terminée est sauvegardée dans `queue.json`, sans mot de passe, puis restaurée **en pause**. Connectez le profil concerné puis choisissez **Transferts → Reprendre tout**. Les transferts temporaires d’édition, de copie distante et de préparation du glisser externe ne sont pas restaurés. Les transferts annulés volontairement ne sont pas relancés automatiquement.
- Les envois utilisent un fichier distant caché `.nom.smftp-….part`, vérifient sa taille et que la source n’a pas changé, puis publient par renommage. La reprise réutilise le temporaire correspondant à la même source et à sa date/taille. Le remplacement d’un fichier existant demande le support de RNTO avec remplacement côté FTP ou de l’extension `posix-rename` côté SFTP. **Si le serveur refuse, l’original est conservé et l’erreur affichée** ; l’application ne le supprime pas pour forcer l’opération. Un temporaire peut rester après interruption et être supprimé manuellement depuis la vue des fichiers cachés. Les permissions SFTP existantes sont conservées lors du remplacement.
- **Modifier / Aperçu** télécharge une copie temporaire et ouvre l’éditeur UTF-8 avec coloration ou l’aperçu d’image (20 Mo maximum). `Ctrl+S` enregistre et envoie le texte. Taille et date distantes sont revérifiées avant envoi ; un changement demande confirmation. L’éditeur système est aussi disponible : les sauvegardes détectées proposent un renvoi.
- **Outils → Synchroniser** compare récursivement les deux dossiers et présente une sélection avant copie. Taille et date sont comparées, sans hachage. La synchronisation copie les fichiers nouveaux/différents, conserve les fichiers supplémentaires et ne supprime rien. Les dossiers vides ne sont pas synchronisés.
- **Serveurs** importe / exporte des profils JSON ou importe FileZilla XML (gestionnaire de sites et serveurs récents : FTP, SFTP, FTPS explicite). L’import conserve noms, utilisateurs et chemins de clés SSH, y compris les serveurs contenus dans des dossiers XML. Les mots de passe XML en clair ou Base64 peuvent être importés **uniquement dans le coffre-fort**, après un choix explicite (Non par défaut). Les mots de passe protégés par un mot de passe maître FileZilla ne peuvent pas être déchiffrés : un avertissement demande de les saisir à nouveau. Les protocoles incompatibles, profils invalides et secrets illisibles sont signalés dans le bilan de l’import. Les secrets des fichiers JSON sont ignorés ; les exports ne contiennent aucun secret.
- Le **Journal de connexion** s’affiche automatiquement dès une tentative, avec horodatage et défilement automatique. Il reçoit les étapes en temps réel depuis les workers : ouverture TCP, authentification, lecture du dossier et résultat. En FTP/FTPS, il montre les commandes et les réponses réelles du serveur (220, 331, 230, 530…), ainsi que la négociation TLS. En SFTP, il montre la négociation SSH, la vérification de la clé, la méthode d’authentification et l’ouverture du sous-système SFTP. Les commandes PASS/ACCT/ADAT et les secrets connus sont masqués avant de quitter le worker ; le mode debug brut de ftplib n’est pas activé.
- Les erreurs réseau apparaissent directement dans le journal et la barre d’état, sans boîte de dialogue bloquante : explication, type, protocole, hôte, port et réponse réelle du serveur. Les refus FTP (530, 550, 425…), erreurs DNS, connexion refusée, coupures, délais, négociation SSH/TLS et problèmes de clé sont distingués. Le serveur peut donner une réponse vague : l’application conserve cette réponse sans inventer une cause plus précise. Les erreurs d’import ou d’enregistrement conservent une boîte de dialogue détaillée. Les connexions de transfert ont aussi leurs événements dans le journal, et leurs erreurs dans l’infobulle du statut. La confirmation d’une clé SSH inconnue reste obligatoire.
- **Préférences** configure dossiers de départ et temporaire, concurrence et conflits. **Apparence** alterne les thèmes clair / sombre.
- **Fichier** propose connexion, gestionnaire de sites, reconnexion, déconnexion, dix connexions récentes et quitter. **Édition** propose copier/couper/coller, sélection, propriétés et recherche récursive (`Ctrl+Shift+F`, motifs `*.php` etc., limite 10 000 résultats). Les copies locales sont accessibles au presse-papiers Windows ; la copie distante utilise un intermédiaire local. Couper/coller entre serveur et disque n’efface rien automatiquement : copiez puis supprimez après vérification.
- **Affichage** propose fichiers distants cachés, vues en icônes, panneaux horizontaux/verticaux, journal et transferts. **Aide** fournit documentation, raccourcis, diagnostic et version. Les boîtes de dialogue Qt standard sont traduites en français quand le catalogue fourni est chargé.
- **Aide → Rechercher les mises à jour** compare la version installée à la dernière release GitHub et ouvre sa page de téléchargement si une version plus récente existe. Le téléchargement et l’installation restent à confirmer depuis GitHub.
- **Préférences → Reconnexion automatique** active jusqu’à trois nouvelles tentatives espacées de 3, 6 et 9 secondes après une erreur réseau transitoire. Les échecs d’authentification et de vérification TLS/SSH ne sont pas relancés automatiquement. La perte d’une connexion inactive est détectée lors de l’opération suivante. Les notifications système de fin de transfert peuvent être désactivées dans les préférences.
- **Outils → Commande FTP** envoie une commande de contrôle sur la session FTP/FTPS et affiche sa réponse dans le journal. Les commandes d’authentification et de transfert de données restent réservées à l’interface. Les commandes potentiellement destructrices demandent confirmation.
- **Glisser du distant vers l’Explorateur Windows** : clic droit → **Préparer le glisser-déposer externe** ; attendez le message « Copie prête », puis glissez la sélection vers l’Explorateur. Cette préparation télécharge une copie locale ; gardez l’application ouverte jusqu’à la fin de la copie Windows. Une modification détectée par actualisation invalide le cache préparé. Le téléchargement Windows différé en un seul geste n’est pas implémenté.
- **Fermer la fenêtre demande confirmation.** Une connexion active sera fermée ; les transferts seront interrompus et conservés dans la file en pause. Les modifications temporaires non envoyées sont signalées avant de quitter. Le bouton Non laisse la fenêtre, la connexion et les transferts actifs.

Raccourcis : `Ctrl+K` connexion, `Ctrl+F` recherche dans le panneau actif, `Ctrl+N` dossier, `F2` renommer, `Suppr` supprimer, `F5` actualiser. Double-cliquez sur un dossier pour l’ouvrir.

## Architecture

```text
main.py                     point d’entrée
smftp/
  core/clients.py           BaseClient, FTPClient, FTPSClient, SFTPClient
  core/errors.py            diagnostic détaillé et masquage des secrets
  core/listing.py           repli FTP LIST Unix / Windows
  models/entries.py         connexions et fichiers
  workers/session.py        session de navigation dans un QThread
  workers/transfer.py       connexions de transfert indépendantes
  workers/local.py          opérations locales récursives
  workers/sync.py           comparaison récursive en arrière-plan
  workers/search.py         recherche récursive annulable
  ui/main_window.py         espace de travail et coordination
  ui/explorer.py            panneaux, navigation, recherche, drag & drop
  ui/transfers.py           file et commandes des transferts
  ui/connection_dialog.py   formulaire de connexion
  ui/editor.py              éditeur et aperçu
  ui/permissions.py         permissions octales / cases à cocher
  ui/settings.py            préférences
  ui/workspace_features.py  menus, profils directs, recherche et commandes
  utils/storage.py          signets, chemin utilisateur, journal rotatif
  utils/profiles.py         validation, fusion, import XML et coffre-fort
  utils/queue_store.py      file persistante sans secrets
  utils/paths.py            validation des noms distants avant écriture locale
resources/themes/           dark.qss, light.qss
tests/                      tests protocoles, transferts, sync et widgets
```

Les opérations réseau passent par des workers, les clients ne sont jamais partagés entre threads. Les transferts utilisent des connexions distinctes de la navigation. La pause est coopérative entre blocs ; une opération serveur bloquée reste soumise aux délais réseau. Les liens symboliques ne sont pas suivis pour les transferts locaux ; les noms distants sont validés lors d’un téléchargement récursif.

Les données utilisateur sont sous le chemin Qt `AppDataLocation/SMFTP` : `sites.json`, `known_hosts`, `smftp.log`. Les profils sont écrits de manière atomique avec une liste de champs autorisés ; les anciens profils restent compatibles. Les préférences utilisent `QSettings`. Les réponses d’erreur sont nettoyées avant journalisation ; aucun secret connu n’est enregistré dans les profils ou les journaux. Les journaux applicatifs tournent à 1 Mo avec trois archives. Les copies d’édition temporaires sont supprimées à la fermeture ; les modifications non envoyées déclenchent un avertissement.

## Tests

Si un XML déclare `standalone="true"` ou `standalone="false"`, l’import explique que les seules valeurs XML autorisées sont `yes` et `no`, puis propose la correction. Elle s’applique uniquement à la copie en mémoire après confirmation ; le fichier original reste inchangé. Les autres erreurs XML affichent leur ligne et colonne et ne sont pas réparées automatiquement.

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

Les tests démarrent des serveurs FTP et SSH/SFTP exclusivement sur `127.0.0.1`, dans des dossiers temporaires. Ils couvrent navigation, certificats TLS (configuration du client), empreinte SSH inconnue / mémorisée / modifiée, transferts FTP et SFTP avec reprise, copie récursive, suppression, annulation, plan de synchronisation et widgets. Les tests ne contactent aucun serveur de production. FTPS n’est pas testé de bout en bout avec un vrai serveur TLS.

## Exécutable Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
# Équivalent :
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --onedir --name SMFTP --add-data "resources;resources" --collect-submodules keyring.backends main.py
```

Résultat : `dist/SMFTP/SMFTP.exe`. Distribuer **tout le dossier**. Pour un seul fichier, remplacer `--onedir` par `--onefile`. Compiler séparément sur chaque OS ; sous Linux / macOS, le séparateur de `--add-data` est `:`.

## Limites et suites prévues

Cette version est une base fonctionnelle, pas encore un remplacement complet de FileZilla. Restent notamment le choix d’autres langues pour toute l’application, les animations, la recherche dans le contenu et la comparaison cryptographique des fichiers. Le français est la langue de l’application et le catalogue Qt français est fourni ; les autres catalogues applicatifs ne le sont pas.

Le dépôt sur dossier fonctionne dans les deux vues, mais la surbrillance avancée de la cible reste à améliorer. Le glisser externe requiert une préparation préalable, décrite plus haut. Le double-clic distant ouvre l’éditeur ou l’aperçu ; sa coloration est générique et ne remplace pas un IDE.

Un repli LIST prend en charge les formats Unix et Windows courants si MLSD est indisponible ; les formats inconnus sont refusés. Les dates LIST sans fuseau sont approximatives. La reprise des téléchargements suppose que le contenu distant n’a pas changé ; elle ne vérifie pas encore un hachage. Les sous-fichiers déjà terminés d’un dossier peuvent être recopiés lors d’une reprise. La détection de modifications distantes à l’édition utilise date/taille et ne garantit pas l’absence de changement entre vérification et publication. Les garanties de remplacement dépendent des capacités du serveur, comme indiqué plus haut. Les tests FTPS vérifient la configuration TLS mais ne couvrent pas encore un serveur TLS réel de bout en bout.

Le catalogue Qt compilé local n’est pas versionné : l’application cherche aussi les traductions fournies par l’installation Qt/PySide6. Leur disponibilité et leur licence doivent être vérifiées pour une distribution binaire.
