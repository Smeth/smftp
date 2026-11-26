## SMFTP 1.2.0

**Français** · [English below](#english)

### Mises à jour intégrées
- Vérification automatique au démarrage, une fois par jour au plus, en arrière-plan (désactivable dans les Préférences). Une nouvelle version est signalée dans la barre d’état et le menu Aide, sans fenêtre bloquante.
- **Télécharger et installer** : l’installeur est téléchargé depuis cette page de GitHub, son empreinte SHA-256 est vérifiée contre `SHA256SUMS.txt`, puis SMFTP se ferme proprement (file de transferts conservée) et lance l’installation. « Ignorer cette version » et « Plus tard » sont proposés.
- L’installeur met à jour une installation existante : il attend la fermeture de SMFTP et ne touche jamais à vos profils, mots de passe ni à votre file de transferts.

### Connexions et serveurs
- Connexions via un proxy.
- Clés PuTTY `.ppk` et agent SSH.
- Connexion rapide et signets.
- Onglets multi-serveurs : plusieurs serveurs ouverts en même temps.
- Navigation synchronisée des panneaux local et distant ; permissions appliquées récursivement.

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

---

<a id="english"></a>
## English

### Built-in updates
- Automatic check at startup, at most once a day, in the background (can be turned off in Preferences). A new version is shown in the status bar and the Help menu, with no blocking window.
- **Download and install**: the installer is downloaded from this GitHub page, its SHA-256 fingerprint is checked against `SHA256SUMS.txt`, then SMFTP closes cleanly (transfer queue kept) and starts the installation. “Skip this version” and “Later” are offered.
- The installer updates an existing installation: it waits for SMFTP to close and never touches your profiles, passwords or transfer queue.

### Connections and servers
- Connections through a proxy.
- PuTTY `.ppk` keys and SSH agent.
- Quick connect and bookmarks.
- Multi-server tabs: several servers open at the same time.
- Synchronized browsing of the local and remote panels; recursive permissions.

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
