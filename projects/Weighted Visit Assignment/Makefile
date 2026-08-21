PY ?= python3

.PHONY: help init demo test week drift debrief calibrate sensitivity sync verify-graph \
        import-calendar inbox audit \
        serve serve-quiet static publish \
        install-automation uninstall-automation deck clean

help:
	@echo "ESD weighted visit assignment (v3)"
	@echo
	@echo "  make init                 write config/engine.json, create the audit db"
	@echo "  make demo                 synthetic lab, score + assign a week, log it all"
	@echo "  make test                 run every test suite (engine, privacy, API)"
	@echo "  make serve                start the Visitboard on http://127.0.0.1:8765"
	@echo "  make static               build the public, backend-free copy into dist-static/"
	@echo "  make publish              build it and deploy to Netlify"
	@echo "  make week                 greedy vs optimiser with measured regret"
	@echo "  make sync                 pull calendars, record the SLO"
	@echo "  make import-calendar FILE=cal.pdf   read an Outlook PDF print"
	@echo "  make inbox                sweep data/inbox and file every PDF"
	@echo "  make audit                what was imported, decided and overridden"
	@echo "  make drift                weekly drift metrics to stdout"
	@echo "  make debrief              write reports/debrief-<week>.md and .html"
	@echo "  make calibrate            recalibrate the review band from the audit log"
	@echo "  make sensitivity          OAT, criticality, redundancy, revealed preference"
	@echo "  make verify-graph         run the Graph privacy probes (needs ESD_CAL_TOKEN)"
	@echo "  make install-automation   load the launchd agents (5 min sync, weekly debrief)"
	@echo "  make deck                 rebuild the v3 slide deck (math, build, preview, brand QA)"
	@echo "  make clean                remove generated data, reports and logs"

init:
	$(PY) -m esd_scheduler init

demo: init
	$(PY) -m esd_scheduler demo

test:
	$(PY) tests/test_engine.py
	$(PY) tests/test_graph_privacy.py
	$(PY) tests/test_constraints.py
	$(PY) tests/test_calendar_import.py
	$(PY) tests/test_automation.py
	$(PY) backend/tests/test_api.py
	$(PY) -m backend.build_static >/dev/null && $(PY) tests/test_static_board.py

serve:
	$(PY) -m backend.server

static:
	$(PY) -m backend.build_static
	$(PY) tests/test_static_board.py

publish: static
	netlify deploy --prod --dir dist-static

serve-quiet:
	$(PY) -m backend.server --no-browser

week:
	$(PY) -m esd_scheduler plan-week --verbose

sync:
	$(PY) -m esd_scheduler sync

inbox:
	$(PY) -m esd_scheduler import-inbox

audit:
	$(PY) -m esd_scheduler audit

import-calendar:
	@test -n "$(FILE)" || (echo "usage: make import-calendar FILE=path/to/calendar.pdf" && exit 1)
	$(PY) -m esd_scheduler import-calendar "$(FILE)" --record

drift:
	$(PY) -m esd_scheduler drift

debrief:
	$(PY) -m esd_scheduler debrief

calibrate:
	$(PY) -m esd_scheduler calibrate

sensitivity:
	$(PY) -m esd_scheduler sensitivity

verify-graph:
	$(PY) -m esd_scheduler verify-graph --out reports/graph-verification.txt

install-automation:
	@chmod +x automation/*.sh
	@./automation/install.sh

uninstall-automation:
	@./automation/uninstall.sh

deck:
	$(PY) deck/render_math_v3.py
	$(PY) deck/build_deck_v3.py
	$(PY) deck/preview.py
	$(PY) deck/qa.py

clean:
	rm -rf data reports logs dist-static deck/math deck/_tex deck/math_dims.json
	find . -name '__pycache__' -type d -exec rm -rf {} +
