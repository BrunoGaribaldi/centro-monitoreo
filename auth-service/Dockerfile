FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
# companies.yml se monta como volumen en runtime, no va en la imagen
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "19100", "--no-access-log"]
