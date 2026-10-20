# Board kit: C-DAC ARIES (THEJAS32 / VEGA ET1031)

This folder lets qrvc and rvpqc run their programs on a real ARIES board (ARIES v2, v3 or ECO)
over the board's USB-serial port. The same files are shipped in both projects.

| File | What it is |
|---|---|
| `monitor/` | **vegamon**, a 2.5 KB resident loader written in C and assembly |
| `vega_board.py` | Host side: XMODEM upload to the boot ROM, the vegamon client, linker scripts, a QEMU stand-in |

## How it works

1. **One upload through the boot ROM.** With the board in UART boot mode, the boot ROM receives
   vegamon by XMODEM after you press RESET. This needs 128-byte blocks with CRC-16 at 115200 8N1.
   The sender reproduces [vegadude](https://github.com/rnayabed/vegadude), the open-source
   uploader that Zephyr uses for these boards.
2. **vegamon probes the core** and reports, in one banner line:
    - which of the counter CSRs exist and count (`mcycle`, `minstret`, their high halves, `cycle`, `instret`, `time`);
    - whether custom-0 instructions trap, and what `mtval` holds;
    - the cycles a 2001-instruction loop takes.
3. **Everything else goes over the same port.** vegamon loads each test program (checked with
   CRC-32) and starts it. The program returns to vegamon when it finishes, so a whole test run
   needs only that one RESET. vegamon stays loaded until reset or power-off.

## Found on a real ARIES ECO (bootloader 1.0.0, 2020)

These findings come from vegamon's probe and from `vega_board.py --hello`, a pure-assembly
bring-up test, run on a real board.

| Finding | Consequence |
|---|---|
| The boot ROM ACKs a first XMODEM block numbered 0, but the image then lands 128 bytes too low | The uploader uses standard numbering from 1 by default. vegadude's 0 is tried only as a fallback |
| After the upload the ROM asks for ENTER | The uploader sends 0x0D |
| `mtvec` accepts our handler | Traps can be caught |
| **Custom-0 instructions are silently ignored (no trap)** | qrvc switches to *ecall mode*: each quantum instruction is preceded by `ecall`, and the firmware reads the word after it. All classical code still runs natively |
| `ecall` traps (cause 11), but `ebreak` and the all-zero word do not: the core seems to raise neither illegal-instruction nor breakpoint exceptions | `ecall` is the only way into a trap handler |
| `mcycle` and `minstret` count; `cycle`, `instret` and `time` read 0 without trapping | rvpqc uses the machine counters, and the loader ignores CSRs that only read 0 |
| `mcycleh` and `minstreth` are real: forcing the low half to wrap made the high half step to 1 | rvpqc reads exact 64-bit counts. If a core fails this test, rvpqc unwraps 32-bit counts with the machine timer instead |
| The machine timer at `0x20010580` ticks every 4,000 cycles: 150 ticks in 600,003 cycles | This confirms the Zephyr port's 25 kHz figure |
| A 2-instruction loop (`addi` + taken `bnez`) runs at 1.5 cycles per instruction | Taken branches cost an extra cycle |

Results on this board:

- **qrvc:** all 8 example and error-correction-lab circuits pass, with outcome distributions identical to QEMU's for the same seed.
- **rvpqc:** all 8 post-quantum schemes pass the differential check, at 1.03 to 1.27 cycles per instruction.

## Memory map (THEJAS32)

| Region | Address | Use |
|---|---|---|
| SRAM | `0x200000`–`0x201FFF` | vegamon (code, data, its stack) |
| SRAM | `0x202000`–`0x23E7FF` | programs; the stack fills whatever the image leaves free |
| SRAM | `0x23E800`–`0x23FFFF` | reserved by the boot ROM |
| UART0 | `0x10000100` | ns16550, registers 4 bytes apart; wired to the USB-serial chip |
| mtime | `0x20010580` | 64-bit machine timer, core clock / 4000 |

Sources: Zephyr's `dts/riscv/cdac/thejas32.dtsi` and `soc/cdac/thejas32/Kconfig.defconfig`
(written and checked on silicon by the Zephyr port's author), and the ARIES ECO datasheet
(CP2102N on UART0).

## Line protocol (vegamon)

| Host sends | vegamon replies |
|---|---|
| `P` | `VEGAMON 1 caps=… cyc=… ins=… mt=… base=… top=…` |
| `L addr len crc32` | `K`, then reads `len` raw bytes, then `OK crc` or `BAD crc` |
| `G addr` | `GO`, then starts the program with `a0` = return address, `a1` = caps |
| (program returns) | `RET code` |
| (program traps unexpectedly) | `E cause mepc mtval`, then `RET e000cccc` |

## Quick check

```bash
pip install pyserial
python board/vega_board.py --port /dev/ttyUSB0      # press RESET when asked
```

This prints the core report. Run it a second time without a reset: it finds vegamon still
running.

## Bring-up test

```bash
python board/vega_board.py --port /dev/ttyUSB0 --hello
```

This uploads a 1 KB assembly program instead of vegamon. It checks printing, memory, `mtvec`, traps
and the counters one step at a time, printing H1 to H9. If something breaks, the last line shows
where.

## If the upload does not start

- **Check boot mode and timing.** Make sure the board is in UART boot mode (BOOT SEL jumper open). Press RESET after the script says it is waiting.
- **Free the serial port.** Close anything else using it, such as an Arduino serial monitor or minicom.
- **Upload the loader with another tool.** Build `vegamon.bin` with `python board/vega_board.py --save-loader out/` and upload it with any standard XMODEM sender (Tera Term, minicom with `sx`, or C-DAC's VEGA XMODEM tool), then press ENTER. Do not use vegadude 1.0 on the ARIES ECO: it numbers the first block 0, and this ROM then loads the image 128 bytes too low. Then run the tools as usual: they find vegamon already running.

## Testing without a board

`--board qemu-sim` runs exactly this flow in QEMU's `virt` machine with the same 250 KB budget.
`tests/test_board.py` in qrvc also tests the XMODEM sender against a fake boot ROM that corrupts
one block.
