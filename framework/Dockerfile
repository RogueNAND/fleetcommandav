FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install watchdog for hot-reload
RUN pip install --no-cache-dir watchdog[watchmedo]

COPY . /fleetcommand

# Run as non-root user (UID 1000) for correct file ownership on host volumes.
# Site-packages and bin must be writable for runtime editable installs (framework/libraries/).
RUN useradd -u 1000 -m fcav \
    && chown -R fcav /usr/local/lib/python3.13/site-packages/ /usr/local/bin/
USER fcav

# Watch framework and automations for changes
CMD ["watchmedo", "auto-restart", \
    "--directory=/fleetcommand", \
    "--directory=/modules", \
    "--pattern=*.py", \
    "--recursive", \
    "--", \
    "python", "-Xfrozen_modules=off", "/fleetcommand/main.py"]
