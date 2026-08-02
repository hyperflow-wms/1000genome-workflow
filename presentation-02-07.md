# Composer 1000genome na Nextflow
### Od pytania badawczego do uruchomionego workflow'u

Prezentacja na spotkanie — 3 wątki: (1) etapy i optymalizacje, (2) skille/wiedza, (3) wyniki.

---

## Slajd 1 — Teza

**Composer = agent, który zamienia pytanie w języku naturalnym w uruchamialny workflow.**

- Ten sam `ResearchIntent` napędza **dwa silniki** (HyperFlow i Nextflow)
- Wynik naukowo **identyczny** niezależnie od silnika
- Composer to specjalista domenowy; silnik to wymienny backend

> **Mów:** „Zbudowaliśmy composer, który z jednego zdania po angielsku lub polsku
> generuje i uruchamia pełny pipeline populacyjny 1000genome — na Nextflow,
> reużywając mózg z wersji HyperFlow."

---

## Slajd 2 — Etapy (pipeline)

```
PROMPT ─► INTERPRET ─► MAP ─► EXTRACT ─► DAG (5 kroków) ─► WYNIKI
          (LLM+skille)  (intent  (tabix    individuals→merge
                        →params) generuje  →sifting→mutation
                                  dane)     _overlap+frequency
```

> **Mów:** „Pięć etapów. Pierwszy to jedyny etap z LLM — reszta jest
> deterministyczna. To ważne: LLM konfiguruje, ale nie dotyka danych pacjenta."

---

## Slajd 3 — Co się dzieje na każdym etapie

| Etap | Co robi | Co powstaje |
|---|---|---|
| **INTERPRET** | Gemini + skille → strukturyzuje pytanie | `ResearchIntent` (populacje, region, focus) |
| **MAP** | intent → parametry pipeline'u | `--populations`, `extract.csv` |
| **EXTRACT** | `tabix` wyciąga region z 1000 Genomes | VCF + annotation (na żywo) |
| **DAG** | 5 skryptów naukowych w kontenerach | macierze mutacji, częstotliwości |
| **WYNIKI** | pakowanie per (chromosom × populacja) | `chrN-POP.tar.gz` + wykresy |

> **Mów:** „Region 'BRCA1' w prompcie zamienia się na współrzędne, tabix pobiera
> tylko ten fragment, a dalej idzie oryginalny DAG 1000genome — bez przepisywania nauki."

---

## Slajd 4 — Interesujące optymalizacje

1. **Scatter-gather** — `individuals` dzieli chromosom na N chunków (parallelizm),
   `individuals_merge` scala. Nextflow robi to natywnie (`groupTuple`).
   Liczba chunków dobierana adaptacyjnie (presety small/medium/large / wg vCPU).
2. **Ekstrakcja zdalna tabixem** — pobiera **tylko region** (~24 MB dla BRCA1)
   zamiast całego chromosomu (~1,2 GB). ~50× mniej transferu.
3. **Reuse engine-agnostic** — przednia połowa (interpret+plan+skille) wspólna
   dla HyperFlow i Nextflow. Nowy tylko backend + runner.
4. **Cache obrazów** — obraz worker budowany raz, reużywany między runami.

> **Mów:** „Dwie najciekawsze: scatter-gather daje równoległość za darmo na kanałach
> Nextflow, a tabix zamienia gigabajty transferu na megabajty — pobieramy tylko to,
> o co pyta użytkownik."

---

## Slajd 5 — Skille / wiedza (pytanie #2)

**Tak — utworzono warstwę skilli. 5 dokumentów wstrzykiwanych do LLM, reużywanych przez oba composery:**

| Skill | Wiedza |
|---|---|
| `SKILL.md` | rola i rekomendowany workflow |
| `populations.md` | kody populacji 1000G (AFR, EUR, EAS…) + liczności |
| `genomic-regions.md` | **geny → współrzędne** (BRCA1, BRCA2, HLA) |
| `research-contexts.md` | choroba → sugerowany region |
| `data-sources.md` | wzorce URL 1000 Genomes (FTP/S3/GCS) |

> **Mów:** „To jest 'wiedza' composera. Kluczowy jest `genomic-regions.md` — dzięki niemu
> słowo 'BRCA1' staje się współrzędnymi i uruchamia ekstrakcję. I ważne: te skille są
> niezależne od silnika — ten sam plik obsługuje HyperFlow i Nextflow."

---

## Slajd 6 — Różnice względem HyperFlow

| Zreusowane (bez zmian) | Dostosowane / nowe |
|---|---|
| `llm_interpreter` — importowany 1:1 | Backend: `composer.py` (intent → params + `extract.csv`) zamiast `HyperFlowGenerator` (intent → `workflow.json`) |
| `ResearchIntent`, `data_resolver` (współrzędne) | DAG: `main.nf` w Nextflow DSL2 (kanały: `groupTuple`, `combine`) zamiast `workflow.json` + Redis |
| 5 skilli (wiedza domenowa) | Obraz `worker-nf`: dodany bash, wyczyszczony entrypoint (Nextflow wymaga bash; oryginał Alpine/dumb-init) |
| 5 skryptów naukowych + obraz worker | Runner: `nextflow run` zamiast `hflow run` |
| URL-e tabix (FTP 1000 Genomes) | Pin `NXF_VER=25.10.2`; pełny `columns.txt` (2504) dla dowolnych populacji |

