# Shortcuts for various tasks (UNIX only).
# To use a specific Python version run: "make install PYTHON=python3.3"
# You can set the variables below from the command line.

# Configurable
PYTHON = python3
ARGS =

PIP_INSTALL_ARGS = --trusted-host files.pythonhosted.org --trusted-host pypi.org --upgrade
PYTHON_ENV_VARS = PYTHONWARNINGS=always PYTHONUNBUFFERED=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONMALLOC=malloc

# if make is invoked with no arg, default to `make help`
.DEFAULT_GOAL := help

# ===================================================================
# Install
# ===================================================================

clean:  ## Remove all build files.
	@rm -rfv `find . \
		-type d -name __pycache__ \
		-o -type f -name \*.bak \
		-o -type f -name \*.orig \
		-o -type f -name \*.pyc \
		-o -type f -name \*.pyd \
		-o -type f -name \*.pyo \
		-o -type f -name \*.rej \
		-o -type f -name \*.so \
		-o -type f -name \*.~ \
		-o -type f -name \*\$testfn`
	@rm -rfv \
		*.core \
		*.egg-info \
		*\@psleak-* \
		.coverage \
		.failed-tests.txt \
		.pytest_cache \
		.ruff_cache/ \
		build/ \
		dist/ \
		docs/_build/ \
		htmlcov/ \
		pytest-cache-files* \
		wheelhouse

.PHONY: build
build:  ## Build the test extension
	$(PYTHON_ENV_VARS) $(PYTHON) tests/build_ext.py build_ext --inplace
	$(PYTHON_ENV_VARS) $(PYTHON) -c "import test_ext"  # make sure it actually worked

install:  ## Install this package as current user in edit mode.
	# If not in a virtualenv, add --user to the install command.
	$(PYTHON_ENV_VARS) $(PYTHON) -m pip install -e . $(SETUP_INSTALL_ARGS) `$(PYTHON) -c \
		"import sys; print('' if hasattr(sys, 'real_prefix') or hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix else '--user')"`

# ===================================================================
# Tests
# ===================================================================

test:  ## Run all tests.
	$(PYTHON_ENV_VARS) $(PYTHON) -m pytest $(ARGS)

test-interface:
	$(PYTHON_ENV_VARS) $(PYTHON) -m pytest -k test_interface.py $(ARGS)

test-c-leaks:
	$(PYTHON_ENV_VARS) $(PYTHON) -m pytest -k test_c_leaks.py $(ARGS)

test-python-leaks:
	$(PYTHON_ENV_VARS) $(PYTHON) -m pytest -k test_python_leaks.py $(ARGS)

ci-test:
	$(PYTHON) -m pip install setuptools pytest pytest-instafail
	$(PYTHON) -m pip install git+https://github.com/giampaolo/psutil.git
	make build
	make test

# ===================================================================
# Linters
# ===================================================================

ruff:  ## Run ruff linter.
	@git ls-files '*.py' | xargs $(PYTHON) -m ruff check --output-format=concise

black:  ## Run black formatter.
	@git ls-files '*.py' | xargs $(PYTHON) -m black --check --safe

lint-c:  ## Run C linter.
	@git ls-files '*.c' '*.h' | xargs -P0 -I{} clang-format --dry-run --Werror {}

lint-toml:  ## Run linter for pyproject.toml.
	@git ls-files '*.toml' | xargs toml-sort --check

lint-all:  ## Run all linters
	$(MAKE) black
	$(MAKE) ruff
	$(MAKE) lint-c
	$(MAKE) lint-toml
	$(MAKE) lint-rst

# ===================================================================
# Fixers
# ===================================================================

fix-black:
	@git ls-files '*.py' | xargs $(PYTHON) -m black

fix-ruff:
	@git ls-files '*.py' | xargs $(PYTHON) -m ruff check --fix --output-format=concise $(ARGS)

fix-c:
	@git ls-files '*.c' '*.h' | xargs -P0 -I{} clang-format -i {}  # parallel exec

fix-toml:  ## Fix pyproject.toml
	@git ls-files '*.toml' | xargs toml-sort

lint-rst:  ## Run linter for .rst files.
	@git ls-files '*.rst' | xargs rstcheck

fix-all:  ## Run all code fixers.
	$(MAKE) fix-ruff
	$(MAKE) fix-black
	$(MAKE) fix-toml

# ===================================================================
# Distribution
# ===================================================================

sdist:  ## Create a .tar.gz distribution.
	$(MAKE) clean
	$(PYTHON) -m build --sdist --no-isolation

check-sdist:  ## Check sanity of source distribution.
	$(PYTHON) -m validate_pyproject -v pyproject.toml
	$(PYTHON) -m twine check --strict dist/*.tar.gz

# ===================================================================
# Misc
# ===================================================================

help: ## Display callable targets.
	@awk -F':.*?## ' '/^[a-zA-Z0-9_.-]+:.*?## / {printf "\033[36m%-24s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST) | sort
