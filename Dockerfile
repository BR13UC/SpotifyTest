FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 10001 --create-home statify \
    && mkdir /data \
    && chown statify:statify /data

COPY app ./app
COPY main.py ./main.py

USER statify
ENV STATIFY_DATA_DIR=/data STATIFY_HOST=0.0.0.0 PYTHONUNBUFFERED=1
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "120", "main:app"]
