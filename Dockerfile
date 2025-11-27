FROM python:3.11-slim-bookworm

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade -r requirements.txt

# Copy the cogs folder (Essential for the bot extensions)
COPY cogs/ ./cogs/

# Copy all utility scripts (Matches docker_utils.py, plex_utils.py, etc.)
# This pattern conveniently excludes the redundant *_functions.py files
COPY *_utils.py ./

# Copy the main bot application files
COPY bot.py config.py utils.py media_watcher_service.py __init__.py ./

# Note: config.json is not copied here because it is mounted as a volume in docker-compose.yml.
# This keeps the image clean of secrets/local config.

CMD ["python", "bot.py"]