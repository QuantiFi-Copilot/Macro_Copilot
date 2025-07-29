#!/usr/bin/env python3
"""
normalization_engine.py

Coordinates the unit and label normalization steps in sequence.
No quality engine logic included yet.
"""
import argparse
import logging
import sys

# Imports for the two normalization stages
from earnings_agent.normalization.unit_normalizer import run_unit_normalizer_batch
from earnings_agent.normalization.label_normalizer import run_label_normalizer_batch

# Standard logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(module)s - %(message)s"
)
logger = logging.getLogger(__name__)


def main(allow_llm: bool):
    """
    Orchestrate the normalization pipeline:
      1) Unit normalization on all pending documents
      2) Label normalization on all pending documents

    `allow_llm` controls whether the label normalizer is allowed to call the LLM for new mappings.
    """
    logger.info("=== Starting Normalization Engine ===")

    # Step 1: Unit normalization
    try:
        logger.info("-- Running unit normalization pass --")
        run_unit_normalizer_batch()
        logger.info("-- Unit normalization complete --")
    except Exception:
        logger.exception("Error during unit normalization")
        sys.exit(1)

    # Step 2: Label normalization
    try:
        logger.info(f"-- Running label normalization pass (allow_llm={allow_llm}) --")
        run_label_normalizer_batch(allow_llm=allow_llm)
        logger.info("-- Label normalization complete --")
    except Exception:
        logger.exception("Error during label normalization")
        sys.exit(2)

    logger.info("=== Normalization Engine finished successfully ===")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Run normalization engine: unit + label normalization"
    )
    parser.add_argument(
        '--allow-llm',
        action='store_true',
        help='Allow LLM calls for unmapped labels during this run'
    )
    args = parser.parse_args()
    main(allow_llm=args.allow_llm)
