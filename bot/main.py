"""
Entry point for the Adaptive Wheel Strategy bot.
Called by GitHub Actions scheduler with --task argument.

Usage:
  python -m bot.main --task morning_scan
  python -m bot.main --task midmorning_check
  python -m bot.main --task midday_review
  python -m bot.main --task afternoon_check
  python -m bot.main --task preclose
"""
import argparse
import logging
import sys
from datetime import datetime
import pytz

from bot.config import LOG_DIR
from bot.market_data import is_market_open
from bot.strategy import WheelStrategy

# ── Logging Setup ─────────────────────────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)
log_file = LOG_DIR / f"bot_{datetime.utcnow().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

TASKS = {
    "morning_scan":      lambda s: s.morning_scan(),
    "midmorning_check":  lambda s: s.midmorning_check(),
    "midday_review":     lambda s: s.midday_review(),
    "afternoon_check":   lambda s: s.afternoon_check(),
    "preclose":          lambda s: s.preclose(),
}


def main():
    parser = argparse.ArgumentParser(description="Adaptive Wheel Strategy Bot")
    parser.add_argument(
        "--task",
        required=True,
        choices=list(TASKS.keys()),
        help="Which scheduled task to run",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even if market appears closed (for testing)",
    )
    args = parser.parse_args()

    et = pytz.timezone("America/New_York")
    now_et = datetime.now(et)
    logger.info(f"=== BOT START | task={args.task} | ET={now_et.strftime('%Y-%m-%d %H:%M')} ===")

    if not args.force and not is_market_open():
        logger.info("Market is closed — skipping task (use --force to override)")
        return 0

    try:
        strategy = WheelStrategy()
        TASKS[args.task](strategy)
        logger.info(f"=== BOT DONE | task={args.task} ===")
        return 0
    except Exception as e:
        logger.exception(f"Unhandled exception in task {args.task}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
