/* vegamon: resident loader for VEGA ET1031 / THEJAS32 boards (and QEMU 'virt' for testing).
 *
 * On start it probes the core (which counters exist and count, whether custom-0 instructions
 * trap, whether mtval holds the trapping instruction) and prints one banner line:
 *
 *   VEGAMON 1 caps=cccccccc cyc=yyyyyyyy ins=iiiiiiii base=bbbbbbbb top=tttttttt
 *
 * Then it serves three line commands (ASCII, hex arguments):
 *   P                       print the banner again (lets the host find a running monitor)
 *   L aaaaaaaa nnnnnnnn cc  load n raw bytes to address a; reply "K", receive the bytes,
 *                           reply "OK crc" if the CRC-32 matches cc, else "BAD crc"
 *   G aaaaaaaa              reply "GO" and jump to a with a0 = return address, a1 = caps;
 *                           when the program returns (jumps to a0 with an exit code) -> "RET code"
 * An unexpected trap in a program prints "E cause mepc mtval" and returns to the monitor.
 *
 * Board parameters come from the build: UART_BASE, UART_STRIDE, APP_BASE, APP_TOP.
 */
#include <stdint.h>

#ifndef UART_BASE
#error "build with -DUART_BASE=... -DUART_STRIDE=... -DAPP_BASE=... -DAPP_TOP=..."
#endif
#define UREG(off) (*(volatile uint8_t *)(UART_BASE + (off) * UART_STRIDE))
#define LSR_DR   0x01
#define LSR_THRE 0x20

/* capability bits (present in bits 0-15, "counts" in bits 16-31 for the counters) */
#define CAP_MCYCLE    (1u << 0)
#define CAP_MCYCLEH   (1u << 1)
#define CAP_MINSTRET  (1u << 2)
#define CAP_MINSTRETH (1u << 3)
#define CAP_CYCLE     (1u << 4)
#define CAP_CYCLEH    (1u << 5)
#define CAP_INSTRET   (1u << 6)
#define CAP_INSTRETH  (1u << 7)
#define CAP_TIME      (1u << 8)
#define CAP_CUSTOM0   (1u << 12)   /* custom-0 instructions raise an illegal-instruction trap */
#define CAP_MTVAL     (1u << 13)   /* mtval holds the trapping instruction word */
#define CAP_NOTRAP    (1u << 14)   /* mtvec did not take our handler: nothing that traps was probed */
/* bits 16-24: the counter in bit (n-16) counts; bits 25-30: further findings */
#define CAP_ECALL     (1u << 25)   /* ecall traps (cause 11) */
#define CAP_EBREAK    (1u << 26)   /* ebreak traps (cause 3) */
#define CAP_ZEROILL   (1u << 27)   /* the all-zero word traps as illegal (cause 2) */
#define CAP_MCYCLEH_OK  (1u << 28) /* mcycleh verified: it steps when mcycle wraps */
#define CAP_MINSTRETH_OK (1u << 29) /* minstreth verified the same way */
#define CAP_MTIME_OK  (1u << 30)   /* memory-mapped mtime ticks at clock / MON_MTIME_DIV */

void mon_reenter(void);
void mon_trap(void);
void mon_jump(uint32_t entry, void (*reenter)(void), uint32_t caps);

static uint32_t caps, loop_cyc, loop_ins, mt_ticks, mt_cycles;
static volatile int probing, trapped;
static volatile uint32_t last_cause, last_tval;

static void putc_(char c) { while (!(UREG(5) & LSR_THRE)) {} UREG(0) = (uint8_t)c; }
static void puts_(const char *s) { while (*s) putc_(*s++); }
static uint8_t getc_(void) { while (!(UREG(5) & LSR_DR)) {} return UREG(0); }
static void puthex(uint32_t v) { for (int i = 28; i >= 0; i -= 4) putc_("0123456789abcdef"[(v >> i) & 15]); }

static int readline(char *b, int max) {
    int n = 0;
    for (;;) {
        char c = (char)getc_();
        if (c == '\r') continue;
        if (c == '\n') break;
        if (n < max - 1) b[n++] = c;
    }
    b[n] = 0;
    return n;
}
static const char *hexarg(const char *p, uint32_t *v) {
    while (*p == ' ') p++;
    uint32_t x = 0;
    for (;; p++) {
        char c = *p;
        int d = c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
        if (d < 0) break;
        x = x * 16 + (uint32_t)d;
    }
    *v = x;
    return p;
}

