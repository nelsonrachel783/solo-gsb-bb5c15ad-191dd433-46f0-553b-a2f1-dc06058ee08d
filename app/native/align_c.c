/*
 * 全局最小编辑代价对齐内核（Levenshtein + “同分时优先较早原位置”）。
 *
 * 输入：源串 s[0..m-1]（按时间排序的识别字符），目标串 t[0..n-1]（人工修订文本）。
 * 输出：out[j] = 修订字符 t[j] 对齐到的识别字符下标；-1 表示该修订字符为新增。
 * 返回值：0 成功；-1 内存不足；-2 参数非法。
 *
 * 算法（O(m*n) 时间）：
 *   1) 前向两行 DP 求最小编辑代价 E；
 *   2) 反向 DP 求后缀编辑距离 B[i][j] = edit(s[i:], t[j:])（uint16 紧凑存储）；
 *   3) 自 (0,0) 起“可行性贪心”游走，已用代价 spent 始终满足 spent + B[i][j] == E：
 *        - 能走“匹配/替换”对角线且仍可达到 E，则走对角线（优先匹配更早的原位置，
 *          重复字符据此结合上下文选择）；
 *        - 否则若“新增”可行则新增（保留当前识别字符，等待后面的匹配）；
 *        - 否则“删除”。
 *      该规则经穷举验证可得到最小编辑代价下、按识别字符原位置字典序最早的匹配方案。
 *
 * B 使用 uint16（要求 m+n < 65535，由应用层上限保证）；m=n=20000 约占 800 MB。
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static int edit_cost(uint32_t a, uint32_t b) { return a == b ? 0 : 1; }

int align_chars(const uint32_t *s, int m, const uint32_t *t, int n, int32_t *out) {
    if (m < 0 || n < 0) return -2;
    if (m > 65534 || n > 65534) return -2;

    int W = n + 1;

    /* ---- 1) 前向两行 DP 求 E ---- */
    int *prev = (int *)malloc(sizeof(int) * (size_t)W);
    int *cur = (int *)malloc(sizeof(int) * (size_t)W);
    if (!prev || !cur) { free(prev); free(cur); return -1; }
    for (int j = 0; j <= n; j++) prev[j] = j;
    for (int i = 1; i <= m; i++) {
        cur[0] = i;
        for (int j = 1; j <= n; j++) {
            int d = prev[j - 1] + edit_cost(s[i - 1], t[j - 1]);
            int ins = cur[j - 1] + 1;
            int del = prev[j] + 1;
            int best = d;
            if (ins < best) best = ins;
            if (del < best) best = del;
            cur[j] = best;
        }
        int *tmp = prev; prev = cur; cur = tmp;
    }
    int E = prev[n];
    free(cur);

    /* ---- 2) 反向后缀编辑距离 B（uint16） ---- */
    size_t cells = (size_t)(m + 1) * (size_t)W;
    uint16_t *B = (uint16_t *)malloc((cells ? cells : 1) * sizeof(uint16_t));
    if (!B) { free(prev); return -1; }

    for (int j = 0; j <= n; j++) B[(size_t)m * W + j] = (uint16_t)(n - j);
    for (int i = 0; i <= m; i++) B[(size_t)i * W + n] = (uint16_t)(m - i);
    for (int i = m - 1; i >= 0; i--) {
        size_t row = (size_t)i * W;
        size_t nxt = row + W;
        uint32_t sc = s[i];
        for (int j = n - 1; j >= 0; j--) {
            int d = (int)B[nxt + (j + 1)] + edit_cost(sc, t[j]);
            int ins = (int)B[row + (j + 1)] + 1;
            int del = (int)B[nxt + j] + 1;
            int best = d;
            if (ins < best) best = ins;
            if (del < best) best = del;
            B[row + j] = (uint16_t)best;
        }
    }

    /* ---- 3) 可行性贪心游走 ---- */
    for (int j = 0; j < n; j++) out[j] = -1;
    int i = 0, j = 0, spent = 0;
    while (i < m || j < n) {
        int diag_ok = 0, cd = 1;
        if (i < m && j < n) {
            cd = edit_cost(s[i], t[j]);
            diag_ok = (spent + cd + (int)B[(size_t)(i + 1) * W + (j + 1)] == E);
        }
        if (diag_ok) {
            out[j] = i;
            spent += cd;
            i++; j++;
        } else if (j < n &&
                   spent + 1 + (int)B[(size_t)i * W + (j + 1)] == E) {
            spent++; j++;                 /* 新增：保留识别字符等待后续匹配 */
        } else {
            /* 不变式保证此删除必然可行 */
            spent++; i++;                 /* 删除该识别字符 */
        }
    }

    free(prev);
    free(B);
    return 0;
}
