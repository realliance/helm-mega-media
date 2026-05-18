FROM public.ecr.aws/docker/library/python:3.12-alpine

RUN pip install --no-cache-dir httpx==0.28.* pyyaml==6.*

WORKDIR /app
COPY reconciler.py /app/reconciler.py

USER 1000

ENTRYPOINT ["python", "/app/reconciler.py"]
