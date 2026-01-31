FROM busybox

LABEL description="1000genome workflow input data container"

# Create data directory structure
RUN mkdir -p /data/20130502 /data/populations

# Copy compressed VCF and annotation files
COPY data/20130502/*.vcf.gz /data/20130502/
COPY data/20130502/columns.txt /data/20130502/

# Copy population files (sample ID lists)
COPY data/populations/ /data/populations/

# Copy workflow definition
COPY data/workflow.json /data/

# Copy prepare script
COPY prepare_data.sh /

VOLUME /data

# Default: list contents
CMD ["sh", "-c", "ls -lh /data/ && ls -lh /data/20130502/ && ls -lh /data/populations/"]
