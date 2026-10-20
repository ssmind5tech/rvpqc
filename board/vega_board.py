"""Run bare-metal programs on C-DAC VEGA boards (ARIES v2/v3/ECO, THEJAS32 / VEGA ET1031) over USB-serial.

Flow:
  1. The board's boot ROM (UART boot mode) receives the resident loader 'vegamon' by XMODEM.
     This needs one press of RESET.  The XMODEM sender reproduces vegadude, the open-source
     uploader used by Zephyr for these boards (128-byte blocks, CRC-16, 115200 8N1).
  2. vegamon probes the core (counters, trap behaviour) and then loads and starts any number of
     programs over the same serial port, each checked with CRC-32.  Programs return to vegamon.

The same code drives a QEMU 'virt' stand-in ('qemu-sim') with the board's 250 KB memory limit,
so everything can be tested without hardware.

Needs: pyserial (pip install pyserial) and a RISC-V GCC (riscv64-unknown-elf-gcc or similar).
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
MON_DIR = HERE / "monitor"
MON_SIZE = 0x2000          # vegamon owns the first 8 KB of SRAM (code, data, its own stack)


@dataclass(frozen=True)
class Board:
    name: str
    mon_base: int           # where the boot ROM (or QEMU) puts vegamon
    app_top: int            # end of usable SRAM (the ARIES boot ROM keeps the top 6 KB)
    uart_base: int
    uart_stride: int        # bytes between 16550 registers
    clock_hz: int
    mtime_addr: int = 0     # memory-mapped 64-bit machine timer (0 = not used)
    mtime_div: int = 0      # core clock cycles per mtime tick

    @property
    def app_base(self) -> int:
        return self.mon_base + MON_SIZE


BOARDS = {
    # THEJAS32: 256 KB SRAM at 0x200000, boot ROM reserves 0x23E800-0x23FFFF; UART0 = ns16550, reg-shift 2.
    # Source: Zephyr dts/riscv/cdac/thejas32.dtsi (verified on silicon by its author).
    # mtime at 0x20010580 ticks at 100 MHz / 4000 (measured against mcycle by the Zephyr port's author).
    "aries": Board("aries", 0x0020_0000, 0x0023_E800, 0x1000_0100, 4, 100_000_000, 0x2001_0580, 4000),
    # QEMU 'virt' with the same 250 KB budget, for testing without hardware.
    # (QEMU runs with -icount shift=0: 1 instruction = 1 ns, and the CLINT mtime ticks every 100 ns.)
    "qemu-sim": Board("qemu-sim", 0x8000_0000, 0x8000_0000 + 0x3E800, 0x1000_0000, 1, 0, 0x0200_BFF8, 100),
}

CAP_NAMES = ["mcycle", "mcycleh", "minstret", "minstreth", "cycle", "cycleh", "instret", "instreth", "time"]
CAP_CUSTOM0, CAP_MTVAL, CAP_NOTRAP = 1 << 12, 1 << 13, 1 << 14
CAP_ECALL, CAP_EBREAK, CAP_ZEROILL = 1 << 25, 1 << 26, 1 << 27
CAP_MCYCLEH_OK, CAP_MINSTRETH_OK, CAP_MTIME_OK = 1 << 28, 1 << 29, 1 << 30


@dataclass
class Caps:
    raw: int
    loop_cycles: int
    loop_insns: int

    def present(self, name: str) -> bool:
        return bool(self.raw & (1 << CAP_NAMES.index(name)))

    def counts(self, name: str) -> bool:
        return bool(self.raw & (1 << (16 + CAP_NAMES.index(name))))

    @property
    def custom0_traps(self) -> bool:
        return bool(self.raw & CAP_CUSTOM0)

    @property
    def mtval_holds_insn(self) -> bool:
        return bool(self.raw & CAP_MTVAL)

    @property
    def ecall_traps(self) -> bool:
        return bool(self.raw & CAP_ECALL)

    @property
    def mtime_ok(self) -> bool:
        return bool(self.raw & CAP_MTIME_OK)

    def counter(self, kind: str) -> tuple[int, int | None] | None:
        """CSR numbers (low, high) of a working cycle or instret counter, or None.
        high is None when the core has only the low 32 bits (then long operations can wrap)."""
        # high halves are used only when verified (the low half was made to wrap and the high half
        # stepped); a CSR that merely reads 0 without trapping may not exist
        pairs = {"cycle": [("mcycle", 0xB00, 0xB80, CAP_MCYCLEH_OK), ("cycle", 0xC00, 0xC80, 0)],
                 "instret": [("minstret", 0xB02, 0xB82, CAP_MINSTRETH_OK), ("instret", 0xC02, 0xC82, 0)]}[kind]
        working = [(n, lo, hi, ok) for n, lo, hi, ok in pairs if self.counts(n)]
        for name, lo, hi, ok in working:
            if ok and self.raw & ok:
                return lo, hi
        return (working[0][1], None) if working else None

    def describe(self) -> str:
        if self.raw & CAP_NOTRAP:
            return ("mtvec did not accept the loader's trap handler, so traps cannot be caught: "
                    "counters were not probed and custom instructions cannot be emulated")
        parts = []
        for n in CAP_NAMES:
            parts.append(f"{n}={'counts' if self.counts(n) else 'present' if self.present(n) else 'absent'}")
        parts.append(f"custom-0 traps={'yes' if self.custom0_traps else 'NO (ignored)'}")
        if self.custom0_traps:
            parts.append("mtval=insn word" if self.mtval_holds_insn else "mtval=0/other")
        parts.append(f"ecall traps={'yes' if self.raw & CAP_ECALL else 'no'}")
        parts.append(f"ebreak traps={'yes' if self.raw & CAP_EBREAK else 'no'}")
        parts.append(f"illegal all-zero word traps={'yes' if self.raw & CAP_ZEROILL else 'no'}")
        parts.append(f"mcycleh {'verified' if self.raw & CAP_MCYCLEH_OK else 'NOT usable'}")
        parts.append(f"minstreth {'verified' if self.raw & CAP_MINSTRETH_OK else 'NOT usable'}")
        parts.append(f"mtime {'verified' if self.raw & CAP_MTIME_OK else 'not verified'}")
        if self.loop_cycles:
            cpi = f", CPI {self.loop_cycles / self.loop_insns:.2f}" if self.loop_insns else ""
            parts.append(f"2001-instruction loop = {self.loop_cycles} cycles{cpi}")
        return ", ".join(parts)


# ------------------------------------------------------------------ toolchain

CROSS_CANDIDATES = ["riscv64-unknown-elf-", "riscv32-unknown-elf-", "riscv-none-elf-", "riscv64-vega-elf-",
                    "riscv64-linux-gnu-"]


def find_cross(user: str | None = None) -> str:
    for p in ([user] if user else CROSS_CANDIDATES):
        if shutil.which(p + "gcc"):
            return p
    sys.exit("No RISC-V cross compiler found. Install one (e.g. 'sudo apt install gcc-riscv64-unknown-elf') "
             "or pass --cross PREFIX")


def gcc(cross: str, args: list[str]) -> None:
    """Compile and link for RV32IM, trying the flag spellings that old and new toolchains accept."""
    errors = []
    for extra in (["-march=rv32im_zicsr_zifencei", "-Wl,--no-warn-rwx-segments"], ["-march=rv32im_zicsr_zifencei"],
                  ["-march=rv32im"]):
        r = subprocess.run([cross + "gcc", *extra, "-mabi=ilp32", *args], capture_output=True, text=True)
        if r.returncode == 0:
            return
        errors.append(f"$ {cross}gcc {' '.join(extra)} ...\n{r.stderr.strip()[-1500:]}")
    raise RuntimeError("build failed with every flag set:\n\n" + "\n\n".join(errors))


def objcopy_bin(cross: str, elf: Path) -> Path:
    out = elf.with_suffix(".bin")
    r = subprocess.run([cross + "objcopy", "-O", "binary", str(elf), str(out)], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"objcopy failed: {r.stderr}")
    return out


def app_linker_script(board: Board, min_stack: int = 4096) -> str:
    """Programs run from app_base; the stack fills whatever SRAM the image leaves free."""
    return f"""/* generated for board '{board.name}' */
OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY {{ RAM (rwx) : ORIGIN = 0x{board.app_base:x}, LENGTH = 0x{board.app_top - board.app_base:x} }}
SECTIONS {{
  .text : {{ *(.text.init) *(.text .text.*) }} > RAM
  .rodata : {{ *(.rodata .rodata.* .srodata .srodata.*) }} > RAM
  .data : {{ *(.data .data.* .sdata .sdata.*) }} > RAM
  .bss : {{ __bss_start = .; *(.bss .bss.* .sbss .sbss.* COMMON) . = ALIGN(4); __bss_end = .; }} > RAM
  . = ALIGN(16);
  __stack_limit = .;
  __stack_top = ORIGIN(RAM) + LENGTH(RAM);
}}
ASSERT(__stack_top - __stack_limit >= {min_stack}, "program too large for this board: less than {min_stack} bytes left for the stack")
"""


def board_defines(board: Board) -> list[str]:
    return [f"-DUART_BASE=0x{board.uart_base:x}u", f"-DUART_STRIDE={board.uart_stride}", "-DBOARD_MON=1",
            f"-DAPP_BASE=0x{board.app_base:x}u", f"-DAPP_TOP=0x{board.app_top:x}u"]


def build_monitor(board: Board, cross: str, workdir: Path) -> tuple[Path, Path]:
    workdir.mkdir(parents=True, exist_ok=True)
    ld = workdir / "vegamon.ld"
    ld.write_text(f"""OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY {{ MON (rwx) : ORIGIN = 0x{board.mon_base:x}, LENGTH = 0x{MON_SIZE:x} }}
