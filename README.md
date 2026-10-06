# rvpqc: post-quantum cryptography performance lab for RISC-V

`rvpqc` measures how the three finalized NIST post-quantum standards perform on small RISC-V
processors: ML-KEM (FIPS 203), ML-DSA (FIPS 204) and SLH-DSA (FIPS 205). It
answers the question device and chip makers ask first: **can ML-KEM and ML-DSA run on our
hardware, and what will they cost in time, memory and code size?**

For each algorithm it builds a vetted portable implementation for bare-metal RV32 (ML-KEM and
ML-DSA from [PQClean](https://github.com/PQClean/PQClean); SLH-DSA from the PQ Code Package's
[slhdsa-c](https://github.com/pq-code-package/slhdsa-c), since PQClean's SPHINCS+ predates the
final FIPS 205), runs it on QEMU with **exact instruction counting**, and measures:

- instructions per operation (key generation, encapsulation/decapsulation, signing/verification), over several runs with different random inputs;
- peak stack use per operation;
- code size of the algorithm itself.

Every run also checks correctness, so the numbers come from code that provably works, and every
results file records the exact toolchain (compiler, QEMU, source commits) so numbers stay
comparable across machines.

## Requirements

- Python 3.8+
- QEMU for RISC-V (`qemu-system-riscv32`)
- a RISC-V GCC (e.g. `riscv64-unknown-elf-gcc`)
- a host C compiler (`cc`), for the differential check
- PQClean and slhdsa-c checkouts (fetched automatically with `--fetch`)

On Ubuntu or WSL:

```bash
sudo apt install qemu-system-misc gcc-riscv64-unknown-elf gcc git
```

## Usage

```bash
python rvpqc.py --fetch                                   # default set: ML-KEM, ML-DSA, SLH-DSA-*-128f
python rvpqc.py --schemes ml-kem-768 --iter 20            # one algorithm, more runs
python rvpqc.py --schemes slh-dsa-sha2-128s               # any of the 12 SLH-DSA parameter sets
python rvpqc.py --march rv32im rv32imc --opt O2 Os        # compare build configurations side by side
python rvpqc.py --cflags="-fno-schedule-insns"            # try extra compiler flags
python rvpqc.py --self-test                               # confirm the correctness check catches errors
```

With several `--march`/`--opt` values, `results.md` adds a comparison table showing each
configuration's instructions and code size relative to the first.

Results are printed and written to `results/results.json`, `results.csv` and `results.md`.

## How the measurements work

- **Instruction counts** come from the RISC-V `instret` counter, read immediately before and after each operation. QEMU runs with `-icount shift=0`, which makes that counter exact (a calibration loop of exactly 2,001 instructions reads back as 2,001 plus the counter read itself). rvpqc reads the full 64-bit counter, because SLH-DSA's "s" variants exceed 4.3 billion instructions per signature, and it measures and subtracts the counter-reading overhead at start-up.
- **Peak stack** is measured by filling the stack with a known pattern before each operation and finding the deepest point that was overwritten afterwards.
- **Code size** is the `.text` size of the algorithm's source files plus the Keccak/SHAKE code it uses, excluding the benchmark harness.

## How correctness is checked

1. **Functional checks on RISC-V:** KEM shared secrets must match between encapsulation and decapsulation; signatures must verify; a signature with one flipped bit must be rejected.
2. **Differential check:** the same benchmark is built natively on the host with the same deterministic random inputs. Every key, ciphertext, shared secret and signature must be byte-identical between the RISC-V and native builds.
3. **Self-test:** `--self-test` plants a one-bit error in the RISC-V build and confirms the differential check catches it.

## Results: RV32IM, GCC 13.2 -O2, 15 runs (SLH-DSA: 2 runs)

| Algorithm | Operation | Instructions (median) | Min | Max | Peak stack (B) | Code (B) |
|---|---|---:|---:|---:|---:|---:|
| ML-KEM-512 | keypair | 1,030,258 | 1,029,811 | 1,057,039 | 6,032 | 23,072 |
| ML-KEM-512 | encaps | 1,098,999 | 1,098,552 | 1,125,780 | 8,672 | 23,072 |
| ML-KEM-512 | decaps | 1,273,850 | 1,273,403 | 1,300,631 | 9,424 | 23,072 |
| ML-KEM-768 | keypair | 1,668,418 | 1,668,037 | 1,668,959 | 10,128 | 23,348 |
| ML-KEM-768 | encaps | 1,814,448 | 1,814,067 | 1,814,989 | 13,280 | 23,348 |
| ML-KEM-768 | decaps | 2,046,846 | 2,046,465 | 2,047,387 | 14,352 | 23,348 |
| ML-KEM-1024 | keypair | 2,618,889 | 2,618,075 | 2,645,506 | 15,264 | 24,108 |
| ML-KEM-1024 | encaps | 2,793,824 | 2,793,010 | 2,820,441 | 18,912 | 24,108 |
| ML-KEM-1024 | decaps | 3,090,539 | 3,089,725 | 3,117,156 | 20,464 | 24,108 |
| ML-DSA-44 | keypair | 3,599,924 | 3,546,180 | 3,653,553 | 38,192 | 30,552 |
| ML-DSA-44 | sign | 8,681,277 | 5,219,772 | 21,870,099 | 51,648 | 30,552 |
| ML-DSA-44 | verify | 3,719,473 | 3,719,290 | 3,719,839 | 35,904 | 30,552 |
| ML-DSA-65 | keypair | 6,337,452 | 6,337,041 | 6,338,092 | 60,720 | 30,236 |
| ML-DSA-65 | sign | 13,211,510 | 8,270,384 | 35,244,216 | 79,312 | 30,236 |
| ML-DSA-65 | verify | 6,286,155 | 6,285,912 | 6,286,335 | 57,440 | 30,236 |
| ML-DSA-87 | keypair | 10,738,533 | 10,684,713 | 10,845,752 | 97,568 | 30,324 |
| ML-DSA-87 | sign | 16,792,301 | 13,529,276 | 41,779,066 | 122,304 | 30,324 |
| ML-DSA-87 | verify | 10,806,195 | 10,805,958 | 10,806,465 | 92,528 | 30,324 |
| SLH-DSA-SHA2-128f | keypair | 23,222,167 | 23,222,167 | 23,222,167 | 3,812 | 52,399 |
| SLH-DSA-SHA2-128f | sign | 543,324,825 | 543,288,658 | 543,360,992 | 4,004 | 52,399 |
| SLH-DSA-SHA2-128f | verify | 32,711,903 | 32,260,888 | 33,162,918 | 3,972 | 52,399 |
| SLH-DSA-SHAKE-128f | keypair | 107,134,587 | 107,134,587 | 107,134,587 | 3,520 | 52,399 |
| SLH-DSA-SHAKE-128f | sign | 2,505,539,889 | 2,504,655,770 | 2,506,424,009 | 3,712 | 52,399 |
| SLH-DSA-SHAKE-128f | verify | 151,970,634 | 151,793,645 | 152,147,624 | 3,664 | 52,399 |

All 24 operations passed every functional check and matched the native build byte for byte. The
full benchmark has also been reproduced on a second machine (GCC 10.2, QEMU 6.2) with every check
passing; exact counts there are 1–3% lower, as expected from the different compiler.
SLH-DSA's code size covers the whole slhdsa-c library (all 12 parameter sets), so it is the same
for both variants.

## Findings

- **ML-KEM is lightweight:** 1–3 million instructions and 6–20 KB of stack per operation, practical even on small cores.
- **ML-DSA signing time varies widely.** Signing retries internally until it finds a valid signature, so the worst case can be 2–4 times the median (ML-DSA-44: median 8.7 million, worst 21.9 million in 15 runs). Devices with timing deadlines must budget for the worst case.
- **ML-DSA's portable code is stack-hungry:** 52 KB (ML-DSA-44) to 122 KB (ML-DSA-87). On a microcontroller with 256 KB of SRAM, ML-DSA-87 would use half the memory as stack.
- **SLH-DSA is the opposite:** only about 4 KB of stack, but far slower to sign (543 million instructions for SHA2-128f).
- **On 32-bit cores, choose SLH-DSA's SHA-2 variants:** SHAKE-128f signing costs 4.6 times as much as SHA2-128f, because SHAKE's Keccak works on 64-bit words that a 32-bit core must emulate.
- **SLH-DSA's "s" and "f" variants trade signing for verification:** SHA2-128s signs about 21 times slower than SHA2-128f (11.3 billion vs 543 million instructions) but verifies about 3 times faster, with signatures less than half the size (7,856 vs 17,088 bytes). That suits firmware signing: sign rarely in the factory, verify on every boot.
- **One compiler flag cuts ML-KEM's cost by about a quarter.** With GCC 13 on RV32, `-O2`'s pre-register-allocation scheduling makes the Keccak permutation spill heavily to the stack (895 stack loads versus 347 without it). Adding `-fno-schedule-insns` reduced ML-KEM-768 encapsulation from 1.81 million to 1.31 million instructions (−28%), also beating `-Os` (1.43 million). **Reproduced independently with GCC 10.2** on a second machine: −26.3% for encapsulation, −28.7% for key generation, −23.3% for decapsulation, again beating `-Os`, and with 14% smaller code. rvpqc measures this for any toolchain with `--cflags`.
- **Compressed instructions shrink code by about a quarter, and `-Os` with compressed instructions by about 45%** (ML-KEM-768: 23,348 → 12,808 bytes).

## Limitations

- **Instructions are not cycles.** Real time depends on the core's pipeline and memory; on small in-order cores, multiplies, loads and taken branches often take more than one cycle. Cycle-accurate numbers need a real board or RTL simulation.
- **Portable reference code.** PQClean's `clean` implementations favor portability over speed. Optimized implementations, or custom instructions, can be much faster.
- **A simple runtime.** The benchmark uses byte-wise `memcpy`/`memset` and a bump allocator. These are slower than an optimized C library, so counts are slightly conservative.
- **A generic core.** QEMU models a standard RISC-V core, not a specific chip.
- **Toolchain-dependent counts.** Exact numbers change by a few percent between compiler versions; always compare results produced with the same toolchain (recorded in every results file).

## Roadmap

- More algorithms: Falcon/FN-DSA and HQC once standardized.
- Real-board timing on VEGA/ARIES and other RISC-V development boards.
- Cycle-accurate measurement through RTL simulation of open cores.
- Optimized implementations alongside the portable ones, and custom acceleration instructions.

## Related project

[qrvc](https://github.com/ssmind5tech/qrvc-v-0.1.0/tree/main/qrvc/qrvc), from the same team: an OpenQASM to RISC-V quantum
extension compiler.

## License

rvpqc is released under the MIT license. PQClean and slhdsa-c are fetched separately and keep
their own licenses (see their repositories).
