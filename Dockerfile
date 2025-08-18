FROM mcr.microsoft.com/playwright/python:v1.40.0

WORKDIR /app

COPY requirements.txt .

# Install dependencies, force-reinstall playwright to ensure correct path
RUN pip install --no-cache-dir -r requirements.txt --force-reinstall

COPY app.py .

# Ensure temp profile dir permissions
RUN mkdir -p /app/temp && chmod -R 777 /app/temp

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
