# Use the host shell for argument quoting, then invoke Windows PowerShell.
# WSL shares the Windows Ollama server and Python environment through run.ps1.
ifeq ($(OS),Windows_NT)
SHELL := cmd.exe
.SHELLFLAGS := /c
else
SHELL := /bin/sh
.SHELLFLAGS := -c
endif
.DEFAULT_GOAL := help

RUNS ?= 3
PERSONA ?= cautious_verifier
RETRIES ?= 2
TURNS ?= 6
AB_TURNS ?= 10
COUNTERBENCH_TURNS ?= 10
COUNTERBENCH_SET ?= pilot
BBH_TASKS ?= 5
BBH_START ?= 0
BBH_TURNS ?= 12
BBH_CONDITION ?= off
AGENTS ?= 2
RUNNER := powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./run.ps1

.PHONY: help start chat query gpu latency samples pex entry conversation disagreement demo personas sessions deadlocks deadlock-ab counterbench-setup counterbench bbh-setup bbh-check bbh bbh-fallacies bbh-causal test stop

help:
	@echo make start                 Start Ollama if needed
	@echo make chat                  Open interactive chat - use /bye to exit
	@echo make query                 Send one example prompt
	@echo make gpu                   Show model placement and GPU logs
	@echo make latency RUNS=5        Measure one first request and 5 warm runs
	@echo make samples               Run five sample prompts
	@echo make pex PERSONA=aggressive_proposer RETRIES=2    Generate validated PEX
	@echo make personas              Check all four personas on three tasks each
	@echo make entry                 Preview one complete BoardEntry without posting
	@echo make conversation TURNS=6  Run a live two-agent blackboard session
	@echo make conversation AGENTS=3 Run an unseeded three-agent session
	@echo make sessions             Run four live three-agent GSM8K and mSCoRe cases
	@echo make deadlocks            Record two controlled deadlock fixtures, without Ollama
	@echo make deadlock-ab AB_TURNS=10 Compare recovery disabled and enabled
	@echo make counterbench-setup   Download and verify CounterBench data
	@echo make counterbench         Run five paired CounterBench cases
	@echo make counterbench COUNTERBENCH_SET=extended  Run 30 paired CounterBench cases
	@echo make bbh-setup            Download and verify both BBH datasets
	@echo make bbh-check            Check local BBH data without inference
	@echo make bbh-fallacies BBH_TASKS=5  Run formal-fallacy questions, recovery off
	@echo make bbh-causal BBH_TASKS=5     Run causal-judgment questions, recovery off
	@echo make bbh BBH_TASKS=15     Run 15 questions per suite, recovery off
	@echo make bbh BBH_CONDITION=both    Compare recovery off and on
	@echo make disagreement TURNS=6  Run a seeded three-agent disagreement scenario
	@echo make demo                  Require a clean three-turn RATIFY demonstration
	@echo make test                  Run offline tests without requiring Ollama
	@echo make stop                  Unload the model

start chat query gpu samples personas test stop:
	@$(RUNNER) $@

latency:
	@$(RUNNER) latency -Runs "$(RUNS)"

pex:
	@$(RUNNER) pex -Persona "$(PERSONA)" -Retries "$(RETRIES)"

entry:
	@$(RUNNER) entry -Persona "$(PERSONA)" -Retries "$(RETRIES)"

conversation:
	@$(RUNNER) conversation -Turns "$(TURNS)" -Retries "$(RETRIES)" -Agents "$(AGENTS)"

sessions:
	@$(RUNNER) sessions -Turns "$(TURNS)" -Retries "$(RETRIES)"

deadlocks:
	@$(RUNNER) deadlocks

deadlock-ab:
	@$(RUNNER) deadlock-ab -Turns "$(AB_TURNS)" -Retries "$(RETRIES)"

counterbench-setup:
	@$(RUNNER) counterbench-setup -CounterBenchSet "$(COUNTERBENCH_SET)"

counterbench:
	@$(RUNNER) counterbench -CounterBenchSet "$(COUNTERBENCH_SET)" -Turns "$(COUNTERBENCH_TURNS)" -Retries "$(RETRIES)"

bbh-setup bbh-check bbh bbh-fallacies bbh-causal:
	@$(RUNNER) $@ -BbhTasks "$(BBH_TASKS)" -BbhStart "$(BBH_START)" -BbhCondition "$(BBH_CONDITION)" -Turns "$(BBH_TURNS)" -Retries "$(RETRIES)"

disagreement:
	@$(RUNNER) disagreement -Turns "$(TURNS)" -Retries "$(RETRIES)"

demo:
	@$(RUNNER) demo -Retries "$(RETRIES)"
