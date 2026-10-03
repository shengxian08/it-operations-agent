"""Read-only BGE tokenizer audit; no inference or new vectors."""
import argparse
import hashlib
import json
from pathlib import Path


def run(encoded_path, output):
    from transformers import AutoTokenizer
    data = encoded_path.read_bytes()
    encoded = json.loads(data)
    model = encoded["model"]
    tokenizer = AutoTokenizer.from_pretrained(model["name"], revision=model["revision"], local_files_only=True)
    records = {}
    for arm, texts in encoded["inputs"].items():
        lengths = [len(tokenizer(text, truncation=False)["input_ids"]) for text in texts]
        records[arm] = {"token_lengths": lengths, "max": max(lengths), "over_512_count": sum(length > 512 for length in lengths)}
    result = {"encoded_sha256": hashlib.sha256(data).hexdigest(), "model": model,
              "source": "pinned local tokenizer; no model inference", "model_token_limit": 512,
              "synthetic_short_corpus_only": True, "inputs": records}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"maximums": {arm: {key: info[key] for key in ("max", "over_512_count")} for arm, info in records.items()}}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--encoded", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.encoded, args.output)
