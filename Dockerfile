# TieBack Studio — container image for Equinor Radix (or any Docker host).
# Radix rules: run as a non-root user given by numeric ID, listen on the port named in radixconfig.yaml.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/radix \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    TIEBACK_SHARE_DIR=/app/data/shared_designs

WORKDIR /app

# dependencies first, so a code change does not reinstall them
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# non-root user (Radix refuses to start a container running as root);
# the app writes only to $HOME (Streamlit) and /app/data (shared designs)
RUN groupadd -g 1001 radix-non-root-group \
 && useradd -u 1001 -g 1001 -m -d /home/radix radix-non-root-user \
 && mkdir -p /app/data/shared_designs \
 && chown -R 1001:1001 /app/data /home/radix

USER 1001

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=4).status == 200 else 1)"

CMD ["streamlit", "run", "tieback_app.py", \
     "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true", \
     "--server.fileWatcherType=none"]
