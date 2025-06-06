# QuantFinanceProject Setup Guide

## Project Architecture

The QuantFinanceProject is a modular Python-based financial intelligence system organized into separate agent layers. Each agent is a self-contained Python package with its own signal processing pipeline, data catalogs, and analysis logic.

### Directory Structure

```
QuantFinanceProject/
├── setup.py                  # Project-level setup file
├── macro_playbook_agent/     # Example agent module
│   ├── __init__.py           # Package initialization
│   ├── data/                 # Raw data storage
│   ├── ingestion/            # Data ingestion scripts
│   ├── insights/             # Generated insights
│   ├── manifest/             # Status tracking and metadata
│   ├── modeling/             # Statistical models
│   ├── processing/           # Data processing scripts
│   │   └── trend_engine.py   # Signal transformation engine
│   ├── prompts/              # Templates for AI interactions
│   ├── reasoning/            # Reasoning modules
│   ├── requirements.txt      # Agent-specific dependencies
│   ├── signals/              # Signal processing
│   │   └── transforms.py     # Signal transformation functions
│   ├── storage/              # Persistent storage
│   ├── streamlit/            # Visualization dashboards
│   ├── test_trend_engine.py  # Signal testing
│   ├── utils/                # Utility functions
│   │   ├── macro_catalog.csv # Macro event catalog
│   │   └── signal_catalog.csv # Signal definitions
│   └── .env                  # Environment configuration
```

### Agent Modularity

Each agent in the system is designed as a standalone Python package that can:
1. Ingest and process raw data
2. Transform data into signals
3. Apply trend analysis
4. Generate insights
5. Visualize results

Agents can interact with each other through shared databases or APIs, but are designed to function independently with their own processing logic and signal catalogs.

## Adding a New Agent Module

To add a new agent (e.g., `credit_playbook_agent`), follow these steps:

### 1. Folder Structure

Create a new directory following the naming convention `{domain}_playbook_agent`:

```
QuantFinanceProject/
└── credit_playbook_agent/
    ├── __init__.py
    ├── data/
    ├── ingestion/
    ├── insights/
    ├── manifest/
    ├── modeling/
    ├── processing/
    │   └── trend_engine.py
    ├── prompts/
    ├── reasoning/
    ├── requirements.txt
    ├── signals/
    │   └── transforms.py
    ├── storage/
    ├── streamlit/
    ├── utils/
    │   ├── credit_catalog.csv
    │   └── signal_catalog.csv
    └── .env
```

### 2. Required Files

At minimum, create these essential files:

1. `__init__.py` - Empty file to make the directory a Python package
2. `requirements.txt` - Agent-specific dependencies
3. `utils/signal_catalog.csv` - Signal definitions
4. `signals/transforms.py` - Signal transformation functions
5. `processing/trend_engine.py` - Signal processing engine
6. `.env` - Environment configuration (e.g., database URI)

### 3. Package Structure

#### Basic Package Setup

Create an `__init__.py` file in the root of your agent directory to make it a proper Python package.

#### Optional setup.py

If you want to install the agent as a package, create a `setup.py` file in the agent directory:

```python
from setuptools import setup, find_packages

setup(
    name='credit_playbook_agent',
    version='0.1',
    packages=find_packages(),
)
```

## Adding a New Signal to an Existing Agent

### 1. Raw Data Placement

Place new raw data CSV files in the `data/` directory of the agent:

```
macro_playbook_agent/data/new_signal_raw.csv
```

### 2. Update Signal Catalog

Add a new entry to the agent's `utils/signal_catalog.csv` file with the following fields:

```
ticker,signal_name,transformation_type,window,comments,analysis_freq,agg_method,effective_lag_days
NEWSIG,NEWSIG_YoY,yoy,12,Year-over-year growth,M,last,10
NEWSIG,NEWSIG_Z,zscore,36,Z-score vs 3-year mean,M,last,10
NEWSIG,NEWSIG_MA3,ma,3,3-month moving average,M,last,10
NEWSIG,NEWSIG_Delta,delta,1,Month-over-month change,M,last,10
```

Fields explained:
- `ticker`: Raw data identifier
- `signal_name`: Unique name for the transformed signal
- `transformation_type`: Type of transformation (yoy, ma, delta, zscore, volatility)
- `window`: Number of periods for the transformation
- `comments`: Description of the signal
- `analysis_freq`: Frequency of analysis (M=monthly, Q=quarterly)
- `agg_method`: Aggregation method (last, mean)
- `effective_lag_days`: Publication lag in days

### 3. Add Transforms (if needed)

If your signal requires a new transformation type, add it to `signals/transforms.py`:

```python
def compute_new_transform(series: pd.Series, window: int = 12) -> pd.Series:
    """
    Documentation for the new transformation.
    """
    # Implementation
    return transformed_series
```

Then update the transformation mapping in `processing/trend_engine.py`:

```python
fn_map = {
    'yoy': transforms.compute_yoy,
    'ma': transforms.compute_ma,
    'delta': transforms.compute_delta,
    'zscore': transforms.compute_zscore,
    'new_transform': transforms.compute_new_transform
}
```

### 4. Testing with Trend Engine

Test your new signal using the `test_trend_engine.py` script:

```bash
python -m macro_playbook_agent.test_trend_engine
```

This will validate that your signal is correctly computed and matches manual calculations.

## Conventions and Best Practices

### File Naming

- Use snake_case for Python files and directories
- Use descriptive names that indicate functionality
- Prefix test files with `test_`

### Testing

- Create test scripts for each major component
- Use `test_trend_engine.py` to validate signal transformations
- Implement unit tests for critical functions

### Environment Configuration

- Store database URIs and API keys in `.env` files
- Never commit `.env` files to version control
- Include a `.env.example` template

### Logging

- Use Python's built-in logging module
- Configure log levels appropriately
- Log important events, errors, and warnings

### Version Control

- Use feature branches for new development
- Include descriptive commit messages
- Tag releases with semantic versioning

### Modular Design

- Keep agent modules independent
- Use dependency injection where possible
- Design for testability and reusability
- Separate data ingestion from transformation and analysis

### Documentation

- Document all functions with docstrings
- Maintain a README.md for each agent
- Include examples of common operations
- Document signal catalogs with clear descriptions
