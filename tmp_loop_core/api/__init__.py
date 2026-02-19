"""
API MODULE
==========

FastAPI REST API for the Agentic Loop Framework.

Usage:
    uvicorn loop_core.api:app --reload

Or:
    python -m loop_core.api
"""

from .app import app, create_app

__all__ = ['app', 'create_app']
