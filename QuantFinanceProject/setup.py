# QuantFinanceProject/setup.py
from setuptools import setup, find_packages

setup(
    name='QuantFinanceProject',
    version='0.1',
    packages=find_packages(include=[
        'macro_playbook_agent',
        'market_data_agent',
        'earnings_agent'
    ]),
)