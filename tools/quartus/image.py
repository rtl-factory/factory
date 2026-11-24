"""The image identity: a passing SOF bound to its commit, inputs, evidence and Quartus version.

After the report gate passes, write_identity() copies inputs.json and
evidence.json beside the SOF as <project>.inputs.json and <project>.evidence.json
and writes the receipt <project>.json with their SHA-256 hashes. Transfer the
four same-stem files together; verify_image() needs only them and the current
manifest. Spec: wiki/tools/quartus/spec.md.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from tools.quartus import build, gate

RECEIPT_SCHEMA = "quartus-sof/1"
RECEIPT_FIELDS = {"schema", "project", "board", "device", "source_sha", "source_dirty", "quartus_version",
                  "sha256", "inputs_sha256", "evidence_sha256"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def companions(sof: Path) -> dict:
    """The receipt and the two hashed files that travel with a SOF."""
    return {"receipt": sof.with_suffix(".json"), "inputs": sof.with_suffix(".inputs.json"),
            "evidence": sof.with_suffix(".evidence.json")}


def write_identity(output: Path, config: dict, source: dict) -> dict:
    """Bind the passing image in output to its source and copy its hashed inputs and evidence beside it."""
    sof = output / "output_files" / f'{config["project"]}.sof'
    files = companions(sof)
    files["inputs"].write_bytes((output / "inputs.json").read_bytes())
    files["evidence"].write_bytes((output / "evidence.json").read_bytes())
    record = {"schema": RECEIPT_SCHEMA, "project": config["project"], "board": config["board"],
              "device": config["device"], **source, "sha256": digest(sof),
              "inputs_sha256": digest(files["inputs"]), "evidence_sha256": digest(files["evidence"])}
    files["receipt"].write_text(json.dumps(record, indent=2) + "\n")
    return verify_image(sof, config)


def verify_image(sof: Path, config: dict) -> dict:
    """The receipt of a SOF built from this config that passed the full gate; ValueError otherwise."""
    files = companions(sof)
    if not files["receipt"].is_file() or files["receipt"].is_symlink():
        raise ValueError("SOF needs its source-bound .json identity receipt before programming")
    try:
        identity = json.loads(files["receipt"].read_text())
    except ValueError as error:
        raise ValueError(f"malformed SOF identity receipt: {error}") from error
    if isinstance(identity, dict) and identity.get("schema") != RECEIPT_SCHEMA:
        raise ValueError(f"SOF identity receipt has schema {identity.get('schema')!r}, not {RECEIPT_SCHEMA}: "
                         "rebuild the image")
    if (not isinstance(identity, dict) or set(identity) != RECEIPT_FIELDS or type(identity["source_dirty"]) is not bool
            or not isinstance(identity["quartus_version"], str) or not identity["quartus_version"].strip()
            or not isinstance(identity["source_sha"], str) or not re.fullmatch(r"[0-9a-f]{40}", identity["source_sha"])
            or any(not isinstance(identity[key], str) or not re.fullmatch(r"[0-9a-f]{64}", identity[key])
                   for key in ("sha256", "inputs_sha256", "evidence_sha256"))):
        raise ValueError("incomplete or malformed source-bound SOF identity receipt")
    if (identity["project"], identity["board"], identity["device"]) != (
            config["project"], config["board"], config["device"]):
        raise ValueError("SOF identity does not match the selected board and project")
    for path, key in ((sof, "sha256"), (files["inputs"], "inputs_sha256"), (files["evidence"], "evidence_sha256")):
        if not path.is_file() or path.is_symlink() or digest(path) != identity[key]:
            raise ValueError(f"SOF identity/hash mismatch or missing transferred file: {path.name}")
    try:
        inputs = json.loads(files["inputs"].read_text())
        evidence = json.loads(files["evidence"].read_text())
    except ValueError as error:
        raise ValueError(f"malformed transferred inputs/evidence: {error}") from error
    # Why: the board must run what the reviewed manifest describes, not an image whose
    # pins, constraints or warning policy differ from the checkout's.
    if not isinstance(inputs, dict) or inputs.get("config") != json.loads(json.dumps(config)):
        raise ValueError("SOF inputs differ from the selected manifest; rebuild the image")
    hashes = inputs.get("sha256")
    if (set(inputs) != {"config", "sha256"} or not isinstance(hashes, dict)
            or set(hashes) != set(build.staged_files(config))
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes.values())):
        raise ValueError("malformed transferred inputs: fields or source hashes")
    gate.check_evidence(evidence, config)
    return identity
