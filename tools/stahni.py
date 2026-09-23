"""Stáhne z e-Sbírky dnes platné znění právních předpisů a uloží je jako markdown do `zakony/`.

Platné, ne poslední: vyhlášená novela s pozdější účinností má v e-Sbírce vlastní znění, a kdyby
se bralo to, agent by citoval text, který ještě neplatí (v září 2026 u 255 předpisů, mezi nimi
zákoník práce). Budoucí znění se jen ohlásí ve frontmatteru a nad textem.

e-Sbírka nabízí každý předpis jako jeden XML soubor se strukturou i textem, takže se nic neskládá
z velkých datasetů — ty slouží jen k tomu zjistit, co stahovat a co se od minule změnilo.

Tři požadavky na předpis:

    GET /sbr-externi/stahni/informativni-zneni/{dokumentId}/XML  -> {pozadavekId, id}
    GET /souborove-sluzby/verejne-pozadavky-dokumenty/pozadavky/{pozadavekId}  -> {stav: OK}
    GET /souborove-sluzby/soubory/{id}  -> XML

Běh je přerušitelný: co je hotové, zůstane, a další spuštění pokračuje tam, kde skončilo. Stav si
drží `stav.json`, aby se po výpadku nezačínalo od nuly.

    python3 tools/stahni.py --od-roku 1990        # co ještě není, nebo se změnilo
    python3 tools/stahni.py --akt 89/2012         # jeden předpis
    python3 tools/stahni.py --od-roku 1990 --limit 20   # zkušební dávka
"""

from __future__ import annotations

import argparse
import gzip
import http.client
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prevod import preved  # noqa: E402

KOREN = Path(__file__).resolve().parent.parent
CACHE = KOREN / ".cache"
ZAKONY = KOREN / "zakony"
SMLOUVY = KOREN / "smlouvy"
STAV = KOREN / ".cache" / "stav.json"
# Nestažené předpisy zůstávají ve starém znění; bez seznamu se to ztratí v logu.
NESTAZENE_SOUBOR = KOREN / ".cache" / "nestazene.json"
NESTAZENE: dict[str, str] = {}

API = "https://e-sbirka.gov.cz"
DATA = "https://opendata.eselpoint.gov.cz/datove-sady-esbirka"
TIMEOUT = 120

# Souběh drží server MV v pohodě a zároveň to není přes noc věčnost. Pauza mezi požadavky je
# schválně, ne omylem — jde o cizí veřejnou službu, ne o náš stroj.
SOUBEH = 4
PAUZA = 0.15

# Velký předpis se na serveru generuje několik sekund; tolik čekání mu dáme, než to vzdáme.
CEKANI_POKUSU = 40
CEKANI_PAUZA = 1.5

tisk = threading.Lock()
# Stav zapisuje hlavní vlákno, ale mění ho všechna — bez zámku spadne json.dumps na
# "dictionary changed size during iteration" až po prvních stovkách předpisů.
zamek_stavu = threading.Lock()


def uloz_stav(stav: dict) -> None:
    with zamek_stavu:
        # Seřazeně a po řádcích, protože stav je v gitu: jinak by každá aktualizace
        # uložila celých 11 MB jako nový blob místo několika změněných řádek.
        obsah = json.dumps(stav, ensure_ascii=False, indent=1, sort_keys=True)
    STAV.write_text(obsah, encoding="utf-8")


# Datasety e-Sbírka přegenerovává denně. Cache bez stáří znamenala, že lokální aktualizace
# po prvním běhu už nikdy neviděla nic nového.
STARI_DATASETU = 20 * 3600


def stahni_dataset(jmeno: str, soubor: str) -> Path:
    cil = CACHE / soubor
    CACHE.mkdir(parents=True, exist_ok=True)
    if cil.exists() and time.time() - cil.stat().st_mtime < STARI_DATASETU:
        return cil
    with tisk:
        print(f"  stahuji {jmeno} …", flush=True)
    # Přes dočasný soubor: přerušené stažení by jinak nechalo useknutý gzip, který se tváří
    # jako platná cache.
    docasny = cil.with_suffix(cil.suffix + ".part")
    urllib.request.urlretrieve(f"{DATA}/{jmeno}", docasny)
    docasny.replace(cil)
    return cil


