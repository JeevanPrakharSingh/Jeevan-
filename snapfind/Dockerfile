FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 libportaudio2 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py liveness.py setup_models.py ./
COPY templates templates
COPY static static
RUN python setup_models.py && useradd --system snapfind && mkdir /data && chown snapfind /data
RUN --mount=type=bind,source=test_mpo_upload.py,target=/app/test_mpo_upload.py \
    OPENBLAS_NUM_THREADS=1 python test_mpo_upload.py
ENV SNAPFIND_DATA=/data PORT=8080 MPLCONFIGDIR=/tmp/matplotlib
USER snapfind
EXPOSE 8080
CMD ["sh", "-c", "exec waitress-serve --listen=0.0.0.0:$PORT --threads=6 app:app"]
