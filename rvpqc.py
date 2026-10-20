#!/usr/bin/env python3
"""rvpqc: post-quantum cryptography performance lab for RISC-V.

Benchmarks the three finalized NIST standards on bare-metal RV32 cores, under QEMU or on a real
board (C-DAC ARIES, THEJAS32 / VEGA ET1031, with --board aries --port PORT):
  ML-KEM (FIPS 203) and ML-DSA (FIPS 204) from PQClean,
  SLH-DSA (FIPS 205) from the PQ Code Package's slhdsa-c.

For each algorithm and build configuration it:
  1. builds a bare-metal RV32 benchmark and runs it on QEMU with exact instruction counting
     (-icount shift=0, read through the 64-bit instret counter, overhead calibrated);
  2. measures instructions per operation, peak stack use and code size;
  3. builds the same benchmark natively and checks that both produce byte-identical keys,
     ciphertexts, shared secrets and signatures (differential check);
  4. checks functional correctness (shared secrets match; signatures verify; tampered
     signatures are rejected).
Every results file records the toolchain, so numbers stay comparable.

On a board, step 1 measures real cycles (and instructions, if the core counts them), stack and
heap inside the board's 250 KB of SRAM; steps 3 and 4 are the same.  The programs are loaded by a
small resident loader (board/), uploaded once through the board's boot ROM.

Usage:
  python rvpqc.py --fetch                                   # ML-KEM, ML-DSA, SLH-DSA-*-128f
  python rvpqc.py --schemes ml-kem-768 --iter 10
  python rvpqc.py --march rv32im rv32imc --opt O2 Os        # compare build configurations
  python rvpqc.py --schemes slh-dsa-sha2-128s               # the slow, small-signature variant
  python rvpqc.py --self-test                               # prove the differential check works
  python rvpqc.py --board aries --port /dev/ttyUSB0         # on an ARIES board (one RESET needed)
  python rvpqc.py --board qemu-sim                          # the board flow, in QEMU, 250 KB limit
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
FW = HERE / "firmware"
sys.path.insert(0, str(HERE / "board"))
SLH_SIG_BYTES = {"128s": 7856, "128f": 17088, "192s": 16224, "192f": 35664, "256s": 29792, "256f": 49856}
DEFAULT_SCHEMES = ["ml-kem-512", "ml-kem-768", "ml-kem-1024", "ml-dsa-44", "ml-dsa-65", "ml-dsa-87",
                   "slh-dsa-sha2-128f", "slh-dsa-shake-128f"]
SLH_PARAMS = [f"{h}-{lvl}{v}" for lvl in (128, 192, 256) for v in "sf" for h in ("sha2", "shake")]
CROSS_CANDIDATES = ["riscv64-unknown-elf-", "riscv32-unknown-elf-", "riscv-none-elf-", "riscv64-linux-gnu-"]
PQCLEAN_URL = "https://github.com/PQClean/PQClean.git"
SLHDSA_URL = "https://github.com/pq-code-package/slhdsa-c.git"
CFLAGS: list[str] = []  # extra compiler flags for the RISC-V builds (--cflags)
SLHDSA_SOURCES = ["sha2_256.c", "sha2_512.c", "sha3_api.c", "sha3_f1600.c", "slh_dsa.c", "slh_sha2.c", "slh_shake.c"]


def die(msg: str) -> None:
    sys.exit(f"rvpqc: {msg}")


def run(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def first_line(cmd: list[str]) -> str:
    try:
        r = run(cmd)
        return (r.stdout or r.stderr).strip().splitlines()[0]
    except (OSError, IndexError):
        return "unknown"


def git_commit(path: Path) -> str:
    return run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"]).stdout.strip() or "unknown"


def find_cross(user: str | None) -> str:
    for p in ([user] if user else CROSS_CANDIDATES):
        if shutil.which(p + "gcc"):
            return p
    die("no RISC-V cross compiler found (install gcc-riscv64-unknown-elf, or pass --cross PREFIX)")


def ensure_checkout(path: Path, marker: str, url: str, fetch: bool, name: str) -> None:
    if (path / marker).exists():
        return
    if not fetch:
        die(f"{name} not found at {path}; clone it (git clone --depth 1 {url} {path}) or pass --fetch")
    print(f"cloning {name} into {path} ...")
    if run(["git", "clone", "-q", "--depth", "1", url, str(path)]).returncode:
        die(f"git clone of {name} failed")


# ------------------------------------------------------------------ algorithm descriptions

def scheme_info(scheme: str, pqclean: Path, slhdsa: Path) -> dict:
    if scheme.startswith("slh-dsa-"):
        param = scheme[len("slh-dsa-"):]
        if param not in SLH_PARAMS:
            die(f"unknown SLH-DSA parameter set '{param}' (choose from {', '.join(SLH_PARAMS)})")
        hash_, size = param.split("-")
        return {"scheme": scheme, "kind": "slh", "dir": slhdsa, "param": param.replace("-", "_"),
                "algname": f"SLH-DSA-{hash_.upper()}-{size}", "source": "slhdsa-c", "sig_max": SLH_SIG_BYTES[size]}
    for kind in ("kem", "sign"):
        d = pqclean / f"crypto_{kind}" / scheme / "clean"
        if d.is_dir():
            api = (d / "api.h").read_text()
            m = re.search(r'#define\s+(PQCLEAN_\w+_CLEAN)_CRYPTO_ALGNAME\s+"([^"]+)"', api)
            if not m:
                die(f"cannot parse {d / 'api.h'}")
            return {"scheme": scheme, "kind": kind, "dir": d, "prefix": m.group(1), "algname": m.group(2),
                    "source": "PQClean"}
    die(f"unknown scheme '{scheme}'")


def defines(info: dict, iters: int) -> list[str]:
    d = [f'-DALGNAME="{info["algname"]}"', f"-DITER={iters}"]
    if info["kind"] == "slh":
        return d + [f"-DSLH={info['param']}", f"-DSLH_SIG_MAX={info['sig_max']}"]
    p = info["prefix"]
    d.append(f"-DNS={p}_")
    for name in ("CRYPTO_PUBLICKEYBYTES", "CRYPTO_SECRETKEYBYTES", "CRYPTO_BYTES", "CRYPTO_CIPHERTEXTBYTES"):
        d.append(f"-D{name}={p}_{name}")
    if info["kind"] == "kem":
        d.append("-DKEM")
    return d


def includes(info: dict, pqclean: Path) -> list[str]:
    return ["-I", str(info["dir"]), "-I", str(pqclean / "common")]


def algorithm_sources(info: dict, pqclean: Path) -> list[str]:
    """The algorithm's own code (used for code size)."""
    if info["kind"] == "slh":
        return [str(info["dir"] / f) for f in SLHDSA_SOURCES]
    return sorted(str(f) for f in info["dir"].glob("*.c")) + [str(pqclean / "common" / "fips202.c")]


