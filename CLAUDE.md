# Sbírka zákonů v markdownu

Lokální kopie českých právních předpisů z e-Sbírky, jeden soubor na předpis, v aktuálním
konsolidovaném znění. Skripty ji aktualizují a zapisují do changelogu, co se kdy změnilo.

Vault je zároveň Obsidian trezor — frontmatter, wikilinky, Bases. Otevřít se dá v Obsidianu i číst
jako obyčejný markdown.

## Kde co je

```
zakony/<rok>/<číslo>-<rok>.md   předpisy; název souboru je citace bez lomítka
Rejstřík.md                     všechny předpisy po letech, s wikilinky — generované
Předpisy.base                   tabulka pro Obsidian Bases — generovaná
CHANGELOG.md                    co se kdy změnilo, po dnech — generovaný
.zmeny/posledni.json            totéž strojově: seznam předpisů a dotčených paragrafů
smlouvy/<rok>/<číslo>-<rok>-ms.md  mezinárodní smlouvy; řady se číslují nezávisle, takže
                                1/2000 Sb. a 1/2000 Sb. m. s. jsou různé předpisy
judikatura/<předpis>/<§>.md     rozhodnutí, která ten paragraf vykládají
.zmeny/platnost.json            co je zrušené, kdy a čím
.cache/                         dávky z e-Sbírky, stav.json a index.db — negitované
tools/                          stahni, prevod, platnost, judikatura, changelog, rejstrik, index, mcp
```

## Hledání: ptej se MCP, neotevírej soubory

Korpus má přes 600 MB a občanský zákoník sám 1,4 MB. **Načíst celý předpis kvůli jednomu paragrafu
je nejdražší možný postup.** V repozitáři je MCP server (`.mcp.json`, `tools/mcp.py`), který
indexuje po paragrafech a vrací ustanovení, ne kodexy:

- `hledej` — fulltext, diakritika se ignoruje („najem" najde „nájem"). Vrací jen živé právo:
  zrušené předpisy a historická „úplná znění" jsou venku, dokud nezapneš `i_historicke`.
  **Skloňování ne**: index drží tvary, ne kmeny, takže „bytu" nenajde „byt". Piš hvězdičku —
  `vypove* najm* byt*` vrátí § 2288 OZ, `najem bytu vypoved` vrátí zrušený zákon z 1964.
- `paragraf` — jedno ustanovení podle předpisu a čísla
- `predpis` — metadata a osnova bez plného textu
- `judikatura` — rozhodnutí k paragrafu (okresní, krajské, vrchní soudy)
- `vrcholne_soudy` — Nejvyšší soud se prohledá živě (Lotus Domino, prostý GET); Ústavní a NSS
  strojově nejdou a nástroj místo nich vrátí návod. **§ se v databázi NS indexuje jen ve frázi:**
  `"§ 2235"` vrátí 43 rozhodnutí, `§ 2235` bez uvozovek nic.
- `zmeny` — poslední aktualizace

Server jede nad `.cache/index.db`, který se staví `python3 tools/index.py` — **po klonu je to
jediné, co je potřeba udělat**, data jsou v repozitáři. Index sám v něm není, má 2,8 GB. Když
chybí nebo je poškozený, nástroje řeknou, jak ho postavit. Bez serveru je nejlevnější
`awk '/^#+ § 2235$/,/^#+ § 2236$/' soubor.md`.

`pip install simplemma` zapne lemmatizaci, díky které „bytu“ najde „byt“ — volitelné, bez ní se
hledá na tvarech a dotaz chce hvězdičku.

**Než změníš řazení, změř to.** `tools/dotazy.py` je zlatá sada 42 dotazů se známými odpověďmi;
`--prohlidka` zkontroluje sadu samotnou, protože i měřidlo umí být rozbité. Stav: hledaný předpis
ve výsledcích v 88 % případů, hledané ustanovení v první desítce v 79 %; u dotazů poučeného agenta
95 a 90 %.

