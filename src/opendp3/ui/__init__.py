"""Presentation layer for the OpenPowerstation desktop application.

Everything visual lives here so that :mod:`opendp3.gui` stays a thin shell around
the recorder service. Colours exist only in :mod:`opendp3.ui.theme`; every other
module in this package takes them from a token mapping.
"""
