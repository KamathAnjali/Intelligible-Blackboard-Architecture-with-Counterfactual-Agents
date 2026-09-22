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
RUNNER := powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./run.ps1

.PHONY: help start chat query gpu latency samples pex personas stop

help:
	@echo make start                 Start Ollama if needed
	@echo make chat                  Open interactive chat - use /bye to exit
	@echo make query                 Send one example prompt
	@echo make gpu                   Show model placement and GPU logs
	@echo make latency RUNS=5        Measure one first request and 5 warm runs
	@echo make samples               Run the five Day 2 sample tasks
	@echo make pex PERSONA=aggressive_proposer RETRIES=2    Generate validated PEX
	@echo make personas              Run Day 4 checks - two personas, three tasks each
	@echo make stop                  Unload the model

start chat query gpu samples personas stop:
	@$(RUNNER) $@

latency:
	@$(RUNNER) latency -Runs "$(RUNS)"

pex:
	@$(RUNNER) pex -Persona "$(PERSONA)" -Retries "$(RETRIES)"
