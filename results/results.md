# rvpqc results

Instructions retired per operation, measured exactly with QEMU `-icount` (64-bit `instret`, measurement overhead calibrated and subtracted).

## Environment

| Item | Value |
|---|---|
| date (UTC) | 2026-10-06 10:02 |
| RISC-V compiler | riscv64-unknown-elf-gcc (13.2.0-11ubuntu1+12) 13.2.0 |
| QEMU | QEMU emulator version 8.2.2 (Debian 1:8.2.2+ds-0ubuntu1.18) |
| host compiler (differential check) | cc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0 |
| host | Linux x86_64 |
| PQClean commit | 0586a82 |
| slhdsa-c commit | 174c02e |
| configurations | rv32im -O2 |
| extra compiler flags | none |
| runs per operation | ML-KEM/ML-DSA 15, SLH-DSA 2 |

## Results: rv32im -O2

| Algorithm | Operation | Instructions (median) | Min | Max | Peak stack (B) | Code (B) | Check |
|---|---|---:|---:|---:|---:|---:|---|
| ML-KEM-512 | keypair | 1,030,258 | 1,029,811 | 1,057,039 | 6,032 | 23,072 | PASS |
| ML-KEM-512 | encaps | 1,098,999 | 1,098,552 | 1,125,780 | 8,672 | 23,072 | PASS |
| ML-KEM-512 | decaps | 1,273,850 | 1,273,403 | 1,300,631 | 9,424 | 23,072 | PASS |
| ML-KEM-768 | keypair | 1,668,418 | 1,668,037 | 1,668,959 | 10,128 | 23,348 | PASS |
| ML-KEM-768 | encaps | 1,814,448 | 1,814,067 | 1,814,989 | 13,280 | 23,348 | PASS |
| ML-KEM-768 | decaps | 2,046,846 | 2,046,465 | 2,047,387 | 14,352 | 23,348 | PASS |
| ML-KEM-1024 | keypair | 2,618,889 | 2,618,075 | 2,645,506 | 15,264 | 24,108 | PASS |
| ML-KEM-1024 | encaps | 2,793,824 | 2,793,010 | 2,820,441 | 18,912 | 24,108 | PASS |
| ML-KEM-1024 | decaps | 3,090,539 | 3,089,725 | 3,117,156 | 20,464 | 24,108 | PASS |
| ML-DSA-44 | keypair | 3,599,924 | 3,546,180 | 3,653,553 | 38,192 | 30,552 | PASS |
| ML-DSA-44 | sign | 8,681,277 | 5,219,772 | 21,870,099 | 51,648 | 30,552 | PASS |
| ML-DSA-44 | verify | 3,719,473 | 3,719,290 | 3,719,839 | 35,904 | 30,552 | PASS |
| ML-DSA-65 | keypair | 6,337,452 | 6,337,041 | 6,338,092 | 60,720 | 30,236 | PASS |
| ML-DSA-65 | sign | 13,211,510 | 8,270,384 | 35,244,216 | 79,312 | 30,236 | PASS |
| ML-DSA-65 | verify | 6,286,155 | 6,285,912 | 6,286,335 | 57,440 | 30,236 | PASS |
| ML-DSA-87 | keypair | 10,738,533 | 10,684,713 | 10,845,752 | 97,568 | 30,324 | PASS |
| ML-DSA-87 | sign | 16,792,301 | 13,529,276 | 41,779,066 | 122,304 | 30,324 | PASS |
| ML-DSA-87 | verify | 10,806,195 | 10,805,958 | 10,806,465 | 92,528 | 30,324 | PASS |
| SLH-DSA-SHA2-128f | keypair | 23,222,167 | 23,222,167 | 23,222,167 | 3,812 | 52,399 | PASS |
| SLH-DSA-SHA2-128f | sign | 543,324,825 | 543,288,658 | 543,360,992 | 4,004 | 52,399 | PASS |
| SLH-DSA-SHA2-128f | verify | 32,711,903 | 32,260,888 | 33,162,918 | 3,972 | 52,399 | PASS |
| SLH-DSA-SHAKE-128f | keypair | 107,134,587 | 107,134,587 | 107,134,587 | 3,520 | 52,399 | PASS |
| SLH-DSA-SHAKE-128f | sign | 2,505,539,889 | 2,504,655,770 | 2,506,424,009 | 3,712 | 52,399 | PASS |
| SLH-DSA-SHAKE-128f | verify | 151,970,634 | 151,793,645 | 152,147,624 | 3,664 | 52,399 | PASS |

Instruction counts are not cycle counts: real time depends on the core's pipeline and memory system. Code size covers the algorithm and its hash code, excluding the benchmark harness. For SLH-DSA it covers the whole slhdsa-c library (all 12 parameter sets and both hash families), so it is the same for every SLH-DSA variant.
