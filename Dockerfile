# syntax=docker/dockerfile:1.4
# Repo #2 / Fragmenta pack: runtime-образ = Python + заранее скопированный бинарь + nodes.
FROM python:3.12-slim
WORKDIR /app
COPY nodes/requirements-install.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY gofactory-server-linux-amd64 /app/golang_factory/gofactory-server
RUN chmod +x /app/golang_factory/gofactory-server
COPY vault/env /app/golang_factory/.env
COPY nodes /app/golang_factory/nodes
ENV PYTHONPATH=/app/golang_factory/nodes
EXPOSE 7677
ENTRYPOINT ["/app/golang_factory/gofactory-server", "-config", "/app/golang_factory/.env", "-grpc.listen-address", "0.0.0.0:7677", "-http.listen-address", "0.0.0.0:7677"]
