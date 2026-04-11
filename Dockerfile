# ── Stage 1: dependency builder ───────────────────────────────────────────────
# Use slim image to keep the final layer small; install build tools only here.
FROM python:3.12-slim AS builder

WORKDIR /build

# Install system packages needed to compile Python C-extensions (pycryptodome)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install into an isolated prefix so we can copy only the installed packages
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: Playwright browser installer ─────────────────────────────────────
# Separate stage so browser binaries don't pollute the builder layer cache.
FROM python:3.12-slim AS browser

COPY --from=builder /install /usr/local

# Install system deps required by Playwright/Chromium, then install only Chromium.
# We do NOT install Firefox or WebKit to keep image size down.
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Chromium runtime dependencies
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libexpat1 \
    libxcb1 \
    libxkbcommon0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

RUN playwright install chromium


# ── Stage 3: final runtime image ──────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Install Chromium runtime dependencies directly.
# We cannot COPY shared libraries between stages — COPY does not run in a shell
# and the library path varies by architecture.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libexpat1 \
    libxcb1 \
    libxkbcommon0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user to run the crawler
RUN useradd --create-home --shell /bin/bash crawler

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy Playwright browsers from browser stage
COPY --from=browser /root/.cache/ms-playwright /home/crawler/.cache/ms-playwright

# Copy application source
COPY --chown=crawler:crawler . .

# Ensure the browser cache is owned by the crawler user
RUN chown -R crawler:crawler /home/crawler/.cache || true

# Switch to non-root user
USER crawler

# Default command: run the crawler with config from the mounted volume
CMD ["python", "main.py"]
