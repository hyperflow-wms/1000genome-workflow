#!/usr/bin/env nextflow
/*
 * 1000genome workflow — wierny port z HyperFlow na Nextflow.
 *
 * Odtwarza DAG:
 *   chunk_vcf -> individuals (scatter) -> individuals_merge (gather) ┐
 *   sifting                                                          ├─> mutation_overlap  (chrom × populacja)
 *                                                                    └─> frequency         (chrom × populacja)
 *
 * Nauka jest reużyta 1:1 — kazdy process wola ORYGINALNY skrypt 1000genome
 * z obrazu hyperflowwms/1000genome-worker (te same pliki co HyperFlow).
 */
nextflow.enable.dsl = 2

// ---- Parametry (nadpisywalne z CLI / composera) ----
params.data_csv         = "${projectDir}/testdata/data.csv"
params.data_dir         = "${projectDir}/testdata"
params.columns          = "${projectDir}/testdata/columns.txt"
params.populations_dir  = "${projectDir}/testdata/populations"
params.populations      = "GBR"          // lista przez przecinek, np. "EUR,AFR"
// ind_jobs i ind_max_forks poda composer z recommend_parallelism (RFC-004).
// Ponizsze wartosci to tylko awaryjne domyslne dla recznego uruchomienia.
params.ind_jobs         = 10             // ile chunkow individuals na chromosom
params.ind_max_forks    = 2              // limit rownoleglych zadan individuals
params.task_mem         = null           // szacowany szczyt pamieci na zadanie, np. "220MB"
params.max_variants     = 0              // TRYB SZYBKI: limit wariantow do liczenia (0 = bez limitu)
params.n_runs           = 0              // TRYB SZYBKI: iteracje Monte Carlo we frequency (0 = domyslne 1000)
params.outdir           = "${projectDir}/results"

// GENERACJA DANYCH (faza EXTRACT): jesli podany, dane sa ekstrahowane tabixem
// z publicznego 1000 Genomes zamiast z pre-wygenerowanego data.csv.
// Format extract.csv: chrom,region,name   np.  17,17:43044295-43125483,brca1
params.extract_csv      = null

SCRIPTS  = "/1000genome/scripts"                     // skrypty w obrazie worker
FTP_BASE = "https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/release/20130502"
HTSLIB   = "community.wave.seqera.io/library/htslib:1.21--ff8e28a189fbecaa"

// ============================================================================
// PROCESSY — kazdy owija oryginalny skrypt
// ============================================================================

process EXTRACT {
    // Faza EXTRACT — generuje dane: tabix wyciaga region z publicznego 1000 Genomes.
    // To ten sam mechanizm co faza EXTRACT composera HyperFlow (te same URL-e).
    tag "chr${chrom}:${region}"
    container HTSLIB
    publishDir "${params.outdir}/extracted", mode: 'copy'
    input:
        tuple val(chrom), val(region), val(name)
    output:
        tuple val(chrom), path("ALL.chr${chrom}.${name}.vcf"),
              path("ALL.chr${chrom}.${name}.annotation.vcf"), env('TOTAL')
    script:
    def geno_url = "${FTP_BASE}/ALL.chr${chrom}.phase3_shapeit2_mvncall_integrated_v5b.20130502.genotypes.vcf.gz"
    def ann_url  = "${FTP_BASE}/supporting/functional_annotation/filtered/ALL.chr${chrom}.phase3_shapeit2_mvncall_integrated_v5.20130502.sites.annotation.vcf.gz"
    """
    tabix -h ${geno_url} ${region} > ALL.chr${chrom}.${name}.vcf
    tabix -h ${ann_url}  ${region} > ALL.chr${chrom}.${name}.annotation.vcf
    TOTAL=\$(grep -vc '^#' ALL.chr${chrom}.${name}.vcf || true)
    echo "= chr${chrom} ${name}: \$TOTAL wariantow"
    """
}

