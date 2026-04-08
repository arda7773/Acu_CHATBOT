import logging
import re

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

EMBED_MODEL = 'nomic-embed-text'
EMBED_DIMENSIONS = 768
CHUNK_SIZE = 350   # words per chunk
CHUNK_OVERLAP = 50  # word overlap between consecutive chunks
MIN_CHUNK_WORDS = 30  # discard chunks shorter than this

LOW_VALUE_PATTERNS = (
    'tanıtım kataloğu',
    'tanitim katalogu',
    'sanal tur',
    'başlangıç tarihi',
    'baslangic tarihi',
    'bitiş tarihi',
    'bitis tarihi',
)


def get_embedding(text: str) -> list[float] | None:
    """
    Call Ollama's nomic-embed-text to get a 768-dim embedding.
    Returns None on failure so callers can skip gracefully.
    """
    if not text or not text.strip():
        return None
    try:
        resp = requests.post(
            f'{settings.OLLAMA_URL}/api/embeddings',
            json={'model': EMBED_MODEL, 'prompt': text[:3000]},
            timeout=30,
        )
        resp.raise_for_status()
        embedding = resp.json().get('embedding')
        if embedding and len(embedding) == EMBED_DIMENSIONS:
            return embedding
        logger.warning(f"Unexpected embedding size: {len(embedding) if embedding else 'None'}")
        return None
    except requests.RequestException as e:
        logger.warning(f"Embedding request failed: {e}")
        return None


def chunk_text(text: str) -> list[str]:
    """
    Split text into overlapping word-level chunks.
    Strips blank lines and very short fragments before chunking.
    """
    # Clean: remove blank lines, collapse whitespace
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    filtered_lines = []
    for line in lines:
        lowered = line.lower()
        if any(pattern in lowered for pattern in LOW_VALUE_PATTERNS) and len(lowered.split()) < 15:
            continue
        filtered_lines.append(line)
    lines = filtered_lines
    clean = ' '.join(lines)
    clean = re.sub(r'\s+', ' ', clean).strip()
    words = clean.split()

    if len(words) < MIN_CHUNK_WORDS:
        return [clean] if clean else []

    chunks = []
    i = 0
    while i < len(words):
        chunk_words = words[i:i + CHUNK_SIZE]
        chunk = ' '.join(chunk_words)
        if len(chunk_words) >= MIN_CHUNK_WORDS:
            chunks.append(chunk)
        i += CHUNK_SIZE - CHUNK_OVERLAP

    return chunks
