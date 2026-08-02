#!/usr/bin/env python3
"""Buduje prezentację slajdową (PDF, poziomo) o composerze 1000genome/Nextflow.
Uruchomienie (env 1000genome): python3 build_slides.py
Wymaga obrazów w tym katalogu: architektura-composer.png, os-czasu.png, weryfikacja-warianty-na-gen.png
"""
from fpdf import FPDF
from fpdf.fonts import FontFace
import matplotlib, os
FD = os.path.join(os.path.dirname(matplotlib.__file__),'mpl-data','fonts','ttf')
ACC=(21,101,192); GREEN=(46,125,50); DARK=(33,33,33); MUT=(110,110,110); LIGHT=(238,243,250)
pdf=FPDF(orientation='L',format='A4',unit='mm'); pdf.set_auto_page_break(False)
pdf.add_font('DV','',os.path.join(FD,'DejaVuSans.ttf')); pdf.add_font('DV','B',os.path.join(FD,'DejaVuSans-Bold.ttf')); pdf.add_font('DV','I',os.path.join(FD,'DejaVuSans-Oblique.ttf'))
W=297;H=210;M=16;CW=W-2*M
def header(t,n):
    pdf.set_fill_color(*ACC); pdf.rect(0,0,W,24,'F'); pdf.set_xy(M,6); pdf.set_font('DV','B',18); pdf.set_text_color(255,255,255); pdf.cell(CW-16,12,t)
    pdf.set_xy(W-22,7); pdf.set_font('DV','B',12); pdf.cell(14,10,str(n),align='R'); pdf.set_text_color(*DARK); pdf.set_xy(M,34)
def note(t):
    pdf.set_xy(M,H-16); pdf.set_font('DV','I',9.5); pdf.set_text_color(*MUT); pdf.multi_cell(CW,4.6,'Mów: '+t); pdf.set_text_color(*DARK)
def bullets(items,size=13.5,y=40,lh=8.5):
    pdf.set_xy(M,y)
    for it in items:
        pdf.set_font('DV','B',size); pdf.set_text_color(*ACC); x0=pdf.get_x(); pdf.cell(7,lh,'▸'); pdf.set_text_color(*DARK)
        pdf.set_font('DV','',size); pdf.set_x(x0+7); pdf.multi_cell(CW-7,lh,it); pdf.ln(1.5)
def table(headers,rows,widths,y=40,size=12,lh=6.5):
    pdf.set_xy(M,y); pdf.set_font('DV','',size); pdf.set_fill_color(*LIGHT)
    hs=FontFace(emphasis='BOLD',color=(255,255,255),fill_color=ACC)
    with pdf.table(col_widths=widths,text_align='LEFT',headings_style=hs,line_height=lh,width=sum(widths),cell_fill_color=LIGHT,cell_fill_mode='ROWS') as t:
        t.row(headers)
        for r in rows: t.row(r)
def bignote(t,color=GREEN,size=13):
    pdf.set_xy(M,H-24); pdf.set_font('DV','B',size); pdf.set_text_color(*color); pdf.multi_cell(CW,7,t); pdf.set_text_color(*DARK)

n=0
# TYTUŁ
pdf.add_page(); pdf.set_fill_color(*ACC); pdf.rect(0,0,W,H,'F'); pdf.set_text_color(255,255,255)
pdf.set_xy(M,72); pdf.set_font('DV','B',30); pdf.multi_cell(CW,14,'Composer 1000genome na Nextflow')
pdf.set_xy(M,102); pdf.set_font('DV','',16); pdf.multi_cell(CW,9,'Od pytania badawczego do uruchomionego workflow’u — agentowe AI dla nauki')
pdf.set_xy(M,150); pdf.set_font('DV','',13); pdf.multi_cell(CW,7,'Rozwija composer HyperFlow (Balis i in., AGH / Sano)')
pdf.set_xy(M,H-24); pdf.set_font('DV','I',12); pdf.multi_cell(CW,6,'Rafał Szepieniec'); pdf.set_text_color(*DARK)

