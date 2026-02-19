"""
OUTPUT MODULE
=============

Output management for the Agentic Loop Framework.

Handles:
- Run output storage (results, transcripts)
- Directory organization by agent and date
- Transcript generation
"""

from .manager import OutputManager, RunOutput

__all__ = [
    'OutputManager',
    'RunOutput'
]
