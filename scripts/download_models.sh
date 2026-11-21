#!/usr/bin/env bash
# Resumable download of the default models into ./models so burstai works fully offline afterwards.
# Usage: bash scripts/download_models.sh   (re-run to resume after a dropped connection)
set -u
cd "$(dirname "$0")/.."
mkdir -p models
get() { # get <hf-repo> <local-dir> <file...>
  repo="$1"; dir="models/$2"; shift 2; mkdir -p "$dir"
  for f in "$@"; do
    mkdir -p "$dir/$(dirname "$f")"
    echo ">> $repo/$f"
    curl -L -C - --retry 50 --retry-delay 3 --retry-all-errors --speed-limit 1000 --speed-time 60 -sS \
      -o "$dir/$f" "https://huggingface.co/$repo/resolve/main/$f" || echo "!! failed $repo/$f (re-run to resume)"
  done
}
get distilgpt2 distilgpt2 config.json generation_config.json tokenizer.json tokenizer_config.json vocab.json merges.txt model.safetensors
get gpt2 gpt2 config.json generation_config.json tokenizer.json tokenizer_config.json vocab.json merges.txt model.safetensors
get sentence-transformers/all-MiniLM-L6-v2 all-MiniLM-L6-v2 config.json modules.json sentence_bert_config.json config_sentence_transformers.json tokenizer.json tokenizer_config.json vocab.txt special_tokens_map.json 1_Pooling/config.json model.safetensors
echo "DONE"
