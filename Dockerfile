FROM python:3.11-slim-bookworm

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade -r requirements.txt

# 1. Copy the 'cogs' folder (Required for the bot to find extensions)
COPY cogs/ ./cogs/

# 2. Copy utility scripts (Matches docker_utils.py, plex_utils.py, etc.)
# This pattern excludes the redundant *_functions.py files
COPY *_utils.py ./

# 3. Copy the main bot application files
COPY bot.py config.py utils.py media_watcher_service.py __init__.py ./

CMD ["python", "bot.py"]