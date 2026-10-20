.PHONY: help activate_venv sync install-ci mypy pylint ruff lint pre-commit check test

default: help

## Available commands for this Makefile, use 'make <command>' to execute:

##
## ---------------

## help		Print commands help.
help: Makefile
	@sed -n 's/^##//p' $<

## sync		Install all dependencies for development and testing.
sync:
	pip install -r requirements-dev.txt

## install-ci	Install all dependencies for CI testing.
install-ci:
	pip install -r requirements-dev.txt

## mypy		Run mypy type checks.
mypy:
	mypy ./src/devkit_driver ./src/devkit_bringup ./src/devkit_ui ./src/devkit_mavlink_bridge ./src/devkit_f2c_planner --exclude 'setup\.py$$' --non-interactive --install-types

## lint-imports	Check UI layer dependency rules.
lint-imports:
	cd src/devkit_ui && lint-imports --config ../../.importlinter

## pylint		Run pylint code analysis.
pylint:
	pylint \
		--disable=duplicate-code \
		--disable=import-error \
		./src/devkit_driver/devkit_driver \
		./src/devkit_ui/devkit_ui \
		./src/devkit_ui/test/*.py \
		./src/devkit_mavlink_bridge/devkit_mavlink_bridge \
		./src/devkit_f2c_planner/devkit_f2c_planner

## ruff		Run ruff code analysis.
ruff:
	find ./src/devkit_driver/devkit_driver -name '*.py' | xargs ruff check
	find ./src/devkit_bringup/launch -name '*.py' | xargs ruff check
	find ./src/devkit_ui/devkit_ui -name '*.py' | xargs ruff check
	ruff check ./src/devkit_mavlink_bridge ./src/devkit_f2c_planner ./src/devkit_simulation ./src/devkit_driver/test ./src/devkit_ui/test manage.py tests

ruff-fix:
	find ./src/devkit_driver/devkit_driver -name '*.py' | xargs ruff check --fix
	find ./src/devkit_bringup/launch -name '*.py' | xargs ruff check --fix
	find ./src/devkit_ui/devkit_ui -name '*.py' | xargs ruff check --fix
	ruff check --fix ./src/devkit_mavlink_bridge ./src/devkit_f2c_planner ./src/devkit_simulation ./src/devkit_driver/test ./src/devkit_ui/test manage.py tests

## pre-commit	Run pre-commit hooks on all files.
pre-commit:
	pre-commit run --all-files

## test		Run unit tests.
test:
	cd src/devkit_ui && python -m unittest discover -s test
	cd src/devkit_f2c_planner && python -m unittest discover
	cd src/devkit_mavlink_bridge && python -m unittest discover -s test
	cd src/devkit_driver && python -m unittest discover -s test
	python -m pytest -q tests

## check		Run all code checks (mypy, pre-commit, pylint).
check: mypy pre-commit pylint test