Ladění vah je vyčerpané — `tools/ladeni.py` projel devět vah autority a sedm sestav vah sloupců
a současné nastavení je z nich nejlepší. Poslední velký skok přineslo slévání variant dotazu
(Reciprocal Rank Fusion, `tools/ladeni-rrf.py`).

**Po zásahu do nástrojů pusť `python3 tools/testy.py`.** Sedmdesát kontrol, každá odpovídá chybě,
která se skutečně stala.

## Když se ptáš, co se změnilo

**Nikdy neprocházej strom složek.** Po každé aktualizaci je odpověď na dvou místech:

- `.zmeny/posledni.json` — strojový tvar: citace, druh změny, účinnost, **seznam dotčených
  paragrafů** a které novely to způsobily. Tohle čti první.
- `CHANGELOG.md` — totéž lidsky, po dnech, s wikilinky.

Teprve pak otevírej konkrétní předpis, a rovnou na paragrafu, který changelog jmenuje.

## Struktura předpisu

```markdown
---
citace: 89/2012 Sb.
nazev: občanský zákoník
ucinnost_od: 2026-01-01
eli: /eli/cz/sb/2012/89/2026-01-01
rok: 2012
tags: [zakon, rok/2012]
---

# 89/2012 Sb.
## ČÁST PRVNÍ
### HLAVA I — OBECNÁ ČÁST
##### § 1 — Soukromé právo
(1) …
```

Paragraf je vždy nadpis, takže se na něj dá odkázat: `[[89-2012#§ 2235]]`. Jeho úroveň se odvíjí od
toho, kolik částí a hlav je nad ním, takže není napříč předpisy stejná — hledej podle `§`, ne podle
počtu mřížek.

## Aktualizace

```bash
python3 tools/stahni.py --od-roku 1918   # stáhne, co je nové nebo změněné
python3 tools/platnost.py                # doplní zrušení do frontmatteru
python3 tools/changelog.py               # zapíše, co se změnilo
python3 tools/rejstrik.py                # přegeneruje rejstřík a base
python3 tools/index.py                   # přestaví fulltextový index
python3 tools/kontrola.py                # ověří, že nic nedrhne (nenulový kód = nález)
python3 tools/cisla.py --oprav           # srovná počty předpisů v README
python3 tools/dotazy.py                  # změří, jestli hledání vrací správná ustanovení
git add -A && git commit                 # diff je historie znění
```

Pořadí drž: `platnost.py` mění frontmatter a `index.py` z něj čte.

**Oprava převodu znamená přegenerovat celý korpus** (`stahni.py --znovu`), protože se dotkne
i souborů, u kterých to podle heuristiky nevypadá — ověřeno tím, že porovnání se zdrojem
odhalilo rozdíl u pětiny vzorku, který heuristika minula. Počítej se dvěma hodinami a s tím, že
31 tisíc změněných souborů přidá do git historie kolem 340 MB. Dělej to jen když je to potřeba.

Běh je přerušitelný — `stav.json` si pamatuje, co je hotové, a další spuštění pokračuje. Detekce
změn stojí jednu dávku o 5 MB: porovná se `datum-čas-poslední-změny`, které znění je dnes platné
a jestli se chystá další, a stahují se jen předpisy, kde se něco pohnulo. Dávka se v `.cache`
obnovuje po 20 hodinách — dřív se stáhla jednou a lokální aktualizace pak nic nového neviděla.

**Bere se znění platné dnes, ne `právní-akt-znění-poslední`.** Poslední bývá vyhlášená novela
s pozdější účinností (v září 2026 u 255 předpisů, mezi nimi zákoník práce od 1. 1. 2027); ohlásí
se jako `pristi_zneni_od` a callout `[!warning]` nad textem. `platnost.py` maže jen vlastní
callout `[!danger]`, ne všechno, co začíná `>`.

## Odkud data jsou

