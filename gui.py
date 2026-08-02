#!/usr/bin/env python3
"""
Prosty lokalny GUI dla composera 1000genome/Nextflow.
Uruchomienie: ./run-gui.sh  (aktywuje env + klucz Gemini + odpala ten plik)
Potem otwórz http://localhost:8765
"""
import json, os, re, subprocess, sys, signal, time, glob, mimetypes, hashlib, tarfile, tempfile, shutil, collections
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BASE = Path(__file__).parent.resolve()
RUNS = BASE / "runs"
COMPOSER = BASE / "composer.py"
REF_HF = BASE / "reference-hyperflow"   # referencyjne wyniki HyperFlow (chr17-GBR-freq)
PORT = 8765

# rejestr aktywnych przebiegów {run_id: Popen}
ACTIVE = {}

# --- HyperFlow (harness) ---
# Domyslnie repo workflow2 (git pull od Bartosza): streaming worker 1.3-je1.4.2,
# engine v1.11.1, completion-transport=stream, adnotacja rs ID. Stary fork mial na
# sztywno wolny worker 1.0. Sciezki mozna nadpisac zmiennymi srodowiskowymi (patrz SETUP.md).
HF_INTEG = Path(os.environ.get("GUI_HF_INTEG",
    str(BASE.parent / "1000genome-workflow2" / "1000genome-workflow" / "tests" / "integration")))
HARNESS  = HF_INTEG / "run-research-tests.sh"
CASES_YAML = HF_INTEG / "cases.yaml"
# bash 5 (Nextflow/harness wymagaja bash); gnubin = GNU head/sed/grep (macOS ma BSD).
BASH5 = (os.environ.get("GUI_BASH")
         or ("/opt/homebrew/bin/bash" if os.path.exists("/opt/homebrew/bin/bash")
             else (shutil.which("bash") or "bash")))
GNUBIN = os.environ.get("GUI_GNUBIN",
    "/opt/homebrew/opt/coreutils/libexec/gnubin:/opt/homebrew/opt/gnu-sed/libexec/gnubin:"
    "/opt/homebrew/opt/grep/libexec/gnubin")
ACTIVE_HF = {}   # {case_id: {"proc":Popen, "tmp_yaml":str}}

def launch_hyperflow(prompt, model):
    """Uruchamia prompt na HyperFlow przez harness: wstrzykuje tymczasowy case i odpala."""
    import yaml
    ts = time.strftime("%Y%m%d-%H%M%S")
    case_id = f"gui-{ts}"
    try:
        data = yaml.safe_load(CASES_YAML.read_text())
    except Exception as e:
        return {"ok": False, "msg": f"cases.yaml: {e}"}
    data.setdefault("test_cases", []).append({"id": case_id, "name": "GUI prompt", "prompt": prompt})
    tmp_yaml = HF_INTEG / f".cases-{case_id}.yaml"
    tmp_yaml.write_text(yaml.safe_dump(data, allow_unicode=True))
    log = open(BASE / f"gui-hf-{case_id}.log", "w")
    env = os.environ.copy()
    env["CASES_YAML"] = str(tmp_yaml)
    # Harness wola gole `python3` z PATH. GUI dziala pod conda-pythonem (ma yaml +
    # workflow_composer), ale conda-bin nie musi byc na przodzie PATH -> harness
    # lapal systemowego pythona bez yaml i padal w INTERPRET. Podajemy nasz python.
    env["PATH"] = os.path.dirname(sys.executable) + ":" + GNUBIN + ":" + env.get("PATH", "")
    cmd = [BASH5, str(HARNESS), "-y", "--model", model, case_id]
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                            cwd=str(HF_INTEG), env=env, start_new_session=True)
    ACTIVE_HF[case_id] = {"proc": proc, "tmp_yaml": str(tmp_yaml)}
    return {"ok": True, "run_id": case_id}

def list_hf_runs():
    out = []
    for d in sorted(HF_INTEG.glob("workflow-gui-*"), key=lambda p: p.name, reverse=True):
        cid = d.name.replace("workflow-", "")
        results = sorted(d.glob("chr*-*.tar.gz"))
        intent = None
        ip = d / "intent.json"
        if ip.exists():
            try: intent = json.loads(ip.read_text())
            except: pass
        proc = ACTIVE_HF.get(cid, {}).get("proc")
        running = proc is not None and proc.poll() is None
        prog = hf_progress(cid)
        if running:
            status = "w toku"
        elif results:
            status = "gotowe"
        elif prog and prog.get("failed"):
            status = "błąd"
        elif intent is not None:
            status = "brak wyników"
        else:
            status = "?"
        log_name = f"gui-hf-{cid}.log"
        out.append({"id": cid, "engine": "hyperflow", "date": fmt_date(cid),
                    "intent": intent, "status": status, "n_results": len(results),
                    "results": [r.name for r in results], "running": running,
                    "hf_log": log_name if (BASE / log_name).exists() else None,
                    "progress": prog if (running or (prog and not prog.get("done"))) else None})
    return out

def stop_hf(cid):
    proc = (ACTIVE_HF.get(cid) or {}).get("proc")
    if proc is None or proc.poll() is not None:
        return {"ok": False, "msg": "Przebieg HyperFlow nie jest aktywny."}
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        return {"ok": True, "msg": "Zatrzymano."}
    except Exception as e:
        return {"ok": False, "msg": str(e)}

