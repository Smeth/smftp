# SMFTP 1.1

Version actuelle : **1.1.0**. Les défauts relevés par l’audit du 9 novembre 2026 sont corrigés et couverts par des tests (voir [docs/AUDIT_2026-11-09.md](docs/AUDIT_2026-11-09.md) pour le constat d’origine). Données personnelles : voir la [politique de confidentialité](docs/CONFIDENTIALITE.md).

Client de bureau natif Python / PySide6, en français. Le design reprend la lisibilité de Cyberduck, l’explorateur à deux panneaux de Transmit et la barre latérale, les onglets et les accents bleus de VS Code. Il ne réutilise pas leurs marques ni leurs ressources graphiques.

## Démarrage Windows

Python 3.11 ou plus récent :

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

`requirements.txt` donne les plages compatibles. `requirements.lock` fige les versions exactes de la dernière version testée : à installer pour construire une version publiée (`build.ps1` signale tout écart).

Pour lancer avec le script Windows : `powershell -ExecutionPolicy Bypass -File .\run.ps1`.

Sur Linux / macOS : `python3 -m venv .venv`, puis `.venv/bin/pip install -r requirements.txt` et `.venv/bin/python main.py`. Un environnement graphique Qt et un backend de coffre-fort système sont nécessaires. Ces plateformes n’ont pas été testées dans cette session.

## Utilisation

