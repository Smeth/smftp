## SMFTP 1.2.0

**Français** · [English below](#english)

### Mises à jour intégrées
- Vérification automatique au démarrage, une fois par jour au plus, en arrière-plan (désactivable dans les Préférences). Une nouvelle version est signalée dans la barre d’état et le menu Aide, sans fenêtre bloquante.
- **Télécharger et installer** : l’installeur est téléchargé depuis cette page de GitHub, son empreinte SHA-256 est vérifiée contre `SHA256SUMS.txt`, puis SMFTP se ferme proprement (file de transferts conservée) et lance l’installation. « Ignorer cette version » et « Plus tard » sont proposés.
- L’installeur met à jour une installation existante : il attend la fermeture de SMFTP et ne touche jamais à vos profils, mots de passe ni à votre file de transferts.

### Connexions et serveurs
- **Onglets multi-serveurs** : plusieurs serveurs ouverts en même temps, chacun dans son onglet ; double-cliquer sur un serveur déjà ouvert affiche son onglet.
- **Connexion rapide** : barre sous le menu (hôte, utilisateur, mot de passe, port ou adresse complète `sftp://…`), sans créer de profil.
- **Signets** (menu Signets, Ctrl+D) : une paire dossier local / dossier distant par signet, pour chaque serveur.
- **Proxy** par profil : SOCKS5 (avec ou sans identifiant) ou HTTP CONNECT, pour FTP, FTPS, SFTP et SSH ; mot de passe du proxy dans le coffre-fort Windows.
- **Clés PuTTY `.ppk`** (formats 2 et 3, chiffrées ou non : RSA, ECDSA, Ed25519) et **agent SSH** (Pageant ou OpenSSH) par profil.
- **Fuseau horaire des serveurs FTP** détecté automatiquement (ou choisi dans le profil) pour des dates justes sans MLSD.
- **Navigation synchronisée** des panneaux local et distant ; **permissions récursives** (un mode pour les dossiers, un pour les fichiers).
- Reprise automatique d'un transfert quand le serveur a coupé la connexion pendant une pause.

### Corrections importantes depuis la 1.1.0
- Les envois FTP échouaient sur vsftpd et les serveurs sans MLSD : corrigé (`LIST -a`, vérification par SIZE/MDTM).
- Les transferts repris ou relancés pouvaient écraser un fichier existant sans demander : la règle de conflit s'applique toujours.
- Un fichier exécutable distant (`.bat`, `.exe`, `.lnk`…) n'est plus jamais lancé par « Ouvrir dans l'éditeur système ».
- Un fichier de profils abîmé n'est plus vidé, un fichier de tâches mal formé ne bloque plus le démarrage, la console FTP fonctionne.
- Les fichiers téléchargés reçoivent la marque Windows « provenant d'Internet » (désactivable).

### Interface en anglais
- SMFTP est disponible en français et en anglais (langue de Windows par défaut, réglable dans les Préférences).
- Révision de toutes les fenêtres : mise en page, lisibilité, textes manquants.

### Transferts et synchronisation
- Conflits plus clairs (taille et date des deux fichiers), colonnes Taille et Destination dans la file, envois de dossiers plus rapides.
- Synchronisation miroir plus prudente (liens, jonctions, noms ne différant que par la casse, listes illisibles), suppressions seulement après toutes les copies.
- Listes FTP tolérantes (formats IIS, dates ISO, mois en français/allemand), réutilisation de session FTPS.
- L’éditeur ouvre et réenregistre les fichiers Windows-1252 / ISO-8859-1.

### Confort
- Zone de notification et démarrage avec Windows pour les tâches planifiées.
- Thèmes clair et sombre complets, thème système au premier lancement.
- Fenêtre adaptée à l’écran, disposition mémorisée, raccourcis clavier, menus Alt+lettre, aide complète.
- Annulation d’une connexion en cours ; relancer SMFTP ramène la fenêtre ouverte.

### Sécurité
- Liens de l’assistant limités à https, collage du terminal filtré, consentement pour un serveur Ollama distant.
- Chemins réseau, TLS FileZilla, phrase secrète des clés SSH ; nettoyage des dossiers temporaires après un plantage.

**Fichiers** : `SMFTP-1.2.0-setup-x64.exe` (installeur, recommandé), `SMFTP-1.2.0-windows-x64.zip` (version portable), `SHA256SUMS.txt` (empreintes).

L'installeur et l'exécutable ne sont pas encore signés : au premier lancement, Windows SmartScreen peut afficher « Windows a protégé votre ordinateur » → **Informations complémentaires** → **Exécuter quand même**. Les empreintes de `SHA256SUMS.txt` permettent de vérifier les fichiers.

---

<a id="english"></a>
## English

### Built-in updates
- Automatic check at startup, at most once a day, in the background (can be turned off in Preferences). A new version is shown in the status bar and the Help menu, with no blocking window.
- **Download and install**: the installer is downloaded from this GitHub page, its SHA-256 fingerprint is checked against `SHA256SUMS.txt`, then SMFTP closes cleanly (transfer queue kept) and starts the installation. “Skip this version” and “Later” are offered.
- The installer updates an existing installation: it waits for SMFTP to close and never touches your profiles, passwords or transfer queue.

### Connections and servers
- **Multi-server tabs**: several servers open at the same time, each in its own tab; double-clicking a server that is already open shows its tab.
- **Quick connect**: a bar under the menu (host, user, password, port, or a full `sftp://…` address), without creating a profile.
- **Bookmarks** (Bookmarks menu, Ctrl+D): a local folder / remote folder pair per bookmark, for each server.
- **Proxy** per profile: SOCKS5 (with or without credentials) or HTTP CONNECT, for FTP, FTPS, SFTP and SSH; the proxy password is kept in the Windows Credential Manager.
- **PuTTY `.ppk` keys** (formats 2 and 3, encrypted or not: RSA, ECDSA, Ed25519) and **SSH agent** (Pageant or OpenSSH) per profile.
- **FTP server time zone** detected automatically (or set in the profile) for correct dates without MLSD.
- **Synchronized browsing** of the local and remote panels; **recursive permissions** (one mode for folders, one for files).
- A transfer resumes automatically when the server closed the connection during a pause.

### Important fixes since 1.1.0
- FTP uploads failed on vsftpd and servers without MLSD: fixed (`LIST -a`, SIZE/MDTM checks).
- Resumed or retried transfers could overwrite an existing file without asking: the conflict rule now always applies.
- A remote executable file (`.bat`, `.exe`, `.lnk`…) is never launched by "Open in system editor".
- A damaged profiles file is no longer emptied, a malformed tasks file no longer blocks startup, the FTP console works.
- Downloaded files get the Windows "downloaded from the Internet" mark (can be turned off).

### English interface
- SMFTP is available in French and English (Windows display language by default, can be changed in Preferences).
- Every window reviewed: layout, readability, missing texts.

### Transfers and synchronization
- Clearer conflicts (size and date of both files), Size and Destination columns in the queue, faster folder uploads.
- Safer mirror synchronization (links, junctions, names differing only by case, unreadable listings); deletions only after every copy.
- Tolerant FTP listings (IIS formats, ISO dates, French/German month names), FTPS session reuse.
- The editor opens and saves Windows-1252 / ISO-8859-1 files.

### Comfort
- Notification area and start with Windows for scheduled tasks.
- Complete light and dark themes, system theme on first launch.
- Window fits the screen, remembered layout, keyboard shortcuts, Alt+letter menus, full help.
- A connection in progress can be cancelled; starting SMFTP again brings the open window to the front.

### Security
- Assistant links limited to https, filtered terminal paste, consent for a remote Ollama server.
- Network paths, FileZilla TLS, SSH key passphrase; temporary folders cleaned after a crash.

**Files**: `SMFTP-1.2.0-setup-x64.exe` (installer, recommended), `SMFTP-1.2.0-windows-x64.zip` (portable), `SHA256SUMS.txt` (fingerprints).

The installer and executable are not code-signed yet: on first launch, Windows SmartScreen may show "Windows protected your PC" → **More info** → **Run anyway**. The fingerprints in `SHA256SUMS.txt` let you check the files.
