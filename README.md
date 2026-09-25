# Právo v markdownu

> **Beta.** Data i nástroje jsou použitelné a ověřené — kontrola integrity prochází, kvalita
> hledání se měří proti sadě dotazů se známými odpověďmi. Počítej ale s tím, že se rozhraní MCP
> nástrojů i struktura souborů můžou ještě změnit, a **u všeho, co jde do podání nebo smlouvy,
> ověř znění proti [e-sbirka.gov.cz](https://e-sbirka.gov.cz)**. Nálezy hlaš do Issues.

Právo platné v Česku jako obyčejné textové soubory, jeden na předpis, v konsolidovaném znění
platném dnes. Určeno hlavně pro **AI asistenty**: v repozitáři je MCP server, který hledá po jednotlivých
ustanoveních, takže agent dostane ten jeden paragraf místo celého kodexu.

| | |
|---|---|
| **Sbírka zákonů** | 31 161 předpisů od roku 1918 |
| **Mezinárodní smlouvy** | 2 263 |
| **Právo EU** | 24 026 nařízení a směrnic česky |
| **Judikatura** | 603 943 rozhodnutí, u 8 649 paragrafů |
| dohromady | 57 450 předpisů, 482 473 ustanovení |

Je to zároveň Obsidian trezor — otevři složku v Obsidianu a máš rejstřík, prokliky mezi předpisy
a tabulku v Bases. Bez Obsidianu je to obyčejný markdown, který přečte i poznámkový blok.

## Co potřebuješ

- **Python 3.9+** a nic dalšího. Žádné knihovny, žádný Docker, žádná databáze — funguje na
  Linuxu, Macu i Windows. Na Macu stačí systémový `python3` (3.9); `python3 --version` ukáže,
  co máš. Hledání potřebuje v SQLite modul FTS5, který běžné Pythony mají — pokud ne, server
  a `tools/index.py` to řeknou a poradí.
- **8 GB volného místa**: 1,6 GB data, 2,8 GB index, zbytek na stažení a stavbu.
- **Deset minut** na postavení indexu.

Volitelně `pip install simplemma`, viz [Skloňování](#skloňování).

## Jak to rozběhnout

Data jsou součástí repozitáře, takže po naklonování zbývá jediný krok — postavit index, nad
kterým hledá MCP server:

```bash
git clone https://github.com/flatiocom/czech-law-md.git
cd czech-law-md
python3 tools/index.py          # asi 10 minut, výsledek jde do .cache/
```

V Claude Code pak stačí repozitář otevřít, konfigurace je v `.mcp.json`. Do jiného klienta:

```json
{"mcpServers": {"zakony": {"command": "python3", "args": ["tools/mcp.py"]}}}
```

Ověř, že to jede — zeptej se asistenta „co říká § 2288 občanského zákoníku o výpovědi z nájmu".
Měl by odpovědět zněním paragrafu, ne tím, že si otevřel soubor. Pak ať něco vyhledá bez
citace, třeba „kdy může pronajímatel vypovědět nájem bytu": první dotaz ověří jen čtení
paragrafu, tenhle i fulltext.

Když klient spouští jiný Python, než chceš (třeba kvůli FTS5), dej do `command` plnou cestu,
v Claude Code `claude mcp add zakony -- /cesta/k/python3 tools/mcp.py`.

### Nebo si stáhni hotový index

Stavbu lze přeskočit — po každé týdenní aktualizaci se index balí do
[Releases](https://github.com/flatiocom/czech-law-md/releases):

```bash
gh release download --pattern 'index.db.gz'
mkdir -p .cache && gunzip -c index.db.gz > .cache/index.db
```

Má 880 MB a je postavený včetně lemmatizace; `index.db.gz.sha256` vedle něj je kontrolní
součet (`sha256sum -c index.db.gz.sha256`, na Macu `shasum -a 256 -c index.db.gz.sha256`). Platí to ale jen do chvíle, než si sbírku
zaktualizuješ — pak ho musíš přestavět, jinak budou odkazy na řádky ukazovat vedle. Kdo
aktualizuje, ať si ho rovnou staví.

Do gitu index nepatří a nebude: má 2,8 GB a měnil by se při každé aktualizaci celý, takže by
historie během roku narostla o desítky gigabajtů. Jako asset release velikost repozitáře
neovlivňuje.

## Jak se v tom hledá

### Přes asistenta (hlavní způsob)

MCP server nabízí šest nástrojů:

| nástroj | co dělá |
|---|---|
| `hledej` | fulltext přes všechna ustanovení, vrací paragrafy se zněním |
| `paragraf` | jedno ustanovení, s `okoli` i sousední |
| `predpis` | metadata a osnova předpisu, bez plného textu |
| `judikatura` | rozhodnutí, která daný paragraf vykládají (okresní až vrchní soudy) |
| `vrcholne_soudy` | Nejvyšší soud živě; k Ústavnímu a NSS návod, kam jít |
| `zmeny` | co přibylo a co se změnilo při poslední aktualizaci |

Předpis EU se cituje CELEXem a člení na články: `paragraf("32016R0679", "Článek 6")` vrátí
ustanovení GDPR o zákonnosti zpracování.

**Ve výchozím stavu vrací jen živé právo.** Zrušené předpisy a historická „úplná znění" (378 publikací textu jiného zákona, vydávaly se do roku 2013) jsou vynechané, dokud si o ně neřekneš
přes `i_historicke`.

### Bez serveru

Každý paragraf je vlastní nadpis, takže stačí `awk`:

```bash
awk '/^#+ § 2235$/,/^#+ § 2236$/' zakony/2012/89-2012.md
```

V Obsidianu vede na ustanovení wikilink `[[89-2012#§ 2235]]`, u práva EU `[[32016R0679#Článek 6]]`.

## Jak hledání funguje

Jede nad SQLite FTS5 s `remove_diacritics`, takže „najem" najde „nájem". Dotaz umí `"přesnou
frázi"` v uvozovkách, `NEAR(a b, 10)` na blízkost slov a `OR`. Hvězdička potřebuje aspoň tři znaky
před sebou — kratší prefix projde statisíce ustanovení a vrátí náhodný vzorek, takže se odmítá.

Dotaz se pokládá **ve třech podobách naráz** — doslova, přes základní tvary slov a přes hrubé
kmeny — a seznamy se slijí Reciprocal Rank Fusion: každý zásah dostane 1/(60 + pořadí) z každého
seznamu, ve kterém se objevil. Dřív se varianty zkoušely po řadě a použila se první, která něco
vrátila, takže jediný špatný zásah přesného dotazu zabránil tomu lepšímu. Změřeno: hledané
ustanovení v první desítce 67 → 79 %, hledaný předpis ve výsledcích 79 → 88 %.

Pořadí vedle relevance zohledňuje **autoritu předpisu** — u českých kolik rozhodnutí se ho
dovolává, u předpisů EU kolikrát se na něj odkazují ostatní předpisy. Bez toho vyhrávají nad
kodexy okrajové vyhlášky, protože bm25 dělí skóre délkou textu.

Embeddingy tu záměrně nejsou: `§ 2235` je přesný řetězec, ne významový odstín, a na právní text
dává lexikální shoda přesnější výsledky než sémantická podobnost.

### Skloňování

Index drží tvary slov, ne kmeny, takže „bytu" samo o sobě nenajde „byt". Řeší to volitelná
lemmatizace:

```bash
pip install simplemma
python3 tools/index.py          # přestavět, aby se základní tvary zaindexovaly
```

Je to čistý Python bez kompilátoru, takže instalace projde na Windows i Macu. Změřeno: správný
předpis na prvním místě 63 → 70 %, u dotazů psaných přirozeně hledané ustanovení v první desítce
47 → 60 %. Index s ní naroste z 2,0 na 2,7 GB a staví se o čtyři minuty déle.

Bez ní se nic nerozbije, jen dotaz potřebuje hvězdičku: `vypove* najm* byt*` vrátí § 2288
občanského zákoníku, kdežto `najem bytu vypoved` vrátí zrušený zákon z roku 1964, protože ten má
hledané tvary doslova.

## Co je uvnitř

**Sbírka zákonů od roku 1918** — zákony, nařízení vlády, vyhlášky, nálezy Ústavního soudu
publikované ve Sbírce, sdělení ministerstev. Jsou tu i kodexy starší, než by člověk čekal:
občanský soudní řád (99/1963 Sb.) a trestní řád (141/1961 Sb.) jsou ze šedesátých let a platí
dodnes.

**Mezinárodní smlouvy** ve složce `smlouvy/`. Řady se číslují nezávisle, takže `1/2000 Sb.`
a `1/2000 Sb. m. s.` jsou dva různé předpisy — proto mají vlastní složku a příponu `-ms`.

**Právo EU** ve složce `eu/`, stažené z CELLARu. Konec platnosti se bere z CELLARu zvlášť
(`tools/platnost-eu.py`), protože konsolidační vazby e-Sbírky o právu EU nic nevědí — bez toho
se 10 669 předpisů, které už neplatí, tvářilo jako živé právo. Nařízení platí v Česku přímo, směrnice určují
podobu českých zákonů, takže bez nich byla sbírka neúplná. Soubor se jmenuje podle CELEXu:
`eu/2016/32016R0679.md` je GDPR. Nestažené předpisy jsou vlastnost zdroje — k části starších aktů
české znění nikdy nevzniklo, část má jen PDF. Rozhodnutí (řada DEC) tu nejsou, jsou to z velké
části jednotlivé akty typu schválení podpory, ne obecné právo.

**Rejstřík judikatury** — u paragrafů, na které soudy odkazují, seznam rozhodnutí se spisovou
značkou a ECLI. Staví se z otevřených dat Ministerstva spravedlnosti. Pokrytí: **říjen 2020 až
dnes**.

**Údaj o zrušení** u 21 136 předpisů, značený na čtyřech místech, aby na něj nešlo narazit omylem:
frontmatter (`zruseno_k`, `zrusil`, tag `zruseno`), varovný callout nad textem, přeškrtnutí
v `Rejstřík.md` (u práva EU v `Rejstřík EU.md`) a táž hlavička v rejstříku judikatury.

**Znění platné dnes, ne poslední vyhlášené.** Když už vyšla novela s pozdější účinností, soubor
drží dnešní znění a nad textem i ve frontmatteru (`pristi_zneni_od`, `pristi_zneni_eli`) ohlásí,
od kdy platí nové; nástroje `paragraf` a `predpis` to agentovi řeknou taky. Jakmile nové znění
nabude účinnosti, týdenní aktualizace ho stáhne sama.

### Kde co leží

| | |
|---|---|
| `zakony/2012/89-2012.md` | občanský zákoník |
| `smlouvy/2000/1-2000-ms.md` | mezinárodní smlouva (řada Sb. m. s.) |
| `eu/2016/32016R0679.md` | GDPR |
| `judikatura/89-2012/2235.md` | rozhodnutí, která vykládají § 2235 |
| `judikatura/metadata/` | metadata všech rozhodnutí po měsících |
| `Rejstřík.md` | předpisy Sbírky a smlouvy po letech |
| `Rejstřík EU.md` | nařízení a směrnice po letech |
| `CHANGELOG.md` | co se kdy změnilo |
| `.zmeny/posledni.json` | totéž strojově, pro agenta |
| `.zmeny/platnost.json` | co je zrušené, kdy a čím |

## Co uvnitř není

- **Rozhodnutí Ústavního a Nejvyššího správního soudu.** Otevřená data justice.cz vydávají
  okresní, krajské a vrchní soudy. **Nejvyšší soud** se ale prohledat dá — jeho databáze běží na
  Lotus Domino a odpovídá na prostý GET, takže `tools/vrcholne.py` a nástroj `vrcholne_soudy`
  vrátí spisové značky i plné texty. Zbylé dva mají vlastní databáze
  ([NALUS](https://nalus.usoud.cz), [nssoud.cz](https://nssoud.cz)), které strojový přístup
  neumožňují; nástroj u nich vrátí, kam jít a co tam zadat.
- **Plné texty rozhodnutí.** Mají přes 13 GB, takže tu jsou jen metadata. Jednotlivý text se
  stáhne na vyžádání: `python3 tools/judikatura.py --text ECLI:CZ:...`
- **Jistota, že se předpis na věc použije.** Zrušení je označené úplně, ale u velmi starých
  předpisů „účinný" znamená jen „nebyl zrušen" — formálně platné nařízení z dvacátých let může
  být fakticky obsoletní.
- **Předpisy, které e-Sbírka odmítá vydat.** U některých server generování XML odmítne
  (HTTP 400) a nedá se s tím nic dělat — v září 2026 to na několik dní potkalo i **občanský
  zákoník a zákoník práce**. Takový předpis zůstane v posledním úspěšně staženém znění, kontrola
  ho vypíše jako varování (aktualizaci nezablokuje) a seznam je v `.cache/nestazene.json`. Dlouhodobě
  jsou takové nařízení vlády 187/2018 Sb. o evropsky významných lokalitách i s novelami
  152/2022 Sb. a 113/2023 Sb. (rozsáhlé přílohy) a smlouva 37/2002 Sb. m. s.
- **Důvodové zprávy, sněmovní tisky, komentářová literatura.**

## Aktualizace

Repozitář se sám aktualizuje **každé pondělí ve 3:00** přes GitHub Actions
(`.github/workflows/aktualizace.yml`), spustit se dá i ručně tlačítkem. Commit vznikne jen tehdy,
když projde kontrola integrity — rozbitá data se do repozitáře nedostanou.

Kdo chce aktualizovat u sebe:

```bash
python3 tools/stahni.py --od-roku 1918   # stáhne nové a změněné předpisy
python3 tools/eu.py                      # totéž pro právo EU
python3 tools/judikatura.py              # metadata rozhodnutí
python3 tools/platnost.py                # doplní, co bylo zrušeno a čím
python3 tools/platnost-eu.py             # totéž pro právo EU, z CELLARu
python3 tools/changelog.py               # zapíše, co se změnilo
python3 tools/rejstrik.py                # přegeneruje rejstřík a base
python3 tools/index.py                   # přestaví index nad novými daty
```

Pořadí není libovolné: `platnost.py` mění frontmatter a `index.py` z něj čte, takže index se staví
až nakonec. Běh se dá kdykoli přerušit a pustit znovu, pokračuje tam, kde skončil — **stav
stahování je součástí repozitáře**, takže se dotáhne jen to, co přibylo.

Kdy se co mění: e-Sbírka vydává částky průběžně, judikatura přibývá denně, právo EU po týdnech.
Týdenní běh je rozumný kompromis; častěji než denně to smysl nedává.

### Verzování

Data a nástroje se mění každé jinak, tak se s nimi jinak i zachází:

- **Data se netagují.** Každý aktualizační commit je bod v čase a `CHANGELOG.md` říká, co
  přibylo — kdo potřebuje stav práva k datu, sáhne po commitu. Vydávat 52 „verzí práva" ročně
  by byl šum.
- **Nástroje a rozhraní MCP mají semver.** Změna jména nástroje nebo parametru rozbije všechno,
  co nad tím stojí, takže dostane tag: `v0.x` dokud je repozitář v betě, `v1.0.0` až bude
  rozhraní stabilní.
- **Index je jeden přepisovaný release** `index-latest`. Index ke starým datům je k ničemu,
  takže se historie verzí nedrží.

## Jak se hlídá, že to funguje

Tři nástroje, každý na něco jiného:

```bash
python3 tools/kontrola.py               # drží data pohromadě?
python3 tools/dotazy.py                 # vrací hledání správné odpovědi?
python3 tools/testy.py                  # dělají nástroje, co mají?
python3 tools/cisla.py                  # nelže dokumentace v počtech?
```

**`kontrola.py`** projde integritu citací a řad, strukturu souborů, značky zrušení, pokrytí indexu
a wikilinky; při nálezu vrací nenulový kód, takže se hodí i do CI. Vyplatí se: dvě vážné chyby —
kolize řad a index, který neuměl články — se předtím našly náhodou.

**`dotazy.py`** je zlatá sada 42 právních dotazů se známou odpovědí napříč obory (občanské,
pracovní, trestní, procesní, správní, daňové, sociální a právo EU), každý ve dvou podobách: jak by
ho napsal člověk a jak poučený agent. Dotazy jsou schválně psané jako právní otázka, ne opsané ze
znění — jinak by se měřila jen přesná shoda slov.

Aktuálně: u dotazů poučeného agenta je hledaný předpis ve výsledcích v **95 %** případů a hledané
ustanovení mezi prvními deseti v **90 %**; přes obě podoby dohromady 88 a 79 %. Pro agenta je
nejdůležitější to první číslo — dostane správný zákon a ustanovení si v něm dočte.

Sada je nástroj jako každý jiný a umí být rozbitá, takže má vlastní kontrolu:
`python3 tools/dotazy.py --prohlidka` ověří, že každý cíl v indexu existuje a že žádné slovo
dotazu nemíří mimo něj. Odhalila tři vady, které se v měření tvářily jako selhání hledání — mimo
jiné `peciv*` místo `pecliv*`, které tiše vracelo nařízení o účinnosti.

**`testy.py`** hlídá chování nástrojů. Každý test tam je proto, že odpovídající chyba se skutečně
stala: `paragraf` nefungoval u předpisů EU, protože CELEXu dopisoval „Sb."; žádost o konkrétní
rozhodnutí Nejvyššího soudu tiše vracela jiné; nadpis hlavy se lepil k prvnímu paragrafu pod ní.
S `--se-siti` zkusí i živé dotazy na cizí weby.

Než změníš řazení, změř to — `tools/ladeni.py` a `tools/ladeni-rrf.py` porovnají varianty proti
téže sadě. Devět vah autority a sedm sestav vah sloupců už zkoušeno; současné nastavení je z nich
nejlepší.

## Pro právníka

Když repozitář dostane do ruky člověk a ne agent, je pro něj [Průvodce](Průvodce.md) — co uvnitř
je, jak v tom hledat a čemu nevěřit.

## Zdroj a právní status

Data pocházejí z e-Sbírky Ministerstva vnitra. Od 1. 1. 2024 je elektronická Sbírka podle zákona
222/2016 Sb. jediná právně závazná forma. Text zákona je úřední dílo podle § 3 autorského zákona,
takže není chráněn autorským právem.

**Stahuje se informativní konsolidované znění.** Pro právní jistotu vždy ověř na
[e-sbirka.gov.cz](https://e-sbirka.gov.cz) — ELI odkaz je ve frontmatteru každého předpisu.

Právo EU pochází z [CELLARu](https://publications.europa.eu) Úřadu pro publikace EU, judikatura
z [rozhodnutí.justice.cz](https://rozhodnuti.justice.cz); rozhodnutí jsou anonymizovaná.

Licenční režim je rozepsaný v [LICENSE](LICENSE): texty předpisů jsou volné dílo podle § 3
autorského zákona, judikatura CC0, právo EU k užití s uvedením zdroje, nástroje MIT.