> **Mów:** „Cała warstwa 'mózgu' — interpreter, wiedza, skrypty naukowe — poszła 1:1.
> Dostosować trzeba było tylko backend: sposób opisania DAG-u i uruchomienia. Plus kilka
> drobiazgów środowiskowych, jak dodanie basha do obrazu, bo Nextflow go wymaga."

---

## Slajd 7 — Wyniki: te same prompty co HyperFlow

**6 oryginalnych promptów z `cases.yaml` HyperFlow → composer Nextflow → 5/6 intentów identycznych.**

| Prompt (skrót) | Intent (Nextflow) | Zgodny z HyperFlow? |
|---|---|---|
| EUR/AFR chr22 deleterious | comparison, [EUR,AFR], chr22, deleterious | ✅ dokładnie |
| genome-wide null | multi, [AFR,AMR,EAS,EUR,SAS] | ~ (pominął ALL,GBR) |
| EAS HLA autoimmune | region, [EAS], HLA | ✅ dokładnie |
| EUR/AFR HLA | comparison, [EUR,AFR], HLA | ✅ dokładnie |
| BRCA1+BRCA2 populacje | multi, 5 pop, [BRCA1,BRCA2] | ✅ dokładnie |
| BRCA1 British | region, [GBR], BRCA1 | ✅ dokładnie |

> **Mów:** „Puściliśmy dokładnie te same pytania, na których testowaliśmy HyperFlow.
> Pięć na sześć intentów identycznych — bo interpreter jest ten sam. To pokazuje, że
> reuse działa: zmiana silnika nie zmienia rozumienia pytania."

---

## Slajd 8 — Wyniki: równoważność silników

**Ten sam prompt → HyperFlow vs Nextflow → identyczny wynik naukowy.**

- 4002 pliki wynikowe — **identyczne** (diff = 0)
- `sifted.SIFT.chr17.txt` — identyczny
- Przykładowy histogram mutacji — identyczny bajt w bajt

> **Mów:** „To jest twardy wynik do artykułu: port na inny silnik nie zmienia nauki.
> Porównaliśmy zawartość — 4002 pliki identyczne."

---

## Slajd 9 — Wyniki: przykład e2e

Prompt: *„Analyze BRCA1 gene variants comparing European and African populations."*

- INTENT: `EUR, AFR`, region `BRCA1 (17:43044295-43125483)`
- EXTRACT: 2369 wariantów pobranych tabixem
- WYNIK: `chr17-EUR.tar.gz`, `chr17-AFR.tar.gz` (+ freq) + wykresy PNG

*(wstaw zrzut wykresu `total_distribution_..._EUR.png`)*

> **Mów:** „Całość od zdania do wykresu w ~4 minuty, koszt LLM ułamek centa."

---

## Slajd 10 — Co dalej / wyniki do artykułu

- ✅ Równoważność HyperFlow ↔ Nextflow (reprodukowalność)
- ✅ Ten sam intent → dwa silniki
- 🔜 Benchmark: czas vs parallelizm (Nextflow trace/timeline)
- 🔜 Dokładność interpretacji LLM na zestawie promptów (EN/PL, geny/populacje)
- 🔜 Optymalizacja transferu: liczby tabix vs pełny chromosom

> **Mów:** „Mamy dwa gotowe wyniki do artykułu. Trzy kolejne wymagają serii runów —
> pokażę plan testów."

---

## Slajd 11 — Różnice faza po fazie

| Faza | Różni się? | Na czym polega różnica |
|---|---|---|
| 1. INTERPRET | 🟢 nie | ten sam kod, ten sam „mózg" (import z HyperFlow) |
| 2. MAP | 🟡 trochę | HyperFlow: pełny graf `workflow.json` co run; my: przepis raz (`main.nf`) + parametry |
| 3. EXTRACT | 🟡 trochę | ta sama metoda (tabix); HyperFlow mierzy dane i robi to na klastrze, my w pipelinie |
| 4. EXECUTE | 🔵 najbardziej | **równoległość**: HyperFlow „zmierz→dobierz liczbę zadań J"; Nextflow rozdziela na kanałach (parametr) |
| 5. WYNIKI | 🟢 nie | pliki identyczne; Nextflow dorzuca raporty gratis |

> **Mów:** „Rozumienie pytania jest wspólne — pożyczyliśmy mózg. Różni się tylko wykonanie:
> HyperFlow najpierw mierzy dane i dobiera liczbę równoległych zadań, Nextflow rozdziela pracę
> dynamicznie na kanałach. A mimo różnic — wynik naukowy identyczny."

---

## Slajd 12 — Gotowe wyniki: realny przebieg (Nextflow)

Prompt: *„Analyze BRCA1 gene variants comparing European and African populations."*

- **18 zadań, 0 błędów**, pełny przebieg od pytania do wyników
- **Równoległość widoczna:** 10× `individuals` liczonych jednocześnie (~2 min każde, nie po kolei)
- Czasy etapów: EXTRACT 26 s · MERGE 31 s · MUTATION_OVERLAP 8 s · FREQUENCY 51 s
- Dane wygenerowane tabixem: **2369 wariantów** (region BRCA1)
- Wyniki: `chr17-EUR/AFR(-freq).tar.gz` + wykresy
- Raporty wykonania automatycznie: `report.html`, `timeline.html`, `trace.txt`

> **Mów:** „To realny run z metrykami. Widać równoległość — dziesięć zadań individuals liczy się
> naraz, nie po kolei. Nextflow sam wygenerował raport czasu i zużycia zasobów, bez dodatkowej pracy."
