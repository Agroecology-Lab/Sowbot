#!/usr/bin/env python3
import argparse
import os
from pathlib import Path

from livesync import Folder, sync

parser = argparse.ArgumentParser(description='Sync local code with robot.')
parser.add_argument('robot', help='Robot hostname')

args = parser.parse_args()
os.chdir(Path(__file__).resolve().parent.parent)  # sync the repo root, whatever the caller's cwd
folders = [Folder('.', f'{args.robot}:~/feldfreund_devkit_ros')]
sync(*folders)
