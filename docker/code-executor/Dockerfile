ARG PYTHON_BASE=python:3.11-slim
FROM ${PYTHON_BASE}
# Operator builds this trusted, dependency-free image and pins its immutable local ID.
RUN python -m pip uninstall -y pip setuptools wheel
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65534:65534
WORKDIR /out
