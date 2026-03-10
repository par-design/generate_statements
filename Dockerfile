FROM python:3.12-slim

# Empêcher la création de fichiers .pyc et activer les logs en temps réel
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Installer les dépendances
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copier le code
COPY app.py .

# Port par défaut de Cloud Run
ENV PORT=8080
EXPOSE 8080

# Lancer avec Gunicorn (recommandé pour Cloud Run)
CMD exec gunicorn --bind :$PORT --workers 2 --threads 4 --timeout 120 app:app
