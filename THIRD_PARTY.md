# Third-party components

BurstAI's own code is MIT-licensed (see `LICENSE`). It relies on the following components, each
under its own licence. Nothing below is modified; models and fonts are downloaded, not vendored
(except the four font files in `static/fonts/`).

## Models (downloaded by `scripts/download_models.sh`)

| Component | Used for | Licence |
|---|---|---|
| [distilgpt2](https://huggingface.co/distilgpt2) | observer model for perplexity / Binoculars | Apache-2.0 |
| [gpt2](https://huggingface.co/gpt2) | performer model for Binoculars | MIT (OpenAI "Modified MIT") |
| [sentence-transformers/all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) | paraphrase embeddings | Apache-2.0 |

The Binoculars method is from Hans et al., *Spotting LLMs With Binoculars: Zero-Shot Detection of
Machine-Generated Text* (ICML 2024). Winnowing is from Schleimer, Wilkerson & Aiken (SIGMOD 2003).

## Fonts (self-hosted in `static/fonts/`)

| Font | Licence |
|---|---|
| Lexend | SIL Open Font License 1.1 |
| Bricolage Grotesque | SIL Open Font License 1.1 |

## Online services contacted at run time

| Service | Used for | Notes |
|---|---|---|
| Web search via the [`ddgs`](https://pypi.org/project/ddgs/) metasearch library | finding candidate source pages | No API key. It queries public search front-ends, which rate-limit and whose terms may restrict automated use. For a high-traffic deployment, replace it with a search API you have an agreement with. |
| Wikipedia API | candidate articles | [API etiquette](https://www.mediawiki.org/wiki/API:Etiquette): identify with a User-Agent, keep request rates low. |
| Europe PMC REST API, Crossref REST API | open academic abstracts | Free, key-less; be polite with request rates. |
| [LanguageTool](https://languagetool.org) public API | grammar and spelling | Free tier is rate-limited (about 20 requests/minute per IP) and intended for light use. For production, self-host LanguageTool (LGPL-2.1) and set `LANGUAGETOOL_URL`. |
| Google Gemini API | optional review, only with the end user's own key | Google's API terms apply to that key. |

## Python packages

See `requirements.txt` / `requirements.lock`. Main ones: FastAPI (MIT), Uvicorn (BSD-3), httpx (BSD-3),
trafilatura (Apache-2.0), rapidfuzz (MIT), PyTorch (BSD-3), transformers (Apache-2.0),
sentence-transformers (Apache-2.0), python-docx (MIT), pypdf (BSD-3), regex (Apache-2.0),
google-genai (Apache-2.0).
