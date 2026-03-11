FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Télécharger les polices Poppins depuis GitHub
RUN mkdir -p /usr/share/fonts/truetype/poppins && \
    curl -sL -o /usr/share/fonts/truetype/poppins/Poppins-Regular.ttf \
      "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-Regular.ttf" && \
    curl -sL -o /usr/share/fonts/truetype/poppins/Poppins-Bold.ttf \
      "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-Bold.ttf" && \
    curl -sL -o /usr/share/fonts/truetype/poppins/Poppins-Medium.ttf \
      "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-Medium.ttf" && \
    curl -sL -o /usr/share/fonts/truetype/poppins/Poppins-Light.ttf \
      "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-Light.ttf" && \
    fc-cache -f -v 2>/dev/null || true

# Vérifier que les polices sont valides (pas des pages HTML d'erreur)
RUN python3 -c "\
import struct; \
f=open('/usr/share/fonts/truetype/poppins/Poppins-Regular.ttf','rb'); \
tag=struct.unpack('>I',f.read(4))[0]; f.close(); \
assert tag in (0x00010000, 0x74727565), f'Invalid font: {hex(tag)}'; \
print('Poppins fonts verified OK')"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

ENV PORT=8080
EXPOSE 8080

CMD exec gunicorn --bind :$PORT --workers 2 --threads 4 --timeout 120 app:app
