SHELL := bash

VERSION := $(shell grep -m 1 version pyproject.toml | tr -s ' ' | tr -d '"' | tr -d "'" | cut -d' ' -f3)
PACKAGE := $(shell grep -m 1 name pyproject.toml | tr -s ' ' | tr -d '"' | tr -d "'" | cut -d' ' -f3)

OBJ := $(shell find rnhttp -type f)
OBJ += pyproject.toml
OBJ += README.md

ifndef SKIP_TESTS
TESTS := $(shell find tests -type f -name '*.py')
INDIVIDUAL_TESTS := $(shell SKIP_TESTS=1 MAKEFLAGS= make --no-print-directory list-tests)
endif

ifeq ($(VENV_BIN_ACTIVATE),)
VENV_BIN_ACTIVATE := .venv/bin/activate
endif

define PLATFORM_SCRIPT
from sysconfig import get_platform
print(get_platform().replace('-', '_'), end="")
endef
export PLATFORM_SCRIPT
PLATFORM := $(shell python -c "$$PLATFORM_SCRIPT")
define ABI_SCRIPT
def main():
    try:
        from wheel.pep425tags import get_abi_tag
        print(get_abi_tag(), end="")
        return
    except ModuleNotFoundError:
        pass

    try:
        from wheel.vendored.packaging import tags
    except ModuleNotFoundError:
        from packaging import tags

    name=tags.interpreter_name()
    version=tags.interpreter_version()
    print(f"{name}{version}", end="")

main()
endef
export ABI_SCRIPT
ABI := $(shell python -c "$$ABI_SCRIPT")

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-10s\033[0m %s\n", $$1, $$2}'

$(VENV_BIN_ACTIVATE):
	python -m venv .venv
	@. ${VENV_BIN_ACTIVATE}; \
	python -m pip install --upgrade pip; \
	python -m pip install --upgrade build wheel

.PHONY: requirements
requirements: $(VENV_BIN_ACTIVATE) pyproject.toml ## Install requirements
	@. ${VENV_BIN_ACTIVATE}; \
	python -m pip install \
	  --quiet \
	  --editable \
	  .

.PHONY: requirements-dev
requirements-dev: $(VENV_BIN_ACTIVATE) pyproject.toml ## Install dev requirements
	@. ${VENV_BIN_ACTIVATE}; \
	python -m pip install \
	  --quiet \
	  --editable \
	  ".[dev]"

.PHONY: test
test: requirements-dev ## Run tests
	@. ${VENV_BIN_ACTIVATE}; \
	python -m pytest \
	  -vv \
	  tests/

.repos:
	mkdir -p .repos

.PHONY: list-tests
list-tests: ## List all available tests
	@if [ ! -f ${VENV_BIN_ACTIVATE} ];then \
	  $(MAKE) requirements-dev >/dev/null; \
	fi
	@. ${VENV_BIN_ACTIVATE}; \
	python -m pytest \
	  --collect-only \
	  --quiet \
	  --disable-warnings \
	  tests/ \
	| grep -v ' tests collected in ' \
	| xargs -n1

ifndef SKIP_TESTS
define test-target
.PHONY: $2
$2: requirements-dev
	@. ${VENV_BIN_ACTIVATE}; \
	python -m pytest \
	  -vv \
	  $1
endef

$(foreach T,\
	$(TESTS),\
	$(eval $(call test-target,\
		$(T),\
		$(shell echo $(T) | sed 's|:|\\:|g'),\,\
	))\
)

$(foreach T,\
	$(INDIVIDUAL_TESTS),\
	$(eval $(call \
		test-target,\
		$(T),\
		$(shell echo $(T) | sed 's|:|\\:|g'),\
	))\
)
endif

.PHONY: build
build: sdist wheel ## Build wheel and sdist

dist:
	mkdir -p dist

.PHONY: wheel
wheel: dist/git_remote_rns-${VERSION}-${ABI}-${ABI}-${PLATFORM}.whl # Build wheel

.PHONY: sdist
sdist: dist/git_remote_rns-${VERSION}.tar.gz # Build sdist

dist/git_remote_rns-${VERSION}-${ABI}-${ABI}-${PLATFORM}.whl: $(VENV_BIN_ACTIVATE) dist $(OBJ)
	@. ${VENV_BIN_ACTIVATE}; \
	python -m build --wheel

dist/git_remote_rns-${VERSION}.tar.gz: $(VENV_BIN_ACTIVATE) dist $(OBJ)
	@. ${VENV_BIN_ACTIVATE}; \
	python -m build --sdist

.PHONY: clean
clean: ## Remove build artifacts
	rm -rf build/ dist/ *.egg-info/ .venv/
	rm -rf *.build *.dist
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

.PHONY: whitelist
whitelist: requirements-dev ## Generate lint whitelists
	@set -e;\
	. ${VENV_BIN_ACTIVATE}; \
	rm -f rnhttp/__whitelist.py; \
	python -m vulture --make-whitelist rnhttp/ >rnhttp/__whitelist.py || true; \
	rm -f tests/__whitelist.py; \
	python -m vulture --make-whitelist tests/ >tests/__whitelist.py || true


.PHONY: lint
lint: requirements-dev ## Lint the codebase
	@set -e;\
	. ${VENV_BIN_ACTIVATE}; \
	runtool() { \
	  tool=$$1; \
	  shift; \
	  echo -n "Running $$tool: "; \
	  set +e; \
	  output=$$(python -um "$$tool" $$@ 2>&1); \
	  ret=$$?; \
	  set -e; \
	  if [[ $$ret -ne 0 ]];then \
	    echo "FAIL ($$ret)"; \
	    echo "$$output"; \
	    exit $$ret; \
	  fi; \
	  echo "OKAY"; \
	}; \
	runtool ruff check; \
	for dir in rnhttp tests;do \
	  for tool in basedpyright vulture;do \
	    runtool "$$tool" "$$dir"; \
	  done; \
	done; \
	runtool dodgy --zero-exit --ignore-paths dist/ build/ .venv/ .repos/; \
	runtool pyroma .

.PHONY: review
review: ## Have coderabbit review the code
	@if command -v coderabbit >/dev/null 2>&1; then \
	  output=$$(coderabbit review --prompt-only 2>&1); \
	  status=$$?; \
	  if echo "$$output" | grep -qiE "auth|unauthorized|login|401"; then \
	    echo "coderabbit auth required"; \
	  elif [ $$status -ne 0 ]; then \
	    echo "$$output"; \
	    echo "coderabbit review failed with exit code $$status"; \
	  fi; \
	else \
	  echo "coderabbit not installed"; \
	fi
