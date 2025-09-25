FROM python:3.11-slim

# Avoid prompts and set UTF-8
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System deps for scientific stack and pygame/turtle (tk), plus build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    git \
    curl \
    ca-certificates \
    libblas-dev \
    liblapack-dev \
    libfreetype6-dev \
    libpng-dev \
    libjpeg-dev \
    libtiff6 \
    libopenjp2-7 \
    python3-tk \
    pkg-config \
    libffi-dev \
    libssl-dev \
    libsqlite3-dev \
    libsm6 \
    libxext6 \
    libxrender1 \
    libsdl2-dev \
    libsdl2-image-dev \
    libsdl2-mixer-dev \
    libsdl2-ttf-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency manifests first for better caching
COPY requirements.txt /app/requirements.txt

# Preinstall popular libs so imports work at runtime
RUN pip install --no-cache-dir -U pip setuptools wheel \
    && pip install --no-cache-dir -r /app/requirements.txt \
    && pip install --no-cache-dir numpy matplotlib pygame \
    && python -c "import sys; print(sys.version)"

# Copy source
COPY . /app

CMD ["python", "bot.py"]

