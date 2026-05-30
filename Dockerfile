FROM python:3.13-slim

RUN apt-get update && apt-get install -y ffmpeg nodejs npm && rm -rf /var/lib/apt/lists/*
RUN pip install spotdl
RUN apt-get install -y ffmpeg

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD ["python", "main.py"]
