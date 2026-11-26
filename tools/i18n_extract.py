"""Collect SMFTP's translatable French texts and update the English catalog.

Usage:
  python tools/i18n_extract.py                 update resources/translations/smftp_en.json
  python tools/i18n_extract.py --check         exit 1 if a text has no English translation
  python tools/i18n_extract.py --add FILE.json  also add the texts listed in FILE.json (texts
                                               the application asked for at run time, see
                                               CatalogTranslator.missing)

Collected: every string literal passed to tr() / self.tr() / translate(), and
the literals of lists or dicts that are translated later through a variable
(menu titles…), recognised as French text. Existing translations are kept; new
texts get an empty value to fill in. A translated text stays in the catalog as long as
the same string literal exists somewhere in the code (texts found at run time); it is
dropped once no literal of the code matches it.
"""
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "resources" / "translations" / "smftp_en.json"
SOURCES = [ROOT / "main.py", *sorted((ROOT / "smftp").rglob("*.py"))]
TRANSLATING = {"tr", "translate", "N_"}
# French-looking text: an accent, a French quote or apostrophe, or a common French word.
FRENCH = re.compile(r"[éèêëàâùûçôîïœÉÈÊÀÇÔ’«»]|\b(le|la|les|des|du|une|un|est|pas|vous|votre|vos|dans|pour|sur|avec|"
                    r"aucun|aucune|fichier|fichiers|dossier|dossiers|serveur|connexion|transfert|transferts|tâche|"
                    r"Annuler|Fermer|Ouvrir|Enregistrer|Supprimer|Renommer|Nouveau|Nouvelle|Copier|Coller|Couper|"
                    r"Ignorer|Écraser|Reprendre|Affichage|Outils|Aide|Serveurs|Édition|Quitter|Statut|Erreur)\b",
                    re.IGNORECASE)
# Model-facing text (system prompt, tool descriptions) stays in French: the model reads it.
SKIP_FILES = {"smftp/core/assistant.py"}


def translated_literals(tree):
    """Literals given directly to a translation call."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", getattr(node.func, "id", ""))
        if name not in TRANSLATING:
            continue
        # QCoreApplication.translate(context, text, …): the text is the second argument.
        arguments = node.args[1:2] if name == "translate" and len(node.args) >= 2 else node.args[:1]
        for argument in arguments:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                found.append(argument.value)
    return found


def container_literals(tree):
    """Every other French-looking literal (menu titles given to add_command, lists of labels,
    status values shown through self.tr(status)…), docstrings excluded. A few entries are
    never displayed: translating them costs nothing, missing one would leave French on screen."""
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
                and FRENCH.search(node.value)):
            found.append(node.value)
    return found


def all_literals():
    """Every string literal of the code: a catalog key matching one is still in use."""
    found = set()
    for path in SOURCES:
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                found.add(node.value)
    return found


def collect():
    texts = {}
    for path in SOURCES:
        relative = path.relative_to(ROOT).as_posix()
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        strict = translated_literals(tree)
        loose = [] if relative in SKIP_FILES else container_literals(tree)
        for text in strict + loose:
            if text.strip():
                texts.setdefault(text, relative)
    return texts


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # French and arrows on a Windows console.
    texts = collect()
    catalog = json.loads(CATALOG.read_text(encoding="utf-8")) if CATALOG.exists() else {}
    literals = all_literals()
    keys = set(texts) | {key for key in catalog if key in literals}
    if "--add" in sys.argv:
        added = json.loads(Path(sys.argv[sys.argv.index("--add") + 1]).read_text(encoding="utf-8"))
        keys |= {text for text in added if isinstance(text, str) and text in literals}
    updated = {text: catalog.get(text, "") for text in sorted(keys, key=str.casefold)}
    missing = [text for text, value in updated.items() if not value]
    if "--check" in sys.argv:
        for text in missing:
            print(f"Sans traduction ({texts.get(text, 'exécution')}) : {text!r}")
        sys.exit(1 if missing else 0)
    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    CATALOG.write_text(json.dumps(updated, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(updated)} textes, {len(missing)} à traduire, {len(set(catalog) - set(updated))} retirés.")


if __name__ == "__main__":
    main()
