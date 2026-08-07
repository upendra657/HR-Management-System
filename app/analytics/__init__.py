"""Reporting and data quality.

Everything here aggregates across the whole company rather than one person,
so the arithmetic happens in SQL. The per-employee summaries in
services/employees.py can afford to sum sixty rows in Python; these cannot.
"""