def build_sources(info: dict, pqclean: Path) -> list[str]:
    """Everything the benchmark links: SLH-DSA also needs PQClean's SHAKE for the test RNG."""
    srcs = algorithm_sources(info, pqclean)
    if info["kind"] == "slh":
        srcs.append(str(pqclean / "common" / "fips202.c"))
    return srcs


# ------------------------------------------------------------------ build and run

def build_riscv(info, pqclean, cross, cfg, iters, work: Path, extra_defs=(), board=None) -> Path:
    """Build the benchmark.  For a board (board.Board), link it for the board and return a raw .bin."""
    elf = work / f"{info['scheme']}-{cfg['march']}-{cfg['opt']}{'-' + board.name if board else ''}.elf"
    ld = FW / "link.ld"
    if board is not None:
        from vega_board import app_linker_script, board_defines
        ld = work / f"app-{board.name}.ld"
        ld.write_text(app_linker_script(board, min_stack=8192))
        extra_defs = [*extra_defs, *board_defines(board)]
    common = [*extra_defs, "-mabi=ilp32", f"-{cfg['opt']}", *CFLAGS, "-ffreestanding", "-nostdlib",
              "-isystem", str(FW / "include"), *includes(info, pqclean), *defines(info, iters),
              "-T", str(ld), str(FW / "start.S"), str(FW / "bench.c"), str(FW / "support.c"),
              *build_sources(info, pqclean), "-lgcc", "-o", str(elf)]
    err = ""
    for arch in (f"{cfg['march']}_zicsr", cfg["march"]):  # newer toolchains need the zicsr name
        for extra in (["-Wl,--no-warn-rwx-segments"], []):
            r = run([cross + "gcc", f"-march={arch}", *extra, *common])
            if r.returncode == 0:
                if board is not None:
                    from vega_board import objcopy_bin
                    return objcopy_bin(cross, elf)
                return elf
            err = r.stderr.strip()[-800:]
    if board is not None and "too large for this board" in err:
        die(f"{info['algname']} does not fit in the {board.name} board's SRAM ({cfg['march']}, -{cfg['opt']}):\n{err}")
    die(f"RISC-V build failed for {info['scheme']} ({cfg['march']}, -{cfg['opt']}):\n{err}")