/* ---------------------------------------------------------------- probing */
static void spin(void) {   /* exactly 2001 instructions */
    __asm__ volatile("li t0, 1000\n1: addi t0, t0, -1\n bnez t0, 1b" ::: "t0");
}
#define PROBE(csr, bit) do {                                        \
        uint32_t a_, b_;                                            \
        puts_("vegamon: probe csr " #csr "\n");                     \
        trapped = 0; probing = 1;                                   \
        __asm__ volatile("csrr %0, " #csr : "=r"(a_));              \
        spin();                                                     \
        __asm__ volatile("csrr %0, " #csr : "=r"(b_));              \
        probing = 0;                                                \
        if (!trapped) { caps |= (bit); if (a_ != b_) caps |= (bit) << 16; } \
    } while (0)
#define RD(csr) ({ uint32_t v_; __asm__ volatile("csrr %0, " #csr : "=r"(v_)); v_; })
#define TRAPTEST(name, insn, bit, want) do {                                         \
        puts_("vegamon: trap test (" name ")\n");                                    \
        trapped = 0; probing = 1;                                                    \
        __asm__ volatile(insn);                                                      \
        probing = 0;                                                                 \
        if (trapped && last_cause == (want)) caps |= (bit);                          \
    } while (0)
#define WRAPTEST(lo, hi, bit) do {                                                   \
        uint32_t h_, l_;                                                             \
        puts_("vegamon: wrap test " #hi "\n");                                       \
        trapped = 0; probing = 1;                                                    \
        __asm__ volatile("csrw " #hi ", zero\n csrw " #lo ", %0" :: "r"(0xFFFFFF00u)); \
        spin();                                                                      \
        h_ = RD(hi); l_ = RD(lo);                                                    \
        probing = 0;                                                                 \
        if (!trapped && h_ == 1 && l_ < 0x100000u) caps |= (bit);                    \
        puts_("vegamon:   after wrap: hi="); puthex(h_); puts_(" lo="); puthex(l_); putc_('\n'); \
    } while (0)

static void probe(void) {
    caps = 0;
    uint32_t tv;
    __asm__ volatile("csrr %0, mtvec" : "=r"(tv));
    puts_("vegamon: boot, mtvec="); puthex(tv); puts_(" (ours "); puthex((uint32_t)mon_trap); puts_(")\n");
    if ((tv & ~3u) != (uint32_t)mon_trap) {
        /* traps would go to the boot ROM (and restart the board): probe nothing that can trap */
        puts_("vegamon: mtvec is not writable; skipping trap tests\n");
        caps = CAP_NOTRAP;
        return;
    }
    /* the first deliberate trap: a custom-0 word (opcode 0x0B) must trap with cause 2 */
    puts_("vegamon: trap test (custom-0)\n");
    trapped = 0; probing = 1;
    __asm__ volatile(".4byte 0x1234500b");
    probing = 0;
    if (trapped && last_cause == 2) {
        caps |= CAP_CUSTOM0;
        if (last_tval == 0x1234500bu) caps |= CAP_MTVAL;
    }
    puts_("vegamon: trap test done, cause="); puthex(trapped ? last_cause : 0xffffffffu);
    puts_(" mtval="); puthex(trapped ? last_tval : 0); putc_('\n');
    PROBE(0xb00, CAP_MCYCLE);  PROBE(0xb80, CAP_MCYCLEH);
    PROBE(0xb02, CAP_MINSTRET); PROBE(0xb82, CAP_MINSTRETH);
    PROBE(0xc00, CAP_CYCLE);   PROBE(0xc80, CAP_CYCLEH);
    PROBE(0xc02, CAP_INSTRET); PROBE(0xc82, CAP_INSTRETH);
    PROBE(0xc01, CAP_TIME);
    /* other ways a program can trap into a handler (some cores silently ignore custom opcodes) */
    TRAPTEST("ecall", ".4byte 0x00000073", CAP_ECALL, 11);
    TRAPTEST("ebreak", ".4byte 0x00100073", CAP_EBREAK, 3);
    TRAPTEST("zero word", ".4byte 0x00000000", CAP_ZEROILL, 2);
    /* high halves: a CSR that reads 0 may be missing or just small, so make the low half wrap */
    WRAPTEST(0xb00, 0xb80, CAP_MCYCLEH_OK);
    WRAPTEST(0xb02, 0xb82, CAP_MINSTRETH_OK);
#ifdef MON_MTIME_ADDR
    if ((caps >> 16) & CAP_MCYCLE) {        /* is mtime where we think, at the rate we think? */
        puts_("vegamon: mtime check\n");
        volatile uint32_t *mt = (volatile uint32_t *)MON_MTIME_ADDR;
        uint32_t t0 = mt[0], c0 = RD(0xb00);
        __asm__ volatile("li t0, 200000\n1: addi t0, t0, -1\n bnez t0, 1b" ::: "t0");
        uint32_t t1 = mt[0], c1 = RD(0xb00);
        mt_ticks = t1 - t0; mt_cycles = c1 - c0;
        uint32_t est = mt_ticks * MON_MTIME_DIV, tol = mt_cycles / 20 + 2 * MON_MTIME_DIV;
        if (mt_ticks && est + tol >= mt_cycles && est <= mt_cycles + tol) caps |= CAP_MTIME_OK;
    }
#endif
    puts_("vegamon: probes done\n");
    /* cycles and instructions for the 2001-instruction loop (0 = no working counter) */
    uint32_t c0 = 0, c1 = 0, i0 = 0, i1 = 0;
    int cm = (caps >> 16) & CAP_MCYCLE, cu = (caps >> 16) & CAP_CYCLE;
    int im = (caps >> 16) & CAP_MINSTRET, iu = (caps >> 16) & CAP_INSTRET;
    if (cm) c0 = RD(0xb00); else if (cu) c0 = RD(0xc00);
    if (im) i0 = RD(0xb02); else if (iu) i0 = RD(0xc02);
    spin();
    if (im) i1 = RD(0xb02); else if (iu) i1 = RD(0xc02);
    if (cm) c1 = RD(0xb00); else if (cu) c1 = RD(0xc00);
    loop_cyc = c1 - c0;
    loop_ins = i1 - i0;
}

static void banner(void) {
    puts_("VEGAMON 1 caps="); puthex(caps);
    puts_(" cyc="); puthex(loop_cyc);
    puts_(" ins="); puthex(loop_ins);
    puts_(" mt="); puthex(mt_ticks); puts_("/"); puthex(mt_cycles);
    puts_(" base="); puthex(APP_BASE);
    puts_(" top="); puthex(APP_TOP);
    putc_('\n');
}

void mon_trap_c(uint32_t *frame) {
    uint32_t cause, epc, tval;
    __asm__ volatile("csrr %0, mcause" : "=r"(cause));
    __asm__ volatile("csrr %0, mepc" : "=r"(epc));
    __asm__ volatile("csrr %0, mtval" : "=r"(tval));
    if (probing) {
        trapped = 1; last_cause = cause; last_tval = tval;
        __asm__ volatile("csrw mepc, %0" :: "r"(epc + 4));
        return;
    }
    puts_("E "); puthex(cause); putc_(' '); puthex(epc); putc_(' '); puthex(tval); putc_('\n');
    frame[10] = 0xE0000000u | (cause & 0xFFFF);              /* exit code seen by the host */
    __asm__ volatile("csrw mepc, %0" :: "r"((uint32_t)mon_reenter));
}

/* ---------------------------------------------------------------- loader */
static uint32_t crc32_update(uint32_t c, uint8_t b) {
    c ^= b;
    for (int k = 0; k < 8; k++) c = (c >> 1) ^ (0xEDB88320u & -(c & 1u));
    return c;
}

static void serve(void) {
    char line[64];
    for (;;) {
        readline(line, sizeof line);
        if (line[0] == 'P') {
            banner();
        } else if (line[0] == 'L') {
            uint32_t a, n, want;
            const char *p = hexarg(line + 1, &a);
            p = hexarg(p, &n);
            hexarg(p, &want);
            if (a < APP_BASE || n > APP_TOP - APP_BASE || a - APP_BASE > APP_TOP - APP_BASE - n) {
                puts_("ERANGE\n");
                continue;
            }
            puts_("K\n");
            uint32_t c = 0xFFFFFFFFu;
            volatile uint8_t *dst = (volatile uint8_t *)a;
            for (uint32_t i = 0; i < n; i++) { uint8_t b = getc_(); dst[i] = b; c = crc32_update(c, b); }
            c = ~c;
            puts_(c == want ? "OK " : "BAD "); puthex(c); putc_('\n');
        } else if (line[0] == 'G') {
            uint32_t a;
            hexarg(line + 1, &a);
            puts_("GO\n");
            mon_jump(a, mon_reenter, caps);
        } else if (line[0]) {
            puts_("?\n");
        }
    }
}

void mon_resume(uint32_t code) {
    puts_("RET "); puthex(code); putc_('\n');
    serve();
}

int mon_main(void) {
    probe();
    banner();
    serve();
    return 0;
}
