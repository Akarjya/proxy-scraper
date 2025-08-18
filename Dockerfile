FROM ubuntu:24.04

# Install Python and Playwright deps (updated for Ubuntu 24.04 t64 packages)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 python3-pip python3-venv \
    fonts-liberation libasound2t64 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 libcairo2 libcups2 libdbus-1-3 libdrm2 libgbm1 libglib2.0-0 libgtk-3-0 libnspr4 libnss3 libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 libxrender1 xdg-utils \
    libpangocairo-1.0-0 libpangoft2-1.0-0 libwayland-client0 libwayland-egl1 libxkbcommon0 libwoff1 libhyphen0 libwebpdemux2 libenchant-2-2 libevdev2 libgudev-1.0-0 libwacom-common libwacom9 libinput10 libjpeg-turbo8 liblcms2-2 libopenjp2-7 libpng16-16 libharfbuzz0b libthai0 libfreetype6 libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create virtual env to avoid PEP 668
RUN python3.12 -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"

COPY requirements.txt .

# Install packages in venv
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright with deps (Ubuntu 24.04 compatible)
RUN playwright install --with-deps chromium

COPY app.py .

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
