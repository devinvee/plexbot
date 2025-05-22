# Use an official Python runtime as a parent image
FROM python:3.11-slim-bookworm

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy all Python files and the config.json into the container's /app
# This includes bot.py, media_watcher_service.py, and now utils.py
COPY *.py .
COPY config.json .

# The command to run the bot when the container starts
CMD ["python", "bot.py"]