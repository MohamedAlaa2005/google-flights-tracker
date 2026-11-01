# Production Dockerfile for Google Flights Intelligence Tracker & Dashboard
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

# Set non-interactive and environment variables
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HEADLESS=true \
    PORT=3000 \
    CHROME_DEBUG_PORT=9222 \
    CHROME_PROFILE_DIR=/app/chrome_profile \
    CHROME_BIN=/usr/bin/google-chrome

WORKDIR /app

# Install Google Chrome Stable
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    gnupg \
    ca-certificates \
    curl \
    && wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/googlechrome-linux-keyring.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/googlechrome-linux-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Expose web dashboard port
EXPOSE 3000

# Default command: launch the FastAPI web app with the background 12:00 PM scheduler
CMD ["python", "web_app.py"]

