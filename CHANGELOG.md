# Changelog

## 0.1.0 - 2026-11-22

Production hardening: signed reports, privacy notice and terms, security headers and rate limits, per-owner document bank, honest coverage reporting, faster matcher, 28 tests.

### Added
- app/llm.py
- app/security.py
- scripts/fetch_fonts.py
- scripts/start_engine.ps1
- static/404.html
- static/css/fonts.css
- static/css/pages.css
- static/fonts/bricolage-grotesque-latin-ext.woff2
- static/fonts/bricolage-grotesque-latin.woff2
- static/fonts/lexend-latin-ext.woff2
- static/fonts/lexend-latin.woff2
- static/js/auth.js
- static/privacy.html
- static/terms.html
- tests/conftest.py
- tests/test_api.py

### Changed
- .env.example
- .gitignore
- DEPLOY.md
- README.md
- app/aidetect/engine.py
- app/aidetect/llm_judge.py
- app/aidetect/lm.py
- app/auth.py
- app/config.py
- app/grammar.py
- app/main.py
- app/plagiarism/corpus.py
- app/plagiarism/engine.py
- app/plagiarism/fetch.py
- app/plagiarism/matcher.py
- app/plagiarism/search.py
- app/reports.py
- app/textproc.py
- cloudflare/schema.sql
- cloudflare/src/worker.js
- cloudflare/wrangler.toml
- requirements.txt
- static/auth.html
- static/check.html
- static/css/burst.css
- static/css/mobile.css
- static/index.html
- static/js/checker.js
- static/js/common.js
- static/js/landing.js
- static/js/me.js
- static/js/report.js
- static/me.html
- static/og.html
- static/report.html
- tests/shot.mjs
- tests/test_core.py

## 0.1.0 - 2026-11-21

### Changed
- scripts/make_release.py

## 0.1.0 - 2026-11-21

Mobile layouts from the BurstAI mobile canvas, accounts (login/signup, saved scans, My writing), SEO pass, Cloudflare Worker deployment, search cache.

### Added
- .env.example
- .gitignore
- DEPLOY.md
- README.md
- app/__init__.py
- app/aidetect/__init__.py
- app/aidetect/engine.py
- app/aidetect/llm_judge.py
- app/aidetect/lm.py
- app/aidetect/stylometry.py
- app/auth.py
- app/cache.py
- app/config.py
- app/extract.py
- app/fingerprint.py
- app/grammar.py
- app/main.py
- app/plagiarism/__init__.py
- app/plagiarism/corpus.py
- app/plagiarism/engine.py
- app/plagiarism/fetch.py
- app/plagiarism/matcher.py
- app/plagiarism/search.py
- app/reports.py
- app/textproc.py
- cloudflare/schema.sql
- cloudflare/src/worker.js
- cloudflare/wrangler.toml
- requirements.txt
- run.bat
- samples/ai_business_email.txt
- samples/ai_casual_blog.txt
- samples/ai_explainer.txt
- samples/ai_story.txt
- samples/human_forum_post.txt
- samples/human_hinglish_chat.txt
- samples/human_lab_notes.txt
- samples/human_letter_1900.txt
- scripts/download_models.sh
- scripts/make_release.py
- static/auth.html
- static/check.html
- static/css/burst.css
- static/css/mobile.css
- static/favicon.svg
- static/index.html
- static/js/checker.js
- static/js/common.js
- static/js/landing.js
- static/js/me.js
- static/js/report.js
- static/me.html
- static/og.html
- static/og.png
- static/report.html
- static/samples/ai.txt
- static/samples/human.txt
- static/samples/urban_heat.txt
- static/site.webmanifest
- tests/calibrate.py
- tests/shot.mjs
- tests/test_core.py