- **Nouvelle connexion** : SFTP (port 22), FTPS explicite (21), FTPS implicite (990), FTP (21) ou **SSH (terminal)** (22). Le FTPS implicite chiffre la connexion dès son ouverture, avant le message d’accueil du serveur ; le certificat est vérifié comme en FTPS explicite. Le champ secret accepte aussi la phrase secrète de la clé SSH. **Parcourir…** ouvre le sélecteur de fichiers pour une clé privée OpenSSH/PEM (SFTP uniquement). Chaque profil possède un groupe, un dossier local et distant de départ et un délai réseau de 5 à 300 secondes. FTP n’est pas chiffré ; SFTP est choisi par défaut. **Afficher** révèle le mot de passe saisi ou stocké dans le coffre-fort ; il est de nouveau masqué à la fermeture du formulaire.
- Les profils peuvent être sauvegardés **avant toute connexion**, avec **Enregistrer seulement**. **Enregistrer et connecter** conserve le profil même si la tentative échoue. **Double-cliquer sur un profil connecte directement avec le secret du coffre-fort, sans rouvrir le formulaire.** Si aucun secret n’est disponible et qu’aucune clé SSH n’est configurée, le formulaire est nécessaire. Le clic droit distingue **Connecter**, **Modifier** et **Supprimer**. Le menu **Fichier → Gestionnaire de sites** présente les profils par groupes. Les doublons sont fusionnés par protocole, hôte, port et utilisateur. Le secret est stocké exclusivement dans le coffre-fort si la case est cochée ; la décocher et enregistrer le supprime. Une indisponibilité du coffre-fort est signalée explicitement.
- **Terminal SSH** : un profil **SSH (terminal)** ouvre une console interactive au centre de la fenêtre à la place des panneaux de fichiers ; la barre « Mes serveurs » reste visible (icône terminal ; icône dossier pour FTP, FTPS et SFTP). Un serveur ouvert y apparaît en gras et en couleur, celui affiché au centre est surligné. Double-clic : ouvre le terminal, ou revient à la session déjà ouverte ; sur le serveur de fichiers déjà connecté, il réaffiche la vue Fichiers sans reconnecter. Chaque session est un onglet avec sa propre connexion SSH (**+** ou `Ctrl+Maj+T` : nouvelle session sur le même serveur, `Ctrl+Maj+W` : fermer). Un profil SFTP garde la vue Fichiers ; son clic droit propose aussi **Ouvrir un terminal**. **Affichage → Vue Fichiers / Vue Terminal** bascule aussi. En vue Terminal, les menus Édition et Transferts et les raccourcis de fichiers (`Suppr`, `F2`, `F5`…) sont désactivés : ils agiraient sur des panneaux masqués. Chaque onglet porte une pastille d’état (orange : connexion, vert : connecté, gris : terminé) et affiche les étapes de connexion. Le terminal émule un xterm (couleurs, `nano`, `vim`, `htop`, Tab, `Ctrl+C`) ; toutes les touches vont au serveur, sauf `Ctrl+Maj+I/T/W`. Copier-coller comme dans MobaXterm ou PuTTY : sélectionner à la souris copie, le clic droit colle (aussi `Ctrl+Maj+C` / `Ctrl+Maj+V` et le clic du milieu) ; `Maj+clic droit` ouvre le menu (copier, coller, effacer l’historique). Molette ou `Maj+Page préc./suiv.` : historique (5 000 lignes). Une session terminée se relance avec Entrée. Le dossier distant de départ du profil est ouvert avec `cd` ; mot de passe, clé, coffre-fort et vérification d’empreinte fonctionnent comme en SFTP. Les profils SSH ne sont pas proposés aux tâches planifiées ni à l’assistant. Un compte limité à `internal-sftp` refuse la console : le message du serveur s’affiche dans l’onglet.
- Première connexion SSH : comparez l’empreinte affichée avec celle fournie par l’administrateur avant de l’accepter. Une clé qui change est bloquée.
- Chaque panneau possède une adresse éditable, un fil d’Ariane cliquable, un historique, une recherche et le tri par colonne.
- Glissez des fichiers locaux (ou depuis l’Explorateur Windows) vers le panneau distant pour les envoyer ; glissez du distant au panneau local pour les télécharger. Les dossiers et sélections multiples sont pris en charge. Un dépôt sur un dossier cible son contenu. Un dépôt dans le même panneau propose un déplacement.
- Le menu contextuel propose transferts, création de dossier, renommage, suppression et permissions distantes. Les suppressions sont définitives et demandent confirmation. Les déplacements et copies ne remplacent jamais silencieusement une destination existante, locale ou distante : écraser (fichiers uniquement), renommer ou ignorer est demandé. Les dossiers ne sont jamais fusionnés ni remplacés. Un doublon au même endroit est nommé « nom (copie).ext », puis « nom (copie 2).ext ».
- La file permet 1 à 5 connexions simultanées, pause/reprise, annulation et relance. Les conflits proposent écraser, ignorer, renommer, reprendre, **copier si la source est plus récente** ou **si la taille diffère**. La comparaison par date admet deux secondes d’écart et ne remplace pas une comparaison du contenu. La limite de débit est **partagée par toute la file** et s’applique immédiatement aux transferts en cours ; zéro signifie illimité. Concurrence et débit sont conservés entre lancements. Le menu **Transferts** contrôle toute la file.
- La file non terminée est sauvegardée dans `queue.json`, sans mot de passe, puis restaurée **en pause**. Connectez le profil concerné puis choisissez **Transferts → Reprendre tout**. Les transferts temporaires d’édition, de copie distante et de préparation du glisser externe ne sont pas restaurés. Les transferts annulés volontairement ne sont pas relancés automatiquement.
- Les envois utilisent un fichier distant caché `.nom.smftp-….part`, vérifient sa taille et que la source n’a pas changé, puis publient par renommage. La reprise réutilise le temporaire correspondant à la même source et à sa date/taille. Le remplacement d’un fichier existant demande le support de RNTO avec remplacement côté FTP ou de l’extension `posix-rename` côté SFTP. **Si le serveur refuse, l’original est conservé et l’erreur affichée** ; l’application ne le supprime pas pour forcer l’opération. Un temporaire peut rester après interruption et être supprimé manuellement depuis la vue des fichiers cachés. Les permissions SFTP existantes sont conservées lors du remplacement.
- **Modifier / Aperçu** télécharge une copie temporaire et ouvre l’éditeur UTF-8 avec coloration ou l’aperçu d’image (20 Mo maximum). `Ctrl+S` enregistre et envoie le texte. L’éditeur numérote les lignes, propose rechercher (`Ctrl+F`, `F3`), remplacer (`Ctrl+H`) et aller à la ligne (`Ctrl+G`), et conserve les fins de ligne (CRLF/LF) et le BOM d’origine. L’aperçu s’ajuste à la fenêtre et se zoome (`Ctrl+molette`, `Ctrl++`, `Ctrl+-`, `Ctrl+0`). Taille et date distantes sont revérifiées avant envoi ; un changement demande confirmation. L’éditeur système est aussi disponible : les sauvegardes détectées proposent un renvoi.
- **Outils → Synchroniser** compare récursivement les deux dossiers et présente une sélection avant toute action. Par défaut, taille et date sont comparées. L’option **Comparer le contenu par empreinte** compare les fichiers de même taille par empreinte (SHA-256 si le serveur le permet) au lieu de la date : un fichier identique mais plus récent n’est plus recopié, un contenu différent de même taille est détecté. Si le serveur ne calcule pas d’empreinte, le journal le signale et la comparaison revient à la taille et à la date. Sans le **mode miroir**, la synchronisation copie les fichiers nouveaux/différents, conserve les fichiers supplémentaires et ne supprime rien. **En mode miroir**, les fichiers et dossiers de la destination absents de la source sont aussi proposés à la suppression, en rouge dans l’aperçu, et une confirmation est demandée avant de les supprimer définitivement. Un fichier modifié depuis la comparaison est conservé ; un dossier n’est supprimé que s’il est vide une fois ses fichiers supprimés, sans suppression récursive. Les suppressions ont lieu après la fin des copies, et aucune si une copie est annulée. Par prudence, ne sont jamais proposés : les liens symboliques et jonctions Windows (jamais suivis) et leur contenu, les noms qui ne diffèrent d’un nom de la source que par les majuscules ou la forme Unicode, et, si le serveur ne liste pas les fichiers cachés ou renvoie des lignes illisibles, les fichiers concernés. Les dossiers vides ne sont pas copiés. Au moment de la copie, une destination modifiée ou apparue depuis la comparaison n’est pas écrasée sans confirmation. Un plan vide est signalé.
- **Serveurs** importe / exporte des profils JSON ou importe FileZilla XML (gestionnaire de sites et serveurs récents : FTP, SFTP, FTPS explicite et implicite). L’import conserve noms, utilisateurs et chemins de clés SSH, y compris les serveurs contenus dans des dossiers XML. Les mots de passe XML en clair ou Base64 peuvent être importés **uniquement dans le coffre-fort**, après un choix explicite (Non par défaut). Les mots de passe protégés par un mot de passe maître FileZilla ne peuvent pas être déchiffrés : un avertissement demande de les saisir à nouveau. Les protocoles incompatibles, profils invalides et secrets illisibles sont signalés dans le bilan de l’import. Les secrets des fichiers JSON sont ignorés ; les exports ne contiennent aucun secret.
- Le **Journal de connexion** s’affiche automatiquement dès une tentative, avec horodatage et défilement automatique. Il reçoit les étapes en temps réel depuis les workers : ouverture TCP, authentification, lecture du dossier et résultat. En FTP/FTPS, il montre les commandes et les réponses réelles du serveur (220, 331, 230, 530…), ainsi que la négociation TLS. En SFTP, il montre la négociation SSH, la vérification de la clé, la méthode d’authentification et l’ouverture du sous-système SFTP. Les commandes PASS/ACCT/ADAT et les secrets connus sont masqués avant de quitter le worker ; le mode debug brut de ftplib n’est pas activé.
- Les erreurs réseau apparaissent directement dans le journal et la barre d’état, sans boîte de dialogue bloquante : explication, type, protocole, hôte, port et réponse réelle du serveur. Les refus FTP (530, 550, 425…), erreurs DNS, connexion refusée, coupures, délais, négociation SSH/TLS et problèmes de clé sont distingués. Le serveur peut donner une réponse vague : l’application conserve cette réponse sans inventer une cause plus précise. Les erreurs d’import ou d’enregistrement conservent une boîte de dialogue détaillée. Les connexions de transfert ont aussi leurs événements dans le journal, et leurs erreurs dans l’infobulle du statut. La confirmation d’une clé SSH inconnue reste obligatoire.
- **Préférences** configure dossiers de départ et temporaire, concurrence, conflits et la **vérification par empreinte** (activée par défaut). Après chaque fichier envoyé ou reçu, le serveur calcule l’empreinte du fichier (commande `HASH` ou `XSHA256`/`XSHA1`/`XMD5` en FTP, `sha256sum`/`shasum`/`sha1sum`/`md5sum` en SFTP) et elle est comparée à la copie locale. Un écart fait échouer le transfert : à l’envoi, le fichier distant d’origine est conservé et le temporaire supprimé ; au téléchargement, le fichier local d’origine est conservé. Un serveur qui ne calcule pas d’empreinte (compte SFTP limité à `internal-sftp`, serveur FTP sans extension) est signalé une fois dans le journal, et seule la taille est vérifiée. **Apparence** alterne les thèmes clair / sombre.
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
  workers/terminal.py       session SSH interactive (shell) dans son propre thread
  workers/search.py         recherche récursive annulable
  ui/main_window.py         espace de travail et coordination
  ui/explorer.py            panneaux, navigation, recherche, drag & drop
  ui/terminal.py            terminal SSH (écran pyte), onglets de sessions
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

