# Windows task shortcuts. Invoke PowerShell explicitly so .ps1 files execute.
SHELL := cmd.exe
.SHELLFLAGS := /c
.DEFAULT_GOAL := help

RUNS ?= 3
RUNNER := powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./run.ps1

.PHONY: help start chat query gpu latency samples stop

help:
	@echo make start                 Start Ollama if needed
	@echo make chat                  Open interactive chat; /bye to exit
	@echo make query                 Send one example prompt
	@echo make gpu                   Show model placement and GPU logs
	@echo make latency RUNS=5        Measure one first request and 5 warm runs
	@echo make samples               Run the five Day 2 sample tasks
	@echo make stop                  Unload the model

start chat query gpu samples stop:
	@$(RUNNER) $@

latency:
	@$(RUNNER) latency -Runs "$(RUNS)"
