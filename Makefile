.PHONY: install build test lint clean

install:
	pip install -e ".[dev]"

build:
	python setup.py build_ext --inplace

test:
	pytest

lint:
	ruff check src tests

clean:
	rm -rf build dist *.egg-info src/ghostroute/_speedups/*.c src/ghostroute/_speedups/*.so

