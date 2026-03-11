FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Installer curl pour télécharger les polices
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip && \
    rm -rf /var/lib/apt/lists/*

# Télécharger les polices Poppins depuis Google Fonts
RUN mkdir -p /usr/share/fonts/truetype/poppins && \
    cd /tmp && \
    curl -sL "https://fonts.google.com/download?family=Poppins" -o poppins.zip && \
    unzip -o poppins.zip -d /usr/share/fonts/truetype/poppins/ && \
    rm poppins.zip && \
    fc-cache -f -v 2>/dev/null || true

# Installer les dépendances Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copier le code
COPY app.py .

ENV PORT=8080
EXPOSE 8080

CMD exec gunicorn --bind :$PORT --workers 2 --threads 4 --timeout 120 app:app
