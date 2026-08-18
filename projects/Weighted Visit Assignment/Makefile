PY ?= python3

.PHONY: help init demo test week drift debrief calibrate sensitivity sync verify-graph \
        install-automation uninstall-automation deck clean

help:
	@echo "ESD weighted visit assignment (v3)"
	@echo
	@echo "  make init                 write config/engine.json, create the audit db"
	@echo "  make demo                 synthetic lab, score + assign a week, log it all"
	@echo "  make test                 run the correctness anchors"
	@echo "  make week                 greedy vs optimiser with measured regret"
	@echo "  make sync                 pull calendars, record the SLO"
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

week:
	$(PY) -m esd_scheduler plan-week --verbose

sync:
	$(PY) -m esd_scheduler sync

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
	rm -rf data reports logs deck/math deck/_tex deck/math_dims.json
	find . -name '__pycache__' -type d -exec rm -rf {} +