Les tests démarrent des serveurs FTP et SSH/SFTP exclusivement sur `127.0.0.1`, dans des dossiers temporaires. Ils couvrent navigation, certificats TLS (configuration du client), empreinte SSH inconnue / mémorisée / modifiée, transferts FTP et SFTP avec reprise, reprise de dossier, vérification par empreinte, copie récursive, suppression, annulation, plan de synchronisation, mode miroir et widgets. FTPS explicite et implicite sont testés de bout en bout avec un vrai serveur TLS local et un certificat de test. Les tests ne contactent aucun serveur de production.

## Exécutable Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Distribution github
```

`-Distribution` choisit le canal : `github` (par défaut) et `uptodown` gardent **Aide → Rechercher les mises à jour** ; `store` le retire, car le Microsoft Store met lui-même à jour les applications qu’il installe.

Le build désactive UPX (souvent pris pour un virus par les antivirus), retire les bibliothèques Qt inutilisées (QML, Quick, PDF, clavier virtuel, OpenGL logiciel : environ 38 Mo), écrit les informations de version de l’exécutable, puis lance `SMFTP.exe --smoke-test` : une bibliothèque manquante fait échouer le build.

Résultat : `dist/SMFTP/SMFTP.exe`, avec les licences dans `dist/SMFTP/licenses`. Distribuer **tout le dossier**. Le mode `--onedir` est volontaire : Qt, PySide6 et paramiko (LGPL) restent des fichiers séparés et remplaçables. Ne pas le remplacer par `--onefile` pour une distribution publique. Compiler séparément sur chaque OS ; sous Linux / macOS, le séparateur de `--add-data` est `:`.

## Publication

`package.ps1` construit une version prête à publier :

```powershell
# Uptodown / GitHub : installeur Inno Setup 6 et archive portable, signés si un certificat est fourni
.\package.ps1 -Target uptodown -CertificateThumbprint <empreinte du certificat de signature de code>

