FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/
RUN pip install -r requirements.txt

COPY main.py /app/

# Required:
# - GOOGLE_CREDS_JSON
# - MAILGUN_API_KEY
# - MAILGUN_DOMAIN            (e.g., mg.yourdomain.com)
# - EMAIL_FROM                (e.g., alerts@yourdomain.com)
# - EMAIL_TO                  (comma-separated)
# Optional:
# - SHEET_NAME (default "Trading Log")
# - LOG_TAB (default "log")
# - LOCAL_TZ (default "America/Denver")
# - EXIT_IF_EMPTY (default "false")
# - MAILGUN_BASE_URL (default "https://api.mailgun.net"; use "https://api.eu.mailgun.net" if your account is EU)

CMD ["python", "/app/main.py"]