def vyber_zneni(zneni: list[dict], posledni: str, dnes: str) -> tuple[dict | None, dict | None]:
    """Vrací (dnes platné znění, budoucí znění nebo None).

    Datum účinnosti je poslední kus IRI: esel-esb:eli/cz/sb/2006/262/2027-01-01. Předpis, který
    ještě vůbec neplatí, žádné platné znění nemá — pak se vezme to nejbližší, ať v repozitáři je."""
    s_textem = [z for z in zneni if z.get("iri") and z.get("znění-dokument-id")]
    od = lambda z: z["iri"].rsplit("/", 1)[-1]  # noqa: E731
    platna = [z for z in s_textem if od(z) <= dnes]
    budouci = [z for z in s_textem if od(z) > dnes]
    if platna:
        platne = max(platna, key=od)
        return platne, (min(budouci, key=od) if budouci else None)
    if budouci:
        return min(budouci, key=od), None
    return next((z for z in s_textem if z["iri"] == posledni), None), None


def json_get(url: str, pokusy: int = 3) -> dict:
    for pokus in range(pokusy):
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
            if pokus == pokusy - 1:
                raise
            time.sleep(2 ** pokus)
    return {}


def stahni_xml(dokument_id: str) -> str:
    """Tři kroky e-Sbírky: požádat o XML, ověřit stav, stáhnout soubor.

    Vyhazuje ValueError s důvodem, aby se v hromadném běhu poznalo, jestli předpis jen nemá XML,
    nebo jestli server odmítá kvůli tempu.
    """
    odpoved = json_get(f"{API}/sbr-externi/stahni/informativni-zneni/{dokument_id}/XML")
    if "chyby" in odpoved:
        raise ValueError(odpoved["chyby"][0].get("kod", "chyba"))
    if "pozadavekId" not in odpoved:
        raise ValueError(f"bez požadavku: {str(odpoved)[:80]}")

    # Většina předpisů se vygeneruje hned a přijde rovnou s id; u velkých vrací server PROBIHA
    # a soubor je hotový až za několik sekund.
    soubor_id = odpoved.get("id")
    if odpoved.get("stavPozadavku") != "OK" or not soubor_id:
        for _ in range(CEKANI_POKUSU):
            time.sleep(CEKANI_PAUZA)
            stav = json_get(f"{API}/souborove-sluzby/verejne-pozadavky-dokumenty/pozadavky/{odpoved['pozadavekId']}")
            if stav.get("stav") == "OK" and stav.get("id"):
                soubor_id = stav["id"]
                break
            if stav.get("stav") not in ("PROBIHA", "CEKA", None):
                raise ValueError(f"stav {stav.get('stav')}")
        else:
            raise ValueError("generování se nedokončilo")

    # Poslední krok padal na RemoteDisconnected — server občas zavře spojení bez odpovědi.
    # Bez opakování to znamená, že se předpis v celém běhu neaktualizuje; v přegenerování
    # korpusu takhle vypadlo 54 předpisů z 31 159.
    for pokus in range(3):
        try:
            with urllib.request.urlopen(f"{API}/souborove-sluzby/soubory/{soubor_id}", timeout=TIMEOUT) as r:
                return r.read().decode("utf-8")
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError):
            if pokus == 2:
                raise
            time.sleep(2 ** pokus)
    raise ValueError("soubor se nepodařilo stáhnout")


