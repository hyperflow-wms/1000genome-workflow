# Uruchomienie toola (GUI composera 1000genome → Nextflow / HyperFlow)

GUI (`gui.py`) przyjmuje pytanie badawcze w języku naturalnym, interpretuje je LLM-em
(intent), po czym uruchamia workflow 1000genome na **Nextflow** albo **HyperFlow** i pokazuje
przebiegi, progress i wyniki. Testowane na **macOS (Apple Silicon)** — obrazy Docker są amd64
i idą przez emulację.

## 1. Wymagany układ katalogów

Tool zależy od dwóch sąsiednich repozytoriów. Sklonuj obok siebie:

```
magisterka/
├── nextflow-1000genome/          # TEN tool (gui.py, composer.py, main.nf, worker-nf.Dockerfile, testdata/)
├── 1000genome-workflow/          # fork: pakiet workflow_composer (interpret intentu) + .env (klucz LLM)
├── 1000genome-workflow2/
│   └── 1000genome-workflow/      # HyperFlow harness (streaming worker 1.3) — `git pull` = najnowszy
└── nextflow-experiments/bin/nextflow   # wrapper Nextflow (respektuje NXF_VER=25.10.2)
```

Ścieżki można nadpisać zmiennymi środowiskowymi (patrz §6), ale domyślnie zakładany jest ten układ.

## 2. Wymagane narzędzia

- **Docker** (Desktop) — uruchomiony.
- **Miniconda/conda**.
- **Homebrew** (macOS): `brew install coreutils gnu-sed grep bash`
  (GNU `head -n -1`/`sed`/`grep` — skrypty harnessu tego wymagają; macOS ma BSD).
- **Nextflow** — użyty wrapper `../nextflow-experiments/bin/nextflow`, `NXF_VER=25.10.2`
  (Nextflow 26.x psuje configi — nie używać brew binary).

## 3. Instalacja (raz)

```bash
cd nextflow-1000genome

# a) narzędzia + lokalny obraz worker-nf + obrazy HyperFlow:
bash setup.sh

# b) środowisko Python:
conda create -y -n 1000genome python=3.11
conda run -n 1000genome pip install -r requirements.txt
conda run -n 1000genome pip install -e ../1000genome-workflow/workflow-composer

# c) klucz LLM (każdy swój) — do pliku ../1000genome-workflow/.env:
echo 'GEMINI_API_KEY=twoj_klucz' > ../1000genome-workflow/.env
```

`setup.sh` buduje lokalnie obraz **`1000genome-worker-nf:1.3`** (nie ma go w rejestrze — powstaje
z `worker-nf.Dockerfile` + `individuals.streaming.py`: streaming individuals.py) i pobiera obrazy
HyperFlow (`hyperflow:v1.11.1`, `1000genome-worker:1.3-je1.4.2`, `redis`, `1000genome-data`, `gatk`).

## 4. Uruchomienie

```bash
./run-gui.sh
```
Potem otwórz **http://localhost:8765**.

W panelu: wpisz pytanie (lub kliknij przykład) → wybierz **Silnik: Nextflow / HyperFlow / Oba** →
**Uruchom ▶**. Przebiegi, progress (X/Y per proces) i wyniki widać w tabeli poniżej (filtr NF/HF/Oba).

## 5. Co robi który przycisk

- **Nextflow** → `composer.py` → `nextflow run main.nf` (obraz `1000genome-worker-nf:1.3`).
  Zawiera proces **ANNOTATE** (rs ID) — bez niego wyniki byłyby puste — i streaming individuals.py.
- **HyperFlow** → harness z `1000genome-workflow2` (streaming worker `1.3-je1.4.2`, engine `v1.11.1`).
- **tryb szybki** (checkbox) — dotyczy Nextflow: ≤3000 wariantów, 100 iteracji Monte Carlo.

## 6. Konfiguracja (zmienne środowiskowe, opcjonalne)

Domyślne wartości pasują do układu z §1. Nadpisz, jeśli masz inaczej:

| Zmienna | Znaczenie | Domyślnie |
|---|---|---|
| `CONDA_SH` | ścieżka `conda.sh` | miniconda w Homebrew |
| `CONDA_ENV` | nazwa env conda | `1000genome` |
| `ENV_FILE` | plik z kluczem LLM | `../1000genome-workflow/.env` |
| `GUI_HF_INTEG` | katalog harnessu HyperFlow | `../1000genome-workflow2/1000genome-workflow/tests/integration` |
| `GUI_BASH` | bash 5 | `/opt/homebrew/bin/bash` (fallback: `which bash`) |
| `GUI_GNUBIN` | katalogi gnubin (PATH) | coreutils/gnu-sed/grep z Homebrew |

## 7. Najczęstsze problemy

- **HyperFlow status „?" / faza INTERPRET pada „PyYAML not installed"** — env conda nie ma yaml/
  workflow_composer albo GUI nie odpalone przez `run-gui.sh`. Sprawdź krok 3b.
- **HyperFlow EXTRACT: `head: illegal line count -- -1`** — brak GNU `head`. `brew install coreutils`.
- **Wyniki puste (same zera / niebieski wykres)** — brak adnotacji rs ID. Nextflow ma proces ANNOTATE
  (już w `main.nf`); HyperFlow robi to w `extract-data.sh`. Upewnij się, że używasz aktualnych repo.
- **Nie widać zmian w GUI po edycji** — twardy reload przeglądarki `Cmd+Shift+R`. Po edycji `gui.py`
  zrestartuj serwer (`./run-gui.sh`); po edycji `main.nf`/`composer.py` restart NIE jest potrzebny.
- **Linux** — usuń zależność od Homebrew/gnubin (natywne GNU), `open` (Finder) → nieaktywne; obrazy
  amd64 bez emulacji. Reszta działa tak samo.
