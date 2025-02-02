FROM python:3.13-slim-bookworm
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY renamarr.py .
CMD ["uvicorn", "renamarr:app", "--host",  "0.0.0.0",  "--port", "8000"]