ANSI = re.compile(r'\x1b\[[0-9;]*m')
PROC_ORDER = ["EXTRACT", "CHUNK_VCF", "INDIVIDUALS", "INDIVIDUALS_MERGE", "SIFTING", "MUTATION_OVERLAP", "FREQUENCY"]

def parse_progress(run_id):
    """Czyta ogon nextflow.log i wyciaga 'X of Y' per proces -> ogolny % + rozbicie."""
    log = RUNS / run_id / "nextflow.log"
    if not log.exists():
        return None
    try:
        with open(log, "rb") as f:
            f.seek(0, 2); size = f.tell(); f.seek(max(0, size - 20000))
            txt = ANSI.sub("", f.read().decode("utf-8", "ignore")).replace("\r", "\n")
    except Exception:
        return None
    if "Pipeline completed" in txt:
        return {"pct": 100, "done": True, "procs": []}
    procs = []
    sx = sy = 0
    for name in PROC_ORDER:
        m = re.findall(rf"{name}\b[^|\n]*\|\s*(\d+) of (\d+)", txt)
        if m:
            x, y = int(m[-1][0]), int(m[-1][1])
            procs.append({"name": name, "x": x, "y": y, "done": x >= y})
            sx += x; sy += y
    pct = int(round(100 * sx / sy)) if sy else 0
    return {"pct": pct, "done": False, "procs": procs}

RUN_TS = re.compile(r'(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})')

def fmt_date(run_id):
    """Z ID typu '20260802-230239' albo 'gui-20260802-230239' -> '2026-08-02 23:02'."""
    m = RUN_TS.search(run_id or "")
    if not m:
        return ""
    y, mo, d, h, mi, _s = m.groups()
    return f"{y}-{mo}-{d} {h}:{mi}"

# Fazy harnessu HyperFlow -> bazowy % na starcie kazdej fazy (przed EXECUTE).
HF_PHASES = [("INTERPRET", 5), ("PLAN", 12), ("EXTRACT", 22), ("GENERATE", 32), ("EXECUTE", 45)]
HF_PROC_ORDER = ["individuals", "individuals_merge", "sifting", "mutation_overlap", "frequency"]

def _hf_task_breakdown(case_id, txt):
    """Rozbicie X/Y per proces: total z workflow.json, ukonczone z logu ('task finished')."""
    wf = HF_INTEG / f"workflow-{case_id}" / "workflow.json"
    if not wf.exists():
        return None
    try:
        items = json.loads(wf.read_text()).get("processes", [])
        totals = collections.Counter(p.get("name") for p in items if isinstance(p, dict))
    except Exception:
        return None
    fin = collections.Counter(re.findall(r"task finished:\s*(\w+)", txt))
    order = [n for n in HF_PROC_ORDER if n in totals] + [n for n in totals if n not in HF_PROC_ORDER]
    procs = []
    for name in order:
        y = totals.get(name, 0)
        if not y:
            continue
        x = min(fin.get(name, 0), y)
        procs.append({"name": name, "x": x, "y": y, "done": x >= y})
    return procs or None

def hf_progress(case_id):
    """Progress dla HyperFlow: fazy harnessu + dokladne X/Y per proces w EXECUTE."""
    log = BASE / f"gui-hf-{case_id}.log"
    if not log.exists():
        return None
    try:
        # caly log (zliczamy 'task finished' od poczatku EXECUTE); logi sa niewielkie
        with open(log, "rb") as f:
            f.seek(0, 2); size = f.tell(); f.seek(max(0, size - 8_000_000))
            txt = ANSI.sub("", f.read().decode("utf-8", "ignore")).replace("\r", "\n")
    except Exception:
        return None
    procs = _hf_task_breakdown(case_id, txt)
    if re.search(r"\bPASSED\b", txt) or "Workflow execution completed" in txt or re.search(r"Workflow \[[^\]]*\] finished", txt):
        return {"pct": 100, "phase": "gotowe", "done": True, "procs": procs}
    if re.search(r"\bFAILED\b", txt):
        phase = next((n for n, _ in reversed(HF_PHASES) if re.search(rf"Phase\s+\d+:\s*{n}", txt)), "?")
        return {"pct": 0, "phase": phase, "failed": True, "procs": procs}
    base, phase = 0, "start"
    for name, pct in HF_PHASES:
        if re.search(rf"Phase\s+\d+:\s*{name}", txt):
            base, phase = pct, name
    # jesli sa juz zadania EXECUTE -> dokladny % z ukonczonych taskow
    if procs:
        sx = sum(p["x"] for p in procs); sy = sum(p["y"] for p in procs)
        if sy:
            return {"pct": int(round(100 * sx / sy)), "phase": "EXECUTE", "done": False, "procs": procs}
    return {"pct": base, "phase": phase, "done": False}

def _cat(name):
    if name.startswith('Histogram'): return 'Histogramy'
    if name.startswith('random_indiv'): return 'random_indiv (losowe)'
    if name.startswith('Mutation_overlap'): return 'Mutation_overlap'
    if name.startswith('map_variations'): return 'map_variations'
    if name.startswith('mutation_index'): return 'mutation_index'
    return 'inne'

