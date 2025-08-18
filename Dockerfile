FROM ubuntu:24.04

# Install Python and deps
RUN apt-get update && apt-get install -y \
    python3.12 python3-pip python3-venv \
    fonts-liberation libasound2 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 libcairo2 libcups2 libdbus-1-3 libdrm2 libgbm1 libglib2.0-0 libgtk-3-0 libnspr4 libnss3 libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 libxrender1 xdg-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create and activate virtual environment
RUN python3.12 -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"

COPY requirements.txt .

# Install Python packages in virtual env
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright with deps
RUN playwright install --with-deps chromium

COPY app.py .

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
