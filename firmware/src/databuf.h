/*
 * databuf.h — 무손실 데이터 버퍼 (RAM 링버퍼 + ACK 윈도우)
 * ------------------------------------------------------------
 *  seq 는 전역 단조증가(uint32, 랩어라운드 안전 비교 사용). 세 포인터:
 *    a(ack) <= s(sent) <= w(write)
 *  - push:  seq=w 부여 후 저장. 용량 초과 시 가장 오래된 것 덮어씀(gap 표시).
 *  - peek_batch: 미전송분(s..w) 복사 + 배치 시작 seq(base) 반환 (s 전진 안함).
 *  - commit_sent(base,n): s 를 base+n 절대위치로 확정.
 *      전송(락 해제) 중 overflow 로 a/s 가 전진했어도 이중전진 없이
 *      s = max(s, base+n) (w 상한) 로만 이동 -> 유효 미전송분 보존.
 *  - ack: 앱이 받은 최대 seq 까지 해제(a 전진).
 *  - rewind_unsent: 재연결 시 미ACK분(a..) 부터 재전송(s=a).
 *  => 앱 ACK 전엔 버퍼에서 안 지워지므로 재연결하면 무손실 재전송.
 *
 *  [향후] 용량 초과 임박 시 플래시 spill (TODO, 이번엔 RAM 만).
 */
#ifndef DATABUF_H_
#define DATABUF_H_

#include "pstat.h"

void     databuf_reset(void);
void     databuf_push(const struct pstat_sample *s);      /* seq 자동부여 */
/* 미전송분 복사. *base=배치 시작 seq. 반환=복사 개수 (s 전진 안함) */
uint32_t databuf_peek_batch(struct pstat_sample *out, uint32_t max, uint32_t *base);
void     databuf_commit_sent(uint32_t base, uint32_t n);  /* base..base+n-1 전송확정 */
void     databuf_ack(uint32_t acked_seq);                 /* acked_seq 까지 해제 */
void     databuf_rewind_unsent(void);                     /* s=a 로 되감기 */

uint32_t databuf_pending(void);   /* 미전송 개수 (w-s) */
uint32_t databuf_unacked(void);   /* 미ACK 개수 (w-a) */
uint32_t databuf_capacity(void);  /* 링버퍼 용량 */
bool     databuf_gap(void);       /* 손실(덮어쓰기) 발생 여부 */

#endif /* DATABUF_H_ */
