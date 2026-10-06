/* rvpqc benchmark harness.
 *
 * Built twice from the same source:
 *   - bare-metal RV32 (default): runs under QEMU with -icount, measures instructions retired
 *     (rdinstret) and stack high-water per operation, prints results over the UART;
 *   - natively on the host (-DHOST): runs the same operations and prints only the output
 *     digests, for a differential check that the RISC-V build computes identical results.
 * Randomness is a deterministic SHAKE256 stream, so both builds see the same "random" bytes.
 */
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "fips202.h"
#ifdef SLH
#include "slh_dsa.h"
#else
#include "api.h"
#endif

#ifndef ITER
#define ITER 5
#endif

#define CAT_(a, b) a##b
#define CAT(a, b) CAT_(a, b)
#define F(name) CAT(NS, name)

/* ------------------------------------------------------------ deterministic randombytes */
static shake256incctx rng;
static int rng_ready;
static void rng_seed(uint32_t seed) {
    uint8_t s[8] = {'r', 'v', 'p', 'q', (uint8_t)seed, (uint8_t)(seed >> 8), (uint8_t)(seed >> 16), (uint8_t)(seed >> 24)};
    if (rng_ready) shake256_inc_ctx_release(&rng);
    shake256_inc_init(&rng);
    shake256_inc_absorb(&rng, s, sizeof s);
    shake256_inc_finalize(&rng);
    rng_ready = 1;
}
/* PQClean's randombytes.h renames randombytes() to PQCLEAN_randombytes() */
int PQCLEAN_randombytes(uint8_t *out, size_t n) { shake256_inc_squeeze(out, n, &rng); return 0; }

/* ------------------------------------------------------------ output */
#ifdef HOST
#include <stdio.h>
#include <stdlib.h>
static void out_str(const char *s) { fputs(s, stdout); }
static void finish(int code) { exit(code); }
#else
#define UART ((volatile uint8_t *)0x10000000u)
static void out_str(const char *s) { while (*s) *UART = (uint8_t)*s++; }
static void finish(int code) { *(volatile uint32_t *)0x100000u = code ? 0x13333u | ((uint32_t)code << 16) : 0x5555u; for (;;) {} }
#endif
static void out_hex(const uint8_t *p, size_t n) {
    static const char h[] = "0123456789abcdef";
    char b[3] = {0, 0, 0};
    for (size_t i = 0; i < n; i++) { b[0] = h[p[i] >> 4]; b[1] = h[p[i] & 15]; out_str(b); }
}
static void out_u32(uint32_t v) {
    char b[11]; int i = 10; b[i] = 0;
    do { b[--i] = (char)('0' + v % 10); v /= 10; } while (v);
    out_str(b + i);
}

/* ------------------------------------------------------------ measurement */
#ifdef HOST
#define MEASURE_BEGIN() do {} while (0)
#define MEASURE_END(op) do {} while (0)
static void calibrate(void) {}
#else
#define STACK_PAINT_BYTES (768u * 1024u)
#define PAINT 0xA5C3E1F7u
/* 64-bit instret (CSRs 0xC02 low, 0xC82 high on RV32): SLH-DSA "s" variants can exceed 2^32. */
static inline uint64_t rd_instret(void) {
    uint32_t hi, lo, hi2;
    do {
        __asm__ volatile("csrr %0, 0xc82" : "=r"(hi));
        __asm__ volatile("csrr %0, 0xc02" : "=r"(lo));
        __asm__ volatile("csrr %0, 0xc82" : "=r"(hi2));
    } while (hi != hi2);
    return ((uint64_t)hi << 32) | lo;
}
static uint64_t t0, overhead;
static volatile uint32_t *paint_lo;
static void __attribute__((noinline)) stack_paint(void) {
    volatile uint32_t here;
    volatile uint32_t *top = &here - 64;            /* leave this frame alone */
    paint_lo = top - STACK_PAINT_BYTES / 4;
    for (volatile uint32_t *p = paint_lo; p < top; p++) *p = PAINT;
}
static uint32_t __attribute__((noinline)) stack_used(void) {
    volatile uint32_t here;
    volatile uint32_t *top = &here - 64;
    volatile uint32_t *p = paint_lo;
    while (p < top && *p == PAINT) p++;
    return (uint32_t)((top - p) * 4);
}
static void out_u64(uint64_t v) {
    char b[21]; int i = 20; b[i] = 0;
    do { b[--i] = (char)('0' + (int)(v % 10)); v /= 10; } while (v);
    out_str(b + i);
}
static void report(const char *op, uint64_t insns, uint32_t stack) {
    out_str("R "); out_str(op); out_str(" "); out_u64(insns); out_str(" "); out_u32(stack); out_str("\n");
}
static void calibrate(void) {      /* instructions the measurement itself adds */
    uint64_t a = rd_instret(), b = rd_instret();
    overhead = b - a;
}
#define MEASURE_BEGIN() do { stack_paint(); t0 = rd_instret(); } while (0)
#define MEASURE_END(op) do { uint64_t t1 = rd_instret(); report(op, t1 - t0 - overhead, stack_used()); } while (0)
#endif

