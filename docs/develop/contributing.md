# Contributing

```sh
cd backend && pip install -e ".[server,postgres,uptimekuma,test]" ruff
pytest -q                                                       # SQLite
LABDEX_TEST_DATABASE_URL=postgresql://u@localhost/t pytest -q   # the same suite on PostgreSQL
LABDEX_E2E=1 pytest -q tests/e2e                                # browser tests (build the UI, playwright install chromium)
LABDEX_PERF=1 pytest -q -s tests/test_perf.py                   # performance budget, 1000 hosts
ruff check labdex tools tests                                   # pyflakes, pycodestyle, bugbear, pyupgrade
cd ../frontend && npm run lint                                  # ESLint: JavaScript and Vue rules (eslint.config.js)
cd ../frontend && npm ci && npm run dev                         # Vite on :5173, proxies /api to labdex run --debug
pip install mkdocs-material && mkdocs serve                     # these docs
```

Plugins for other systems are tested against small fake servers (`tests/fakes.py`)
that follow the real API; `tests/test_contracts.py` pins the details that matter.

Official plugins live in `plugins/` and must have a `labdex` range, tests, and use
nothing outside the [plugin API](plugin-api.md).
