FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ospf_exporter.py .
EXPOSE 8000
CMD ["python", "ospf_exporter.py"]
