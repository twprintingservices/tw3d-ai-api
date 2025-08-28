# Use micromamba to install pythonocc-core from conda-forge
FROM mambaorg/micromamba:1.5.8

# Create env from environment.yml
COPY --chown=$MAMBA_USER:$MAMBA_USER environment.yml /tmp/environment.yml
RUN micromamba install -y -n base -f /tmp/environment.yml && micromamba clean -a -y

WORKDIR /app
COPY --chown=$MAMBA_USER:$MAMBA_USER app.py .

EXPOSE 8000
CMD ["/usr/local/bin/python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