/* ------------------------------------------------------------ operations */
#if defined(SLH)
#define PRM CAT(slh_dsa_, SLH)
static uint8_t pk[64], sk[128], sig[49856], addrnd[32];
static size_t pk_len, sk_len, sig_len;
static const uint8_t msg[] = "rvpqc benchmark message: 59 bytes of data to be signed....";
static const uint8_t ctxs[] = "rvpqc";
#elif defined(KEM)
static uint8_t pk[CRYPTO_PUBLICKEYBYTES], sk[CRYPTO_SECRETKEYBYTES];
static uint8_t ct[CRYPTO_CIPHERTEXTBYTES], ss1[CRYPTO_BYTES], ss2[CRYPTO_BYTES];
#else
static uint8_t pk[CRYPTO_PUBLICKEYBYTES], sk[CRYPTO_SECRETKEYBYTES];
static uint8_t sig[CRYPTO_BYTES];
static const uint8_t msg[] = "rvpqc benchmark message: 59 bytes of data to be signed....";
#endif

static void digest(const char *label, uint32_t iter) {
    uint8_t d[16];
#ifdef RVPQC_SABOTAGE
    pk[0] ^= 1;   /* self-test only: corrupt one output bit in this build */
#endif
    shake256incctx h;
    shake256_inc_init(&h);
#if defined(SLH)
    shake256_inc_absorb(&h, pk, pk_len);
    shake256_inc_absorb(&h, sk, sk_len);
    shake256_inc_absorb(&h, sig, sig_len);
#else
    shake256_inc_absorb(&h, pk, sizeof pk);
    shake256_inc_absorb(&h, sk, sizeof sk);
#endif
#if defined(SLH)
#elif defined(KEM)
    shake256_inc_absorb(&h, ct, sizeof ct);
    shake256_inc_absorb(&h, ss1, sizeof ss1);
#else
    shake256_inc_absorb(&h, sig, sizeof sig);
#endif
    shake256_inc_finalize(&h);
    shake256_inc_squeeze(d, sizeof d, &h);
    shake256_inc_ctx_release(&h);
    out_str("H "); out_str(label); out_str(" "); out_u32(iter); out_str(" "); out_hex(d, sizeof d); out_str("\n");
}

int main(void) {
    out_str("BEGIN " ALGNAME "\n");
    calibrate();
    for (uint32_t it = 0; it < ITER; it++) {
        rng_seed(it);
#if defined(SLH)
        pk_len = slh_pk_sz(&PRM); sk_len = slh_sk_sz(&PRM);
        MEASURE_BEGIN(); slh_keygen(sk, pk, PQCLEAN_randombytes, &PRM); MEASURE_END("keypair");
        PQCLEAN_randombytes(addrnd, pk_len / 2);            /* n bytes of hedging randomness */
        MEASURE_BEGIN();
        sig_len = slh_sign(sig, msg, sizeof msg - 1, ctxs, sizeof ctxs - 1, sk, addrnd, &PRM);
        MEASURE_END("sign");
        if (sig_len != slh_sig_sz(&PRM)) { out_str("FAIL signing produced the wrong length\n"); finish(1); }
        int ok;
        MEASURE_BEGIN(); ok = slh_verify(msg, sizeof msg - 1, sig, sig_len, ctxs, sizeof ctxs - 1, pk, &PRM); MEASURE_END("verify");
        if (ok != 1) { out_str("FAIL signature did not verify\n"); finish(1); }
        sig[0] ^= 1;
        if (slh_verify(msg, sizeof msg - 1, sig, sig_len, ctxs, sizeof ctxs - 1, pk, &PRM) == 1) { out_str("FAIL tampered signature accepted\n"); finish(1); }
        sig[0] ^= 1;
#elif defined(KEM)
        MEASURE_BEGIN(); F(crypto_kem_keypair)(pk, sk); MEASURE_END("keypair");
        MEASURE_BEGIN(); F(crypto_kem_enc)(ct, ss1, pk); MEASURE_END("encaps");
        MEASURE_BEGIN(); F(crypto_kem_dec)(ss2, ct, sk); MEASURE_END("decaps");
        if (memcmp(ss1, ss2, sizeof ss1) != 0) { out_str("FAIL shared secrets differ\n"); finish(1); }
#else
        size_t siglen = 0;
        MEASURE_BEGIN(); F(crypto_sign_keypair)(pk, sk); MEASURE_END("keypair");
        MEASURE_BEGIN(); F(crypto_sign_signature)(sig, &siglen, msg, sizeof msg - 1, sk); MEASURE_END("sign");
        int ok;
        MEASURE_BEGIN(); ok = F(crypto_sign_verify)(sig, siglen, msg, sizeof msg - 1, pk); MEASURE_END("verify");
        if (ok != 0) { out_str("FAIL signature did not verify\n"); finish(1); }
        sig[0] ^= 1;   /* a tampered signature must be rejected */
        if (F(crypto_sign_verify)(sig, siglen, msg, sizeof msg - 1, pk) == 0) { out_str("FAIL tampered signature accepted\n"); finish(1); }
        sig[0] ^= 1;
#endif
        digest("out", it);
    }
    out_str("END\n");
    finish(0);
    return 0;
}
