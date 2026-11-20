"""Run from the repo root:  .venv/bin/python -m pytest 3_action_items 6_evaluation/action_items"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
for folder in ["3_action_items", "6_evaluation/action_items", "5_data/ami"]:
    sys.path.insert(0, os.path.join(REPO, folder))