def get_pairs():
    """Zwraca dostępne pary Nextflow↔HyperFlow (te same wejścia = bit-do-bit porównywalne)."""
    pairs = []
    # Para 1: BRCA1 / GBR
    if (BASE / "verified-gbr-match" / "chr17-GBR-freq.tar.gz").exists() and (REF_HF / "chr17-GBR-freq.tar.gz").exists():
        pairs.append({"id": "brca1-gbr", "label": "BRCA1 / GBR",
                      "nf": str(BASE / "verified-gbr-match"), "hf": str(REF_HF)})
    # Para 2: BRCA1+BRCA2 / 5 populacji
    p2nf = BASE / "match-brca" / "results"; p2hf = HF_INTEG / "workflow-brca-breast-cancer"
    if p2nf.exists() and p2hf.exists() and list(p2nf.glob("*.tar.gz")):
        pairs.append({"id": "brca-breast-cancer", "label": "BRCA1+BRCA2 / 5 populacji",
                      "nf": str(p2nf), "hf": str(p2hf)})
    return pairs

def compare_pair(pair_id):
    """Porównuje parę per-tarball (region×populacja) i per-kategoria (md5)."""
    p = {x["id"]: x for x in get_pairs()}.get(pair_id)
    if not p:
        return {"ok": False, "msg": "Brak takiej pary (może jeszcze się liczy)."}
    nf = Path(p["nf"]); hf = Path(p["hf"])
    tarballs = sorted(t.name for t in nf.glob("*.tar.gz") if (hf / t.name).exists())
    if not tarballs:
        return {"ok": False, "msg": "Brak wspólnych plików wynikowych do porównania."}
    per = []; tot = collections.defaultdict(lambda: [0, 0])
    tmp = Path(tempfile.mkdtemp())
    try:
        for tb in tarballs:
            a = tmp / "a"; b = tmp / "b"
            shutil.rmtree(a, ignore_errors=True); shutil.rmtree(b, ignore_errors=True)
            with tarfile.open(nf / tb) as t: t.extractall(a)
            with tarfile.open(hf / tb) as t: t.extractall(b)
            ident = diff = 0
            for f in a.rglob("*"):
                if not f.is_file(): continue
                g = b / f.relative_to(a)
                if not g.exists(): continue
                same = hashlib.md5(f.read_bytes()).hexdigest() == hashlib.md5(g.read_bytes()).hexdigest()
                tot[_cat(f.name)][0 if same else 1] += 1
                ident += same; diff += (not same)
            per.append({"tarball": tb, "id": ident, "diff": diff})
        cats = [{"kat": k, "id": v[0], "diff": v[1]} for k, v in sorted(tot.items())]
        return {"ok": True, "pair": p["label"], "per": per, "cats": cats,
                "total_id": sum(v[0] for v in tot.values()), "total_diff": sum(v[1] for v in tot.values())}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def list_runs():
    out = []
    for d in sorted(RUNS.glob("*"), key=lambda p: p.name, reverse=True):
        if not d.is_dir():
            continue
        intent = None
        ip = d / "intent.json"
        if ip.exists():
            try: intent = json.loads(ip.read_text())
            except: pass
        results = sorted((d / "results").glob("*.tar.gz")) if (d / "results").exists() else []
        nextflow_log = d / "nextflow.log"
        failed = False
        if nextflow_log.exists():
            try:
                tail = nextflow_log.read_text(errors="ignore")[-8000:]
                failed = "ERROR ~" in tail or "terminated with an error" in tail
            except Exception:
                failed = False
        def one(pattern):
            g = list(d.glob(pattern))
            return g[0].name if g else None
        proc = ACTIVE.get(d.name)
        if proc is not None and proc.poll() is None:
            status = "w toku"
        elif results:
            status = "gotowe"
        elif failed:
            status = "błąd"
        elif intent is not None:
            status = "brak wyników"
        else:
            status = "?"
        comparable = (REF_HF / "chr17-GBR-freq.tar.gz").exists() and (d / "results" / "chr17-GBR-freq.tar.gz").exists()
        out.append({
            "id": d.name,
            "engine": "nextflow",
            "date": fmt_date(d.name),
            "intent": intent,
            "status": status,
            "n_results": len(results),
            "results": [r.name for r in results],
            "report": one("report-*.html"),
            "timeline": one("timeline-*.html"),
            "dag": one("dag-*.html"),
            "nextflow_log": nextflow_log.name if nextflow_log.exists() else None,
            "has_intent": ip.exists(),
            "running": status == "w toku",
            "comparable": comparable,
            "progress": parse_progress(d.name) if status == "w toku" else None,
        })
    return out

def stop_run(run_id):
    proc = ACTIVE.get(run_id)
    if proc is None or proc.poll() is not None:
        return {"ok": False, "msg": "Przebieg nie jest aktywny."}
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        return {"ok": True, "msg": "Wysłano sygnał zatrzymania."}
    except Exception as e:
        return {"ok": False, "msg": str(e)}

def run_composer_dry(prompt, model):
    cp = subprocess.run([sys.executable, str(COMPOSER), "--dry-run", "--model", model, prompt],
                        capture_output=True, text=True, cwd=str(BASE))
    txt = cp.stdout + "\n" + cp.stderr
    m = re.search(r'\{.*?"clarification_needed".*?\}', txt, re.S)
    intent = None
    if m:
        try: intent = json.loads(m.group(0))
        except: pass
    return {"ok": intent is not None, "intent": intent, "raw": txt[-3000:]}

