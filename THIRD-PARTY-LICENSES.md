# Composants tiers

SMFTP est distribué sous la [PolyForm Noncommercial License 1.0.0](LICENSE.md). Les composants ci-dessous restent sous leur propre licence ; cette dernière n’est pas modifiée par celle de SMFTP. L’exécutable Windows contient leurs textes de licence dans le dossier `licenses/`.

| Composant | Licence | Rôle |
|---|---|---|
| Qt for Python : PySide6, PySide6_Essentials, PySide6_Addons, shiboken6 | LGPL-3.0-only (ou GPL-2.0/3.0) | Interface graphique |
| Qt (bibliothèques embarquées par PySide6) | LGPL-3.0-only | Interface graphique |
| paramiko | LGPL-2.1 | SFTP / SSH |
| cryptography | Apache-2.0 ou BSD-3-Clause | Cryptographie (paramiko) |
| bcrypt | Apache-2.0 | Clés SSH (paramiko) |
| PyNaCl, libsodium | Apache-2.0, ISC | Cryptographie (paramiko) |
| cffi | MIT | Liaison C |
| pycparser | BSD-3-Clause | Dépendance de cffi |
| invoke | BSD-2-Clause | Dépendance de paramiko |
| keyring, jaraco.classes, jaraco.context, jaraco.functools, more-itertools | MIT | Coffre-fort système |
| pywin32-ctypes | BSD-3-Clause | Coffre-fort Windows |
| typing_extensions | PSF-2.0 | Compatibilité Python |
| Python (interpréteur embarqué par PyInstaller) | PSF-2.0 | Exécution |
| PyInstaller (chargeur de l’exécutable) | GPL-2.0 avec exception pour le chargeur | Construction uniquement |

## Qt, PySide6 et paramiko (LGPL)

Ces bibliothèques sont utilisées sans modification et restent des fichiers séparés dans le dossier de l’exécutable (`dist/SMFTP/_internal`). Vous pouvez les remplacer par une autre version compatible. Leur code source est disponible auprès de leurs projets :

- Qt for Python : <https://code.qt.io/cgit/pyside/pyside-setup.git/> et <https://download.qt.io/official_releases/QtForPython/>
- Qt : <https://download.qt.io/official_releases/qt/>
- paramiko : <https://github.com/paramiko/paramiko>

Les textes de la LGPL v3 et de la GPL v3, à laquelle la LGPL v3 renvoie, sont fournis dans [`licenses/`](licenses/). La LGPL v2.1 de paramiko est copiée depuis son paquet lors de la construction.