def code_size(info, pqclean, cross, cfg, work: Path) -> dict:
    objs = []
    for src in algorithm_sources(info, pqclean):
        obj = work / f"{Path(src).stem}-{info['scheme']}-{cfg['march']}-{cfg['opt']}.o"
        r = None
        for arch in (f"{cfg['march']}_zicsr", cfg["march"]):
            r = run([cross + "gcc", f"-march={arch}", "-mabi=ilp32", f"-{cfg['opt']}", *CFLAGS, "-ffreestanding", "-c",
                     "-isystem", str(FW / "include"), *includes(info, pqclean), src, "-o", str(obj)])
            if r.returncode == 0:
                break
        if r.returncode:
            die(f"cannot compile {src} for size measurement:\n{r.stderr[-400:]}")
        objs.append(str(obj))
    total = run([cross + "size", "-t", *objs]).stdout.strip().splitlines()[-1].split()
    return {"text": int(total[0]), "data": int(total[1]), "bss": int(total[2])}


def run_qemu(elf: Path, qemu: str, timeout: int) -> str:
    try:
        r = run([qemu, "-M", "virt", "-bios", "none", "-display", "none", "-monitor", "none",
                 "-serial", "stdio", "-icount", "shift=0", "-kernel", str(elf)], timeout=timeout)
    except subprocess.TimeoutExpired:
        die(f"QEMU timed out after {timeout}s on {elf.name} (raise --timeout)")
    return r.stdout


def run_host(info, pqclean, iters, work: Path, cc: str) -> str:
    exe = work / f"{info['scheme']}-host"
    if not exe.exists():
        r = run([cc, "-O2", "-DHOST", *includes(info, pqclean), *defines(info, iters),
                 str(FW / "bench.c"), *build_sources(info, pqclean), "-o", str(exe)])
        if r.returncode:
            die(f"host build failed for {info['scheme']}:\n{r.stderr[-600:]}")
    return run([str(exe)], timeout=1800).stdout


def run_board(mon, bin_path: Path, timeout: int) -> str:
    """Run one benchmark through the vegamon loader; returns everything it printed."""
    mon.run(bin_path)
    lines = []
    while True:
        line = mon.readline(timeout=timeout)
        if line.startswith("RET "):
            break
        if line.startswith("E "):
            lines.append(f"FAIL unexpected trap on the core: {line}")
            mon.expect("RET", timeout=10)
            break
        lines.append(line)
    return "\n".join(lines) + "\n"


def parse(out: str) -> tuple[dict, list[str], list[str]]:
    """R lines: 'R op count1 stack [count2]'.  Also returns digests and failures; heap peak under '_heap'."""
    results: dict = {}
    digests, failures = [], []
    for line in out.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "R" and len(parts) in (4, 5):
            results.setdefault(parts[1], []).append(
                (int(parts[2]), int(parts[3]), int(parts[4]) if len(parts) == 5 else None))
        elif parts[0] == "M" and len(parts) == 3 and parts[1] == "heap":
            results["_heap"] = int(parts[2])
        elif parts[0] == "H":
            digests.append(line)
        elif parts[0] == "FAIL":
            failures.append(line)
    return results, digests, failures


# ------------------------------------------------------------------ reporting

def fmt(n: float) -> str:
    return f"{n:,.0f}"