process ANNOTATE {
    // Port Step 2.5 z HyperFlow extract-data.sh (commit b1d62eb "Annotate extracted
    // genotype VCFs with rs IDs"). Genotypowy VCF 1000G phase3 ma '.' w kolumnie ID;
    // numery rs sa TYLKO w pliku adnotacji (sites), po pozycji. mutation_overlap.py
    // i frequency.py matchuja warianty osobnika po rs ID -> bez adnotacji wyniki sa
    // PUSTE (zera). Ten proces przepisuje ID genotypu z annotation.vcf matchujac
    // CHROM+POS+REF+ALT. Nauka reuzyta 1:1 (ten sam python co HyperFlow).
    // Uzywa obrazu worker (domyslny z configu) — ma python3; kontener htslib nie ma.
    tag "chr${chrom}"
    input:
        tuple val(chrom), path(geno), val(total), path(annotation)
    output:
        tuple val(chrom), path("annot.chr${chrom}.vcf"), val(total), path(annotation)
    script:
    """
    python3 - ${geno} ${annotation} annot.chr${chrom}.vcf <<'PY'
    import sys
    geno, annot, out = sys.argv[1], sys.argv[2], sys.argv[3]
    ids = {}
    with open(annot) as f:
        for line in f:
            if line.startswith('#'):
                continue
            c = line.rstrip('\\n').split('\\t')
            if len(c) >= 5 and c[2] not in ('', '.'):
                ids[(c[0], c[1], c[3], c[4])] = c[2]
    n_tot = n_ann = 0
    with open(geno) as fin, open(out, 'w') as fout:
        for line in fin:
            if line.startswith('#'):
                fout.write(line)
                continue
            c = line.rstrip('\\n').split('\\t')
            if len(c) >= 5:
                n_tot += 1
                r = ids.get((c[0], c[1], c[3], c[4]))
                if r:
                    c[2] = r
                    n_ann += 1
            fout.write('\\t'.join(c) + '\\n')
    print("= chr${chrom}: %d/%d wariantow zaanotowanych rs ID" % (n_ann, n_tot))
    PY
    """
}

process CHUNK_VCF {
    // individuals.py czyta caly input przez readlines(); dla gestych regionow
    // najpierw robimy male pliki z rekordami wariantow, bez naglowkow VCF.
    tag "chr${chrom}:${counter}-${stop}"
    input:
        tuple val(chrom), path(vcf), val(counter), val(stop), val(chunk_size),
              val(total), path(columns)
    output:
        tuple val(chrom), path("chunk.chr${chrom}.${counter}-${stop}.vcf"),
              val(counter), val(stop), val(chunk_size), path("columns.txt")
    script:
    """
    awk -v counter=${counter} -v stop=${stop} '
      /^#/ { next }
      { variant++; if (variant >= counter && variant < stop) print }
    ' ${vcf} > chunk.chr${chrom}.${counter}-${stop}.vcf
    """
}

process INDIVIDUALS {
    tag "chr${chrom}:${counter}-${stop}"
    maxForks params.ind_max_forks.toInteger()
    input:
        tuple val(chrom), path(vcf), val(counter), val(stop), val(chunk_size), path(columns)
    output:
        tuple val(chrom), path("chr${chrom}n-${counter}-${stop}.tar.gz")
    script:
    """
    python3 ${SCRIPTS}/individuals.py ${vcf} ${chrom} 0 ${chunk_size} ${chunk_size}
    mv chr${chrom}n-0-${chunk_size}.tar.gz chr${chrom}n-${counter}-${stop}.tar.gz
    """
}

process INDIVIDUALS_MERGE {
    tag "chr${chrom}"
    input:
        tuple val(chrom), path(chunks)
    output:
        tuple val(chrom), path("chr${chrom}n.tar.gz")
    script:
    """
    python3 ${SCRIPTS}/individuals_merge.py ${chrom} ${chunks}
    """
}

process SIFTING {
    tag "chr${chrom}"
    input:
        tuple val(chrom), path(annotation)
    output:
        tuple val(chrom), path("sifted.SIFT.chr${chrom}.txt")
    script:
    """
    python3 ${SCRIPTS}/sifting.py ${annotation} ${chrom}
    """
}

process MUTATION_OVERLAP {
    tag "chr${chrom}:${pop}"
    publishDir "${params.outdir}", mode: 'copy'
    input:
        tuple val(chrom), path(merged), path(sifted), val(pop), path(popfile), path(columns)
    output:
        path "chr${chrom}-${pop}.tar.gz"
    script:
    """
    python3 ${SCRIPTS}/mutation_overlap.py -c ${chrom} -pop ${pop}
    """
}

process FREQUENCY {
    tag "chr${chrom}:${pop}"
    publishDir "${params.outdir}", mode: 'copy'
    input:
        tuple val(chrom), path(merged), path(sifted), val(pop), path(popfile), path(columns)
    output:
        path "chr${chrom}-${pop}-freq.tar.gz"
    script:
    def nr = (params.n_runs as int) > 0 ? "N_RUNS=${params.n_runs} " : ""   // TRYB SZYBKI: mniej iteracji Monte Carlo
    """
    ${nr}python3 ${SCRIPTS}/frequency.py -c ${chrom} -pop ${pop}
    """
}