n+=1; pdf.add_page(); header('Teza',n)
bullets(['Composer = agent: pytanie w języku naturalnym → uruchamialny workflow naukowy',
 'Ten sam ResearchIntent napędza DWA silniki: HyperFlow i Nextflow','Wynik naukowo IDENTYCZNY niezależnie od silnika',
 'Composer to specjalista domenowy; silnik to wymienny backend'],size=15,lh=11,y=44)
note('Z jednego zdania po PL/EN generujemy i uruchamiamy pełny pipeline — na Nextflow, reużywając mózg z wersji HyperFlow.')

n+=1; pdf.add_page(); header('Etapy pipeline’u',n)
pdf.image('architektura-composer.png',x=M,y=32,w=CW)
note('Pięć etapów. Tylko pierwszy używa LLM — reszta deterministyczna. LLM konfiguruje, ale nie dotyka danych.')

n+=1; pdf.add_page(); header('Co się dzieje na każdym etapie',n)
table(['Etap','Co robi','Co powstaje'],[
 ['INTERPRET','LLM + skille strukturyzują pytanie','intent (populacje, region, focus)'],
 ['MAP','intent → parametry i adresy do pobrania','--populations, extract.csv'],
 ['EXTRACT','tabix pobiera region z 1000 Genomes','VCF (tylko potrzebny kawałek)'],
 ['DAG','5 skryptów naukowych w kontenerach','macierze mutacji, częstości'],
 ['WYNIKI','pakowanie per (chromosom × populacja)','chrN-POP.tar.gz + wykresy']],[38,120,109],y=44,lh=9)
note('Region „BRCA1” zamienia się na współrzędne, tabix pobiera tylko ten fragment, dalej idzie oryginalny DAG 1000genome.')

n+=1; pdf.add_page(); header('Ciekawe optymalizacje',n)
table(['Optymalizacja','Gdzie występuje'],[
 ['Scatter-gather (równoległość individuals)','oba silniki (Nextflow: natywnie na kanałach)'],
 ['Ekstrakcja tabix — tylko region (~50× mniej transferu)','oba silniki'],['Cache obrazów kontenerów','oba silniki'],
 ['Automatyczne raporty wykonania (timeline, report, trace)','tylko Nextflow'],
 ['Wznawianie -resume (pomija zadania już policzone)','tylko Nextflow']],[128,139],y=44,lh=9,size=11.5)
note('Część optymalizacji jest wspólna dla obu silników; raporty i wznawianie -resume to cechy specyficzne dla Nextflow.')

n+=1; pdf.add_page(); header('Skille / wiedza (reużywane przez oba silniki)',n)
table(['Skill','Wiedza domenowa'],[['SKILL.md','rola i rekomendowany workflow'],
 ['populations.md','kody populacji 1000G (AFR, EUR, EAS…) + liczności'],['genomic-regions.md','geny → współrzędne (BRCA1, BRCA2, HLA)'],
 ['research-contexts.md','choroba → sugerowany region'],['data-sources.md','wzorce URL 1000 Genomes (FTP/S3/GCS)']],[70,197],y=44,lh=9)
note('Kluczowy genomic-regions.md — dzięki niemu „BRCA1” staje się współrzędnymi. Te same skille obsługują oba silniki.')

n+=1; pdf.add_page(); header('Różnice: co zreusowane, co nowe',n)
table(['Zreusowane 1:1','Dostosowane / nowe'],[
 ['interpreter LLM, ResearchIntent, 5 skilli','backend composer.py (intent → params + extract.csv)'],
 ['5 skryptów naukowych + obraz worker','DAG main.nf w Nextflow DSL2 (kanały)'],
 ['URL-e tabix (1000 Genomes)','obraz worker-nf (+bash), runner nextflow run']],[128,139],y=46,lh=9)
