# Contribuer à SMFTP

Merci de votre intérêt ! Tout le monde peut proposer des corrections, des fonctions, des traductions ou de la documentation.

## Comment proposer une contribution

1. Ouvrez d’abord une *issue* pour un changement important, afin d’en discuter avant d’écrire le code.
2. Faites un *fork*, puis créez une branche à partir de `main`.
3. Installez l’environnement et lancez les tests :

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   $env:QT_QPA_PLATFORM = "offscreen"
   .\.venv\Scripts\python.exe -m pytest tests -q
   ```

4. Ajoutez des tests pour tout comportement corrigé ou nouveau. Les tests ne doivent contacter aucun serveur réel : utilisez les serveurs locaux fournis par `tests/`.
5. L’interface est en français. Ne mettez jamais de mot de passe, clé ou nom de serveur réel dans le code, les tests ou les captures.
6. Ouvrez une *pull request* qui explique le problème, la solution et la façon de la vérifier, et cochez la case d’acceptation des conditions ci-dessous.

## Licence du projet

SMFTP est distribué sous la [PolyForm Noncommercial License 1.0.0](LICENSE.md) : son code est disponible, modifiable et redistribuable pour tout usage **non commercial**. L’usage commercial nécessite une autorisation du mainteneur (contact : ouvrez une *issue* sur le dépôt GitHub).

## Conditions applicables aux contributions

Pour que le projet puisse rester cohérent et accorder si besoin des autorisations commerciales, chaque contribution est soumise aux conditions suivantes. En soumettant une contribution (code, documentation, traduction, ressource graphique ou autre) à ce dépôt, vous déclarez et acceptez que :

1. vous êtes l’auteur de la contribution, ou vous avez le droit de la soumettre dans ces conditions, et elle ne contient pas de code soumis à une licence incompatible ;
2. votre contribution est publiée dans ce dépôt sous la PolyForm Noncommercial License 1.0.0 ;
3. vous accordez à Smeth, mainteneur de SMFTP, une licence mondiale, non exclusive, gratuite, perpétuelle et irrévocable, avec droit de sous-licence et de transfert, pour utiliser, reproduire, modifier, distribuer et publier votre contribution, y compris sous d’autres licences, commerciales ou non ;
4. vous conservez la propriété de votre contribution et restez libre de l’utiliser ailleurs.

Si vous contribuez dans le cadre de votre emploi, assurez-vous que votre employeur vous autorise à accepter ces conditions.

### English summary (for non-French speakers)

By submitting a contribution to this repository, you confirm that you have the right to submit it, that it is published here under the PolyForm Noncommercial License 1.0.0, and you grant Smeth, the maintainer of SMFTP, a worldwide, non-exclusive, royalty-free, perpetual and irrevocable license, with the right to sublicense and transfer, to use, reproduce, modify, distribute and publish your contribution, including under other licenses, commercial or not. You keep the ownership of your contribution. The French text above prevails.
