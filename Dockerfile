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
    chromium \
    chromium-driver \
    ffmpeg \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/tndnews_static /app/staticfiles

COPY django.sh /django.sh
RUN chmod +x /django.sh

EXPOSE 6200