SECTIONS {{
  .text : {{ *(.text.init) *(.text .text.*) }} > MON
  .rodata : {{ *(.rodata .rodata.* .srodata .srodata.*) }} > MON
  .data : {{ *(.data .data.* .sdata .sdata.*) }} > MON
  .bss : {{ __bss_start = .; *(.bss .bss.* .sbss .sbss.* COMMON) . = ALIGN(4); __bss_end = .; }} > MON
  __mon_end = .;
  __mon_stack_top = ORIGIN(MON) + LENGTH(MON);
}}
ASSERT(__mon_end + 2048 <= __mon_stack_top, "vegamon too large")
""")
    elf = workdir / "vegamon.elf"
    mt = ([f"-DMON_MTIME_ADDR=0x{board.mtime_addr:x}u", f"-DMON_MTIME_DIV={board.mtime_div}u"]
          if board.mtime_addr else [])
    gcc(cross, ["-Os", "-nostdlib", "-ffreestanding", "-fno-builtin", *board_defines(board), *mt, "-T", str(ld),
                str(MON_DIR / "mon_start.S"), str(MON_DIR / "mon.c"), "-o", str(elf)])
    return elf, objcopy_bin(cross, elf)


def build_hello(board: Board, cross: str, workdir: Path) -> Path:
    """The pure-assembly bring-up test (monitor/hello.S), linked where the boot ROM loads programs."""
    workdir.mkdir(parents=True, exist_ok=True)
    ld = workdir / "hello.ld"
    ld.write_text(f"""OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY {{ RAM (rwx) : ORIGIN = 0x{board.mon_base:x}, LENGTH = 0x{MON_SIZE:x} }}