def nacti_akty(od_roku: int, sbirky: set[str] | None = None) -> list[dict]:
    """Ze základního datasetu vytáhne předpisy, jejich poslední znění a čas poslední změny.

    `sbirky` filtruje podle `akt-sbírka-kód`: `sb` je Sbírka zákonů, `sm` mezinárodní smlouvy,
    zbytek jsou starší a zvláštní řady.
    """
    cesta = stahni_dataset("002PravniAkt.json.gz", "002.json.gz")
    with gzip.open(cesta, "rt", encoding="utf-8") as f:
        polozky = json.load(f)["položky"]

    dnes = time.strftime("%Y-%m-%d")
    akty = []
    for a in polozky:
        rok = a.get("akt-rok-předpisu")
        if not isinstance(rok, int) or rok < od_roku:
            continue
        if sbirky and a.get("akt-sbírka-kód") not in sbirky:
            continue

        posledni = (a.get("právní-akt-znění-poslední") or {}).get("iri")
        if not posledni:
            continue

        zneni, budouci = vyber_zneni(a.get("právní-akt-znění", []), posledni, dnes)
        if not zneni:
            continue

        akty.append({
            "citace": a.get("akt-citace", ""),
            "rok": str(rok),
            "cislo": a.get("akt-číslo-předpisu", ""),
            "nazev": a.get("akt-název-vyhlášený", ""),
            "sbirka": a.get("akt-sbírka-kód", ""),
            "eli": zneni["iri"].replace("esel-esb:", "/"),
            "dokument_id": str(zneni["znění-dokument-id"]),
            "zmena": zneni.get("datum-čas-poslední-změny", ""),
            "ucinnost_od": zneni["iri"].rsplit("/", 1)[-1],
            "pristi_zneni_od": budouci["iri"].rsplit("/", 1)[-1] if budouci else "",
            "pristi_zneni_eli": budouci["iri"].replace("esel-esb:", "/") if budouci else "",
        })
    return akty


def soubor_pro(akt: dict) -> Path:
    # číslo bývá i s písmenem (např. "138a"), lomítko v něm být nemůže, ale jistota je jistota
    cislo = re.sub(r"[^\w-]", "", akt["cislo"])
    # Řady se číslují nezávisle, takže 1/2000 Sb. a 1/2000 Sb. m. s. jsou dva různé předpisy.
    # Bez rozlišení řady si přepíšou soubor — u smluv se to týká úplně všech.
    if akt.get("sbirka") == "sm":
        return SMLOUVY / akt["rok"] / f"{cislo}-{akt['rok']}-ms.md"
    return ZAKONY / akt["rok"] / f"{cislo}-{akt['rok']}.md"