// ============================================================================
// WORKFLOW — okablowanie DAG-u
// ============================================================================

// Pozyskanie danych, wspolne dla obu wejsc (-entry extract i pelnego runu).
// Emituje (chrom, vcf, total, annotation) po adnotacji rs ID.
workflow acquire {
    main:

    // 1) Zrodlo danych: albo EXTRACT (generacja tabixem), albo pre-wygenerowany data.csv.
    //    Oba dają ten sam ksztalt kanalu: (chrom, vcf, total, annotation).
    if (params.extract_csv) {
        extract_in = Channel
            .fromPath(params.extract_csv)
            .splitCsv()
            .map { row -> tuple(row[0].trim(), row[1].trim(), row[2].trim()) }

        rows = EXTRACT(extract_in)
            .map { chrom, vcf, annotation, total ->
                tuple(chrom, vcf, total as int, annotation)
            }
    } else {
        rows = Channel
            .fromPath(params.data_csv)
            .splitCsv()
            .map { row ->
                def vcf_name        = row[0].trim()
                def total           = row[1].trim() as int
                def annotation_name = row[2].trim()
                // chrom z nazwy pliku: ALL.chr17.brca1.vcf -> "17"
                def m = (vcf_name =~ /chr([0-9XY]+)/)
                def chrom = m ? m[0][1] : 'NA'
                tuple(chrom, file("${params.data_dir}/${vcf_name}"), total,
                      file("${params.data_dir}/${annotation_name}"))
            }
    }

    // 1b) ANOTACJA rs ID: bez tego mutation_overlap/frequency zwracaja puste dane.
    //     Obejmuje obie sciezki (EXTRACT i testdata). Ksztalt kanalu bez zmian.
    rows = ANNOTATE(rows)

    emit:
    rows
}

// Faza EXTRACT sama: pozyskuje dane i zapisuje zmierzone liczby wariantow.
// Composer czyta measurements.csv, liczy ind_jobs/max_parallelism przez
// recommend_parallelism i dopiero wtedy startuje pelny run — maxForks wiaze
// sie przy starcie procesu i nie da sie go ustawic z kanalu.
workflow extract {
    acquire()
    acquire.out
        .map { chrom, vcf, total, annotation -> "${chrom},${total}" }
        .collectFile(name: 'measurements.csv', storeDir: params.outdir, newLine: true)
}

workflow {
    columns_file = file(params.columns)

    acquire()
    rows = acquire.out

    // 2) SCATTER: rozbij kazdy chromosom na chunki individuals.
    //    Podzial na rowne czesci sufitem — ta sama arytmetyka co generator
    //    HyperFlow (step = ceil(total / ind_jobs)), zeby oba silniki tnaly
    //    identycznie dla tego samego ind_jobs.
    ind_input = rows.flatMap { chrom, vcf, total_raw, annotation ->
        int mv = params.max_variants as int
        int total = (mv > 0) ? Math.min(total_raw, mv) : total_raw   // TRYB SZYBKI: limit wariantow
        int ind_jobs = Math.max(1, params.ind_jobs as int)
        int step = Math.max(1, (int) Math.ceil(total / (double) ind_jobs))
        def chunks = []
        int counter = 1
        while (counter <= total) {
            int stop = Math.min(counter + step, total + 1)
            int chunk_size = Math.max(0, stop - counter)
            chunks << tuple(chrom, vcf, counter, stop, chunk_size, total, columns_file)
            counter = stop
        }
        return chunks
    }

    vcf_chunks = CHUNK_VCF(ind_input)
    individuals_out = INDIVIDUALS(vcf_chunks)

    // 3) GATHER: zbierz chunki per chromosom -> merge
    merged = INDIVIDUALS_MERGE(individuals_out.groupTuple())

    // 4) SIFTING: osobna galaz (chrom, annotation)
    sift_input = rows.map { chrom, vcf, total, annotation -> tuple(chrom, annotation) }
    sifted = SIFTING(sift_input)

    // 5) Iloczyn kartezjanski: (chrom -> merged+sifted) × populacje
    pops = Channel
        .fromList(params.populations.split(',').collect { it.trim() })
        .map { pop -> tuple(pop, file("${params.populations_dir}/${pop}")) }

    analysis_input = merged
        .join(sifted)                       // (chrom, merged, sifted)
        .combine(pops)                      // (chrom, merged, sifted, pop, popfile)
        .map { chrom, merged_f, sifted_f, pop, popfile ->
            tuple(chrom, merged_f, sifted_f, pop, popfile, columns_file)
        }

    MUTATION_OVERLAP(analysis_input)
    FREQUENCY(analysis_input)
}
