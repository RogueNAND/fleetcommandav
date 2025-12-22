"""
FleetCommand Framework - Core library for FleetCommandAV automation system.
"""

from .companion import Companion

# Create singleton companion instance
companion = Companion()

__version__ = "1.0.0"
__all__ = ["companion", "Companion"]