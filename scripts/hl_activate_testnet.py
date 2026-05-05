"""Flip Bot E from HYPERLIQUID_DRY_RUN to HYPERLIQUID_TESTNET.

Run this once the testnet wallet is funded:
    1. Fund 0xDd876... on https://app.hyperliquid-testnet.xyz/drip
    2. python scripts/hl_activate_testnet.py
    3. systemctl restart pmbot-e

What it does:
- Verifies the wallet has testnet USDC > 1.0
- Patches config/app_model_e_staging.yaml: execution.mode -> HYPERLIQUID_TESTNET
- Prints the next-restart command (does not restart automatically).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

CONFIG_PATH = Path("/opt/pmbot/config/app_model_e_staging.yaml")


def load_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.strip().partition("=")
            if k:
                os.environ[k] = v


def check_balance(min_usdc: float = 1.0) -> float:
    try:
        import eth_account
        from hyperliquid.info import Info
        from hyperliquid.utils import constants
    except ImportError as exc:
        print(f"hyperliquid SDK missing: {exc}", file=sys.stderr)
        sys.exit(2)

    priv = os.environ.get("HL_PRIVATE_KEY", "")
    if not priv:
        print("HL_PRIVATE_KEY not set in environment", file=sys.stderr)
        sys.exit(2)
    if not priv.startswith("0x"):
        priv = "0x" + priv
    address = eth_account.Account.from_key(priv).address

    info = Info(constants.TESTNET_API_URL, skip_ws=True)
    state = info.user_state(address) or {}
    margin = state.get("marginSummary") or {}
    balance = float(margin.get("accountValue") or 0.0)
    print(f"  testnet wallet: {address}")
    print(f"  account_value:  ${balance:.4f}")
    if balance < min_usdc:
        print(f"  ✗ balance below ${min_usdc:.2f} — fund first via faucet")
        sys.exit(1)
    print(f"  ✓ funded (>= ${min_usdc:.2f})")
    return balance


def patch_config_to_testnet(config_path: Path) -> None:
    text = config_path.read_text()
    if "HYPERLIQUID_TESTNET" in text and "HYPERLIQUID_DRY_RUN" not in text:
        print(f"  config already on TESTNET")
        return
    new_text = text.replace("HYPERLIQUID_DRY_RUN", "HYPERLIQUID_TESTNET")
    if new_text == text:
        print(f"  ! could not find HYPERLIQUID_DRY_RUN in {config_path}")
        sys.exit(2)
    config_path.write_text(new_text)
    print(f"  ✓ {config_path} -> HYPERLIQUID_TESTNET")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=CONFIG_PATH)
    p.add_argument("--env",    type=Path, default=Path("/opt/pmbot/.env"))
    p.add_argument("--min-usdc", type=float, default=1.0)
    p.add_argument("--check-only", action="store_true")
    args = p.parse_args()

    load_env(args.env)

    print("Checking testnet balance...")
    check_balance(min_usdc=args.min_usdc)

    if args.check_only:
        return 0

    print("\nPatching config...")
    patch_config_to_testnet(args.config)

    print("\nNext step:")
    print("  systemctl restart pmbot-e")
    return 0


if __name__ == "__main__":
    sys.exit(main())
