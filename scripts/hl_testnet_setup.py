"""Provision a Hyperliquid testnet wallet and verify connectivity.

Usage:
    python scripts/hl_testnet_setup.py [--key-out /opt/pmbot/.hl_testnet_key.json]

What it does:
1. Generates a fresh secp256k1 wallet (or reuses one from --key-in).
2. Writes the key file with mode 0600.
3. Prints the address that needs to be funded from the Hyperliquid testnet
   faucet (https://app.hyperliquid-testnet.xyz/drip).
4. Probes the testnet info endpoint to confirm the SDK can reach it.

After funding the wallet on the faucet:
- Append HL_PRIVATE_KEY=0x... to /opt/pmbot/.env
- systemctl restart pmbot-e
- Switch execution.mode to HYPERLIQUID_TESTNET in app_model_e_staging.yaml
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path

try:
    import eth_account
except ImportError:
    print("eth_account not installed; run: pip install eth-account", file=sys.stderr)
    sys.exit(2)

try:
    from hyperliquid.info import Info
    from hyperliquid.utils import constants
except ImportError:
    print("hyperliquid SDK not installed; run: pip install hyperliquid-python-sdk", file=sys.stderr)
    sys.exit(2)


def generate_wallet() -> tuple[str, str]:
    acct = eth_account.Account.create()
    return acct.address, acct.key.hex()


def load_wallet(path: Path) -> tuple[str, str]:
    payload = json.loads(path.read_text())
    return payload["address"], payload["private_key"]


def write_wallet(path: Path, address: str, private_key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"address": address, "private_key": private_key}, indent=2))
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except Exception:
        pass


def probe_testnet(address: str) -> None:
    info = Info(constants.TESTNET_API_URL, skip_ws=True)
    try:
        state = info.user_state(address) or {}
        margin = state.get("marginSummary") or {}
        account_value = margin.get("accountValue") or 0.0
        print(f"  testnet user_state: account_value=${float(account_value):.2f} "
              f"open_positions={len(state.get('assetPositions') or [])}")
    except Exception as exc:
        print(f"  testnet user_state probe failed: {exc}")
    try:
        meta = info.meta_and_asset_ctxs()
        if isinstance(meta, list) and len(meta) == 2:
            universe = meta[0].get("universe", [])
            ctxs = meta[1]
            for i, a in enumerate(universe):
                if a.get("name") == "BTC":
                    ctx = ctxs[i] if i < len(ctxs) else {}
                    print(f"  testnet BTC mark_px={ctx.get('markPx')}  oracle={ctx.get('oraclePx')}  "
                          f"funding={ctx.get('funding')}")
                    break
    except Exception as exc:
        print(f"  testnet meta probe failed: {exc}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--key-in", type=Path, help="Reuse a wallet json {address, private_key}")
    p.add_argument("--key-out", type=Path, default=Path("/opt/pmbot/.hl_testnet_key.json"))
    p.add_argument("--probe-only", action="store_true", help="Only probe an existing key file, no write")
    args = p.parse_args()

    if args.key_in:
        address, private_key = load_wallet(args.key_in)
        print(f"Loaded wallet from {args.key_in}")
    elif args.probe_only:
        if not args.key_out.exists():
            print(f"No wallet file at {args.key_out}", file=sys.stderr)
            return 2
        address, private_key = load_wallet(args.key_out)
        print(f"Probing wallet from {args.key_out}")
    else:
        address, private_key = generate_wallet()
        write_wallet(args.key_out, address, private_key)
        print(f"New wallet written: {args.key_out}")

    print(f"  address: {address}")
    print(f"  private_key: 0x{private_key[2:] if private_key.startswith('0x') else private_key}")
    print()
    print("Next steps:")
    print(f"  1. Fund the address on the faucet: https://app.hyperliquid-testnet.xyz/drip")
    print(f"     (paste {address} and request 100 USDC)")
    print(f"  2. Append to /opt/pmbot/.env (key MUST start with 0x):")
    pk_with_prefix = private_key if private_key.startswith("0x") else f"0x{private_key}"
    print(f"        HL_PRIVATE_KEY={pk_with_prefix}")
    print(f"  3. systemctl restart pmbot-e")
    print(f"  4. Edit config/app_model_e_staging.yaml: execution.mode: HYPERLIQUID_TESTNET")
    print()
    print("Probing testnet...")
    probe_testnet(address)
    return 0


if __name__ == "__main__":
    sys.exit(main())
