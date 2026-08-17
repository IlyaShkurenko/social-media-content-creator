.PHONY: test-bdd

test-bdd:
	.venv/bin/python -m pytest -q test/bdd