# Microsoft Store : paquet MSIX (signé par le Store) ; valeurs de Partner Center > Identité du produit
.\package.ps1 -Target store -IdentityName <Package/Identity/Name> -Publisher "CN=<…>" -PublisherDisplayName <nom d’éditeur>
```

- L’installeur ([installer/smftp.iss](installer/smftp.iss)) installe par utilisateur, sans droits administrateur, et conserve profils et mots de passe à la désinstallation. Il nécessite Inno Setup 6 (`winget install JRSoftware.InnoSetup`).
- Sans signature, Windows SmartScreen avertit au premier lancement de l’installeur Uptodown / GitHub. Le paquet MSIX n’a pas besoin de signature : le Store le signe.
- Politique de confidentialité exigée par le Store : [docs/CONFIDENTIALITE.md](docs/CONFIDENTIALITE.md). Indiquer son adresse publique dans Partner Center.
- Langue à déclarer sur les fiches : français uniquement.

## Licence

SMFTP est distribué sous la **[PolyForm Noncommercial License 1.0.0](LICENSE.md)** : chacun peut utiliser, étudier, modifier et redistribuer le code pour tout usage **non commercial** (usage personnel, apprentissage, recherche, associations, établissements d’enseignement, organismes publics…). **L’usage commercial nécessite une autorisation** : ouvrez une *issue* sur le dépôt pour en faire la demande.

Ce n’est pas une licence « open source » au sens de l’OSI, car l’usage commercial est restreint ; le code reste public et ouvert aux contributions. Voir [CONTRIBUTING.md](CONTRIBUTING.md) pour contribuer, et [THIRD-PARTY-LICENSES.md](THIRD-PARTY-LICENSES.md) pour les composants tiers, qui gardent leur propre licence.

## Limites et suites prévues

Cette version est une base fonctionnelle, pas encore un remplacement complet de FileZilla. Restent notamment le choix d’autres langues pour toute l’application, les animations, et la recherche dans le contenu des fichiers. Le français est la langue de l’application et le catalogue Qt français est fourni ; les autres catalogues applicatifs ne le sont pas.

Le dépôt sur dossier fonctionne dans les deux vues, mais la surbrillance avancée de la cible reste à améliorer. Le glisser externe requiert une préparation préalable, décrite plus haut. Le double-clic distant ouvre l’éditeur ou l’aperçu ; sa coloration est générique et ne remplace pas un IDE.

Un repli LIST prend en charge les formats Unix (y compris sans propriétaire ni groupe, mois en français, dates ISO) et Windows/IIS si MLSD est indisponible. Une ligne incomprise est ignorée et signalée dans le journal ; un format entièrement inconnu est refusé. `LIST -a` est essayé pour afficher les fichiers cachés (`.htaccess`). Les dates LIST sans fuseau sont approximatives. Les téléchargements passent par un fichier local caché `.nom.smftp-….part`, publié par renommage une fois la taille vérifiée. La reprise ne réutilise que le partiel lié au même chemin, à la même taille et à la même date distantes ; un fichier local existant n’est jamais considéré comme un partiel. Un partiel corrompu est détecté par la vérification d’empreinte quand le serveur la permet ; le partiel est alors supprimé et la relance repart de zéro. Lors de la reprise d’un dossier (relance ou file restaurée), les fichiers déjà terminés sont ignorés si la source et la destination n’ont pas changé depuis ; leur liste est conservée dans `queue.json`. La détection de modifications distantes à l’édition utilise date/taille et ne garantit pas l’absence de changement entre vérification et publication. Les garanties de remplacement dépendent des capacités du serveur, comme indiqué plus haut. Les connexions de données FTPS réutilisent la session TLS du canal de contrôle, comme l’exigent vsftpd (`require_ssl_reuse`), FileZilla Server et ProFTPD.

Le catalogue Qt compilé local n’est pas versionné : l’application cherche aussi les traductions fournies par l’installation Qt/PySide6. Leur disponibilité et leur licence doivent être vérifiées pour une distribution binaire.
