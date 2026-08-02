# 1000genome Composer — Nextflow

Wierny port workflow'u **1000genome** (oryginalnie na HyperFlow) na **Nextflow** + **composer**:
pytanie badawcze w języku naturalnym → intent (LLM) → `nextflow run` → wyniki naukowe.

Teza: ten sam `ResearchIntent` napędza dwa silniki (HyperFlow i Nextflow) i daje **te same wyniki**.
Composer reużywa `interpret_research_question` z pakietu `workflow_composer` (composer HyperFlow) — realny reuse, nie kopia.

---

## 🖥️ Narzędzie (GUI)

Najprościej użyć przez lokalny **GUI**: wpisujesz pytanie badawcze, wybierasz silnik
(**Nextflow / HyperFlow / Oba**) i klikasz **Uruchom** — widzisz przebiegi, progress
(X/Y per proces) i wyniki. Runy o tym samym wejściu (NF ↔ HF) listują się same jako pary
do przejrzenia.

```bash
./run-gui.sh          # → http://localhost:8765
```

> 📄 **Instalacja, wymagania, konfiguracja, troubleshooting → [SETUP.md](SETUP.md)**
> (Docker, conda env, klucz LLM, obraz workera, układ katalogów, macOS **i Linux**).
> To jest źródło prawdy dla uruchomienia — sekcje niżej opisują port Nextflow od strony technicznej.

---

## Architektura — jak to działa

### DAG naukowy (to odtwarza `main.nf`)

```
DLA KAŻDEGO CHROMOSOMU c:
┌─────────────────────────────────────────────────────────────┐
│  individuals(vcf, c, start, stop, total)   × N chunków        │  scatter
│         │  (dzieli chromosom na kawałki, przetwarza osobniki)  │
│         ▼                                                       │
│  individuals_merge(c, [wszystkie chunki])  → chrCn.tar.gz     │  gather
│                                                                │
│  sifting(annotation_vcf, c)  → sifted.SIFT.chrC.txt           │  osobna gałąź
└─────────────────────────────────────────────────────────────┘
                          │
        (iloczyn kartezjański: chromosom × populacja)
                          ▼
DLA KAŻDEJ PARY (chromosom c, populacja pop):
┌─────────────────────────────────────────────────────────────┐
│  mutation_overlap(-c c -pop pop)  → chrC-pop.tar.gz          │  ← współdzielone mutacje
│  frequency(-c c -pop pop)         → chrC-pop-freq.tar.gz     │  ← częstotliwości alleli
│    (oba biorą: chrCn.tar.gz + sifted.SIFT.chrC.txt + pop)    │
└─────────────────────────────────────────────────────────────┘
```

To jest klasyczny **scatter-gather + cross-product**. Nextflow realizuje to natywnie na
kanałach (channels): `individuals` rozsypuje pracę na chunki, `groupTuple` je zbiera do
`individuals_merge`, a `combine` tworzy iloczyn (chromosom × populacja) dla analiz.
Każdy z 5 kroków to jeden `process` owijający **ten sam skrypt** z obrazu
`hyperflowwms/1000genome-worker` — **zero przepisywania nauki**.

### Podział na warstwy — co reużywamy, co piszemy

```
                    ┌──── REUSE (przednia połowa, engine-agnostic) ────┐
NL prompt ──► llm_interpreter ──► ResearchIntent ──► planner ──► DataPreparationPlan
             (100% reuse)         (models.py,        (~80%
                                   100% reuse)        reuse)
                                                          │
              ┌───────────────────────────────────────────┤
              ▼                                            ▼
    HyperFlowGenerator (jest)              NextflowGenerator / composer.py (NOWE)
              │                                            │
    workflow.json                          params + data.csv + main.nf
              │                                            │
    hflow run                              nextflow run  ◄── NOWE (runner)
              │                                            │
              └──────────── te same skrypty + obrazy Docker ─────────────┘
                                    (100% reuse nauki)
```

