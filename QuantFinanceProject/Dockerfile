# ────────────────────────────────────────────────────────────────
# Dockerfile – QuantFinanceProject
# • Miniconda base
# • micromamba env  (pandas, etc.)
# • Prefect 3 CLI
# • Debian Chromium  + chromedriver (symlinked for Selenium)
# ────────────────────────────────────────────────────────────────

#########################
# 1️⃣  Base image
#########################
FROM continuumio/miniconda3:24.1.2-0

#########################
# 2️⃣  Install micromamba
#########################
RUN conda install -y -c conda-forge micromamba && \
    conda clean -afy

#########################
# 3️⃣  Project working dir
#########################
WORKDIR /app

#########################
# 4️⃣  Build env + Prefect
#########################
COPY environment.yml .
RUN micromamba create -y -n quant-env -f environment.yml && \
    micromamba run -n quant-env pip install "prefect>=3,<4" && \
    micromamba clean -afy

#########################
# 5️⃣  Headless Chromium + driver
#########################
RUN apt-get update && \
    apt-get install -y --no-install-recommends chromium chromium-driver && \
    # put driver on PATH where Selenium expects it
    ln -s $(command -v chromedriver) /usr/local/bin/chromedriver && \
    # sanity check (fails build if either binary missing)
    chromedriver --version && chromium --version && \
    rm -rf /var/lib/apt/lists/*

#########################
# 6️⃣  Copy project code
#########################
COPY . .

#########################
# 7️⃣  Default command
#########################
CMD ["micromamba", "run", "-n", "quant-env", "python", "-m", "market_data_agent.ingestion.stream_intraday_1m"]