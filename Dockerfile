FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .

# Install MySQL/PostgreSQL dev dependencies
RUN apt-get update && apt-get install -y \
    pkg-config \
    default-libmysqlclient-dev \
    build-essential \
    libpq-dev \
 && rm -rf /var/lib/apt/lists/*

# Replace opencv-python with headless version
RUN pip install --no-cache-dir -r requirements.txt && \
    pip uninstall -y opencv-python && \
    pip install --no-cache-dir opencv-python-headless

COPY . .

COPY django.sh /django.sh
RUN chmod +x /django.sh

EXPOSE 6200