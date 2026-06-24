#!/usr/bin/env python3
"""
Ibis Publisher — development launcher.
Run this directly without building: python run.py
"""
import sys
from pathlib import Path

# Ensure companion-app is on the path
sys.path.insert(0, str(Path(__file__).parent / 'companion-app'))

from app import main

if __name__ == '__main__':
    main()
