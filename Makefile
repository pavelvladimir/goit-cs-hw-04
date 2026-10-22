.DEFAULT_GOAL := help
UV_CACHE_DIR ?= $(CURDIR)/.uv-cache
export UV_CACHE_DIR
ROCKY_TEST_IMAGE ?= goit-cs-hw-04-rocky-tests
ROCKY_TEST_RUN = docker run --rm --init --network none --read-only \
	--tmpfs /tmp:rw,nosuid,nodev,noexec,size=256m,mode=1777 \
	--cap-drop ALL --security-opt no-new-privileges $(ROCKY_TEST_IMAGE)
.PHONY: help init sync demo threading multiprocessing benchmark lint lint-python lint-docs format typecheck test coverage check build-rocky-tests check-rocky

help: ## Показати доступні команди
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

init: sync ## Підготувати локальне середовище

sync: ## Встановити залежності з lockfile
	uv sync --locked

demo: ## Запустити обидві версії на прикладах
	uv run --locked python main.py

threading: ## Запустити багатопотокову версію
	uv run --locked python threading_search.py

multiprocessing: ## Запустити багатопроцесорну версію
	uv run --locked python multiprocessing_search.py

benchmark: ## Порівняти послідовний пошук, потоки та процеси
	uv run --locked python -m scripts.benchmark

lint: lint-python lint-docs ## Перевірити Python і Markdown

lint-python: ## Перевірити Ruff і форматування
	uv run --locked ruff check .
	uv run --locked ruff format --check .

lint-docs: ## Перевірити документацію
	uv run --locked pymarkdown --config pyproject.toml scan --recurse --respect-gitignore --exclude .git .

format: ## Форматувати Python
	uv run --locked ruff format .

typecheck: ## Перевірити strict mypy
	uv run --locked mypy

test: ## Виконати тести, включно зі справжніми spawn-процесами
	uv run --locked pytest

coverage: ## Перевірити покриття рядків і гілок, включно з дочірніми процесами
	uv run --locked coverage erase
	uv run --locked coverage run -m pytest
	uv run --locked coverage combine
	uv run --locked coverage report
	uv run --locked coverage html
	uv run --locked coverage xml

check: lint typecheck coverage ## Виконати всі перевірки з coverage >= 90%

build-rocky-tests: ## Зібрати локальний тестовий образ Rocky Linux
	docker build --file docker/tests.Dockerfile --tag $(ROCKY_TEST_IMAGE) .

check-rocky: build-rocky-tests ## Перевірки й demo в ізольованому Rocky Linux
	$(ROCKY_TEST_RUN)
