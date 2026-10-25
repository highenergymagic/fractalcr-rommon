#!/usr/bin/env python3
"""Require current-artifact evidence before publishing a validation summary."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--artifacts", type=Path, default=Path("/work/artifacts"))
p.add_argument("--native", action="store_true")
a = p.parse_args()
root = a.artifacts
cases = [
    "trusted", "unsigned-rommon", "insecure-firmware", "unsigned-inner-kernel",
    "optional-firmware", "optional-unsigned-kernel",
    "usb-persist", "usb-boot", "usb-hotplug", "tftp-virtio", "tftp-e1000",
    "break", "env-fallback",
]

def digest(name):
    return hashlib.sha256((root / name).read_bytes()).hexdigest()

images = {name: {"bytes": (root / name).stat().st_size, "sha256": digest(name)}
          for name in ("rommon.efi", "system.bin")}
reproducibility = None
if (root / "reproducibility.json").exists():
    reproducibility = json.loads((root / "reproducibility.json").read_text())
    assert reproducibility["passed"], "Reproducibility check failed"
    assert set(reproducibility["artifacts"]) == {"rommon.unsigned.efi", "rommon.efi", "witness.efi", "system.bin"}, "Incomplete reproducibility artifacts"
    for name, expected in reproducibility["artifacts"].items():
        assert digest(name) == expected, f"Stale reproducibility result: {name}"
results = []
for case in cases:
    result = json.loads((root / f"result-{case}.json").read_text())
    assert result["case"] == case and result["passed"], f"Failed/missing result: {case}"
    efi = "rommon.unsigned.efi" if case == "unsigned-rommon" else "rommon.efi"
    system = "unsigned-inner-kernel.bin" if case in ("unsigned-inner-kernel", "optional-unsigned-kernel") else "system.bin"
    assert result["efi_sha256"] == digest(efi), f"Stale EFI evidence: {case}"
    assert result["system_sha256"] == digest(system), f"Stale system evidence: {case}"
    results.append(result)
assert "VERIFIER_TESTS_OK" in (root / "verifier-tests.log").read_text()
assert "MONITOR_INPUT_TESTS_OK" in (root / "monitor-input-tests.log").read_text()
assert (root / "service-graph-tests.log").read_text().count("PASS ") == 7
assert (root / "service-graph-musl-tests.log").read_text().count("PASS ") == 7
assert (root / "control-tests.log").read_text().count("PASS ") == 3
assert (root / "control-musl-tests.log").read_text().count("PASS ") == 3
if a.native:
    native = json.loads((root / "result-native.json").read_text())
    assert native["passed"]
    for name, info in images.items():
        assert native[name + "_sha256"] == info["sha256"], "Stale native QEMU result"
    results.append(native)
report = {
    "generated_utc": datetime.now(timezone.utc).isoformat(),
    "passed": True, "images": images,
    "host_tests": ["C verifier authentication, epoch and file-type rejection cases", "monitor path/network/policy/environment input tests", "dependency lifecycle tests", "delayed/idle/malformed control packets, glibc and musl"],
    "qemu_results": results,
    "scope": "TCG/OVMF emulation; signed boot witness, not the container host OS",
}
(root / "boot-validation.json").write_text(json.dumps(report, indent=2) + "\n")
lines = [
    "# Boot and recovery validation", "", f"Generated: {report['generated_utc']}", "",
    "All listed results match the SHA-256 hashes of these exported artifacts.", "",
    "| Image | Bytes | SHA-256 |", "|---|---:|---|",
]
for name, info in images.items():
    lines.append(f"| `{name}` | {info['bytes']:,} | `{info['sha256']}` |")
if reproducibility:
    lines += ["", "Reproducibility: PASS (`reproducibility.json`).", reproducibility["scope"] + "."]
if (root / "source-snapshot.json").exists():
    snapshot = json.loads((root / "source-snapshot.json").read_text())
    assert digest("build-source.tar.gz") == snapshot["sha256"], "Stale source snapshot archive"
    lines += ["", "Exact build source: `build-source.tar.gz`; archive hash and builder identity: `source-snapshot.json`."]
lines += ["", "| QEMU scenario | Result |", "|---|---|"]
for result in results:
    lines.append(f"| {result['case']} | PASS |")
lines += [
    "", "The C verifier rejection cases and monitor input tests also passed.", "",
    "Serial transcripts: `qemu-*.log`. Individual evidence: `result-*.json`.", "",
    "Scope: QEMU TCG with OVMF Secure Boot. The system payload is a boot witness.",
    "This does not establish physical hardware compatibility, KVM performance,",
    "power-loss resilience, production key management, or a working Docker/Kata OS.", "",
]
(root / "validation.md").write_text("\n".join(lines))
print(f"VALIDATION_OK: {len(results)} QEMU scenarios, current image hashes")
