# Cienki obraz pochodny worker 1000genome dla Nextflow.
# Nextflow wymaga bash w kontenerze; oryginalny obraz (Alpine) ma tylko sh
# i entrypoint dumb-init, ktory koliduje z wrapperem Nextflow.
# Dodajemy bash i czyscimy entrypoint. Nauka (skrypty) bez zmian.
FROM hyperflowwms/1000genome-worker:1.1-latest
RUN apk add --no-cache bash
# TRYB SZYBKI (opcjonalny): frequency.py czyta liczbe iteracji Monte Carlo ze zmiennej N_RUNS.
# Domyslnie 1000 (jak oryginal) -> bez N_RUNS zachowanie identyczne. Nextflow moze podac mniej.
RUN sed -i "s/^n_runs = 1000/n_runs = int(os.environ.get('N_RUNS', 1000))/" /1000genome/scripts/frequency.py
# PORT FIXU Z HYPERFLOW (upstream commit ee112c1 "Stream the VCF in individuals.py"):
# stara wersja wczytywala caly VCF do pamieci (readfile) i trzymala kolumny genotypow
# w liscie -> ~4.7 GB RAM/job na regionie HLA, przy rownoleglosci OOM-kill = brak wynikow.
# Nowa wersja streamuje plik przez itertools.islice (peak ~19 MB/job), wynik bajtowo
# identyczny. To ta sama poprawka co na HyperFlow -> silnik wymienny, nauka ta sama.
COPY individuals.streaming.py /1000genome/scripts/individuals.py
ENTRYPOINT []
