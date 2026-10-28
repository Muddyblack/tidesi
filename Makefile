.PHONY: readme-screenshots new-language test-install format help view view-h view-hyprland hyprland install pack tag test test-py test-e2e translations check-translations lint-py run-desktop run-windows macos
.DEFAULT_GOAL := help

help: ## list targets
	@awk 'BEGIN{FS=":.*##"} /^[a-z][a-zA-Z0-9_-]+:.*##/ {printf "  make %-10s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

view: ## preview widget (planar)
	@if command -v nix >/dev/null 2>&1 && [ -f flake.nix ]; then \
	  nix run .#view; \
	else \
	  ./scripts/build-kde-package.sh && plasmoidviewer -a build/kde -f planar; \
	fi

view-h: ## preview widget (horizontal)
	@if command -v nix >/dev/null 2>&1 && [ -f flake.nix ]; then \
	  nix run .#view -- horizontal; \
	else \
	  ./scripts/build-kde-package.sh && plasmoidviewer -a build/kde -f horizontal; \
	fi

view-hyprland: ## preview widget (Hyprland / Quickshell)
	@if command -v nix >/dev/null 2>&1 && [ -f flake.nix ]; then \
	  nix run path:.#hyprland; \
	else \
	  echo "Running the Hyprland frontend requires Nix (Quickshell runtime)." >&2; \
	  exit 1; \
	fi

hyprland: view-hyprland

test-install: ## install a side-by-side "(Test)" copy of the KDE widget into your Plasma session
	@./scripts/test_install.sh

install: test-install

test: ## run the provider backend contract tests
	@$(MAKE) --no-print-directory test-py
	@./tests/python-interp.test.sh
	@./tests/history-io.test.sh
	@./tests/test_install_script.sh
	@./tests/translation-provider-parity.test.sh
	@if command -v node >/dev/null 2>&1; then node --test tests/*.test.js; \
	  else echo "skipping tests/shared-code.test.js (node not found)"; fi

test-py: ## run the portable unittest suites (also what CI runs on Windows)
	@python3 -m unittest discover -s tests/python

test-e2e: ## run the hermetic provider E2E suites
	@python3 -m unittest discover -s tests/python -p "test_e2e_*.py" -v

run-desktop: ## run the desktop tray app (Windows/macOS host) on this machine (PySide6 via 'nix develop .#windows')
	@if command -v nix >/dev/null 2>&1 && [ -f flake.nix ]; then \
	  nix develop .#windows --command python3 hosts/desktop/app.py; \
	else \
	  python3 hosts/desktop/app.py; \
	fi

run-windows: run-desktop

macos: ## build Tidesi.app (macOS only; needs hosts/macos/build-requirements.txt)
	@hosts/macos/build-app.sh

translations: ## regenerate template.pot from sources and compile the .mo catalogs
	@./translate/Messages.sh
	@./translate/build.sh

new-language: ## start a catalog for a language (make new-language CODE=de); LANG is the shell's locale, so not that
	@test -n "$(CODE)" || { echo "usage: make new-language CODE=<language code, e.g. de or pt_BR>" >&2; exit 2; }
	@test ! -e translate/$(CODE).po || { echo "translate/$(CODE).po already exists" >&2; exit 1; }
	@./translate/Messages.sh >/dev/null
	@msginit --no-translator --input=translate/template.pot --locale=$(CODE) --output-file=translate/$(CODE).po
	@sed -i '/^"POT-Creation-Date:/d' translate/$(CODE).po
	@echo "wrote translate/$(CODE).po: translate it (or on Weblate), then: make translations && make check-translations"

check-translations: ## check every catalog compiles; listed ones (translate/complete-languages) must be complete
	@./translate/check.sh

lint-py: ## lint + format-check the Python backend, frontends and helpers (dev only, needs ruff)
	@if command -v ruff >/dev/null 2>&1; then \
	  ruff check backend/tidesi hosts scripts tests/python && \
	  ruff format --check backend/tidesi hosts scripts tests/python; \
	else \
	  echo "ruff not found — install it or run 'nix develop'"; exit 1; \
	fi

format: ## format and auto-fix the Python (backend, hosts, scripts, tests) with ruff
	@if command -v ruff >/dev/null 2>&1; then ruff=ruff; else ruff="nix run .#ruff --"; fi; \
	  $$ruff format backend/tidesi hosts scripts tests/python && $$ruff check --fix backend/tidesi hosts scripts tests/python

readme-screenshots: ## capture the real QML UI with demo data into docs/readme/screenshots
	@if command -v nix >/dev/null 2>&1 && [ -f flake.nix ]; then \
	  QT_QPA_PLATFORM=offscreen nix develop .\#windows --command python hosts/desktop/app.py --screenshot docs/readme/screenshots --demo; \
	else \
	  QT_QPA_PLATFORM=offscreen python3 hosts/desktop/app.py --screenshot docs/readme/screenshots --demo; \
	fi


pack: ## build .plasmoid archive
	@if command -v nix >/dev/null 2>&1 && [ -f flake.nix ]; then \
	  nix run .#pack; \
	else \
	  ver=$$(grep -oE '"Version":[[:space:]]*"[^"]+"' hosts/kde/metadata.json | head -1 | sed -E 's/.*"([^"]+)"$$/\1/'); \
	  name=tidesi; \
	  out="$$PWD/$$name-$$ver.plasmoid"; \
	  rm -f "$$out"; \
	  ./scripts/build-kde-package.sh && \
	  (cd build/kde && zip -r "$$out" . -x '*.swp' '*~'); \
	  echo "wrote $$out"; \
	fi

tag: ## open a release PR (bump version); merging it tags + publishes
	@./scripts/tag.sh