note('Cały mózg i cała nauka poszły 1:1. Dostosować trzeba było tylko sposób opisania DAG-u i uruchomienia — ok. 2 nowe pliki.')

n+=1; pdf.add_page(); header('Różnice faza po fazie',n)
table(['Faza','Różni się?','Charakter różnicy'],[
 ['1. INTERPRET','nie','Wspólny komponent interpretacji (LLM + skille); intent identyczny'],
 ['2. MAP','częściowo','HyperFlow generuje materializowany graf workflow.json; Nextflow: statyczny opis (main.nf) + parametry runtime'],
 ['3. EXTRACT','częściowo','Ta sama metoda (tabix, te same źródła); różni się umiejscowienie kroku i pomiar wolumenu danych'],
 ['4. EXECUTE','zasadniczo','Model równoległości: HyperFlow kalibruje liczbę zadań po pomiarze; Nextflow rozwija graf dynamicznie na kanałach'],
 ['5. WYNIKI','nie','Artefakty identyczne; Nextflow dodatkowo generuje raporty wykonania automatycznie']],[34,32,201],y=44,lh=8,size=11)
note('Warstwa interpretacji jest wspólna. Różnice koncentrują się w sposobie opisu grafu i modelu równoległości — wynik identyczny.')

n+=1; pdf.add_page(); header('Równoległość: scatter-gather (krok EXECUTE)',n)
bullets(['Wyekstrahowany VCF: ~2369 wierszy (wariantów)','Dzielimy na kawałki (np. 10) → 10× individuals liczonych RÓWNOLEGLE (scatter)',
 'individuals_merge scala 10 wyników w jeden (gather)','W Nextflow: wzorzec opisany raz w main.nf; silnik sam tworzy N zadań z przepływu danych (kanały)',
 'workflow.json (HyperFlow) nie jest w tym podejściu używany — to alternatywa, nie wąskie gardło'],size=13,lh=9.5,y=42)
note('MAP zamienia intent w konkretne adresy. Potem dzielimy pracę na starcie, liczymy równolegle i zbieramy.')

n+=1; pdf.add_page(); header('Ile zadań? Ręcznie vs automatycznie',n)
table(['Podejście','Jak dobiera liczbę zadań J'],[
 ['HyperFlow','„zmierz → dobierz J” automatycznie: 136 wierszy→J=1, 166 tys.→J=51 (nie przesadza)'],
 ['Nextflow (obecnie)','J z parametru --ind_jobs (domyślnie 10) — stałe, ustawiane ręcznie'],
 ['Nextflow (możliwe)','proces liczy wiersze → dynamiczny podział; operatory splitText/splitFastq']],[52,215],y=46,lh=9,size=11.5)
bignote('Auto-kalibracja nie jest wbudowana w Nextflow — wymaga dodania (proces mierzący wolumen). Dobry następny krok.',ACC,12)

n+=1; pdf.add_page(); header('Reuse interpretera: te same prompty (1/2)',n)
table(['Prompt (pełny)','HyperFlow (oczekiwany)','Nextflow','✓'],[
 ['Do European and African populations show different patterns of shared deleterious mutations on chromosome 22? This is relevant for understanding population-specific disease risks.','comparison · [EUR,AFR] · chr22 · deleterious','comparison · [EUR,AFR] · chr22 · deleterious','✓'],
 ['What is the genome-wide null distribution of mutation sharing across all populations? This provides the statistical baseline needed to evaluate whether disease-associated mutations show unusual sharing patterns.','multi · [AFR,ALL,AMR,EAS,EUR,GBR,SAS] · all_variants','multi · [AFR,AMR,EAS,EUR,SAS] · all_variants','~'],
 ['Do East Asian populations show distinct mutation sharing patterns in the HLA immune region that could explain population-specific autoimmune disease susceptibilities?','region · [EAS] · HLA · all_variants','region · [EAS] · HLA · all_variants','✓']],[106,76,68,17],y=32,lh=5.0,size=9)
