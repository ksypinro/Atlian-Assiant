# Contributing to Atlian-Assiant

Thanks for helping! Bug reports, docs fixes and pull requests are all welcome.

## Set up

```bash
git clone https://github.com/ksypinro/Atlian-Assiant.git && cd Atlian-Assiant
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
```

## Before you open a pull request

```bash
pytest -q                                  # the whole suite runs offline against built-in fakes
mypy atlian_assiant                        # strict typing
ruff check atlian_assiant
python -m tools.docgen --check             # generated docs are up to date (python -m tools.docgen --write)
python -m tools.gen_stubs --check          # sync-client stubs (python -m tools.gen_stubs --write)
python -m tools.api_surface --check        # public API: nothing removed by accident
python -m tools.gql_schema_subset --check  # stored GraphQL queries match the recorded gateway schema
```

CI runs the same checks.

## Ground rules

* **Never** put real tokens, site names, account ids or customer data in code, tests, fixtures or issues. Tests use the fakes in
  `atlian_assiant/testing/`.
* A backend that cannot do something must raise `UnsupportedOperationError` (the router then falls back), never return partial data silently.
* Changes to what a backend supports go together with the operation × backend table in `docs/architecture_design.md` (section 11);
  `tests/contract/test_matrix_matches_design.py` keeps the two in sync.
* If you checked behaviour against a live Atlassian site, say so in the pull request (what, which version, which date). Recorded interface data
  (CLI help pages, schema subsets, tool catalogues) is welcome as a test fixture; see [NOTICE](NOTICE).
* Keep pull requests focused; add a test for every bug fix.

By contributing you agree that your contribution is licensed under the [MIT License](LICENSE), and you agree to follow the
[Code of Conduct](CODE_OF_CONDUCT.md).
