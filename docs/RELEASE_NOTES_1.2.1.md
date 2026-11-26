## SMFTP 1.2.1

**Français** · [English below](#english)

### Connexion rapide

- La barre de connexion rapide propose maintenant le choix du protocole : **FTP** (par défaut), FTPS explicite, FTPS implicite, SFTP ou SSH (terminal).
- Une adresse complète (`sftp://…`, `ftps://…`) règle d’elle-même le protocole ; le port « auto » suit le protocole choisi (21, 990 ou 22).
- En FTP sans utilisateur, la connexion se fait en `anonymous`.

**Mise à jour** : depuis SMFTP 1.2.0 installé avec l’installeur, **Aide → Rechercher les mises à jour… → Télécharger et installer** ; vos profils, mots de passe et votre file de transferts sont conservés.

**Fichiers** : `SMFTP-1.2.1-setup-x64.exe` (installeur, recommandé), `SMFTP-1.2.1-windows-x64.zip` (version portable), `SHA256SUMS.txt` (empreintes).

L’installeur et l’exécutable ne sont pas encore signés : au premier lancement, Windows SmartScreen peut afficher « Windows a protégé votre ordinateur » → **Informations complémentaires** → **Exécuter quand même**.

---

<a id="english"></a>

## English

### Quick connect

- The quick connect bar now lets you choose the protocol: **FTP** (default), explicit FTPS, implicit FTPS, SFTP or SSH (terminal).
- A full address (`sftp://…`, `ftps://…`) sets the protocol by itself; the "auto" port follows the chosen protocol (21, 990 or 22).
- On FTP without a user name, SMFTP logs in as `anonymous`.

**Updating**: from SMFTP 1.2.0 installed with the installer, **Help → Check for updates… → Download and install**; your profiles, passwords and transfer queue are kept.

**Files**: `SMFTP-1.2.1-setup-x64.exe` (installer, recommended), `SMFTP-1.2.1-windows-x64.zip` (portable), `SHA256SUMS.txt` (fingerprints).

The installer and executable are not code-signed yet: on first launch, Windows SmartScreen may show "Windows protected your PC" → **More info** → **Run anyway**.