def launch_full(prompt, model, fast=False):
    existing = {p.name for p in RUNS.glob("*")}
    log = open(BASE / "gui-lastrun.log", "w")
    cmd = [sys.executable, str(COMPOSER), "--model", model]
    if fast:  # tryb szybki: mniej wariantow + mniej iteracji Monte Carlo
        cmd += ["--max-variants", "3000", "--n-runs", "100"]
    cmd += [prompt]
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=str(BASE),
                            start_new_session=True)  # własna grupa procesów -> da się zatrzymać
    run_id = None
    for _ in range(40):  # do 20s na pojawienie się katalogu runu
        time.sleep(0.5)
        new = {p.name for p in RUNS.glob("*")} - existing
        if new:
            run_id = sorted(new)[-1]; break
    if run_id:
        ACTIVE[run_id] = proc
    return {"ok": run_id is not None, "run_id": run_id}

EXAMPLES = [
    ("BRCA1 (rzadki region)", "Analyze BRCA1 gene variants comparing European and African populations."),
    ("HLA (GĘSTY region)", "Compare mutation patterns in the HLA region between European and African populations."),
    ("EAS HLA autoimmune", "Do East Asian populations show distinct mutation sharing patterns in the HLA immune region?"),
    ("BRCA1+BRCA2 (multi)", "What variants exist in the BRCA1 and BRCA2 genes across European and African populations?"),
    ("Po polsku", "Porównaj warianty w regionie HLA między populacją brytyjską a afrykańską."),
]

