/*
 * pstat.h — 크로노암페로메트리 포텐쇼스탯 공유 타입
 * 전위는 +0.5V 고정이므로 config 에 전압 필드는 없음.
 */
#ifndef PSTAT_H_
#define PSTAT_H_

#include <stdint.h>
#include <stdbool.h>

/* 실행 모드 */
enum pstat_mode {
	PSTAT_MODE_CONTINUOUS = 0, /* 무한 (stop 까지) */
	PSTAT_MODE_TIMED,          /* duration_s 후 자동정지 */
	PSTAT_MODE_CYCLE,          /* on_s 측정 / off_s 대기(바이어스 off) 반복 */
};

/* 실행 상태 */
enum pstat_state {
	PSTAT_IDLE = 0,
	PSTAT_RUN,        /* 측정중 */
	PSTAT_CYCLE_REST, /* cycle 대기 (바이어스 off) */
};

/* 앱에서 받는 측정 설정 */
struct pstat_config {
	uint16_t rate_hz;    /* 샘플레이트 1~100 */
	uint8_t  mode;       /* enum pstat_mode */
	bool     autorange;  /* true=오토레인지 */
	uint8_t  range_idx;  /* 오토 시작/수동 레인지 인덱스 (RTIA 테이블) */
	uint32_t duration_s; /* TIMED: 측정 시간 */
	uint32_t on_s;       /* CYCLE: 측정 구간 */
	uint32_t off_s;      /* CYCLE: 대기 구간 */
	uint32_t cycles;     /* CYCLE: 반복 횟수 (0=무한) */
};

/* 한 측정 샘플 (16바이트) */
struct pstat_sample {
	uint32_t seq;        /* 전역 시퀀스(=인덱스), databuf 가 부여 */
	uint32_t t_ms;       /* 런 시작 후 경과 ms */
	float    current_nA; /* 전류 (레인지 반영해 환산됨) */
	uint8_t  range_idx;  /* 이 샘플의 RTIA 레인지 인덱스 */
	uint8_t  _pad[3];
};

#endif /* PSTAT_H_ */
