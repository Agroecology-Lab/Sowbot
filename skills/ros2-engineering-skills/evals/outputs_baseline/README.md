# evals/outputs_baseline/

Paste captured model responses HERE — one file per eval name, with the
skill **NOT** loaded. Used as the baseline by `eval_runner.py --parity`
to compute the skill's value-add delta.

Procedure: same as `evals/outputs/` but in a session where this skill is
disabled. The parity test compares the two to detect skill regression
(see `docs/EVAL_WORKFLOW.md`).
