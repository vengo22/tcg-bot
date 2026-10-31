FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Datenbank liegt im Volume /app/data, damit der Preisverlauf Updates übersteht
ENV TCGBOT_DB=/app/data/tcgbot.db
CMD ["python", "-m", "tcgbot"]
