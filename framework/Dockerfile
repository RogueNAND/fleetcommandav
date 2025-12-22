FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install watchdog for hot-reload
RUN pip install --no-cache-dir watchdog[watchmedo]

COPY . /fleetcommand

# Watch framework and automations for changes
CMD ["watchmedo", "auto-restart", \
     "--directory=/fleetcommand", \
     "--directory=/modules", \
     "--pattern=*.py", \
     "--recursive", \
     "--", \
     "python", "/fleetcommand/main.py"]
