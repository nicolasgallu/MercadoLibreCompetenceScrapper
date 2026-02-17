# Usa tu imagen base de Python
FROM python:3.10-slim

# Establecer el directorio de trabajo en /app
WORKDIR /app

# Configurar la zona horaria (Buena práctica, se mantiene)
RUN apt-get update && apt-get install -y tzdata \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=America/Argentina/Buenos_Aires
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Copiar el contenido del proyecto al contenedor
COPY . /app

# Instalar dependencias (asumiendo que 'gunicorn' está en requirements.txt)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# El watchdog no es necesario en producción, se omite.

# Exponer el puerto (es solo informativo en Cloud Run, pero se mantiene)
EXPOSE 8080

CMD ["python", "-m", "gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--worker-class", "gthread", "--timeout", "300", "main:app"]