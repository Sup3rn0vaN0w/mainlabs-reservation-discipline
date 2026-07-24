# T-055 simulator - single test/run entry point (brief Section 7).
# House style: hyphens only (D-026).
#
# All targets run from the repo root so `import sim...` resolves, using the
# project-local virtualenv at code/sim/.venv.

PY := $(CURDIR)/.venv/bin/python
ROOT := $(CURDIR)/..
WORKERS ?= 6
ARGS ?=

.PHONY: test sanity littles util validate tune pilot grid-project grid-run h1-readout h2-run h3-run ablations-run clean

## test   : run the full unit + sanity test suite
test:
	cd $(ROOT) && $(PY) -m pytest sim/tests -q

## sanity : run both SG1 sanity checks and print their tables
sanity: littles util

littles:
	cd $(ROOT) && $(PY) -m sim.sanity.littles_law

util:
	cd $(ROOT) && $(PY) -m sim.sanity.utilization_curve

## validate : generate the SG2 workload validation report + plots
validate:
	cd $(ROOT) && $(PY) -m sim.workload.validate

## tune   : run the SG5 equal-budget tuning harness -> docs/TUNING_REPORT.md
tune:
	cd $(ROOT) && $(PY) -m sim.tuning.report

## pilot  : run the SG6 pilot grid + sensitivity -> docs/PILOT_READOUT.md
pilot:
	cd $(ROOT) && $(PY) -m sim.experiments.pilot

## grid-project : re-project the SG7 main-grid wall time + 24h guard (WORKERS=N)
grid-project:
	cd $(ROOT) && $(PY) -m sim.experiments.run_main_grid --project --workers $(WORKERS)

## grid-run : run the SG7 main grid, resumable (WORKERS=N; set SIM_INSTANCE_ID)
grid-run:
	cd $(ROOT) && $(PY) -m sim.experiments.run_main_grid --run --workers $(WORKERS)

## h1-readout : generate the SG7 H1 verdict from the manifest set -> docs/H1_READOUT.md
h1-readout:
	cd $(ROOT) && $(PY) -m sim.analysis.h1_readout $(ARGS)

## h2-run / h3-run : run the H2 load sweep / H3 adversarial sweep (local demo)
h2-run:
	cd $(ROOT) && $(PY) -m sim.experiments.run_main_grid --run --config grid_h2.yaml --results-subdir h2 --workers $(WORKERS)

h3-run:
	cd $(ROOT) && $(PY) -m sim.experiments.run_main_grid --run --config grid_h3.yaml --results-subdir h3 --workers $(WORKERS)

## ablations-run : run the A1-A5 ablation sweep -> results/ablations/
ablations-run:
	cd $(ROOT) && $(PY) -c "from sim.experiments.ablations import run_ablations; from sim.core.orchestration import load_yaml, CONFIG_DIR; print('ran', run_ablations(load_yaml(CONFIG_DIR/'ablations.yaml')))"

## clean  : remove Python caches
clean:
	find $(CURDIR) -type d -name __pycache__ -prune -exec rm -rf {} +
	find $(CURDIR) -type d -name .pytest_cache -prune -exec rm -rf {} +
