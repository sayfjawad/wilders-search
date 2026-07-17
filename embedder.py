"""Shared BGE-M3 dense embedding helper (GPU, fp16)."""
import os

os.environ.setdefault("HF_HOME", "/data/huggingface")

import torch
from transformers import AutoTokenizer, AutoModel

MODEL_NAME = "BAAI/bge-m3"
DIM = 1024


class Embedder:
    def __init__(self, device: str = "cuda:0", max_length: int = 1024):
        self.device = device
        self.max_length = max_length
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
        dtype = torch.float16 if device.startswith("cuda") else torch.float32
        self.model = AutoModel.from_pretrained(MODEL_NAME, torch_dtype=dtype).to(device).eval()

    @torch.no_grad()
    def encode(self, texts: list[str]) -> torch.Tensor:
        """Return L2-normalized fp16 embeddings, shape (n, 1024), on CPU."""
        batch = self.tokenizer(
            texts, padding=True, truncation=True,
            max_length=self.max_length, return_tensors="pt",
        ).to(self.device)
        out = self.model(**batch).last_hidden_state[:, 0]
        out = torch.nn.functional.normalize(out, dim=-1)
        return out.cpu()
