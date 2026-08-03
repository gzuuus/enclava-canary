FROM python:3.12-slim
WORKDIR /app
COPY server.py index.html ./
# Writable mount target for the storage.paths bind — the workload rootfs is
# read-only, so the path must exist as a volume for enclava-init to bind into.
VOLUME ["/app/data"]
EXPOSE 8080
CMD ["python3", "/app/server.py"]
