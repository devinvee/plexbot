# Use an official Python runtime as a parent image
FROM python:3.11-slim-bookworm

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r pip install -r requirements.txt

# Copy all Python files (*.py) from your repo root to /app in container
COPY *.py .

# The command to run the bot when the container starts
CMD ["python", "bot.py"]