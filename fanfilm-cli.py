#!/usr/bin/env python3
import sys
import subprocess
import os

if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.abspath(__file__))
    tui_script = os.path.join(base_dir, "fanfilm_tui.py")
    
    venv_python = os.path.join(base_dir, ".venv", "bin", "python")
    python_exe = venv_python if os.path.exists(venv_python) else sys.executable
    
    args = [python_exe, tui_script] + sys.argv[1:]
    sys.exit(subprocess.call(args))