HTML = """<!doctype html><html lang=pl><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Composer 1000genome / Nextflow</title>
<style>
*{box-sizing:border-box} body{font-family:system-ui,Segoe UI,Roboto,sans-serif;margin:0;background:#f4f6fa;color:#1a2230}
header{background:#1565c0;color:#fff;padding:16px 24px} header h1{margin:0;font-size:20px}
header p{margin:4px 0 0;opacity:.9;font-size:13px}
.wrap{max-width:1100px;margin:0 auto;padding:20px}
.card{background:#fff;border-radius:10px;padding:18px;margin-bottom:18px;box-shadow:0 1px 4px rgba(0,0,0,.06)}
textarea{width:100%;min-height:70px;padding:10px;border:1px solid #cdd5e0;border-radius:8px;font-size:14px;font-family:inherit}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:10px}
button{background:#1565c0;color:#fff;border:0;border-radius:8px;padding:10px 16px;font-size:14px;cursor:pointer}
button.sec{background:#eef3fa;color:#1565c0}
button:disabled{opacity:.5;cursor:default}
select{padding:9px;border-radius:8px;border:1px solid #cdd5e0}
.chip{background:#eef3fa;color:#1565c0;border:1px solid #d3e0f2;border-radius:20px;padding:6px 12px;font-size:12.5px;cursor:pointer}
.chip:hover{background:#e0eaf8}
pre{background:#0d1b2a;color:#d6e6ff;padding:12px;border-radius:8px;overflow:auto;font-size:12.5px;max-height:280px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #eef1f6;vertical-align:top}
th{background:#f7f9fc;font-weight:600}
a.link{color:#1565c0;text-decoration:none;margin-right:10px;font-size:12.5px;white-space:nowrap}
a.link:hover{text-decoration:underline}
.pbar{height:14px;background:#e6ecf5;border-radius:8px;overflow:hidden;min-width:120px;margin:3px 0}
.pbar>div{height:100%;background:#1565c0;transition:width .4s;text-align:right;color:#fff;font-size:10px;line-height:14px;padding-right:5px;box-sizing:border-box}
.pmini{font-size:11px;color:#555}.pmini .d{color:#2e7d34}.pmini .a{color:#b26b00;font-weight:600}
button.mini{font-size:11.5px;padding:5px 10px;margin:2px 4px 2px 0;background:#eef3fa;color:#1565c0;border:1px solid #d3e0f2}
button.mini.stop{background:#fbe9e9;color:#b02a2a;border-color:#f0cccc}
.badge{font-size:11px;padding:2px 8px;border-radius:20px}
.b-done{background:#e6f4ea;color:#1e7d34}.b-run{background:#fff4e0;color:#b26b00}.b-none{background:#fbe9e9;color:#b02a2a}
.muted{color:#6b7683;font-size:12.5px}
h3{margin:0 0 10px}
.help li{margin:4px 0;font-size:13px}
.ext{font-size:12.5px}
.eng{font-size:10.5px;font-weight:700;padding:2px 7px;border-radius:6px;letter-spacing:.3px;white-space:nowrap}
.eng-nf{background:#e7f0fd;color:#1565c0;border:1px solid #cadef7}
.eng-hf{background:#fdeede;color:#b26b00;border:1px solid #f0d8b3}
.filt{display:flex;gap:6px;margin:2px 0 12px}
.filt button{background:#eef3fa;color:#41506a;border:1px solid #d3e0f2;padding:5px 13px;font-size:12.5px;border-radius:20px}
.filt button.on{background:#1565c0;color:#fff;border-color:#1565c0}
td.dt{white-space:nowrap;color:#41506a;font-size:12px}
tbody tr:hover{background:#fafcff}
.seg{font-size:13px;color:#41506a;display:flex;align-items:center;gap:10px;background:#f2f6fc;border:1px solid #dbe6f5;border-radius:8px;padding:6px 12px}
.seg label{display:flex;align-items:center;gap:4px;cursor:pointer;margin:0}
</style></head><body>
<header><h1>Composer 1000genome → Nextflow</h1>
<p>Pytanie w języku naturalnym → intent (LLM) → tabix → workflow → wyniki. Region i populacje zależą od prompta.</p></header>
<div class=wrap>

<div class=card>
  <h3>1. Wpisz pytanie badawcze</h3>
  <textarea id=prompt placeholder="np. Compare mutation patterns in the HLA region between European and African populations."></textarea>
  <div class=row>
    <select id=model>
      <option value="gemini/gemini-2.5-flash">gemini-2.5-flash (tani)</option>
      <option value="gemini/gemini-2.5-pro">gemini-2.5-pro</option>
    </select>
    <span class=seg>Silnik:
      <label><input type=radio name=engine value=nextflow checked> Nextflow</label>
      <label><input type=radio name=engine value=hyperflow> HyperFlow</label>
      <label><input type=radio name=engine value=both> Oba</label>
    </span>
    <button class=sec onclick=dry()>Podejrzyj intent (dry-run, ~3s)</button>
    <button onclick=runSelected()>Uruchom ▶</button>
    <label style="font-size:13px;display:flex;align-items:center;gap:5px"><input type=checkbox id=fast> tryb szybki (Nextflow: ≤3000 wariantów, 100 iteracji)</label>
  </div>
  <div class=row>
    <span class=muted>Przykłady (kliknij):</span>
    __CHIPS__
  </div>
  <div id=out style=margin-top:12px></div>
</div>

<div class=card>
  <div class=row style="justify-content:space-between">
    <h3 style=margin:0>2. Weryfikacje i przebiegi</h3>
    <button class=sec onclick=refresh()>Odśwież</button>
  </div>
  <div id=pairs></div>
  <div id=cmpout></div>
  <div class=filt id=filt>
    <button data-f=all class=on onclick="setFilter('all',this)">Oba</button>
    <button data-f=nextflow onclick="setFilter('nextflow',this)">Nextflow</button>
    <button data-f=hyperflow onclick="setFilter('hyperflow',this)">HyperFlow</button>
  </div>
  <div id=runs></div>
</div>

<div class=card help>
  <h3>Co możesz tu robić</h3>
  <ul class=help>
    <li><b>Region zależy od prompta</b> — wpisz „HLA" → gęsty region (niezerowe histogramy); „BRCA1" → mały region.</li>
    <li><b>Populacje z prompta</b> — np. „European and African" → EUR, AFR (napędzają obliczenia).</li>
    <li><b>Podejrzyj intent</b> — zobacz, co LLM zrozumiał, bez uruchamiania (szybkie, tanie).</li>
    <li><b>Uruchom pełny</b> — tabix pobiera region, workflow liczy, wyniki + raporty pojawią się w przebiegach.</li>
    <li><b>Raporty Nextflow</b> — przy każdym przebiegu: <i>report</i> (CPU/RAM), <i>timeline</i> (równoległość), <i>dag</i> (graf).</li>
  </ul>
  <h3 style=margin-top:14px>Odnośniki — dokumentacja Nextflow</h3>
  <ul class=help>
    <li class=ext><a class=link href="https://www.nextflow.io/docs/latest/index.html" target=_blank>Dokumentacja Nextflow</a></li>
    <li class=ext><a class=link href="https://www.nextflow.io/docs/latest/reports.html" target=_blank>Raporty: report / timeline / trace / dag</a></li>
    <li class=ext><a class=link href="https://www.nextflow.io/docs/latest/channel.html" target=_blank>Kanały (scatter-gather)</a></li>
    <li class=ext><a class=link href="https://www.nextflow.io/docs/latest/executor.html" target=_blank>Executory (local / SLURM / Kubernetes)</a></li>
    <li class=ext><a class=link href="https://training.nextflow.io/" target=_blank>Nextflow Training</a></li>
  </ul>
</div>

</div>
<script>
const $=id=>document.getElementById(id);
function setChip(t){$('prompt').value=t}
async function dry(){
  $('out').innerHTML='<span class=muted>Interpretuję…</span>';
  const r=await fetch('/api/dry',{method:'POST',body:JSON.stringify({prompt:$('prompt').value,model:$('model').value})});
  const j=await r.json();
  if(j.intent){$('out').innerHTML='<b>Intent (co LLM zrozumiał):</b><pre>'+JSON.stringify(j.intent,null,2)+'</pre>';}
  else{$('out').innerHTML='<span class=muted>Nie udało się sparsować intentu.</span><pre>'+(j.raw||'')+'</pre>';}
}
async function runFull(){
  $('out').innerHTML='<span class=muted>Startuję workflow… (interpret + tabix + DAG)</span>';

  try {
    const r = await fetch('/api/run',{
      method:'POST',
      headers:{
        'Content-Type':'application/json'
      },
      body:JSON.stringify({
        prompt:$('prompt').value,
        model:$('model').value,
        fast:$('fast').checked
      })
    });

    if(!r.ok){
      throw new Error("HTTP "+r.status);
    }

    const j = await r.json();

    if(j.run_id){
      $('out').innerHTML =
        '<b>Uruchomiono:</b> '+j.run_id+
        ' — śledź w tabeli przebiegów.';
      refresh();
    }
    else{
      $('out').innerHTML =
        '<span class=muted>Nie udało się wystartować.</span>';
    }

  } catch(e){
    console.error("runFull error:", e);
    $('out').innerHTML =
      '<span class=muted>Błąd: '+e.message+'</span>';
  }
}
function selEngine(){const r=document.querySelector('input[name=engine]:checked');return r?r.value:'nextflow';}
async function runSelected(){
  if(!$('prompt').value.trim()){$('out').innerHTML='<span class=muted>Wpisz najpierw pytanie badawcze.</span>';return;}
  const e=selEngine();
  if(e=='nextflow')return runFull();
  if(e=='hyperflow')return runHF();
  return runBoth();
}
async function runBoth(){
  $('out').innerHTML='<span class=muted>Startuję na Nextflow i HyperFlow…</span>';
  const nfBody=JSON.stringify({prompt:$('prompt').value,model:$('model').value,fast:$('fast').checked});
  const hfBody=JSON.stringify({prompt:$('prompt').value,model:$('model').value});
  const [nf,hf]=await Promise.all([
    fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:nfBody}).then(r=>r.json()).catch(e=>({err:e.message})),
    fetch('/api/run_hf',{method:'POST',headers:{'Content-Type':'application/json'},body:hfBody}).then(r=>r.json()).catch(e=>({err:e.message}))
  ]);
  let msg='<b>Uruchomiono:</b> ';
  msg+=nf.run_id?('Nextflow '+nf.run_id):('Nextflow — '+(nf.err||'błąd'));
  msg+=' &nbsp;·&nbsp; '+(hf.run_id?('HyperFlow '+hf.run_id):('HyperFlow — '+(hf.msg||hf.err||'błąd')));
  $('out').innerHTML=msg+' — śledź w tabeli przebiegów.';
  refresh();
}
function badge(s){if(s=='gotowe')return '<span class="badge b-done">gotowe</span>';if(s=='w toku')return '<span class="badge b-run">w toku</span>';return '<span class="badge b-none">'+s+'</span>';}
function isummary(i){if(!i)return '<span class=muted>—</span>';const regs=(i.regions||[]).map(r=>r.name).join(',')||'—';return (i.analysis_type||'')+' · ['+(i.populations||[]).join(',')+'] · '+regs+' · '+(i.focus||'');}
let RUNFILTER='all', ALLRUNS=[];
function setFilter(f,btn){RUNFILTER=f;document.querySelectorAll('#filt button').forEach(b=>b.classList.remove('on'));btn.classList.add('on');renderRuns();}
function engBadge(e){return e=='hyperflow'?'<span class="eng eng-hf">HyperFlow</span>':'<span class="eng eng-nf">Nextflow</span>';}
function runLinks(x){
  let links='';
  if(x.engine=='hyperflow'){
    if(x.hf_log)links+='<a class=link target=_blank href="/file?p='+x.hf_log+'">log</a>';
    links+='<a class=link style=cursor:pointer data-id="'+x.id+'" onclick="openHF(this.dataset.id)">📂 folder</a>';
    return links;
  }
  if(x.report)links+='<a class=link target=_blank href="/file?p=runs/'+x.id+'/'+x.report+'">report</a>';
  if(x.timeline)links+='<a class=link target=_blank href="/file?p=runs/'+x.id+'/'+x.timeline+'">timeline</a>';
  if(x.dag)links+='<a class=link target=_blank href="/file?p=runs/'+x.id+'/'+x.dag+'">dag</a>';
  if(x.nextflow_log)links+='<a class=link target=_blank href="/file?p=runs/'+x.id+'/'+x.nextflow_log+'">log</a>';
  if(x.has_intent)links+='<a class=link target=_blank href="/file?p=runs/'+x.id+'/intent.json">intent</a>';
  let fp='runs/'+x.id+(x.n_results?'/results':'');
  links+='<a class=link style=cursor:pointer data-p="'+fp+'" onclick="openFolder(this.dataset.p)">📂 folder</a>';
  return links;
}
function shortProc(n){return n.replace("INDIVIDUALS_MERGE","MERGE").replace("MUTATION_OVERLAP","MUT").replace("individuals_merge","merge").replace("mutation_overlap","mut");}
function progHtml(x){
  if(!x.progress)return '';
  let st='<div class=pbar><div style="width:'+x.progress.pct+'%">'+x.progress.pct+'%</div></div>';
  if(x.progress.procs && x.progress.procs.length){
    let ph=x.progress.procs.map(p=>'<span class="'+(p.done?'d':(p.x<p.y?'a':''))+'">'+shortProc(p.name)+' '+p.x+'/'+p.y+'</span>').join(' · ');
    let ex=(x.engine=='hyperflow'&&x.progress.phase&&x.progress.phase!='EXECUTE'&&x.progress.phase!='gotowe')?'<span class=a>'+x.progress.phase+'</span> · ':'';
    st+='<div class=pmini>'+ex+ph+'</div>';
  }else if(x.progress.phase){
    st+='<div class=pmini>faza: <span class=a>'+x.progress.phase+'</span></div>';
  }
  return st;
}
function stopBtn(x){
  if(!x.running)return '';
  const fn=x.engine=='hyperflow'?'stopHF':'stopRun';
  return '<button class="mini stop" data-id="'+x.id+'" onclick="'+fn+'(this.dataset.id)">Zatrzymaj</button>';
}
function renderRuns(){
  let rows=ALLRUNS.filter(x=>RUNFILTER=='all'||x.engine==RUNFILTER);
  if(!rows.length){$('runs').innerHTML='<p class=muted>Brak przebiegów dla tego filtra.</p>';return;}
  let h='<table><tr><th>Silnik</th><th>Data</th><th>Przebieg</th><th>Intent</th><th>Status</th><th>Wyniki</th><th>Raporty</th><th>Akcje</th></tr>';
  for(const x of rows){
    h+='<tr><td>'+engBadge(x.engine)+'</td><td class=dt>'+(x.date||'')+'</td><td>'+x.id+'</td><td>'+isummary(x.intent)+'</td><td>'+badge(x.status)+progHtml(x)+'</td><td>'+x.n_results+' plików</td><td>'+(runLinks(x)||'—')+'</td><td>'+(stopBtn(x)||'—')+'</td></tr>';
  }
  h+='</table>';$('runs').innerHTML=h;
}
async function refresh(){
  const [a,b]=await Promise.all([fetch('/api/runs').then(r=>r.json()),fetch('/api/hf_runs').then(r=>r.json())]);
  ALLRUNS=a.concat(b).sort((p,q)=>q.id.replace('gui-','').localeCompare(p.id.replace('gui-','')));
  renderRuns();
}
async function runHF(){
  $('out').innerHTML='<span class=muted>Startuję na HyperFlow (harness: interpret + extract + execute)… wolne (emulacja).</span>';
  const r=await fetch('/api/run_hf',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:$('prompt').value,model:$('model').value})});
  const j=await r.json();
  if(j.run_id){$('out').innerHTML='<b>HyperFlow uruchomiony:</b> '+j.run_id+' — śledź w tabeli przebiegów (filtr HyperFlow).';refresh();}
  else{$('out').innerHTML='<span class=muted>Nie udało się: '+(j.msg||'')+'</span>';}
}
async function stopHF(id){
  if(!confirm('Zatrzymać HyperFlow '+id+'?'))return;
  const r=await fetch('/api/stop_hf?id='+id);const j=await r.json();alert(j.msg||'ok');refresh();
}
async function openHF(id){const r=await fetch('/api/open_hf?id='+id);const j=await r.json();if(!j.ok)alert(j.msg||'błąd');}
async function openFolder(p){
  const r=await fetch('/api/open?p='+encodeURIComponent(p));const j=await r.json();
  if(!j.ok)alert(j.msg||'nie udało się otworzyć folderu');
}
async function openPair(el){
  const r=await fetch('/api/open_pair?pair='+el.dataset.pid+'&side='+el.dataset.side);const j=await r.json();
  if(!j.ok)alert(j.msg||'nie udało się otworzyć folderu');
}
async function stopRun(id){
  if(!confirm('Zatrzymać przebieg '+id+'?'))return;
  const r=await fetch('/api/stop?id='+id);const j=await r.json();
  alert(j.msg||(j.ok?'zatrzymano':'nie udało się'));refresh();
}
async function loadPairs(){
  const r=await fetch('/api/pairs');const ps=await r.json();
  let h='';
  if(ps.length){
    h='<div class=card style="background:#eef6ff;margin-bottom:14px"><b>Weryfikacje HyperFlow ↔ Nextflow</b> <span class=muted>(dostępne, gdy mamy wynik HyperFlow na tym samym wejściu)</span><div class=row style=margin-top:8px>';
    for(const p of ps){
      h+='<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:3px 0">';
      h+='<button class=sec data-pid="'+p.id+'" onclick="cmp(this.dataset.pid)">Porównaj: '+p.label+'</button>';
      h+='<a class=link style=cursor:pointer data-pid="'+p.id+'" data-side=nf onclick="openPair(this)">📂 Nextflow</a>';
      h+='<a class=link style=cursor:pointer data-pid="'+p.id+'" data-side=hf onclick="openPair(this)">📂 HyperFlow</a>';
      h+='</div>';
    }
    h+='</div></div>';
  }else{
    h='<p class=muted>Brak par do weryfikacji (potrzebny wynik HyperFlow na identycznym wejściu). Runy composera używają pełnych 2504 kolumn — nie pasują bit-do-bit do referencji HyperFlow.</p>';
  }
  $('pairs').innerHTML=h;
}
async function cmp(pid){
  $('cmpout').innerHTML='<span class=muted>Porównuję md5… (dla wielu plików może potrwać ~1 min)</span>';
  const r=await fetch('/api/compare?pair='+pid);const j=await r.json();
  if(!j.ok){$('cmpout').innerHTML='<span class=muted>'+(j.msg||'brak porównania')+'</span>';return;}
  let t='<h3 style=margin-top:14px>Weryfikacja: '+j.pair+' — Nextflow vs HyperFlow (md5)</h3>';
  t+='<table><tr><th>Kategoria wyników</th><th>Identyczne</th><th>Różne</th></tr>';
  for(const c of j.cats)t+='<tr><td>'+c.kat+'</td><td>'+c.id+'</td><td>'+(c.diff?'<b style=color:#b26b00>'+c.diff+'</b>':'0')+'</td></tr>';
  t+='<tr><th>RAZEM</th><th>'+j.total_id+'</th><th>'+(j.total_diff?'<b style=color:#b26b00>'+j.total_diff+'</b>':'0')+'</th></tr></table>';
  t+='<div style="margin-top:8px;padding:10px 12px;background:#fff8ec;border:1px solid #f0e0c0;border-radius:8px;font-size:12.5px">'
    +'<b>Co to random_indiv i czemu się różni?</b><br>'
    +'To pliki z <b>losowo dobranymi grupami osobników</b> (np. HG00158, HG00250…), tworzone przez '
    +'<code>random.sample()</code> <b>bez ustawionego seed</b>. Jest ich 1000 = 1000 iteracji '
    +'<b>testu permutacyjnego (rozkład zerowy / Monte Carlo)</b> — baza odniesienia „jak wygląda '
    +'współdzielenie mutacji przy losowym pogrupowaniu?”. Ponieważ losowanie nie ma seed, '
    +'<b>każde uruchomienie losuje inne osobniki</b> → dwa runy HyperFlow też by się różniły. '
    +'To <b>celowa losowość metody, nie różnica silników</b>. Wyniki deterministyczne (histogramy, '
    +'overlap, mapy) są identyczne bit-do-bit.</div>';
  t+='<details style=margin-top:8px><summary style="cursor:pointer;color:#1565c0">Szczegóły per (region × populacja) — '+j.per.length+' plików wynikowych</summary><table style=margin-top:6px><tr><th>Plik wynikowy</th><th>Identyczne</th><th>Różne</th></tr>';
  for(const p of j.per)t+='<tr><td>'+p.tarball+'</td><td>'+p.id+'</td><td>'+(p.diff?'<b style=color:#b26b00>'+p.diff+'</b>':'0')+'</td></tr>';
  t+='</table></details>';
  $('cmpout').innerHTML=t;
}
function tick(){refresh();loadPairs();}
tick();setInterval(tick,5000);
</script></body></html>"""

