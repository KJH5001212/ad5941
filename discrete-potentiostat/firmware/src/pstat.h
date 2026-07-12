/*
 * pstat.h — 디스크리트 포텐쇼스탯(nRF52832 SAADC 직접측정) 공유 타입
 * ------------------------------------------------------------
 * AD5941 버전과의 차이:
 *  - MCU 가 직접 ADC (SAADC 차동: AIN0=TIA_OUT − AIN1=VREF 1.024V)
 *  - 레인지 = SAADC 게인 인덱스 (RTIA 전환 아님 — RF 는 고정 저항)
 *  - RF/오버샘플링은 보드에서 교체/조정 가능하므로 런타임 설정값 (NVM 저장)
 * 전위는 하드웨어 고정(+0.512V, VREF 1:1 분압)이므로 config 에 전압 필드 없음.
 */
#ifndef PSTAT_H_
#define PSTAT_H_

#include <stdint.h>
#include <stdbool.h>

/* 실행 모드 */
enum pstat_mode {
	PSTAT_MODE_CONTINUOUS = 0, /* 무한 (stop 까지) */
	PSTAT_MODE_TIMED,          /* duration_s 후 자동정지 */
	PSTAT_MODE_CYCLE,          /* on_s 측정 / off_s 대기(AFE off) 반복 */
};

/* 실행 상태 */
enum pstat_state {
	PSTAT_IDLE = 0,
	PSTAT_RUN,        /* 측정중 */
	PSTAT_CYCLE_REST, /* cycle 대기 (AFE 전원 off) */
};

/* SAADC 게인 레인지: index0 = 최고감도. FS(ΔV) = ±0.6V/gain */
#define GAIN_N 3   /* 0: gain4(±0.15V) / 1: gain1(±0.6V) / 2: gain1/2(±1.2V) */

/* 앱에서 받는 측정 설정 (BLE config/start 로 갱신, rf/os 는 NVM 저장) */
struct pstat_config {
	uint16_t rate_hz;    /* 출력 샘플레이트 1~100 */
	uint8_t  mode;       /* enum pstat_mode */
	bool     autorange;  /* true=SAADC 게인 오토레인지 */
	uint8_t  range_idx;  /* 수동/시작 게인 인덱스 (0..GAIN_N-1) */
	uint32_t duration_s; /* TIMED: 측정 시간 */
	uint32_t on_s;       /* CYCLE: 측정 구간 */
	uint32_t off_s;      /* CYCLE: 대기 구간 */
	uint32_t cycles;     /* CYCLE: 반복 횟수 (0=무한) */
	/* --- 하드웨어 파라미터 (RF/CF 교체 시 앱에서 변경, NVM 저장) --- */
	uint32_t rf_ohm;     /* TIA 피드백 저항 [Ω] (기본 1M, 5.1M/10M 교체 가능) */
	uint16_t oversample; /* SAADC 하드웨어 오버샘플 1/2/4/…/256 (2^n 으로 반올림) */
};

/* 캘리브레이션 (NVM 저장) */
struct pstat_cal {
	float scale;            /* 전류 스케일 (더미셀 보정, 기본 1.0) */
	float off_nA[GAIN_N];   /* 게인별 제로 오프셋 [nA] (셀 오픈 보정) */
};

/* 한 측정 샘플 (16바이트) — 전송 포맷 [seq,t_ms,current_nA,range] 동일 */
struct pstat_sample {
	uint32_t seq;        /* 전역 시퀀스, databuf 가 부여 */
	uint32_t t_ms;       /* 런 시작 후 경과 ms */
	float    current_nA; /* 전류 (게인/RF/캘리브레이션 반영) */
	uint8_t  range_idx;  /* 이 샘플의 SAADC 게인 인덱스 */
	uint8_t  _pad[3];
};

#endif /* PSTAT_H_ */