def cfg_label(cfg: dict) -> str:
    return f"{cfg['march']} -{cfg['opt']}"


def write_reports(outdir: Path, meta: dict, rows: list[dict], configs: list[dict], hw: bool = False) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "results.json").write_text(json.dumps({"meta": meta, "results": rows}, indent=2))
    with open(outdir / "results.csv", "w", newline="") as fh:
        fields = list(rows[0].keys()) if rows else ["algorithm"]
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    if hw:
        md = ["# rvpqc results (hardware)", "",
              "Cycles per operation measured on the board with the core's 64-bit cycle counter (measurement "
              "overhead calibrated and subtracted); time assumes the board clock below. Instructions and CPI "
              "are shown when the core also counts retired instructions.", ""]
    else:
        md = ["# rvpqc results", "",
              "Instructions retired per operation, measured exactly with QEMU `-icount` (64-bit `instret`, "
              "measurement overhead calibrated and subtracted).", ""]
    md += [
          "## Environment", "",
          "| Item | Value |", "|---|---|"]
    for k, v in meta.items():
        md.append(f"| {k} | {v} |")
    for cfg in configs:
        if hw:
            md += ["", f"## Results: {cfg_label(cfg)}", "",
                   "| Algorithm | Operation | Cycles (median) | Time (ms) | Min cycles | Max cycles | Instructions (median) "
                   "| CPI | Peak stack (B) | Peak heap (B) | Code (B) | Check |",
                   "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
            for r in rows:
                if r["march"] == cfg["march"] and r["opt"] == cfg["opt"]:
                    ins = r.get("instructions_median")
                    md.append(f"| {r['algorithm']} | {r['operation']} | {fmt(r['cycles_median'])} | {r['time_ms_median']:,.2f} | "
                              f"{fmt(r['cycles_min'])} | {fmt(r['cycles_max'])} | {fmt(ins) if ins is not None else '-'} | "
                              f"{r['cpi'] if r.get('cpi') else '-'} | {fmt(r['stack_bytes_max'])} | {fmt(r['heap_peak_bytes'])} | "
                              f"{fmt(r['code_text_bytes'])} | {r['status']} |")
            continue
        md += ["", f"## Results: {cfg_label(cfg)}", "",
               "| Algorithm | Operation | Instructions (median) | Min | Max | Peak stack (B) | Code (B) | Check |",
               "|---|---|---:|---:|---:|---:|---:|---|"]
        for r in rows:
            if r["march"] == cfg["march"] and r["opt"] == cfg["opt"]:
                md.append(f"| {r['algorithm']} | {r['operation']} | {fmt(r['instructions_median'])} | "
                          f"{fmt(r['instructions_min'])} | {fmt(r['instructions_max'])} | {fmt(r['stack_bytes_max'])} | "
                          f"{fmt(r['code_text_bytes'])} | {r['status']} |")
    if len(configs) > 1 and not hw:
        base = configs[0]
        md += ["", f"## Configuration comparison (relative to {cfg_label(base)})", "",
               "| Algorithm | Operation | " + " | ".join(f"{cfg_label(c)} instructions" for c in configs)
               + " | " + " | ".join(f"{cfg_label(c)} code (B)" for c in configs) + " |",
               "|---|---|" + "---:|" * (2 * len(configs))]
        keys = []
        for r in rows:
            k = (r["algorithm"], r["operation"])
            if k not in keys:
                keys.append(k)
        for alg, op in keys:
            by = {(r["march"], r["opt"]): r for r in rows if r["algorithm"] == alg and r["operation"] == op}
            b = by.get((base["march"], base["opt"]))
            cells_i, cells_c = [], []
            for c in configs:
                r = by.get((c["march"], c["opt"]))
                if not r or not b:
                    cells_i.append("-")
                    cells_c.append("-")
                    continue
                di = (r["instructions_median"] / b["instructions_median"] - 1) * 100
                dc = (r["code_text_bytes"] / b["code_text_bytes"] - 1) * 100
                same = c is base
                cells_i.append(fmt(r["instructions_median"]) + ("" if same else f" ({di:+.1f}%)"))
                cells_c.append(fmt(r["code_text_bytes"]) + ("" if same else f" ({dc:+.1f}%)"))
            md.append(f"| {alg} | {op} | " + " | ".join(cells_i) + " | " + " | ".join(cells_c) + " |")
    md += ["", ("Cycle counts are from the board's own counter, so they include pipeline stalls and memory "
                "effects. " if hw else "Instruction counts are not cycle counts: real time depends on the core's "
                "pipeline and memory system. ") + "Code size covers the algorithm and its hash code, excluding the benchmark harness. "
           "For SLH-DSA it covers the whole slhdsa-c library (all 12 parameter sets and both hash families), "
           "so it is the same for every SLH-DSA variant."]
    (outdir / "results.md").write_text("\n".join(md) + "\n")


# ------------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--schemes", nargs="+", default=DEFAULT_SCHEMES,
                    help="ml-kem-*, ml-dsa-*, slh-dsa-{sha2,shake}-{128,192,256}{s,f}")
    ap.add_argument("--iter", type=int, default=5, help="runs per operation for ML-KEM and ML-DSA")
    ap.add_argument("--slh-iter", type=int, default=2,
                    help="runs per operation for SLH-DSA (deterministic cost, and slow)")
    ap.add_argument("--march", nargs="+", default=["rv32im"], help="one or more RISC-V ISA strings")
    ap.add_argument("--opt", nargs="+", default=["O2"], help="one or more GCC optimization levels, e.g. O2 Os O3")
    ap.add_argument("--cflags", default="", help='extra flags for the RISC-V builds, e.g. "-fno-unroll-loops"')
    ap.add_argument("--pqclean", default=str(HERE / "PQClean"))
    ap.add_argument("--slhdsa", default=str(HERE / "slhdsa-c"))
    ap.add_argument("--fetch", action="store_true", help="clone PQClean and slhdsa-c if missing")
    ap.add_argument("--cross", help="RISC-V cross-compiler prefix")
    ap.add_argument("--qemu", default="qemu-system-riscv32")
    ap.add_argument("--host-cc", default="cc")
    ap.add_argument("--no-host-check", action="store_true", help="skip the native differential check")
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--board", default="qemu", choices=["qemu", "qemu-sim", "aries"],
                    help="qemu: QEMU -icount (default); aries: a real ARIES board over --port; "
                         "qemu-sim: the board flow in QEMU with the board's 250 KB limit")
    ap.add_argument("--port", help="serial port of the board, e.g. /dev/ttyUSB0 or COM5")
    ap.add_argument("--reupload", action="store_true", help="upload the board loader again even if one is running")
    ap.add_argument("--heap", type=int, default=24576, help="heap bytes for board builds (hash contexts)")
    ap.add_argument("-v", "--verbose", action="store_true", help="print the serial traffic (board modes)")
    ap.add_argument("--self-test", action="store_true",
                    help="plant a one-bit error in the RISC-V build and confirm the differential check catches it")
    args = ap.parse_args()

    CFLAGS.extend(args.cflags.split())
    pqclean, slhdsa = Path(args.pqclean), Path(args.slhdsa)
    ensure_checkout(pqclean, "common/fips202.c", PQCLEAN_URL, args.fetch, "PQClean")
    if any(s.startswith("slh-dsa-") for s in args.schemes):
        ensure_checkout(slhdsa, "slh_dsa.c", SLHDSA_URL, args.fetch, "slhdsa-c")
    if args.board != "aries" and not shutil.which(args.qemu):
        die(f"'{args.qemu}' not found (install qemu-system-misc, or pass --qemu PATH)")
    cross = find_cross(args.cross)
    hw = args.board != "qemu"
    mon, board, board_defs = None, None, []
    if hw:
        from vega_board import connect
        mon = connect(args.board, cross, Path(tempfile.mkdtemp(prefix="rvpqc-board-")), args.port, args.qemu,
                      args.verbose, args.reupload, log=lambda s: print("board:", s))
        board = mon.board
        cyc, ins = mon.caps.counter("cycle"), mon.caps.counter("instret")
        if os.environ.get("RVPQC_FORCE_32BIT"):   # testing: pretend the core lacks the high halves
            cyc = cyc and (cyc[0], None)
            ins = ins and (ins[0], None)
        first = cyc or ins
        if not first:
            mon.close()
            die("the core has no working 64-bit cycle or instret counter, so nothing can be measured")
        board_defs = [f"-DCNT1_LO={first[0]:#x}", f"-DHEAP_BYTES={args.heap}u"]
        if first[1] is not None:
            board_defs.append(f"-DCNT1_HI={first[1]:#x}")
        if cyc and ins:
            board_defs.append(f"-DCNT2_LO={ins[0]:#x}")
            if ins[1] is not None:
                board_defs.append(f"-DCNT2_HI={ins[1]:#x}")
        counts_cycles = bool(cyc)
        ins32 = bool(cyc and ins and ins[1] is None)
        wrap32 = first[1] is None
        if wrap32 and cyc and board.mtime_addr and mon.caps.mtime_ok:
            # recover whole 2^32 wraps from the board's memory-mapped machine timer (clock / mtime_div)
            board_defs += [f"-DMTIME_ADDR={board.mtime_addr:#x}u", f"-DMTIME_DIV={board.mtime_div}u"]
            print("board: the core has a 32-bit cycle counter; long operations are unwrapped with the machine timer")
        elif wrap32:
            print("board: WARNING no verified 64-bit counter and no verified timer: operations longer than "
                  "2^32 counts (42.9 s at 100 MHz) will wrap")
        if args.slh_iter == 2 and any(s.startswith("slh-dsa-") for s in args.schemes):
            args.slh_iter = 1   # SLH-DSA is deterministic and takes seconds to minutes per signature on a 100 MHz core
    configs = [{"march": m, "opt": o.lstrip("-")} for m, o in itertools.product(args.march, args.opt)]

    if args.self_test:
        with tempfile.TemporaryDirectory(prefix="rvpqc-selftest-") as tmp:
            info = scheme_info("ml-kem-512", pqclean, slhdsa)
            elf = build_riscv(info, pqclean, cross, configs[0], 2, Path(tmp), ["-DRVPQC_SABOTAGE", *board_defs], board)
            _, rv, _ = parse(run_board(mon, elf, args.timeout) if hw else run_qemu(elf, args.qemu, args.timeout))
            _, host, _ = parse(run_host(info, pqclean, 2, Path(tmp), args.host_cc))
            caught = rv != host
            print(f"self-test: planted one-bit error {'CAUGHT by' if caught else 'MISSED by'} the differential check")
            if mon:
                mon.close()
            return 0 if caught else 1

    meta = {
        "date (UTC)": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "RISC-V compiler": first_line([cross + "gcc", "--version"]),
        "QEMU": first_line([args.qemu, "--version"]) if args.board != "aries" else "not used",
        **({"board": ("C-DAC ARIES (THEJAS32, VEGA ET1031)" if args.board == "aries" else
                      "QEMU virt stand-in with the ARIES 250 KB memory map"),
            "board clock": f"{board.clock_hz / 1e6:.0f} MHz" if board.clock_hz else "n/a (QEMU)",
            "core capabilities (probed)": mon.caps.describe(),
            "counter": (("cycles" + (" + instructions" if any(d.startswith("-DCNT2_LO") for d in board_defs) else ""))
                        if counts_cycles else "instructions only (no cycle counter)")
                       + ((" (32-bit, unwrapped with mtime)" if any(d.startswith("-DMTIME") for d in board_defs)
                           else " (32-bit: long operations wrap)") if wrap32 else " (64-bit)"),
            "heap for hash contexts": f"{args.heap} B"} if hw else {}),
        "host compiler (differential check)": first_line([args.host_cc, "--version"]),
        "host": f"{platform.system()} {platform.machine()}",
        "PQClean commit": git_commit(pqclean),
        "slhdsa-c commit": git_commit(slhdsa) if slhdsa.exists() else "not used",
        "configurations": ", ".join(cfg_label(c) for c in configs),
        "extra compiler flags": " ".join(CFLAGS) or "none",
        "runs per operation": f"ML-KEM/ML-DSA {args.iter}, SLH-DSA {args.slh_iter}",
    }
    print("rvpqc: " + ("measurements on " + args.board if hw else "exact instruction counts (QEMU -icount)"))
    for k, v in meta.items():
        print(f"  {k}: {v}")
    print()

    rows, all_ok = [], True
    with tempfile.TemporaryDirectory(prefix="rvpqc-") as tmp:
        work = Path(tmp)
        for cfg in configs:
            print(f"== {cfg_label(cfg)}")
            for scheme in args.schemes:
                info = scheme_info(scheme, pqclean, slhdsa)
                iters = args.slh_iter if info["kind"] == "slh" else args.iter
                t0 = time.perf_counter()
                elf = build_riscv(info, pqclean, cross, cfg, iters, work, board_defs, board)
                size = code_size(info, pqclean, cross, cfg, work)
                out = run_board(mon, elf, args.timeout) if hw else run_qemu(elf, args.qemu, args.timeout)
                results, rv_digests, failures = parse(out)
                heap_peak = results.pop("_heap", None)
                if "END" not in out:
                    failures.append("benchmark did not finish (crash or out of memory?)")
                match = None
                if not args.no_host_check:
                    _, host_digests, host_fail = parse(run_host(info, pqclean, iters, work, args.host_cc))
                    match = bool(rv_digests) and rv_digests == host_digests and not host_fail
                ok = not failures and match is not False
                all_ok &= ok
                status = "PASS" if ok else "FAIL"
                check = ("identical to native build" if match else
                         "native check skipped" if match is None else "DIFFERS from native build")
                print(f"{info['algname']:<20} {status}  ({check}; code {fmt(size['text'])} B; "
                      f"{time.perf_counter() - t0:.0f}s)")
                for f in failures:
                    print(f"    {f}")
                for op, samples in results.items():
                    v1 = [s[0] for s in samples]
                    stk = max(s[1] for s in samples)
                    row = {"algorithm": info["algname"], "operation": op, "march": cfg["march"], "opt": cfg["opt"]}
                    if hw and counts_cycles:
                        row.update({"cycles_median": int(statistics.median(v1)), "cycles_min": min(v1),
                                    "cycles_max": max(v1)})
                        row["time_ms_median"] = (row["cycles_median"] / board.clock_hz * 1000 if board.clock_hz
                                                 else 0.0)
                        v2 = [s[2] for s in samples if s[2] is not None]
                        if ins32 and max(v1) >= 2**32:
                            v2 = []   # a 32-bit instret may have wrapped (it cannot when cycles < 2^32: CPI >= 1)
                        if v2:
                            row["instructions_median"] = int(statistics.median(v2))
                            row["cpi"] = round(row["cycles_median"] / row["instructions_median"], 3)
                    else:
                        row.update({"instructions_median": int(statistics.median(v1)), "instructions_min": min(v1),
                                    "instructions_max": max(v1)})
                        if hw:   # board without a cycle counter: report instructions in the cycle columns' place
                            row.update({"cycles_median": row["instructions_median"], "cycles_min": min(v1),
                                        "cycles_max": max(v1), "time_ms_median": 0.0})
                    row.update({"stack_bytes_max": stk, "heap_peak_bytes": heap_peak, "code_text_bytes": size["text"],
                                "code_data_bytes": size["data"], "runs": len(v1), "status": status,
                                "native_match": match, "source": info["source"]})
                    rows.append(row)
                    spread = "" if min(v1) == max(v1) else f"  (min {fmt(min(v1))}, max {fmt(max(v1))})"
                    unit = "cycles" if hw and counts_cycles else "instructions"
                    extra = ""
                    if hw and counts_cycles and board.clock_hz:
                        extra = f" = {row['time_ms_median']:,.2f} ms"
                    if row.get("cpi"):
                        extra += f", CPI {row['cpi']}"
                    print(f"    {op:<8} {fmt(statistics.median(v1)):>14} {unit}{extra}{spread}   stack {fmt(stk)} B")
                if heap_peak is not None:
                    print(f"    heap peak {fmt(heap_peak)} B")
            print()
    if mon:
        mon.close()
    out_dir = Path(args.out) if args.out != str(HERE / "results") or not hw else HERE / f"results-{args.board}"
    write_reports(out_dir, meta, rows, configs, hw)
    print(f"wrote {out_dir}/results.json, results.csv, results.md")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
