FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install watchfiles for hot-reload
RUN pip install --no-cache-dir watchfiles

COPY . /fleetcommand

# Run as non-root user (UID 1000) for correct file ownership on host volumes.
# Site-packages and bin must be writable for runtime editable installs (addons with pyproject.toml).
RUN useradd -u 1000 -m fcav \
    && chown -R fcav /usr/local/lib/python3.13/site-packages/ /usr/local/bin/
USER fcav

# Watcher runs sync/install, then watches for changes and restarts main.py
CMD ["python", "-Xfrozen_modules=off", "/fleetcommand/watcher.py"]
