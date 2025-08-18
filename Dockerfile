FROM ubuntu:24.04

# Install Python and pip (Ubuntu 24.04 has Python 3.12)
RUN apt-get update && apt-get install -y python3.12 python3-pip python3-venv && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip3 install --no-cache-dir -r requirements.txt

RUN playwright install --with-deps chromium

COPY app.py .

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
