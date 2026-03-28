# ────────────────────────────────────────────────────────────────
# Dockerfile – Macro Research Copilot
# • Miniconda base
# • micromamba env (pandas, scipy, blpapi, etc.)
# • Prefect 3 CLI
# ────────────────────────────────────────────────────────────────

#########################
# 1️⃣  Base image
#########################
FROM continuumio/miniconda3:24.1.2-0

#########################
# 2️⃣  Install micromamba & Build Tools
#########################
# Note: gcc and g++ are often required to compile blpapi C++ bindings
RUN apt-get update && apt-get install -y gcc g++ && \
    rm -rf /var/lib/apt/lists/* && \
    conda install -y -c conda-forge micromamba && \
    conda clean -afy

#########################
# 3️⃣  Project working dir
#########################
WORKDIR /app

#########################
# 4️⃣  Build env + Prefect
#########################
COPY environment.yml .
RUN micromamba create -y -n macro-env -f environment.yml && \
    micromamba clean -afy

#########################
# 5️⃣  Copy project code
#########################
COPY . .

#########################
# 6️⃣  Default command (Keeps container alive for dev)
#########################
CMD ["tail", "-f", "/dev/null"]