Kluczowa obserwacja: composer dzieli się na część **niezależną od silnika**
(pytanie → intent → plan) i część **zależną od silnika** (plan → konkretny workflow +
uruchomienie). Przednia połowa + cała nauka są wspólne dla HyperFlow i Nextflow;
nowy jest tylko backend generujący i uruchamiający Nextflow.

| Plik | Linie | Zależny od silnika? | Reuse |
|---|---|---|---|
| `models.py` (ResearchIntent, GenomicRegion) | 120 | ❌ nie | ~100% — dodać `OutputFormat.NEXTFLOW` |
| `llm_interpreter.py` (NL → intent) | 109 | ❌ nie | **100%** — czysta interpretacja LLM |
| `data_resolver.py` (KNOWN_REGIONS, estymacje) | 321 | ❌ nie | ~95% — wiedza o genomie, nie o silniku |
| `planner.py` (intent → DataPreparationPlan) | 343 | ⚠️ częściowo | ~80% — plan głównie agnostyczny |
| `generator.py` (HyperFlowGenerator) | 481 | ✅ TAK | tu piszemy backend Nextflow obok |
| `export.py` (switch OutputFormat) | 83 | ⚠️ | dodać `case NEXTFLOW` |
| 5 skryptów naukowych | — | ❌ nie | **100%** — czysty Python, już w obrazie worker |

---

## Wymagania (skrót — pełna instrukcja w [SETUP.md](SETUP.md))

- Docker uruchomiony
- conda env `1000genome` (litellm, pydantic, pakiet `workflow_composer`)
- Klucz LLM w `../1000genome-workflow/.env` jako `GEMINI_API_KEY`
- Obraz `1000genome-worker-nf:1.3` (streaming individuals.py + ANNOTATE). Buduje go `bash setup.sh`; ręcznie:
  ```bash
  docker build --platform linux/amd64 -f worker-nf.Dockerfile -t 1000genome-worker-nf:1.3 .
  ```

> **Uwaga:** zawsze `NXF_VER=25.10.2`. Nextflow 26.x nie parsuje configów nf-core.
> Używamy wrappera `../nextflow-experiments/bin/nextflow`.

---

## Uruchamianie

Wszystko z tego katalogu:
```bash
cd /Users/rafalszepieniec/Uczelnia/magisterka/nextflow-1000genome
```

### 1) Composer (pełne: prompt → wyniki)
```bash
# Podgląd samego intentu (szybkie, ~3s, bez uruchamiania pipeline)
./run-composer.sh --dry-run "Compare deleterious mutations between European and African populations."

# Pełny e2e (uruchamia Nextflow, ~3-4 min)
./run-composer.sh "Compare deleterious mutations between European and African populations."

# Inne populacje / język — zmienia realne obliczenia
./run-composer.sh "Analyze variants in East Asian and South Asian populations."
./run-composer.sh "Porównaj warianty w populacji brytyjskiej i europejskiej."

# Z REGIONEM w promptcie -> faza EXTRACT generuje dane tabixem z 1000 Genomes
# (nie korzysta z pre-wygenerowanych danych — pobiera region na żywo)
./run-composer.sh "Analyze BRCA1 gene variants in European and African populations."
./run-composer.sh "Compare BRCA2 variants between East Asian and African populations."
```

Gdy prompt wskazuje gen/region (BRCA1, BRCA2, HLA), interpreter dopisuje współrzędne
(z `KNOWN_REGIONS`), composer tworzy `extract.csv`, a proces `EXTRACT` w Nextflow
wyciąga region tabixem z publicznego 1000 Genomes. Bez regionu → dane testowe chr17/BRCA1.
Sukces = `[composer] SUKCES — wyniki:` + lista `chr17-<POP>.tar.gz` / `chr17-<POP>-freq.tar.gz`.

