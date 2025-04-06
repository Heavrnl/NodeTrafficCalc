FROM python:3.10-slim

WORKDIR /app


RUN pip install --no-cache-dir requests PyYAML

COPY . .

CMD ["python", "./main.py"]