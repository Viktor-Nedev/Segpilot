"""Measure which of Paritok's steering parameters the hosted GPU actually honours.

    python -m segpilot.probe --all

This is the experiment SEGPILOT's design rests on, kept runnable so anyone can
re-derive the numbers in docs/findings.md instead of taking our word for it.

Method: hold the content constant, vary exactly one parameter, compare outputs
byte-for-byte. Compression runs at temperature 0, so identical output across
different parameter values means the parameter did not reach the model.

Calls go through `GpuServerStrategy` -- Paritok's own client -- rather than raw
HTTP, so we are measuring the real code path, and deliberately NOT through
`CompressionPipeline`, whose cache would mask the differences (finding 2).
"""

from __future__ import annotations

import argparse
import sys

from paritok.strategies.gpu_server import GpuServerStrategy
from paritok.token_counter import count_tokens

from segpilot.config import SegpilotConfig

# Three unrelated functions. If intent steers keep/drop, asking about one of
# them should preserve that one and discard the others -- a visible, semantic
# outcome rather than just a token delta.
THREE_FUNCTIONS = '''\
import logging
logger = logging.getLogger(__name__)

def authenticate_user(username, password, session_store):
    """Authenticate and open a session."""
    logger.debug("auth attempt for %s", username)
    user = UserRepository.find_by_username(username)
    if user is None:
        raise AuthenticationError("no such user")
    if not verify_password_hash(password, user.password_hash):
        raise AuthenticationError("bad password")
    token = session_store.create_session(user.id, ttl_seconds=3600)
    logger.info("session opened for %s", username)
    return token


def rotate_encryption_keys(keyring, kms_client):
    """Rotate all data-encryption keys against the KMS."""
    logger.debug("starting key rotation")
    for key_id in keyring.list_active_key_ids():
        new_material = kms_client.generate_data_key(key_id)
        keyring.stage_rotation(key_id, new_material)
    keyring.commit_rotation()
    logger.info("key rotation complete")


def export_metrics_snapshot(registry, destination_path):
    """Write a Prometheus snapshot to disk."""
    logger.debug("exporting metrics")
    payload = registry.render_prometheus_text()
    with open(destination_path, "w") as handle:
        handle.write(payload)
    logger.info("metrics written to %s", destination_path)
'''

FUNCS = ("authenticate_user", "rotate_encryption_keys", "export_metrics_snapshot")

DEFAULTS = {"query": "debug the authentication flow", "kind": "file_read", "level": "L1"}


def _hr(t: str) -> None:
    print(f"\n{'=' * 70}\n{t}\n{'=' * 70}")


class Probe:
    def __init__(self, cfg: SegpilotConfig):
        self.strategy = GpuServerStrategy(cfg.to_paritok_config().gpu_server)
        self.base_tokens = count_tokens(THREE_FUNCTIONS)

    def compress(self, **over) -> str:
        kw = {**DEFAULTS, **over}
        return self.strategy.compress(THREE_FUNCTIONS, **kw)

    def sweep(self, param: str, values: list, label: str | None = None) -> dict:
        """Vary one parameter; report tokens and whether outputs are distinct."""
        _hr(f"Parameter: {label or param}")
        print(f"   baseline content: {self.base_tokens} tokens\n")
        out: dict[str, str] = {}
        for v in values:
            body = self.compress(**{param: v})
            out[repr(v)] = body
            kept = [f for f in FUNCS if f in body]
            tok = count_tokens(body)
            print(
                f"   {param}={repr(v):<14} {tok:>4} tok  ratio {tok / self.base_tokens:.3f}"
                f"   kept: {', '.join(k.split('_')[0] for k in kept) or 'none'}"
            )
        distinct = len(set(out.values()))
        print(f"\n   -> {distinct} distinct output(s) across {len(values)} values")
        if distinct == 1:
            print(f"   -> VERDICT: `{param}` is IGNORED by the hosted server.")
        else:
            print(f"   -> VERDICT: `{param}` is HONOURED.")
        return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Which Paritok levers actually work?")
    ap.add_argument("--all", action="store_true", help="run every probe")
    ap.add_argument("--level", action="store_true")
    ap.add_argument("--kind", action="store_true")
    ap.add_argument("--query", action="store_true")
    ap.add_argument("--target-ratio", action="store_true")
    args = ap.parse_args(argv)
    if not any([args.all, args.level, args.kind, args.query, args.target_ratio]):
        args.all = True

    cfg = SegpilotConfig.load()
    available, message = GpuServerStrategy(cfg.to_paritok_config().gpu_server).check()
    if not available:
        print(f"hosted GPU unavailable: {message}")
        return 1

    probe = Probe(cfg)

    if args.all or args.query:
        # The headline result: intent decides what survives.
        probe.sweep(
            "query",
            [
                "why does authenticate_user reject valid passwords",
                "debug the KMS key rotation logic",
                "fix the prometheus metrics export path",
                "",
            ],
            label="query (user intent)",
        )
    if args.all or args.kind:
        probe.sweep("kind", ["file_read", "log_output", "assistant_thinking"])
    if args.all or args.level:
        # "BANANA" is the tell: an honoured, validated parameter would reject it.
        probe.sweep("level", ["L0", "L1", "L2", "L3", None, "BANANA"])
    if args.all or args.target_ratio:
        probe.sweep("target_ratio", ["0.5", "0.2", "10%", None])

    _hr("Interpretation")
    print("   Compression runs at temperature 0. Byte-identical output across")
    print("   different values of a parameter means that parameter never reached")
    print("   the model. See docs/findings.md for the full write-up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
