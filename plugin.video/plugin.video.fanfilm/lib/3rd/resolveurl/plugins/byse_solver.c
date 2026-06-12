#include <stdint.h>
#include <string.h>
#include <stdio.h>

#define m 0xFFFFFFFF
#define be 512
#define lt 511
#define dr 2
#define lr 2654435761U
#define hr 2246822519U

static inline uint32_t re(uint32_t t, int e_val) {
    return ((t << e_val) | (t >> (32 - e_val))) & m;
}

static inline void ye(uint32_t *t) {
    t[0] = (t[0] + t[1]) & m;
    t[3] = re(t[3] ^ t[0], 16);
    t[2] = (t[2] + t[3]) & m;
    t[1] = re(t[1] ^ t[2], 12);
    t[0] = (t[0] + t[1]) & m;
    t[3] = re(t[3] ^ t[0], 8);
    t[2] = (t[2] + t[3]) & m;
    t[1] = re(t[1] ^ t[2], 7);
}

void gr(const uint8_t *t, int t_len, uint32_t *n) {
    uint32_t e[4] = {1779033703, 3144134277, 1013904242, 2773480762};
    for (int i = 0; i < t_len; i++) {
        e[0] = (e[0] + t[i]) & m;
        e[0] = re(e[0], 7);
        ye(e);
    }
    for (int i = 0; i < 8; i++) {
        ye(e);
    }
    uint32_t r[be];
    for (int i = 0; i < be; i++) {
        ye(e);
        r[i] = (e[0] ^ e[2]) & m;
    }
    for (int i = 0; i < dr; i++) {
        for (int s = 0; s < be; s++) {
            uint32_t a = r[s] & lt;
            uint32_t c = (r[s] + r[a]) & m;
            c = re(c, 13);
            c = (c ^ ((r[(s + 1) & lt] * lr) & m)) & m;
            r[s] = c;
            e[0] = (e[0] ^ c) & m;
            ye(e);
        }
    }
    int o = be / 8;
    for (int i = 0; i < 8; i++) {
        ye(e);
        uint32_t s = e[0];
        int a = i * o;
        for (int c = 0; c < o; c++) {
            uint32_t d = r[a + c];
            s = (s + d) & m;
            s = re(s, 5);
            s = (s ^ ((d * hr) & m)) & m;
        }
        n[i] = (s ^ e[2]) & m;
    }
}

int wr(const uint32_t *t) {
    int e_val = 0;
    for (int r_idx = 0; r_idx < 8; r_idx++) {
        uint32_t n_val = t[r_idx];
        if (n_val == 0) {
            e_val += 32;
            continue;
        }
        int clz = __builtin_clz(n_val);
        return e_val + clz;
    }
    return e_val;
}

int solve_challenge(const char *prefix, int difficulty, char *result_str) {
    uint8_t buffer[256];
    int prefix_len = strlen(prefix);
    strcpy((char*)buffer, prefix);
    
    uint32_t n[8];
    for (uint32_t s = 0; ; s++) {
        int s_len = sprintf((char*)buffer + prefix_len, "%u", s);
        gr(buffer, prefix_len + s_len, n);
        if (wr(n) >= difficulty) {
            sprintf(result_str, "%u", s);
            return 1;
        }
    }
    return 0;
}
