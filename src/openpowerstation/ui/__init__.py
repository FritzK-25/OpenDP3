"""Presentation layer for the OpenPowerstation desktop application.

Everything visual lives here so that :mod:`openpowerstation.gui` stays a thin shell around
the recorder service. Colours exist only in :mod:`openpowerstation.ui.theme`; every other
module in this package takes them from a token mapping.
"""
