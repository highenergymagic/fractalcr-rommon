"""Bounded shutdown for a QEMU process owned by a pexpect test."""
import signal
import time


def stop_qemu(child, timeout=5.0):
    """Wait for termination before closing the PTY and releasing disk fixtures.

    pexpect's close(force=True) only waits briefly after each signal. A QEMU
    process flushing disk state can outlive those waits even after SIGKILL.
    Never proceed to modify/reuse its images until the child has been reaped.
    """
    if child is None or child.closed:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if not child.isalive():
            break
        child.kill(sig)
        deadline = time.monotonic() + timeout
        while child.isalive() and time.monotonic() < deadline:
            time.sleep(0.05)
    if child.isalive():
        raise RuntimeError(f"QEMU child {child.pid} did not exit after SIGKILL")
    child.close()


def finish_qemu(child, result, path, transcript=None):
    """Always write the result, including a failed cleanup, before propagating."""
    import json

    try:
        stop_qemu(child)
    except Exception as error:
        result["passed"] = False
        result["cleanup_error"] = str(error)
        raise
    finally:
        try:
            if transcript is not None:
                transcript.close()
        finally:
            path.write_text(json.dumps(result, indent=2) + "\n")