note('Interpreter jest WSPÓŁDZIELONY (import z HyperFlow). To test reużycia i zmienności LLM — nie różnicy silników. Kolumna HyperFlow = expected_intent z cases.yaml.')

n+=1; pdf.add_page(); header('Reuse interpretera: te same prompty (2/2)',n)
table(['Prompt (pełny)','HyperFlow (oczekiwany)','Nextflow','✓'],[
 ['Compare the patterns of genetic variation between European and African populations in the HLA region. Are there population-specific variants that might explain differences in immune response?','comparison · [EUR,AFR] · HLA · all_variants','comparison · [EUR,AFR] · HLA · all_variants','✓'],
 ['What variants exist in the BRCA1 and BRCA2 breast cancer genes across different populations? Are there population-specific patterns that might inform targeted screening recommendations?','multi · [AFR,EUR,EAS,SAS,AMR] · BRCA1,BRCA2 · all_variants','multi · [AFR,AMR,EAS,EUR,SAS] · BRCA1,BRCA2 · all_variants','✓'],
 ['Analyze BRCA1 gene variants in the British population.','region · [GBR] · BRCA1 · all_variants','region · [GBR] · BRCA1 · all_variants','✓']],[106,76,68,17],y=32,lh=5.0,size=9)
bignote('5/6 identycznych — jedyna różnica to zmienność LLM (genome-wide: ALL, GBR). Interpreter jest ten sam kod.')

n+=1; pdf.add_page(); header('Wynik: weryfikacja równoważności (md5)',n)
pdf.set_xy(M,32); pdf.set_font('DV','',11.5); pdf.multi_cell(CW,5.5,'Ten sam prompt (BRCA1, GBR) → HyperFlow vs Nextflow → porównanie bit-do-bit (md5) 4002 plików')
table(['Kategoria wyników','Identyczne','Różne'],[
 ['Histogramy mutation_overlap','1000','0'],
 ['Mutation_overlap + pozostałe deterministyczne','2000','0'],
 ['map_variations, mutation_index','2','0'],
 ['random_indiv (losowe próbki)','0','1000']],[137,60,50],y=44,lh=9,size=12)
bignote('3002/4002 deterministycznych wyników identycznych bit-do-bit. 1000 różnic = random.sample BEZ seed → losowość skryptu, niezależna od silnika (dwa runy HyperFlow też by się różniły).',ACC,10.5)

n+=1; pdf.add_page(); header('Weryfikacja: dane wyników pokrywają się',n)
pdf.image('weryfikacja-warianty-na-gen.png',x=M+34,y=32,w=CW-68)
note('Warianty na gen (deterministyczny wynik) — HyperFlow i Nextflow identyczne (md5 zgodny). Histogramy mutation_overlap są zerowe, bo region BRCA1 jest mały — stąd wcześniejsze puste wykresy.')

n+=1; pdf.add_page(); header('Zakres weryfikacji + dlaczego histogramy zerowe',n)
table(['Aspekt','Ustalenie'],[
 ['Równoważność (wyniki deterministyczne)','3002/4002 identyczne BIT-DO-BIT (md5) — port nie zmienia nauki'],
 ['1000 plików różnych','wyłącznie random_indiv — random.sample BEZ seed; niezależne od silnika'],
 ['Histogramy mutation_overlap','zerowe — analiza liczy współdzielenie mutacji SZKODLIWYCH (rzadkie z natury)'],
 ['Niezerowe histogramy','wymagają skali genome-wide → klaster (pełne HLA = VCF 1,69 GB → OOM na laptopie)']],[68,199],y=44,lh=8,size=11)
bignote('Równoważność UDOWODNIONA na danych deterministycznych. Zerowe histogramy to własność analizy (biologia) + limit laptopa, nie błąd portu. Skala genome-wide = osobny etap na klastrze.',ACC,10.5)

