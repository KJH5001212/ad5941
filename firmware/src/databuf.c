/*
 * databuf.c — 무손실 데이터 버퍼 구현 (RAM 링버퍼 + ACK 윈도우)
 *  seq 비교는 전부 부호있는 모듈러 차이((int32_t)(x-y)) 사용
 *  -> uint32 랩어라운드에도 안전 (497일+ 연속운전 대비).
 */
#include "databuf.h"
#include <zephyr/kernel.h>
#include <string.h>

/* 링버퍼 크기: 16B/샘플 * 768 = 12KB (RAM 여유 확보).
 * cycle 모드(측정 5s@10Hz=50샘플/주기)면 ~15주기 무연결 버퍼링.
 * 고속 연속(100Hz)이면 ~7.5s. 장시간은 향후 플래시 spill 로 확장. */
#define RING_N 768U

/* 모듈러 대소비교: a>b 이면 양수 (랩 안전) */
#define SEQ_GT(a, b)  ((int32_t)((a) - (b)) > 0)
#define SEQ_GE(a, b)  ((int32_t)((a) - (b)) >= 0)

static struct pstat_sample ring[RING_N];
static uint32_t w_idx;   /* 다음 write seq */
static uint32_t a_idx;   /* seq < a_idx 는 ACK/해제됨 */
static uint32_t s_idx;   /* 다음 send seq (a<=s<=w) */
static bool     had_gap;
static K_MUTEX_DEFINE(lock);

void databuf_reset(void)
{
	k_mutex_lock(&lock, K_FOREVER);
	w_idx = a_idx = s_idx = 0;
	had_gap = false;
	k_mutex_unlock(&lock);
}

void databuf_push(const struct pstat_sample *s)
{
	k_mutex_lock(&lock, K_FOREVER);
	ring[w_idx % RING_N] = *s;
	ring[w_idx % RING_N].seq = w_idx;   /* seq 강제 부여 */
	w_idx++;
	if (w_idx - a_idx > RING_N) {        /* 용량 초과 -> 가장 오래된 것 손실 */
		a_idx = w_idx - RING_N;
		had_gap = true;
		if (SEQ_GT(a_idx, s_idx)) {
			s_idx = a_idx;
		}
	}
	k_mutex_unlock(&lock);
}

uint32_t databuf_peek_batch(struct pstat_sample *out, uint32_t max, uint32_t *base)
{
	uint32_t k = 0;
	k_mutex_lock(&lock, K_FOREVER);
	*base = s_idx;
	uint32_t i = s_idx;
	while (SEQ_GT(w_idx, i) && k < max) {
		out[k++] = ring[i % RING_N];
		i++;
	}
	k_mutex_unlock(&lock);
	return k;
}

void databuf_commit_sent(uint32_t base, uint32_t n)
{
	k_mutex_lock(&lock, K_FOREVER);
	uint32_t target = base + n;
	/* 절대위치 확정: 이미 overflow 로 s 가 target 이상으로 당겨졌으면 유지
	 * (이중전진 금지 -> 살아있는 미전송분 보존) */
	if (SEQ_GT(target, s_idx)) {
		s_idx = target;
	}
	if (SEQ_GT(s_idx, w_idx)) {
		s_idx = w_idx;
	}
	k_mutex_unlock(&lock);
}

void databuf_ack(uint32_t acked_seq)
{
	k_mutex_lock(&lock, K_FOREVER);
	uint32_t na = acked_seq + 1;
	if (SEQ_GT(na, a_idx) && SEQ_GE(w_idx, na)) {   /* 유효 범위 내에서만 */
		a_idx = na;
		if (SEQ_GT(a_idx, s_idx)) {
			s_idx = a_idx;
		}
	}
	k_mutex_unlock(&lock);
}

void databuf_rewind_unsent(void)
{
	k_mutex_lock(&lock, K_FOREVER);
	s_idx = a_idx;   /* 미ACK분 처음부터 재전송 */
	k_mutex_unlock(&lock);
}

uint32_t databuf_pending(void)
{
	k_mutex_lock(&lock, K_FOREVER);
	uint32_t v = w_idx - s_idx;
	k_mutex_unlock(&lock);
	return v;
}

uint32_t databuf_unacked(void)
{
	k_mutex_lock(&lock, K_FOREVER);
	uint32_t v = w_idx - a_idx;
	k_mutex_unlock(&lock);
	return v;
}

uint32_t databuf_capacity(void) { return RING_N; }

bool databuf_gap(void)
{
	k_mutex_lock(&lock, K_FOREVER);
	bool g = had_gap;
	k_mutex_unlock(&lock);
	return g;
}
