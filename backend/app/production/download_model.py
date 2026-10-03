"""Explicit one-time deployment preparation; normal workers run offline."""
from huggingface_hub import snapshot_download
from app.core.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    snapshot_download(settings.embedding_model,revision=settings.embedding_revision,
        allow_patterns=["*.json", "*.txt", "*.safetensors", "1_Pooling/*"])
    print("Pinned embedding model is available in the mounted model cache.")