e-Sbírka Ministerstva vnitra, `e-sbirka.gov.cz` a `opendata.eselpoint.gov.cz`. Od 1. 1. 2024 je
elektronická Sbírka podle zákona 222/2016 Sb. **jediná právně závazná** forma, takže tohle není
kopie oficiálního zdroje — je to ten oficiální zdroj.

Text zákona je úřední dílo podle § 3 písm. a) autorského zákona, tedy bez autorskoprávní ochrany.

Předpis se stahuje jako jeden XML soubor se strukturou i textem:

```
GET /sbr-externi/stahni/informativni-zneni/{dokumentId}/XML   -> pozadavekId, id
GET /souborove-sluzby/verejne-pozadavky-dokumenty/pozadavky/{pozadavekId}  -> stav
GET /souborove-sluzby/soubory/{id}                            -> XML
```

Endpointy nejsou zdokumentované — vytažené z `assets/configs/env.js` té webové aplikace. Velké
předpisy se generují asynchronně a první odpověď vrací `PROBIHA`, takže se musí čekat na `OK`.

## Na co si dát pozor

**Je to informativní znění, ne závazné.** e-Sbírka rozlišuje informativní a právně závazné znění;
stahuje se informativní, protože to je to konsolidované. Pro právní jistotu vždy odkaž na
`e-sbirka.gov.cz` a na ELI z frontmatteru.

**Tempo stahování je záměrně mírné** — 4 souběžné požadavky a pauza mezi nimi. Je to cizí veřejná
služba placená z daní, ne náš server. Nezrychluj to bez důvodu.

**Zrušení ano, platnost ne.** `platnost.py` bere datum z metadat e-Sbírky
(`006PravniAktMetadata`, pole `metadata-datum-zrušení`) a rušící předpis z vazeb `ZRUSPRED`.
Když je ve frontmatteru `zruseno_k`, předpis k tomu datu skončil.
**Obrácený závěr neplatí** — chybějící `zruseno_k` neznamená, že předpis je účinný. U starých
předpisů bývá zrušení jen v textu novely a do vazeb se nedostalo. Nikdy netvrď „platí", tvrď
„zrušení není v datech zaznamenáno".

**Terminologie se mění a hledání synonyma nezná.** Občanský zákoník z roku 2012 přejmenoval
řadu institutů, takže starý termín v textu prostě není: `§ 1257` o služebnostech slovo „břemeno“
neobsahuje vůbec. Když dotaz nic nevrátí, zkus druhý termín — věcné břemeno/služebnost,
půjčka/zápůjčka, sdružení/spolek.

**Označení nemusí být v předpisu jedinečné.** Mezinárodní smlouvy mívají desítky protokolů
a příloh, každou s vlastním číslováním — v 65/2016 Sb. m. s. je „§ 1“ stočrnasedmdesátkrát.
`paragraf` vrátí první a upozorní, kolikrát se označení vyskytuje; když na tom záleží, ověř
v souboru, o kterou část jde. Takových dvojic je v korpusu 4 998.

**Dlouhá ustanovení se ořezávají.** § 3 zákona 1/1998 Sb. (celní sazebník) má přes sedm milionů
znaků. `paragraf` vypíše prvních 40 000 a řekne to; celý text je v souboru.

**Cizí data lžou tiše.** Otevřená data justice.cz posílají u části záznamů literál „null“ nebo
„<nezadán>“ místo prázdné hodnoty, takže `.get(klíč, "")` je propustí dál. V `judikatura.py` je
na to `hodnota()`; když sáhneš na nová pole, použij ji taky.

**Judikatura je jen z nižších soudů.** Otevřená data justice.cz nesou okresní, krajské a vrchní
soudy od 10/2020. Ústavní, Nejvyšší ani Nejvyšší správní soud tam nejsou; pár záznamů se tak
tváří, ale jsou to mylně označené okresní věci. Pro vrcholné soudy odkaž na NALUS, nsoud.cz
a nssoud.cz.
