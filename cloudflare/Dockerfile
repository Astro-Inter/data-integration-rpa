FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.txt -c requirements.lock && pip check
COPY app ./app
COPY cloudflare/job_server.py cloudflare/jobs.json ./cloudflare/
USER 10001:10001
EXPOSE 8080
STOPSIGNAL SIGTERM
CMD ["python", "cloudflare/job_server.py"]