### 2) Sam pipeline Nextflow (bez LLM)
```bash
NXF_VER=25.10.2 ../nextflow-experiments/bin/nextflow run main.nf
# parametry: --populations "EUR,AFR"  --ind_jobs 10  --outdir results  --columns <plik>
```

---

## Testowanie / weryfikacja

### Sprawdź wynik ostatniego runu composera
```bash
LATEST=$(ls -t runs/ | head -1)
cat runs/$LATEST/intent.json        # co LLM zrozumiał
ls  runs/$LATEST/results/           # pliki wynikowe
cat runs/$LATEST/command.sh         # dokładna komenda (reprodukcja)
```

### Weryfikacja równoważności z HyperFlow (dowód wierności portu)
```bash
# Uruchom z 91-kolumnowym columns.txt (tym samym co HyperFlow brca1-gbr)
NXF_VER=25.10.2 ../nextflow-experiments/bin/nextflow run main.nf \
  --columns testdata/columns.gbr91.txt --populations GBR --outdir results-gbr91

# Porównaj zawartość z referencją HyperFlow
mkdir -p /tmp/nf /tmp/hf
tar xzf results-gbr91/chr17-GBR-freq.tar.gz -C /tmp/nf
tar xzf reference-hyperflow/chr17-GBR-freq.tar.gz -C /tmp/hf
echo "Pliki: NF=$(find /tmp/nf -type f|wc -l) HF=$(find /tmp/hf -type f|wc -l)"
diff -r /tmp/nf /tmp/hf && echo "IDENTYCZNE z HyperFlow"
```

### Podejrzyj zawartość pliku wynikowego (macOS: gzcat)
```bash
tar tzf runs/$LATEST/results/chr17-EUR-freq.tar.gz | head
```

---

## Raporty wykonania (do magisterki)

Nextflow generuje je automatycznie w tym katalogu po każdym runie:
```bash
open report-*.html     # zużycie CPU/RAM per proces
open timeline-*.html   # Gantt chart wykonania
open dag-*.html        # graf DAG-u
```
Surowe metryki per task: `trace-*.txt`.

---

## Struktura

| Plik / katalog | Rola |
|---|---|
| `main.nf` | Pipeline: EXTRACT (opcjonalnie) + DAG 1000genome (individuals → merge → sifting → mutation_overlap + frequency) |
| `nextflow.config` | Docker + obraz worker + raporty |
| `composer.py` | NL → intent (REUSE workflow_composer) → params → `nextflow run` |
| `run-composer.sh` | Wrapper: conda env + klucz Gemini + composer.py |
| `worker-nf.Dockerfile` | Obraz worker + bash (Nextflow wymaga) |
| `testdata/` | chr17/BRCA1 VCF, populacje, `columns.txt` (2504), `columns.gbr91.txt` (91) |
| `reference-hyperflow/` | Wyniki HyperFlow do porównania |
| `runs/<timestamp>/` | Artefakty per run composera: intent.json, command.sh, nextflow.log, results/ |

---

## Faza EXTRACT (generacja danych)

Proces `EXTRACT` w `main.nf` generuje dane samodzielnie — `tabix` wyciąga wskazany region
z publicznego 1000 Genomes (`https://ftp.1000genomes.ebi.ac.uk/.../20130502`), te same URL-e
co faza EXTRACT composera HyperFlow. Używa kontenera `htslib:1.21`.

- Aktywuje się, gdy composer wykryje region w promptcie (przekazuje `--extract_csv`).
- Ręcznie: `--extract_csv plik.csv` (format wiersza: `chrom,region,name`, np. `17,17:43044295-43125483,brca1`).
- Zweryfikowane: wygenerowany VCF jest identyczny (2369 wariantów) z pre-ekstrahowanym.

Znane ograniczenia: obsługiwane autosomy (chrX/chrY mają inne nazwy plików źródłowych —
do dodania w razie potrzeby). Multi-region działa (jeden wiersz `extract.csv` na region).