class H(BaseHTTPRequestHandler):
    def _send(self, code, ctype, body):
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            chips = "".join(f'<span class=chip onclick="setChip(this.dataset.p)" data-p="{p}">{lbl}</span>' for lbl,p in EXAMPLES)
            self._send(200, "text/html; charset=utf-8", HTML.replace("__CHIPS__", chips).encode())
        elif u.path == "/api/runs":
            self._send(200, "application/json", json.dumps(list_runs()).encode())
        elif u.path == "/api/stop":
            rid = parse_qs(u.query).get("id", [""])[0]
            self._send(200, "application/json", json.dumps(stop_run(rid)).encode())
        elif u.path == "/api/open":
            rel = parse_qs(u.query).get("p", [""])[0]
            target = (BASE / rel).resolve()
            if str(target).startswith(str(BASE)) and target.exists():
                subprocess.run(["open", str(target)])   # macOS: otwiera Finder
                self._send(200, "application/json", json.dumps({"ok": True}).encode())
            else:
                self._send(200, "application/json", json.dumps({"ok": False, "msg": "brak folderu"}).encode())
        elif u.path == "/api/open_pair":
            q = parse_qs(u.query); pid = q.get("pair", [""])[0]; side = q.get("side", [""])[0]
            p = {x["id"]: x for x in get_pairs()}.get(pid)
            path = p.get(side) if p and side in ("nf", "hf") else None
            if path and Path(path).exists():
                subprocess.run(["open", path])
                self._send(200, "application/json", json.dumps({"ok": True}).encode())
            else:
                self._send(200, "application/json", json.dumps({"ok": False, "msg": "brak folderu"}).encode())
        elif u.path == "/api/hf_runs":
            self._send(200, "application/json", json.dumps(list_hf_runs()).encode())
        elif u.path == "/api/stop_hf":
            self._send(200, "application/json", json.dumps(stop_hf(parse_qs(u.query).get("id", [""])[0])).encode())
        elif u.path == "/api/open_hf":
            cid = parse_qs(u.query).get("id", [""])[0]
            d = HF_INTEG / f"workflow-{cid}"
            if d.exists():
                subprocess.run(["open", str(d)]); self._send(200, "application/json", json.dumps({"ok": True}).encode())
            else:
                self._send(200, "application/json", json.dumps({"ok": False, "msg": "brak folderu"}).encode())
        elif u.path == "/api/pairs":
            self._send(200, "application/json", json.dumps(get_pairs()).encode())
        elif u.path == "/api/compare":
            pid = parse_qs(u.query).get("pair", [""])[0]
            self._send(200, "application/json", json.dumps(compare_pair(pid)).encode())
        elif u.path == "/file":
            p = parse_qs(u.query).get("p", [""])[0]
            target = (BASE / p).resolve()
            if not str(target).startswith(str(BASE)) or not target.exists():
                self._send(404, "text/plain", b"not found"); return
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            self._send(200, ctype, target.read_bytes())
        else:
            self._send(404, "text/plain", b"not found")
    def do_POST(self):
        u = urlparse(self.path)
        ln = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(ln) or b"{}")
        prompt = (data.get("prompt") or "").strip()
        model = data.get("model") or "gemini/gemini-2.5-flash"
        if not prompt:
            self._send(400, "application/json", json.dumps({"ok": False}).encode()); return
        if u.path == "/api/dry":
            self._send(200, "application/json", json.dumps(run_composer_dry(prompt, model)).encode())
        elif u.path == "/api/run":
            self._send(200, "application/json", json.dumps(launch_full(prompt, model, bool(data.get("fast")))).encode())
        elif u.path == "/api/run_hf":
            self._send(200, "application/json", json.dumps(launch_hyperflow(prompt, model)).encode())
        else:
            self._send(404, "text/plain", b"not found")

if __name__ == "__main__":
    print(f"GUI na http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