def zpracuj(akt: dict, stav: dict, znovu: bool = False) -> str:
    """Vrátí 'novy', 'zmeneny', 'beze-zmeny' nebo 'chyba'."""
    klic = akt["citace"]
    cil = soubor_pro(akt)
    znamy = stav.get(klic)

    # Kromě změny textu se sleduje i to, které znění je platné a jestli se chystá další:
    # novela nabude účinnosti, aniž by se na staré znění sáhlo, a ohlášení budoucího znění
    # je ve frontmatteru.
    if (not znovu and znamy and cil.exists()
            and znamy.get("zmena") == akt["zmena"]
            and znamy.get("dokument_id") == akt["dokument_id"]
            and (znamy.get("pristi_zneni_od") or "") == akt["pristi_zneni_od"]):
        return "beze-zmeny"

    time.sleep(PAUZA)
    try:
        xml = stahni_xml(akt["dokument_id"])
        obsah = preved(xml, akt)
    except Exception as e:  # noqa: BLE001 — jeden vadný předpis nesmí shodit celý běh
        with tisk:
            print(f"  ! {klic}: {type(e).__name__}: {e}", flush=True)
        with zamek_stavu:
            NESTAZENE[klic] = f"{type(e).__name__}: {str(e)[:120]}"
        return "chyba"

    cil.parent.mkdir(parents=True, exist_ok=True)
    cil.write_text(obsah, encoding="utf-8")
    vysledek = "zmeneny" if znamy else "novy"
    zaznam = {
        "zmena": akt["zmena"],
        "ucinnost_od": akt["ucinnost_od"],
        "dokument_id": akt["dokument_id"],
        "pristi_zneni_od": akt["pristi_zneni_od"],
        "soubor": str(cil.relative_to(KOREN)),
        "nazev": akt["nazev"],
    }
    with zamek_stavu:
        stav[klic] = zaznam
    return vysledek


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--od-roku", type=int, default=1990)
    p.add_argument("--sbirka", action="append", default=None, help="kód sbírky, ve výchozím stavu jen sb")
    p.add_argument("--akt", help="jen jeden předpis, např. 89/2012")
    p.add_argument("--limit", type=int)
    p.add_argument("--znovu", action="store_true",
                   help="přegenerovat i to, co se od minula nezměnilo (po opravě převodu)")
    args = p.parse_args()

    stav = json.loads(STAV.read_text(encoding="utf-8")) if STAV.exists() else {}
    akty = nacti_akty(args.od_roku, set(args.sbirka) if args.sbirka else {"sb"})

    if args.akt:
        # Citace se liší podle řady („12/2000 Sb." vs „12/2000 Sb. m. s."), tak porovnávej
        # číslo a rok — řadu už zúžil --sbirka.
        cislo, _, rok = args.akt.split(" ")[0].partition("/")
        akty = [a for a in akty if a["cislo"] == cislo and a["rok"] == rok]
    if args.limit:
        akty = akty[: args.limit]

    print(f"předpisů ke zpracování: {len(akty):,}")
    pocty = {"novy": 0, "zmeneny": 0, "beze-zmeny": 0, "chyba": 0}
    zacatek = time.time()

    with ThreadPoolExecutor(max_workers=SOUBEH) as bazen:
        for i, vysledek in enumerate(bazen.map(lambda a: zpracuj(a, stav, args.znovu), akty), 1):
            pocty[vysledek] += 1
            if i % 100 == 0 or i == len(akty):
                ubehlo = time.time() - zacatek
                tempo = i / ubehlo if ubehlo else 0
                zbyva = (len(akty) - i) / tempo / 60 if tempo else 0
                with tisk:
                    print(
                        f"  {i:>6,}/{len(akty):,}  nové {pocty['novy']}  změněné {pocty['zmeneny']}  "
                        f"beze změny {pocty['beze-zmeny']}  chyby {pocty['chyba']}  "
                        f"{tempo:.1f}/s  zbývá ~{zbyva:.0f} min",
                        flush=True,
                    )
                uloz_stav(stav)

    uloz_stav(stav)
    print(f"\nhotovo za {(time.time() - zacatek) / 60:.0f} min: " + ", ".join(f"{k} {v:,}" for k, v in pocty.items()))

    # Nestažený předpis zůstane v repozitáři ve starém znění a chyba se ztratí mezi stovkami
    # řádků logu. e-Sbírka přitom některá XML trvale odmítá vydat — v září 2026 i občanský
    # zákoník a zákoník práce. Seznam se proto uloží a vypíše zvlášť.
    # Řady se stahují zvlášť (Sb. a pak Sb. m. s.), takže běh smí přepsat jen předpisy, na které
    # sáhl; jinak by běh pro smlouvy smazal seznam nestažených zákonů.
    NESTAZENE_SOUBOR.parent.mkdir(parents=True, exist_ok=True)
    try:
        drive = json.loads(NESTAZENE_SOUBOR.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        drive = {}
    zpracovane = {a["citace"] for a in akty}
    seznam = {k: v for k, v in drive.items() if k not in zpracovane} | NESTAZENE
    NESTAZENE_SOUBOR.write_text(json.dumps(seznam, ensure_ascii=False, indent=1, sort_keys=True),
                                encoding="utf-8")
    if NESTAZENE:
        print(f"\n  NESTAŽENO {len(NESTAZENE)} předpisů — zůstávají ve starém znění:")
        for klic in sorted(NESTAZENE)[:10]:
            print(f"    {klic}: {NESTAZENE[klic][:70]}")
        if len(NESTAZENE) > 10:
            print(f"    … a dalších {len(NESTAZENE) - 10}, celý seznam v {NESTAZENE_SOUBOR.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