n+=1; pdf.add_page(); header('Wynik: metryki realnego runu (Nextflow)',n)
pdf.set_xy(M,32); pdf.set_font('DV','',11.5); pdf.multi_cell(CW,5.5,'Prompt: „Analyze BRCA1 gene variants comparing European and African populations”')
table(['Proces','Czas','%CPU'],[['EXTRACT (tabix)','26 s','3%'],['INDIVIDUALS ×10 (RÓWNOLEGLE)','~2 min każde','~93%'],
 ['INDIVIDUALS_MERGE','31 s','31%'],['SIFTING','<1 s','77%'],['MUTATION_OVERLAP ×2 (EUR, AFR)','8 s','53%'],
 ['FREQUENCY ×2 (EUR, AFR)','51 s','80%']],[110,90,67],y=48,lh=8,size=11.5)
bignote('18 zadań, 0 błędów — 10 zadań individuals liczonych naraz (widać równoległość)')

n+=1; pdf.add_page(); header('Oś czasu przebiegu — równoległość',n)
pdf.image('os-czasu.png',x=M,y=30,w=CW)
note('To samo widać w raporcie na żywo (timeline.html). Individuals liczą się równolegle; przy limicie ~8 CPU część czeka na wolny slot — realna równoległość.')

n+=1; pdf.add_page(); header('Więcej przebiegów composera',n)
table(['Populacje','Region','Pliki wynikowe'],[['EUR, AFR','BRCA1','4'],['EAS, SAS','(dane testowe)','4'],
 ['GBR, EUR','(dane testowe)','4'],['EUR, AFR','BRCA1 + BRCA2 (multi-region)','8']],[70,120,77],y=46,lh=9,size=12)
bignote('Różne populacje, single i multi-region. Liczba plików = regiony × populacje × 2 analizy.',ACC,12)

n+=1; pdf.add_page(); header('Skalowanie: single vs multi-region',n)
pdf.set_xy(M,32); pdf.set_font('DV','',11); pdf.multi_cell(CW,5.2,'BRCA1 (EUR+AFR) vs BRCA1+BRCA2 (EUR+AFR) — ten sam sprzęt (laptop)')
table(['Metryka','single (BRCA1)','multi (BRCA1+BRCA2)','wzrost'],[
 ['Liczba zadań','18','36','×2'],['Praca CPU (suma)','23 min','47,5 min','×2'],
 ['Wall-clock (realny czas)','6,3 min','9,1 min','×1,4']],[70,66,80,51],y=44,lh=8.5,size=12)
pdf.set_xy(M,88); pdf.set_font('DV','',10.5); pdf.set_text_color(*MUT)
pdf.multi_cell(CW,5,'Rozkład zadań multi-region: EXTRACT ×2 · INDIVIDUALS ×22 · MERGE ×2 · SIFTING ×2 · MUTATION_OVERLAP ×4 · FREQUENCY ×4  (regiony × populacje)')
pdf.set_text_color(*DARK)
bignote('Zadania i praca CPU podwajają się, ale wall-clock rośnie tylko 1,4× — skalowanie sublinearne dzięki równoległości.')

n+=1; pdf.add_page(); header('Co dalej / dalsze rozwijanie',n)
bullets(['GOTOWE: weryfikacja równoważności (md5) + reuse interpretera + realny run z metrykami i osią czasu',
 'Dyrygent: bramka zatwierdzenia (podgląd planu przed uruchomieniem) + routing między composerami',
 'Automatyczny dobór liczby zadań (ind_jobs) — w Nextflow do dodania (proces mierzący wolumen)',
 'Benchmark na wspólnym klastrze; obsługa chrX/chrY w ekstrakcji'],size=13.5,lh=10.5,y=44)
note('Trzy kierunki: warstwa dyrygenta (bramka + routing), automatyczna kalibracja zadań i wspólny benchmark.')

pdf.output('prezentacja-composer-nextflow.pdf'); print('OK slajdów:', pdf.page)