SECTIONS {{
  .text : {{ *(.text.init) *(.text .text.*) }} > RAM
  .rodata : {{ *(.rodata .rodata.* .srodata .srodata.*) }} > RAM
}}
""")
    elf = workdir / "hello.elf"
    gcc(cross, ["-nostdlib", f"-DUART_BASE=0x{board.uart_base:x}", f"-DUART_STRIDE={board.uart_stride}",
                f"-DMON_BASE=0x{board.mon_base:x}", f"-DAPP_TOP=0x{board.app_top:x}", "-T", str(ld),
                str(MON_DIR / "hello.S"), "-o", str(elf)])
    return objcopy_bin(cross, elf)


def dump(ser, seconds: float, log=print) -> bytes:
    """Print everything the board sends for a while: text lines as they come, then all bytes in hex."""
    end, got, line = time.monotonic() + seconds, bytearray(), bytearray()
    while time.monotonic() < end:
        chunk = ser.read(256)
        got += chunk
        for ch in chunk:
            if ch == 10:
                log("  board: " + line.decode("ascii", "replace").rstrip("\r"))
                line.clear()
            else:
                line.append(ch)
    if line:
        log("  board: " + line.decode("ascii", "replace"))
    log(f"received {len(got)} bytes; first 200 in hex: {bytes(got[:200]).hex(' ')}")
    return bytes(got)


# ------------------------------------------------------------------ XMODEM (boot ROM)

SOH, EOT, ACK, NAK, CAN, SUB = 0x01, 0x04, 0x06, 0x15, 0x18, 0x1A


def xmodem_send(ser, data: bytes, log=print, first_block: int | None = None, start_byte: int | None = 0x0D,
                wait_s: float = 120.0, max_retry: int = 10, verbose: bool = False) -> None:
    """Send `data` to a waiting VEGA boot ROM.

    Follows vegadude 1.0 (github.com/rnayabed/vegadude), which is known to work with these ROMs:
    128-byte blocks padded with 0x1A, CRC-16-CCITT, then EOT.  The ARIES ECO ROM then asks
    "Please send file using XMODEM and then press ENTER key", so ENTER (0x0D) is sent to start the
    program (vegadude sends 0x1D, which some ROM versions also accept).  Standard XMODEM numbers the first
    block 1, which the ARIES ECO ROM needs: vegadude's 0 is acknowledged but the image then lands
    128 bytes too low (found on a board).  first_block=None tries 1, then 0 if the ROM never answers.
    Anything the board prints while waiting is shown, and a 'C' inside text (e.g. "CDAC") is not
    mistaken for the ROM's transfer request.
    """
    blocks = [data[i:i + 128].ljust(128, bytes([SUB])) for i in range(0, len(data), 128)] or [bytes([SUB]) * 128]
    try:
        ser.reset_input_buffer()
    except Exception:  # noqa: BLE001
        pass
    log("waiting for the boot ROM (press RESET on the board, BOOT SEL in UART mode) ...")
    deadline = time.monotonic() + wait_s
    seen = bytearray()
    text_line = bytearray()

    def show(b: bytes) -> None:          # echo the ROM's text, line by line
        for ch in b:
            if ch in (10, 13):
                if text_line.strip():
                    log("  board: " + text_line.decode("ascii", "replace").strip())
                text_line.clear()
            elif 32 <= ch < 127:
                text_line.append(ch)

    run_c = 0
    while True:                          # a transfer request is two or more 'C' in a row ("CDAC" is not)
        if time.monotonic() > deadline:
            text = seen.decode("ascii", "replace").strip()
            raise TimeoutError("the boot ROM never asked for a transfer ('C')." +
                               (f" Board said: {text[-300:]!r}" if text else " Nothing was received."))
        b = ser.read(1)
        if not b:
            continue
        seen += b
        if b == b"C":
            run_c += 1
            if run_c >= 2:
                if text_line.strip(b"C ").strip():
                    show(b"\n")          # flush the last text line the ROM printed
                break
        elif b not in (b"\r", b"\n", b" ", b"\0"):
            run_c = 0
        show(b)
    log(f"boot ROM ready; sending {len(data)} bytes in {len(blocks)} blocks")
    # the ARIES ECO boot ROM (bootloader 1.0.0, 2020) ACKs a block numbered 0 but stores the image
    # 128 bytes too low, so standard numbering (1) must come first
    numberings = [first_block] if first_block is not None else [1, 0]
    num0 = numberings.pop(0)
    i, tries, silent_tries = 0, 0, 0
    replies = bytearray()

    last_send = 0.0

    def send(k: int) -> None:
        nonlocal last_send
        last_send = time.monotonic()
        payload = blocks[k]
        crc = crc16_ccitt(payload)
        num = (num0 + k) & 0xFF
        ser.write(bytes([SOH, num, 0xFF - num]) + payload + bytes([crc >> 8, crc & 0xFF]))
        try:
            ser.flush()
        except Exception:  # noqa: BLE001
            pass

    send(0)
    silent_since = time.monotonic()
    while i < len(blocks):
        b = ser.read(1)
        if not b:
            if time.monotonic() - silent_since < 3.0:   # be patient: re-sending early creates duplicates
                continue
            silent_tries += 1
            if i == 0 and silent_tries >= 2 and numberings:
                num0 = numberings.pop(0)
                log(f"no answer to the first block; retrying with blocks numbered from {num0}")
                silent_tries = 0
            elif silent_tries > max_retry:
                raise TimeoutError(f"no answer from the boot ROM at block {i}. Bytes received after sending: "
                                   f"{bytes(replies[-64:])!r}")
            send(i)
            silent_since = time.monotonic()
            continue
        silent_since = time.monotonic()
        replies += b
        c = b[0]
        if verbose:
            log(f"  rx {c:#04x} at block {i}")
        if c == ACK:
            if i == 0 and first_block is None:
                log(f"boot ROM accepted the first block (numbered {num0})")
            i, tries, silent_tries = i + 1, 0, 0
            if i < len(blocks):
                send(i)
        elif c == NAK:
            tries += 1
            if i == 0 and tries >= 3 and numberings:
                num0 = numberings.pop(0)
                log(f"first block rejected; retrying with blocks numbered from {num0}")
                tries = 0
            elif tries > max_retry:
                raise RuntimeError(f"boot ROM rejected block {i} {max_retry} times")
            send(i)
        elif c == ord("C") and i == 0:   # receiver still polling: it did not take the first block
            if time.monotonic() - last_send < 1.0:
                continue
            silent_tries += 1
            if silent_tries >= 2 and numberings:
                num0 = numberings.pop(0)
                log(f"boot ROM ignored the first block; retrying with blocks numbered from {num0}")
                silent_tries = 0
            elif silent_tries > max_retry:
                raise TimeoutError("the boot ROM keeps asking for data but never accepts the first block")
            send(0)
        elif c == CAN:
            raise RuntimeError("boot ROM cancelled the transfer")
    ser.write(bytes([EOT]))
    if start_byte is not None:
        time.sleep(0.3)                  # let the ROM acknowledge EOT before the ENTER key
        ser.write(bytes([start_byte]))
    log("upload complete")


def crc16_ccitt(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


# ------------------------------------------------------------------ vegamon client

class Monitor:
    """Talks to vegamon (and to the program it starts) over one serial link."""

    def __init__(self, ser, board: Board, verbose: bool = False, cleanup=None):
        self.ser = ser
        self.board = board
        self.verbose = verbose
        self.caps: Caps | None = None
        self._buf = bytearray()
        self._cleanup = cleanup

    # -- line I/O
    def readline(self, timeout: float | None = 30.0) -> str:
        deadline = None if timeout is None else time.monotonic() + timeout
        while b"\n" not in self._buf:
            chunk = self.ser.read(max(1, getattr(self.ser, "in_waiting", 0) or 1))
            if chunk:
                self._buf += chunk
            elif deadline is not None and time.monotonic() > deadline:
                raise TimeoutError(f"no reply from the board within {timeout:.0f}s (partial: {bytes(self._buf)[-80:]!r})")
        line, _, rest = self._buf.partition(b"\n")
        self._buf = bytearray(rest)
        text = line.decode("ascii", "replace").strip()
        if self.verbose:
            print("  <", text, file=sys.stderr)
        return text

    def send(self, s: str) -> None:
        if self.verbose:
            print("  >", s, file=sys.stderr)
        self.ser.write((s + "\n").encode())

    def expect(self, prefix: str, timeout: float = 10.0) -> str:
        """Read lines until one starts with prefix (boot ROM chatter and blank lines are skipped)."""
        deadline = time.monotonic() + timeout
        while True:
            line = self.readline(max(0.1, deadline - time.monotonic()))
            if line.startswith(prefix):
                return line
            if line.startswith(("E ", "ERANGE", "BAD")):
                raise RuntimeError(f"board reported: {line}")

    # -- monitor commands
    def hello(self, timeout: float = 1.5) -> bool:
        try:
            self.ser.reset_input_buffer()
        except Exception:  # noqa: BLE001  (socket:// links)
            pass
        self._buf.clear()
        self.send("")
        self.send("P")
        try:
            self._parse_banner(self.expect("VEGAMON", timeout))
            return True
        except (TimeoutError, RuntimeError):
            return False

    def _parse_banner(self, line: str) -> None:
        f = dict(p.split("=", 1) for p in line.split()[2:] if "=" in p)
        self.caps = Caps(int(f["caps"], 16), int(f["cyc"], 16), int(f["ins"], 16))
        base, top = int(f["base"], 16), int(f["top"], 16)
        if (base, top) != (self.board.app_base, self.board.app_top):
            raise RuntimeError(f"vegamon on the board was built for another memory map "
                               f"({base:#x}-{top:#x}); re-upload it (reset the board)")

    def wait_banner(self, timeout: float = 10.0) -> None:
        self._parse_banner(self.expect("VEGAMON", timeout))

    def load(self, addr: int, data: bytes) -> None:
        crc = zlib.crc32(data) & 0xFFFFFFFF
        self.send(f"L {addr:08x} {len(data):08x} {crc:08x}")
        self.expect("K")
        for i in range(0, len(data), 4096):
            self.ser.write(data[i:i + 4096])
        line = self.expect("OK", timeout=30 + len(data) / 5000)
        if int(line.split()[1], 16) != crc:
            raise RuntimeError(f"CRC mismatch after loading ({line})")

    def run(self, bin_path: Path) -> None:
        """Load a program built with app_linker_script() and start it."""
        data = Path(bin_path).read_bytes()
        if len(data) > self.board.app_top - self.board.app_base:
            raise RuntimeError(f"{bin_path} ({len(data)} B) does not fit in the board's SRAM")
        self.load(self.board.app_base, data)
        self.send(f"G {self.board.app_base:08x}")
        self.expect("GO")

    def close(self) -> None:
        try:
            self.ser.close()
        finally:
            if self._cleanup:
                self._cleanup()


def connect(board_name: str, cross: str, workdir: Path, port: str | None = None, qemu: str = "qemu-system-riscv32",
            verbose: bool = False, reupload: bool = False, log=print) -> Monitor:
    """Return a Monitor ready to run programs.  For 'aries', uploads vegamon if it is not already running."""
    try:
        import serial  # noqa: F401
    except ImportError:
        sys.exit("This needs pyserial:  pip install pyserial")
    import serial
    board = BOARDS[board_name]
    mon_elf, mon_bin = build_monitor(board, cross, workdir / "vegamon")
    if board_name == "qemu-sim":
        mon = _start_qemu_sim(board, mon_elf, qemu, verbose)
        log("core (QEMU stand-in): " + mon.caps.describe())
        return mon
    if not port:
        sys.exit("pass --port (for example /dev/ttyUSB0 on Linux, COM5 on Windows)")
    ser = serial.Serial(port, 115200, bytesize=8, parity="N", stopbits=1, timeout=0.5,
                        rtscts=False, dsrdtr=False)
    mon = Monitor(ser, board, verbose)
    rom_waiting = False
    if not reupload:                  # a waiting boot ROM polls with 'C': then do not send anything
        t_end, got = time.monotonic() + 1.5, bytearray()
        while time.monotonic() < t_end and b"CC" not in got:
            got += ser.read(64)
        rom_waiting = b"CC" in got
    if not reupload and not rom_waiting and mon.hello():
        log(f"vegamon already running on {port}")
    else:
        xmodem_send(ser, mon_bin.read_bytes(), log=log, verbose=verbose)
        progress = []
        deadline = time.monotonic() + 10
        while True:                   # vegamon reports each start-up step; the ROM banner means a restart
            line = mon.readline(max(0.1, deadline - time.monotonic()))
            if verbose and line and not line.isprintable():
                log(f"  (raw line: {line!r})")
            if line.startswith("vegamon:"):
                progress.append(line)     # shown with -v, and in the error if the board restarts
            elif line.startswith("VEGAMON"):
                mon._parse_banner(line)
                break
            elif "Bootloader" in line or "Please send file" in line:
                raise RuntimeError("the board restarted while vegamon was starting (it crashed or reset). "
                                   "Last steps it reported: " + (" | ".join(progress[-4:]) or "none"))
        log("vegamon started")
    log("core: " + mon.caps.describe())
    return mon


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_qemu_sim(board: Board, mon_elf: Path, qemu: str, verbose: bool) -> Monitor:
    import serial
    if not shutil.which(qemu):
        sys.exit(f"'{qemu}' not found (install qemu-system-misc)")
    port = _free_port()
    proc = subprocess.Popen([qemu, "-M", "virt", "-bios", "none", "-display", "none", "-monitor", "none",
                             "-icount", "shift=0",
                             "-chardev", f"socket,id=c0,host=127.0.0.1,port={port},server=on,wait=on,nodelay=on",
                             "-serial", "chardev:c0", "-kernel", str(mon_elf)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    deadline = time.monotonic() + 10
    while True:
        try:
            ser = serial.serial_for_url(f"socket://127.0.0.1:{port}", timeout=0.5)
            ser._socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)   # small request/reply lines
            break
        except Exception:  # noqa: BLE001
            if time.monotonic() > deadline or proc.poll() is not None:
                proc.kill()
                raise RuntimeError("QEMU did not start: " + (proc.stderr.read() or b"").decode()[:300])
            time.sleep(0.05)

    def cleanup() -> None:
        proc.kill()
        proc.wait()

    mon = Monitor(ser, board, verbose, cleanup)
    mon.wait_banner(timeout=10)
    return mon


if __name__ == "__main__":   # quick check: python vega_board.py --board aries --port /dev/ttyUSB0
    import argparse
    import tempfile
    ap = argparse.ArgumentParser(description="Upload vegamon and report what the core supports")
    ap.add_argument("--board", default="aries", choices=sorted(BOARDS))
    ap.add_argument("--port")
    ap.add_argument("--cross")
    ap.add_argument("--reupload", action="store_true")
    ap.add_argument("--save-loader", metavar="DIR",
                    help="only build vegamon.bin into DIR (to upload it with C-DAC's VEGA XMODEM tool or vegadude)")
    ap.add_argument("--listen", type=float, metavar="SECONDS",
                    help="only print what the board sends for SECONDS (press RESET meanwhile), byte by byte")
    ap.add_argument("--hello", action="store_true",
                    help="upload a tiny assembly bring-up test instead of vegamon and show what it prints")
    ap.add_argument("--first-block", type=int, choices=[0, 1],
                    help="XMODEM number of the first block (default: try 0, then 1)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    if a.listen:
        import serial
        if not a.port:
            sys.exit("pass --port")
        s = serial.Serial(a.port, 115200, timeout=0.2)
        print(f"listening on {a.port} at 115200 8N1 for {a.listen:.0f}s - press RESET now")
        end, got = time.monotonic() + a.listen, bytearray()
        while time.monotonic() < end:
            got += s.read(256)
        s.close()
        print(f"received {len(got)} bytes")
        print("as text :", got.decode("ascii", "replace")[:2000])
        print("as bytes:", got[:400].hex(" "))
        raise SystemExit(0)
    if a.first_block is not None:
        _orig = xmodem_send
        xmodem_send = lambda *x, **k: _orig(*x, **{**k, "first_block": a.first_block})  # noqa: E731
    if a.hello:
        import serial
        if not a.port:
            sys.exit("pass --port")
        with tempfile.TemporaryDirectory() as t:
            hb = build_hello(BOARDS[a.board], find_cross(a.cross), Path(t))
            s = serial.Serial(a.port, 115200, timeout=0.2)
            xmodem_send(s, hb.read_bytes(), verbose=a.verbose)
            print("output of the bring-up test (H1 ... H9):")
            dump(s, 8)
            s.close()
        raise SystemExit(0)
    if a.save_loader:
        _, b = build_monitor(BOARDS[a.board], find_cross(a.cross), Path(a.save_loader))
        print(f"wrote {b} ({b.stat().st_size} bytes); load address 0x{BOARDS[a.board].mon_base:x}")
        raise SystemExit(0)
    with tempfile.TemporaryDirectory() as t:
        m = connect(a.board, find_cross(a.cross), Path(t), a.port, verbose=a.verbose, reupload=a.reupload)
        m.close()
