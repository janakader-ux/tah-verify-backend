"""Build only public pages for Netlify; keep backend files out of the publish folder."""
from pathlib import Path
from shutil import copyfile, copytree, rmtree

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist"


def main():
    # Read the source first so a missing page fails before clearing old output.
    page = (ROOT / "apply.html").read_text(encoding="utf-8")
    if OUTPUT.exists():
        rmtree(OUTPUT)
    OUTPUT.mkdir()
    # Serve the form directly at /, retaining query parameters without redirects.
    (OUTPUT / "index.html").write_text(page, encoding="utf-8")
    copyfile(ROOT / "apply.html", OUTPUT / "apply.html")
    copytree(ROOT / "assets", OUTPUT / "assets")
    print("Built dist/index.html, dist/apply.html and assets")


if __name__ == "__main__":
    main()
