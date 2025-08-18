FROM mcr.microsoft.com/playwright/python:v1.40.0

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

RUN playwright install --with-deps chromium

RUN playwright install --with-deps firefox  # Optional, kept for backup

CMD uvicorn app:app --host 0.0.0.0 --port $PORT
