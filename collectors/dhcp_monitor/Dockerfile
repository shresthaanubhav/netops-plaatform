FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY dhcp_exporter.py .
EXPOSE 8002
CMD ["python", "dhcp_exporter.py"]
