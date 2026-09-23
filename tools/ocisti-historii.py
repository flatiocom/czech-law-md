"""Odstraní jména soudců ze všech verzí metadat judikatury v git historii.

Smazat pole v novém commitu nestačí: ve veřejném repozitáři si kdokoli vytáhne starší verzi
souboru z historie. Tohle přepíše i ty staré verze.

Přepis historie je nevratný a mění SHA všech commitů, takže po něm musí následovat
`git push --force`. Skript na konci sám ověří, že v historii nezůstal ani jeden záznam se
jménem, a když zůstane, skončí nenulovým kódem.

    python3 tools/ocisti-historii.py --zkouska   # jen spočítá, čeho by se to týkalo
    python3 tools/ocisti-historii.py             # přepíše historii
"""

from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import sys
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
OSOBNI_UDAJE = ("autor",)
GZIP = b"\x1f\x8b"


def bloby_metadat() -> list[str]:
    beh = subprocess.run(["git", "rev-list", "--objects", "--all"],
                         cwd=KOREN, capture_output=True, text=True, timeout=900, check=True)
    return [radek.split()[0] for radek in beh.stdout.splitlines()
            if len(radek.split()) > 1 and "judikatura/metadata" in radek and radek.rstrip().endswith(".gz")]


def zasazene() -> tuple[int, int]:
    """Kolik blobů historie nese jméno soudce a kolik jich je celkem."""
    bloby = bloby_metadat()
    spatne = 0
    for b in bloby:
        data = subprocess.run(["git", "cat-file", "blob", b],
                              cwd=KOREN, capture_output=True, timeout=300).stdout
        try:
            text = gzip.decompress(data).decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(radek.strip() and any(k in json.loads(radek) for k in OSOBNI_UDAJE)
               for radek in text.split("\n")):
            spatne += 1
    return spatne, len(bloby)


CALLBACK = f'''
import gzip, json
if blob.data[:2] == {GZIP!r}:
    try:
        text = gzip.decompress(blob.data).decode("utf-8")
    except Exception:
        text = None
    if text is not None:
        radky, zmena = [], False
        for radek in text.split("\\n"):
            if not radek.strip():
                continue
            z = json.loads(radek)
            if any(k in z for k in {OSOBNI_UDAJE!r}):
                z = {{k: v for k, v in z.items() if k not in {OSOBNI_UDAJE!r}}}
                zmena = True
            radky.append(json.dumps(z, ensure_ascii=False))
        if zmena:
            blob.data = gzip.compress(("\\n".join(radky) + "\\n").encode("utf-8"), mtime=0)
'''


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--zkouska", action="store_true", help="jen spočítat, nepřepisovat")
    args = p.parse_args()

    spatne, celkem = zasazene()
    print(f"  blobů metadat v historii: {celkem}, z toho se jménem soudce: {spatne}")
    if args.zkouska:
        return 0
    if not spatne:
        print("  historie je čistá, není co dělat")
        return 0

    # Remote si filter-repo odstraní sám (chrání před nechtěným pushem), tak se zapamatuje.
    beh = subprocess.run(["git", "remote", "get-url", "origin"],
                         cwd=KOREN, capture_output=True, text=True, timeout=60)
    origin = beh.stdout.strip()

    nastroj = Path.home() / ".local" / "bin" / "git-filter-repo"
    prikaz = [str(nastroj) if nastroj.exists() else "git-filter-repo",
              "--force", "--blob-callback", CALLBACK]
    print("  přepisuji historii …", flush=True)
    beh = subprocess.run(prikaz, cwd=KOREN, timeout=7200)
    if beh.returncode != 0:
        print(f"  filter-repo skončil s kódem {beh.returncode}")
        return 1

    if origin:
        subprocess.run(["git", "remote", "add", "origin", origin],
                       cwd=KOREN, capture_output=True, timeout=60)
        print(f"  remote origin vrácen: {origin}")

    spatne_po, celkem_po = zasazene()
    print(f"  po přepisu: blobů {celkem_po}, se jménem {spatne_po}")
    if spatne_po:
        print("  NEPOVEDLO SE — jména v historii zůstala")
        return 1
    print("  historie je čistá; teď `git push --force origin main`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
