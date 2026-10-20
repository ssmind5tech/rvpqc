# Board bring-up test: pure assembly, modelled on the THEJAS32 programmer's manual hello-world.
# It checks, one step at a time, everything vegamon relies on, printing a line after each step,
# so if the board restarts, the last line printed shows which step broke it.
#   H1  printing works (no stack, no CSRs)
#   H2  a store/load high in SRAM works      H3  the same at the bottom (vegamon's stack area)
#   H4  disabling interrupts works           H5  mtvec takes our handler (prints what it reads back)
#   H6  a custom-0 instruction traps to our handler (cause, mtval)   H7  execution continues after it
#   H8  counter CSRs (a missing one traps and prints its cause instead)
#   H9  alive, repeated forever
# Build parameters: UART_BASE, UART_STRIDE, MON_BASE (load address), APP_TOP.
    .section .text.init
    .globl _start
    .option norvc
_start:
    li   s0, UART_BASE
    la   a0, m1
    jal  ra, puts                    # H1: no stack used yet
    li   sp, (APP_TOP - 256)
    li   t0, 0x5a5a1234
    sw   t0, -4(sp)
    lw   t1, -4(sp)
    bne  t0, t1, fail
    la   a0, m2
    jal  ra, puts
    li   t2, (MON_BASE + 0x1F00)
    sw   t0, 0(t2)
    lw   t1, 0(t2)
    bne  t0, t1, fail
    la   a0, m3
    jal  ra, puts
    csrci mstatus, 8
    csrw mie, zero
    la   a0, m4
    jal  ra, puts
    la   t0, handler
    csrw mtvec, t0
    csrr s1, mtvec
    la   a0, m5
    jal  ra, puts
    mv   a0, s1
    jal  ra, puthex
    jal  ra, newline
    la   a0, m6
    jal  ra, puts
    .4byte 0x1234500b                # custom-0: should trap to handler
    la   a0, m7
    jal  ra, puts
    la   a0, c_mcycle
    jal  ra, puts
    csrr a0, 0xb00
    jal  ra, puthex
    jal  ra, newline
    la   a0, c_mcycleh
    jal  ra, puts
    csrr a0, 0xb80
    jal  ra, puthex
    jal  ra, newline
    la   a0, c_minstret
    jal  ra, puts
    csrr a0, 0xb02
    jal  ra, puthex
    jal  ra, newline
    la   a0, c_minstreth
    jal  ra, puts
    csrr a0, 0xb82
    jal  ra, puthex
    jal  ra, newline
    la   a0, c_cycle
    jal  ra, puts
    csrr a0, 0xc00
    jal  ra, puthex
    jal  ra, newline
    la   a0, c_instret
    jal  ra, puts
    csrr a0, 0xc02
    jal  ra, puthex
    jal  ra, newline
    li   s2, 0
alive:
    la   a0, m9
    jal  ra, puts
    mv   a0, s2
    jal  ra, puthex
    jal  ra, newline
    addi s2, s2, 1
    li   t0, 25000000                # about half a second at 100 MHz
1:  addi t0, t0, -1
    bnez t0, 1b
    j    alive
fail:
    la   a0, mf
    jal  ra, puts
2:  j    2b

# ---- trap handler: print cause, mepc, mtval, then skip the instruction
    .balign 4
handler:
    addi sp, sp, -32
    sw   ra, 0(sp)
    sw   a0, 4(sp)
    sw   t0, 8(sp)
    sw   t1, 12(sp)
    sw   t2, 16(sp)
    la   a0, mt
    jal  ra, puts
    csrr a0, mcause
    jal  ra, puthex
    li   a0, ' '
    jal  ra, putc
    csrr a0, mepc
    jal  ra, puthex
    li   a0, ' '
    jal  ra, putc
    csrr a0, mtval
    jal  ra, puthex
    jal  ra, newline
    csrr t0, mepc
    addi t0, t0, 4
    csrw mepc, t0
    lw   ra, 0(sp)
    lw   a0, 4(sp)
    lw   t0, 8(sp)
    lw   t1, 12(sp)
    lw   t2, 16(sp)
    addi sp, sp, 32
    mret

# ---- output (s0 = UART base); putc uses t0, t1
putc:
1:  lbu  t1, (5 * UART_STRIDE)(s0)
    andi t1, t1, 0x20
    beqz t1, 1b
    sb   a0, 0(s0)
    ret
puts:                                # a0 = string; uses t2 and a0
    mv   t2, a0
    mv   t3, ra
1:  lbu  a0, 0(t2)
    beqz a0, 2f
    jal  ra, putc
    addi t2, t2, 1
    j    1b
2:  mv   ra, t3
    ret
newline:
    mv   t3, ra
    li   a0, 13
    jal  ra, putc
    li   a0, 10
    jal  ra, putc
    mv   ra, t3
    ret
puthex:                              # a0 = value
    mv   t4, a0
    mv   t5, ra
    li   t6, 28
1:  srl  a0, t4, t6
    andi a0, a0, 15
    addi a0, a0, 48
    li   t1, 58
    blt  a0, t1, 2f
    addi a0, a0, 39
2:  jal  ra, putc
    addi t6, t6, -4
    bgez t6, 1b
    mv   ra, t5
    ret

    .section .rodata
m1: .asciz "\r\nH1 hello from the board: printing works\r\n"
m2: .asciz "H2 stack store/load at the top of SRAM ok\r\n"
m3: .asciz "H3 store/load at the bottom of SRAM ok\r\n"
m4: .asciz "H4 interrupts disabled\r\n"
m5: .asciz "H5 mtvec reads back "
m6: .asciz "H6 trying a custom-0 instruction...\r\n"
m7: .asciz "H7 continued after the trap\r\n"
mt: .asciz "   trap: mcause mepc mtval = "
c_mcycle:    .asciz "H8 mcycle    "
c_mcycleh:   .asciz "H8 mcycleh   "
c_minstret:  .asciz "H8 minstret  "
c_minstreth: .asciz "H8 minstreth "
c_cycle:     .asciz "H8 cycle     "
c_instret:   .asciz "H8 instret   "
m9: .asciz "H9 alive "
mf: .asciz "FAIL memory store/load mismatch\r\n"
